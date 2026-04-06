# Hybrid OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Gemini-only OCR with a three-model hybrid (Tesseract text + Gemini structure + Haiku text correction) to eliminate RECITATION failures on scanned pages.

**Architecture:** Tesseract extracts raw text blocks with bounding boxes. Gemini 2.5 Flash classifies structure (headings, tables, figures, reading order). Claude Haiku 4.5 corrects Tesseract OCR errors by comparing against the page image. Gemini and Haiku calls run in parallel. Graceful fallback at every level — worst case equals current Tesseract-only output.

**Tech Stack:** pytesseract, google-genai (Gemini 2.5 Flash), anthropic (Claude Haiku 4.5), PyMuPDF (fitz), Pillow

**Spec:** `docs/superpowers/specs/2026-04-05-hybrid-ocr-design.md`

**Test document:** `testdocs/7) Mayer Learners as information processors-Legacies and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional.pdf` (fully scanned, 11 pages, two-column academic paper with tables and figures)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/tools/scanned_page_ocr.py` | Modify | Add `_tesseract_extract_blocks()`, `_gemini_classify_structure()`, `_haiku_correct_text()`, `_apply_corrections()`, `_merge_blocks_and_structure()`, `_heuristic_classify_blocks()`. Rewrite `_process_single_page()`. Remove old Gemini-OCR functions. |
| `src/prompts/hybrid_ocr_structure.md` | Create | Gemini structure-classification prompt |
| `src/prompts/hybrid_ocr_correction.md` | Create | Haiku text-correction prompt |
| `tests/test_scanned_ocr.py` | Modify | Add tests for new functions, update imports, remove tests for deleted functions |

---

### Task 1: `_tesseract_extract_blocks()` — Extract raw text blocks

Refactor the Tesseract block-extraction logic from `_tesseract_fallback()` (lines 1424-1507 of `scanned_page_ocr.py`) into a standalone function that returns raw block dicts instead of `ParagraphInfo` objects. This becomes the foundation that Gemini and Haiku both consume.

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scanned_ocr.py`:

```python
class TestTesseractExtractBlocks:
    """Tests for _tesseract_extract_blocks() — raw block extraction."""

    @patch("src.tools.scanned_page_ocr.pytesseract")
    def test_returns_blocks_with_id_text_bbox(self, mock_tess):
        """Each block has id, text, and bbox fields."""
        mock_tess.Output.DICT = "dict"
        mock_tess.image_to_data.return_value = {
            "text": ["Hello", "world", "", "Second", "block"],
            "conf": ["95", "90", "-1", "88", "92"],
            "block_num": [1, 1, 2, 3, 3],
            "left": [100, 160, 0, 100, 160],
            "top": [50, 50, 0, 200, 200],
            "width": [50, 50, 0, 50, 50],
            "height": [12, 12, 0, 12, 12],
        }

        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        pix = MagicMock()
        pix.width = 800
        pix.height = 1200
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        from src.tools.scanned_page_ocr import _tesseract_extract_blocks
        blocks = _tesseract_extract_blocks(doc, page_number=0, dpi=300)

        assert len(blocks) == 2
        assert blocks[0]["id"] == 0
        assert blocks[0]["text"] == "Hello world"
        assert "bbox" in blocks[0]
        assert len(blocks[0]["bbox"]) == 4
        assert blocks[1]["id"] == 1
        assert blocks[1]["text"] == "Second block"

    @patch("src.tools.scanned_page_ocr.pytesseract")
    def test_filters_low_confidence_words(self, mock_tess):
        """Words with confidence < 20 are excluded."""
        mock_tess.Output.DICT = "dict"
        mock_tess.image_to_data.return_value = {
            "text": ["Good", "bad"],
            "conf": ["90", "10"],
            "block_num": [1, 1],
            "left": [100, 160],
            "top": [50, 50],
            "width": [50, 50],
            "height": [12, 12],
        }

        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        pix = MagicMock()
        pix.width = 800
        pix.height = 1200
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        from src.tools.scanned_page_ocr import _tesseract_extract_blocks
        blocks = _tesseract_extract_blocks(doc, page_number=0, dpi=300)

        assert len(blocks) == 1
        assert blocks[0]["text"] == "Good"

    @patch("src.tools.scanned_page_ocr.pytesseract")
    def test_filters_short_fragments(self, mock_tess):
        """Blocks with text < 3 chars are excluded."""
        mock_tess.Output.DICT = "dict"
        mock_tess.image_to_data.return_value = {
            "text": ["Hi", "", "Proper block text here"],
            "conf": ["90", "-1", "88"],
            "block_num": [1, 2, 3],
            "left": [100, 0, 100],
            "top": [50, 0, 200],
            "width": [20, 0, 200],
            "height": [12, 0, 12],
        }

        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        pix = MagicMock()
        pix.width = 800
        pix.height = 1200
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        from src.tools.scanned_page_ocr import _tesseract_extract_blocks
        blocks = _tesseract_extract_blocks(doc, page_number=0, dpi=300)

        assert len(blocks) == 1
        assert "Proper" in blocks[0]["text"]

    @patch("src.tools.scanned_page_ocr.pytesseract")
    def test_filters_leaked_headers_footers(self, mock_tess):
        """Page headers/footers are excluded from blocks."""
        mock_tess.Output.DICT = "dict"
        mock_tess.image_to_data.return_value = {
            "text": ["LEARNERS", "AS", "INFORMATION", "PROCESSORS", "157"],
            "conf": ["90", "90", "90", "90", "90"],
            "block_num": [1, 1, 1, 1, 1],
            "left": [100, 200, 300, 450, 700],
            "top": [10, 10, 10, 10, 10],
            "width": [80, 30, 120, 100, 30],
            "height": [12, 12, 12, 12, 12],
        }

        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        pix = MagicMock()
        pix.width = 800
        pix.height = 1200
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        from src.tools.scanned_page_ocr import _tesseract_extract_blocks
        blocks = _tesseract_extract_blocks(doc, page_number=0, dpi=300)

        # "LEARNERS AS INFORMATION PROCESSORS 157" is a leaked header
        assert len(blocks) == 0

    @patch("src.tools.scanned_page_ocr.pytesseract")
    def test_empty_page_returns_empty_list(self, mock_tess):
        """Pages with no OCR output return empty list."""
        mock_tess.Output.DICT = "dict"
        mock_tess.image_to_data.return_value = {
            "text": ["", ""],
            "conf": ["-1", "-1"],
            "block_num": [0, 0],
            "left": [0, 0],
            "top": [0, 0],
            "width": [0, 0],
            "height": [0, 0],
        }

        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        pix = MagicMock()
        pix.width = 800
        pix.height = 1200
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix

        from src.tools.scanned_page_ocr import _tesseract_extract_blocks
        blocks = _tesseract_extract_blocks(doc, page_number=0, dpi=300)

        assert blocks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestTesseractExtractBlocks -v`
