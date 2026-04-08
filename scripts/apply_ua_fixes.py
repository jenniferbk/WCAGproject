"""Apply Track C + Track A post-processing to remediated benchmark PDFs.

Reads a ``remediation_benchmark_results.json`` produced by
``scripts/remediation_benchmark.py``, iterates every successful doc,
applies:

  1. ``apply_pdf_ua_metadata()`` (Track C: XMP pdfuaid, DisplayDocTitle, /Metadata)
  2. ``mark_untagged_content_as_artifact()`` (Track A: /Artifact for rule 7.1-3)

with per-track verification gates. Writes an enriched results file
``ua_fixes_results.json`` plus a markdown report.

Usage:
    python3 scripts/apply_ua_fixes.py --results-dir /tmp/remediation_bench_full
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

from src.tools.pdf_writer import (
    apply_pdf_ua_metadata,
    mark_untagged_content_as_artifact,
    populate_link_annotation_contents,
)
from src.tools.verapdf_checker import check_pdf_ua

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("apply_ua_fixes")
logger.setLevel(logging.INFO)


def _text_snapshot(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def _vera_snapshot(pdf_path: str) -> dict:
    r = check_pdf_ua(pdf_path)
    return {
        "success": r.success,
        "compliant": r.compliant,
        "failed_rules": r.failed_rules,
        "passed_rules": r.passed_rules,
        "violation_count": r.violation_count,
        "rule_ids": sorted({v.rule_id for v in r.violations}),
    }


def _process_one(doc_info: dict, work_dir: Path) -> dict:
    """Apply both tracks to one doc with verification + blame attribution."""
    out_path = doc_info.get("output_path")
    if not out_path or not Path(out_path).exists():
        return {**doc_info, "ua_fix_status": "missing_output"}

    pdf_path = Path(out_path)
    backup = work_dir / f"{pdf_path.stem}.pre_ua_fix.pdf"
    post_c_backup = work_dir / f"{pdf_path.stem}.post_c.pdf"

    shutil.copy(pdf_path, backup)
    try:
        baseline_text = _text_snapshot(str(pdf_path))
    except Exception as exc:
        return {**doc_info, "ua_fix_status": f"baseline_text_failed:{exc}"}
    baseline_vera = _vera_snapshot(str(pdf_path))

    record = {
        **doc_info,
        "ua_fix_status": "pending",
        "ua_fix_track_c": None,
        "ua_fix_track_a": None,
        "ua_vera_before": baseline_vera,
        "ua_vera_after": None,
    }

    # ── Track C ──
    try:
        c_result = apply_pdf_ua_metadata(pdf_path)
        record["ua_fix_track_c"] = {
            "success": c_result.success,
            "changes": c_result.changes,
            "error": c_result.error,
        }
        if not c_result.success:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_failed"
            return record

        post_c_text = _text_snapshot(str(pdf_path))
        if post_c_text != baseline_text:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_text_mismatch"
            return record

        post_c_vera = _vera_snapshot(str(pdf_path))
        new_rules = set(post_c_vera["rule_ids"]) - set(baseline_vera["rule_ids"])
        if new_rules:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_new_rules"
            record["new_rules"] = sorted(new_rules)
            return record

        shutil.copy(pdf_path, post_c_backup)
    except Exception as exc:
        shutil.copy(backup, pdf_path)
        record["ua_fix_status"] = f"track_c_exception:{exc}"
        record["traceback"] = traceback.format_exc(limit=5)
        return record

    # ── Track A ──
    try:
        a_result = mark_untagged_content_as_artifact(pdf_path)
        # Also propagate link annotation /Contents (Bucket 2 / rule 7.18.5-2).
        # We treat this as part of Track A's measurement so the gate
        # logic still has a single "Track A" before/after to compare.
        try:
            link_result = populate_link_annotation_contents(pdf_path)
            link_modified = link_result.annotations_modified if link_result.success else 0
        except Exception:
            link_modified = 0
        record["ua_fix_track_a"] = {
            "success": a_result.success,
            "pages_modified": a_result.pages_modified,
            "artifact_wrappers_inserted": a_result.artifact_wrappers_inserted,
            "pages_skipped": a_result.pages_skipped,
            "form_xobjects_modified": a_result.form_xobjects_modified,
            "link_contents_set": link_modified,
            "errors": a_result.errors,
        }
        if not a_result.success:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_failed_kept_c"
            return record

        post_a_text = _text_snapshot(str(pdf_path))
        if post_a_text != baseline_text:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_text_mismatch_kept_c"
            return record

        post_a_vera = _vera_snapshot(str(pdf_path))
        new_rules = set(post_a_vera["rule_ids"]) - set(baseline_vera["rule_ids"])
        if new_rules:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_new_rules_kept_c"
            record["new_rules"] = sorted(new_rules)
            return record

        if post_a_vera["violation_count"] > baseline_vera["violation_count"]:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_no_improvement_kept_c"
            return record

        record["ua_vera_after"] = post_a_vera
        record["ua_fix_status"] = "success"
        return record
    except Exception as exc:
        shutil.copy(post_c_backup, pdf_path)
        record["ua_fix_status"] = f"track_a_exception_kept_c:{exc}"
        record["traceback"] = traceback.format_exc(limit=5)
        return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument(
        "--json-name", default="remediation_benchmark_results.json"
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    input_json = args.results_dir / args.json_name
    if not input_json.exists():
        print(f"ERROR: {input_json} not found", file=sys.stderr)
        sys.exit(1)

    data = json.loads(input_json.read_text())
    if args.limit:
        data = data[: args.limit]

    work_dir = args.results_dir / "ua_fixes_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    out_json = args.results_dir / "ua_fixes_results.json"
    results: list[dict] = []
    done_keys: set[str] = set()
    if out_json.exists():
        try:
            results = json.loads(out_json.read_text())
            done_keys = {
                f'{r.get("openalex_id","")}|{r.get("task","")}|{r.get("label","")}'
                for r in results
                if r.get("ua_fix_status")
            }
            print(f"Resuming: {len(done_keys)} already processed")
        except Exception:
            results = []
            done_keys = set()

    start = time.time()
    for i, doc_info in enumerate(data, 1):
        key = f'{doc_info.get("openalex_id","")}|{doc_info.get("task","")}|{doc_info.get("label","")}'
        if key in done_keys:
            continue
        print(f"[{i}/{len(data)}] {doc_info.get('task')}/{doc_info.get('label')}/{doc_info.get('openalex_id')}")
        rec = _process_one(doc_info, work_dir)
        results.append(rec)
        status = rec["ua_fix_status"]
        vc_before = rec["ua_vera_before"].get("violation_count", "?") if rec.get("ua_vera_before") else "?"
        vc_after = (rec.get("ua_vera_after") or {}).get("violation_count", "-")
        print(f"    {status}: violations {vc_before} → {vc_after}")

        # Atomic rewrite of the results file so we can resume.
        tmp = out_json.with_suffix(".tmp")
        tmp.write_text(json.dumps(results, indent=2, default=str))
        tmp.replace(out_json)

    elapsed = time.time() - start

    # Markdown report
    report = args.results_dir / "ua_fixes_results.md"
    successes = [r for r in results if r["ua_fix_status"] == "success"]

    statuses: dict[str, int] = {}
    for r in results:
        statuses[r["ua_fix_status"]] = statuses.get(r["ua_fix_status"], 0) + 1

    total_vc_before = sum(
        r.get("ua_vera_before", {}).get("violation_count", 0) for r in results
    )
    total_vc_after = sum(
        (r.get("ua_vera_after") or r.get("ua_vera_before") or {}).get("violation_count", 0)
        for r in results
    )

    lines: list[str] = []
    lines.append("# PDF/UA Post-Processing Results")
    lines.append("")
    lines.append(f"- Total docs: {len(results)}")
    lines.append(f"- Full success (both tracks applied): {len(successes)}")
    for status, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {status}: {n}")
    lines.append("")
    lines.append(f"- **Total failed checks before:** {total_vc_before}")
    lines.append(f"- **Total failed checks after:** {total_vc_after}")
    delta = total_vc_before - total_vc_after
    lines.append(f"- **Reduction:** {delta}")
    if total_vc_before:
        lines.append(f"- **% reduction:** {delta / total_vc_before:.1%}")
    lines.append("")
    lines.append(f"Wall time: {elapsed:.0f}s")

    report.write_text("\n".join(lines))
    print(f"\nReport: {report}")
    print(f"JSON:   {out_json}")


if __name__ == "__main__":
    main()
