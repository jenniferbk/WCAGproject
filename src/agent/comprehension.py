"""Phase 1: Document comprehension via Gemini.

Sends the parsed DocumentModel to Gemini for holistic understanding:
- Document type classification
- Element purpose identification (decorative vs content images, fake headings)
- Image description generation (detailed, vision-analyzed alt text)
- Pre-remediation validation summary

For documents with images, runs a separate image description pass
using Gemini's vision capabilities before the main comprehension call.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.models.document import DocumentModel, ImageInfo
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

# Schema for image description response
IMAGE_DESC_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "descriptions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "image_id": {"type": "STRING"},
                    "alt_text": {"type": "STRING"},
                    "is_decorative": {"type": "BOOLEAN"},
                },
                "required": ["image_id", "alt_text", "is_decorative"],
            },
        },
    },
    "required": ["descriptions"],
}

IMAGE_DESC_PROMPT = """You are an accessibility specialist writing alt text for images in a university course document.

## Document Context
- Course: {course_context}
- Document summary: This is course material for university students.

## Your Task

For EACH image below, write a **thorough, detailed alt text description**. This is critically important for accessibility — a blind student must be able to understand the image as completely as a sighted student.

## Alt Text Guidelines

- **Be thorough and specific.** Describe ALL visible content: text, numbers, labels, arrows, colors used meaningfully, spatial relationships.
- **For mathematical content:** Include every equation, variable, number, graph feature, axis label, and data point visible.
- **For diagrams and charts:** Describe the structure (what connects to what, how things are organized), all labels, and the relationships shown.
- **For handwritten work:** Transcribe ALL legible handwriting. Note what is written, crossed out, circled, or highlighted.
- **For screenshots of text or software:** Transcribe the visible text fully, describe the interface elements shown.
- **For photos of student work:** Describe what the student has written, drawn, or created in detail.
- **For graphs:** Describe axes, scales, data points, trends, labels, and any annotations.
- **For tables or grids:** Describe the structure, headers, and cell contents.
- **Context matters:** Explain what the image means in the context of the surrounding document content.
- **Do NOT say** "image of" or "picture of" — start directly with what is shown.
- **Minimum 2-3 sentences** for any content-bearing image. Complex images (diagrams, charts, student work) need as much detail as needed — there is no maximum length.
- If an image is purely decorative (logo, divider, background pattern), mark is_decorative as true and provide a brief note.

## Images

