"""Phase 2: Remediation strategy via Claude.

Takes the comprehension result and document model, produces a
specific remediation plan: which elements to fix, what tools to use,
in what order, with rationale for each action.
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
    ComprehensionResult,
    RemediationAction,
    RemediationStrategy,
)
from src.utils.json_repair import parse_json_lenient

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "strategy.md"


def _load_prompt() -> str:
    """Load the strategy prompt template."""
    return PROMPT_PATH.read_text()


def _build_document_summary(doc: DocumentModel) -> str:
    """Build a concise document summary for strategy planning."""
    data = {
        "metadata": doc.metadata.model_dump(),
        "stats": doc.stats.model_dump(),
        "paragraphs": [],
        "tables": [],
        "images": [],
        "links": [],
    }

    # Include paragraphs with key info only
    for i, para in enumerate(doc.paragraphs):
        p = {
            "id": para.id,
            "index": i,
            "text": para.text[:150] + ("..." if len(para.text) > 150 else ""),
            "style": para.style_name,
            "heading_level": para.heading_level,
            "image_ids": para.image_ids,
            "is_list_item": para.is_list_item,
        }
        if para.fake_heading_signals:
            p["fake_heading_score"] = para.fake_heading_signals.score
        data["paragraphs"].append(p)

    # Tables
    for i, table in enumerate(doc.tables):
        data["tables"].append({
            "id": table.id,
            "index": i,
            "row_count": table.row_count,
            "col_count": table.col_count,
            "header_row_count": table.header_row_count,
            "first_row": [c.text[:50] for c in table.rows[0]] if table.rows else [],
        })

    # Images — include drawing_index for docx multi-image paragraphs
    # Build lookup: image_id -> drawing_index within its paragraph
    img_drawing_idx: dict[str, int] = {}
    for para in doc.paragraphs:
        for di, iid in enumerate(para.image_ids):
            img_drawing_idx[iid] = di

    for img in doc.images:
        img_data = {
            "id": img.id,
            "alt_text": img.alt_text,
            "is_decorative": img.is_decorative,
            "paragraph_id": img.paragraph_id,
            "surrounding_text": img.surrounding_text[:100] if img.surrounding_text else "",
        }
        if img.slide_index is not None:
            img_data["slide_index"] = img.slide_index
            img_data["shape_index"] = img.shape_index
        if img.page_number is not None:
            img_data["page_number"] = img.page_number
        if doc.source_format == "docx" and img.id in img_drawing_idx:
            img_data["drawing_index"] = img_drawing_idx[img.id]
        data["images"].append(img_data)

    # Links
    for link in doc.links:
        data["links"].append({
            "id": link.id,
            "text": link.text[:100],
            "url": link.url,
            "paragraph_id": link.paragraph_id,
        })

    return json.dumps(data, indent=2, default=str)


def _find_paragraph_index(doc: DocumentModel, para_id: str) -> int | None:
    """Find the index of a paragraph by its ID."""
    for i, p in enumerate(doc.paragraphs):
        if p.id == para_id:
            return i
    return None


def _find_table_index(doc: DocumentModel, table_id: str) -> int | None:
    """Find the index of a table by its ID."""
    for i, t in enumerate(doc.tables):
        if t.id == table_id:
            return i
    return None


def _find_image(doc: DocumentModel, img_id: str):
    """Find an ImageInfo by its ID."""
    for img in doc.images:
        if img.id == img_id:
            return img
    return None


def _find_image_paragraph_index(doc: DocumentModel, img_id: str) -> int | None:
    """Find the paragraph index containing an image."""
    for i, p in enumerate(doc.paragraphs):
        if img_id in p.image_ids:
            return i
    return None


def strategize(
    doc: DocumentModel,
    comprehension: ComprehensionResult,
    model: str = "claude-sonnet-4-5-20250929",
) -> RemediationStrategy:
    """Create a remediation strategy via Claude.

    Args:
        doc: Parsed document model.
        comprehension: Result from comprehension phase.
        model: Claude model ID.

    Returns:
        RemediationStrategy with planned actions.
    """
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return RemediationStrategy(
            strategy_summary="Error: ANTHROPIC_API_KEY not configured",
        )

    # Build the prompt
    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{comprehension_json}",
        comprehension.model_dump_json(indent=2))
    prompt = prompt.replace("{document_json}", _build_document_summary(doc))
    prompt = prompt.replace("{validation_report}", comprehension.validation_summary)

    strategy_schema = {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "string"},
                        "action_type": {"type": "string"},
                        "parameters": {"type": "object"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["element_id", "action_type", "parameters", "rationale"],
                },
            },
            "items_for_human_review": {
                "type": "array",
                "items": {"type": "string"},
            },
            "strategy_summary": {"type": "string"},
        },
        "required": ["actions", "strategy_summary"],
    }

    try:
        client = Anthropic(api_key=api_key, max_retries=5)
        response = client.messages.create(
            model=model,
            max_tokens=16384,
            system=(
                "You are a WCAG 2.1 AA accessibility remediation planner. "
                "Respond with ONLY valid JSON matching the requested schema. "
                "No markdown, no code fences, just JSON."
            ),
            messages=[
                {
                    "role": "user",
                    "content": prompt + f"\n\nRespond with JSON matching this schema:\n{json.dumps(strategy_schema, indent=2)}",
                },
            ],
        )

        strategy_usage = ApiUsage(
            phase="strategy",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        response_text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        result_data = parse_json_lenient(response_text)
        logger.info("Strategy created: %d actions", len(result_data.get("actions", [])))

    except Exception as e:
        logger.exception("Strategy creation failed")
        return RemediationStrategy(
            strategy_summary=f"Strategy creation failed: {e}",
        )

    # Build actions with resolved indices
    actions = []
    for action_data in result_data.get("actions", []):
        element_id = action_data["element_id"]
        action_type = action_data["action_type"]
        params = action_data.get("parameters", {})

        # Resolve image location parameters
        if action_type in ("set_alt_text", "set_decorative"):
            if doc.source_format == "pdf":
                # PDF executor uses element_id directly, no index resolution needed
                pass
            elif doc.source_format == "pptx" and "slide_index" not in params:
                # Resolve slide_index and shape_index from image info
                img = _find_image(doc, element_id)
                if img is not None and img.slide_index is not None:
                    params["slide_index"] = img.slide_index
                    params["shape_index"] = img.shape_index
            elif "paragraph_index" not in params:
                idx = _find_image_paragraph_index(doc, element_id)
                if idx is not None:
                    params["paragraph_index"] = idx
                    # Resolve correct drawing_index from image position
                    # within its paragraph's image_ids list
                    if "drawing_index" not in params:
                        para = doc.paragraphs[idx]
                        try:
                            params["drawing_index"] = para.image_ids.index(element_id)
                        except ValueError:
                            params["drawing_index"] = 0

        if action_type == "set_heading_level" and "paragraph_index" not in params:
            idx = _find_paragraph_index(doc, element_id)
            if idx is not None:
                params["paragraph_index"] = idx

        if action_type == "mark_header_rows" and "table_index" not in params:
            idx = _find_table_index(doc, element_id)
            if idx is not None:
                params["table_index"] = idx

        actions.append(RemediationAction(
            element_id=element_id,
            action_type=action_type,
            parameters=params,
            rationale=action_data.get("rationale", ""),
            status="planned",
        ))

    return RemediationStrategy(
        actions=actions,
        items_for_human_review=result_data.get("items_for_human_review", []),
        strategy_summary=result_data.get("strategy_summary", ""),
        api_usage=[strategy_usage],
    )
