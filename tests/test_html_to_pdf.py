"""Tests for the OpenHTMLtoPDF Python wrapper."""

from pathlib import Path

import pytest

from src.tools.html_to_pdf import ConversionResult, html_to_tagged_pdf

JAR_PATH = Path(__file__).parent.parent / "java" / "html-to-pdf" / "build" / "libs" / "html-to-pdf-all.jar"
HAS_JAR = JAR_PATH.exists()
skip_no_jar = pytest.mark.skipif(not HAS_JAR, reason="html-to-pdf JAR not built")

SIMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Test Doc</title></head>
<body>
<h1>Test Document</h1>
<p>This is a paragraph.</p>
<h2>Section One</h2>
<p>Section content here.</p>
<table>
<tr><th scope="col">Name</th><th scope="col">Value</th></tr>
<tr><td>Item A</td><td>42</td></tr>
<tr><td>Item B</td><td>99</td></tr>
</table>
<ul>
<li>First item</li>
<li>Second item</li>
</ul>
</body>
</html>"""


@skip_no_jar
class TestHtmlToTaggedPdf:

    def test_basic_conversion(self, tmp_path):
        html_path = tmp_path / "input.html"
        html_path.write_text(SIMPLE_HTML, encoding="utf-8")
        out_path = tmp_path / "output.pdf"

        result = html_to_tagged_pdf(str(html_path), str(out_path))

        assert result.success
        assert Path(result.output_path).exists()
        assert Path(result.output_path).stat().st_size > 0

    def test_output_is_valid_pdf(self, tmp_path):
        html_path = tmp_path / "input.html"
        html_path.write_text(SIMPLE_HTML, encoding="utf-8")
        out_path = tmp_path / "output.pdf"

        result = html_to_tagged_pdf(str(html_path), str(out_path))

        assert result.success
        # Verify it's a valid PDF by opening with PyMuPDF
        import fitz
        doc = fitz.open(str(out_path))
        assert doc.page_count >= 1
        # Note: OpenHTMLtoPDF uses embedded fonts that PyMuPDF may not
        # fully decode for text extraction. Verify page count and title.
        title = doc.metadata.get("title", "")
        doc.close()
        # Title should be set from the HTML <title> tag
        assert title == "Test Doc"  # OpenHTMLtoPDF preserves <title>

    def test_missing_html_file(self, tmp_path):
        result = html_to_tagged_pdf(
            "/nonexistent/input.html",
            str(tmp_path / "output.pdf"),
        )
        assert not result.success
        assert any("not found" in e.lower() for e in result.errors)

    def test_missing_jar(self, tmp_path):
        html_path = tmp_path / "input.html"
        html_path.write_text(SIMPLE_HTML, encoding="utf-8")

        result = html_to_tagged_pdf(
            str(html_path),
            str(tmp_path / "output.pdf"),
            jar_path="/nonexistent/html-to-pdf.jar",
        )
        assert not result.success
        assert any("not found" in e.lower() for e in result.errors)

    def test_creates_output_directory(self, tmp_path):
        html_path = tmp_path / "input.html"
        html_path.write_text(SIMPLE_HTML, encoding="utf-8")
        out_path = tmp_path / "subdir" / "nested" / "output.pdf"

        result = html_to_tagged_pdf(str(html_path), str(out_path))

        assert result.success
        assert Path(result.output_path).exists()
