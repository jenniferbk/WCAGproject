# Data Schema Reference

Canonical reference for all data models in the a11y remediation pipeline. All models use **Pydantic v2** with `frozen=True`. When adding or modifying models, **update this document**.

Source files:
- `src/models/document.py` — document content models (format-agnostic)
- `src/models/pipeline.py` — pipeline flow models (request → result)
- `src/tools/validator.py` — validation result models (dataclasses)
- `src/tools/contrast.py` — contrast result models (dataclasses)

## Model Hierarchy

```
RemediationRequest              # What comes in
├── document_path
├── CourseContext                # Faculty-provided course info
│   ├── course_name
│   ├── department
│   └── description
├── submitter_email
└── output_format

DocumentModel                   # Parsed document content
├── MetadataInfo
├── ParagraphInfo[]
│   ├── RunInfo[]               # Text runs with formatting
│   ├── LinkInfo[]
│   ├── FakeHeadingSignals?
│   └── image_ids[]
├── TableInfo[]
│   └── CellInfo[][]
├── ImageInfo[]                 # image_data excluded from JSON
├── LinkInfo[]
├── ContentOrderItem[]          # Document reading order
├── ContrastIssue[]
└── DocumentStats

ComprehensionResult             # Phase 1 output
├── document_type               # syllabus, lecture_notes, exam, etc.
├── document_summary
├── ElementPurpose[]            # Per-element purpose judgments
└── validation_summary          # Pre-remediation validator results

RemediationStrategy             # Phase 2 output
├── RemediationAction[]         # Planned actions with rationale
├── items_for_human_review[]
└── strategy_summary

RemediationResult               # Final output
├── ComprehensionResult
├── RemediationStrategy
├── ReviewFinding[]             # Phase 4 output
├── issues_before / issues_after / issues_fixed
└── items_for_human_review[]
```

## Document Content Models (`src/models/document.py`)

These represent what's IN a document. Format-agnostic — same models for .docx, .pdf, and future .pptx.

### RunInfo
A text run within a paragraph. `None` = inherited from style.

| Field | Type | Notes |
|-------|------|-------|
| `text` | `str` | The run's text content |
| `bold` | `bool \| None` | `None` = inherit from style |
| `italic` | `bool \| None` | |
| `underline` | `bool \| None` | |
| `font_size_pt` | `float \| None` | Resolved through inheritance chain |
| `font_name` | `str \| None` | |
| `color` | `str \| None` | Hex like `#FF0000`, or `None` for default/theme |

### LinkInfo
A hyperlink.

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `link_0`, `link_1`, ... |
| `text` | `str` | Display text |
| `url` | `str` | Target URL |
| `paragraph_id` | `str` | Which paragraph contains this link |

### ImageInfo
An image extracted from the document.

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `img_0`, `img_1`, ... |
| `image_data` | `bytes \| None` | **Excluded from JSON** (`Field(exclude=True)`) |
| `content_type` | `str` | MIME type, e.g. `image/png` |
| `alt_text` | `str` | Current alt text (empty = missing) |
| `width_px` | `int \| None` | From Pillow |
| `height_px` | `int \| None` | |
| `surrounding_text` | `str` | ~100 chars before/after for LLM context |
| `relationship_id` | `str` | rId from docx relationships |
| `paragraph_id` | `str` | Which paragraph contains this image |
| `is_decorative` | `bool` | Decorative images get empty alt text |

### CellInfo
A single table cell.

| Field | Type | Notes |
|-------|------|-------|
| `text` | `str` | Full cell text |
| `paragraphs` | `list[str]` | Text of each paragraph in the cell |
| `grid_span` | `int` | Horizontal merge span (1 = no merge) |
| `v_merge` | `str \| None` | `"restart"` = start, `"continue"` = merged, `None` = no merge |

### TableInfo

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `tbl_0`, `tbl_1`, ... |
| `rows` | `list[list[CellInfo]]` | 2D grid of cells |
| `header_row_count` | `int` | Rows marked as headers in XML |
| `has_header_style` | `bool` | Style name contains "header" |
| `style_name` | `str` | |
| `row_count` | `int` | |
| `col_count` | `int` | |

### FakeHeadingSignals
Heuristic signals populated by the parser. The **agent decides** whether these are actually headings.

