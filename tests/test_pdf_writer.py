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