Expected: FAIL with `ImportError` — `_tesseract_extract_blocks` doesn't exist yet.

- [ ] **Step 3: Implement `_tesseract_extract_blocks()`**

Add to `src/tools/scanned_page_ocr.py`, after the existing `_is_leaked_header_footer()` function (around line 715):

```python
def _tesseract_extract_blocks(
    doc: fitz.Document,
    page_number: int,
    dpi: int = PAGE_DPI,
) -> list[dict]:
    """Extract raw text blocks from a scanned page using Tesseract.

    Returns list of {"id": int, "text": str, "bbox": [x, y, w, h]} dicts.
    Filters out low-confidence words, short fragments, and leaked headers/footers.
    """
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        logger.warning("pytesseract or Pillow not installed")
        return []

    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))

        data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)

        if not data or not data.get("text"):
            return []

        # Group words into blocks
        raw_blocks: dict[int, dict] = {}

        for i in range(len(data["text"])):
            text = data["text"][i].strip()
            conf = int(data["conf"][i]) if data["conf"][i] != "-1" else 0
            if not text or conf < 20:
                continue

            block_num = data["block_num"][i]
            if block_num not in raw_blocks:
                raw_blocks[block_num] = {
                    "words": [],
                    "left": data["left"][i],
                    "top": data["top"][i],
                    "right": data["left"][i] + data["width"][i],
                    "bottom": data["top"][i] + data["height"][i],
                }
            block = raw_blocks[block_num]
            block["words"].append(text)
            block["left"] = min(block["left"], data["left"][i])
            block["top"] = min(block["top"], data["top"][i])
            block["right"] = max(block["right"], data["left"][i] + data["width"][i])
            block["bottom"] = max(block["bottom"], data["top"][i] + data["height"][i])

        if not raw_blocks:
            return []

        # Build output blocks, filtering short fragments and headers/footers
        blocks: list[dict] = []
        block_id = 0

        for block_num in sorted(raw_blocks.keys()):
            raw = raw_blocks[block_num]
            text = " ".join(raw["words"])

            if len(text) < 3:
                continue

            if _is_leaked_header_footer(text):
                continue

            blocks.append({
                "id": block_id,
                "text": text,
                "bbox": [raw["left"], raw["top"],
                         raw["right"] - raw["left"],
                         raw["bottom"] - raw["top"]],
            })
            block_id += 1

        return blocks

    except Exception as e:
        logger.warning("Tesseract block extraction failed for page %d: %s", page_number + 1, e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestTesseractExtractBlocks -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "feat: add _tesseract_extract_blocks() for hybrid OCR pipeline"
```

---

### Task 2: Gemini structure-classification prompt and `_gemini_classify_structure()`

Create the Gemini prompt that receives Tesseract blocks + page image and returns structure annotations only (no text reproduction). Then implement the function that calls Gemini with this prompt.

**Files:**
- Create: `src/prompts/hybrid_ocr_structure.md`
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Create the Gemini structure prompt**

Write to `src/prompts/hybrid_ocr_structure.md`:

```markdown
# Document Structure Analyzer — Accessibility Remediation

You are a document structure analyzer for ADA/Section 508 accessibility remediation. A separate OCR engine has already extracted text blocks from this scanned page. Your job is to classify their structural roles so the content can be rendered as accessible HTML for screen readers.

## Input

You receive:
1. A scanned page image
2. A JSON array of OCR text blocks, each with `id`, `text`, and `bbox` [x, y, width, height]

## Your Task

Classify each block and determine the document's reading order. Return a JSON object with a `regions` array.

### For text regions (heading, paragraph, caption, footnote, equation):

- `block_ids`: array of block IDs that belong to this region. You may merge multiple blocks into one region if they are part of the same logical element (e.g., a paragraph split across two blocks). Every text block ID must appear in exactly one region.
- `type`: one of `heading`, `paragraph`, `caption`, `footnote`, `equation`, `page_header`, `page_footer`
- `reading_order`: sequential integer for screen reader order (1, 2, 3, ...)
- `column`: 0 (full-width), 1 (left column), 2 (right column)
- `heading_level`: 1-6 if type is `heading` (based on visual hierarchy — largest = 1)
- `bold`: true/false based on visual appearance in the image
- `italic`: true/false based on visual appearance in the image
- `font_size_relative`: `large`, `normal`, or `small`

### For TABLE regions:

Tables appear as gridlines, aligned columns, or content under "Table N" captions.
- `block_ids`: [] (empty — you provide the content directly)
- `type`: `table`
- `table_data`: {"headers": ["col1", "col2"], "rows": [["cell1", "cell2"]]}
- Extract headers and cell values by reading the table from the image

### For FIGURE regions:

Charts, diagrams, photos, or other visual content.
- `block_ids`: [] (empty — you provide the description)
- `type`: `figure`
- `figure_description`: thorough description for alt text (content, data, labels, meaning)

### For page_header / page_footer:

Running headers, footers, page numbers — mark for exclusion from accessible output.
- `block_ids`: array of block IDs in the header/footer
- `type`: `page_header` or `page_footer`

## Two-Column Layout

Academic documents often use two-column format:
- Set column=1 for left-column blocks, column=2 for right-column blocks
- Full-width elements (title, abstract, full-width tables/figures): column=0
- Reading order: left column top-to-bottom, then right column top-to-bottom
- Title/author/abstract at the top of an article are usually full-width (column=0)

## Important

- Every block ID must appear in exactly one region (no duplicates, no orphans)
- `page_header` and `page_footer` regions will be excluded from the accessible output
- Do NOT output any text content for non-table/figure regions — text comes from the OCR blocks

TESSERACT BLOCKS:
{blocks_json}
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_scanned_ocr.py`:

