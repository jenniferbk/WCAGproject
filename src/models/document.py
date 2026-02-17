"""Document data models for the a11y remediation pipeline.

All models use Pydantic v2 with frozen=True for immutability.
The DocumentModel is format-agnostic — it represents both .docx and .pdf content.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """Type of content element in document order."""
    PARAGRAPH = "paragraph"
    TABLE = "table"


class ContentOrderItem(BaseModel, frozen=True):
    """An item in the document's content order sequence."""
    content_type: ContentType
    id: str


class RunInfo(BaseModel, frozen=True):
    """A text run within a paragraph. Preserves None = inherited from style."""
    text: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_size_pt: float | None = None
    font_name: str | None = None
    color: str | None = None  # hex like "#FF0000" or None


class LinkInfo(BaseModel, frozen=True):
    """A hyperlink within the document."""
    id: str  # link_0, link_1, ...
    text: str
    url: str
    paragraph_id: str


class ImageInfo(BaseModel, frozen=True):
    """An image extracted from the document."""
    id: str  # img_0, img_1, ...
    image_data: bytes | None = Field(default=None, exclude=True)
    content_type: str = ""  # MIME type like "image/png"
    alt_text: str = ""
    width_px: int | None = None
    height_px: int | None = None
    surrounding_text: str = ""  # ~100 chars before/after for context
    relationship_id: str = ""  # rId from docx relationships
    paragraph_id: str = ""  # which paragraph contains this image
    is_decorative: bool = False


class CellInfo(BaseModel, frozen=True):
    """A single cell in a table."""
    text: str
    paragraphs: list[str] = Field(default_factory=list)  # paragraph texts
    grid_span: int = 1  # horizontal merge span
    v_merge: str | None = None  # "restart" = start of merge, "continue" = merged


class TableInfo(BaseModel, frozen=True):
    """A table in the document."""
    id: str  # tbl_0, tbl_1, ...
    rows: list[list[CellInfo]] = Field(default_factory=list)
    header_row_count: int = 0
    has_header_style: bool = False
    style_name: str = ""
    row_count: int = 0
    col_count: int = 0


class FakeHeadingSignals(BaseModel, frozen=True):
    """Heuristic signals for fake heading detection.

    The parser populates these signals; the agent decides whether
    the paragraph is actually a heading.
    """
    all_runs_bold: bool = False
    font_size_pt: float | None = None
    font_size_above_avg: bool = False
    is_short: bool = False  # < ~10 words
    followed_by_non_bold: bool = False
    not_in_table: bool = True
    score: float = 0.0  # weighted 0-1 composite


class ParagraphInfo(BaseModel, frozen=True):
    """A paragraph in the document."""
    id: str  # p_0, p_1, ...
    text: str
    style_name: str = "Normal"
    heading_level: int | None = None  # 1-9 if a heading style, else None
    runs: list[RunInfo] = Field(default_factory=list)
    links: list[LinkInfo] = Field(default_factory=list)
    image_ids: list[str] = Field(default_factory=list)
    alignment: str | None = None  # left, center, right, justify
    is_list_item: bool = False
    list_level: int | None = None
    fake_heading_signals: FakeHeadingSignals | None = None


class MetadataInfo(BaseModel, frozen=True):
    """Document metadata."""
    title: str = ""
    author: str = ""
    language: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""


class ContrastIssue(BaseModel, frozen=True):
    """A color contrast failure."""
    paragraph_id: str
    run_index: int
    text_preview: str  # first ~50 chars of the run
    foreground: str  # hex color
    background: str  # hex color
    contrast_ratio: float
    required_ratio: float
    is_large_text: bool
    font_size_pt: float | None = None
    is_bold: bool = False


class DocumentStats(BaseModel, frozen=True):
    """Summary statistics about the parsed document."""
    paragraph_count: int = 0
    table_count: int = 0
    image_count: int = 0
    link_count: int = 0
    heading_count: int = 0
    images_missing_alt: int = 0
    fake_heading_candidates: int = 0


class DocumentModel(BaseModel, frozen=True):
    """Top-level document model. Format-agnostic representation of document content."""
    source_format: str = ""  # "docx" or "pdf"
    source_path: str = ""
    metadata: MetadataInfo = Field(default_factory=MetadataInfo)
    paragraphs: list[ParagraphInfo] = Field(default_factory=list)
    tables: list[TableInfo] = Field(default_factory=list)
    images: list[ImageInfo] = Field(default_factory=list)
    links: list[LinkInfo] = Field(default_factory=list)
    content_order: list[ContentOrderItem] = Field(default_factory=list)
    contrast_issues: list[ContrastIssue] = Field(default_factory=list)
    stats: DocumentStats = Field(default_factory=DocumentStats)
    parse_warnings: list[str] = Field(default_factory=list)
