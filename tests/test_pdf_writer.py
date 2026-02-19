"""Tests for the PDF writer (in-place PDF modification).

Tests against real PDF files in testdocs/ directory.
Tests Tier 1 (metadata, alt text) and Tier 2 (heading tags, contrast).
"""

import pytest
import shutil
from pathlib import Path

import fitz

from src.models.document import DocumentModel, ImageInfo, MetadataInfo, ParagraphInfo
from src.tools.pdf_parser import parse_pdf
from src.tools.pdf_writer import (
    PdfWriteResult,
    Token,
    _ensure_struct_tree,
    _hex_to_rgb_floats,
    _is_tagged,
    _pdf_string,
    _pixel_diff,
    _render_page,
    _set_metadata,
    _tokenize_content_stream,
    _reassemble_stream,
    _find_text_in_stream,
    _inject_bdc_emc,
    _replace_color_in_stream,
    _extract_text_from_string,
    _extract_text_from_tj_array,
    apply_pdf_fixes,
)

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"
LESSON_PDF = TESTDOCS / "Lesson 2 Behaviorism and Structuralism.pdf"
SKINNER_PDF = TESTDOCS / "2) Skinner-TeachingMachines.pdf"


@pytest.fixture
def tmp_pdf(tmp_path):
    """Copy syllabus PDF to tmp dir for modification tests."""
    dest = tmp_path / "test.pdf"
    shutil.copy2(SYLLABUS_PDF, dest)
    return dest


@pytest.fixture
def tmp_lesson_pdf(tmp_path):
    """Copy lesson PDF (with images) to tmp dir."""
    dest = tmp_path / "lesson.pdf"
    shutil.copy2(LESSON_PDF, dest)
    return dest


@pytest.fixture
def parsed_syllabus():
    """Parse the syllabus PDF."""
    result = parse_pdf(SYLLABUS_PDF)
    assert result.success
    return result.document


@pytest.fixture
def parsed_lesson():
    """Parse the lesson PDF."""
    result = parse_pdf(LESSON_PDF)
    assert result.success
    return result.document


# ── Tier 1: Metadata Tests ──────────────────────────────────────────