```python
class TestGeminiClassifyStructure:
    """Tests for _gemini_classify_structure()."""

    def _make_mock_doc(self):
        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        doc.__len__ = MagicMock(return_value=5)
        pix = MagicMock()
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix
        return doc

    def test_returns_structure_on_success(self):
        """Returns parsed regions dict when Gemini succeeds."""
        expected = {
            "regions": [
                {"block_ids": [0], "type": "heading", "heading_level": 1,
                 "column": 0, "reading_order": 1, "bold": True, "italic": False,
                 "font_size_relative": "large"},
                {"block_ids": [1], "type": "paragraph",
                 "column": 1, "reading_order": 2, "bold": False, "italic": False,
                 "font_size_relative": "normal"},
            ]
        }

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps(expected)
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=100, candidates_token_count=50,
        )
        mock_client.models.generate_content.return_value = mock_response

        blocks = [
            {"id": 0, "text": "INTRODUCTION", "bbox": [100, 50, 300, 20]},
            {"id": 1, "text": "This paper examines...", "bbox": [50, 100, 300, 200]},
        ]

        from src.tools.scanned_page_ocr import _gemini_classify_structure
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", self._make_mock_doc(), 0, blocks, 300,
        )

        assert result is not None
        assert len(result["regions"]) == 2
        assert result["regions"][0]["type"] == "heading"

    def test_returns_none_on_empty_response(self):
        """Returns None when Gemini returns empty (RECITATION)."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_response.candidates = [MagicMock(finish_reason="RECITATION")]
        mock_client.models.generate_content.return_value = mock_response

        blocks = [{"id": 0, "text": "Some text", "bbox": [0, 0, 100, 20]}]

        from src.tools.scanned_page_ocr import _gemini_classify_structure
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", self._make_mock_doc(), 0, blocks, 300,
        )

        assert result is None

    def test_returns_none_on_exception(self):
        """Returns None when Gemini throws an exception."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API error")

        blocks = [{"id": 0, "text": "Some text", "bbox": [0, 0, 100, 20]}]

        from src.tools.scanned_page_ocr import _gemini_classify_structure
        result = _gemini_classify_structure(
            mock_client, "gemini-2.5-flash", self._make_mock_doc(), 0, blocks, 300,
        )

        assert result is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestGeminiClassifyStructure -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `_gemini_classify_structure()`**

Add to `src/tools/scanned_page_ocr.py`:

```python
# New Gemini schema for structure-only classification
HYBRID_STRUCTURE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "regions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "block_ids": {
                        "type": "ARRAY",
                        "items": {"type": "INTEGER"},
                    },
                    "type": {
                        "type": "STRING",
                        "enum": [
                            "heading", "paragraph", "table", "figure",
                            "equation", "caption", "page_header",
                            "page_footer", "footnote",
                        ],
                    },
                    "heading_level": {"type": "INTEGER"},
                    "column": {"type": "INTEGER"},
                    "reading_order": {"type": "INTEGER"},
                    "bold": {"type": "BOOLEAN"},
                    "italic": {"type": "BOOLEAN"},
                    "font_size_relative": {
                        "type": "STRING",
                        "enum": ["large", "normal", "small"],
                    },
                    "table_data": {
                        "type": "OBJECT",
                        "properties": {
                            "headers": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                            },
                            "rows": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                            },
                        },
                    },
                    "figure_description": {"type": "STRING"},
                },
                "required": ["block_ids", "type", "reading_order"],
            },
        },
    },
    "required": ["regions"],
}


def _load_hybrid_structure_prompt() -> str:
    """Load the hybrid OCR structure-classification prompt."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "hybrid_ocr_structure.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Classify the structure of the OCR text blocks on this page. Return JSON."


def _gemini_classify_structure(
    client,
    model: str,
    doc: fitz.Document,
    page_number: int,
    blocks: list[dict],
    dpi: int = PAGE_DPI,
) -> dict | None:
    """Send page image + Tesseract blocks to Gemini for structure classification.

    Returns {"regions": [...]} dict on success, None on failure.
    Gemini never reproduces text — only classifies block roles and extracts
    table data / figure descriptions.
    """
    from google.genai import types

    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")

        prompt_template = _load_hybrid_structure_prompt()
        blocks_json = json.dumps(blocks, indent=2)
        prompt = prompt_template.replace("{blocks_json}", blocks_json)

        content_parts = [
            prompt,
            types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
        ]

        response = client.models.generate_content(
            model=model,
            contents=content_parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=HYBRID_STRUCTURE_SCHEMA,
                temperature=0.1,
            ),
        )

        resp_text = response.text
        if resp_text is None:
            finish_reason = ""
            try:
                if response.candidates:
                    finish_reason = str(response.candidates[0].finish_reason)
            except Exception:
                pass
            logger.warning(
                "Gemini structure classification returned empty for page %d (finish_reason=%s)",
                page_number + 1, finish_reason or "unknown",
            )
            return None

        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError:
            data = parse_json_lenient(resp_text)

        return data

    except Exception as e:
        logger.warning("Gemini structure classification failed for page %d: %s", page_number + 1, e)
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestGeminiClassifyStructure -v`
Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tools/scanned_page_ocr.py src/prompts/hybrid_ocr_structure.md tests/test_scanned_ocr.py
git commit -m "feat: add Gemini structure classifier and prompt for hybrid OCR"
```

---

### Task 3: Haiku text-correction prompt and `_haiku_correct_text()`

Create the Claude Haiku prompt for OCR text correction and implement the function. Haiku compares Tesseract text against the page image and returns corrections for blocks with errors.

**Files:**
- Create: `src/prompts/hybrid_ocr_correction.md`
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Create the Haiku correction prompt**

Write to `src/prompts/hybrid_ocr_correction.md`:

```markdown
# OCR Text Correction — Accessibility Remediation

You are an OCR text correction tool for ADA/Section 508 accessibility remediation. A separate OCR engine (Tesseract) has extracted text blocks from this scanned page. Tesseract sometimes makes errors:

- Wrong characters (e.g., "tbe" → "the", "rn" → "m", "cl" → "d")
- Missed ligatures (e.g., "fi" → "fi", "fl" → "fl")
- Broken words from line breaks
- Garbled symbols or special characters
- Missed or incorrect punctuation
- Accented characters in author names or citations

## Your Task

Compare each text block below against what you can see in the page image. Return corrections ONLY for blocks that contain errors. If a block's text is correct, omit it from your response.

Return JSON:
```json
{"corrections": [{"id": 0, "corrected_text": "the corrected text"}]}
```

If all blocks are correct, return:
```json
{"corrections": []}
```

## Important

- Only correct OCR errors — do not rephrase, reformat, or improve the text
- Preserve the original formatting (capitalization, punctuation, spacing)
- If a block is mostly correct but has one wrong character, return the full corrected text for that block
- For mathematical notation, use Unicode where possible (e.g., x², α, Σ)

TESSERACT BLOCKS:
{blocks_json}
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_scanned_ocr.py`:

```python
class TestHaikuCorrectText:
    """Tests for _haiku_correct_text()."""

    def _make_mock_doc(self):
        doc = MagicMock()
        page = MagicMock()
        doc.__getitem__ = MagicMock(return_value=page)
        doc.__len__ = MagicMock(return_value=5)
        pix = MagicMock()
        pix.tobytes.return_value = b"fake_png"
        page.get_pixmap.return_value = pix
        return doc

    @patch("src.tools.scanned_page_ocr.Anthropic")
    def test_returns_corrections_dict(self, mock_anthropic_cls):
        """Returns {block_id: corrected_text} for blocks with errors."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text='{"corrections": [{"id": 1, "corrected_text": "the learner"}]}',
            type="text",
        )]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = mock_response

        blocks = [
            {"id": 0, "text": "INTRODUCTION", "bbox": [100, 50, 300, 20]},
            {"id": 1, "text": "tbe learner", "bbox": [50, 100, 300, 20]},
        ]

        from src.tools.scanned_page_ocr import _haiku_correct_text
        corrections = _haiku_correct_text(blocks, self._make_mock_doc(), page_number=0, dpi=300)

        assert corrections == {1: "the learner"}

    @patch("src.tools.scanned_page_ocr.Anthropic")
    def test_returns_empty_dict_when_all_correct(self, mock_anthropic_cls):
        """Returns empty dict when no corrections needed."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text='{"corrections": []}',
            type="text",
        )]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=10)
        mock_client.messages.create.return_value = mock_response

        blocks = [{"id": 0, "text": "Perfect text", "bbox": [0, 0, 100, 20]}]

        from src.tools.scanned_page_ocr import _haiku_correct_text
        corrections = _haiku_correct_text(blocks, self._make_mock_doc(), page_number=0, dpi=300)

        assert corrections == {}

    def test_returns_empty_dict_when_no_api_key(self):
        """Returns empty dict when ANTHROPIC_API_KEY is not set."""
        blocks = [{"id": 0, "text": "Some text", "bbox": [0, 0, 100, 20]}]

        from src.tools.scanned_page_ocr import _haiku_correct_text
        with patch.dict("os.environ", {}, clear=True):
            corrections = _haiku_correct_text(blocks, self._make_mock_doc(), page_number=0, dpi=300)

        assert corrections == {}

    @patch("src.tools.scanned_page_ocr.Anthropic")
    def test_returns_empty_dict_on_exception(self, mock_anthropic_cls):
        """Returns empty dict when Haiku throws an exception."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        blocks = [{"id": 0, "text": "Some text", "bbox": [0, 0, 100, 20]}]

        from src.tools.scanned_page_ocr import _haiku_correct_text
        corrections = _haiku_correct_text(blocks, self._make_mock_doc(), page_number=0, dpi=300)

        assert corrections == {}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestHaikuCorrectText -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `_haiku_correct_text()`**