| Field | Type | Notes |
|-------|------|-------|
| `all_runs_bold` | `bool` | Every text run is bold |
| `font_size_pt` | `float \| None` | Max font size across runs |
| `font_size_above_avg` | `bool` | >= median + 2pt |
| `is_short` | `bool` | < ~10 words |
| `followed_by_non_bold` | `bool` | Next non-empty paragraph isn't all-bold |
| `not_in_table` | `bool` | Always True for paragraph-level items |
| `score` | `float` | Weighted composite 0-1. Weights: bold=0.3, font=0.25, short=0.2, followed_by=0.15, not_in_table=0.1 |

### ParagraphInfo

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | `p_0`, `p_1`, ... |
| `text` | `str` | Full paragraph text |
| `style_name` | `str` | Word style name, default `"Normal"` |
| `heading_level` | `int \| None` | 1-9 if heading style, else `None` |
| `runs` | `list[RunInfo]` | |
| `links` | `list[LinkInfo]` | |
| `image_ids` | `list[str]` | IDs of images in this paragraph |
| `alignment` | `str \| None` | `left`, `center`, `right`, `justify` |
| `is_list_item` | `bool` | Has `w:numPr` in XML |
| `list_level` | `int \| None` | 0-based indentation level |
| `fake_heading_signals` | `FakeHeadingSignals \| None` | Only set on candidates |

### MetadataInfo

| Field | Type | Notes |
|-------|------|-------|
| `title` | `str` | WCAG 2.4.2 |
| `author` | `str` | |
| `language` | `str` | BCP 47 tag, WCAG 3.1.1 |
| `subject` | `str` | |
| `created` | `str` | ISO datetime string |
| `modified` | `str` | |

### ContrastIssue

| Field | Type | Notes |
|-------|------|-------|
| `paragraph_id` | `str` | |
| `run_index` | `int` | |
| `text_preview` | `str` | First ~50 chars |
| `foreground` | `str` | Hex color |
| `background` | `str` | Hex color |
| `contrast_ratio` | `float` | Actual ratio |
| `required_ratio` | `float` | 4.5 normal, 3.0 large |
| `is_large_text` | `bool` | >=18pt or >=14pt bold |
| `font_size_pt` | `float \| None` | |
| `is_bold` | `bool` | |

### DocumentStats

| Field | Type | Notes |
|-------|------|-------|
| `paragraph_count` | `int` | |
| `table_count` | `int` | |
| `image_count` | `int` | |
| `link_count` | `int` | |
| `heading_count` | `int` | |
| `images_missing_alt` | `int` | |
| `fake_heading_candidates` | `int` | Score >= 0.5 |

### DocumentModel
Top-level container. Format-agnostic.

| Field | Type | Notes |
|-------|------|-------|
| `source_format` | `str` | `"docx"`, `"pdf"`, `"pptx"` |
| `source_path` | `str` | |
| `metadata` | `MetadataInfo` | |
| `paragraphs` | `list[ParagraphInfo]` | |
| `tables` | `list[TableInfo]` | |
| `images` | `list[ImageInfo]` | |
| `links` | `list[LinkInfo]` | |
| `content_order` | `list[ContentOrderItem]` | Reading order |
| `contrast_issues` | `list[ContrastIssue]` | |
| `stats` | `DocumentStats` | |
| `parse_warnings` | `list[str]` | |

### ContentOrderItem

| Field | Type | Notes |
|-------|------|-------|
| `content_type` | `ContentType` | `"paragraph"` or `"table"` |
| `id` | `str` | References paragraph or table ID |

## Pipeline Flow Models (`src/models/pipeline.py`)

These represent the submission and flow of data through the four pipeline phases.

### CourseContext
Faculty-provided context about the course. Shapes comprehension and strategy.

| Field | Type | Notes |
|-------|------|-------|
| `course_name` | `str` | e.g. `"MATH 201: Calculus II"` |
| `department` | `str` | e.g. `"Mathematics"` |
| `description` | `str` | Any additional context |

### RemediationRequest
Input to the pipeline. Created from email parsing or CLI.

| Field | Type | Notes |
|-------|------|-------|
| `document_path` | `str` | Path to input document |
| `course_context` | `CourseContext` | |
| `submitter_email` | `str` | For sending results back |
| `submitted_at` | `str` | ISO 8601 |
| `output_dir` | `str` | Where to write results |
| `output_format` | `str` | `"same"`, `"pdf"`, `"both"` |

### ElementPurpose
Comprehension phase's judgment about a single document element.

| Field | Type | Notes |
|-------|------|-------|
| `element_id` | `str` | `p_0`, `img_0`, `tbl_0`, etc. |
| `purpose` | `str` | Free-text description |
| `is_decorative` | `bool` | For images |
| `suggested_action` | `str` | e.g. `"add_alt_text"`, `"convert_to_heading"` |
| `confidence` | `float` | 0-1 |

