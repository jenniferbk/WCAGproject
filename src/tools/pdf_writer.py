"""In-place PDF modification for accessibility remediation (Tier 1 only).

Modifies a copy of the original PDF to add accessibility metadata
while preserving 100% visual fidelity. Used as a fallback when
the iText tagger (java/itext-tagger/) is unavailable.

Tier 1 (deterministic, always safe):
  - Set title and language in PDF metadata
  - Set alt text on images via structure tree

Note: Tier 2 operations (heading tags via content stream, contrast fixes)
have been moved to the iText Java tagger, which handles position-based
matching properly with full font decoding. The content stream manipulation
approach in Python cannot work with CIDFont/Identity-H encoded PDFs.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from src.models.document import DocumentModel, ImageInfo

logger = logging.getLogger(__name__)


@dataclass
class PdfWriteResult:
    """Result of applying fixes to a PDF."""
    success: bool
    output_path: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    heading_tags_applied: int = 0
    contrast_fixes_applied: int = 0


def apply_pdf_fixes(
    source_path: str | Path,
    doc_model: DocumentModel,
    title: str = "",
    language: str = "en",
    alt_texts: dict[str, str] | None = None,
    decorative_ids: set[str] | None = None,
    heading_actions: list[dict] | None = None,
    contrast_fixes: list[dict] | None = None,
    output_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    verify_visually: bool = True,
) -> PdfWriteResult:
    """Apply accessibility fixes to a PDF file in-place.

    Creates a modified copy of the original PDF. Visual fidelity
    is preserved — the output should look identical to the input.

    Args:
        source_path: Path to the original PDF.
        doc_model: Parsed DocumentModel from the PDF.
        title: Document title for metadata.
        language: BCP 47 language tag (default "en").
        alt_texts: Map of image_id -> alt text string.
        decorative_ids: Set of image_ids to mark as decorative (empty alt).
        heading_actions: List of dicts with keys: element_id, level, text.
        contrast_fixes: List of dicts with keys: element_id, run_index,
            original_color, fixed_color.
        output_path: Explicit output path. If None, auto-generated.
        output_dir: Directory for output. Used if output_path is None.
        verify_visually: Whether to use visual verification for Tier 2 ops.

    Returns:
        PdfWriteResult with success/failure and change log.
    """
    source_path = Path(source_path)
    alt_texts = alt_texts or {}
    decorative_ids = decorative_ids or set()
    heading_actions = heading_actions or []
    contrast_fixes = contrast_fixes or []

    if not source_path.exists():
        return PdfWriteResult(
            success=False, errors=[f"Source file not found: {source_path}"]
        )

    # Determine output path
    if output_path:
        out_path = Path(output_path)
    elif output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{source_path.stem}_remediated.pdf"
    else:
        out_path = source_path.parent / f"{source_path.stem}_remediated.pdf"

    # Copy source to output
    shutil.copy2(source_path, out_path)

    changes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    heading_tags_applied = 0
    contrast_fixes_applied = 0

    try:
        doc = fitz.open(str(out_path))
    except Exception as e:
        return PdfWriteResult(
            success=False, errors=[f"Failed to open PDF: {e}"]
        )

    try:
        # ── Tier 1: Metadata + Alt Text (always safe) ──────────────

        # Set title and language
        if title or language:
            meta_changes = _set_metadata(doc, title, language)
            changes.extend(meta_changes)

        # Set alt text on images
        if alt_texts or decorative_ids:
            alt_changes, alt_warnings = _set_alt_texts(
                doc, doc_model, alt_texts, decorative_ids
            )
            changes.extend(alt_changes)
            warnings.extend(alt_warnings)

        # Note: Tier 2 (heading tags, contrast fixes via content stream)
        # has been superseded by the iText Java tagger. Those parameters
        # are accepted but ignored here — they are handled by itext_tagger.py.
        if heading_actions:
            warnings.append(
                f"Tier 2 heading tags ({len(heading_actions)} actions) skipped — use iText tagger"
            )
        if contrast_fixes:
            warnings.append(
                f"Tier 2 contrast fixes ({len(contrast_fixes)} actions) skipped — use iText tagger"
            )

        # Save the modified PDF
        doc.save(str(out_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        logger.info(
            "PDF fixes applied: %s (%d changes, %d warnings, %d errors)",
            out_path, len(changes), len(warnings), len(errors),
        )

        return PdfWriteResult(
            success=True,
            output_path=str(out_path),
            changes=changes,
            warnings=warnings,
            errors=errors,
            heading_tags_applied=heading_tags_applied,
            contrast_fixes_applied=contrast_fixes_applied,
        )

    except Exception as e:
        logger.exception("Failed to apply PDF fixes")
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass
        return PdfWriteResult(
            success=False,
            output_path=str(out_path),
            changes=changes,
            warnings=warnings,
            errors=[f"Fatal error: {e}"] + errors,
        )


def apply_contrast_fixes_to_pdf(
    pdf_path: str | Path,
    color_map: dict[str, str],
    verify: bool = True,
) -> PdfWriteResult:
    """Apply contrast color fixes to an existing PDF (post-iText tagging).

    Opens the PDF, scans each page's content stream for color operators
    matching the original colors, replaces with the fixed colors, and saves.

    Args:
        pdf_path: Path to the PDF to modify (in-place).
        color_map: Dict of original_hex -> fixed_hex (e.g. {"#C0C0C0": "#595959"}).
        verify: Whether to verify changes don't corrupt layout.

    Returns:
        PdfWriteResult with change log.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PdfWriteResult(success=False, errors=[f"PDF not found: {pdf_path}"])

    if not color_map:
        return PdfWriteResult(success=True, output_path=str(pdf_path))

    # Build fix list
    fixes = []
    for orig, fixed in color_map.items():
        if orig != fixed:
            fixes.append({"original_color": orig, "fixed_color": fixed})

    if not fixes:
        return PdfWriteResult(success=True, output_path=str(pdf_path))

    changes: list[str] = []
    warnings: list[str] = []
    total_fixed = 0

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return PdfWriteResult(success=False, errors=[f"Failed to open PDF: {e}"])

    try:
        for page_idx in range(len(doc)):
            page_changes = _apply_contrast_fixes(
                doc, page_idx, fixes, verify=verify
            )
            changes.extend(page_changes)
            total_fixed += len(page_changes)

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        logger.info("Contrast fixes applied: %d changes across %s", total_fixed, pdf_path)

        return PdfWriteResult(
            success=True,
            output_path=str(pdf_path),
            changes=changes,
            warnings=warnings,
            contrast_fixes_applied=total_fixed,
        )

    except Exception as e:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass
        return PdfWriteResult(
            success=False,
            output_path=str(pdf_path),
            changes=changes,
            warnings=warnings,
            errors=[f"Contrast fix error: {e}"],
        )