Add to `src/tools/scanned_page_ocr.py`. Add `import base64` at the top of the file if not already present, and add `from anthropic import Anthropic` as a lazy import inside the function:

```python
def _load_hybrid_correction_prompt() -> str:
    """Load the hybrid OCR text-correction prompt."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "hybrid_ocr_correction.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Compare these OCR text blocks against the page image. Return corrections for errors."


def _haiku_correct_text(
    blocks: list[dict],
    doc: fitz.Document,
    page_number: int,
    dpi: int = PAGE_DPI,
) -> dict[int, str]:
    """Send page image + Tesseract blocks to Claude Haiku for text correction.

    Returns {block_id: corrected_text} for blocks with OCR errors.
    Returns empty dict on failure (Tesseract text used uncorrected).
    """
    import base64

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping text correction")
        return {}

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        img_b64 = base64.b64encode(png_bytes).decode("utf-8")

        prompt_template = _load_hybrid_correction_prompt()
        blocks_json = json.dumps(blocks, indent=2)
        prompt = prompt_template.replace("{blocks_json}", blocks_json)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }],
        )

        resp_text = response.content[0].text
        try:
            data = json.loads(resp_text)
        except json.JSONDecodeError:
            data = parse_json_lenient(resp_text)

        corrections: dict[int, str] = {}
        for item in data.get("corrections", []):
            block_id = item.get("id")
            corrected = item.get("corrected_text", "")
            if block_id is not None and corrected:
                corrections[int(block_id)] = corrected

        n_corrected = len(corrections)
        if n_corrected:
            logger.info(
                "Haiku corrected %d/%d blocks on page %d",
                n_corrected, len(blocks), page_number + 1,
            )
        else:
            logger.debug("Haiku: no corrections needed for page %d", page_number + 1)

        return corrections

    except Exception as e:
        logger.warning("Haiku text correction failed for page %d: %s", page_number + 1, e)
        return {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestHaikuCorrectText -v`
Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tools/scanned_page_ocr.py src/prompts/hybrid_ocr_correction.md tests/test_scanned_ocr.py
git commit -m "feat: add Haiku text correction and prompt for hybrid OCR"
```

---

### Task 4: `_apply_corrections()` and `_merge_blocks_and_structure()`

These two functions combine the outputs of all three models into the final `ParagraphInfo` / `TableInfo` / `ImageInfo` objects that the rest of the pipeline expects.

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scanned_ocr.py`:

