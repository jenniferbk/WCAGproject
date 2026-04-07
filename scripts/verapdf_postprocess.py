"""Post-process a remediation_benchmark run with veraPDF PDF/UA-1 validation.

Reads the JSON produced by ``remediation_benchmark.py``, runs veraPDF on the
input and remediated output for every successful document, and rewrites the
report with before/after compliance columns. Kept as a separate script so
it can run against a completed benchmark directory without touching the
main runner.

Usage:
    python3 scripts/verapdf_postprocess.py \\
        --benchmark-dir /tmp/PDF-Accessibility-Benchmark \\
        --results-dir /tmp/remediation_bench_full
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools.verapdf_checker import check_pdf_ua


def _vera_summary(pdf_path: str) -> dict:
    r = check_pdf_ua(pdf_path)
    return {
        "success": r.success,
        "compliant": r.compliant,
        "passed_rules": r.passed_rules,
        "failed_rules": r.failed_rules,
        "violation_count": r.violation_count,
        "error": r.error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--json-name", default="remediation_benchmark_results.json")
    parser.add_argument(
        "--report-name",
        default="remediation_benchmark_results_with_verapdf.md",
    )
    args = parser.parse_args()

    json_path = args.results_dir / args.json_name
    data = json.loads(json_path.read_text())

    started = time.time()
    for i, r in enumerate(data, 1):
        if not r.get("success"):
            continue
        in_path = r.get("pdf_path")
        out_path = r.get("output_path")
        if not in_path or not out_path or not Path(out_path).exists():
            continue
        print(f"[{i}/{len(data)}] {r['task']}/{r['openalex_id']}")
        r["verapdf_before"] = _vera_summary(in_path)
        r["verapdf_after"] = _vera_summary(out_path)
        b = r["verapdf_before"]
        a = r["verapdf_after"]
        print(
            f"    failed rules: {b['failed_rules']} → {a['failed_rules']}  "
            f"violations: {b['violation_count']} → {a['violation_count']}  "
            f"compliant: {b['compliant']} → {a['compliant']}"
        )

    # Save enriched JSON
    json_path.write_text(json.dumps(data, indent=2, default=str))

    # Write a compact markdown report focused on veraPDF deltas
    lines: list[str] = []
    lines.append("# Remediation Benchmark + veraPDF Report")
    lines.append("")
    succ = [r for r in data if r.get("success") and "verapdf_after" in r]
    lines.append(f"Documents with veraPDF results: {len(succ)}")
    lines.append("")

    if succ:
        compliant_before = sum(1 for r in succ if r["verapdf_before"]["compliant"])
        compliant_after = sum(1 for r in succ if r["verapdf_after"]["compliant"])
        failed_rules_improved = sum(
            1 for r in succ
            if r["verapdf_after"]["failed_rules"] < r["verapdf_before"]["failed_rules"]
        )
        failed_rules_same = sum(
            1 for r in succ
            if r["verapdf_after"]["failed_rules"] == r["verapdf_before"]["failed_rules"]
        )
        failed_rules_worse = sum(
            1 for r in succ
            if r["verapdf_after"]["failed_rules"] > r["verapdf_before"]["failed_rules"]
        )
        total_failed_before = sum(r["verapdf_before"]["failed_rules"] for r in succ)
        total_failed_after = sum(r["verapdf_after"]["failed_rules"] for r in succ)

        lines.append("## Headline — PDF/UA compliance (independent veraPDF check)")
        lines.append("")
        lines.append(f"- **Fully compliant before remediation:** {compliant_before}/{len(succ)}")
        lines.append(f"- **Fully compliant after remediation:** {compliant_after}/{len(succ)}")
        lines.append(f"- **Compliance swing:** +{compliant_after - compliant_before}")
        lines.append(f"- **Total failed rules before:** {total_failed_before}")
        lines.append(f"- **Total failed rules after:** {total_failed_after}")
        lines.append(
            f"- **Net rule improvement:** {total_failed_before - total_failed_after} fewer failed rules"
        )
        lines.append(f"- **Docs with improved rule score:** {failed_rules_improved}")
        lines.append(f"- **Docs unchanged:** {failed_rules_same}")
        lines.append(f"- **Docs that regressed:** {failed_rules_worse}")
        lines.append("")

    lines.append("## Per-document veraPDF deltas")
    lines.append("")
    lines.append("| Task | Label | OpenAlex ID | Failed rules | Violations | Compliant |")
    lines.append("|---|---|---|---|---|---|")
    for r in data:
        if "verapdf_after" not in r:
            continue
        b = r["verapdf_before"]
        a = r["verapdf_after"]
        fr = f"{b['failed_rules']} → {a['failed_rules']}"
        vl = f"{b['violation_count']} → {a['violation_count']}"
        comp = f"{'✓' if b['compliant'] else '✗'} → {'✓' if a['compliant'] else '✗'}"
        lines.append(
            f"| {r['task']} | {r['label']} | {r['openalex_id']} | {fr} | {vl} | {comp} |"
        )

    report_path = args.results_dir / args.report_name
    report_path.write_text("\n".join(lines))

    elapsed = time.time() - started
    print(f"\nDone in {elapsed:.0f}s")
    print(f"Enriched JSON: {json_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
