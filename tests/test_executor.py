"""Tests for the remediation executor.

Tests execute() for docx and execute_pdf() for PDF workflows,
using real test documents and mock strategies.
"""

import shutil
from pathlib import Path

import pytest

from src.agent.executor import ExecutionResult, execute, execute_pdf
from src.models.document import (
    CellInfo,
    DocumentModel,
    ImageInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)
from src.models.pipeline import RemediationAction, RemediationStrategy
from src.tools.pdf_parser import parse_pdf

TESTDOCS = Path(__file__).parent.parent / "testdocs"
DOCX_FILE = TESTDOCS / "Assignment 1.docx"
DOCX_WITH_LINKS = TESTDOCS / "Wesley Olivia Day 4.docx"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"


# ── Docx executor tests ───────────────────────────────────────────────


class TestExecuteDocx:
    """Tests for execute() on .docx files."""

    @pytest.fixture
    def docx_copy(self, tmp_path):
        if not DOCX_FILE.exists():
            pytest.skip("Test docx not found")
        dest = tmp_path / "test.docx"
        shutil.copy2(DOCX_FILE, dest)
        return dest

    def test_execute_empty_strategy(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 0
        assert result.actions_failed == 0

    def test_execute_set_title(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "Test Assignment"},
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1
        assert Path(result.output_path).exists()

    def test_execute_set_language(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_language",
                parameters={"language": "en"},
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_set_heading_level(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"paragraph_index": 0, "level": 1},
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_skipped_actions(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"paragraph_index": 0, "level": 1},
                status="skipped",
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 0
        assert result.actions_skipped == 1

    def test_execute_invalid_input(self, tmp_path):
        strategy = RemediationStrategy(actions=[])
        result = execute(strategy, "/nonexistent/file.docx")

        assert not result.success
        assert "not found" in result.error.lower()

    def test_execute_unknown_action_type(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="magical_fix",
                parameters={},
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        # Unknown actions get skipped, not failed
        assert result.actions_skipped == 1

    def test_execute_missing_paragraph_index(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 1},  # missing paragraph_index
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success  # overall succeeds
        assert result.actions_failed == 1  # but this action failed


    def test_execute_set_link_text(self, tmp_path):
        if not DOCX_WITH_LINKS.exists():
            pytest.skip("Test docx with links not found")
        dest = tmp_path / "links.docx"
        shutil.copy2(DOCX_WITH_LINKS, dest)

        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="link_0",
                action_type="set_link_text",
                parameters={"new_text": "Accessible Link Description"},
            ),
        ])
        result = execute(strategy, str(dest), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1
        assert result.updated_actions[0]["status"] == "executed"

    def test_execute_set_link_text_empty(self, docx_copy, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="link_0",
                action_type="set_link_text",
                parameters={"new_text": ""},
            ),
        ])
        result = execute(strategy, str(docx_copy), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_failed == 1

    def test_execute_set_link_text_pptx_skipped(self, tmp_path):
        """set_link_text should be skipped for PPTX files."""
        pptx_files = list(TESTDOCS.glob("*.pptx"))
        if not pptx_files:
            pytest.skip("No PPTX test files found")
        dest = tmp_path / "test.pptx"
        shutil.copy2(pptx_files[0], dest)

        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="link_0",
                action_type="set_link_text",
                parameters={"new_text": "Some Text"},
            ),
        ])
        result = execute(strategy, str(dest), output_dir=str(tmp_path))

        assert result.success
        assert result.actions_skipped == 1


# ── PDF executor tests ─────────────────────────────────────────────────


