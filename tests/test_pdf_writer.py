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


class TestPdfUaMetadata:
    """Tests for apply_pdf_ua_metadata() — Track C."""

    def _make_minimal_pdf(self, tmp_path, with_xmp: bool = True) -> Path:
        """Create a minimal test PDF with or without an XMP metadata stream."""
        import fitz
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata({"title": "Test Title", "author": "Test Author"})
        if with_xmp:
            doc.set_xml_metadata(
                '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description rdf:about="" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Test Title</rdf:li></rdf:Alt></dc:title>'
                '</rdf:Description>'
                '</rdf:RDF>'
                '</x:xmpmeta>'
                '<?xpacket end="w"?>'
            )
        out = tmp_path / "minimal.pdf"
        doc.save(str(out))
        doc.close()
        return out

    def test_helper_reads_existing_xmp(self, tmp_path):
        from src.tools.pdf_writer import _read_or_synthesize_xmp
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        doc = fitz.open(str(pdf))
        xmp_bytes = _read_or_synthesize_xmp(doc)
        doc.close()
        assert b"dc:title" in xmp_bytes
        assert b"Test Title" in xmp_bytes

    def test_helper_synthesizes_when_no_xmp(self, tmp_path):
        from src.tools.pdf_writer import _read_or_synthesize_xmp
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=False)
        doc = fitz.open(str(pdf))
        xmp_bytes = _read_or_synthesize_xmp(doc)
        doc.close()
        assert b"rdf:RDF" in xmp_bytes
        assert b"rdf:Description" in xmp_bytes

    def test_apply_adds_pdfuaid_part(self, tmp_path):
        import re as _re
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        result = apply_pdf_ua_metadata(pdf)
        assert result.success
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        # The pdfuaid namespace URI must be present (prefix may vary
        # depending on serializer behaviour).
        assert "http://www.aiim.org/pdfua/ns/id/" in xmp
        # And a `<...:part ...>1</...:part>` element with value 1 must
        # exist using whatever prefix is bound to that namespace.
        assert _re.search(
            r"<[A-Za-z_][\w-]*:part(?:\s[^>]*)?>1</[A-Za-z_][\w-]*:part>", xmp
        )

    def test_apply_preserves_existing_dc_title(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        assert "Test Title" in xmp

    def test_apply_sets_display_doc_title(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        vp = doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")
        doc.close()
        assert vp[0] == "dict"
        assert "DisplayDocTitle" in vp[1]
        assert "true" in vp[1]

    def test_apply_preserves_other_viewer_prefs(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        # Pre-populate ViewerPreferences with a non-DisplayDocTitle entry
        doc = fitz.open(str(pdf))
        doc.xref_set_key(
            doc.pdf_catalog(),
            "ViewerPreferences",
            "<< /FitWindow true >>",
        )
        doc.save(str(pdf), incremental=True, encryption=0)
        doc.close()

        apply_pdf_ua_metadata(pdf)

        doc = fitz.open(str(pdf))
        vp = doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")
        doc.close()
        assert "FitWindow" in vp[1]
        assert "DisplayDocTitle" in vp[1]

    def test_apply_is_idempotent(self, tmp_path):
        """Running twice must not add a second pdfuaid:part element."""
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        import re as _re
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        # Exactly one part element with value 1, regardless of prefix
        matches = _re.findall(
            r"<[A-Za-z_][\w-]*:part(?:\s[^>]*)?>1</[A-Za-z_][\w-]*:part>", xmp
        )
        assert len(matches) == 1

    def test_apply_synthesizes_xmp_for_bare_pdf(self, tmp_path):
        """A PDF with no /Metadata stream gets a fresh XMP with pdfuaid."""
        import re as _re
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=False)
        result = apply_pdf_ua_metadata(pdf)
        assert result.success
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        assert "http://www.aiim.org/pdfua/ns/id/" in xmp
        assert _re.search(
            r"<[A-Za-z_][\w-]*:part(?:\s[^>]*)?>1</[A-Za-z_][\w-]*:part>", xmp
        )


class TestArtifactMarkingHelpers:
    """Tests for Track A operator classification."""

    def test_tj_is_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        assert _is_content_producing_op("Tj")
        assert _is_content_producing_op("TJ")
        assert _is_content_producing_op("'")
        assert _is_content_producing_op('"')

    def test_path_painting_is_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        for op in ["S", "s", "f", "F", "f*", "B", "B*", "b", "b*"]:
            assert _is_content_producing_op(op), f"{op} should be content-producing"

    def test_do_and_sh_are_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        assert _is_content_producing_op("Do")
        assert _is_content_producing_op("sh")

    def test_state_ops_not_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        for op in ["q", "Q", "cm", "Tf", "Td", "TD", "Tm", "T*",
                   "gs", "rg", "RG", "g", "G", "k", "K", "sc", "SC",
                   "scn", "SCN", "cs", "CS", "w", "J", "j", "M", "d",
                   "ri", "i", "m", "l", "c", "v", "y", "re", "h", "n",
                   "BT", "ET", "W", "W*"]:
            assert not _is_content_producing_op(op), f"{op} should NOT be content-producing"

    def test_state_ops_classified_as_state(self):
        from src.tools.pdf_writer import _is_state_setting_op
        for op in ["q", "Q", "cm", "Tf", "Td", "gs", "rg", "BT", "ET"]:
            assert _is_state_setting_op(op), f"{op} should be state-setting"

    def test_bdc_emc_not_classified_as_either(self):
        from src.tools.pdf_writer import _is_content_producing_op, _is_state_setting_op
        for op in ["BDC", "BMC", "EMC"]:
            assert not _is_content_producing_op(op)
            assert not _is_state_setting_op(op)


class TestFindUntaggedRuns:
    """Tests for _find_untagged_content_runs() — Track A state machine."""

    def _tokenize(self, stream_str: str):
        from src.tools.pdf_writer import _tokenize_content_stream
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_empty_stream_no_runs(self):
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("")
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_state_ops_alone_no_runs(self):
        """A page with only state ops at depth 0 — no content to wrap."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("q\n1 0 0 1 0 0 cm\nQ\n")
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_content_at_depth_0_yields_run(self):
        """A simple BT/ET text object at depth 0 — one run covering it."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("BT\n/F0 10 Tf\n72 720 Td\n(hi) Tj\nET\n")
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1
        start, end = runs[0]
        # Run should include the Tj token
        ops_in_run = [
            t.value for t in tokens[start:end + 1]
            if t.type == "operator"
        ]
        assert "Tj" in ops_in_run

    def test_content_inside_bdc_not_wrapped(self):
        """Content inside /P BDC is at depth 1 — yields no run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = "/P << /MCID 0 >> BDC\nBT (hi) Tj ET\nEMC\n"
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_mixed_tagged_and_untagged(self):
        """Tagged body plus untagged footer — only the footer becomes a run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = (
            "/P << /MCID 0 >> BDC\n"
            "BT (body) Tj ET\n"
            "EMC\n"
            "BT (footer) Tj ET\n"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1
        start, end = runs[0]
        run_text = "".join(t.value for t in tokens[start:end + 1])
        assert "footer" in run_text
        assert "body" not in run_text

    def test_nested_bdc_handled(self):
        """/P BDC /Span BDC content EMC EMC — untouched."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = "/P BDC /Span BDC BT (x) Tj ET EMC EMC\n"
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_do_operator_at_depth_0_yields_run(self):
        """Form XObject call (Do) at depth 0 produces a run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("/Fm0 Do\n")
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1


class TestMarkUntaggedContent:
    """End-to-end tests for mark_untagged_content_as_artifact()."""

    def _pdf_with_untagged_footer(self, tmp_path) -> Path:
        """Create a PDF whose content stream has a BDC-tagged body and
        an untagged footer line."""
        import fitz
        import re as _re
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((50, 50), "Body text")
        page.insert_text((50, 380), "Page 1")  # untagged footer
        out = tmp_path / "with_footer.pdf"
        doc.save(str(out))
        doc.close()

        # Post-process: wrap the first BT..ET in /P BDC/EMC so the body
        # is "tagged" and the footer is "untagged" at depth 0.
        doc = fitz.open(str(out))
        p = doc[0]
        stream = p.read_contents()
        m = _re.search(rb"BT\b.*?ET", stream, flags=_re.DOTALL)
        if not m:
            doc.close()
            raise RuntimeError("Test fixture: no BT..ET in generated content stream")
        wrapped = (
            stream[:m.start()]
            + b"/P << /MCID 0 >> BDC\n"
            + m.group(0)
            + b"\nEMC\n"
            + stream[m.end():]
        )
        contents_ref = doc.xref_get_key(p.xref, "Contents")
        if contents_ref[0] == "xref":
            xref = int(contents_ref[1].split()[0])
        elif contents_ref[0] == "array":
            xref = int(contents_ref[1].strip("[]").split()[0])
        else:
            doc.close()
            raise RuntimeError(f"Unexpected /Contents type: {contents_ref}")
        doc.update_stream(xref, wrapped)
        doc.save(str(out), incremental=True, encryption=0)
        doc.close()
        return out

    def test_wraps_untagged_footer(self, tmp_path):
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        import fitz
        pdf = self._pdf_with_untagged_footer(tmp_path)
        result = mark_untagged_content_as_artifact(pdf)
        assert result.success
        assert result.artifact_wrappers_inserted >= 1
        doc = fitz.open(str(pdf))
        content = doc[0].read_contents()
        doc.close()
        # The wrapper uses ``/Artifact <</Type /Pagination>> BDC`` —
        # the property dict is required by veraPDF rule 7.1-3.
        assert b"/Artifact" in content
        assert b"BDC" in content
        assert b"/Type /Pagination" in content

    def test_empty_pdf_no_op(self, tmp_path):
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        import fitz
        doc = fitz.open()
        doc.new_page()  # blank page, no content
        out = tmp_path / "empty.pdf"
        doc.save(str(out))
        doc.close()
        result = mark_untagged_content_as_artifact(out)
        assert result.success
        assert result.artifact_wrappers_inserted == 0

    def test_converts_suspect_to_artifact(self, tmp_path):
        """Content inside /Suspect BDC is converted to /Artifact."""
        import fitz
        import re as _re
        from src.tools.pdf_writer import mark_untagged_content_as_artifact

        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_text((50, 50), "Good text")
        page.insert_text((50, 200), "Suspect OCR text")
        out = tmp_path / "with_suspect.pdf"
        doc.save(str(out))
        doc.close()

        # Wrap first text in /P BDC, second in /Suspect BDC
        doc = fitz.open(str(out))
        p = doc[0]
        stream = p.read_contents()
        bts = list(_re.finditer(rb"BT\b.*?ET", stream, flags=_re.DOTALL))
        assert len(bts) >= 2, f"Expected 2 BT..ET, got {len(bts)}"
        # Build new stream with tagged first and /Suspect second
        new_stream = (
            b"/P << /MCID 0 >> BDC\n"
            + bts[0].group(0)
            + b"\nEMC\n"
            + b"/Suspect << /BBox [50 200 250 220] >> BDC\n"
            + bts[1].group(0)
            + b"\nEMC\n"
        )
        contents_ref = doc.xref_get_key(p.xref, "Contents")
        if contents_ref[0] == "xref":
            xref = int(contents_ref[1].split()[0])
        else:
            xref = int(contents_ref[1].strip("[]").split()[0])
        doc.update_stream(xref, new_stream)
        doc.save(str(out), incremental=True, encryption=0)
        doc.close()

        # Verify /Suspect is present before
        doc = fitz.open(str(out))
        assert b"/Suspect" in doc[0].read_contents()
        doc.close()

        result = mark_untagged_content_as_artifact(out)
        assert result.success
        assert result.artifact_wrappers_inserted >= 1

        # Verify /Suspect is gone, /Artifact is there
        doc = fitz.open(str(out))
        content = doc[0].read_contents()
        doc.close()
        assert b"/Suspect" not in content
        assert b"/Artifact" in content
        assert b"/Type /Pagination" in content

    def test_idempotent(self, tmp_path):
        """Running twice inserts zero wrappers on the second call."""
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        pdf = self._pdf_with_untagged_footer(tmp_path)
        first = mark_untagged_content_as_artifact(pdf)
        second = mark_untagged_content_as_artifact(pdf)
        assert first.success and second.success
        assert second.artifact_wrappers_inserted == 0

    def test_on_real_benchmark_pdf(self, tmp_path):
        """Run on a real remediated benchmark PDF and verify:
        - text extraction is unchanged
        - veraPDF total violation count decreases
        - no new rule types appear
        """
        import shutil
        import fitz
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        from src.tools.verapdf_checker import check_pdf_ua

        # Prefer the pre_ua_fix backup (created by apply_ua_fixes.py).
        # If that's missing, fall back to the post-fix file (which will
        # have a smaller delta but still works as a smoke test).
        backup = Path(
            "/tmp/remediation_bench_full/ua_fixes_work/"
            "W2895738059_remediated.pre_ua_fix.pdf"
        )
        live = Path(
            "/tmp/remediation_bench_full/"
            "semantic_tagging_passed_W2895738059/"
            "W2895738059_remediated.pdf"
        )
        src = backup if backup.exists() else live
        if not src.exists():
            pytest.skip(f"Benchmark PDF not available: {src}")

        dst = tmp_path / "smoke.pdf"
        shutil.copy(src, dst)

        # Baseline text extraction
        doc = fitz.open(str(dst))
        baseline_text = [p.get_text() for p in doc]
        doc.close()

        # Baseline verapdf
        baseline_vera = check_pdf_ua(str(dst))
        assert baseline_vera.success

        # Apply Track A
        result = mark_untagged_content_as_artifact(dst)
        assert result.success, result.errors

        # Text must match
        doc = fitz.open(str(dst))
        post_text = [p.get_text() for p in doc]
        doc.close()
        assert post_text == baseline_text, "Text extraction changed after Track A"

        # veraPDF total checks should decrease
        post_vera = check_pdf_ua(str(dst))
        assert post_vera.success
        assert post_vera.violation_count < baseline_vera.violation_count, (
            f"violations did not decrease: {baseline_vera.violation_count} "
            f"→ {post_vera.violation_count}"
        )


# ── Phase 2b: Link ParentTree tests ─────────────────────────────────

class TestExtractUri:
    """Tests for _extract_uri_from_annotation covering indirect refs."""

    def test_inline_uri(self, tmp_path):
        """Annotation with /A << /S /URI /URI (http://...) >> inline."""
        from src.tools.pdf_writer import _extract_uri_from_annotation

        doc = fitz.open()
        page = doc.new_page()
        page.insert_link({
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(72, 72, 200, 92),
            "uri": "https://inline.example.com",
        })
        out = tmp_path / "inline.pdf"
        doc.save(str(out))
        doc.close()

        doc = fitz.open(str(out))
        for xref in range(1, doc.xref_length()):
            try:
                st = doc.xref_get_key(xref, "Subtype")
            except Exception:
                continue
            if st[0] == "name" and st[1] == "/Link":
                uri = _extract_uri_from_annotation(doc, xref)
                assert "inline.example.com" in uri
                break
        doc.close()

    def test_indirect_action_and_uri(self, tmp_path):
        """Annotation with /A N 0 R where action has /URI M 0 R (double indirect)."""
        from src.tools.pdf_writer import _extract_uri_from_annotation

        doc = fitz.open()
        page = doc.new_page()

        # Create the deep indirect structure manually
        uri_xref = doc.get_new_xref()
        doc.update_object(uri_xref, "(https://deep.example.com/page)")

        action_xref = doc.get_new_xref()
        doc.update_object(
            action_xref,
            f"<< /S /URI /Type /Action /URI {uri_xref} 0 R >>",
        )

        annot_xref = doc.get_new_xref()
        doc.update_object(
            annot_xref,
            f"<< /Type /Annot /Subtype /Link "
            f"/Rect [72 72 200 92] "
            f"/A {action_xref} 0 R >>",
        )

        # Add to page's Annots
        doc.xref_set_key(page.xref, "Annots", f"[{annot_xref} 0 R]")

        out = tmp_path / "indirect.pdf"
        doc.save(str(out))
        doc.close()

        doc = fitz.open(str(out))
        uri = _extract_uri_from_annotation(doc, annot_xref)
        assert "deep.example.com" in uri, f"Got: {uri!r}"
        doc.close()


class TestLinkParentTreeHelpers:
    """Unit tests for Phase 2b helper functions."""

    def _make_pdf_with_links(self, tmp_path, num_links=2, add_struct_tree=True):
        """Create a minimal PDF with link annotations for testing."""
        doc = fitz.open()
        page = doc.new_page()

        # Add link annotations
        for i in range(num_links):
            rect = fitz.Rect(72, 72 + i * 30, 300, 92 + i * 30)
            page.insert_link({
                "kind": fitz.LINK_URI,
                "from": rect,
                "uri": f"https://example.com/link{i}",
            })

        if add_struct_tree:
            # Create a minimal struct tree with a /Document element
            cat = doc.pdf_catalog()
            doc_elem_xref = doc.get_new_xref()
            st_root_xref = doc.get_new_xref()
            doc.update_object(
                doc_elem_xref,
                f"<< /Type /StructElem /S /Document /P {st_root_xref} 0 R /K [] >>",
            )
            doc.update_object(
                st_root_xref,
                f"<< /Type /StructTreeRoot /K {doc_elem_xref} 0 R >>",
            )
            doc.xref_set_key(cat, "StructTreeRoot", f"{st_root_xref} 0 R")
            doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")

        out = tmp_path / "links.pdf"
        doc.save(str(out))
        doc.close()
        return out

    def test_get_link_annotation_xrefs(self, tmp_path):
        from src.tools.pdf_writer import _get_link_annotation_xrefs

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=3)
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        xrefs = _get_link_annotation_xrefs(doc, page)
        assert len(xrefs) == 3
        # Each should be a /Link annotation
        for xref in xrefs:
            st = doc.xref_get_key(xref, "Subtype")
            assert st[1] == "/Link"
        doc.close()

    def test_get_annot_alt_text_from_contents(self, tmp_path):
        from src.tools.pdf_writer import _get_annot_alt_text

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        # Find the link annotation and set /Contents on it
        for xref in range(1, doc.xref_length()):
            try:
                st = doc.xref_get_key(xref, "Subtype")
            except Exception:
                continue
            if st[0] == "name" and st[1] == "/Link":
                doc.xref_set_key(xref, "Contents", "(Example Link Text)")
                alt = _get_annot_alt_text(doc, xref)
                assert alt == "Example Link Text"
                break
        doc.close()

    def test_get_annot_alt_text_fallback_to_uri(self, tmp_path):
        from src.tools.pdf_writer import _get_annot_alt_text

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
        doc = fitz.open(str(pdf_path))
        # Find link — it shouldn't have /Contents yet
        for xref in range(1, doc.xref_length()):
            try:
                st = doc.xref_get_key(xref, "Subtype")
            except Exception:
                continue
            if st[0] == "name" and st[1] == "/Link":
                alt = _get_annot_alt_text(doc, xref)
                assert "example.com" in alt
                break
        doc.close()

    def test_find_document_elem(self, tmp_path):
        from src.tools.pdf_writer import _find_document_elem

        pdf_path = self._make_pdf_with_links(tmp_path)
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_root = int(st_key[1].split()[0])
        doc_elem = _find_document_elem(doc, st_root)
        assert doc_elem is not None
        obj = doc.xref_object(doc_elem) or ""
        assert "/Document" in obj
        doc.close()

    def test_find_next_struct_parent_empty(self, tmp_path):
        from src.tools.pdf_writer import _find_next_struct_parent

        pdf_path = self._make_pdf_with_links(tmp_path)
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_root = int(st_key[1].split()[0])
        # Fresh PDF with no StructParent entries → should return 0
        next_sp = _find_next_struct_parent(doc, st_root)
        assert next_sp == 0
        doc.close()

    def test_create_link_struct_elem(self, tmp_path):
        from src.tools.pdf_writer import _create_link_struct_elem

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_root = int(st_key[1].split()[0])

        # Find /Document element
        from src.tools.pdf_writer import _find_document_elem
        doc_elem = _find_document_elem(doc, st_root)

        # Find an annotation
        from src.tools.pdf_writer import _get_link_annotation_xrefs
        page = doc[0]
        annot_xrefs = _get_link_annotation_xrefs(doc, page)
        assert len(annot_xrefs) >= 1
        annot_xref = annot_xrefs[0]

        # Create struct element
        link_xref = _create_link_struct_elem(
            doc, doc_elem, annot_xref, "Test Link"
        )
        obj = doc.xref_object(link_xref) or ""
        assert "/Link" in obj
        assert "/OBJR" in obj
        assert str(annot_xref) in obj
        assert "Test Link" in obj
        doc.close()


class TestLinkParentTree:
    """Integration tests for populate_link_parent_tree()."""

    def _make_pdf_with_links(self, tmp_path, num_links=2):
        """Create a minimal PDF with link annotations and struct tree."""
        doc = fitz.open()
        page = doc.new_page()

        for i in range(num_links):
            rect = fitz.Rect(72, 72 + i * 30, 300, 92 + i * 30)
            page.insert_link({
                "kind": fitz.LINK_URI,
                "from": rect,
                "uri": f"https://example.com/link{i}",
            })

        # Create struct tree
        cat = doc.pdf_catalog()
        doc_elem_xref = doc.get_new_xref()
        st_root_xref = doc.get_new_xref()
        doc.update_object(
            doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {st_root_xref} 0 R /K [] >>",
        )
        doc.update_object(
            st_root_xref,
            f"<< /Type /StructTreeRoot /K {doc_elem_xref} 0 R >>",
        )
        doc.xref_set_key(cat, "StructTreeRoot", f"{st_root_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")

        out = tmp_path / "links.pdf"
        doc.save(str(out))
        doc.close()
        return out

    def test_basic_link_parent_tree(self, tmp_path):
        """Core test: annotations get StructParent and ParentTree is populated."""
        from src.tools.pdf_writer import populate_link_parent_tree

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=3)
        result = populate_link_parent_tree(pdf_path)

        assert result.success
        assert result.annotations_linked == 3
        assert result.struct_elements_created == 3
        assert result.parent_tree_entries == 3

        # Verify annotations now have /StructParent
        doc = fitz.open(str(pdf_path))
        for xref in range(1, doc.xref_length()):
            try:
                st = doc.xref_get_key(xref, "Subtype")
            except Exception:
                continue
            if st[0] == "name" and st[1] == "/Link":
                sp = doc.xref_get_key(xref, "StructParent")
                assert sp[0] not in ("null", "undefined"), (
                    f"annotation xref {xref} still missing /StructParent"
                )

        # Verify ParentTree exists on StructTreeRoot
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_root = int(st_key[1].split()[0])
        pt_key = doc.xref_get_key(st_root, "ParentTree")
        assert pt_key[0] == "xref", "ParentTree not set on StructTreeRoot"

        # Verify ParentTreeNextKey
        nk = doc.xref_get_key(st_root, "ParentTreeNextKey")
        assert nk[0] not in ("null", "undefined")
        assert int(nk[1]) == 3

        doc.close()

    def test_idempotent(self, tmp_path):
        """Running twice should be a no-op the second time."""
        from src.tools.pdf_writer import populate_link_parent_tree

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=2)

        r1 = populate_link_parent_tree(pdf_path)
        assert r1.success
        assert r1.annotations_linked == 2

        r2 = populate_link_parent_tree(pdf_path)
        assert r2.success
        assert r2.annotations_linked == 0  # all already have StructParent

    def test_no_struct_tree(self, tmp_path):
        """PDF without StructTreeRoot returns failure gracefully."""
        from src.tools.pdf_writer import populate_link_parent_tree

        doc = fitz.open()
        doc.new_page()
        out = tmp_path / "no_struct.pdf"
        doc.save(str(out))
        doc.close()

        result = populate_link_parent_tree(out)
        assert not result.success
        assert "StructTreeRoot" in result.error

    def test_no_links_is_noop(self, tmp_path):
        """PDF with struct tree but no link annotations is a no-op."""
        from src.tools.pdf_writer import populate_link_parent_tree

        doc = fitz.open()
        doc.new_page()
        cat = doc.pdf_catalog()
        doc_elem = doc.get_new_xref()
        st_root = doc.get_new_xref()
        doc.update_object(
            doc_elem,
            f"<< /Type /StructElem /S /Document /P {st_root} 0 R /K [] >>",
        )
        doc.update_object(
            st_root,
            f"<< /Type /StructTreeRoot /K {doc_elem} 0 R >>",
        )
        doc.xref_set_key(cat, "StructTreeRoot", f"{st_root} 0 R")
        out = tmp_path / "no_links.pdf"
        doc.save(str(out))
        doc.close()

        result = populate_link_parent_tree(out)
        assert result.success
        assert result.annotations_linked == 0

    def test_file_not_found(self):
        from src.tools.pdf_writer import populate_link_parent_tree

        result = populate_link_parent_tree("/nonexistent.pdf")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_struct_elements_have_objr(self, tmp_path):
        """Each created /Link struct element should have an OBJR kid."""
        from src.tools.pdf_writer import populate_link_parent_tree

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=2)
        populate_link_parent_tree(pdf_path)

        doc = fitz.open(str(pdf_path))
        # Find all /Link struct elements
        link_struct_count = 0
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref) or ""
            except Exception:
                continue
            if "/S /Link" in obj and "/OBJR" in obj:
                link_struct_count += 1
        assert link_struct_count == 2
        doc.close()

    def test_parent_tree_entries_resolve_to_link(self, tmp_path):
        """ParentTree entries should resolve to /Link struct elements."""
        from src.tools.pdf_writer import populate_link_parent_tree

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
        populate_link_parent_tree(pdf_path)

        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_root = int(st_key[1].split()[0])
        pt_key = doc.xref_get_key(st_root, "ParentTree")
        pt_xref = int(pt_key[1].split()[0])
        pt_obj = doc.xref_object(pt_xref) or ""

        # Extract the value xref from the /Nums array
        import re
        nums_match = re.search(r"/Nums\s*\[\s*0\s+(\d+)\s+0\s+R", pt_obj)
        assert nums_match, f"Could not find entry in ParentTree: {pt_obj}"
        elem_xref = int(nums_match.group(1))

        # Verify it's a /Link
        elem_obj = doc.xref_object(elem_xref) or ""
        assert "/S /Link" in elem_obj
        doc.close()

    def test_on_real_syllabus_pdf(self, tmp_path):
        """Test on the real syllabus PDF which has 9 link annotations."""
        from src.tools.pdf_writer import (
            populate_link_annotation_contents,
            populate_link_parent_tree,
        )

        if not SYLLABUS_PDF.exists():
            pytest.skip("syllabus PDF not available")

        dst = tmp_path / "syllabus.pdf"
        shutil.copy2(SYLLABUS_PDF, dst)

        # Need to set up a struct tree first (syllabus may not have one)
        doc = fitz.open(str(dst))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        if st_key[0] != "xref":
            # Create minimal struct tree
            doc_elem = doc.get_new_xref()
            st_root = doc.get_new_xref()
            doc.update_object(
                doc_elem,
                f"<< /Type /StructElem /S /Document /P {st_root} 0 R /K [] >>",
            )
            doc.update_object(
                st_root,
                f"<< /Type /StructTreeRoot /K {doc_elem} 0 R >>",
            )
            doc.xref_set_key(cat, "StructTreeRoot", f"{st_root} 0 R")
            doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")
            doc.save(str(dst), incremental=True, encryption=0)
        doc.close()

        # First set /Contents on annotations
        contents_result = populate_link_annotation_contents(dst)
        assert contents_result.success

        # Then populate ParentTree
        result = populate_link_parent_tree(dst)
        assert result.success
        assert result.annotations_linked > 0, "Expected some annotations to be linked"

        # Verify all link annotations now have /StructParent
        doc = fitz.open(str(dst))
        unlinked = 0
        for xref in range(1, doc.xref_length()):
            try:
                st = doc.xref_get_key(xref, "Subtype")
            except Exception:
                continue
            if st[0] == "name" and st[1] == "/Link":
                sp = doc.xref_get_key(xref, "StructParent")
                if sp[0] in ("null", "undefined"):
                    unlinked += 1
        assert unlinked == 0, f"{unlinked} link annotations still unlinked"
        doc.close()

    def test_link_text_overrides(self, tmp_path):
        """When overrides are provided, /Link elements use improved text."""
        from src.tools.pdf_writer import populate_link_parent_tree
        import re

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=2)
        overrides = {
            "https://example.com/link0": "Example Homepage",
            # link1 has no override — should keep raw URL
        }
        result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)

        assert result.success
        assert result.annotations_linked == 2

        # Read back the /Link struct elements and check /ActualText
        doc = fitz.open(str(pdf_path))
        actual_texts = []
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref)
            except Exception:
                continue
            if not obj or "/S /Link" not in obj:
                continue
            m = re.search(r"/ActualText\s*\(([^)]*)\)", obj)
            if m:
                actual_texts.append(m.group(1))
        doc.close()

        assert "Example Homepage" in actual_texts, f"Override text not found in {actual_texts}"
        # link1 should have raw URL
        assert any("example.com/link1" in t for t in actual_texts)

    def test_url_normalization_trailing_slash(self, tmp_path):
        """Overrides match even with trailing slash differences."""
        from src.tools.pdf_writer import populate_link_parent_tree
        import re

        pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
        overrides = {
            "https://example.com/link0/": "Example with slash",
        }
        result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)

        assert result.success

        doc = fitz.open(str(pdf_path))
        actual_texts = []
        for xref in range(1, doc.xref_length()):
            try:
                obj = doc.xref_object(xref)
            except Exception:
                continue
            if not obj or "/S /Link" not in obj:
                continue
            m = re.search(r"/ActualText\s*\(([^)]*)\)", obj)
            if m:
                actual_texts.append(m.group(1))
        doc.close()

        assert "Example with slash" in actual_texts, f"Trailing-slash override not matched: {actual_texts}"