def strip_struct_tree(source_path: str | Path, output_path: str | Path) -> bool:
    """Remove existing structure tree from a PDF.

    Creates a copy without StructTreeRoot so iText can build a clean
    structure from scratch. Prevents duplicate /Figure elements when
    the original PDF already has structure tags (e.g., from PowerPoint export).

    Args:
        source_path: Path to the original PDF.
        output_path: Path for the stripped copy.

    Returns:
        True if the struct tree was stripped (or didn't exist), False on error.
    """
    try:
        doc = fitz.open(str(source_path))
        catalog_xref = doc.pdf_catalog()

        # Check if there's a StructTreeRoot to remove
        sroot = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if sroot[0] == "xref":
            doc.xref_set_key(catalog_xref, "StructTreeRoot", "null")
            logger.info("Stripped existing StructTreeRoot from %s", source_path)

        # Remove MarkInfo (marked content info)
        mark_info = doc.xref_get_key(catalog_xref, "MarkInfo")
        if mark_info[0] != "null":
            doc.xref_set_key(catalog_xref, "MarkInfo", "null")

        doc.save(str(output_path))
        doc.close()
        return True
    except Exception as e:
        logger.warning("Failed to strip structure tree from %s: %s", source_path, e)
        return False


def update_existing_figure_alt_texts(
    pdf_path: str | Path,
    alt_texts: dict[str, str],
    doc_model: DocumentModel,
) -> PdfWriteResult:
    """Update /Alt on ALL /Figure struct elements in an already-tagged PDF.

    Called after iText tagging to fix existing /Figure elements (from the
    original PDF structure) that still have missing or filename-only alt text.
    iText adds NEW /Figure elements but doesn't touch pre-existing ones.

    Args:
        pdf_path: Path to the tagged PDF (modified in-place).
        alt_texts: Mapping of image_id -> alt text from the strategy.
        doc_model: Parsed document model with image metadata.

    Returns:
        PdfWriteResult with change log.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return PdfWriteResult(success=False, errors=[f"PDF not found: {pdf_path}"])

    if not alt_texts:
        return PdfWriteResult(success=True, output_path=str(pdf_path))

    changes: list[str] = []
    warnings: list[str] = []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return PdfWriteResult(success=False, errors=[f"Failed to open PDF: {e}"])

    try:
        if not _is_tagged(doc):
            doc.close()
            return PdfWriteResult(success=True, output_path=str(pdf_path))

        # Build lookup: image xref -> (image_id, alt_text)
        xref_to_alt: dict[int, tuple[str, str]] = {}
        page_to_alts: dict[int, list[tuple[str, str]]] = {}
        for img in doc_model.images:
            if img.id in alt_texts:
                alt = alt_texts[img.id]
                if img.xref is not None:
                    xref_to_alt[img.xref] = (img.id, alt)
                if img.page_number is not None:
                    page_to_alts.setdefault(img.page_number, []).append((img.id, alt))

        # Find all /Figure elements in the entire structure tree
        catalog_xref = doc.pdf_catalog()
        sroot = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if sroot[0] != "xref":
            doc.close()
            return PdfWriteResult(success=True, output_path=str(pdf_path))

        sroot_xref = int(sroot[1].split()[0])
        figure_xrefs = _find_struct_elements_by_type(doc, sroot_xref, "/Figure")

        updated = 0
        for fig_xref in figure_xrefs:
            # Check current alt text
            current_alt = doc.xref_get_key(fig_xref, "Alt")
            current_text = ""
            if current_alt[0] == "string":
                current_text = current_alt[1]

            # Skip if already has good alt text (> 20 chars, not a filename)
            if current_text and len(current_text) > 20 and not _is_filename_alt(current_text):
                continue

            # Try to match by A11yXref (set by iText)
            a11y_xref = doc.xref_get_key(fig_xref, "A11yXref")
            if a11y_xref[0] == "int":
                img_xref = int(a11y_xref[1])
                if img_xref in xref_to_alt:
                    img_id, alt = xref_to_alt[img_xref]
                    doc.xref_set_key(fig_xref, "Alt", _pdf_string(alt))
                    changes.append(f"Updated alt on {img_id} (A11yXref {img_xref})")
                    updated += 1
                    continue

            # Try to match by page
            pg_val = doc.xref_get_key(fig_xref, "Pg")
            if pg_val[0] == "xref":
                page_obj_xref = int(pg_val[1].split()[0])
                for page_idx in range(len(doc)):
                    try:
                        if doc[page_idx].xref == page_obj_xref:
                            if page_idx in page_to_alts and page_to_alts[page_idx]:
                                img_id, alt = page_to_alts[page_idx].pop(0)
                                doc.xref_set_key(fig_xref, "Alt", _pdf_string(alt))
                                changes.append(f"Updated alt on {img_id} (page {page_idx})")
                                updated += 1
                            break
                    except Exception:
                        continue

        doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        if updated:
            logger.info("Updated alt text on %d existing /Figure elements in %s", updated, pdf_path)

        return PdfWriteResult(
            success=True,
            output_path=str(pdf_path),
            changes=changes,
            warnings=warnings,
        )

    except Exception as e:
        try:
            if not doc.is_closed:
                doc.close()
        except Exception:
            pass
        return PdfWriteResult(
            success=False,
            output_path=str(pdf_path),
            changes=changes,
            warnings=warnings,
            errors=[f"Figure alt text update error: {e}"],
        )


def _is_filename_alt(text: str) -> bool:
    """Check if alt text appears to be a filename rather than a description."""
    text = text.strip()
    # Common image filename patterns
    if text.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".svg")):
        return True
    if text.startswith("Screen Shot ") or text.startswith("image"):
        return True
    # Very short text that's likely auto-generated
    if len(text) < 10 and "." in text:
        return True
    return False


# ── Tier 1: Metadata ────────────────────────────────────────────────

def _set_metadata(doc: fitz.Document, title: str, language: str) -> list[str]:
    """Set PDF title in /Info and language on the catalog."""
    changes: list[str] = []

    if title:
        meta = doc.metadata or {}
        meta["title"] = title
        doc.set_metadata(meta)
        changes.append(f"Set PDF title: {title}")

    if language:
        try:
            catalog_xref = doc.pdf_catalog()
            if catalog_xref:
                doc.xref_set_key(
                    catalog_xref, "Lang", _pdf_string(language)
                )
                changes.append(f"Set PDF language: {language}")
        except Exception as e:
            changes.append(f"Failed to set language: {e}")

    return changes


# ── Tier 1: Alt Text ────────────────────────────────────────────────

def _set_alt_texts(
    doc: fitz.Document,
    doc_model: DocumentModel,
    alt_texts: dict[str, str],
    decorative_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Set alt text on images in the PDF structure tree.

    For tagged PDFs: find existing /Figure struct elements and set /Alt.
    For untagged PDFs: create a minimal structure tree with /Figure elements.
    """
    changes: list[str] = []
    warnings: list[str] = []

    if not alt_texts and not decorative_ids:
        return changes, warnings

    is_tagged = _is_tagged(doc)

    if is_tagged:
        tag_changes, tag_warnings = _set_alt_texts_tagged(
            doc, doc_model, alt_texts, decorative_ids
        )
        changes.extend(tag_changes)
        warnings.extend(tag_warnings)
    else:
        untag_changes, untag_warnings = _set_alt_texts_untagged(
            doc, doc_model, alt_texts, decorative_ids
        )
        changes.extend(untag_changes)
        warnings.extend(untag_warnings)

    return changes, warnings