class TestExecutePdf:
    """Tests for execute_pdf() on PDF documents."""

    @pytest.fixture
    def parsed_doc(self):
        if not SYLLABUS_PDF.exists():
            pytest.skip("Test PDF not found")
        result = parse_pdf(str(SYLLABUS_PDF))
        assert result.success
        return result.document

    def test_execute_pdf_empty_strategy(self, parsed_doc, tmp_path):
        strategy = RemediationStrategy(actions=[])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 0
        assert Path(result.output_path).exists()

    def test_execute_pdf_set_title_and_language(self, parsed_doc, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "EMAT 8030 Test Syllabus"},
            ),
            RemediationAction(
                element_id="document",
                action_type="set_language",
                parameters={"language": "en"},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 2

    def test_execute_pdf_set_heading_level(self, parsed_doc, tmp_path):
        # Use the first paragraph as a heading
        first_id = parsed_doc.paragraphs[0].id
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id=first_id,
                action_type="set_heading_level",
                parameters={"level": 1},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_pdf_model_dict_mutation(self, parsed_doc, tmp_path):
        """Verify that execute_pdf updates the model dict correctly."""
        first_para_id = parsed_doc.paragraphs[0].id
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "New Title"},
            ),
            RemediationAction(
                element_id=first_para_id,
                action_type="set_heading_level",
                parameters={"level": 2},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 2
        # Verify action details are recorded
        title_action = [a for a in result.updated_actions if a["action_type"] == "set_title"]
        assert len(title_action) == 1
        assert title_action[0]["status"] == "executed"

    def test_execute_pdf_nonexistent_element(self, parsed_doc, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_9999",
                action_type="set_heading_level",
                parameters={"level": 1},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success  # overall succeeds
        assert result.actions_failed == 1

    def test_execute_pdf_companion_html_generated(self, parsed_doc, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "Test"},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        if result.companion_html_path:
            assert Path(result.companion_html_path).exists()

    def test_execute_pdf_contrast_fix_collects_color_map(self, tmp_path):
        """Verify fix_all_contrast on PDF collects color changes."""
        doc = DocumentModel(
            source_format="pdf",
            source_path=str(SYLLABUS_PDF),
            paragraphs=[
                ParagraphInfo(
                    id="p_0", text="Light text",
                    runs=[RunInfo(text="Light text", color="#C0C0C0", font_size_pt=12.0)],
                    page_number=0,
                ),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="document",
                action_type="set_title",
                parameters={"title": "Test"},
            ),
            RemediationAction(
                element_id="contrast",
                action_type="fix_all_contrast",
                parameters={"default_bg": "#FFFFFF"},
            ),
        ])
        result = execute_pdf(strategy, doc, output_dir=str(tmp_path))

        assert result.success
        # The contrast action should have executed
        contrast_actions = [a for a in result.updated_actions if a["action_type"] == "fix_all_contrast"]
        assert len(contrast_actions) == 1
        assert contrast_actions[0]["status"] == "executed"
        assert "1" in contrast_actions[0]["result_detail"]  # "Fixed 1 contrast issues"

    def test_execute_pdf_mark_header_rows(self, parsed_doc, tmp_path):
        if not parsed_doc.tables:
            pytest.skip("No tables in test PDF")
        tbl_id = parsed_doc.tables[0].id
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id=tbl_id,
                action_type="mark_header_rows",
                parameters={"header_count": 1},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_pdf_set_link_text(self, parsed_doc, tmp_path):
        if not parsed_doc.links:
            pytest.skip("No links in test PDF")
        link_id = parsed_doc.links[0].id
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id=link_id,
                action_type="set_link_text",
                parameters={"new_text": "Example Descriptive Link"},
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_pdf_mark_header_rows_empty_first_row_skipped(self, tmp_path):
        """mark_header_rows should skip when first row is all empty."""
        doc = DocumentModel(
            source_format="pdf",
            source_path=str(SYLLABUS_PDF),
            tables=[
                TableInfo(
                    id="tbl_0",
                    rows=[
                        [CellInfo(text=""), CellInfo(text=""), CellInfo(text="")],
                        [CellInfo(text="Date"), CellInfo(text="Topic"), CellInfo(text="Reading")],
                        [CellInfo(text="Jan 12"), CellInfo(text="Intro"), CellInfo(text="Ch 1")],
                    ],
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
        result = execute_pdf(strategy, doc, output_dir=str(tmp_path))

        assert result.success
        # The action should be skipped, not executed
        skipped = [a for a in result.updated_actions if a["status"] == "skipped"]
        assert len(skipped) == 1
        assert "empty" in skipped[0]["result_detail"].lower()

    def test_execute_pdf_mark_header_rows_non_empty_first_row_works(self, tmp_path):
        """mark_header_rows should work normally when first row has content."""
        doc = DocumentModel(
            source_format="pdf",
            source_path=str(SYLLABUS_PDF),
            tables=[
                TableInfo(
                    id="tbl_0",
                    rows=[
                        [CellInfo(text="Date"), CellInfo(text="Topic"), CellInfo(text="Reading")],
                        [CellInfo(text="Jan 12"), CellInfo(text="Intro"), CellInfo(text="Ch 1")],
                    ],
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
        result = execute_pdf(strategy, doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_executed == 1

    def test_execute_pdf_skipped_actions(self, parsed_doc, tmp_path):
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 1},
                status="skipped",
            ),
        ])
        result = execute_pdf(strategy, parsed_doc, output_dir=str(tmp_path))

        assert result.success
        assert result.actions_skipped == 1
        assert result.actions_executed == 0
