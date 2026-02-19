"""Tests for the iText tagger Python wrapper and tagging plan builder.

Tests the tagging plan construction from strategy + document model,
and the Java CLI invocation on real PDFs.
"""

import shutil
from pathlib import Path

import pytest

from src.models.document import (
    CellInfo,
    DocumentModel,
    ImageInfo,
    LinkInfo,
    MetadataInfo,
    ParagraphInfo,
    TableInfo,
)
from src.models.pipeline import RemediationAction, RemediationStrategy
from src.tools.itext_tagger import TaggingResult, build_tagging_plan, tag_pdf
from src.tools.pdf_parser import parse_pdf

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"
LESSON_PDF = TESTDOCS / "Lesson 2 Behaviorism and Structuralism.pdf"

JAR_PATH = Path(__file__).parent.parent / "java" / "itext-tagger" / "build" / "libs" / "itext-tagger-all.jar"
HAS_JAR = JAR_PATH.exists()
skip_no_jar = pytest.mark.skipif(not HAS_JAR, reason="itext-tagger JAR not built")


# ── build_tagging_plan tests ────────────────────────────────────────


class TestBuildTaggingPlan:
    """Tests for building a tagging plan from strategy + document model."""

    def test_heading_action_produces_heading_element(self):
        doc = DocumentModel(
            paragraphs=[
                ParagraphInfo(
                    id="p_0", text="Course Description",
                    bbox=(72.0, 255.0, 174.0, 268.5), page_number=0,
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 2},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert plan["input_path"] == "/in.pdf"
        assert plan["output_path"] == "/out.pdf"
        assert len(plan["elements"]) == 1
        elem = plan["elements"][0]
        assert elem["type"] == "heading"
        assert elem["level"] == 2
        assert elem["text"] == "Course Description"
        assert elem["page"] == 0
        assert elem["bbox"] == [72.0, 255.0, 174.0, 268.5]

    def test_alt_text_action_produces_image_element(self):
        doc = DocumentModel(
            images=[
                ImageInfo(id="img_0", page_number=2, xref=42),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="img_0",
                action_type="set_alt_text",
                parameters={"alt_text": "Bar chart of enrollment"},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert len(plan["elements"]) == 1
        elem = plan["elements"][0]
        assert elem["type"] == "image_alt"
        assert elem["alt_text"] == "Bar chart of enrollment"
        assert elem["page"] == 2
        assert elem["xref"] == 42

    def test_decorative_image_produces_empty_alt(self):
        doc = DocumentModel(
            images=[
                ImageInfo(id="img_0", page_number=1, xref=10),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="img_0",
                action_type="set_decorative",
                parameters={},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert len(plan["elements"]) == 1
        assert plan["elements"][0]["alt_text"] == ""

    def test_table_action_produces_table_element(self):
        doc = DocumentModel(
            tables=[
                TableInfo(
                    id="tbl_0", row_count=2, col_count=2,
                    bbox=(72.0, 100.0, 544.0, 700.0), page_number=5,
                    rows=[
                        [CellInfo(text="Name"), CellInfo(text="Value")],
                        [CellInfo(text="Item 1"), CellInfo(text="100")],
                    ],
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="tbl_0",
                action_type="mark_header_rows",
                parameters={"header_count": 1},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert len(plan["elements"]) == 1
        elem = plan["elements"][0]
        assert elem["type"] == "table"
        assert elem["header_rows"] == 1
        assert elem["page"] == 5

    def test_table_element_includes_row_data(self):
        """Table element should include row/cell data for /TR, /TH, /TD."""
        doc = DocumentModel(
            tables=[
                TableInfo(
                    id="tbl_0", row_count=3, col_count=2,
                    bbox=(72.0, 100.0, 544.0, 500.0), page_number=0,
                    rows=[
                        [CellInfo(text="Name"), CellInfo(text="Grade")],
                        [CellInfo(text="Alice"), CellInfo(text="A")],
                        [CellInfo(text="Bob"), CellInfo(text="B", grid_span=2)],
                    ],
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="tbl_0",
                action_type="mark_header_rows",
                parameters={"header_count": 1},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        elem = plan["elements"][0]
        assert "rows" in elem
        assert len(elem["rows"]) == 3

        # Header row
        header = elem["rows"][0]
        assert len(header["cells"]) == 2
        assert header["cells"][0]["text"] == "Name"
        assert header["cells"][1]["text"] == "Grade"

        # Data rows
        assert elem["rows"][1]["cells"][0]["text"] == "Alice"
        assert elem["rows"][2]["cells"][1]["grid_span"] == 2

    def test_table_empty_rows_produces_empty_list(self):
        """Table with no rows should produce empty rows list in plan."""
        doc = DocumentModel(
            tables=[
                TableInfo(
                    id="tbl_0", row_count=0, col_count=0,
                    page_number=0,
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="tbl_0",
                action_type="mark_header_rows",
                parameters={"header_count": 1},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        elem = plan["elements"][0]
        assert elem["rows"] == []

    def test_metadata_actions_go_to_metadata(self):
        doc = DocumentModel()
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "My Title"},
            ),
            RemediationAction(
                element_id="document",
                action_type="set_language",
                parameters={"language": "en"},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert plan["metadata"]["title"] == "My Title"
        assert plan["metadata"]["language"] == "en"
        # Auto-heading fallback adds an H1 from title when no heading actions exist
        assert len(plan["elements"]) == 1
        assert plan["elements"][0]["type"] == "heading"
        assert plan["elements"][0]["level"] == 1
        assert plan["elements"][0]["text"] == "My Title"

    def test_skipped_actions_excluded(self):
        doc = DocumentModel(
            paragraphs=[
                ParagraphInfo(id="p_0", text="Test", page_number=0),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 1},
                status="skipped",
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")
        assert len(plan["elements"]) == 0

    def test_link_text_action_produces_link_element(self):
        doc = DocumentModel(
            links=[
                LinkInfo(
                    id="link_0", text="http://www.example.com/article",
                    url="http://www.example.com/article",
                    paragraph_id="p_0",
                    bbox=(100.0, 200.0, 300.0, 215.0), page_number=3,
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="link_0",
                action_type="set_link_text",
                parameters={"new_text": "Example.com Article"},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")

        assert len(plan["elements"]) == 1
        elem = plan["elements"][0]
        assert elem["type"] == "link"
        assert elem["link_text"] == "Example.com Article"
        assert elem["link_url"] == "http://www.example.com/article"
        assert elem["page"] == 3
        assert elem["bbox"] == [100.0, 200.0, 300.0, 215.0]

    def test_link_text_missing_link_id_graceful(self):
        doc = DocumentModel()
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="link_999",
                action_type="set_link_text",
                parameters={"new_text": "Descriptive text"},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")
        assert len(plan["elements"]) == 0

    def test_missing_element_id_graceful(self):
        """Action referencing a non-existent element should be silently skipped."""
        doc = DocumentModel()
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_999",
                action_type="set_heading_level",
                parameters={"level": 1},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, "/in.pdf", "/out.pdf")
        assert len(plan["elements"]) == 0

    def test_real_pdf_produces_plan_with_bbox(self):
        """Parse a real PDF and verify plan elements have bbox data."""
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        doc = result.document

        # Create a strategy with the first paragraph as heading
        first_para = doc.paragraphs[0]
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id=first_para.id,
                action_type="set_heading_level",
                parameters={"level": 1},
            ),
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "EMAT 8030"},
            ),
        ])

        plan = build_tagging_plan(strategy, doc, str(SYLLABUS_PDF), "/tmp/out.pdf")

        assert plan["metadata"]["title"] == "EMAT 8030"
        assert len(plan["elements"]) == 1
        heading = plan["elements"][0]
        assert heading["bbox"] is not None
        assert len(heading["bbox"]) == 4
        assert heading["page"] == 0


# ── tag_pdf tests (require built JAR) ───────────────────────────────


class TestTagPdf:
    """Tests for invoking the iText tagger Java CLI."""

    @skip_no_jar
    def test_tag_pdf_sets_metadata(self, tmp_path):
        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {"title": "Test Title", "language": "en"},
            "elements": [],
        }

        result = tag_pdf(plan)

        assert result.success
        assert result.output_path == str(tmp_path / "out.pdf")
        assert any("title" in c.lower() for c in result.changes)
        assert any("language" in c.lower() for c in result.changes)

    @skip_no_jar
    def test_tag_pdf_applies_heading(self, tmp_path):
        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {"title": "", "language": ""},
            "elements": [
                {
                    "type": "heading",
                    "level": 1,
                    "text": "EMAT 8030",
                    "page": 0,
                    "bbox": [186.0, 72.0, 429.0, 101.0],
                },
            ],
        }

        result = tag_pdf(plan)

        assert result.success
        assert result.tags_applied >= 1
        assert Path(result.output_path).exists()

    @skip_no_jar
    def test_tag_pdf_with_real_parsed_data(self, tmp_path):
        """End-to-end: parse PDF → build plan → tag."""
        parse_result = parse_pdf(str(SYLLABUS_PDF))
        assert parse_result.success
        doc = parse_result.document

        # Pick the first paragraph with a fake heading signal
        heading_para = None
        for p in doc.paragraphs:
            if p.fake_heading_signals and p.fake_heading_signals.score >= 0.5:
                heading_para = p
                break

        if heading_para is None:
            heading_para = doc.paragraphs[0]

        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id=heading_para.id,
                action_type="set_heading_level",
                parameters={"level": 1},
            ),
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "EMAT 8030 Syllabus"},
            ),
            RemediationAction(
                element_id="document",
                action_type="set_language",
                parameters={"language": "en"},
            ),
        ])

        output_path = str(tmp_path / "tagged.pdf")
        plan = build_tagging_plan(
            strategy, doc,
            input_path=str(SYLLABUS_PDF),
            output_path=output_path,
        )

        result = tag_pdf(plan)

        assert result.success
        assert result.tags_applied >= 1
        assert Path(output_path).exists()

    @skip_no_jar
    def test_tag_pdf_invalid_page(self, tmp_path):
        """Tagging an element on a non-existent page should warn, not fail."""
        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {"title": "", "language": ""},
            "elements": [
                {
                    "type": "heading",
                    "level": 1,
                    "text": "Test",
                    "page": 9999,
                    "bbox": [72.0, 72.0, 540.0, 90.0],
                },
            ],
        }

        result = tag_pdf(plan)

        # Should succeed overall (just skip the bad page)
        assert result.success
        assert any("out of range" in w.lower() or "skipping" in w.lower() for w in result.warnings)

    @skip_no_jar
    def test_tag_pdf_applies_link_text(self, tmp_path):
        """Tag a link with descriptive text."""
        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {"title": "", "language": ""},
            "elements": [
                {
                    "type": "link",
                    "link_id": "link_0",
                    "link_text": "UGA Academic Honesty Policy",
                    "link_url": "https://ovpi.uga.edu/academic-honesty/",
                    "page": 5,
                    "bbox": [72.0, 400.0, 350.0, 415.0],
                },
            ],
        }

        result = tag_pdf(plan)

        assert result.success
        assert result.tags_applied >= 1
        assert any("link" in c.lower() for c in result.changes)

    @skip_no_jar
    def test_tag_pdf_table_with_row_cells(self, tmp_path):
        """Tag a table with row/cell data and verify /TR, /TH, /TD structure."""
        import fitz

        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {"title": "Table Test", "language": "en"},
            "elements": [
                {
                    "type": "table",
                    "table_id": "tbl_0",
                    "header_rows": 1,
                    "page": 0,
                    "bbox": [72.0, 100.0, 540.0, 400.0],
                    "rows": [
                        {"cells": [
                            {"text": "Assignment", "grid_span": 1},
                            {"text": "Due Date", "grid_span": 1},
                            {"text": "Points", "grid_span": 1},
                        ]},
                        {"cells": [
                            {"text": "Homework 1", "grid_span": 1},
                            {"text": "Jan 15", "grid_span": 1},
                            {"text": "100", "grid_span": 1},
                        ]},
                        {"cells": [
                            {"text": "Homework 2", "grid_span": 1},
                            {"text": "Feb 1", "grid_span": 1},
                            {"text": "100", "grid_span": 1},
                        ]},
                    ],
                },
            ],
        }

        result = tag_pdf(plan)

        assert result.success
        assert result.tags_applied >= 1
        assert any("table" in c.lower() for c in result.changes)
        # Verify the change message mentions row/cell counts
        assert any("3 rows" in c and "9 cells" in c for c in result.changes)

        # Open the tagged PDF with PyMuPDF and verify structure tree
        doc = fitz.open(str(tmp_path / "out.pdf"))
        # Check that StructTreeRoot exists and contains Table, TR, TH, TD
        cat = doc.xref_get_key(doc.xref_length() - 1, "")  # dummy check
        # Walk xrefs looking for /Table, /TR, /TH, /TD type entries
        found_types = set()
        for xref in range(1, doc.xref_length()):
            s_val = doc.xref_get_key(xref, "S")
            if s_val[0] == "name":
                name = s_val[1].lstrip("/")
                if name in ("Table", "TR", "TH", "TD"):
                    found_types.add(name)
        doc.close()

        assert "Table" in found_types, f"No /Table found; got {found_types}"
        assert "TR" in found_types, f"No /TR found; got {found_types}"
        assert "TH" in found_types, f"No /TH found; got {found_types}"
        assert "TD" in found_types, f"No /TD found; got {found_types}"

    def test_tag_pdf_missing_jar(self, tmp_path):
        plan = {
            "input_path": str(SYLLABUS_PDF),
            "output_path": str(tmp_path / "out.pdf"),
            "metadata": {},
            "elements": [],
        }

        result = tag_pdf(plan, jar_path="/nonexistent/itext-tagger.jar")

        assert not result.success
        assert any("not found" in e.lower() for e in result.errors)
