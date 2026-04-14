"""Run the full remediation pipeline against the Kumar et al. benchmark.

Unlike ``scripts/benchmark.py`` which only *detects* accessibility problems
and maps them to benchmark labels, this script actually *remediates* each
document and measures whether the output is better than the input. This is
the number the product actually cares about: given a pile of real PDFs with
known accessibility issues, how many can we fix?

Outputs a markdown report with aggregate stats plus per-document detail,
grouped by the benchmark's task labels. Also dumps full results as JSON so
we can rerun the analysis or post-process outputs (e.g. run veraPDF later
on the remediated PDFs).

Usage:
    # Smoke-test on 5 diverse documents
    python3 scripts/remediation_benchmark.py \\
        --benchmark-dir /tmp/PDF-Accessibility-Benchmark \\
        --limit 5 --output-dir /tmp/remediation_bench_smoke

    # Full 125-doc run
    python3 scripts/remediation_benchmark.py \\
        --benchmark-dir /tmp/PDF-Accessibility-Benchmark \\
        --output-dir /tmp/remediation_bench
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

# Make ``src`` importable when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest
from scripts.struct_tree_probe import probe_struct_tree

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("remediation_benchmark")
logger.setLevel(logging.INFO)


# Generic course context. The benchmark docs are mostly scientific papers
# (Nature, PhysRev, etc.) plus a few magazine articles, so a generic
# "academic reading" framing is the most honest default.
DEFAULT_CONTEXT = CourseContext(
    course_name="General Academic Reading",
    department="Interdisciplinary",
    description=(
        "Academic papers and articles used as course readings. Documents may "
        "contain figures, tables, equations, and citations typical of "
        "peer-reviewed literature."
    ),
)


def _find_pdf(benchmark_dir: Path, task_name: str, label: str, item: dict) -> Path | None:
    """Locate the PDF for a benchmark item, matching ``benchmark.py``'s logic."""
    rel = item.get("pdf_path", "")
    if rel:
        p = benchmark_dir / rel
        if p.exists():
            return p
    iid = item.get("openalex_id", "")
    if iid:
        for candidate in (
            benchmark_dir / "data" / "inputs" / task_name / label / iid / f"{iid}_0.pdf",
            benchmark_dir / "inputs" / task_name / label / iid / f"{iid}_0.pdf",
        ):
            if candidate.exists():
                return candidate
    return None


def _collect_unique_docs(benchmark_dir: Path) -> list[dict]:
    """Return a deduplicated list of (pdf_path, task, label, openalex_id) entries.

    The benchmark re-uses documents across labels (and sometimes across tasks).
    Remediating the same file multiple times wastes money and skews averages,
    so we dedupe by resolved PDF path.
    """
    dataset_path = benchmark_dir / "data" / "dataset.json"
    if not dataset_path.exists():
        print(f"ERROR: dataset.json not found at {dataset_path}", file=sys.stderr)
        sys.exit(1)

    with open(dataset_path) as f:
        dataset = json.load(f)

    seen: set[str] = set()
    docs: list[dict] = []
    for task_name, task_data in dataset["tasks"].items():
        for label, items in task_data.items():
            for item in items:
                pdf = _find_pdf(benchmark_dir, task_name, label, item)
                if not pdf:
                    continue
                key = str(pdf.resolve())
                if key in seen:
                    continue
                seen.add(key)
                docs.append({
                    "pdf_path": str(pdf),
                    "task": task_name,
                    "label": label,
                    "openalex_id": item.get("openalex_id", ""),
                })
    return docs


def _pick_diverse(docs: list[dict], limit: int) -> list[dict]:
    """Pick ``limit`` documents spread across tasks for a smoke test.

    Prioritises task diversity over label diversity: if the limit is 5 and
    there are 7 tasks, we want 5 different tasks represented (varying
    labels) rather than 4 versions of the same document.
    """
    if len(docs) <= limit:
        return docs
    by_task: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_task[d["task"]].append(d)
    # Within each task, prefer label variety by shuffling stably by label
    for task in by_task:
        by_task[task].sort(key=lambda d: (d["label"], d["openalex_id"]))
    picks: list[dict] = []
    tasks = sorted(by_task.keys())
    i = 0
    while len(picks) < limit and any(by_task.values()):
        task = tasks[i % len(tasks)]
        if by_task[task]:
            picks.append(by_task[task].pop(0))
        i += 1
    return picks[:limit]