def _is_tagged(doc: fitz.Document) -> bool:
    """Check if the PDF has a structure tree (is tagged)."""
    try:
        catalog_xref = doc.pdf_catalog()
        if not catalog_xref:
            return False
        mark_info = doc.xref_get_key(catalog_xref, "MarkInfo")
        struct_root = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        return struct_root[0] == "xref"
    except Exception:
        return False


def _set_alt_texts_tagged(
    doc: fitz.Document,
    doc_model: DocumentModel,
    alt_texts: dict[str, str],
    decorative_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Set /Alt on existing /Figure struct elements in tagged PDFs."""
    changes: list[str] = []
    warnings: list[str] = []

    # Build a map from image xref to image info for matching
    xref_to_img: dict[int, ImageInfo] = {}
    for img in doc_model.images:
        if img.xref is not None:
            xref_to_img[img.xref] = img

    # Traverse struct tree to find /Figure elements
    try:
        catalog_xref = doc.pdf_catalog()
        sroot = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if sroot[0] != "xref":
            warnings.append("No StructTreeRoot found in tagged PDF")
            return changes, warnings

        sroot_xref = int(sroot[1].split()[0])
        figure_xrefs = _find_struct_elements_by_type(doc, sroot_xref, "/Figure")

        for fig_xref in figure_xrefs:
            # Try to match this figure to one of our images
            # by checking its content reference
            matched_img = _match_figure_to_image(doc, fig_xref, xref_to_img)
            if not matched_img:
                continue

            if matched_img.id in alt_texts:
                alt = alt_texts[matched_img.id]
                doc.xref_set_key(fig_xref, "Alt", _pdf_string(alt))
                changes.append(
                    f"Set alt text on {matched_img.id} (xref {fig_xref}): {alt[:60]}"
                )
            elif matched_img.id in decorative_ids:
                doc.xref_set_key(fig_xref, "Alt", _pdf_string(""))
                changes.append(
                    f"Marked {matched_img.id} as decorative (xref {fig_xref})"
                )

    except Exception as e:
        warnings.append(f"Error setting alt text on tagged PDF: {e}")

    # Report images we couldn't match
    all_ids = set(alt_texts.keys()) | decorative_ids
    matched_ids = {c.split()[4] for c in changes if c.startswith("Set alt") or c.startswith("Marked")}
    # Use a simpler approach to track which images were handled
    handled = set()
    for c in changes:
        for img_id in all_ids:
            if img_id in c:
                handled.add(img_id)
    unhandled = all_ids - handled
    if unhandled:
        warnings.append(
            f"Could not find struct elements for images: {', '.join(sorted(unhandled))}"
        )
        # Fall back to creating new struct elements for unhandled images
        fallback_changes, fallback_warnings = _set_alt_texts_untagged(
            doc, doc_model, {k: alt_texts[k] for k in unhandled if k in alt_texts},
            unhandled & decorative_ids,
        )
        changes.extend(fallback_changes)
        warnings.extend(fallback_warnings)

    return changes, warnings


def _set_alt_texts_untagged(
    doc: fitz.Document,
    doc_model: DocumentModel,
    alt_texts: dict[str, str],
    decorative_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Create structure tree and add /Figure elements for untagged PDFs."""
    changes: list[str] = []
    warnings: list[str] = []

    if not alt_texts and not decorative_ids:
        return changes, warnings

    try:
        # Ensure structure tree exists
        struct_root_xref = _ensure_struct_tree(doc)
        if struct_root_xref is None:
            warnings.append("Failed to create structure tree")
            return changes, warnings

        # Create /Figure struct elements for each image
        all_ids = set(alt_texts.keys()) | decorative_ids
        for img in doc_model.images:
            if img.id not in all_ids:
                continue

            alt = alt_texts.get(img.id, "")
            if img.id in decorative_ids:
                alt = ""

            try:
                _add_figure_with_alt(
                    doc, struct_root_xref, img, alt
                )
                if img.id in decorative_ids:
                    changes.append(f"Created /Figure for {img.id} (decorative)")
                else:
                    changes.append(
                        f"Created /Figure for {img.id} with alt: {alt[:60]}"
                    )
            except Exception as e:
                warnings.append(f"Failed to create /Figure for {img.id}: {e}")

    except Exception as e:
        warnings.append(f"Error creating structure tree: {e}")

    return changes, warnings


def _ensure_struct_tree(doc: fitz.Document) -> int | None:
    """Create a minimal StructTreeRoot if one doesn't exist. Returns its xref."""
    try:
        catalog_xref = doc.pdf_catalog()
        if not catalog_xref:
            return None

        # Check if StructTreeRoot already exists
        sroot = doc.xref_get_key(catalog_xref, "StructTreeRoot")
        if sroot[0] == "xref":
            return int(sroot[1].split()[0])

        # Create new StructTreeRoot object
        # /Type /StructTreeRoot /K [] /ParentTree (placeholder)
        sroot_xref = doc.get_new_xref()
        doc.update_object(
            sroot_xref,
            "<<\n/Type /StructTreeRoot\n/K []\n>>",
        )

        # Link it from the catalog
        doc.xref_set_key(catalog_xref, "StructTreeRoot", f"{sroot_xref} 0 R")

        # Set MarkInfo to indicate this is a tagged PDF
        doc.xref_set_key(catalog_xref, "MarkInfo", "<</Marked true>>")

        return sroot_xref

    except Exception as e:
        logger.warning("Failed to create StructTreeRoot: %s", e)
        return None


def _add_figure_with_alt(
    doc: fitz.Document,
    struct_root_xref: int,
    img: ImageInfo,
    alt_text: str,
) -> None:
    """Create a /Figure struct element with /Alt and add it to the struct tree."""
    # Create the /Figure struct element
    fig_xref = doc.get_new_xref()
    alt_encoded = _pdf_string(alt_text)

    # Build the Figure object — references the StructTreeRoot as parent
    fig_obj = (
        f"<<\n"
        f"/Type /StructElem\n"
        f"/S /Figure\n"
        f"/P {struct_root_xref} 0 R\n"
        f"/Alt {alt_encoded}\n"
        f">>"
    )
    doc.update_object(fig_xref, fig_obj)

    # Add this Figure to the StructTreeRoot's /K array
    k_val = doc.xref_get_key(struct_root_xref, "K")
    if k_val[0] == "array":
        # Append to existing array
        existing = k_val[1].strip("[]").strip()
        if existing:
            new_k = f"[{existing} {fig_xref} 0 R]"
        else:
            new_k = f"[{fig_xref} 0 R]"
    elif k_val[0] == "xref":
        # Convert single reference to array
        new_k = f"[{k_val[1]} {fig_xref} 0 R]"
    else:
        new_k = f"[{fig_xref} 0 R]"

    doc.xref_set_key(struct_root_xref, "K", new_k)


def _find_struct_elements_by_type(
    doc: fitz.Document,
    parent_xref: int,
    elem_type: str,
    max_depth: int = 8,
) -> list[int]:
    """Recursively find struct elements of a given type (e.g. /Figure)."""
    if max_depth <= 0:
        return []
    results: list[int] = []

    try:
        # Check if this element is the target type
        stype = doc.xref_get_key(parent_xref, "S")
        if stype[0] == "name" and stype[1] == elem_type:
            results.append(parent_xref)

        # Recurse into children
        k_val = doc.xref_get_key(parent_xref, "K")
        child_xrefs = _parse_xref_array(k_val)
        for cx in child_xrefs:
            results.extend(
                _find_struct_elements_by_type(doc, cx, elem_type, max_depth - 1)
            )
    except Exception:
        pass

    return results


def _match_figure_to_image(
    doc: fitz.Document,
    fig_xref: int,
    xref_to_img: dict[int, ImageInfo],
) -> ImageInfo | None:
    """Try to match a /Figure struct element to an ImageInfo by examining its content."""
    try:
        # Check /K for an OBJR (object reference) pointing to the image
        k_val = doc.xref_get_key(fig_xref, "K")

        if k_val[0] == "dict":
            # Inline dict — might have /Obj reference
            obj_ref = doc.xref_get_key(fig_xref, "K")
            # This is tricky with inline dicts; fall through to page-based matching
            pass
        elif k_val[0] == "xref":
            child_xref = int(k_val[1].split()[0])
            # Check if this child is an OBJR
            obj_type = doc.xref_get_key(child_xref, "Type")
            if obj_type[0] == "name" and obj_type[1] == "/OBJR":
                obj_val = doc.xref_get_key(child_xref, "Obj")
                if obj_val[0] == "xref":
                    target_xref = int(obj_val[1].split()[0])
                    if target_xref in xref_to_img:
                        return xref_to_img[target_xref]

        # Fall back: match by page number and order
        pg_val = doc.xref_get_key(fig_xref, "Pg")
        if pg_val[0] == "xref":
            page_xref = int(pg_val[1].split()[0])
            for img_xref, img_info in xref_to_img.items():
                if img_info.page_number is not None:
                    try:
                        page = doc[img_info.page_number]
                        if page.xref == page_xref:
                            return img_info
                    except Exception:
                        continue

    except Exception:
        pass
    return None


def _parse_xref_array(k_val: tuple) -> list[int]:
    """Parse an xref value that could be a single ref or array of refs."""
    xrefs: list[int] = []
    if k_val[0] == "xref":
        xrefs.append(int(k_val[1].split()[0]))
    elif k_val[0] == "array":
        raw = k_val[1].strip("[]")
        parts = raw.split()
        i = 0
        while i < len(parts):
            if (
                i + 2 < len(parts)
                and parts[i + 1] == "0"
                and parts[i + 2] == "R"
            ):
                try:
                    xrefs.append(int(parts[i]))
                except ValueError:
                    pass
                i += 3
            else:
                i += 1
    return xrefs


# ── Tier 2: Content Stream Modifications ─────────────────────────────


@dataclass
class Token:
    """A token from a PDF content stream."""
    value: str
    type: str  # "operator", "operand", "other"


def _apply_heading_tags(
    doc: fitz.Document,
    page_idx: int,
    headings: list[dict],
    max_attempts: int = 5,
    verify: bool = True,
) -> list[str]:
    """Tag heading text in the content stream with BDC/EMC + StructElems.

    Uses visual verification: heading tags should be invisible — if the
    rendered page changes by more than 0.5%, the modification is reverted.

    Args:
        doc: Open PyMuPDF document.
        page_idx: 0-based page index.
        headings: List of dicts with element_id, level, text.
        max_attempts: Max retry attempts.
        verify: Whether to visually verify (pixel diff).

    Returns:
        List of change descriptions.
    """
    changes: list[str] = []

    if page_idx >= len(doc):
        return changes

    page = doc[page_idx]

    # Ensure struct tree exists
    struct_root_xref = _ensure_struct_tree(doc)
    if struct_root_xref is None:
        return changes

    for heading in headings:
        element_id = heading.get("element_id", "")
        level = heading.get("level", 1)
        text = heading.get("text", "")
        tag_name = f"H{min(level, 6)}"

        if not text:
            continue

        # Render baseline
        if verify:
            baseline_png = _render_page(doc, page_idx)

        # Read the content stream
        try:
            xref = page.xref
            stream_bytes = doc.xref_stream(xref)
            if not stream_bytes:
                # Try the page's /Contents
                contents = doc.xref_get_key(xref, "Contents")
                if contents[0] == "xref":
                    stream_xref = int(contents[1].split()[0])
                    stream_bytes = doc.xref_stream(stream_xref)

            if not stream_bytes:
                continue
        except Exception:
            continue

        success = False
        for attempt in range(max_attempts):
            try:
                tokens = _tokenize_content_stream(stream_bytes)
                matches = _find_text_in_stream(tokens, text)

                if not matches:
                    break  # text not found, no point retrying

                # Create struct element for this heading
                mcid = attempt  # unique per attempt
                heading_xref = doc.get_new_xref()
                doc.update_object(
                    heading_xref,
                    f"<<\n/Type /StructElem\n/S /{tag_name}\n"
                    f"/P {struct_root_xref} 0 R\n"
                    f"/K <</Type /MCR /MCID {mcid} /Pg {page.xref} 0 R>>\n"
                    f">>",
                )

                # Add to struct tree
                k_val = doc.xref_get_key(struct_root_xref, "K")
                if k_val[0] == "array":
                    existing = k_val[1].strip("[]").strip()
                    if existing:
                        new_k = f"[{existing} {heading_xref} 0 R]"
                    else:
                        new_k = f"[{heading_xref} 0 R]"
                elif k_val[0] == "xref":
                    new_k = f"[{k_val[1]} {heading_xref} 0 R]"
                else:
                    new_k = f"[{heading_xref} 0 R]"
                doc.xref_set_key(struct_root_xref, "K", new_k)

                # Inject BDC/EMC around the first match
                match = matches[0]
                modified_tokens = _inject_bdc_emc(
                    tokens, match, mcid, tag_name
                )
                new_stream = _reassemble_stream(modified_tokens)

                # Write back
                contents = doc.xref_get_key(page.xref, "Contents")
                if contents[0] == "xref":
                    stream_xref = int(contents[1].split()[0])
                    doc.update_stream(stream_xref, new_stream)
                else:
                    doc.update_stream(page.xref, new_stream)

                # Verify
                if verify:
                    modified_png = _render_page(doc, page_idx)
                    diff = _pixel_diff(baseline_png, modified_png)
                    if diff > 0.5:
                        # Revert — restore original stream
                        if contents[0] == "xref":
                            doc.update_stream(stream_xref, stream_bytes)
                        else:
                            doc.update_stream(page.xref, stream_bytes)
                        logger.warning(
                            "Heading tag reverted (%.1f%% diff) for %s on page %d, attempt %d",
                            diff, element_id, page_idx, attempt + 1,
                        )
                        continue

                changes.append(
                    f"Tagged '{text[:40]}' as {tag_name} on page {page_idx + 1}"
                )
                success = True
                break

            except Exception as e:
                logger.warning(
                    "Heading tag attempt %d failed for %s: %s",
                    attempt + 1, element_id, e,
                )
                continue

        if not success and text:
            logger.warning(
                "Could not tag heading '%s' on page %d after %d attempts",
                text[:40], page_idx + 1, max_attempts,
            )

    return changes


def _apply_contrast_fixes(
    doc: fitz.Document,
    page_idx: int,
    fixes: list[dict],
    max_attempts: int = 5,
    verify: bool = True,
) -> list[str]:
    """Fix contrast by modifying color operators in the content stream.

    Replaces color-setting operators (rg, g, k, sc, etc.) that affect
    text with the fixed color values.

    Args:
        doc: Open PyMuPDF document.
        page_idx: 0-based page index.
        fixes: List of dicts with original_color (hex), fixed_color (hex).
        max_attempts: Max retry attempts.
        verify: Whether to visually verify.

    Returns:
        List of change descriptions.
    """
    changes: list[str] = []

    if page_idx >= len(doc):
        return changes

    page = doc[page_idx]

    # Render baseline for verification
    if verify:
        baseline_png = _render_page(doc, page_idx)

    # Read content stream
    try:
        xref = page.xref
        contents = doc.xref_get_key(xref, "Contents")
        if contents[0] == "xref":
            stream_xref = int(contents[1].split()[0])
            stream_bytes = doc.xref_stream(stream_xref)
        else:
            stream_xref = xref
            stream_bytes = doc.xref_stream(xref)

        if not stream_bytes:
            return changes
    except Exception:
        return changes

    modified = False
    current_stream = stream_bytes

    for fix in fixes:
        orig_color = fix.get("original_color", "")
        fixed_color = fix.get("fixed_color", "")

        if not orig_color or not fixed_color:
            continue

        # Convert hex colors to RGB floats
        orig_rgb = _hex_to_rgb_floats(orig_color)
        fixed_rgb = _hex_to_rgb_floats(fixed_color)

        if orig_rgb is None or fixed_rgb is None:
            continue

        try:
            tokens = _tokenize_content_stream(current_stream)
            replaced = _replace_color_in_stream(tokens, orig_rgb, fixed_rgb)
            if replaced:
                current_stream = _reassemble_stream(tokens)
                modified = True
                changes.append(
                    f"Changed color {orig_color} → {fixed_color} on page {page_idx + 1}"
                )
        except Exception as e:
            logger.warning("Contrast fix failed for %s: %s", orig_color, e)

    if modified:
        # Verify if requested
        if verify:
            doc.update_stream(stream_xref, current_stream)
            modified_png = _render_page(doc, page_idx)
            diff = _pixel_diff(baseline_png, modified_png)
            # Contrast changes should show some diff (text color changed)
            # but layout shouldn't shift dramatically
            if diff > 15.0:
                # Too much changed — layout corruption likely, revert
                doc.update_stream(stream_xref, stream_bytes)
                changes.clear()
                logger.warning(
                    "Contrast fix reverted (%.1f%% diff) on page %d — layout corruption suspected",
                    diff, page_idx + 1,
                )
        else:
            doc.update_stream(stream_xref, current_stream)

    return changes


# ── Content Stream Utilities ─────────────────────────────────────────


def _tokenize_content_stream(stream_bytes: bytes) -> list[Token]:
    """Parse PDF content stream bytes into tokens.

    Handles the major PDF content stream elements:
    - Operators (Tj, TJ, rg, g, BT, ET, etc.)
    - String operands (literal and hex)
    - Numeric operands
    - Array operands
    """
    text = stream_bytes.decode("latin-1")
    tokens: list[Token] = []

    # Known PDF operators (multi-char sorted longest first to match greedily)
    i = 0
    n = len(text)

    while i < n:
        c = text[i]

        # Skip whitespace
        if c in " \t\r\n":
            tokens.append(Token(value=c, type="whitespace"))
            i += 1
            continue

        # Comment
        if c == "%":
            end = text.find("\n", i)
            if end == -1:
                end = n
            tokens.append(Token(value=text[i:end], type="comment"))
            i = end
            continue

        # Literal string (...)
        if c == "(":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(Token(value=text[i:j], type="string"))
            i = j
            continue

        # Hex string <...>
        if c == "<" and (i + 1 >= n or text[i + 1] != "<"):
            j = text.find(">", i)
            if j == -1:
                j = n
            else:
                j += 1
            tokens.append(Token(value=text[i:j], type="hexstring"))
            i = j
            continue

        # Dict << ... >>
        if c == "<" and i + 1 < n and text[i + 1] == "<":
            # Find matching >>
            depth = 1
            j = i + 2
            while j < n - 1 and depth > 0:
                if text[j] == "<" and text[j + 1] == "<":
                    depth += 1
                    j += 2
                elif text[j] == ">" and text[j + 1] == ">":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            tokens.append(Token(value=text[i:j], type="dict"))
            i = j
            continue

        # Array [...]
        if c == "[":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                # Handle nested strings
                elif text[j] == "(":
                    sd = 1
                    j += 1
                    while j < n and sd > 0:
                        if text[j] == "\\" and j + 1 < n:
                            j += 2
                            continue
                        if text[j] == "(":
                            sd += 1
                        elif text[j] == ")":
                            sd -= 1
                        j += 1
                    continue
                elif text[j] == "<" and (j + 1 >= n or text[j + 1] != "<"):
                    end = text.find(">", j)
                    if end != -1:
                        j = end + 1
                    else:
                        j += 1
                    continue
                j += 1
            tokens.append(Token(value=text[i:j], type="array"))
            i = j
            continue

        # Name /...
        if c == "/":
            j = i + 1
            while j < n and text[j] not in " \t\r\n/<>[](%){}":
                j += 1
            tokens.append(Token(value=text[i:j], type="name"))
            i = j
            continue

        # Number or operator
        # Try number first
        if c in "0123456789+-.":
            j = i + 1
            is_num = True
            has_dot = c == "."
            while j < n and text[j] not in " \t\r\n/<>[]()%{}":
                if text[j] == ".":
                    if has_dot:
                        is_num = False
                    has_dot = True
                elif text[j] not in "0123456789":
                    is_num = False
                    break
                j += 1
            if is_num and j > i:
                tokens.append(Token(value=text[i:j], type="number"))
                i = j
                continue

        # Operator or identifier
        j = i
        while j < n and text[j] not in " \t\r\n/<>[]()%{}":
            j += 1
        if j > i:
            val = text[i:j]
            tokens.append(Token(value=val, type="operator"))
            i = j
        else:
            tokens.append(Token(value=c, type="other"))
            i += 1

    return tokens


def _find_text_in_stream(
    tokens: list[Token],
    target_text: str,
) -> list[tuple[int, int]]:
    """Find indices of tokens that render the target text.

    Looks for Tj and TJ operators whose string operands contain
    the target text.

    Returns list of (start_idx, end_idx) tuples marking the token range.
    """
    matches: list[tuple[int, int]] = []

    for i, tok in enumerate(tokens):
        if tok.type != "operator":
            continue

        if tok.value == "Tj":
            # Previous non-whitespace token should be a string
            for j in range(i - 1, -1, -1):
                if tokens[j].type == "whitespace":
                    continue
                if tokens[j].type in ("string", "hexstring"):
                    extracted = _extract_text_from_string(tokens[j].value)
                    if target_text.lower() in extracted.lower():
                        matches.append((j, i))
                break

        elif tok.value == "TJ":
            # Previous non-whitespace token should be an array
            for j in range(i - 1, -1, -1):
                if tokens[j].type == "whitespace":
                    continue
                if tokens[j].type == "array":
                    extracted = _extract_text_from_tj_array(tokens[j].value)
                    if target_text.lower() in extracted.lower():
                        matches.append((j, i))
                break

    return matches


def _inject_bdc_emc(
    tokens: list[Token],
    match: tuple[int, int],
    mcid: int,
    tag_name: str,
) -> list[Token]:
    """Wrap matched operators with BDC (begin marked content) / EMC (end)."""
    start_idx, end_idx = match

    bdc_tokens = [
        Token(value=f"/{tag_name}", type="name"),
        Token(value=" ", type="whitespace"),
        Token(value=f"<</MCID {mcid}>>", type="dict"),
        Token(value=" ", type="whitespace"),
        Token(value="BDC", type="operator"),
        Token(value="\n", type="whitespace"),
    ]

    emc_tokens = [
        Token(value="\n", type="whitespace"),
        Token(value="EMC", type="operator"),
        Token(value="\n", type="whitespace"),
    ]

    result = (
        tokens[:start_idx]
        + bdc_tokens
        + tokens[start_idx:end_idx + 1]
        + emc_tokens
        + tokens[end_idx + 1:]
    )
    return result


def _reassemble_stream(tokens: list[Token]) -> bytes:
    """Serialize tokens back to content stream bytes."""
    return "".join(t.value for t in tokens).encode("latin-1")


def _replace_color_in_stream(
    tokens: list[Token],
    orig_rgb: tuple[float, float, float],
    fixed_rgb: tuple[float, float, float],
    tolerance: float = 0.02,
) -> bool:
    """Replace color-setting operators in-place within the token list.

    Looks for patterns like: R G B rg  or  R G B RG
    and replaces R G B values when they match orig_rgb within tolerance.
    """
    replaced = False

    for i, tok in enumerate(tokens):
        if tok.type != "operator" or tok.value not in ("rg", "RG", "scn", "SCN"):
            continue

        # Collect the 3 preceding numeric tokens
        nums: list[int] = []
        j = i - 1
        while j >= 0 and len(nums) < 3:
            if tokens[j].type == "whitespace":
                j -= 1
                continue
            if tokens[j].type == "number":
                nums.append(j)
                j -= 1
            else:
                break

        if len(nums) != 3:
            continue

        # nums is in reverse order
        nums.reverse()
        try:
            r = float(tokens[nums[0]].value)
            g = float(tokens[nums[1]].value)
            b = float(tokens[nums[2]].value)
        except ValueError:
            continue

        if (
            abs(r - orig_rgb[0]) < tolerance
            and abs(g - orig_rgb[1]) < tolerance
            and abs(b - orig_rgb[2]) < tolerance
        ):
            tokens[nums[0]] = Token(value=f"{fixed_rgb[0]:.4f}", type="number")
            tokens[nums[1]] = Token(value=f"{fixed_rgb[1]:.4f}", type="number")
            tokens[nums[2]] = Token(value=f"{fixed_rgb[2]:.4f}", type="number")
            replaced = True

    return replaced


# ── Rendering & Verification ─────────────────────────────────────────


def _render_page(doc: fitz.Document, page_idx: int) -> bytes:
    """Render a page to PNG bytes via PyMuPDF's get_pixmap."""
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=72)
    return pix.tobytes("png")


