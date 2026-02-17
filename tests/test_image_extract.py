"""Tests for image extraction from docx files."""

from pathlib import Path

import pytest

from src.tools.docx_parser import parse_docx


class TestImageExtraction:
    def test_image_found(self, image_docx: Path):
        result = parse_docx(image_docx)
        assert result.success
        doc = result.document
        assert doc.stats.image_count == 1

    def test_alt_text_extracted(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        img = doc.images[0]
        assert img.alt_text == "A blue test rectangle"

    def test_image_dimensions(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        img = doc.images[0]
        assert img.width_px == 200
        assert img.height_px == 100

    def test_image_has_paragraph_id(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        img = doc.images[0]
        assert img.paragraph_id.startswith("p_")

    def test_surrounding_text(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        img = doc.images[0]
        # Should contain text from surrounding paragraphs
        assert len(img.surrounding_text) > 0

    def test_image_data_present_but_excluded(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        img = doc.images[0]
        # image_data should be accessible on the object
        assert img.image_data is not None
        assert len(img.image_data) > 0
        # But excluded from serialization
        dumped = img.model_dump()
        assert "image_data" not in dumped


class TestMissingAltText:
    def test_missing_alt_count(self, image_no_alt_docx: Path):
        result = parse_docx(image_no_alt_docx)
        doc = result.document
        assert doc.stats.images_missing_alt >= 1

    def test_empty_alt_text(self, image_no_alt_docx: Path):
        result = parse_docx(image_no_alt_docx)
        doc = result.document
        img = doc.images[0]
        assert img.alt_text == ""


class TestImageParaMapping:
    def test_paragraph_has_image_ids(self, image_docx: Path):
        result = parse_docx(image_docx)
        doc = result.document
        # Find the paragraph that contains the image
        img = doc.images[0]
        para = next(
            (p for p in doc.paragraphs if p.id == img.paragraph_id), None
        )
        assert para is not None
        assert img.id in para.image_ids