### ComprehensionResult
Output of Phase 1. Combines Gemini's analysis with validator results.

| Field | Type | Notes |
|-------|------|-------|
| `document_type` | `DocumentType` | Enum: syllabus, lecture_notes, exam, etc. |
| `document_summary` | `str` | 1-3 sentences |
| `audience` | `str` | e.g. `"undergraduate students"` |
| `element_purposes` | `list[ElementPurpose]` | Per-element analysis |
| `validation_summary` | `str` | Pre-remediation validator summary |
| `validation_issues_count` | `int` | |
| `raw_validation_report` | `str` | Full validator output |

### RemediationAction
A single planned or executed fix.

| Field | Type | Notes |
|-------|------|-------|
| `element_id` | `str` | What element this acts on |
| `action_type` | `str` | e.g. `"set_alt_text"`, `"set_heading_level"` |
| `parameters` | `dict` | Tool-specific params |
| `rationale` | `str` | Why this action was chosen |
| `status` | `str` | `"planned"`, `"executed"`, `"failed"`, `"skipped"` |
| `result_detail` | `str` | What happened when executed |

### RemediationStrategy
Output of Phase 2. Claude's plan.

| Field | Type | Notes |
|-------|------|-------|
| `actions` | `list[RemediationAction]` | Ordered list of fixes |
| `items_for_human_review` | `list[str]` | Things the agent can't fix |
| `strategy_summary` | `str` | High-level approach description |

### ReviewFinding
A single finding from Phase 4.

| Field | Type | Notes |
|-------|------|-------|
| `element_id` | `str` | |
| `finding_type` | `str` | `"pass"`, `"concern"`, `"failure"`, `"needs_human_review"` |
| `detail` | `str` | |
| `criterion` | `str` | WCAG criterion, e.g. `"1.1.1"` |

### RemediationResult
Final pipeline output.

| Field | Type | Notes |
|-------|------|-------|
| `success` | `bool` | |
| `input_path` | `str` | |
| `output_path` | `str` | Remediated document |
| `report_path` | `str` | Compliance report |
| `comprehension` | `ComprehensionResult` | Phase 1 artifact |
| `strategy` | `RemediationStrategy` | Phase 2 artifact |
| `review_findings` | `list[ReviewFinding]` | Phase 4 findings |
| `pre_validation_summary` | `str` | Before remediation |
| `post_validation_summary` | `str` | After remediation |
| `issues_before` | `int` | |
| `issues_after` | `int` | |
| `issues_fixed` | `int` | |
| `items_for_human_review` | `list[str]` | Combined from strategy + review |
| `error` | `str` | If pipeline failed |
| `processing_time_seconds` | `float` | |

## Tool Result Models

These are `@dataclass` (not Pydantic) since they're internal to tool execution, not serialized to LLM prompts.

### From `src/tools/validator.py`
- **`CheckResult`**: Single WCAG criterion check (criterion, name, status, issues)
- **`ValidationReport`**: Full audit (list of CheckResult, pass/fail/warn counts, summary)

### From `src/tools/contrast.py`
- **`ContrastResult`**: Single contrast check (ratio, passes, required_ratio)
- **`ContrastFixResult`**: Result of fixing a color (original/fixed colors, strategy used)

### From other tools
Each tool has its own `*Result` dataclass (e.g. `AltTextResult`, `HeadingResult`, `TableResult`, `ListResult`, `MetadataResult`). All follow the pattern: `success: bool`, `changes: list[str]`, `error: str`.

## ID Scheme

All IDs are sequential per-parse, stable within a single parse run:

| Prefix | Model | Example |
|--------|-------|---------|
| `p_` | ParagraphInfo | `p_0`, `p_1`, `p_42` |
| `img_` | ImageInfo | `img_0`, `img_1` |
| `tbl_` | TableInfo | `tbl_0`, `tbl_1` |
| `link_` | LinkInfo | `link_0`, `link_1` |

## Serialization Notes

- All Pydantic models support `.model_dump()` and `.model_dump_json()`
- `ImageInfo.image_data` is **excluded** from serialization (`Field(exclude=True)`) to avoid dumping megabytes into LLM prompts
- `None` values on `RunInfo` formatting fields mean "inherited from style" — preserve them, don't default to `False`
- `CourseContext` may have all-empty fields if no course info was provided — tools should handle gracefully
