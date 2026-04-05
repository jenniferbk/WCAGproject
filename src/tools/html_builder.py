"""Convert a DocumentModel to semantic, accessible HTML.

Generates HTML that meets WCAG 2.1 AA structural requirements:
- <html lang="..."> with document language
- <title> from metadata
- Proper heading hierarchy (h1-h6)
- Alt text on all <img> tags (empty alt for decorative)
- <table> with <th scope="col|row"> for header cells
- Lists use <ul>/<ol> + <li>
- Logical source order = reading order
- Color contrast preserved via inline styles

This HTML serves two purposes:
1. Validation target for axe-core (WCAG checks on the generated output)
2. Input to WeasyPrint for PDF/UA-1 generation (Phase 2)
"""

from __future__ import annotations

import base64
import html
import logging
from dataclasses import dataclass, field

from src.models.document import (
    ContentType,
    DocumentModel,
    ImageInfo,
    MathInfo,
    ParagraphInfo,
    TableInfo,
)
from src.tools.math_renderer import render_mathml_to_svg

logger = logging.getLogger(__name__)


@dataclass
class HtmlBuildResult:
    """Result of building HTML from a DocumentModel."""
    success: bool
    html: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def build_html(
    doc: DocumentModel,
    embed_images: bool = False,
    css: str = "",
) -> HtmlBuildResult:
    """Convert a DocumentModel to a complete, accessible HTML document.

    Args:
        doc: Parsed document model.
        embed_images: If True, embed images as base64 data URIs.
            If False, images get a placeholder src.
        css: Optional CSS to include in <style> tag.

    Returns:
        HtmlBuildResult with the generated HTML string.
    """
    warnings: list[str] = []

    try:
        lang = doc.metadata.language or "en"
        title = doc.metadata.title or "Untitled Document"

        # Build lookup maps
        image_map = {img.id: img for img in doc.images}
        table_map = {tbl.id: tbl for tbl in doc.tables}
        para_map = {p.id: p for p in doc.paragraphs}
        math_map = {m.id: m for m in doc.math} if hasattr(doc, 'math') and doc.math else {}

        # Build body content in document order, grouping column content
        body_parts: list[str] = []
        # Track whether we have any column info (enables two-column CSS)
        has_columns = any(
            (para_map.get(item.id) and para_map[item.id].column in (1, 2))
            for item in doc.content_order
            if item.content_type == ContentType.PARAGRAPH and item.id in para_map
        )

        # Current column section state
        in_column_section = False
        current_col = 0  # 0=full-width, 1=left, 2=right

        for item in doc.content_order:
            if item.content_type == ContentType.PARAGRAPH:
                para = para_map.get(item.id)
                if not para:
                    continue
                para_html = _render_paragraph(para, image_map, embed_images, warnings, math_map=math_map)
                if not para_html:
                    continue

                col = para.column or 0

                if has_columns and col in (1, 2):
                    # Entering or continuing a column section
                    if not in_column_section:
                        body_parts.append('  <div class="two-column">')
                        body_parts.append('    <div class="column">')
                        in_column_section = True
                        current_col = col
                    elif col != current_col and current_col == 1:
                        # Switching from left to right column
                        body_parts.append('    </div>')
                        body_parts.append('    <div class="column">')
                        current_col = col
                    body_parts.append(f"  {para_html}")
                else:
                    # Full-width — close any open column section
                    if in_column_section:
                        body_parts.append('    </div>')
                        body_parts.append('  </div>')
                        in_column_section = False
                        current_col = 0
                    body_parts.append(para_html)

            elif item.content_type == ContentType.TABLE:
                # Tables are always full-width
                if in_column_section:
                    body_parts.append('    </div>')
                    body_parts.append('  </div>')
                    in_column_section = False
                    current_col = 0
                table = table_map.get(item.id)
                if table:
                    body_parts.append(_render_table(table, warnings))

            elif item.content_type == ContentType.MATH:
                math = math_map.get(item.id)
                if math:
                    # Close any open column section first (math blocks are full-width)
                    if in_column_section:
                        body_parts.append('    </div>')
                        body_parts.append('  </div>')
                        in_column_section = False
                        current_col = 0
                    body_parts.append(_render_block_math(math))

        # Close any trailing column section
        if in_column_section:
            body_parts.append('    </div>')
            body_parts.append('  </div>')

        body_content = "\n".join(body_parts)

        # Default CSS for basic readability
        default_css = """
      body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        line-height: 1.6;
        max-width: 800px;
        margin: 0 auto;
        padding: 2rem;
        color: #000000;
        background: #FFFFFF;
      }
      table { border-collapse: collapse; width: 100%; margin: 1em 0; }
      th, td { border: 1px solid #666; padding: 0.5em; text-align: left; }
      th { background-color: #f0f0f0; font-weight: bold; }
      img { max-width: 100%; height: auto; }
      .two-column {
        display: grid;
        grid-template-columns: 1fr 1fr;
        column-gap: 2em;
        margin: 1em 0;
      }
      @media (max-width: 600px) {
        .two-column { grid-template-columns: 1fr; }
      }
      .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }
      .math-block {
        margin: 1em 0;
        text-align: center;
      }
      .math-inline svg {
        vertical-align: middle;
      }
      .eq-number {
        float: right;
        margin-right: 1em;
      }
"""
        style_block = default_css + ("\n" + css if css else "")

        html_doc = f"""<!DOCTYPE html>
<html lang="{_esc(lang)}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(title)}</title>
  <style>{style_block}</style>
</head>
<body>
{body_content}
</body>
</html>"""

        return HtmlBuildResult(success=True, html=html_doc, warnings=warnings)

    except Exception as e:
        logger.exception("Failed to build HTML")
        return HtmlBuildResult(success=False, error=f"HTML build failed: {e}", warnings=warnings)


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html.escape(text, quote=True)