The following images are from the document. Each image is preceded by its ID and context.
"""

# Max images per batch to stay within rate limits
IMAGES_PER_BATCH = 8


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
    data = doc.model_dump(exclude={"contrast_issues"})

    # Trim long paragraph text to keep prompt manageable
    for para in data.get("paragraphs", []):
        if len(para.get("text", "")) > 200:
            para["text"] = para["text"][:200] + "..."
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


def _describe_images_batch(
    client: genai.Client,
    images: list[ImageInfo],
    course_context: str,
    model: str,
) -> dict[str, str]:
    """Describe a batch of images using Gemini vision.

    Returns:
        Dict mapping image_id to detailed alt text.
    """
    prompt = IMAGE_DESC_PROMPT.replace("{course_context}", course_context)

    content_parts: list = [prompt]
    for img in images:
        mime = img.content_type or "image/png"
        content_parts.append(
            types.Part.from_bytes(data=img.image_data, mime_type=mime)
        )
        context = ""
        if img.surrounding_text:
            context = f" Context from surrounding text: \"{img.surrounding_text[:150]}\""
        content_parts.append(
            f"The image above is {img.id}.{context} Describe what it shows in detail."
        )

    response = client.models.generate_content(
        model=model,
        contents=content_parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=IMAGE_DESC_SCHEMA,
            temperature=0.2,
        ),
    )

    result = json.loads(response.text)
    descriptions = {}
    for desc in result.get("descriptions", []):
        img_id = desc.get("image_id", "")
        alt = desc.get("alt_text", "")
        if img_id and alt and not desc.get("is_decorative", False):
            descriptions[img_id] = alt
    return descriptions


def _describe_all_images(
    client: genai.Client,
    doc: DocumentModel,
    course_context: str,
    model: str,
) -> dict[str, str]:
    """Describe all content-bearing images, batching to avoid rate limits.

    Returns:
        Dict mapping image_id to detailed alt text.
    """
    # Filter to images with actual data that aren't decorative
    content_images = [
        img for img in doc.images
        if img.image_data and not img.is_decorative
    ]

    if not content_images:
        return {}

    all_descriptions: dict[str, str] = {}

    # Process in batches
    for batch_start in range(0, len(content_images), IMAGES_PER_BATCH):
        batch = content_images[batch_start:batch_start + IMAGES_PER_BATCH]
        batch_num = batch_start // IMAGES_PER_BATCH + 1
        total_batches = (len(content_images) + IMAGES_PER_BATCH - 1) // IMAGES_PER_BATCH

        logger.info(
            "Describing images batch %d/%d (%d images: %s)",
            batch_num, total_batches, len(batch),
            ", ".join(img.id for img in batch),
        )

        try:
            batch_descs = _describe_images_batch(client, batch, course_context, model)
            all_descriptions.update(batch_descs)
            logger.info("Batch %d: got %d descriptions", batch_num, len(batch_descs))

            # Rate limit pause between batches
            if batch_start + IMAGES_PER_BATCH < len(content_images):
                time.sleep(5)

        except Exception as e:
            logger.warning("Image batch %d failed: %s", batch_num, e)
            # Continue with remaining batches
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                logger.info("Rate limited, waiting 60s before next batch")
                time.sleep(60)

    logger.info("Image descriptions complete: %d/%d images described",
                len(all_descriptions), len(content_images))
    return all_descriptions


def comprehend(
    doc: DocumentModel,
    course_name: str = "",
    department: str = "",
    course_description: str = "",
    model: str = "gemini-2.5-flash",
) -> ComprehensionResult:
    """Run document comprehension via Gemini.

    Two-step process:
    1. Describe all images using Gemini vision (batched)
    2. Analyze document structure and purposes (text-only, fast)

    Args:
        doc: Parsed document model.
        course_name: Faculty-provided course name.
        department: Faculty-provided department.
        course_description: Additional context.
        model: Gemini model ID.

    Returns:
        ComprehensionResult with document understanding and image descriptions.
    """
    load_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY not set")
        return ComprehensionResult(
            document_summary="Error: GEMINI_API_KEY not configured",
        )

    client = genai.Client(api_key=api_key)
    course_context = _build_course_context_str(course_name, department, course_description)

    # Run pre-remediation validation
    validation_report = validate_document(doc)
    validation_text = format_report(validation_report)

    # ── Step 1: Describe images ─────────────────────────────────
    image_descriptions: dict[str, str] = {}
    if doc.images:
        logger.info("Step 1: Describing %d images via Gemini vision", len(doc.images))
        try:
            image_descriptions = _describe_all_images(client, doc, course_context, model)
        except Exception as e:
            logger.warning("Image description failed: %s", e)

    # ── Step 2: Document comprehension (text-only) ──────────────
    logger.info("Step 2: Document comprehension")

    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{course_context}", course_context)
    prompt = prompt.replace("{document_json}", _build_document_json(doc))
    prompt = prompt.replace("{image_descriptions}", _build_image_descriptions(doc))
    prompt = prompt.replace("{validation_summary}", validation_text)

    # Text-only call — no images, just structure analysis
    try:
        # Rate limit buffer if we just did image calls
        if image_descriptions:
            time.sleep(3)

        response = client.models.generate_content(
            model=model,
            contents=[prompt],
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
            image_descriptions=image_descriptions,
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
        image_descriptions=image_descriptions,
        validation_summary=validation_text,
        validation_issues_count=validation_report.failed,
        raw_validation_report=validation_text,
    )
