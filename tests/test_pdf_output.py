"""Tests for PDF output (WeasyPrint rendering)."""

import pytest
from pathlib import Path

from src.tools.pdf_output import render_pdf, PdfOutputResult


@pytest.fixture
def output_dir(tmp_path):
    return tmp_path / "pdf_output"


class TestRenderPdf:
    """Test HTML → PDF rendering."""

    def test_render_simple_html(self, output_dir):
        html = """<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body><h1>Hello World</h1><p>Test paragraph.</p></body>
</html>"""
        out = output_dir / "test.pdf"
        result = render_pdf(html, out)
        assert result.success is True
        assert Path(result.output_path).exists()
        # Check it's actually a PDF
        with open(result.output_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"

    def test_render_with_headings(self, output_dir):
        html = """<!DOCTYPE html>
<html lang="en">
<head><title>Headings Test</title></head>
<body>
<h1>Title</h1>
<h2>Section 1</h2>
<p>Content under section 1.</p>
<h2>Section 2</h2>
<p>Content under section 2.</p>
</body>
</html>"""
        out = output_dir / "headings.pdf"
        result = render_pdf(html, out)
        assert result.success is True
        assert Path(result.output_path).exists()

    def test_render_with_table(self, output_dir):
        html = """<!DOCTYPE html>
<html lang="en">
<head><title>Table Test</title></head>
<body>
<table>
<thead><tr><th scope="col">Name</th><th scope="col">Value</th></tr></thead>
<tbody><tr><td>A</td><td>1</td></tr></tbody>
</table>
</body>
</html>"""
        out = output_dir / "table.pdf"
        result = render_pdf(html, out)
        assert result.success is True

    def test_render_with_image_placeholder(self, output_dir):
        html = """<!DOCTYPE html>
<html lang="en">
<head><title>Image Test</title></head>
<body>
<p>A document with an image reference.</p>
<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==" alt="A test image">
</body>
</html>"""
        out = output_dir / "image.pdf"
        result = render_pdf(html, out)
        assert result.success is True

    def test_render_empty_html_fails(self, output_dir):
        result = render_pdf("", output_dir / "empty.pdf")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_render_creates_parent_dir(self, tmp_path):
        html = """<!DOCTYPE html>
<html lang="en"><head><title>Test</title></head>
<body><p>Content</p></body></html>"""
        nested = tmp_path / "a" / "b" / "c" / "test.pdf"
        result = render_pdf(html, nested)
        assert result.success is True
        assert nested.exists()

    def test_output_path_in_result(self, output_dir):
        html = """<!DOCTYPE html>
<html lang="en"><head><title>Test</title></head>
<body><p>Content</p></body></html>"""
        out = output_dir / "result_path.pdf"
        result = render_pdf(html, out)
        assert result.success is True
        assert result.output_path == str(out)
