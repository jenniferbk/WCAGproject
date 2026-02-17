"""Tests for data models: creation, serialization, immutability, field exclusion."""

import pytest
from pydantic import ValidationError

from src.models.document import (
    ContrastIssue,
    DocumentModel,
    DocumentStats,
    FakeHeadingSignals,
    ImageInfo,
    LinkInfo,
    MetadataInfo,
    ParagraphInfo,
    RunInfo,
    TableInfo,
)


class TestRunInfo:
    def test_create_minimal(self):
        run = RunInfo(text="hello")
        assert run.text == "hello"
        assert run.bold is None
        assert run.font_size_pt is None

    def test_create_full(self):
        run = RunInfo(
            text="bold text",
            bold=True,
            italic=False,
            font_size_pt=14.0,
            color="#FF0000",
        )
        assert run.bold is True
        assert run.color == "#FF0000"

    def test_immutable(self):
        run = RunInfo(text="test")
        with pytest.raises(ValidationError):
            run.text = "changed"


class TestImageInfo:
    def test_image_data_excluded_from_json(self):
        img = ImageInfo(
            id="img_0",
            image_data=b"\x89PNG\r\n" * 1000,
            content_type="image/png",
            alt_text="A photo",
            width_px=800,
            height_px=600,
        )
        dumped = img.model_dump()
        assert "image_data" not in dumped
        assert dumped["alt_text"] == "A photo"
        assert dumped["width_px"] == 800

    def test_image_data_excluded_from_json_str(self):
        img = ImageInfo(id="img_0", image_data=b"bytes here")
        json_str = img.model_dump_json()
        assert "bytes here" not in json_str
        assert "image_data" not in json_str

    def test_image_data_still_accessible(self):
        data = b"\x89PNG\r\n"
        img = ImageInfo(id="img_0", image_data=data)
        assert img.image_data == data


class TestParagraphInfo:
    def test_heading_paragraph(self):
        para = ParagraphInfo(
            id="p_0",
            text="Title",
            style_name="Heading 1",
            heading_level=1,
        )
        assert para.heading_level == 1

    def test_with_fake_heading_signals(self):
        signals = FakeHeadingSignals(
            all_runs_bold=True,
            font_size_above_avg=True,
            is_short=True,
            score=0.75,
        )
        para = ParagraphInfo(
            id="p_1",
            text="Bold Title",
            fake_heading_signals=signals,
        )
        assert para.fake_heading_signals.score == 0.75


class TestDocumentModel:
    def test_create_empty(self):
        doc = DocumentModel()
        assert doc.paragraphs == []
        assert doc.tables == []
        assert doc.stats.paragraph_count == 0

    def test_serialization_roundtrip(self):
        doc = DocumentModel(
            source_format="docx",
            metadata=MetadataInfo(title="Test", language="en"),
            paragraphs=[
                ParagraphInfo(id="p_0", text="Hello"),
            ],
            stats=DocumentStats(paragraph_count=1),
        )
        json_str = doc.model_dump_json()
        restored = DocumentModel.model_validate_json(json_str)
        assert restored.metadata.title == "Test"
        assert restored.paragraphs[0].text == "Hello"

    def test_immutable(self):
        doc = DocumentModel(source_format="docx")
        with pytest.raises(ValidationError):
            doc.source_format = "pdf"


class TestContrastIssue:
    def test_create(self):
        issue = ContrastIssue(
            paragraph_id="p_5",
            run_index=0,
            text_preview="Light text",
            foreground="#AAAAAA",
            background="#FFFFFF",
            contrast_ratio=2.32,
            required_ratio=4.5,
            is_large_text=False,
        )
        assert issue.contrast_ratio == 2.32
        assert not issue.is_large_text