def _pixel_diff(img1_bytes: bytes, img2_bytes: bytes) -> float:
    """Compute percentage of differing pixels between two PNG images.

    Uses PyMuPDF's Pixmap for comparison (no Pillow dependency needed).
    """
    try:
        pix1 = fitz.Pixmap(img1_bytes)
        pix2 = fitz.Pixmap(img2_bytes)

        if pix1.width != pix2.width or pix1.height != pix2.height:
            return 100.0

        samples1 = pix1.samples
        samples2 = pix2.samples
        total_pixels = pix1.width * pix1.height
        if total_pixels == 0:
            return 0.0

        # Count pixels that differ by more than a small threshold
        n = pix1.n  # components per pixel
        diff_count = 0
        for p in range(total_pixels):
            offset = p * n
            differs = False
            for c in range(min(n, 3)):  # compare RGB only
                if abs(samples1[offset + c] - samples2[offset + c]) > 5:
                    differs = True
                    break
            if differs:
                diff_count += 1

        return (diff_count / total_pixels) * 100.0

    except Exception:
        return 100.0


# ── Helper Utilities ─────────────────────────────────────────────────


def _pdf_string(text: str) -> str:
    """Encode a Python string as a PDF literal string.

    Handles escaping of special characters. For ASCII text, uses
    parenthesized literals. For Unicode, uses UTF-16BE hex strings.
    """
    try:
        text.encode("latin-1")
        # Safe for literal string
        escaped = (
            text
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        return f"({escaped})"
    except UnicodeEncodeError:
        # Need UTF-16BE hex string
        encoded = text.encode("utf-16-be")
        hex_str = encoded.hex().upper()
        return f"<FEFF{hex_str}>"


def _hex_to_rgb_floats(hex_color: str) -> tuple[float, float, float] | None:
    """Convert a hex color (#RRGGBB) to RGB floats (0-1 range)."""
    if not hex_color or not hex_color.startswith("#"):
        return None
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return None
    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


def _extract_text_from_string(s: str) -> str:
    """Extract readable text from a PDF literal string like (Hello World)."""
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        # Unescape
        inner = (
            inner
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace("\\(", "(")
            .replace("\\)", ")")
            .replace("\\\\", "\\")
        )
        return inner
    elif s.startswith("<") and s.endswith(">"):
        # Hex string — try to decode
        hex_str = s[1:-1]
        try:
            raw = bytes.fromhex(hex_str)
            # Try UTF-16BE (with or without BOM)
            if raw[:2] == b"\xfe\xff":
                return raw[2:].decode("utf-16-be", errors="replace")
            # Try latin-1
            return raw.decode("latin-1", errors="replace")
        except Exception:
            return ""
    return ""


def _extract_text_from_tj_array(array_str: str) -> str:
    """Extract readable text from a TJ array like [(Hello) -50 (World)]."""
    parts: list[str] = []
    i = 0
    n = len(array_str)
    while i < n:
        if array_str[i] == "(":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if array_str[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if array_str[j] == "(":
                    depth += 1
                elif array_str[j] == ")":
                    depth -= 1
                j += 1
            parts.append(_extract_text_from_string(array_str[i:j]))
            i = j
        elif array_str[i] == "<":
            j = array_str.find(">", i)
            if j == -1:
                j = n
            else:
                j += 1
            parts.append(_extract_text_from_string(array_str[i:j]))
            i = j
        else:
            i += 1
    return "".join(parts)
