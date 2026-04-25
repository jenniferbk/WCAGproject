"""Strategy mapper experiment — compare LLM strategy vs deterministic mapper.

Question: does the strategy LLM call do decision work that can't be reduced
to a template, or is it a checklist follower?

Method:
1. For each test PDF, run comprehension ONCE (so both strategies see the
   same input — controls for Gemini vision noise).
2. Run `strategize()` (LLM) and `strategize_deterministic()` (mapper) on
   the cached comprehension result.
3. Compare action sets: counts by type, set-diff of (element_id, action_type),
   parameter divergence.

Output: docs/experiments/2026-04-25-strategy-mapper-comparison.md

Phase A only — no full pipeline runs. If action sets are near-identical
(modulo link-text), the LLM is template-following. If they differ
meaningfully, run Phase B (full pipeline + veraPDF) to measure outcome
impact.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.comprehension import comprehend
from src.agent.strategy import strategize, strategize_deterministic
from src.models.pipeline import (
    ApiUsage,
    ComprehensionResult,
    RemediationStrategy,
    estimate_usage_cost,
)
from src.tools.pdf_parser import parse_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("strategy_experiment")

TESTDOCS_DIR = Path(__file__).parent.parent / "testdocs"
OUTPUT_DIR = Path(__file__).parent.parent / "testdocs" / "strategy_experiment"

DEFAULT_COURSE = "EMAT 8030"
DEFAULT_DEPT = "Mathematics Education"


def _action_key(action) -> tuple[str, str]:
    """Identify an action by (element_id, action_type)."""
    return (action.element_id, action.action_type)


def _summarize_actions(strategy: RemediationStrategy) -> dict:
    """Count actions by type and collect element-id sets."""
    by_type: dict[str, int] = {}
    keys: set[tuple[str, str]] = set()
    for a in strategy.actions:
        by_type[a.action_type] = by_type.get(a.action_type, 0) + 1
        keys.add(_action_key(a))
    return {
        "total": len(strategy.actions),
        "by_type": by_type,
        "keys": keys,
        "review_items": len(strategy.items_for_human_review),
    }


def _diff_actions(llm: RemediationStrategy, det: RemediationStrategy) -> dict:
    """Set-difference action keys between LLM and mapper outputs."""
    llm_keys = {_action_key(a) for a in llm.actions}
    det_keys = {_action_key(a) for a in det.actions}
    only_llm = llm_keys - det_keys
    only_det = det_keys - llm_keys
    common = llm_keys & det_keys

    # Within common keys, find param divergence
    llm_by_key = {_action_key(a): a for a in llm.actions}
    det_by_key = {_action_key(a): a for a in det.actions}
    param_diffs: list[dict] = []
    for k in common:
        la = llm_by_key[k]
        da = det_by_key[k]
        if la.parameters != da.parameters:
            param_diffs.append({
                "key": list(k),
                "llm_params": _trunc_params(la.parameters),
                "det_params": _trunc_params(da.parameters),
            })

    return {
        "only_llm": sorted(only_llm),
        "only_det": sorted(only_det),
        "common_count": len(common),
        "param_diffs": param_diffs,
    }


def _trunc_params(params: dict, max_len: int = 100) -> dict:
    """Truncate long string values for readable diffs."""
    out: dict = {}
    for k, v in params.items():
        if isinstance(v, str) and len(v) > max_len:
            out[k] = v[:max_len] + "..."
        else:
            out[k] = v
    return out


def _strategy_cost(s: RemediationStrategy) -> float:
    return round(sum(estimate_usage_cost(u) for u in s.api_usage), 4)


def _run_one(pdf_path: Path) -> dict:
    """Run comprehension once, then both strategies. Return comparison record."""
    name = pdf_path.name
    logger.info("=== %s ===", name)

    # Parse
    parse = parse_pdf(str(pdf_path))
    if not parse.success or parse.document is None:
        return {"name": name, "success": False, "error": f"parse failed: {parse.error}"}
    doc = parse.document
    logger.info("Parsed: %d paragraphs, %d images, %d tables",
                len(doc.paragraphs), len(doc.images), len(doc.tables))

    # Comprehension (one call — both strategies see this same result)
    t0 = time.time()
    comprehension = comprehend(
        doc,
        course_name=DEFAULT_COURSE,
        department=DEFAULT_DEPT,
    )
    comp_time = time.time() - t0
    comp_cost = round(sum(estimate_usage_cost(u) for u in comprehension.api_usage), 4)
    logger.info("Comprehension: %d image_descriptions, %d element_purposes, %.1fs, $%.4f",
                len(comprehension.image_descriptions),
                len(comprehension.element_purposes),
                comp_time, comp_cost)

    # LLM strategy
    t0 = time.time()
    llm_strategy = strategize(doc, comprehension)
    llm_time = time.time() - t0
    llm_cost = _strategy_cost(llm_strategy)
    logger.info("LLM strategy: %d actions, %.1fs, $%.4f",
                len(llm_strategy.actions), llm_time, llm_cost)

    # Deterministic strategy
    t0 = time.time()
    det_strategy = strategize_deterministic(doc, comprehension)
    det_time = time.time() - t0
    det_cost = _strategy_cost(det_strategy)
    logger.info("Deterministic mapper: %d actions, %.3fs, $%.4f",
                len(det_strategy.actions), det_time, det_cost)

    # Compare
    llm_summary = _summarize_actions(llm_strategy)
    det_summary = _summarize_actions(det_strategy)
    diff = _diff_actions(llm_strategy, det_strategy)

    # Drop the unhashable 'keys' field before JSON serialization
    llm_summary_serializable = {k: v for k, v in llm_summary.items() if k != "keys"}
    det_summary_serializable = {k: v for k, v in det_summary.items() if k != "keys"}

    return {
        "name": name,
        "success": True,
        "doc_stats": {
            "paragraphs": len(doc.paragraphs),
            "images": len(doc.images),
            "tables": len(doc.tables),
            "links": len(doc.links),
        },
        "comprehension": {
            "image_descriptions": len(comprehension.image_descriptions),
            "element_purposes": len(comprehension.element_purposes),
            "validation_issues": comprehension.validation_issues_count,
            "time_s": round(comp_time, 1),
            "cost_usd": comp_cost,
        },
        "llm": {
            "summary": llm_summary_serializable,
            "time_s": round(llm_time, 2),
            "cost_usd": llm_cost,
        },
        "deterministic": {
            "summary": det_summary_serializable,
            "time_s": round(det_time, 3),
            "cost_usd": det_cost,
        },
        "diff": diff,
    }


def main():
    pdfs = sorted(TESTDOCS_DIR.glob("*.pdf"))
    if not pdfs:
        logger.error("No PDFs found in %s", TESTDOCS_DIR)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    t_total = time.time()
    for pdf in pdfs:
        try:
            res = _run_one(pdf)
        except Exception as e:
            logger.exception("Run failed for %s", pdf.name)
            res = {"name": pdf.name, "success": False, "error": str(e)}
        results.append(res)

    total_time = time.time() - t_total

    # Convert sets in diff to lists for JSON
    for r in results:
        d = r.get("diff")
        if d:
            d["only_llm"] = [list(k) for k in d["only_llm"]]
            d["only_det"] = [list(k) for k in d["only_det"]]

    json_path = OUTPUT_DIR / "results.json"
    json_path.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Wrote %s", json_path)

    # Print quick summary
    print("\n=== SUMMARY ===")
    for r in results:
        if not r.get("success"):
            print(f"FAIL  {r['name']}: {r.get('error')}")
            continue
        llm_n = r["llm"]["summary"]["total"]
        det_n = r["deterministic"]["summary"]["total"]
        only_llm = len(r["diff"]["only_llm"])
        only_det = len(r["diff"]["only_det"])
        param_diffs = len(r["diff"]["param_diffs"])
        common = r["diff"]["common_count"]
        print(f"OK    {r['name'][:60]:60s}")
        print(f"      actions: LLM={llm_n:3d}  det={det_n:3d}  common={common:3d}  only_llm={only_llm}  only_det={only_det}  param_diffs={param_diffs}")
        print(f"      cost: LLM strategy=${r['llm']['cost_usd']:.4f}  det=$0  comp=${r['comprehension']['cost_usd']:.4f}")
    print(f"\nTotal time: {total_time:.0f}s")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    main()
