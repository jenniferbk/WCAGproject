"""Tests for complete struct tree tagging."""

import pytest
import shutil
from pathlib import Path

import fitz

from src.tools.pdf_writer import (
    Token,
    _tokenize_content_stream,
)

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"


class TestGetMaxMcidForPage:
    """Per-page MCID scanning from content streams."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_no_bdcs_returns_negative_one(self):
        """Page with no BDC markers has no MCIDs."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("BT (hello) Tj ET")
        assert _get_max_mcid_for_page(tokens) == -1

    def test_single_mcid(self):
        """One BDC with MCID 0."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("/P <</MCID 0>> BDC BT (hi) Tj ET EMC")
        assert _get_max_mcid_for_page(tokens) == 0

    def test_multiple_mcids_returns_max(self):
        """Multiple BDCs — return the highest MCID."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = (
            "/P <</MCID 0>> BDC BT (a) Tj ET EMC\n"
            "/P <</MCID 3>> BDC BT (b) Tj ET EMC\n"
            "/H1 <</MCID 1>> BDC BT (c) Tj ET EMC\n"
        )
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == 3

    def test_artifact_bdc_ignored(self):
        """/Artifact BDC has no MCID — should be ignored."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = "/Artifact <</Type /Pagination>> BDC BT (1) Tj ET EMC"
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == -1


class TestApplyContentTagWrappers:
    """Tests for _apply_content_tag_wrappers() — mixed /P and /Artifact wrapping."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_single_p_run(self):
        """One run tagged as /P with MCID 0."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (hello) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=0)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 0>> BDC" in text
        assert "EMC" in text
        assert "/Artifact" not in text

    def test_single_artifact_run(self):
        """One run tagged as /Artifact."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (3) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/Artifact", mcid=None)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/Artifact <</Type /Pagination>> BDC" in text
        assert "EMC" in text
        assert "/MCID" not in text

    def test_mixed_p_and_artifact(self):
        """Two runs — first /P, second /Artifact, separated by existing tagged content."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        # Existing tagged heading between two untagged runs forces them to be separate
        stream = (
            "BT (body text) Tj ET "
            "/H1 <</MCID 0>> BDC BT (heading) Tj ET EMC "
            "BT (3) Tj ET"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 2
        tagged_runs = [
            TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=1),
            TaggedRun(start=runs[1][0], end=runs[1][1], tag_type="/Artifact", mcid=None),
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 1>> BDC" in text
        assert "/Artifact <</Type /Pagination>> BDC" in text

    def test_no_runs_returns_original(self):
        """Empty run list returns original stream."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers,
        )
        tokens = self._tokenize("BT (x) Tj ET")
        result = _apply_content_tag_wrappers(tokens, [])
        text = result.decode("latin-1")
        assert "BDC" not in text

    def test_unique_mcids_per_run(self):
        """Each /P run gets its own MCID."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        # Three untagged runs separated by existing tagged headings
        stream = (
            "BT (a) Tj ET "
            "/H1 <</MCID 0>> BDC BT (h1) Tj ET EMC "
            "BT (b) Tj ET "
            "/H2 <</MCID 1>> BDC BT (h2) Tj ET EMC "
            "BT (c) Tj ET"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 3
        tagged_runs = [
            TaggedRun(start=r[0], end=r[1], tag_type="/P", mcid=i + 2)
            for i, r in enumerate(runs)
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 2>> BDC" in text
        assert "/P <</MCID 3>> BDC" in text
        assert "/P <</MCID 4>> BDC" in text


class TestExtractTextFromRun:
    """Tests for extracting text from token runs."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_simple_tj(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT (Hello world) Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello world" in text

    def test_tj_array(self):
        """TJ operator with array of strings and kerning."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT [(He) -10 (llo)] TJ ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text

    def test_no_text_ops_returns_empty(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("/Fm0 Do")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert text == ""

    def test_hex_string(self):
        """Hex-encoded string <48656C6C6F> = 'Hello'."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT <48656C6C6F> Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text