def _struct_tree_signals(pdf_path: str) -> dict:
    """Extract the PDF/UA-relevant structure tree signals from a PDF.

    Our existing validator reads the *visual* content via PyMuPDF and so is
    blind to struct tree changes — adding iText tags for headings, figures,
    and links doesn't change what the visual parser sees. These signals are
    what screen readers and PDF/UA validators actually care about.
    """
    try:
        facts = probe_struct_tree(pdf_path)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "has_struct_tree": facts.has_struct_tree,
        "headings": facts.heading_count,
        "figures": facts.figure_count,
        "figures_with_alt": facts.figures_with_alt,
        "tables": facts.table_count,
        "table_th": facts.table_th_count,
        "links_tagged": facts.link_count,
        "annot_links": facts.annot_link_count,
        "annot_links_with_struct_parent": facts.annot_links_with_struct_parent,
    }


def _struct_tree_delta(before: dict, after: dict) -> dict:
    """Summarize the before→after change as a set of deltas.

    Also computes an accessibility "move" score: the count of PDF/UA signals
    that moved from absent to present (untagged → tagged, no alt → alt, etc.),
    which is the most honest measure of whether remediation improved the
    document's machine-readable accessibility.
    """
    def fig_alt_frac(d: dict) -> float | None:
        f = d.get("figures", 0) or 0
        fa = d.get("figures_with_alt", 0) or 0
        return fa / f if f else None

    def table_th_frac(d: dict) -> float | None:
        t = d.get("tables", 0) or 0
        th = d.get("table_th", 0) or 0
        return min(1.0, th / (t * 1.5)) if t else None

    gains = 0
    if not before.get("has_struct_tree") and after.get("has_struct_tree"):
        gains += 1
    if before.get("headings", 0) == 0 and after.get("headings", 0) > 0:
        gains += 1
    # Figure alt text gained: either new figures appeared with alt, or existing
    # figures now have alt where they didn't before.
    fa_before = fig_alt_frac(before)
    fa_after = fig_alt_frac(after)
    if fa_after is not None and fa_after > 0:
        if fa_before is None or fa_after > fa_before:
            gains += 1
    # Tables: same idea — new tables with TH count as a gain.
    tt_before = table_th_frac(before)
    tt_after = table_th_frac(after)
    if tt_after is not None and tt_after > 0:
        if tt_before is None or tt_after > tt_before:
            gains += 1

    return {
        "headings_before": before.get("headings", 0),
        "headings_after": after.get("headings", 0),
        "fig_alt_before": f'{before.get("figures_with_alt", 0)}/{before.get("figures", 0)}',
        "fig_alt_after": f'{after.get("figures_with_alt", 0)}/{after.get("figures", 0)}',
        "tables_th_before": f'{before.get("table_th", 0)}/{before.get("tables", 0)}',
        "tables_th_after": f'{after.get("table_th", 0)}/{after.get("tables", 0)}',
        "annot_links_before": before.get("annot_links", 0),
        "annot_links_after": after.get("annot_links", 0),
        "struct_tree_before": before.get("has_struct_tree", False),
        "struct_tree_after": after.get("has_struct_tree", False),
        "accessibility_gains": gains,
    }


def _run_one(doc: dict, output_dir: Path) -> dict:
    """Run the remediation pipeline on one document and summarize the result."""
    pdf_path = doc["pdf_path"]
    per_doc_out = output_dir / f"{doc['task']}_{doc['label']}_{doc['openalex_id']}"

    # Resume support: skip if a remediated PDF already exists in the output dir
    if per_doc_out.exists():
        existing = list(per_doc_out.glob("*_remediated.pdf"))
        if existing:
            logger.info("Skipping %s (already remediated)", per_doc_out.name)
            return {
                **doc,
                "success": True,
                "skipped_existing": True,
                "output_path": str(existing[0]),
                "elapsed_seconds": 0,
            }

    per_doc_out.mkdir(parents=True, exist_ok=True)

    request = RemediationRequest(
        document_path=pdf_path,
        course_context=DEFAULT_CONTEXT,
        output_dir=str(per_doc_out),
        output_format="same",
    )

    # Probe the input's structure tree before we kick off the pipeline so we
    # can measure real PDF/UA changes regardless of what the visual validator
    # thinks.
    struct_before = _struct_tree_signals(pdf_path) if pdf_path.lower().endswith(".pdf") else {}

    start = time.time()
    try:
        result = process(request, on_phase=None)
    except Exception as exc:
        return {
            **doc,
            "success": False,
            "error": f"Pipeline crashed: {exc}",
            "traceback": traceback.format_exc(limit=5),
            "elapsed_seconds": round(time.time() - start, 2),
            "struct_before": struct_before,
        }

    struct_after: dict = {}
    struct_delta: dict = {}
    out_path = result.output_path
    if out_path and out_path.lower().endswith(".pdf") and Path(out_path).exists():
        struct_after = _struct_tree_signals(out_path)
        struct_delta = _struct_tree_delta(struct_before, struct_after)

    summary = {
        **doc,
        "success": result.success,
        "error": result.error or "",
        "output_path": result.output_path,
        "report_path": result.report_path,
        "issues_before": result.issues_before,
        "issues_after": result.issues_after,
        "issues_fixed": result.issues_fixed,
        "elapsed_seconds": round(result.processing_time_seconds, 2),
        "cost_usd": result.cost_summary.estimated_cost_usd,
        "items_for_human_review": len(result.items_for_human_review),
        "struct_before": struct_before,
        "struct_after": struct_after,
        "struct_delta": struct_delta,
    }
    return summary


