"""Extract images from .docx files with alt text, dimensions, and context.

Handles both wp:inline and wp:anchor image positioning.
Alt text is read from wp:docPr[@descr] via raw XML since python-docx
doesn't expose it through its API.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image

from src.models.document import ImageInfo

if TYPE_CHECKING:
    from docx import Document

logger = logging.getLogger(__name__)

# XML namespaces used in docx
_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}


@dataclass
class ImageExtractionResult:
    """Result of image extraction from a docx."""
    images: list[ImageInfo]
    warnings: list[str]


def _get_image_dimensions(image_data: bytes) -> tuple[int | None, int | None]:
    """Get width and height in pixels from image binary data."""
    try:
        with Image.open(io.BytesIO(image_data)) as img:
            return img.width, img.height
    except Exception as e:
        logger.warning("Could not read image dimensions: %s", e)
        return None, None


def _get_surrounding_text(paragraphs_text: list[str], para_index: int, chars: int = 100) -> str:
    """Get ~chars characters of text surrounding the paragraph at para_index."""
    before_parts: list[str] = []
    after_parts: list[str] = []

    # Collect text before
    remaining = chars
    for i in range(para_index - 1, -1, -1):
        t = paragraphs_text[i]
        if len(t) >= remaining:
            before_parts.insert(0, t[-remaining:])
            break
        before_parts.insert(0, t)
        remaining -= len(t)

    # Collect text after
    remaining = chars
    for i in range(para_index + 1, len(paragraphs_text)):
        t = paragraphs_text[i]
        if len(t) >= remaining:
            after_parts.append(t[:remaining])
            break
        after_parts.append(t)
        remaining -= len(t)

    before = " ".join(before_parts).strip()
    after = " ".join(after_parts).strip()
    parts = [p for p in [before, after] if p]
    return " [...] ".join(parts) if parts else ""


def extract_images_from_docx(doc: Document, paragraphs_text: list[str] | None = None) -> ImageExtractionResult:
    """Extract all images from a docx Document with metadata.

    Args:
        doc: An opened python-docx Document.
        paragraphs_text: Optional list of paragraph texts for surrounding context.
            Index must correspond to paragraph order in the document.

    Returns:
        ImageExtractionResult with list of ImageInfo and any warnings.
    """
    images: list[ImageInfo] = []
    warnings: list[str] = []
    image_counter = 0

    if paragraphs_text is None:
        paragraphs_text = [p.text for p in doc.paragraphs]

    # Build a map of relationship IDs to image parts
    rel_map: dict[str, tuple[bytes, str]] = {}
    for rel_id, rel in doc.part.rels.items():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
                ct = rel.target_part.content_type
                rel_map[rel_id] = (blob, ct)
            except Exception as e:
                warnings.append(f"Could not read image for rel {rel_id}: {e}")

    # Walk paragraphs to find images in document order
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_elem = paragraph._element

        # Find all drawing elements (both inline and anchor)
        drawings = para_elem.findall(f".//{{{_NS['w']}}}drawing")

        for drawing in drawings:
            # Look for both wp:inline and wp:anchor
            for position_type in ("inline", "anchor"):
                position_elems = drawing.findall(
                    f"{{{_NS['wp']}}}{position_type}"
                )
                for pos_elem in position_elems:
                    img_id = f"img_{image_counter}"

                    # Get alt text from wp:docPr
                    doc_pr = pos_elem.find(f"{{{_NS['wp']}}}docPr")
                    alt_text = ""
                    if doc_pr is not None:
                        alt_text = doc_pr.get("descr", "")

                    # Get relationship ID from a:blip
                    blip = pos_elem.find(
                        f".//{{{_NS['a']}}}blip"
                    )
                    rel_id = ""
                    if blip is not None:
                        rel_id = blip.get(
                            f"{{{_NS['r']}}}embed", ""
                        )

                    # Get image binary and dimensions
                    image_data = None
                    content_type = ""
                    width_px = None
                    height_px = None

                    if rel_id and rel_id in rel_map:
                        image_data, content_type = rel_map[rel_id]
                        width_px, height_px = _get_image_dimensions(image_data)
                    elif rel_id:
                        warnings.append(
                            f"Image {img_id}: relationship {rel_id} not found in document parts"
                        )

                    # Get surrounding text for context
                    surrounding = _get_surrounding_text(
                        paragraphs_text, para_idx
                    ) if paragraphs_text else ""

                    para_id = f"p_{para_idx}"

                    images.append(ImageInfo(
                        id=img_id,
                        image_data=image_data,
                        content_type=content_type,
                        alt_text=alt_text,
                        width_px=width_px,
                        height_px=height_px,
                        surrounding_text=surrounding,
                        relationship_id=rel_id,
                        paragraph_id=para_id,
                        is_decorative=False,
                    ))

                    image_counter += 1
                    logger.debug(
                        "Extracted %s: alt=%r, rel=%s, size=%sx%s",
                        img_id, alt_text, rel_id, width_px, height_px,
                    )

    missing_alt_count = sum(1 for img in images if not img.alt_text)
    if missing_alt_count:
        logger.info(
            "%d of %d images missing alt text", missing_alt_count, len(images)
        )

    return ImageExtractionResult(images=images, warnings=warnings)