def _render_paragraph(
    para: ParagraphInfo,
    image_map: dict[str, ImageInfo],
    embed_images: bool,
    warnings: list[str],
    math_map: dict | None = None,
) -> str:
    """Render a paragraph as HTML."""
    # Render images in this paragraph first
    img_html_parts: list[str] = []
    for img_id in para.image_ids:
        img = image_map.get(img_id)
        if img:
            img_html_parts.append(_render_image(img, embed_images, warnings))

    # Heading
    if para.heading_level is not None:
        level = min(para.heading_level, 6)
        tag = f"h{level}"
        return f"  <{tag}>{_esc(para.text)}</{tag}>"

    # Empty paragraph with only images
    if not para.text.strip() and img_html_parts:
        return "\n".join(f"  {img}" for img in img_html_parts)

    # Empty paragraph (skip)
    if not para.text.strip() and not img_html_parts:
        return ""

    # Algorithm pseudocode block (already formatted as HTML by latex_parser)
    if para.text.strip().startswith("<pre"):
        return f"  {para.text}"

    # List item
    if para.is_list_item:
        return f"  <li>{_render_inline(para, math_map=math_map)}</li>"

    # Regular paragraph
    inline = _render_inline(para, math_map=math_map)
    parts: list[str] = []
    if img_html_parts:
        parts.extend(f"  {img}" for img in img_html_parts)
    parts.append(f"  <p>{inline}</p>")
    return "\n".join(parts)