```python
class TestApplyCorrections:
    """Tests for _apply_corrections()."""

    def test_applies_corrections_to_matching_blocks(self):
        from src.tools.scanned_page_ocr import _apply_corrections

        blocks = [
            {"id": 0, "text": "INTRODUCTION", "bbox": [100, 50, 300, 20]},
            {"id": 1, "text": "tbe learner", "bbox": [50, 100, 300, 20]},
            {"id": 2, "text": "correct text", "bbox": [50, 200, 300, 20]},
        ]
        corrections = {1: "the learner"}

        result = _apply_corrections(blocks, corrections)

        assert result[0]["text"] == "INTRODUCTION"  # unchanged
        assert result[1]["text"] == "the learner"    # corrected
        assert result[2]["text"] == "correct text"   # unchanged

    def test_empty_corrections_returns_blocks_unchanged(self):
        from src.tools.scanned_page_ocr import _apply_corrections

        blocks = [{"id": 0, "text": "hello", "bbox": [0, 0, 100, 20]}]
        result = _apply_corrections(blocks, {})

        assert result[0]["text"] == "hello"

    def test_preserves_bbox_and_id(self):
        from src.tools.scanned_page_ocr import _apply_corrections

        blocks = [{"id": 5, "text": "wrng", "bbox": [10, 20, 30, 40]}]
        corrections = {5: "wrong"}

        result = _apply_corrections(blocks, corrections)

        assert result[0]["id"] == 5
        assert result[0]["bbox"] == [10, 20, 30, 40]
        assert result[0]["text"] == "wrong"


class TestMergeBlocksAndStructure:
    """Tests for _merge_blocks_and_structure()."""

    def test_heading_region(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = [{"id": 0, "text": "INTRODUCTION", "bbox": [100, 50, 300, 20]}]
        structure = {
            "regions": [{
                "block_ids": [0], "type": "heading", "heading_level": 1,
                "column": 0, "reading_order": 1, "bold": True, "italic": False,
                "font_size_relative": "large",
            }]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )

        assert len(paras) == 1
        assert paras[0].heading_level == 1
        assert paras[0].text == "INTRODUCTION"
        assert paras[0].id == "ocr_p_0"
        assert paras[0].style_name == "Heading 1"
        assert paras[0].runs[0].bold is True

    def test_paragraph_region_merges_blocks(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = [
            {"id": 0, "text": "First part", "bbox": [50, 100, 300, 20]},
            {"id": 1, "text": "second part", "bbox": [50, 120, 300, 20]},
        ]
        structure = {
            "regions": [{
                "block_ids": [0, 1], "type": "paragraph",
                "column": 1, "reading_order": 1, "bold": False, "italic": False,
                "font_size_relative": "normal",
            }]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=3,
            para_offset=5, table_offset=0, img_offset=0,
        )

        assert len(paras) == 1
        assert paras[0].text == "First part second part"
        assert paras[0].id == "ocr_p_5"
        assert paras[0].page_number == 3
        assert paras[0].column == 1

    def test_table_region(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = [{"id": 0, "text": "Table 1 Summary", "bbox": [50, 100, 300, 20]}]
        structure = {
            "regions": [
                {
                    "block_ids": [0], "type": "caption",
                    "column": 0, "reading_order": 1,
                    "font_size_relative": "small",
                },
                {
                    "block_ids": [], "type": "table",
                    "column": 0, "reading_order": 2,
                    "table_data": {
                        "headers": ["Name", "Value"],
                        "rows": [["A", "1"], ["B", "2"]],
                    },
                },
            ]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )

        assert len(paras) == 1  # caption
        assert paras[0].text == "Table 1 Summary"
        assert len(tables) == 1
        assert tables[0].id == "ocr_tbl_0"
        assert tables[0].header_row_count == 1
        assert tables[0].row_count == 3  # 1 header + 2 data rows
        assert tables[0].col_count == 2

    def test_figure_region(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = []
        structure = {
            "regions": [{
                "block_ids": [], "type": "figure",
                "column": 0, "reading_order": 1,
                "figure_description": "Bar chart showing test scores",
            }]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=2,
            para_offset=0, table_offset=0, img_offset=0, pdf_doc=None,
        )

        assert len(figures) == 1
        assert figures[0].alt_text == "Bar chart showing test scores"
        assert figures[0].id == "ocr_img_0"

    def test_page_header_footer_skipped(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = [
            {"id": 0, "text": "MAYER 157", "bbox": [100, 10, 300, 12]},
            {"id": 1, "text": "Real content here", "bbox": [50, 100, 300, 20]},
        ]
        structure = {
            "regions": [
                {"block_ids": [0], "type": "page_header", "reading_order": 1, "column": 0},
                {"block_ids": [1], "type": "paragraph", "reading_order": 2, "column": 1,
                 "bold": False, "italic": False, "font_size_relative": "normal"},
            ]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )

        assert len(paras) == 1
        assert paras[0].text == "Real content here"

    def test_reading_order_respected(self):
        from src.tools.scanned_page_ocr import _merge_blocks_and_structure

        blocks = [
            {"id": 0, "text": "Second", "bbox": [50, 200, 300, 20]},
            {"id": 1, "text": "First", "bbox": [50, 100, 300, 20]},
        ]
        structure = {
            "regions": [
                {"block_ids": [1], "type": "paragraph", "reading_order": 1,
                 "column": 0, "font_size_relative": "normal"},
                {"block_ids": [0], "type": "paragraph", "reading_order": 2,
                 "column": 0, "font_size_relative": "normal"},
            ]
        }

        paras, tables, figures = _merge_blocks_and_structure(
            blocks, structure, page_number=0,
            para_offset=0, table_offset=0, img_offset=0,
        )

        assert paras[0].text == "First"
        assert paras[1].text == "Second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestApplyCorrections tests/test_scanned_ocr.py::TestMergeBlocksAndStructure -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement both functions**

Add to `src/tools/scanned_page_ocr.py`:

```python
def _apply_corrections(
    blocks: list[dict],
    corrections: dict[int, str],
) -> list[dict]:
    """Apply Haiku text corrections to Tesseract blocks.

    Returns a new list of blocks with corrected text where available.
    Blocks not in corrections dict keep their original text.
    """
    if not corrections:
        return blocks

    result = []
    for block in blocks:
        block_id = block["id"]
        if block_id in corrections:
            result.append({**block, "text": corrections[block_id]})
        else:
            result.append(block)
    return result


