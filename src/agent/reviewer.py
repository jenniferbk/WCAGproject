"""Phase 4: Post-remediation review via Claude.

Reviews the remediated document against WCAG criteria, verifies
the quality of changes, and flags remaining issues for human review.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from src.models.document import DocumentModel
from src.models.pipeline import (
    ApiUsage,
    RemediationStrategy,
    ReviewFinding,
)
from src.tools.validator import format_report, validate_document
from src.utils.json_repair import parse_json_lenient

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "review.md"


def _load_prompt() -> str:
    """Load the review prompt template."""
    return PROMPT_PATH.read_text()


def _build_actions_summary(updated_actions: list[dict]) -> str:
    """Build a readable summary of what was done."""
    if not updated_actions:
        return "No actions were executed."

    lines = []
    for a in updated_actions:
        status_icon = {"executed": "OK", "failed": "FAIL", "skipped": "SKIP"}.get(a["status"], "?")
        lines.append(
            f"[{status_icon}] {a['action_type']} on {a['element_id']}: {a['result_detail']}"
        )
    return "\n".join(lines)


def _build_review_doc_json(doc: DocumentModel) -> str:
    """Build concise document JSON for review."""
    data = {
        "metadata": doc.metadata.model_dump(),
        "stats": doc.stats.model_dump(),
        "headings": [
            {"id": p.id, "level": p.heading_level, "text": p.text[:100]}
            for p in doc.paragraphs if p.heading_level is not None
        ],
        "images": [
            {"id": img.id, "alt_text": img.alt_text, "is_decorative": img.is_decorative}
            for img in doc.images
        ],
        "tables": [
            {"id": t.id, "header_row_count": t.header_row_count,
             "first_row": [c.text[:50] for c in t.rows[0]] if t.rows else []}
            for t in doc.tables
        ],
    }
    return json.dumps(data, indent=2, default=str)


def review(
    doc: DocumentModel,
    updated_actions: list[dict],
    model: str = "claude-sonnet-4-5-20250929",
) -> tuple[list[ReviewFinding], list[ApiUsage]]:
    """Review the remediated document.

    Args:
        doc: Re-parsed document model (after remediation).
        updated_actions: Action results from the executor.
        model: Claude model ID.

    Returns:
        Tuple of (list of ReviewFinding, list of ApiUsage).
    """
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return [ReviewFinding(
            finding_type="failure",
            detail="ANTHROPIC_API_KEY not configured",
        )], []

    # Run post-remediation validation
    validation_report = validate_document(doc)
    validation_text = format_report(validation_report)

    # Build the prompt
    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{actions_summary}", _build_actions_summary(updated_actions))
    prompt = prompt.replace("{validation_report}", validation_text)
    prompt = prompt.replace("{document_json}", _build_review_doc_json(doc))

    review_schema = {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "string"},
                        "finding_type": {
                            "type": "string",
                            "enum": ["pass", "concern", "failure", "needs_human_review"],
                        },
                        "detail": {"type": "string"},
                        "criterion": {"type": "string"},
                    },
                    "required": ["element_id", "finding_type", "detail", "criterion"],
                },
            },
            "overall_assessment": {"type": "string"},
            "items_for_human_review": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["findings", "overall_assessment"],
    }

    try:
        client = Anthropic(api_key=api_key, max_retries=5)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=(
                "You are a WCAG 2.1 AA accessibility reviewer. "
                "Respond with ONLY valid JSON matching the requested schema. "
                "No markdown, no code fences, just JSON."
            ),
            messages=[
                {
                    "role": "user",
                    "content": prompt + f"\n\nRespond with JSON matching this schema:\n{json.dumps(review_schema, indent=2)}",
                },
            ],
        )

        review_usage = ApiUsage(
            phase="review",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        response_text = response.content[0].text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        result_data = parse_json_lenient(response_text)
        logger.info("Review complete: %d findings", len(result_data.get("findings", [])))

    except Exception as e:
        logger.exception("Review failed")
        return [ReviewFinding(
            finding_type="failure",
            detail=f"Review failed: {e}",
        )], []

    # Parse findings
    findings = []
    for f in result_data.get("findings", []):
        findings.append(ReviewFinding(
            element_id=f.get("element_id", ""),
            finding_type=f.get("finding_type", "concern"),
            detail=f.get("detail", ""),
            criterion=f.get("criterion", ""),
        ))

    # Add human review items as findings
    for item in result_data.get("items_for_human_review", []):
        findings.append(ReviewFinding(
            finding_type="needs_human_review",
            detail=item,
        ))

    return findings, [review_usage]
