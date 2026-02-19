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

from src.utils.json_repair import parse_json_lenient

from dotenv import load_dotenv
from google import genai
from google.genai import types

import fitz  # PyMuPDF — for converting unsupported image formats (WMF, EMF) to PNG

from src.models.document import DocumentModel, ImageInfo
from src.models.pipeline import (
    ApiUsage,
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

# MIME types Gemini supports natively
_GEMINI_SUPPORTED_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Batching and rate limit settings
IMAGES_PER_BATCH = 4           # Smaller batches = less tokens per request
DELAY_BETWEEN_BATCHES = 5      # Seconds between image batches (retry backoff handles real rate limits)
DELAY_BEFORE_COMPREHENSION = 5  # Seconds after images before text comprehension
MAX_RETRIES = 3                # Max retries per Gemini call
INITIAL_BACKOFF = 30           # Seconds for first retry backoff (doubles each retry)


def _convert_image_to_png(image_data: bytes, content_type: str) -> tuple[bytes, str]:
    """Convert unsupported image formats (WMF, EMF, etc.) to PNG via PyMuPDF.

    Returns:
        Tuple of (image_bytes, mime_type). If already supported, returns input unchanged.
    """
    if content_type in _GEMINI_SUPPORTED_MIMES:
        return image_data, content_type

    # Derive filetype hint from MIME (e.g. "image/x-wmf" -> "wmf")
    filetype = content_type.split("/")[-1].replace("x-", "")

    try:
        doc = fitz.open(stream=image_data, filetype=filetype)
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        doc.close()
        logger.info("Converted %s (%d bytes) to PNG (%d bytes)", content_type, len(image_data), len(png_bytes))
        return png_bytes, "image/png"
    except Exception as e:
        logger.warning("Failed to convert %s to PNG: %s", content_type, e)
        return image_data, content_type


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
) -> tuple[dict[str, str], ApiUsage]:
    """Describe a batch of images using Gemini vision.

    Returns:
        Tuple of (dict mapping image_id to detailed alt text, ApiUsage).
    """
    prompt = IMAGE_DESC_PROMPT.replace("{course_context}", course_context)

    content_parts: list = [prompt]
    for img in images:
        mime = img.content_type or "image/png"
        data = img.image_data
        # Convert unsupported formats (WMF, EMF) to PNG
        data, mime = _convert_image_to_png(data, mime)
        content_parts.append(
            types.Part.from_bytes(data=data, mime_type=mime)
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

    usage = _extract_gemini_usage(response, "comprehension_images", model)

    resp_text = response.text
    if resp_text is None:
        logger.warning("Gemini returned empty text for image batch (possible safety block)")
        return {}, usage
    result = parse_json_lenient(resp_text)
    descriptions = {}
    for desc in result.get("descriptions", []):
        img_id = desc.get("image_id", "")
        alt = desc.get("alt_text", "")
        if img_id and alt and not desc.get("is_decorative", False):
            descriptions[img_id] = alt
    return descriptions, usage


def _extract_gemini_usage(response, phase: str, model: str) -> ApiUsage:
    """Extract token usage from a Gemini response."""
    try:
        meta = response.usage_metadata
        return ApiUsage(
            phase=phase,
            model=model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        )
    except Exception:
        return ApiUsage(phase=phase, model=model)


def _is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception is a Gemini rate limit error."""
    err_str = str(error)
    return "429" in err_str or "RESOURCE_EXHAUSTED" in err_str


def _call_with_retry(func, label: str, max_retries: int = MAX_RETRIES):
    """Call a function with exponential backoff on rate limit errors.

    Args:
        func: Zero-arg callable that makes the Gemini API call.
        label: Human-readable label for logging (e.g. "image batch 2/4").
        max_retries: Maximum number of retries on rate limit errors.

    Returns:
        The return value of func().

    Raises:
        The last exception if all retries are exhausted, or any
        non-rate-limit exception immediately.
    """
    last_error = None
    for attempt in range(1 + max_retries):
        try:
            return func()
        except Exception as e:
            last_error = e
            if not _is_rate_limit_error(e):
                # Not a rate limit — don't retry
                logger.warning("%s failed (non-retryable): %s", label, e)
                raise

            if attempt < max_retries:
                backoff = INITIAL_BACKOFF * (2 ** attempt)
                logger.warning(
                    "%s rate limited (attempt %d/%d), waiting %ds before retry: %s",
                    label, attempt + 1, max_retries + 1, backoff, e,
                )
                time.sleep(backoff)
            else:
                logger.error(
                    "%s failed after %d attempts (all rate limited): %s",
                    label, max_retries + 1, e,
                )
    raise last_error  # type: ignore[misc]


def _describe_all_images(
    client: genai.Client,
    doc: DocumentModel,
    course_context: str,
    model: str,
) -> tuple[dict[str, str], list[ApiUsage]]:
    """Describe all content-bearing images, batching to avoid rate limits.

    Uses exponential backoff: if a batch is rate-limited, waits 30s, 60s, 120s
    before retries (up to MAX_RETRIES). Pauses DELAY_BETWEEN_BATCHES seconds
    between successful batches to stay under Gemini's RPM/TPM limits.

    Returns:
        Tuple of (dict mapping image_id to alt text, list of ApiUsage records).
    """
    # Filter to images with actual data that aren't decorative
    content_images = [
        img for img in doc.images
        if img.image_data and not img.is_decorative
    ]

    if not content_images:
        return {}, []

    all_descriptions: dict[str, str] = {}
    all_usage: list[ApiUsage] = []
    total_batches = (len(content_images) + IMAGES_PER_BATCH - 1) // IMAGES_PER_BATCH

    # Process in batches
    for batch_start in range(0, len(content_images), IMAGES_PER_BATCH):
        batch = content_images[batch_start:batch_start + IMAGES_PER_BATCH]
        batch_num = batch_start // IMAGES_PER_BATCH + 1

        logger.info(
            "Describing images batch %d/%d (%d images: %s)",
            batch_num, total_batches, len(batch),
            ", ".join(img.id for img in batch),
        )

        try:
            batch_descs, batch_usage = _call_with_retry(
                lambda b=batch: _describe_images_batch(client, b, course_context, model),
                label=f"Image batch {batch_num}/{total_batches}",
            )
            all_descriptions.update(batch_descs)
            all_usage.append(batch_usage)
            logger.info("Batch %d: got %d descriptions", batch_num, len(batch_descs))
        except Exception as e:
            logger.error("Image batch %d failed permanently: %s", batch_num, e)
            # Continue with remaining batches — partial results better than none

        # Rate limit pause between batches (not after last batch)
        if batch_start + IMAGES_PER_BATCH < len(content_images):
            logger.info("Waiting %ds before next batch...", DELAY_BETWEEN_BATCHES)
            time.sleep(DELAY_BETWEEN_BATCHES)

    logger.info("Image descriptions complete: %d/%d images described",
                len(all_descriptions), len(content_images))
    return all_descriptions, all_usage


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
    usage_records: list[ApiUsage] = []
    if doc.images:
        logger.info("Step 1: Describing %d images via Gemini vision", len(doc.images))
        try:
            image_descriptions, img_usage = _describe_all_images(client, doc, course_context, model)
            usage_records.extend(img_usage)
        except Exception as e:
            logger.warning("Image description failed: %s", e)

    # ── Step 2: Document comprehension (text-only) ──────────────
    logger.info("Step 2: Document comprehension")

    prompt_template = _load_prompt()
    prompt = prompt_template.replace("{course_context}", course_context)
    prompt = prompt.replace("{document_json}", _build_document_json(doc))
    prompt = prompt.replace("{image_descriptions}", _build_image_descriptions(doc))
    prompt = prompt.replace("{validation_summary}", validation_text)

    # Text-only call — no images, just structure analysis (with retry)
    try:
        # Rate limit buffer if we just did image calls
        if doc.images:
            logger.info("Waiting %ds before comprehension call...", DELAY_BEFORE_COMPREHENSION)
            time.sleep(DELAY_BEFORE_COMPREHENSION)

        def _comprehension_call():
            resp = client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=COMPREHENSION_SCHEMA,
                    temperature=0.2,
                ),
            )
            resp_text = resp.text
            if resp_text is None:
                raise ValueError("Gemini returned empty response (possible safety block)")
            return parse_json_lenient(resp_text), _extract_gemini_usage(resp, "comprehension", model)

        result_data, comp_usage = _call_with_retry(_comprehension_call, label="Document comprehension")
        usage_records.append(comp_usage)
        logger.info("Gemini comprehension complete: %s", result_data.get("document_type"))

    except Exception as e:
        logger.exception("Gemini comprehension failed after all retries")
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
        suggested_title=result_data.get("suggested_title", ""),
        suggested_language=result_data.get("suggested_language", ""),
        element_purposes=element_purposes,
        image_descriptions=image_descriptions,
        validation_summary=validation_text,
        validation_issues_count=validation_report.failed,
        raw_validation_report=validation_text,
        api_usage=usage_records,
    )