def _write_report(results: list[dict], output_path: Path, total_elapsed: float) -> None:
    """Write a human-readable markdown summary of the run."""
    n = len(results)
    succeeded = [r for r in results if r.get("success") and not r.get("skipped_existing")]
    skipped = [r for r in results if r.get("skipped_existing")]
    failed = [r for r in results if not r.get("success")]

    total_before = sum(r.get("issues_before", 0) for r in succeeded)
    total_after = sum(r.get("issues_after", 0) for r in succeeded)
    total_fixed = sum(r.get("issues_fixed", 0) for r in succeeded)
    total_cost = round(sum(r.get("cost_usd", 0.0) for r in succeeded), 4)

    # Struct-tree aggregate: counts how many docs gained proper tagging.
    # The visual validator (issues_before/after above) is blind to struct
    # tree changes; this block is the honest PDF/UA signal.
    st_docs = [r for r in succeeded if r.get("struct_delta")]
    st_untagged_now_tagged = sum(
        1 for r in st_docs
        if r["struct_delta"].get("struct_tree_before") is False
        and r["struct_delta"].get("struct_tree_after") is True
    )
    st_headings_gained = sum(
        1 for r in st_docs
        if r["struct_delta"].get("headings_before", 0) == 0
        and r["struct_delta"].get("headings_after", 0) > 0
    )
    st_total_headings_added = sum(
        max(0, r["struct_delta"].get("headings_after", 0) - r["struct_delta"].get("headings_before", 0))
        for r in st_docs
    )
    st_alt_added = sum(
        1 for r in st_docs
        if (r.get("struct_after") or {}).get("figures_with_alt", 0)
           > (r.get("struct_before") or {}).get("figures_with_alt", 0)
    )
    st_avg_gains = (
        sum(r["struct_delta"].get("accessibility_gains", 0) for r in st_docs) / len(st_docs)
        if st_docs else 0.0
    )
    median_time = (
        sorted(r["elapsed_seconds"] for r in succeeded)[len(succeeded) // 2]
        if succeeded else 0
    )

    lines: list[str] = []
    lines.append("# Remediation Benchmark Results")
    lines.append("")
    lines.append(f"Ran the full remediation pipeline on {n} documents from the ")
    lines.append("Kumar et al. PDF Accessibility Benchmark.")
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- **Documents processed:** {n}")
    lines.append(f"- **Succeeded:** {len(succeeded)} ({len(succeeded)/max(n,1):.1%})")
    lines.append(f"- **Failed:** {len(failed)}")
    lines.append(f"- **Total issues before remediation:** {total_before}")
    lines.append(f"- **Total issues after remediation:** {total_after}")
    lines.append(f"- **Total issues fixed:** {total_fixed}")
    if total_before:
        lines.append(f"- **Fix rate:** {total_fixed/total_before:.1%}")
    lines.append(f"- **Total API cost:** ${total_cost}")
    if succeeded:
        lines.append(f"- **Median time per doc:** {median_time:.1f}s")
        lines.append(f"- **Avg cost per doc:** ${total_cost/len(succeeded):.4f}")
    lines.append(f"- **Wall time:** {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    lines.append("")

    if st_docs:
        lines.append("## Structure tree improvements (the honest PDF/UA metric)")
        lines.append("")
        lines.append(
            "Our visual validator reads the page content and so cannot see "
            "iText struct tree changes. These numbers measure what screen "
            "readers and PDF/UA validators actually care about:"
        )
        lines.append("")
        lines.append(f"- **Docs with no struct tree → tagged:** {st_untagged_now_tagged}/{len(st_docs)}")
        lines.append(f"- **Docs with 0 headings → at least 1 heading:** {st_headings_gained}/{len(st_docs)}")
        lines.append(f"- **Total headings added across all docs:** {st_total_headings_added}")
        lines.append(f"- **Docs with new figure alt text:** {st_alt_added}/{len(st_docs)}")
        lines.append(f"- **Average accessibility gain per doc:** {st_avg_gains:.2f} (out of 4)")
        lines.append("")

    # Per-task breakdown
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in succeeded:
        by_task[r["task"]].append(r)
    if by_task:
        lines.append("## Per-task breakdown")
        lines.append("")
        lines.append("| Task | N | Issues before | Issues after | Fixed | Fix rate | Avg cost | Median time |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for task in sorted(by_task):
            rs = [r for r in by_task[task] if not r.get("skipped_existing")]
            if not rs:
                continue
            b = sum(r.get("issues_before", 0) for r in rs)
            a = sum(r.get("issues_after", 0) for r in rs)
            f = sum(r.get("issues_fixed", 0) for r in rs)
            c = sum(r.get("cost_usd", 0) for r in rs)
            times = sorted(r.get("elapsed_seconds", 0) for r in rs)
            mt = times[len(times) // 2] if times else 0
            rate = f"{f/b:.0%}" if b else "—"
            lines.append(f"| {task} | {len(rs)} | {b} | {a} | {f} | {rate} | ${c/len(rs):.4f} | {mt:.0f}s |")
        lines.append("")

    # Failures
    if failed:
        lines.append("## Failures")
        lines.append("")
        for r in failed:
            lines.append(f"- **{r['task']}/{r['label']}/{r['openalex_id']}**: {r.get('error', 'unknown')}")
        lines.append("")

    # Per-document detail
    lines.append("## Per-document results")
    lines.append("")
    lines.append("| Task | Label | OpenAlex ID | Headings | Fig alt | Visual Δ | Time | Cost |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        status = "✓" if r.get("success") else "✗"
        delta = r.get("struct_delta") or {}
        headings = f"{delta.get('headings_before', '—')} → {delta.get('headings_after', '—')}"
        fig_alt = f"{delta.get('fig_alt_before', '—')} → {delta.get('fig_alt_after', '—')}"
        visual = f"{r.get('issues_before', '—')} → {r.get('issues_after', '—')}"
        t = f"{r.get('elapsed_seconds', 0):.0f}s"
        c = f"${r.get('cost_usd', 0):.4f}"
        lines.append(
            f"| {r['task']} | {r['label']} | {r['openalex_id']} {status} | "
            f"{headings} | {fig_alt} | {visual} | {t} | {c} |"
        )
    lines.append("")

    output_path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-dir", required=True, type=Path,
        help="Path to the cloned PDF-Accessibility-Benchmark repo",
    )
    parser.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write remediated outputs and reports",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only process N diverse documents (for smoke testing)",
    )
    parser.add_argument(
        "--ids", type=str, default=None,
        help="Comma-separated OpenAlex IDs to run (e.g. W2460269320,W2642438850)",
    )
    parser.add_argument(
        "--report", default="remediation_benchmark_results.md", type=Path,
        help="Markdown report filename (written inside --output-dir)",
    )
    parser.add_argument(
        "--json", default="remediation_benchmark_results.json", type=Path,
        help="JSON results filename (written inside --output-dir)",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    docs = _collect_unique_docs(args.benchmark_dir)
    print(f"Found {len(docs)} unique benchmark PDFs")

    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        docs = [d for d in docs if d["openalex_id"] in wanted]
        print(f"Filtered to {len(docs)} docs matching --ids")
    if args.limit:
        docs = _pick_diverse(docs, args.limit)
        print(f"Smoke test: running on {len(docs)} diverse docs")

    results: list[dict] = []
    overall_start = time.time()
    for i, doc in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] {doc['task']}/{doc['label']}/{doc['openalex_id']}")
        r = _run_one(doc, args.output_dir)
        results.append(r)
        if r.get("skipped_existing"):
            print(f"    ⏭ already remediated, skipping")
        elif r.get("success"):
            print(
                f"    ✓ {r['issues_before']} → {r['issues_after']} "
                f"(fixed {r['issues_fixed']}) in {r['elapsed_seconds']:.0f}s "
                f"(${r['cost_usd']:.4f})"
            )
        else:
            print(f"    ✗ {r.get('error', 'unknown error')}")
    total_elapsed = time.time() - overall_start

    report_path = args.output_dir / args.report
    json_path = args.output_dir / args.json
    _write_report(results, report_path, total_elapsed)
    json_path.write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'='*60}")
    succeeded = sum(1 for r in results if r.get("success"))
    print(f"Processed: {succeeded}/{len(results)} succeeded")
    print(f"Report:    {report_path}")
    print(f"JSON:      {json_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