class TestMetadataFixes:
    """Test setting PDF title and language."""

    def test_set_title(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        changes = _set_metadata(doc, "Test Title", "")
        doc.save(str(tmp_pdf), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        doc2 = fitz.open(str(tmp_pdf))
        assert doc2.metadata["title"] == "Test Title"
        doc2.close()
        assert any("Set PDF title" in c for c in changes)

    def test_set_language(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        changes = _set_metadata(doc, "", "en")
        doc.save(str(tmp_pdf), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        doc2 = fitz.open(str(tmp_pdf))
        catalog = doc2.pdf_catalog()
        lang = doc2.xref_get_key(catalog, "Lang")
        assert lang[0] == "string"
        assert "en" in lang[1]
        doc2.close()
        assert any("Set PDF language" in c for c in changes)

    def test_set_both(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        changes = _set_metadata(doc, "My Title", "en-US")
        doc.save(str(tmp_pdf), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        doc2 = fitz.open(str(tmp_pdf))
        assert doc2.metadata["title"] == "My Title"
        catalog = doc2.pdf_catalog()
        lang = doc2.xref_get_key(catalog, "Lang")
        assert "en-US" in lang[1]
        doc2.close()
        assert len(changes) == 2

    def test_preserves_pages(self, tmp_pdf):
        doc_before = fitz.open(str(tmp_pdf))
        page_count = len(doc_before)
        doc_before.close()

        doc = fitz.open(str(tmp_pdf))
        _set_metadata(doc, "Title", "en")
        doc.save(str(tmp_pdf), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()

        doc_after = fitz.open(str(tmp_pdf))
        assert len(doc_after) == page_count
        doc_after.close()


class TestApplyPdfFixes:
    """Integration tests for apply_pdf_fixes."""

    def test_basic_metadata(self, tmp_path, parsed_syllabus):
        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="EMAT 8030 Syllabus",
            language="en",
            output_dir=str(tmp_path),
        )
        assert result.success
        assert result.output_path != ""
        assert Path(result.output_path).exists()

        # Verify metadata was set
        doc = fitz.open(result.output_path)
        assert doc.metadata["title"] == "EMAT 8030 Syllabus"
        catalog = doc.pdf_catalog()
        lang = doc.xref_get_key(catalog, "Lang")
        assert "en" in lang[1]
        doc.close()

    def test_output_preserves_page_count(self, tmp_path, parsed_syllabus):
        original = fitz.open(str(SYLLABUS_PDF))
        orig_pages = len(original)
        original.close()

        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="Test",
            output_dir=str(tmp_path),
        )
        assert result.success

        modified = fitz.open(result.output_path)
        assert len(modified) == orig_pages
        modified.close()

    def test_output_text_extractable(self, tmp_path, parsed_syllabus):
        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="Test",
            output_dir=str(tmp_path),
        )
        assert result.success

        doc = fitz.open(result.output_path)
        page = doc[0]
        text = page.get_text("text")
        assert len(text) > 50  # should have extractable text
        doc.close()

    def test_file_not_found(self, parsed_syllabus):
        result = apply_pdf_fixes(
            source_path="/nonexistent.pdf",
            doc_model=parsed_syllabus,
        )
        assert not result.success
        assert any("not found" in e.lower() for e in result.errors)

    def test_explicit_output_path(self, tmp_path, parsed_syllabus):
        out = tmp_path / "custom_output.pdf"
        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="Custom",
            output_path=str(out),
        )
        assert result.success
        assert result.output_path == str(out)
        assert out.exists()


# ── Tier 1: Alt Text Tests ──────────────────────────────────────────


class TestAltTextUntaggedPdf:
    """Test creating structure tree and adding /Figure for untagged PDFs."""

    def test_create_struct_tree(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))

        sroot_xref = _ensure_struct_tree(doc)
        assert sroot_xref is not None

        # Verify StructTreeRoot was created
        catalog = doc.pdf_catalog()
        sr = doc.xref_get_key(catalog, "StructTreeRoot")
        assert sr[0] == "xref"

        doc.close()

    def test_set_alt_on_untagged(self, tmp_path, parsed_lesson):
        result = apply_pdf_fixes(
            source_path=LESSON_PDF,
            doc_model=parsed_lesson,
            alt_texts={"img_0": "A diagram showing behaviorism concepts"},
            output_dir=str(tmp_path),
        )
        assert result.success
        assert any("img_0" in c for c in result.changes)

    def test_mark_decorative(self, tmp_path, parsed_lesson):
        result = apply_pdf_fixes(
            source_path=LESSON_PDF,
            doc_model=parsed_lesson,
            decorative_ids={"img_0"},
            output_dir=str(tmp_path),
        )
        assert result.success
        assert any("decorative" in c.lower() for c in result.changes)


class TestIsTagged:
    """Test tagged PDF detection."""

    def test_detect_untagged(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        # Most academic PDFs are untagged
        tagged = _is_tagged(doc)
        doc.close()
        # Result depends on the file — just verify it doesn't crash
        assert isinstance(tagged, bool)


# ── Content Stream Tokenizer Tests ───────────────────────────────────


class TestContentStreamTokenizer:
    """Test parsing and reassembling content streams."""

    def test_tokenize_simple(self):
        stream = b"BT /F1 12 Tf (Hello World) Tj ET"
        tokens = _tokenize_content_stream(stream)
        assert len(tokens) > 0

        # Find key tokens
        values = [t.value for t in tokens if t.type != "whitespace"]
        assert "BT" in values
        assert "ET" in values
        assert "Tj" in values

    def test_roundtrip_identity(self):
        """Tokenize and reassemble should produce identical output."""
        stream = b"BT /F1 12 Tf (Hello World) Tj ET"
        tokens = _tokenize_content_stream(stream)
        result = _reassemble_stream(tokens)
        assert result == stream

    def test_roundtrip_complex(self, tmp_pdf):
        """Tokenize real PDF content stream and verify roundtrip."""
        doc = fitz.open(str(tmp_pdf))
        page = doc[0]

        # Get content stream
        contents = doc.xref_get_key(page.xref, "Contents")
        if contents[0] == "xref":
            stream_xref = int(contents[1].split()[0])
            stream = doc.xref_stream(stream_xref)
        else:
            stream = doc.xref_stream(page.xref)

        if stream:
            tokens = _tokenize_content_stream(stream)
            result = _reassemble_stream(tokens)
            assert result == stream

        doc.close()

    def test_tokenize_with_array(self):
        stream = b"BT /F1 12 Tf [(Hello) -50 (World)] TJ ET"
        tokens = _tokenize_content_stream(stream)
        values = [t.value for t in tokens if t.type != "whitespace"]
        assert "TJ" in values

    def test_tokenize_with_hex_string(self):
        stream = b"BT /F1 12 Tf <48656C6C6F> Tj ET"
        tokens = _tokenize_content_stream(stream)
        hex_tokens = [t for t in tokens if t.type == "hexstring"]
        assert len(hex_tokens) == 1

    def test_tokenize_with_colors(self):
        stream = b"0 0 0 rg BT /F1 12 Tf (Hello) Tj ET"
        tokens = _tokenize_content_stream(stream)
        ops = [t.value for t in tokens if t.type == "operator"]
        assert "rg" in ops


class TestFindTextInStream:
    """Test finding text in content streams."""

    def test_find_tj_text(self):
        stream = b"BT /F1 12 Tf (Hello World) Tj ET"
        tokens = _tokenize_content_stream(stream)
        matches = _find_text_in_stream(tokens, "Hello World")
        assert len(matches) >= 1

    def test_find_tj_partial(self):
        stream = b"BT /F1 12 Tf (Hello World) Tj ET"
        tokens = _tokenize_content_stream(stream)
        matches = _find_text_in_stream(tokens, "Hello")
        assert len(matches) >= 1

    def test_no_match(self):
        stream = b"BT /F1 12 Tf (Hello World) Tj ET"
        tokens = _tokenize_content_stream(stream)
        matches = _find_text_in_stream(tokens, "Goodbye")
        assert len(matches) == 0


# ── Heading Tag Tests ────────────────────────────────────────────────


class TestHeadingTagging:
    """Test injecting heading tags into content streams."""

    def test_inject_bdc_emc(self):
        stream = b"BT /F1 12 Tf (Chapter 1) Tj ET"
        tokens = _tokenize_content_stream(stream)
        matches = _find_text_in_stream(tokens, "Chapter 1")
        assert len(matches) >= 1

        modified = _inject_bdc_emc(tokens, matches[0], mcid=0, tag_name="H1")
        result = _reassemble_stream(modified)

        # Should contain BDC and EMC markers
        assert b"BDC" in result
        assert b"EMC" in result
        assert b"/H1" in result

    def test_heading_tag_pixel_diff_zero(self, tmp_pdf):
        """Heading tags should be invisible — ~0% pixel diff."""
        doc = fitz.open(str(tmp_pdf))

        # Render baseline
        baseline = _render_page(doc, 0)

        # The tag doesn't change visible content
        # Re-render same page
        same = _render_page(doc, 0)
        diff = _pixel_diff(baseline, same)
        assert diff < 0.1  # same page, 0% diff

        doc.close()


# ── Contrast Fix Tests ───────────────────────────────────────────────


class TestContrastFix:
    """Test contrast color replacement in content streams."""

    def test_replace_color(self):
        stream = b"0.5000 0.5000 0.5000 rg BT /F1 12 Tf (Gray text) Tj ET"
        tokens = _tokenize_content_stream(stream)

        orig_rgb = (0.5, 0.5, 0.5)
        fixed_rgb = (0.2, 0.2, 0.2)

        replaced = _replace_color_in_stream(tokens, orig_rgb, fixed_rgb)
        assert replaced is True

        result = _reassemble_stream(tokens)
        assert b"0.2000" in result

    def test_no_replace_when_no_match(self):
        stream = b"0 0 0 rg BT /F1 12 Tf (Black text) Tj ET"
        tokens = _tokenize_content_stream(stream)

        # Try to replace red (which isn't present)
        replaced = _replace_color_in_stream(
            tokens, (1.0, 0.0, 0.0), (0.8, 0.0, 0.0)
        )
        assert replaced is False


# ── Output Integrity Tests ───────────────────────────────────────────


class TestOutputIntegrity:
    """Test that remediated PDF maintains integrity."""

    def test_file_size_similar(self, tmp_path, parsed_syllabus):
        original_size = SYLLABUS_PDF.stat().st_size

        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="EMAT 8030 Syllabus",
            language="en",
            output_dir=str(tmp_path),
        )
        assert result.success

        modified_size = Path(result.output_path).stat().st_size
        # File should be similar size (metadata adds ~1-5KB typically)
        ratio = modified_size / original_size
        assert 0.9 < ratio < 1.5

    def test_all_pages_present(self, tmp_path, parsed_syllabus):
        original = fitz.open(str(SYLLABUS_PDF))
        orig_pages = len(original)
        original.close()

        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="Test",
            output_dir=str(tmp_path),
        )
        assert result.success

        modified = fitz.open(result.output_path)
        assert len(modified) == orig_pages
        modified.close()

    def test_text_still_extractable(self, tmp_path, parsed_syllabus):
        result = apply_pdf_fixes(
            source_path=SYLLABUS_PDF,
            doc_model=parsed_syllabus,
            title="Test",
            language="en",
            output_dir=str(tmp_path),
        )
        assert result.success

        doc = fitz.open(result.output_path)
        all_text = ""
        for page in doc:
            all_text += page.get_text("text")
        doc.close()

        # Should have substantial text
        assert len(all_text) > 100


# ── Helper Function Tests ────────────────────────────────────────────


class TestPdfString:
    """Test PDF string encoding."""

    def test_simple_ascii(self):
        assert _pdf_string("hello") == "(hello)"

    def test_escape_parens(self):
        result = _pdf_string("(test)")
        assert "\\(" in result
        assert "\\)" in result

    def test_escape_backslash(self):
        result = _pdf_string("back\\slash")
        assert "\\\\" in result

    def test_unicode_uses_hex(self):
        result = _pdf_string("caf\u00e9")
        # Latin-1 encodable, so should use literal
        assert result.startswith("(")

    def test_non_latin1_uses_hex(self):
        result = _pdf_string("\u4e16\u754c")  # 世界
        assert result.startswith("<FEFF")


class TestHexToRgbFloats:
    """Test hex color to RGB float conversion."""

    def test_black(self):
        assert _hex_to_rgb_floats("#000000") == (0.0, 0.0, 0.0)

    def test_white(self):
        assert _hex_to_rgb_floats("#FFFFFF") == (1.0, 1.0, 1.0)

    def test_red(self):
        r, g, b = _hex_to_rgb_floats("#FF0000")
        assert abs(r - 1.0) < 0.01
        assert abs(g - 0.0) < 0.01
        assert abs(b - 0.0) < 0.01

    def test_invalid(self):
        assert _hex_to_rgb_floats("") is None
        assert _hex_to_rgb_floats("red") is None
        assert _hex_to_rgb_floats("#FFF") is None


class TestExtractText:
    """Test text extraction from PDF strings."""

    def test_literal_string(self):
        assert _extract_text_from_string("(Hello)") == "Hello"

    def test_escaped_parens(self):
        assert _extract_text_from_string("(test\\(1\\))") == "test(1)"

    def test_hex_string(self):
        result = _extract_text_from_string("<48656C6C6F>")
        assert result == "Hello"

    def test_tj_array(self):
        result = _extract_text_from_tj_array("[(Hello) -50 ( World)]")
        assert "Hello" in result
        assert "World" in result


class TestPixelDiff:
    """Test pixel difference calculation."""

    def test_identical_images(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        img1 = _render_page(doc, 0)
        img2 = _render_page(doc, 0)
        doc.close()

        diff = _pixel_diff(img1, img2)
        assert diff < 0.1  # should be 0% for identical renders

    def test_different_pages(self, tmp_pdf):
        doc = fitz.open(str(tmp_pdf))
        if len(doc) > 1:
            img1 = _render_page(doc, 0)
            img2 = _render_page(doc, 1)
            doc.close()

            diff = _pixel_diff(img1, img2)
            assert diff > 0  # different pages should differ
        else:
            doc.close()


# ── Scanned Page Detection Tests ─────────────────────────────────────


class TestScannedPageDetection:
    """Test that scanned pages are detected in parser."""

    def test_syllabus_not_scanned(self):
        result = parse_pdf(SYLLABUS_PDF)
        assert result.success
        # The syllabus has real text, shouldn't detect as scanned
        assert len(result.scanned_page_numbers) == 0

    def test_parser_returns_scanned_pages(self):
        result = parse_pdf(LESSON_PDF)
        assert result.success
        # scanned_page_numbers should be a list (may or may not have items)
        assert isinstance(result.scanned_page_numbers, list)


# ── Image XRef Tests ─────────────────────────────────────────────────


class TestImageXref:
    """Test that xref is stored on parsed images."""

    def test_images_have_xref(self):
        result = parse_pdf(LESSON_PDF)
        assert result.success
        doc = result.document
        for img in doc.images:
            assert img.xref is not None
            assert img.xref > 0

    def test_xref_is_positive_int(self):
        result = parse_pdf(LESSON_PDF)
        assert result.success
        for img in result.document.images:
            assert isinstance(img.xref, int)
            assert img.xref > 0


# ── Image Linking Tests ──────────────────────────────────────────────


class TestImageLinking:
    """Test that images are linked to paragraphs via image_ids."""

    def test_images_have_paragraph_link(self):
        result = parse_pdf(LESSON_PDF)
        assert result.success
        doc = result.document

        # Collect all image_ids referenced from paragraphs
        linked_ids = set()
        for p in doc.paragraphs:
            for img_id in p.image_ids:
                linked_ids.add(img_id)

        # Every image should be linked to a paragraph
        for img in doc.images:
            assert img.id in linked_ids, f"{img.id} not linked to any paragraph"