def _merge_blocks_and_structure(
    blocks: list[dict],
    structure: dict,
    page_number: int,
    para_offset: int,
    table_offset: int,
    img_offset: int,
    pdf_doc: fitz.Document | None = None,
) -> tuple[list[ParagraphInfo], list[TableInfo], list[ImageInfo]]:
    """Combine corrected Tesseract blocks with Gemini structure annotations.

    For text regions: concatenate text from referenced block_ids.
    For table regions: use Gemini's table_data.
    For figure regions: use Gemini's figure_description.
    """
    blocks_by_id = {b["id"]: b for b in blocks}
    regions = structure.get("regions", [])

    # Sort by reading_order
    regions.sort(key=lambda r: r.get("reading_order", 0))

    paragraphs: list[ParagraphInfo] = []
    tables: list[TableInfo] = []
    figures: list[ImageInfo] = []

    para_idx = 0
    tbl_idx = 0
    fig_idx = 0

    for region in regions:
        region_type = region.get("type", "")
        block_ids = region.get("block_ids", [])
        column = region.get("column", 0) or 0

        # Skip page headers/footers
        if region_type in ("page_header", "page_footer"):
            continue

        if region_type in ("heading", "paragraph", "caption", "footnote", "equation"):
            # Concatenate text from referenced blocks
            texts = []
            for bid in block_ids:
                if bid in blocks_by_id:
                    texts.append(blocks_by_id[bid]["text"])
            text = " ".join(texts).strip()
            if not text:
                continue

            # Filter leaked headers/footers
            if _is_leaked_header_footer(text):
                continue

            bold = region.get("bold", False)
            italic = region.get("italic", False)
            font_size_rel = region.get("font_size_relative", "normal")

            heading_level = None
            style_name = "Normal"

            if region_type == "heading":
                heading_level = region.get("heading_level", 2)
                heading_level = max(1, min(6, heading_level))
                style_name = f"Heading {heading_level}"
                bold = True

            if region_type == "caption":
                italic = True
                font_size_rel = "small"

            if region_type == "equation":
                italic = True

            paragraphs.append(ParagraphInfo(
                id=f"ocr_p_{para_offset + para_idx}",
                text=text,
                style_name=style_name,
                heading_level=heading_level,
                runs=[RunInfo(
                    text=text,
                    bold=bold if bold else None,
                    italic=italic if italic else None,
                    font_size_pt=_relative_to_pt(font_size_rel),
                )],
                page_number=page_number,
                column=column,
            ))
            para_idx += 1

        elif region_type == "table":
            table_data = region.get("table_data", {})
            headers = table_data.get("headers", [])
            rows = table_data.get("rows", [])

            if not headers and not rows:
                continue

            table_rows: list[list[CellInfo]] = []
            if headers:
                table_rows.append([CellInfo(text=h, paragraphs=[h]) for h in headers])
            for row_cells in rows:
                table_rows.append([CellInfo(text=c, paragraphs=[c]) for c in row_cells])

            col_count = max((len(r) for r in table_rows), default=0)

            tables.append(TableInfo(
                id=f"ocr_tbl_{table_offset + tbl_idx}",
                rows=table_rows,
                header_row_count=1 if headers else 0,
                has_header_style=bool(headers),
                row_count=len(table_rows),
                col_count=col_count,
                page_number=page_number,
            ))
            tbl_idx += 1

        elif region_type == "figure":
            desc = region.get("figure_description", "")
            img_data = None
            width = None
            height = None
            if pdf_doc is not None and 0 <= page_number < len(pdf_doc):
                try:
                    page = pdf_doc[page_number]
                    pix = page.get_pixmap(dpi=150)
                    img_data = pix.tobytes("png")
                    width = pix.width
                    height = pix.height
                except Exception:
                    pass

            figures.append(ImageInfo(
                id=f"ocr_img_{img_offset + fig_idx}",
                image_data=img_data,
                content_type="image/png",
                alt_text=desc,
                width_px=width,
                height_px=height,
                page_number=page_number,
                is_decorative=False,
            ))
            fig_idx += 1

    return paragraphs, tables, figures
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestApplyCorrections tests/test_scanned_ocr.py::TestMergeBlocksAndStructure -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "feat: add _apply_corrections() and _merge_blocks_and_structure() for hybrid OCR"
```

---

### Task 5: `_heuristic_classify_blocks()` — Fallback when Gemini is unavailable

Extract the heading-detection and column-classification heuristics from `_tesseract_fallback()` into a standalone function that operates on block dicts instead of raw Tesseract data.

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scanned_ocr.py`:

```python
class TestHeuristicClassifyBlocks:
    """Tests for _heuristic_classify_blocks() — fallback classification."""

    def test_all_caps_detected_as_heading(self):
        from src.tools.scanned_page_ocr import _heuristic_classify_blocks

        blocks = [
            {"id": 0, "text": "INTRODUCTION", "bbox": [100, 50, 300, 24]},
            {"id": 1, "text": "This is body text that continues for a while.", "bbox": [50, 100, 350, 14]},
        ]

        paras = _heuristic_classify_blocks(blocks, page_number=0, para_offset=0)

        assert len(paras) == 2
        assert paras[0].heading_level == 2
        assert paras[0].style_name == "Heading 2"
        assert paras[1].heading_level is None
        assert paras[1].style_name == "Normal"

    def test_body_text_not_heading(self):
        from src.tools.scanned_page_ocr import _heuristic_classify_blocks

        blocks = [
            {"id": 0, "text": "This is a normal paragraph with lower case text.", "bbox": [50, 50, 350, 14]},
        ]

        paras = _heuristic_classify_blocks(blocks, page_number=2, para_offset=10)

        assert len(paras) == 1
        assert paras[0].heading_level is None
        assert paras[0].id == "ocr_p_10"
        assert paras[0].page_number == 2

    def test_empty_blocks_returns_empty(self):
        from src.tools.scanned_page_ocr import _heuristic_classify_blocks

        paras = _heuristic_classify_blocks([], page_number=0, para_offset=0)

        assert paras == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestHeuristicClassifyBlocks -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `_heuristic_classify_blocks()`**

Add to `src/tools/scanned_page_ocr.py`:

```python
def _heuristic_classify_blocks(
    blocks: list[dict],
    page_number: int,
    para_offset: int,
) -> list[ParagraphInfo]:
    """Classify blocks using heuristics when Gemini is unavailable.

    Uses ALL CAPS detection and font size heuristics from bbox height
    to identify headings. Everything else becomes a paragraph.
    """
    if not blocks:
        return []

    # Estimate average body text height from bbox
    heights = [b["bbox"][3] for b in blocks if len(b["text"]) > 20]
    avg_height = sum(heights) / len(heights) if heights else 14

    paragraphs: list[ParagraphInfo] = []

    for i, block in enumerate(blocks):
        text = block["text"]
        block_height = block["bbox"][3]  # height from bbox

        is_heading = False
        heading_level = 0

        words = text.split()
        # ALL CAPS short text
        if (len(words) <= 8
                and text == text.upper()
                and any(c.isalpha() for c in text)
                and len(text) > 3):
            is_heading = True
            heading_level = 2

        # Significantly larger than body text
        elif block_height > avg_height * 1.3 and len(words) <= 10:
            is_heading = True
            heading_level = 2

        font_size = 16.0 if is_heading else 12.0

        paragraphs.append(ParagraphInfo(
            id=f"ocr_p_{para_offset + i}",
            text=text,
            style_name=f"Heading {heading_level}" if is_heading else "Normal",
            heading_level=heading_level if is_heading else None,
            runs=[RunInfo(
                text=text,
                bold=is_heading or None,
                font_size_pt=font_size,
            )],
            page_number=page_number,
        ))

    return paragraphs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestHeuristicClassifyBlocks -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "feat: add _heuristic_classify_blocks() fallback for hybrid OCR"
```

---

### Task 6: Rewrite `_process_single_page()` and update imports

Replace the old Gemini→Gemini HD→crops→Tesseract retry chain with the new hybrid pipeline. Update the test file imports to reflect removed/added functions.

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`
- Modify: `tests/test_scanned_ocr.py`

- [ ] **Step 1: Write the failing tests for the new `_process_single_page()`**

Add to `tests/test_scanned_ocr.py`:

