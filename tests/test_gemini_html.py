"""Tests for the Gemini HTML generator.

Tests the non-API functions (HTML wrapping, remediation hints building).
API-dependent functions are tested separately in integration tests.
"""

import pytest

from src.models.document import (
    DocumentModel,
    ImageInfo,
    MetadataInfo,
    ParagraphInfo,
    TableInfo,
)
from src.models.pipeline import RemediationAction, RemediationStrategy
from src.tools.gemini_html import (
    GeminiHtmlResult,
    _build_remediation_hints,
    _wrap_html,
)


class TestBuildRemediationHints:

    def test_heading_hints(self):
        doc = DocumentModel(
            paragraphs=[
                ParagraphInfo(id="p_0", text="Course Description", page_number=0),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 2},
            ),
        ])

        hints = _build_remediation_hints(doc, strategy)

        assert "Course Description" in hints
        assert "Heading 2" in hints or "H2" in hints

    def test_alt_text_hints(self):
        doc = DocumentModel(
            images=[
                ImageInfo(id="img_0", page_number=2, alt_text=""),
            ],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="img_0",
                action_type="set_alt_text",
                parameters={"alt_text": "A bar chart showing trends"},
            ),
        ])

        hints = _build_remediation_hints(doc, strategy)

        assert "img_0" in hints
        assert "bar chart" in hints

    def test_table_hints(self):
        doc = DocumentModel(
            tables=[TableInfo(id="tbl_0", row_count=3, col_count=2)],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="tbl_0",
                action_type="mark_header_rows",
                parameters={"header_count": 1},
            ),
        ])

        hints = _build_remediation_hints(doc, strategy)

        assert "tbl_0" in hints
        assert "1 header" in hints

    def test_no_strategy_returns_message(self):
        doc = DocumentModel()
        hints = _build_remediation_hints(doc, None)
        assert "No remediation strategy" in hints

    def test_skipped_actions_excluded(self):
        doc = DocumentModel(
            paragraphs=[ParagraphInfo(id="p_0", text="Test")],
        )
        strategy = RemediationStrategy(actions=[
            RemediationAction(
                element_id="p_0",
                action_type="set_heading_level",
                parameters={"level": 1},
                status="skipped",
            ),
        ])

        hints = _build_remediation_hints(doc, strategy)

        assert "Test" not in hints


class TestWrapHtml:

    def test_basic_structure(self):
        html = _wrap_html("<h1>Hello</h1>", title="My Doc", language="en")

        assert '<!DOCTYPE html>' in html
        assert 'lang="en"' in html
        assert '<title>My Doc</title>' in html
        assert '<h1>Hello</h1>' in html

    def test_escapes_title(self):
        html = _wrap_html("", title='Title with "quotes" & <tags>')
        assert '&quot;' in html or '"quotes"' not in html
        assert '&amp;' in html

    def test_default_language(self):
        html = _wrap_html("")
        assert 'lang="en"' in html

    def test_gemini_html_result_no_api_key(self):
        """generate_gemini_html should fail gracefully without API key."""
        import os
        from src.tools.gemini_html import generate_gemini_html

        # Temporarily clear the API key
        old_key = os.environ.pop("GEMINI_API_KEY", None)
        try:
            result = generate_gemini_html(
                doc_model=DocumentModel(source_path="/nonexistent.pdf"),
            )
            assert not result.success
            assert "GEMINI_API_KEY" in result.error or "not found" in result.error.lower()
        finally:
            if old_key is not None:
                os.environ["GEMINI_API_KEY"] = old_key
