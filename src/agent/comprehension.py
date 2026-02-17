"""Phase 1: Document comprehension via Gemini.

Sends the parsed DocumentModel to Gemini for holistic understanding:
- Document type classification
- Element purpose identification (decorative vs content images, fake headings)
- Image description generation
- Pre-remediation validation summary
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.models.document import DocumentModel
from src.models.pipeline import (
    ComprehensionResult,
    DocumentType,
    ElementPurpose,
)
from src.tools.validator import format_report, validate_document

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "comprehension.md"

# Schema for Gemini structured output
COMPREHENSION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "document_type": {
            "type": "STRING",
            "enum": [dt.value for dt in DocumentType],
        },
        "document_summary": {"type": "STRING"},
        "audience": {"type": "STRING"},
        "suggested_title": {"type": "STRING"},
        "suggested_language": {"type": "STRING"},
        "element_purposes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "element_id": {"type": "STRING"},
                    "purpose": {"type": "STRING"},
                    "is_decorative": {"type": "BOOLEAN"},
                    "suggested_action": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["element_id", "purpose", "is_decorative", "suggested_action", "confidence"],
            },
        },
    },
    "required": ["document_type", "document_summary", "audience", "element_purposes"],
}


def _load_prompt() -> str:
    """Load the comprehension prompt template."""
    return PROMPT_PATH.read_text()


def _build_course_context_str(course_name: str, department: str, description: str) -> str:
    """Format course context for the prompt."""
    parts = []
    if course_name:
        parts.append(f"Course: {course_name}")
    if department:
        parts.append(f"Department: {department}")
    if description:
        parts.append(f"Additional context: {description}")
    return "\n".join(parts) if parts else "No course context provided."


def _build_document_json(doc: DocumentModel) -> str:
    """Serialize document to JSON for the prompt, keeping it concise."""
    # Use model_dump to get dict, then trim for prompt size
    data = doc.model_dump(exclude={"contrast_issues"})

    # Trim long paragraph text to keep prompt manageable
    for para in data.get("paragraphs", []):
        if len(para.get("text", "")) > 200:
            para["text"] = para["text"][:200] + "..."
        # Trim runs text too
        for run in para.get("runs", []):
            if len(run.get("text", "")) > 200:
                run["text"] = run["text"][:200] + "..."

    return json.dumps(data, indent=2, default=str)


def _build_image_descriptions(doc: DocumentModel) -> str:
    """Build image description section for the prompt."""
    if not doc.images:
        return "No images in this document."

    parts = []
    for img in doc.images:
        desc = f"- **{img.id}** ({img.content_type})"
        if img.width_px and img.height_px:
            desc += f" {img.width_px}x{img.height_px}px"
        if img.alt_text:
            desc += f"\n  Current alt text: \"{img.alt_text}\""
        else:
            desc += "\n  No alt text"
        if img.surrounding_text:
            desc += f"\n  Surrounding text: \"{img.surrounding_text[:100]}\""
        if img.paragraph_id:
            desc += f"\n  In paragraph: {img.paragraph_id}"
        parts.append(desc)
    return "\n".join(parts)


def comprehend(
    doc: DocumentModel,
    course_name: str = "",
    department: str = "",
    course_description: str = "",
    model: str = "gemini-2.5-flash",
) -> ComprehensionResult:
    """Run document comprehension via Gemini.

    Args:
        doc: Parsed document model.
        course_name: Faculty-provided course name.
        department: Faculty-provided department.
        course_description: Additional context.
        model: Gemini model ID.

    Returns:
        ComprehensionResult with document understanding.
    """
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        return ComprehensionResult(
            document_summary="Error: GEMINI_API_KEY not configured",
        )

    # Run pre-remediation validation
    validation_report = validate_document(doc)
    validation_text = format_report(validation_report)

    # Build the prompt
    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{course_context}", _build_course_context_str(
        course_name, department, course_description,
    ))
    prompt = prompt.replace("{document_json}", _build_document_json(doc))
    prompt = prompt.replace("{image_descriptions}", _build_image_descriptions(doc))
    prompt = prompt.replace("{validation_summary}", validation_text)

    # Build content parts — text prompt + any images
    content_parts: list = [prompt]

    # Include image data for Gemini vision analysis
    for img in doc.images:
        if img.image_data and not img.is_decorative:
            mime = img.content_type or "image/png"
            content_parts.append(
                types.Part.from_bytes(data=img.image_data, mime_type=mime)
            )
            content_parts.append(f"The image above is {img.id}. Describe what it shows.")

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=content_parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=COMPREHENSION_SCHEMA,
                temperature=0.2,
            ),
        )

        result_data = json.loads(response.text)
        logger.info("Gemini comprehension complete: %s", result_data.get("document_type"))

    except Exception as e:
        logger.exception("Gemini comprehension failed")
        return ComprehensionResult(
            document_summary=f"Comprehension failed: {e}",
            validation_summary=validation_text,
            validation_issues_count=validation_report.failed,
            raw_validation_report=validation_text,
        )

    # Parse element purposes
    element_purposes = []
    for ep in result_data.get("element_purposes", []):
        element_purposes.append(ElementPurpose(
            element_id=ep["element_id"],
            purpose=ep["purpose"],
            is_decorative=ep.get("is_decorative", False),
            suggested_action=ep.get("suggested_action", ""),
            confidence=ep.get("confidence", 0.5),
        ))

    # Parse document type
    doc_type_str = result_data.get("document_type", "other")
    try:
        doc_type = DocumentType(doc_type_str)
    except ValueError:
        doc_type = DocumentType.OTHER

    return ComprehensionResult(
        document_type=doc_type,
        document_summary=result_data.get("document_summary", ""),
        audience=result_data.get("audience", ""),
        element_purposes=element_purposes,
        validation_summary=validation_text,
        validation_issues_count=validation_report.failed,
        raw_validation_report=validation_text,
    )