```python
class TestHybridProcessSinglePage:
    """Tests for the rewritten _process_single_page() using hybrid OCR."""

    @patch("src.tools.scanned_page_ocr._haiku_correct_text")
    @patch("src.tools.scanned_page_ocr._gemini_classify_structure")
    @patch("src.tools.scanned_page_ocr._tesseract_extract_blocks")
    def test_full_success_path(self, mock_tess, mock_gemini, mock_haiku):
        """All three models succeed → corrected text + full structure."""
        mock_tess.return_value = [
            {"id": 0, "text": "INTRO", "bbox": [100, 50, 300, 24]},
            {"id": 1, "text": "tbe body text", "bbox": [50, 100, 300, 14]},
        ]
        mock_gemini.return_value = {
            "regions": [
                {"block_ids": [0], "type": "heading", "heading_level": 1,
                 "column": 0, "reading_order": 1, "bold": True,
                 "font_size_relative": "large"},
                {"block_ids": [1], "type": "paragraph",
                 "column": 1, "reading_order": 2, "bold": False,
                 "font_size_relative": "normal"},
            ]
        }
        mock_haiku.return_value = {1: "the body text"}

        from src.tools.scanned_page_ocr import _process_single_page
        doc = MagicMock()
        result = _process_single_page(None, "gemini-2.5-flash", doc, 0, "")

        assert result.source == "hybrid"
        assert len(result.paragraphs) == 2
        assert result.paragraphs[0].heading_level == 1
        assert result.paragraphs[1].text == "the body text"  # corrected

    @patch("src.tools.scanned_page_ocr._haiku_correct_text")
    @patch("src.tools.scanned_page_ocr._gemini_classify_structure")
    @patch("src.tools.scanned_page_ocr._tesseract_extract_blocks")
    def test_gemini_fails_haiku_succeeds(self, mock_tess, mock_gemini, mock_haiku):
        """Gemini fails → heuristic structure, corrected text."""
        mock_tess.return_value = [
            {"id": 0, "text": "REFERENCES", "bbox": [100, 50, 200, 24]},
            {"id": 1, "text": "tbe citation", "bbox": [50, 100, 300, 14]},
        ]
        mock_gemini.return_value = None
        mock_haiku.return_value = {1: "the citation"}

        from src.tools.scanned_page_ocr import _process_single_page
        doc = MagicMock()
        result = _process_single_page(None, "gemini-2.5-flash", doc, 0, "")

        assert result.source == "hybrid_fallback"
        assert len(result.paragraphs) == 2
        assert result.paragraphs[0].heading_level == 2  # ALL CAPS heuristic
        assert result.paragraphs[1].text == "the citation"

    @patch("src.tools.scanned_page_ocr._haiku_correct_text")
    @patch("src.tools.scanned_page_ocr._gemini_classify_structure")
    @patch("src.tools.scanned_page_ocr._tesseract_extract_blocks")
    def test_both_apis_fail(self, mock_tess, mock_gemini, mock_haiku):
        """Both APIs fail → heuristic structure, uncorrected text."""
        mock_tess.return_value = [
            {"id": 0, "text": "some text", "bbox": [50, 50, 300, 14]},
        ]
        mock_gemini.return_value = None
        mock_haiku.return_value = {}

        from src.tools.scanned_page_ocr import _process_single_page
        doc = MagicMock()
        result = _process_single_page(None, "gemini-2.5-flash", doc, 0, "")

        assert result.source == "hybrid_fallback"
        assert len(result.paragraphs) == 1
        assert result.paragraphs[0].text == "some text"

    @patch("src.tools.scanned_page_ocr._tesseract_extract_blocks")
    def test_tesseract_fails(self, mock_tess):
        """Tesseract fails → page fails entirely."""
        mock_tess.return_value = []

        from src.tools.scanned_page_ocr import _process_single_page
        doc = MagicMock()
        result = _process_single_page(None, "gemini-2.5-flash", doc, 0, "")

        assert result.source == "failed"
        assert len(result.paragraphs) == 0
        assert len(result.warnings) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scanned_ocr.py::TestHybridProcessSinglePage -v`
Expected: Tests fail because `_process_single_page` still uses the old implementation.

- [ ] **Step 3: Rewrite `_process_single_page()`**

Replace the existing `_process_single_page()` function (lines ~379-553 of `scanned_page_ocr.py`) with:

```python
def _process_single_page(
    client,
    model: str,
    doc: fitz.Document,
    page_number: int,
    prompt: str,
) -> PageOCRResult:
    """Process a single scanned page using hybrid OCR.

    1. Tesseract extracts raw text blocks (always runs first)
    2. In parallel: Gemini classifies structure, Haiku corrects text
    3. Merge results

    Falls back gracefully at each level.
    """
    result = PageOCRResult(page_number=page_number)

    # ── Step 1: Tesseract text extraction ──────────────────────
    blocks = _tesseract_extract_blocks(doc, page_number)
    if not blocks:
        result.warnings.append(f"Page {page_number + 1}: Tesseract extracted no text")
        return result

    logger.info(
        "Page %d: Tesseract extracted %d blocks", page_number + 1, len(blocks),
    )

    # ── Step 2: Gemini structure + Haiku correction (parallel) ─
    # In production these could be concurrent; for now sequential
    structure = None
    corrections: dict[int, str] = {}

    if client is not None:
        structure = _gemini_classify_structure(
            client, model, doc, page_number, blocks,
        )
        if structure:
            logger.info("Page %d: Gemini structure classification succeeded", page_number + 1)
        else:
            logger.warning("Page %d: Gemini structure classification failed — using heuristics", page_number + 1)

    corrections = _haiku_correct_text(blocks, doc, page_number)
    if corrections:
        logger.info(
            "Page %d: Haiku corrected %d/%d blocks",
            page_number + 1, len(corrections), len(blocks),
        )

    # ── Step 3: Apply corrections ──────────────────────────────
    corrected_blocks = _apply_corrections(blocks, corrections)

    # ── Step 4: Merge into model objects ───────────────────────
    if structure:
        paras, tables, figures = _merge_blocks_and_structure(
            corrected_blocks, structure, page_number,
            para_offset=0, table_offset=0, img_offset=0,
            pdf_doc=doc,
        )
        result.paragraphs = paras
        result.tables = tables
        result.figures = figures
        result.source = "hybrid"
    else:
        # Fallback: heuristic classification
        result.paragraphs = _heuristic_classify_blocks(
            corrected_blocks, page_number, para_offset=0,
        )
        result.source = "hybrid_fallback"

    logger.info(
        "Page %d: %s → %d paragraphs, %d tables, %d figures",
        page_number + 1, result.source,
        len(result.paragraphs), len(result.tables), len(result.figures),
    )

    return result
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `pytest tests/test_scanned_ocr.py::TestHybridProcessSinglePage -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Update imports in test file**