class TestIsPageFurniture:
    """Tests for page furniture detection."""

    def test_bare_page_number(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("3", set()) is True

    def test_page_number_with_dashes(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("- 3 -", set()) is True

    def test_roman_numeral(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("iv", set()) is True
        assert _is_page_furniture("XII", set()) is True

    def test_body_text_not_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture(
            "The results of this study demonstrate that",
            set(),
        ) is False

    def test_repeated_header(self):
        from src.tools.pdf_writer import _is_page_furniture
        furniture = {"Chapter 3: Methods"}
        assert _is_page_furniture("Chapter 3: Methods", furniture) is True

    def test_empty_string_is_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("", set()) is True
        assert _is_page_furniture("   ", set()) is True


class TestScanPageFurniture:
    """Tests for repeated header/footer detection across pages."""

    def test_repeated_text_detected(self, tmp_path):
        """Text appearing on 3+ pages at similar positions → furniture."""
        from src.tools.pdf_writer import _scan_page_furniture
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page()
            page.insert_text((72, 30), "Journal of Education Vol. 12", fontsize=9)
            page.insert_text((72, 300), f"Content of page {i + 1}", fontsize=12)
            page.insert_text((300, 780), str(i + 1), fontsize=9)
        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        furniture = _scan_page_furniture(str(pdf_path))
        assert "Journal of Education Vol. 12" in furniture

    def test_unique_text_not_detected(self, tmp_path):
        """Text appearing on only 1 page is not furniture."""
        from src.tools.pdf_writer import _scan_page_furniture
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 300), f"Unique content {i}", fontsize=12)
        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        furniture = _scan_page_furniture(str(pdf_path))
        assert len(furniture) == 0


class TestTagOrArtifactUntaggedContent:
    """End-to-end tests for the new content tagging function."""

    def _make_pdf_with_content(self, tmp_path, stream_str: str) -> Path:
        """Create a 1-page PDF with the given content stream and minimal struct tree."""
        doc = fitz.open()
        page = doc.new_page()
        xref = page.xref
        contents = doc.xref_get_key(xref, "Contents")
        if contents[0] == "xref":
            c_xref = int(contents[1].split()[0])
        else:
            c_xref = doc.get_new_xref()
            doc.update_object(c_xref, "<< /Length 0 >>")
            doc.xref_set_key(xref, "Contents", f"{c_xref} 0 R")

        doc.update_stream(c_xref, stream_str.encode("latin-1"))

        # Add a minimal struct tree with /Document element
        cat = doc.pdf_catalog()
        sroot_xref = doc.get_new_xref()
        doc_elem_xref = doc.get_new_xref()
        doc.update_object(doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {sroot_xref} 0 R /K [] >>")
        doc.update_object(sroot_xref,
            f"<< /Type /StructTreeRoot /K [{doc_elem_xref} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sroot_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_untagged_body_text_becomes_p(self, tmp_path):
        """Untagged body text gets /P BDC, not /Artifact."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT /F0 12 Tf 72 700 Td (This is body text for the paper) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1
        doc = fitz.open(str(pdf_path))
        stream = doc[0].read_contents().decode("latin-1")
        doc.close()
        assert "/P <<" in stream
        assert "/MCID" in stream

    def test_already_tagged_content_untouched(self, tmp_path):
        """Content inside BDC/EMC is not re-tagged."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "/P <</MCID 0>> BDC\nBT (tagged body) Tj ET\nEMC"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged == 0
        assert result.artifacts_tagged == 0

    def test_mixed_tagged_and_untagged(self, tmp_path):
        """Tagged heading + untagged body → body gets /P with non-colliding MCID."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        stream = (
            "/H1 <</MCID 0>> BDC\n"
            "BT (Heading) Tj ET\n"
            "EMC\n"
            "BT (This is untagged body text paragraph) Tj ET\n"
        )
        pdf_path = self._make_pdf_with_content(tmp_path, stream)
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1
        doc = fitz.open(str(pdf_path))
        stream_out = doc[0].read_contents().decode("latin-1")
        doc.close()
        # MCID should be 1 (next after existing 0)
        assert "/P <</MCID 1>> BDC" in stream_out

    def test_page_number_becomes_artifact(self, tmp_path):
        """Bare page number gets /Artifact, not /P."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT /F0 9 Tf 300 30 Td (3) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.artifacts_tagged >= 1
        doc = fitz.open(str(pdf_path))
        stream = doc[0].read_contents().decode("latin-1")
        doc.close()
        assert "/Artifact" in stream

    def test_struct_elements_created_in_tree(self, tmp_path):
        """New /P struct elements appear in StructTreeRoot."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT (paragraph one) Tj ET\nBT (paragraph two) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1
        # Check struct tree has /P elements
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st = doc.xref_get_key(cat, "StructTreeRoot")
        st_xref = int(st[1].split()[0])
        st_obj = doc.xref_object(st_xref) or ""
        import re as _re
        doc_kids = _re.findall(r"(\d+)\s+0\s+R", st_obj)
        found_p = False
        for kid_xref_str in doc_kids:
            kid_obj = doc.xref_object(int(kid_xref_str)) or ""
            for m in _re.finditer(r"(\d+)\s+0\s+R", kid_obj):
                grandkid = doc.xref_object(int(m.group(1))) or ""
                if "/S /P" in grandkid:
                    found_p = True
                    break
        doc.close()
        assert found_p, "No /P struct elements found in tree"

    def test_page_mcid_map_populated(self, tmp_path):
        """Result includes page_mcid_map for ParentTree update."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT (body text here) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert 0 in result.page_mcid_map
        entries = result.page_mcid_map[0]
        assert len(entries) >= 1
        mcid, xref = entries[0]
        assert isinstance(mcid, int)
        assert isinstance(xref, int)


class TestUpdateParentTreeForMcids:
    """Tests for MCID→struct element ParentTree entries."""

    def _make_tagged_pdf(self, tmp_path) -> Path:
        """Create a PDF with a struct tree, some MCIDs, and /P elements."""
        doc = fitz.open()
        page = doc.new_page()
        xref = page.xref
        contents = doc.xref_get_key(xref, "Contents")
        if contents[0] == "xref":
            c_xref = int(contents[1].split()[0])
        else:
            c_xref = doc.get_new_xref()
            doc.update_object(c_xref, "<< /Length 0 >>")
            doc.xref_set_key(xref, "Contents", f"{c_xref} 0 R")
        stream = (
            "/H1 <</MCID 0>> BDC\nBT (Heading) Tj ET\nEMC\n"
            "/P <</MCID 1>> BDC\nBT (Body) Tj ET\nEMC\n"
        )
        doc.update_stream(c_xref, stream.encode("latin-1"))

        cat = doc.pdf_catalog()
        sroot_xref = doc.get_new_xref()
        doc_elem_xref = doc.get_new_xref()
        h1_xref = doc.get_new_xref()
        p_xref = doc.get_new_xref()

        doc.update_object(h1_xref,
            f"<< /Type /StructElem /S /H1 /P {doc_elem_xref} 0 R /Pg {page.xref} 0 R /K 0 >>")
        doc.update_object(p_xref,
            f"<< /Type /StructElem /S /P /P {doc_elem_xref} 0 R /Pg {page.xref} 0 R /K 1 >>")
        doc.update_object(doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {sroot_xref} 0 R /K [{h1_xref} 0 R {p_xref} 0 R] >>")
        doc.update_object(sroot_xref,
            f"<< /Type /StructTreeRoot /K [{doc_elem_xref} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sroot_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "tagged.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_creates_parent_tree_entries(self, tmp_path):
        """Page MCID map produces ParentTree with array entries."""
        from src.tools.pdf_writer import _update_parent_tree_for_mcids
        pdf_path = self._make_tagged_pdf(tmp_path)
        doc = fitz.open(str(pdf_path))

        p_elem_xref = doc.get_new_xref()
        doc.update_object(p_elem_xref, "<< /Type /StructElem /S /P >>")

        page_mcid_map = {0: [(2, p_elem_xref)]}
        count = _update_parent_tree_for_mcids(doc, page_mcid_map)
        doc.save(str(pdf_path), incremental=True, encryption=0)
        doc.close()

        assert count >= 1

        # Verify ParentTree exists
        doc2 = fitz.open(str(pdf_path))
        cat2 = doc2.pdf_catalog()
        st2 = doc2.xref_get_key(cat2, "StructTreeRoot")
        st2_xref = int(st2[1].split()[0])
        pt_key = doc2.xref_get_key(st2_xref, "ParentTree")
        assert pt_key[0] == "xref", "ParentTree should exist"
        doc2.close()

    def test_empty_map_does_nothing(self, tmp_path):
        from src.tools.pdf_writer import _update_parent_tree_for_mcids
        pdf_path = self._make_tagged_pdf(tmp_path)
        doc = fitz.open(str(pdf_path))
        count = _update_parent_tree_for_mcids(doc, {})
        doc.close()
        assert count == 0
