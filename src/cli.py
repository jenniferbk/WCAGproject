"""CLI entry point for the a11y remediation tool."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

from src.agent.orchestrator import process
from src.models.pipeline import CourseContext, RemediationRequest


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="WCAG 2.1 AA accessibility remediation for course documents",
    )
    parser.add_argument("document", help="Path to the document to remediate")
    parser.add_argument("--output-dir", "-o", default="", help="Output directory")
    parser.add_argument("--course", default="", help="Course name")
    parser.add_argument("--department", default="", help="Department")
    parser.add_argument("--format", default="same", choices=["same", "pdf", "both"],
                        help="Output format")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    request = RemediationRequest(
        document_path=args.document,
        course_context=CourseContext(
            course_name=args.course,
            department=args.department,
        ),
        output_dir=args.output_dir,
        output_format=args.format,
    )

    result = process(request)

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        if result.success:
            print(f"\nRemediation complete!")
            print(f"  Input:  {result.input_path}")
            print(f"  Output: {result.output_path}")
            print(f"  Issues: {result.issues_before} → {result.issues_after} ({result.issues_fixed} fixed)")
            print(f"  Time:   {result.processing_time_seconds:.1f}s")
            if result.items_for_human_review:
                print(f"\n  Items for human review:")
                for item in result.items_for_human_review:
                    print(f"    - {item}")
        else:
            print(f"\nRemediation failed: {result.error}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