Update the import block at the top of `tests/test_scanned_ocr.py`. Remove imports for deleted functions and add imports for new functions:

Remove from imports:
- `_find_garbled_pages`
- `_is_garbled_text`
- `_integrate_page_data`

Add to imports:
- `_tesseract_extract_blocks`
- `_gemini_classify_structure`
- `_haiku_correct_text`
- `_apply_corrections`
- `_merge_blocks_and_structure`
- `_heuristic_classify_blocks`

The updated import block should be:

```python
from src.tools.scanned_page_ocr import (
    PageOCRResult,
    ScannedPageResult,
    _apply_corrections,
    _collect_table_paragraphs,
    _find_table_captions,
    _gemini_classify_structure,
    _haiku_correct_text,
    _heuristic_classify_blocks,
    _is_leaked_header_footer,
    _merge_blocks_and_structure,
    _process_single_page,
    _regions_to_model_objects,
    _relative_to_pt,
    _rescue_missed_tables,
    _sort_regions_by_column,
    _stitch_page_results,
    _tesseract_extract_blocks,
)
```

- [ ] **Step 6: Remove or update tests for deleted functions**

Delete these test classes that test removed functions:
- `TestIsGarbledText` (tests `_is_garbled_text`)
- `TestFindGarbledPages` (tests `_find_garbled_pages`)
- `TestIntegratePageDataWithRescue` (tests `_integrate_page_data`)

Update `TestProcessSinglePage` — either delete it (replaced by `TestHybridProcessSinglePage`) or rename/update to match the new implementation. The old test class at line 1638 tests the old Gemini→Tesseract retry chain which no longer exists. Delete it.

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All tests PASS. Some existing tests (like `TestRegionsToModelObjects`, `TestMergeOcrIntoModel`, etc.) should still pass since `_regions_to_model_objects` and `_stitch_page_results` are preserved.

- [ ] **Step 8: Commit**

```bash
git add src/tools/scanned_page_ocr.py tests/test_scanned_ocr.py
git commit -m "feat: rewrite _process_single_page() for hybrid OCR pipeline

Replaces Gemini→Gemini HD→crops→Tesseract retry chain with:
Tesseract blocks → Gemini structure + Haiku correction → merge.
Graceful fallback at every level."
```

---

### Task 7: Clean up removed code from `scanned_page_ocr.py`

Remove the old Gemini-OCR functions and schema that are no longer used. The old `_tesseract_fallback()` is also replaced by `_tesseract_extract_blocks()` + `_heuristic_classify_blocks()`.

**Files:**
- Modify: `src/tools/scanned_page_ocr.py`

- [ ] **Step 1: Remove dead code**

Delete the following from `src/tools/scanned_page_ocr.py`:

1. `OCR_PAGE_SCHEMA` (the old Gemini schema, lines ~73-142) — replaced by `HYBRID_STRUCTURE_SCHEMA`
2. `_load_prompt()` (line ~145) — replaced by `_load_hybrid_structure_prompt()`
3. `_process_ocr_batch()` (line ~588) — no longer used
4. `_is_garbled_text()` (line ~718) — no longer relevant
5. `_find_garbled_pages()` (line ~1116) — no longer relevant
6. `_integrate_page_data()` (line ~278) — replaced by `_merge_blocks_and_structure()`
7. `_tesseract_fallback()` (line ~1404) — replaced by `_tesseract_extract_blocks()` + `_heuristic_classify_blocks()`
8. The half-page crop logic that was inside the old `_process_single_page()` — already removed in Task 6

Keep:
- `_load_table_rescue_prompt()` — still used by table rescue
- `_regions_to_model_objects()` — still used by table rescue (`_rescue_missed_tables` calls it internally for rescued tables). Check if this is true: read `_rescue_missed_tables()` to confirm.

- [ ] **Step 2: Verify no remaining references to deleted functions**

Run: `grep -n "_process_ocr_batch\|_is_garbled_text\|_find_garbled_pages\|_integrate_page_data\|_tesseract_fallback\|OCR_PAGE_SCHEMA\|_load_prompt" src/tools/scanned_page_ocr.py`
Expected: No matches (or only comments referencing old behavior).

Also check tests:

Run: `grep -n "_process_ocr_batch\|_is_garbled_text\|_find_garbled_pages\|_integrate_page_data\|_tesseract_fallback\|OCR_PAGE_SCHEMA\|_load_prompt" tests/test_scanned_ocr.py`
Expected: No matches.

- [ ] **Step 3: Run the full test suite**

Run: `pytest tests/test_scanned_ocr.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Run the full project test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All ~900 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/scanned_page_ocr.py
git commit -m "refactor: remove old Gemini-OCR code replaced by hybrid pipeline"
```

---

### Task 8: End-to-end test with Mayer document

Run the batch test script on the Mayer paper to validate the hybrid OCR pipeline produces good output with no RECITATION failures.

**Files:**
- No code changes — this is a validation task

- [ ] **Step 1: Run the Mayer document through the pipeline**

```bash
cd /Users/jenniferkleiman/Documents/GitHub/WCAGproject
python scripts/test_batch.py --doc "7) Mayer"
```

Watch the log output for:
- All 11 pages should show `Tesseract extracted N blocks`
- Gemini structure classification should succeed on all/most pages (no RECITATION)
- Haiku text corrections should appear on most pages
- No pages should show `failed`

- [ ] **Step 2: Check the output quality**

Open the generated HTML file in `testdocs/output/` and verify:
- All 11 pages have text content (no blank pages)
- Headings are correctly identified (INTRODUCTION, METHOD, RESULTS, etc.)
- Tables have proper headers and cell data (especially Table 1 and Table 2)
- Two-column reading order is correct (left column before right)
- No garbled or duplicated text
- Figures have alt text descriptions

- [ ] **Step 3: Compare against previous output**

The previous output is at:
`testdocs/output/7) Mayer Learners as information processors-..._accessible.html`

Compare:
- Pages 10-11 should now have real text (previously lost to RECITATION)
- Non-RECITATION pages should have equal or better text quality (Haiku corrections)
- Table extraction should be at least as good as before

- [ ] **Step 4: Document results**

Note any issues found for follow-up. If output quality is good, update `NOW.md` with the results.

- [ ] **Step 5: Commit any test output changes**

If `NOW.md` was updated:
```bash
git add NOW.md
git commit -m "Update NOW.md: hybrid OCR shipped, Mayer results"
```