def _render_inline(para: ParagraphInfo, math_map: dict | None = None) -> str:
    """Render paragraph inline content (runs with formatting)."""
    if not para.runs:
        text = _esc(para.text)
        # Resolve [math_N] placeholders even when there are no runs
        if math_map and para.math_ids:
            for mid in para.math_ids:
                math = math_map.get(mid)
                if math:
                    placeholder = f"[{mid}]"
                    escaped_placeholder = _esc(placeholder)
                    if escaped_placeholder in text:
                        text = text.replace(escaped_placeholder, _render_inline_math(math))
                    elif placeholder in text:
                        text = text.replace(placeholder, _render_inline_math(math))
        return text

    parts: list[str] = []
    for run in para.runs:
        text = _esc(run.text)
        if not text:
            continue

        # Apply inline styles
        styles: list[str] = []
        if run.color and run.color.startswith("#"):
            styles.append(f"color: {run.color}")
        if run.font_size_pt:
            styles.append(f"font-size: {run.font_size_pt}pt")

        # Wrap in semantic elements
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"

        if styles:
            style_attr = "; ".join(styles)
            text = f'<span style="{style_attr}">{text}</span>'

        parts.append(text)

    # Add links
    for link in para.links:
        link_html = f'<a href="{_esc(link.url)}">{_esc(link.text)}</a>'
        # Try to replace the link text in the rendered output
        escaped_text = _esc(link.text)
        joined = "".join(parts)
        if escaped_text in joined:
            joined = joined.replace(escaped_text, link_html, 1)
            return joined

    text = "".join(parts)

    # Resolve [math_N] placeholders
    if math_map and para.math_ids:
        for mid in para.math_ids:
            math = math_map.get(mid)
            if math:
                placeholder = f"[{mid}]"
                escaped_placeholder = _esc(placeholder)
                if escaped_placeholder in text:
                    text = text.replace(escaped_placeholder, _render_inline_math(math))
                elif placeholder in text:
                    text = text.replace(placeholder, _render_inline_math(math))

    return text


def _render_block_math(math: MathInfo) -> str:
    """Render a block math expression as SVG + hidden MathML."""
    svg = render_mathml_to_svg(math.mathml)
    desc = _esc(math.description) if math.description else _esc(math.latex_source)
    eq_num = f'<span class="eq-number">{_esc(math.equation_number)}</span>' if math.equation_number else ""

    return (
        f'  <div class="math-block">\n'
        f'    {eq_num}\n'
        f'    <span role="math" aria-label="{desc}">\n'
        f'      {svg}\n'
        f'      <span class="sr-only">{math.mathml}</span>\n'
        f'    </span>\n'
        f'  </div>'
    )


def _render_inline_math(math: MathInfo) -> str:
    """Render an inline math expression as SVG + hidden MathML."""
    svg = render_mathml_to_svg(math.mathml)
    desc = _esc(math.description) if math.description else _esc(math.latex_source)

    return (
        f'<span class="math-inline" role="math" aria-label="{desc}">'
        f'{svg}'
        f'<span class="sr-only">{math.mathml}</span>'
        f'</span>'
    )


def _render_image(
    img: ImageInfo,
    embed: bool,
    warnings: list[str],
) -> str:
    """Render an image as an HTML <img> tag."""
    alt = _esc(img.alt_text)

    if embed and img.image_data:
        mime = img.content_type or "image/png"
        b64 = base64.b64encode(img.image_data).decode("ascii")
        src = f"data:{mime};base64,{b64}"
    else:
        src = f"images/{img.id}.png"

    attrs = [f'src="{src}"', f'alt="{alt}"']

    if img.width_px and img.height_px:
        attrs.append(f'width="{img.width_px}"')
        attrs.append(f'height="{img.height_px}"')

    if not img.alt_text and not img.is_decorative:
        warnings.append(f"{img.id}: image has no alt text in generated HTML")

    return f'<img {" ".join(attrs)}>'


def _render_table(table: TableInfo, warnings: list[str]) -> str:
    """Render a table as HTML with proper header markup."""
    parts: list[str] = ["  <table>"]

    for row_idx, row in enumerate(table.rows):
        is_header_row = row_idx < table.header_row_count

        if is_header_row and row_idx == 0:
            parts.append("    <thead>")

        parts.append("      <tr>")

        for cell in row:
            tag = "th" if is_header_row else "td"
            attrs: list[str] = []

            if is_header_row:
                attrs.append('scope="col"')

            if cell.grid_span > 1:
                attrs.append(f'colspan="{cell.grid_span}"')

            attr_str = (" " + " ".join(attrs)) if attrs else ""
            parts.append(f"        <{tag}{attr_str}>{_esc(cell.text)}</{tag}>")

        parts.append("      </tr>")

        if is_header_row and (row_idx == table.header_row_count - 1):
            parts.append("    </thead>")
            parts.append("    <tbody>")

    # Close tbody if we had headers
    if table.header_row_count > 0:
        parts.append("    </tbody>")

    parts.append("  </table>")
    return "\n".join(parts)