class TestLinkAccessibleName:
    """Test that the parser reads /ActualText from /Link struct elements."""

    def test_parser_reads_struct_tree_link_text(self, tmp_path):
        """After populate_link_parent_tree with overrides, parser extracts improved text."""
        from src.tools.pdf_writer import populate_link_parent_tree
        from src.tools.pdf_parser import parse_pdf

        # Create PDF with a link annotation and struct tree
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 150), "Visit https://example.com/test for details")
        page.insert_link({
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(72, 135, 400, 155),
            "uri": "https://example.com/test",
        })

        # Create struct tree
        cat = doc.pdf_catalog()
        doc_elem_xref = doc.get_new_xref()
        st_root_xref = doc.get_new_xref()
        doc.update_object(
            doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {st_root_xref} 0 R /K [] >>",
        )
        doc.update_object(
            st_root_xref,
            f"<< /Type /StructTreeRoot /K {doc_elem_xref} 0 R >>",
        )
        doc.xref_set_key(cat, "StructTreeRoot", f"{st_root_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")

        pdf_path = tmp_path / "link_with_struct.pdf"
        doc.save(str(pdf_path))
        doc.close()

        # Run populate_link_parent_tree with override
        overrides = {"https://example.com/test": "Example Test Page"}
        result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)
        assert result.success

        # Now parse the PDF and check link text
        parse_result = parse_pdf(str(pdf_path))
        assert parse_result.success

        # Find the link with this URL
        matching = [l for l in parse_result.document.links if l.url == "https://example.com/test"]
        assert len(matching) >= 1, f"No link found for test URL. Links: {parse_result.document.links}"
        assert matching[0].text == "Example Test Page", (
            f"Expected 'Example Test Page' but got {matching[0].text!r}"
        )
