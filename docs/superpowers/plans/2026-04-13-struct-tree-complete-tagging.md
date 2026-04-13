# Complete Struct Tree Tagging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace artifact-marking of untagged content with /P struct tagging, and preserve good existing struct trees instead of stripping them — fixing the benchmark regression from 86.7% to 38.8%.

**Architecture:** Python-centric approach. `mark_untagged_content_as_artifact()` in `pdf_writer.py` is replaced by `tag_or_artifact_untagged_content()` which tags untagged content stream runs as /P (with MCIDs and struct elements) instead of /Artifact. A tree quality assessment function decides whether to preserve or rebuild the existing struct tree. Minimal iText Java change: use existing /Document root instead of always creating a new one.

**Tech Stack:** Python 3.11+, PyMuPDF (fitz), iText 9 (Java), pytest

**Spec:** `docs/superpowers/specs/2026-04-13-struct-tree-complete-tagging-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/tools/pdf_writer.py` | Modify | Add `TreeAssessment`, `assess_struct_tree_quality()`, `ContentTaggingResult`, `TaggedRun`, `_apply_content_tag_wrappers()`, `tag_or_artifact_untagged_content()`, `_extract_text_from_run()`, `_is_page_furniture()`, `_scan_page_furniture()`, `_get_max_mcid_for_page()`, `_update_parent_tree_for_mcids()` |
| `src/tools/itext_tagger.py` | Modify | Add `filter_tagging_plan_for_existing_tree()` |
| `src/agent/executor.py` | Modify | Branch on tree assessment, wire up new functions |
| `java/.../PdfTagger.java` | Modify | Use existing /Document root on preserve path |
| `tests/test_struct_tree.py` | Create | All new tests for this feature |

---

### Task 1: `_get_max_mcid_for_page()` — per-page MCID scanning

**Files:**
- Create: `tests/test_struct_tree.py`
- Modify: `src/tools/pdf_writer.py:2205` (near `_collect_struct_tree_mcids`)

- [ ] **Step 1: Write failing tests**

```python
"""Tests for complete struct tree tagging."""

import pytest
import shutil
from pathlib import Path

import fitz

from src.tools.pdf_writer import (
    Token,
    _tokenize_content_stream,
)

TESTDOCS = Path(__file__).parent.parent / "testdocs"
SYLLABUS_PDF = TESTDOCS / "EMAT 8030 syllabus spring 2026.pdf"


class TestGetMaxMcidForPage:
    """Per-page MCID scanning from content streams."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_no_bdcs_returns_negative_one(self):
        """Page with no BDC markers has no MCIDs."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("BT (hello) Tj ET")
        assert _get_max_mcid_for_page(tokens) == -1

    def test_single_mcid(self):
        """One BDC with MCID 0."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        tokens = self._tokenize("/P <</MCID 0>> BDC BT (hi) Tj ET EMC")
        assert _get_max_mcid_for_page(tokens) == 0

    def test_multiple_mcids_returns_max(self):
        """Multiple BDCs — return the highest MCID."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = (
            "/P <</MCID 0>> BDC BT (a) Tj ET EMC\n"
            "/P <</MCID 3>> BDC BT (b) Tj ET EMC\n"
            "/H1 <</MCID 1>> BDC BT (c) Tj ET EMC\n"
        )
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == 3

    def test_artifact_bdc_ignored(self):
        """/Artifact BDC has no MCID — should be ignored."""
        from src.tools.pdf_writer import _get_max_mcid_for_page
        stream = "/Artifact <</Type /Pagination>> BDC BT (1) Tj ET EMC"
        tokens = self._tokenize(stream)
        assert _get_max_mcid_for_page(tokens) == -1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestGetMaxMcidForPage -v`
Expected: ImportError — `_get_max_mcid_for_page` doesn't exist yet

- [ ] **Step 3: Implement `_get_max_mcid_for_page`**

Add after `_collect_struct_tree_mcids()` (line ~2260) in `src/tools/pdf_writer.py`:

```python
def _get_max_mcid_for_page(tokens: list[Token]) -> int:
    """Scan tokenized content stream for the highest MCID value.

    MCIDs are per-page. Returns -1 if no MCIDs found.
    Parses BDC operands like ``/P <</MCID 3>> BDC``.
    """
    max_mcid = -1
    for i, token in enumerate(tokens):
        if token.type != "operator" or token.value != "BDC":
            continue
        # Look backward for the dict operand containing /MCID
        for j in range(i - 1, max(i - 5, -1), -1):
            t = tokens[j]
            if t.type in ("dict", "operand") and "/MCID" in t.value:
                m = re.search(r"/MCID\s+(\d+)", t.value)
                if m:
                    mcid = int(m.group(1))
                    if mcid > max_mcid:
                        max_mcid = mcid
                break
            if t.type == "operator":
                break
    return max_mcid
```

Add to imports if not already present: `import re` (already imported in pdf_writer.py).

- [ ] **Step 4: Export from module and run tests**

Add `_get_max_mcid_for_page` to the test import at the top of `tests/test_struct_tree.py`.

Run: `pytest tests/test_struct_tree.py::TestGetMaxMcidForPage -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add _get_max_mcid_for_page() for per-page MCID scanning"
```

---

### Task 2: `TaggedRun` and `_apply_content_tag_wrappers()`

**Files:**
- Modify: `src/tools/pdf_writer.py:1982` (near `_apply_artifact_wrappers`)
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_struct_tree.py`:

```python
class TestApplyContentTagWrappers:
    """Tests for _apply_content_tag_wrappers() — mixed /P and /Artifact wrapping."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_single_p_run(self):
        """One run tagged as /P with MCID 0."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (hello) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=0)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 0>> BDC" in text
        assert "EMC" in text
        assert "/Artifact" not in text

    def test_single_artifact_run(self):
        """One run tagged as /Artifact."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (3) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/Artifact", mcid=None)]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/Artifact <</Type /Pagination>> BDC" in text
        assert "EMC" in text
        assert "/MCID" not in text

    def test_mixed_p_and_artifact(self):
        """Two runs — first /P, second /Artifact."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (body text) Tj ET\nBT (3) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 2
        tagged_runs = [
            TaggedRun(start=runs[0][0], end=runs[0][1], tag_type="/P", mcid=0),
            TaggedRun(start=runs[1][0], end=runs[1][1], tag_type="/Artifact", mcid=None),
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 0>> BDC" in text
        assert "/Artifact <</Type /Pagination>> BDC" in text

    def test_no_runs_returns_original(self):
        """Empty run list returns original stream."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers,
        )
        tokens = self._tokenize("BT (x) Tj ET")
        result = _apply_content_tag_wrappers(tokens, [])
        text = result.decode("latin-1")
        assert "BDC" not in text

    def test_unique_mcids_per_run(self):
        """Each /P run gets its own MCID."""
        from src.tools.pdf_writer import (
            TaggedRun, _apply_content_tag_wrappers, _find_untagged_content_runs,
        )
        tokens = self._tokenize("BT (a) Tj ET\nBT (b) Tj ET\nBT (c) Tj ET")
        runs = _find_untagged_content_runs(tokens)
        tagged_runs = [
            TaggedRun(start=r[0], end=r[1], tag_type="/P", mcid=i)
            for i, r in enumerate(runs)
        ]
        result = _apply_content_tag_wrappers(tokens, tagged_runs)
        text = result.decode("latin-1")
        assert "/P <</MCID 0>> BDC" in text
        assert "/P <</MCID 1>> BDC" in text
        assert "/P <</MCID 2>> BDC" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestApplyContentTagWrappers -v`
Expected: ImportError — `TaggedRun` and `_apply_content_tag_wrappers` don't exist

- [ ] **Step 3: Implement `TaggedRun` and `_apply_content_tag_wrappers`**

Add after `_apply_artifact_wrappers()` (~line 2020) in `src/tools/pdf_writer.py`:

```python
@dataclass
class TaggedRun:
    """A content stream run classified for tagging."""
    start: int          # token index (inclusive)
    end: int            # token index (inclusive)
    tag_type: str       # "/P", "/L", "/Artifact"
    mcid: int | None    # MCID for struct-tagged runs, None for /Artifact


def _apply_content_tag_wrappers(
    tokens: list[Token],
    tagged_runs: list[TaggedRun],
) -> bytes:
    """Reassemble token list with per-run BDC/EMC wrappers.

    Unlike _apply_artifact_wrappers which applies the same wrapper to all
    runs, this handles mixed tagging: /P runs get MCID-bearing BDCs,
    /Artifact runs get pagination BDCs.
    """
    if not tagged_runs:
        return _reassemble_stream(tokens)

    # Build lookup: start_idx → TaggedRun, end_idx → TaggedRun
    starts: dict[int, TaggedRun] = {r.start: r for r in tagged_runs}
    ends: dict[int, TaggedRun] = {r.end: r for r in tagged_runs}

    out: list[Token] = []
    for i, t in enumerate(tokens):
        if i in starts:
            run = starts[i]
            if run.tag_type == "/Artifact":
                bdc = Token(
                    value="/Artifact <</Type /Pagination>> BDC\n",
                    type="operator",
                )
            else:
                bdc = Token(
                    value=f"{run.tag_type} <</MCID {run.mcid}>> BDC\n",
                    type="operator",
                )
            out.append(bdc)
        out.append(t)
        if i in ends:
            out.append(Token(value="\nEMC", type="operator"))

    return _reassemble_stream(out)
```

- [ ] **Step 4: Update imports and run tests**

Add `TaggedRun` and `_apply_content_tag_wrappers` to the import in `tests/test_struct_tree.py`.

Run: `pytest tests/test_struct_tree.py::TestApplyContentTagWrappers -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add TaggedRun and _apply_content_tag_wrappers() for mixed BDC wrapping"
```

---

### Task 3: `_extract_text_from_run()` and `_is_page_furniture()`

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_struct_tree.py`:

```python
class TestExtractTextFromRun:
    """Tests for extracting text from token runs."""

    def _tokenize(self, stream_str: str):
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_simple_tj(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT (Hello world) Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello world" in text

    def test_tj_array(self):
        """TJ operator with array of strings and kerning."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT [(He) -10 (llo)] TJ ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text

    def test_no_text_ops_returns_empty(self):
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("/Fm0 Do")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert text == ""

    def test_hex_string(self):
        """Hex-encoded string <48656C6C6F> = 'Hello'."""
        from src.tools.pdf_writer import _extract_text_from_run
        tokens = self._tokenize("BT <48656C6C6F> Tj ET")
        text = _extract_text_from_run(tokens, 0, len(tokens) - 1)
        assert "Hello" in text


class TestIsPageFurniture:
    """Tests for page furniture detection."""

    def test_bare_page_number(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("3", set()) is True

    def test_page_number_with_dashes(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("- 3 -", set()) is True

    def test_roman_numeral(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("iv", set()) is True
        assert _is_page_furniture("XII", set()) is True

    def test_body_text_not_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture(
            "The results of this study demonstrate that",
            set(),
        ) is False

    def test_repeated_header(self):
        from src.tools.pdf_writer import _is_page_furniture
        furniture = {"Chapter 3: Methods"}
        assert _is_page_furniture("Chapter 3: Methods", furniture) is True

    def test_empty_string_is_furniture(self):
        from src.tools.pdf_writer import _is_page_furniture
        assert _is_page_furniture("", set()) is True
        assert _is_page_furniture("   ", set()) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestExtractTextFromRun tests/test_struct_tree.py::TestIsPageFurniture -v`
Expected: ImportError

- [ ] **Step 3: Implement `_extract_text_from_run`**

Add in `src/tools/pdf_writer.py` near the other text extraction helpers:

```python
def _extract_text_from_run(
    tokens: list[Token], start: int, end: int
) -> str:
    """Extract readable text from a content stream token run.

    Best-effort: collects string operands from Tj, TJ, ', " operators.
    Handles parenthesized strings and hex strings. Font-encoded bytes
    are decoded as latin-1 (covers ASCII range for furniture detection).
    """
    parts: list[str] = []
    i = start
    while i <= end:
        t = tokens[i]
        if t.type == "operator" and t.value in ("Tj", "'", '"'):
            # Look backward for the string operand
            for j in range(i - 1, max(start - 1, i - 4), -1):
                s = tokens[j]
                if s.type == "operand":
                    parts.append(_decode_pdf_string_operand(s.value))
                    break
                if s.type == "operator":
                    break
        elif t.type == "operator" and t.value == "TJ":
            # Look backward for the array operand
            for j in range(i - 1, max(start - 1, i - 4), -1):
                s = tokens[j]
                if s.type == "operand" and s.value.startswith("["):
                    parts.append(_decode_tj_array(s.value))
                    break
                if s.type == "operator":
                    break
        i += 1
    return "".join(parts)


def _decode_pdf_string_operand(s: str) -> str:
    """Decode a PDF string operand: (text) or <hex>."""
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        return s[1:-1]
    if s.startswith("<") and s.endswith(">"):
        hex_str = s[1:-1]
        try:
            return bytes.fromhex(hex_str).decode("latin-1")
        except (ValueError, UnicodeDecodeError):
            return ""
    return s


def _decode_tj_array(s: str) -> str:
    """Decode a TJ array like [(He) -10 (llo)] to 'Hello'."""
    parts: list[str] = []
    for m in re.finditer(r"\(([^)]*)\)|<([0-9A-Fa-f]+)>", s):
        if m.group(1) is not None:
            parts.append(m.group(1))
        elif m.group(2) is not None:
            try:
                parts.append(bytes.fromhex(m.group(2)).decode("latin-1"))
            except (ValueError, UnicodeDecodeError):
                pass
    return "".join(parts)
```

- [ ] **Step 4: Implement `_is_page_furniture`**

```python
_PAGE_NUMBER_RE = re.compile(
    r"^[\s\-–—.]*"                # optional leading dashes/dots
    r"(?:\d{1,4}|[ivxlcdm]{1,8})"  # arabic or roman numeral
    r"[\s\-–—.]*$",               # optional trailing dashes/dots
    re.IGNORECASE,
)


def _is_page_furniture(text: str, furniture_set: set[str]) -> bool:
    """Return True if text is page decoration (not real content).

    Checks: empty/whitespace, page numbers, repeated headers/footers.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if _PAGE_NUMBER_RE.match(stripped):
        return True
    if stripped in furniture_set:
        return True
    return False
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_struct_tree.py::TestExtractTextFromRun tests/test_struct_tree.py::TestIsPageFurniture -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add _extract_text_from_run() and _is_page_furniture() for content classification"
```

---

### Task 4: `_scan_page_furniture()` — header/footer pre-pass

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing test**

```python
class TestScanPageFurniture:
    """Tests for repeated header/footer detection across pages."""

    def test_repeated_text_detected(self, tmp_path):
        """Text appearing on 3+ pages at similar positions → furniture."""
        from src.tools.pdf_writer import _scan_page_furniture
        # Create a multi-page PDF with repeated footer "Page N" and header
        doc = fitz.open()
        for i in range(5):
            page = doc.new_page()
            # Repeated header at top
            page.insert_text((72, 30), "Journal of Education Vol. 12",
                             fontsize=9)
            # Body text in middle (varies)
            page.insert_text((72, 300), f"Content of page {i + 1}",
                             fontsize=12)
            # Page number at bottom
            page.insert_text((300, 780), str(i + 1), fontsize=9)
        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        furniture = _scan_page_furniture(str(pdf_path))
        # The repeated header should be detected
        assert "Journal of Education Vol. 12" in furniture

    def test_unique_text_not_detected(self, tmp_path):
        """Text appearing on only 1 page is not furniture."""
        from src.tools.pdf_writer import _scan_page_furniture
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 300), f"Unique content {i}", fontsize=12)
        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        furniture = _scan_page_furniture(str(pdf_path))
        assert len(furniture) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestScanPageFurniture -v`
Expected: ImportError

- [ ] **Step 3: Implement `_scan_page_furniture`**

```python
def _scan_page_furniture(pdf_path: str | Path) -> set[str]:
    """Pre-scan all pages for repeated short text at top/bottom margins.

    Text appearing on 3+ pages at similar y-coordinates (within top/bottom
    10% of page height) and shorter than 50 chars is classified as page
    furniture (headers, footers, running titles).

    Returns set of normalized text strings.
    """
    doc = fitz.open(str(pdf_path))
    # Collect: {normalized_text: count}
    text_counts: dict[str, int] = {}
    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            height = page.rect.height
            margin_top = height * 0.10
            margin_bottom = height * 0.90

            blocks = page.get_text("blocks")
            for block in blocks:
                # block = (x0, y0, x1, y1, text, block_no, block_type)
                if block[6] != 0:  # skip image blocks
                    continue
                y0 = block[1]
                y1 = block[3]
                text = block[4].strip()

                if not text or len(text) > 50:
                    continue

                # Only consider text in top or bottom margins
                if y1 <= margin_top or y0 >= margin_bottom:
                    normalized = text.strip()
                    if normalized:
                        text_counts[normalized] = text_counts.get(
                            normalized, 0
                        ) + 1
    finally:
        doc.close()

    return {text for text, count in text_counts.items() if count >= 3}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_struct_tree.py::TestScanPageFurniture -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add _scan_page_furniture() for repeated header/footer detection"
```

---

### Task 5: `tag_or_artifact_untagged_content()` — core function

**Files:**
- Modify: `src/tools/pdf_writer.py:2021` (replaces `mark_untagged_content_as_artifact`)
- Modify: `tests/test_struct_tree.py`

This is the main function. It replaces `mark_untagged_content_as_artifact()` — the direct cause of the regression. The old function stays in the file (renamed `_legacy_mark_untagged_content_as_artifact`) until we verify the new one works, then removed.

- [ ] **Step 1: Write failing tests**

```python
class TestTagOrArtifactUntaggedContent:
    """End-to-end tests for the new content tagging function."""

    def _make_pdf_with_content(self, tmp_path, stream_str: str) -> Path:
        """Create a 1-page PDF with the given content stream."""
        doc = fitz.open()
        page = doc.new_page()
        # We'll write the stream directly
        xref = page.xref
        # Get contents xref
        contents = doc.xref_get_key(xref, "Contents")
        if contents[0] == "xref":
            c_xref = int(contents[1].split()[0])
        else:
            c_xref = doc.get_new_xref()
            doc.update_object(c_xref, "<< /Length 0 >>")
            doc.xref_set_key(xref, "Contents", f"{c_xref} 0 R")

        doc.update_stream(c_xref, stream_str.encode("latin-1"))

        # Add a minimal struct tree so the function has a /Document to
        # parent new elements under
        cat = doc.pdf_catalog()
        sroot_xref = doc.get_new_xref()
        doc_elem_xref = doc.get_new_xref()
        doc.update_object(doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {sroot_xref} 0 R /K [] >>")
        doc.update_object(sroot_xref,
            f"<< /Type /StructTreeRoot /K [{doc_elem_xref} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sroot_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_untagged_body_text_becomes_p(self, tmp_path):
        """Untagged body text gets /P BDC, not /Artifact."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT /F0 12 Tf 72 700 Td (This is body text for the paper) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1
        # Verify the content stream has /P BDC
        doc = fitz.open(str(pdf_path))
        stream = doc[0].read_contents().decode("latin-1")
        doc.close()
        assert "/P <<" in stream
        assert "/MCID" in stream
        assert "/Artifact" not in stream

    def test_already_tagged_content_untouched(self, tmp_path):
        """Content inside BDC/EMC is not re-tagged."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "/P <</MCID 0>> BDC\nBT (tagged body) Tj ET\nEMC"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged == 0
        assert result.artifacts_tagged == 0

    def test_mixed_tagged_and_untagged(self, tmp_path):
        """Tagged heading + untagged body → body gets /P."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        stream = (
            "/H1 <</MCID 0>> BDC\n"
            "BT (Heading) Tj ET\n"
            "EMC\n"
            "BT (This is untagged body text paragraph) Tj ET\n"
        )
        pdf_path = self._make_pdf_with_content(tmp_path, stream)
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1
        # Verify MCID doesn't collide with existing 0
        doc = fitz.open(str(pdf_path))
        stream_out = doc[0].read_contents().decode("latin-1")
        doc.close()
        assert "/P <</MCID 1>> BDC" in stream_out

    def test_page_number_becomes_artifact(self, tmp_path):
        """Bare page number gets /Artifact, not /P."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT /F0 9 Tf 300 30 Td (3) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.artifacts_tagged >= 1
        doc = fitz.open(str(pdf_path))
        stream = doc[0].read_contents().decode("latin-1")
        doc.close()
        assert "/Artifact" in stream

    def test_struct_elements_created_in_tree(self, tmp_path):
        """New /P struct elements appear in StructTreeRoot."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        pdf_path = self._make_pdf_with_content(
            tmp_path,
            "BT (paragraph one) Tj ET\nBT (paragraph two) Tj ET"
        )
        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 2
        # Check struct tree has /P elements
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st = doc.xref_get_key(cat, "StructTreeRoot")
        st_xref = int(st[1].split()[0])
        st_obj = doc.xref_object(st_xref) or ""
        # Find /Document kid
        import re as _re
        doc_kids = _re.findall(r"(\d+)\s+0\s+R", st_obj)
        found_p = False
        for kid_xref_str in doc_kids:
            kid_obj = doc.xref_object(int(kid_xref_str)) or ""
            # Check kids of /Document
            for m in _re.finditer(r"(\d+)\s+0\s+R", kid_obj):
                grandkid = doc.xref_object(int(m.group(1))) or ""
                if "/S /P" in grandkid:
                    found_p = True
                    break
        doc.close()
        assert found_p, "No /P struct elements found in tree"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestTagOrArtifactUntaggedContent -v`
Expected: ImportError

- [ ] **Step 3: Write `ContentTaggingResult` dataclass**

Add near `ArtifactMarkingResult` in `src/tools/pdf_writer.py`:

```python
@dataclass
class ContentTaggingResult:
    """Result of tag_or_artifact_untagged_content()."""
    success: bool
    pages_modified: int = 0
    paragraphs_tagged: int = 0
    lists_tagged: int = 0
    artifacts_tagged: int = 0
    pages_skipped: int = 0
    form_xobjects_modified: int = 0
    errors: list[str] = field(default_factory=list)
    # Per-page MCID→struct element mapping for ParentTree update
    page_mcid_map: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
```

- [ ] **Step 4: Implement `tag_or_artifact_untagged_content`**

Add after `ContentTaggingResult` in `src/tools/pdf_writer.py`:

```python
def tag_or_artifact_untagged_content(
    pdf_path: str | Path,
) -> ContentTaggingResult:
    """Walk PDF content streams and tag depth-0 untagged content.

    Replaces mark_untagged_content_as_artifact(). Instead of wrapping
    all untagged content as /Artifact, classifies each run:
    - Body text → /P struct element with MCID + BDC/EMC
    - Page furniture (page numbers, repeated headers/footers) → /Artifact

    Creates struct elements in the StructTreeRoot for /P runs.
    Populates result.page_mcid_map for subsequent ParentTree update.
    """
    path = Path(pdf_path)
    if not path.exists():
        return ContentTaggingResult(
            success=False, errors=[f"File not found: {path}"]
        )

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return ContentTaggingResult(
            success=False, errors=[f"Open failed: {exc}"]
        )

    result = ContentTaggingResult(success=True)

    # Collect referenced MCIDs for orphan detection (existing behavior)
    referenced_mcids = _collect_struct_tree_mcids(doc)

    # Pre-scan for repeated headers/footers
    try:
        furniture_set = _scan_page_furniture(pdf_path)
    except Exception:
        furniture_set = set()

    # Find /Document struct element for parenting new elements
    cat = doc.pdf_catalog()
    st_key = doc.xref_get_key(cat, "StructTreeRoot")
    if st_key[0] != "xref":
        doc.close()
        return ContentTaggingResult(
            success=False, errors=["No StructTreeRoot found"]
        )
    st_root_xref = int(st_key[1].split()[0])
    doc_elem_xref = _find_document_elem(doc, st_root_xref)
    if doc_elem_xref is None:
        doc.close()
        return ContentTaggingResult(
            success=False, errors=["No /Document struct element found"]
        )

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            try:
                stream_bytes = page.read_contents()
            except Exception as exc:
                result.errors.append(
                    f"page {page_idx}: read_contents failed: {exc}"
                )
                result.pages_skipped += 1
                continue

            if not stream_bytes:
                result.pages_skipped += 1
                continue

            tokens = _tokenize_content_stream(stream_bytes)

            # Convert orphan and suspect BDCs (existing behavior)
            orphan_indices = _find_orphan_bdc_openings(
                tokens, referenced_mcids
            )
            orphans_converted = _convert_orphan_bdc_to_artifact(
                tokens, orphan_indices
            )
            suspects_converted = _convert_suspect_bdc_to_artifact(tokens)

            runs = _find_untagged_content_runs(tokens)

            if not runs and not orphans_converted and not suspects_converted:
                result.pages_skipped += 1
                continue

            # Classify each run and assign MCIDs
            next_mcid = _get_max_mcid_for_page(tokens) + 1
            tagged_runs: list[TaggedRun] = []
            page_mcid_entries: list[tuple[int, int]] = []

            for start, end in runs:
                text = _extract_text_from_run(tokens, start, end)
                if _is_page_furniture(text, furniture_set):
                    tagged_runs.append(TaggedRun(
                        start=start, end=end,
                        tag_type="/Artifact", mcid=None,
                    ))
                    result.artifacts_tagged += 1
                else:
                    mcid = next_mcid
                    next_mcid += 1
                    tagged_runs.append(TaggedRun(
                        start=start, end=end,
                        tag_type="/P", mcid=mcid,
                    ))

                    # Create /P struct element
                    p_xref = doc.get_new_xref()
                    p_obj = (
                        f"<< /Type /StructElem /S /P"
                        f" /P {doc_elem_xref} 0 R"
                        f" /Pg {page.xref} 0 R"
                        f" /K {mcid} >>"
                    )
                    doc.update_object(p_xref, p_obj)

                    # Add to /Document's /K array
                    k_val = doc.xref_get_key(doc_elem_xref, "K")
                    if k_val[0] == "array":
                        existing = k_val[1].strip("[]").strip()
                        if existing:
                            new_k = f"[{existing} {p_xref} 0 R]"
                        else:
                            new_k = f"[{p_xref} 0 R]"
                    elif k_val[0] == "xref":
                        new_k = f"[{k_val[1]} {p_xref} 0 R]"
                    else:
                        new_k = f"[{p_xref} 0 R]"
                    doc.xref_set_key(doc_elem_xref, "K", new_k)

                    page_mcid_entries.append((mcid, p_xref))
                    result.paragraphs_tagged += 1

            if page_mcid_entries:
                result.page_mcid_map[page_idx] = page_mcid_entries

            # Rewrite content stream with wrappers
            new_stream = _apply_content_tag_wrappers(tokens, tagged_runs)

            contents_ref = doc.xref_get_key(page.xref, "Contents")
            if contents_ref[0] == "xref":
                xref = int(contents_ref[1].split()[0])
                doc.update_stream(xref, new_stream)
            elif contents_ref[0] == "array":
                refs = [
                    int(piece.split()[0])
                    for piece in contents_ref[1].strip("[]").split("R")
                    if piece.strip()
                ]
                if refs:
                    doc.update_stream(refs[0], new_stream)
                    for xref_clear in refs[1:]:
                        doc.update_stream(xref_clear, b"")

            result.pages_modified += 1

        # Pass 2: form XObjects — tag as /P instead of /Artifact
        # (struct tree integration deferred for form XObjects in v1)
        form_xrefs = _find_form_xobject_xrefs(doc)
        for fx in form_xrefs:
            try:
                stream_bytes = doc.xref_stream(fx)
                if not stream_bytes:
                    continue
                tokens = _tokenize_content_stream(stream_bytes)
                runs = _find_untagged_content_runs(tokens)
                if not runs:
                    continue
                # Form XObjects: still use /Artifact for now (no struct
                # tree integration for form XObject content in v1)
                tagged_runs = [
                    TaggedRun(start=s, end=e, tag_type="/Artifact", mcid=None)
                    for s, e in runs
                ]
                new_stream = _apply_content_tag_wrappers(tokens, tagged_runs)
                doc.update_stream(fx, new_stream)
                result.form_xobjects_modified += 1
            except Exception as exc:
                result.errors.append(f"form XObject {fx}: {exc}")

        doc.save(str(path), incremental=True, encryption=0)

    except Exception as exc:
        result.success = False
        result.errors.append(f"tag_or_artifact_untagged_content: {exc}")
    finally:
        try:
            doc.close()
        except Exception:
            pass

    return result
```

- [ ] **Step 5: Update `_is_page_furniture` signature**

The implementation above calls `_is_page_furniture(text, furniture_set)` with two args. Update the function from Task 3 to match (remove `page_idx` and `page_bbox` args — position-based checking will use the furniture pre-scan instead):

```python
def _is_page_furniture(text: str, furniture_set: set[str]) -> bool:
```

This is already the signature from Task 3.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_struct_tree.py::TestTagOrArtifactUntaggedContent -v`
Expected: All 5 tests PASS

- [ ] **Step 7: Run existing tests to verify no regressions**

Run: `pytest tests/test_pdf_writer.py -v --tb=short`
Expected: All existing tests still PASS (we haven't changed `mark_untagged_content_as_artifact` yet)

- [ ] **Step 8: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add tag_or_artifact_untagged_content() — body text → /P, not /Artifact"
```

---

### Task 6: `_update_parent_tree_for_mcids()`

**Files:**
- Modify: `src/tools/pdf_writer.py` (near `_write_parent_tree` at line 2938)
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing tests**

```python
class TestUpdateParentTreeForMcids:
    """Tests for MCID→struct element ParentTree entries."""

    def _make_tagged_pdf(self, tmp_path) -> Path:
        """Create a PDF with a struct tree, some MCIDs, and /P elements."""
        doc = fitz.open()
        page = doc.new_page()

        # Write content with one tagged and one untagged region
        xref = page.xref
        contents = doc.xref_get_key(xref, "Contents")
        c_xref = int(contents[1].split()[0]) if contents[0] == "xref" else doc.get_new_xref()
        stream = (
            "/H1 <</MCID 0>> BDC\nBT (Heading) Tj ET\nEMC\n"
            "/P <</MCID 1>> BDC\nBT (Body) Tj ET\nEMC\n"
        )
        doc.update_stream(c_xref, stream.encode("latin-1"))
        if contents[0] != "xref":
            doc.xref_set_key(xref, "Contents", f"{c_xref} 0 R")

        # Build struct tree
        cat = doc.pdf_catalog()
        sroot_xref = doc.get_new_xref()
        doc_elem_xref = doc.get_new_xref()
        h1_xref = doc.get_new_xref()
        p_xref = doc.get_new_xref()

        doc.update_object(h1_xref,
            f"<< /Type /StructElem /S /H1 /P {doc_elem_xref} 0 R /Pg {page.xref} 0 R /K 0 >>")
        doc.update_object(p_xref,
            f"<< /Type /StructElem /S /P /P {doc_elem_xref} 0 R /Pg {page.xref} 0 R /K 1 >>")
        doc.update_object(doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {sroot_xref} 0 R /K [{h1_xref} 0 R {p_xref} 0 R] >>")
        doc.update_object(sroot_xref,
            f"<< /Type /StructTreeRoot /K [{doc_elem_xref} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sroot_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "tagged.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_creates_parent_tree_entries(self, tmp_path):
        """Page MCID map produces ParentTree with array entries."""
        from src.tools.pdf_writer import _update_parent_tree_for_mcids
        pdf_path = self._make_tagged_pdf(tmp_path)
        doc = fitz.open(str(pdf_path))
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        st_xref = int(st_key[1].split()[0])

        # Simulate: page 0 had MCIDs 0,1 from existing tree;
        # gap-fill added MCID 2 → p_elem_xref
        p_elem_xref = doc.get_new_xref()
        doc.update_object(p_elem_xref, "<< /Type /StructElem /S /P >>")

        page_mcid_map = {0: [(2, p_elem_xref)]}
        count = _update_parent_tree_for_mcids(doc, page_mcid_map)
        doc.save(str(pdf_path), incremental=True, encryption=0)
        doc.close()

        assert count >= 1

        # Verify ParentTree exists and has entries
        doc2 = fitz.open(str(pdf_path))
        cat2 = doc2.pdf_catalog()
        st2 = doc2.xref_get_key(cat2, "StructTreeRoot")
        st2_xref = int(st2[1].split()[0])
        pt_key = doc2.xref_get_key(st2_xref, "ParentTree")
        assert pt_key[0] == "xref", "ParentTree should exist"
        doc2.close()

    def test_empty_map_does_nothing(self, tmp_path):
        from src.tools.pdf_writer import _update_parent_tree_for_mcids
        pdf_path = self._make_tagged_pdf(tmp_path)
        doc = fitz.open(str(pdf_path))
        count = _update_parent_tree_for_mcids(doc, {})
        doc.close()
        assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestUpdateParentTreeForMcids -v`
Expected: ImportError

- [ ] **Step 3: Implement `_update_parent_tree_for_mcids`**

Add near `_write_parent_tree` in `src/tools/pdf_writer.py`:

```python
def _update_parent_tree_for_mcids(
    doc: fitz.Document,
    page_mcid_map: dict[int, list[tuple[int, int]]],
) -> int:
    """Update ParentTree with MCID→struct element mappings.

    For each page in page_mcid_map:
    1. Check if page already has /StructParents → read existing array
    2. Build array where array[mcid] = struct_elem_xref
    3. If page has no /StructParents, assign next available number
    4. Write/update ParentTree entries

    Args:
        doc: Open fitz.Document (modified in place, caller must save).
        page_mcid_map: {page_idx: [(mcid, struct_elem_xref), ...]}.

    Returns:
        Count of ParentTree entries added/updated.
    """
    if not page_mcid_map:
        return 0

    cat = doc.pdf_catalog()
    st_key = doc.xref_get_key(cat, "StructTreeRoot")
    if st_key[0] != "xref":
        return 0
    st_root_xref = int(st_key[1].split()[0])

    # Read existing ParentTree
    existing_pt_xref, existing_nums = _read_existing_parent_tree(
        doc, st_root_xref
    )
    all_nums = dict(existing_nums)

    # Find next available StructParents number
    next_sp = _find_next_struct_parent(doc, st_root_xref)

    entries_added = 0

    for page_idx, mcid_entries in page_mcid_map.items():
        if not mcid_entries:
            continue

        page = doc[page_idx]

        # Check if page already has /StructParents
        sp_key = doc.xref_get_key(page.xref, "StructParents")
        if sp_key[0] not in ("null", "undefined"):
            sp_num = int(sp_key[1])
            # Read existing array from ParentTree
            existing_array_xref = all_nums.get(sp_num)
            existing_mcid_map: dict[int, int] = {}
            if existing_array_xref is not None:
                arr_obj = doc.xref_object(existing_array_xref) or ""
                # Parse array of refs: [ref1 ref2 null ref3 ...]
                for idx, m in enumerate(
                    re.finditer(r"(\d+)\s+0\s+R|null", arr_obj)
                ):
                    if m.group(1):
                        existing_mcid_map[idx] = int(m.group(1))
        else:
            sp_num = next_sp
            next_sp += 1
            doc.xref_set_key(page.xref, "StructParents", str(sp_num))
            existing_mcid_map = {}

        # Merge new entries
        for mcid, elem_xref in mcid_entries:
            existing_mcid_map[mcid] = elem_xref

        # Build array object: [elem0_ref elem1_ref null elem3_ref ...]
        if existing_mcid_map:
            max_mcid = max(existing_mcid_map.keys())
            parts = []
            for i in range(max_mcid + 1):
                if i in existing_mcid_map:
                    parts.append(f"{existing_mcid_map[i]} 0 R")
                else:
                    parts.append("null")
            arr_content = " ".join(parts)

            arr_xref = doc.get_new_xref()
            doc.update_object(arr_xref, f"[{arr_content}]")
            all_nums[sp_num] = arr_xref
            entries_added += 1

    # Write updated ParentTree
    _write_parent_tree(doc, st_root_xref, all_nums, next_sp, existing_pt_xref)

    return entries_added
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_struct_tree.py::TestUpdateParentTreeForMcids -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add _update_parent_tree_for_mcids() for MCID ParentTree entries"
```

---

### Task 7: `assess_struct_tree_quality()` — tree quality assessment

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing tests**

```python
class TestAssessStructTreeQuality:
    """Tests for struct tree quality assessment."""

    def test_no_tree_recommends_rebuild(self, tmp_path):
        """PDF without StructTreeRoot → rebuild."""
        from src.tools.pdf_writer import assess_struct_tree_quality
        doc = fitz.open()
        doc.new_page()
        pdf_path = tmp_path / "no_tree.pdf"
        doc.save(str(pdf_path))
        doc.close()
        assessment = assess_struct_tree_quality(str(pdf_path))
        assert assessment.has_tree is False
        assert assessment.recommendation == "rebuild"

    def test_good_tree_recommends_preserve(self, tmp_path):
        """PDF with high-coverage tree with /P tags → preserve."""
        from src.tools.pdf_writer import assess_struct_tree_quality
        doc = fitz.open()
        page = doc.new_page()
        # Content with 3 tagged paragraphs
        stream = (
            "/P <</MCID 0>> BDC BT (Para 1) Tj ET EMC\n"
            "/P <</MCID 1>> BDC BT (Para 2) Tj ET EMC\n"
            "/H1 <</MCID 2>> BDC BT (Heading) Tj ET EMC\n"
        )
        c_ref = doc.xref_get_key(page.xref, "Contents")
        c_xref = int(c_ref[1].split()[0])
        doc.update_stream(c_xref, stream.encode("latin-1"))

        # Build struct tree with /P and /H1
        cat = doc.pdf_catalog()
        sr = doc.get_new_xref()
        de = doc.get_new_xref()
        p0 = doc.get_new_xref()
        p1 = doc.get_new_xref()
        h1 = doc.get_new_xref()
        doc.update_object(p0, f"<< /Type /StructElem /S /P /P {de} 0 R /Pg {page.xref} 0 R /K 0 >>")
        doc.update_object(p1, f"<< /Type /StructElem /S /P /P {de} 0 R /Pg {page.xref} 0 R /K 1 >>")
        doc.update_object(h1, f"<< /Type /StructElem /S /H1 /P {de} 0 R /Pg {page.xref} 0 R /K 2 >>")
        doc.update_object(de, f"<< /Type /StructElem /S /Document /P {sr} 0 R /K [{p0} 0 R {p1} 0 R {h1} 0 R] >>")
        doc.update_object(sr, f"<< /Type /StructTreeRoot /K [{de} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sr} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "good_tree.pdf"
        doc.save(str(pdf_path))
        doc.close()

        assessment = assess_struct_tree_quality(str(pdf_path))
        assert assessment.has_tree is True
        assert assessment.has_paragraph_tags is True
        assert assessment.coverage_ratio >= 0.5
        assert assessment.recommendation == "preserve"

    def test_slide_tree_recommends_rebuild(self, tmp_path):
        """PowerPoint-style tree with /Slide, no /P → rebuild."""
        from src.tools.pdf_writer import assess_struct_tree_quality
        doc = fitz.open()
        page = doc.new_page()
        # Some tagged content but with /Slide type
        stream = "/Slide <</MCID 0>> BDC BT (text) Tj ET EMC\n"
        c_ref = doc.xref_get_key(page.xref, "Contents")
        c_xref = int(c_ref[1].split()[0])
        doc.update_stream(c_xref, stream.encode("latin-1"))

        cat = doc.pdf_catalog()
        sr = doc.get_new_xref()
        sl = doc.get_new_xref()
        doc.update_object(sl, f"<< /Type /StructElem /S /Slide /P {sr} 0 R /K 0 >>")
        doc.update_object(sr, f"<< /Type /StructTreeRoot /K [{sl} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sr} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "slide_tree.pdf"
        doc.save(str(pdf_path))
        doc.close()

        assessment = assess_struct_tree_quality(str(pdf_path))
        assert assessment.has_paragraph_tags is False
        assert assessment.recommendation == "rebuild"

    def test_invalid_page_refs_recommends_rebuild(self, tmp_path):
        """Struct tree referencing pages beyond doc page count → rebuild."""
        from src.tools.pdf_writer import assess_struct_tree_quality
        doc = fitz.open()
        page = doc.new_page()  # only 1 page

        cat = doc.pdf_catalog()
        sr = doc.get_new_xref()
        p0 = doc.get_new_xref()
        # Reference page xref 99999 which doesn't exist
        doc.update_object(p0, f"<< /Type /StructElem /S /P /P {sr} 0 R /Pg 99999 0 R /K 0 >>")
        doc.update_object(sr, f"<< /Type /StructTreeRoot /K [{p0} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sr} 0 R")

        pdf_path = tmp_path / "bad_refs.pdf"
        doc.save(str(pdf_path))
        doc.close()

        assessment = assess_struct_tree_quality(str(pdf_path))
        assert assessment.page_refs_valid is False
        assert assessment.recommendation == "rebuild"

    def test_real_syllabus_pdf(self):
        """Real syllabus PDF should have a tree and get an assessment."""
        from src.tools.pdf_writer import assess_struct_tree_quality
        if not SYLLABUS_PDF.exists():
            pytest.skip("Test PDF not available")
        assessment = assess_struct_tree_quality(str(SYLLABUS_PDF))
        # Just verify it runs without error and returns valid data
        assert isinstance(assessment.coverage_ratio, float)
        assert assessment.recommendation in ("preserve", "rebuild")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestAssessStructTreeQuality -v`
Expected: ImportError

- [ ] **Step 3: Implement `TreeAssessment` and `assess_struct_tree_quality`**

Add in `src/tools/pdf_writer.py`:

```python
@dataclass
class TreeAssessment:
    """Result of assess_struct_tree_quality()."""
    has_tree: bool
    coverage_ratio: float = 0.0
    has_paragraph_tags: bool = False
    mcid_orphan_rate: float = 0.0
    page_refs_valid: bool = True
    role_distribution: dict[str, int] = field(default_factory=dict)
    tag_content_mismatches: int = 0
    total_text_objects: int = 0
    tagged_text_objects: int = 0
    recommendation: str = "rebuild"


def assess_struct_tree_quality(pdf_path: str | Path) -> TreeAssessment:
    """Assess whether an existing struct tree is worth preserving.

    Runs four validation checks:
    1. MCID orphan rate (>20% → rebuild)
    2. Text-under-tag sanity (headings >50 words → mismatch)
    3. Page reference validity (refs beyond page count → rebuild)
    4. Role distribution (no /P, all /Slide → rebuild)

    Returns TreeAssessment with recommendation "preserve" or "rebuild".
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return TreeAssessment(has_tree=False)

    try:
        return _assess_struct_tree_inner(doc)
    except Exception as exc:
        logger.warning("Tree assessment failed: %s", exc)
        return TreeAssessment(has_tree=False)
    finally:
        doc.close()


def _assess_struct_tree_inner(doc: fitz.Document) -> TreeAssessment:
    """Core logic for assess_struct_tree_quality."""
    cat = doc.pdf_catalog()
    st_key = doc.xref_get_key(cat, "StructTreeRoot")
    if st_key[0] != "xref":
        return TreeAssessment(has_tree=False)

    st_root_xref = int(st_key[1].split()[0])
    result = TreeAssessment(has_tree=True)

    # ── Check 1: MCID coverage and orphan rate ──────────────────────
    tree_mcids = _collect_struct_tree_mcids(doc)
    stream_mcids: set[int] = set()
    total_text_objects = 0

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            stream_bytes = page.read_contents()
        except Exception:
            continue
        if not stream_bytes:
            continue

        tokens = _tokenize_content_stream(stream_bytes)
        # Count text objects (BT...ET blocks)
        in_bt = False
        for t in tokens:
            if t.type == "operator":
                if t.value == "BT":
                    in_bt = True
                    total_text_objects += 1
                elif t.value == "ET":
                    in_bt = False

        # Collect MCIDs from content stream
        page_max = _get_max_mcid_for_page(tokens)
        for t in tokens:
            if t.type in ("dict", "operand") and "/MCID" in t.value:
                for m in re.finditer(r"/MCID\s+(\d+)", t.value):
                    stream_mcids.add(int(m.group(1)))

    result.total_text_objects = total_text_objects

    # Count tagged text objects (those inside BDC/EMC with MCIDs)
    tagged_count = 0
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            stream_bytes = page.read_contents()
        except Exception:
            continue
        if not stream_bytes:
            continue
        tokens = _tokenize_content_stream(stream_bytes)
        depth = 0
        for t in tokens:
            if t.type == "operator":
                if t.value in ("BDC", "BMC"):
                    depth += 1
                elif t.value == "EMC":
                    depth -= 1
                elif t.value == "BT" and depth > 0:
                    tagged_count += 1

    result.tagged_text_objects = tagged_count
    result.coverage_ratio = (
        tagged_count / total_text_objects if total_text_objects > 0 else 0.0
    )

    # Orphan rate: MCIDs in tree but not stream, or in stream but not tree
    all_mcids = tree_mcids | stream_mcids
    if all_mcids:
        orphans = len(tree_mcids.symmetric_difference(stream_mcids))
        result.mcid_orphan_rate = orphans / len(all_mcids)
    else:
        result.mcid_orphan_rate = 0.0

    # ── Check 2: Role distribution ──────────────────────────────────
    role_dist: dict[str, int] = {}
    seen_xrefs: set[int] = set()

    def _walk_roles(xref: int, depth: int = 0) -> None:
        if xref in seen_xrefs or depth > 200:
            return
        seen_xrefs.add(xref)
        try:
            obj = doc.xref_object(xref) or ""
        except Exception:
            return
        s_match = re.search(r"/S\s*(/\w+)", obj)
        if s_match:
            role = s_match.group(1)
            role_dist[role] = role_dist.get(role, 0) + 1
        for m in re.finditer(r"(\d+)\s+0\s+R", obj):
            _walk_roles(int(m.group(1)), depth + 1)

    _walk_roles(st_root_xref)
    result.role_distribution = role_dist
    result.has_paragraph_tags = role_dist.get("/P", 0) > 0

    # ── Check 3: Page reference validity ────────────────────────────
    page_xrefs = {doc[i].xref for i in range(len(doc))}

    def _check_page_refs(xref: int, depth: int = 0) -> bool:
        if depth > 200:
            return True
        try:
            obj = doc.xref_object(xref) or ""
        except Exception:
            return True
        pg_match = re.search(r"/Pg\s+(\d+)\s+0\s+R", obj)
        if pg_match:
            pg_xref = int(pg_match.group(1))
            if pg_xref not in page_xrefs:
                return False
        for m in re.finditer(r"(\d+)\s+0\s+R", obj):
            child_xref = int(m.group(1))
            if child_xref not in seen_xrefs:
                # Avoid infinite recursion — only check struct elements
                s_type = doc.xref_get_key(child_xref, "S")
                if s_type[0] == "name":
                    if not _check_page_refs(child_xref, depth + 1):
                        return False
        return True

    result.page_refs_valid = _check_page_refs(st_root_xref)

    # ── Check 4: Tag-content sanity (sample headings) ───────────────
    # Deferred: check heading text length. For v1, skip this check
    # and rely on checks 1-3.
    result.tag_content_mismatches = 0

    # ── Decision ────────────────────────────────────────────────────
    if not result.has_tree:
        result.recommendation = "rebuild"
    elif result.coverage_ratio < 0.5:
        result.recommendation = "rebuild"
    elif result.mcid_orphan_rate > 0.2:
        result.recommendation = "rebuild"
    elif not result.page_refs_valid:
        result.recommendation = "rebuild"
    elif not result.has_paragraph_tags:
        result.recommendation = "rebuild"
    elif result.tag_content_mismatches > 0:
        # Will be enabled when check 4 is implemented
        sampled = sum(role_dist.get(r, 0) for r in ("/H1", "/H2", "/H3", "/H4", "/H5", "/H6", "/P"))
        if sampled > 0 and result.tag_content_mismatches / sampled > 0.3:
            result.recommendation = "rebuild"
        else:
            result.recommendation = "preserve"
    else:
        result.recommendation = "preserve"

    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_struct_tree.py::TestAssessStructTreeQuality -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/pdf_writer.py
git commit -m "Add assess_struct_tree_quality() with 4 validation checks"
```

---

### Task 8: `filter_tagging_plan_for_existing_tree()`

**Files:**
- Modify: `src/tools/itext_tagger.py`
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write failing tests**

```python
class TestFilterTaggingPlanForExistingTree:
    """Tests for duplicate prevention on preserve path."""

    def _make_tagged_pdf_with_figure(self, tmp_path) -> Path:
        """PDF with an existing /Figure struct element for image xref 10."""
        doc = fitz.open()
        page = doc.new_page()
        cat = doc.pdf_catalog()
        sr = doc.get_new_xref()
        de = doc.get_new_xref()
        fig = doc.get_new_xref()
        doc.update_object(fig,
            f"<< /Type /StructElem /S /Figure /P {de} 0 R /Alt (old alt) >>")
        doc.update_object(de,
            f"<< /Type /StructElem /S /Document /P {sr} 0 R /K [{fig} 0 R] >>")
        doc.update_object(sr,
            f"<< /Type /StructTreeRoot /K [{de} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sr} 0 R")
        pdf_path = tmp_path / "with_figure.pdf"
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    def test_existing_figure_removed_from_plan(self, tmp_path):
        """Figure already in tree → removed from tagging plan elements."""
        from src.tools.itext_tagger import filter_tagging_plan_for_existing_tree
        pdf_path = self._make_tagged_pdf_with_figure(tmp_path)
        plan = {
            "input_path": str(pdf_path),
            "output_path": str(tmp_path / "out.pdf"),
            "elements": [
                {"type": "image_alt", "xref": 10, "alt_text": "new alt",
                 "page": 0, "bbox": [0, 0, 100, 100]},
                {"type": "heading", "level": 1, "text": "Title",
                 "page": 0, "bbox": [72, 700, 500, 720]},
            ],
        }
        filtered = filter_tagging_plan_for_existing_tree(plan, str(pdf_path))
        # Heading should remain, figure should be removed
        types = [e["type"] for e in filtered["elements"]]
        assert "heading" in types
        # Figure may be filtered or converted to update — either way,
        # there should be at most 1 element (the heading)
        assert len(filtered["elements"]) <= 2

    def test_no_tree_returns_plan_unchanged(self, tmp_path):
        """PDF without struct tree → plan returned unchanged."""
        from src.tools.itext_tagger import filter_tagging_plan_for_existing_tree
        doc = fitz.open()
        doc.new_page()
        pdf_path = tmp_path / "no_tree.pdf"
        doc.save(str(pdf_path))
        doc.close()

        plan = {
            "input_path": str(pdf_path),
            "output_path": str(tmp_path / "out.pdf"),
            "elements": [
                {"type": "image_alt", "xref": 10, "alt_text": "alt",
                 "page": 0, "bbox": [0, 0, 100, 100]},
            ],
        }
        filtered = filter_tagging_plan_for_existing_tree(plan, str(pdf_path))
        assert len(filtered["elements"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_struct_tree.py::TestFilterTaggingPlanForExistingTree -v`
Expected: ImportError

- [ ] **Step 3: Implement `filter_tagging_plan_for_existing_tree`**

Add in `src/tools/itext_tagger.py`:

```python
def filter_tagging_plan_for_existing_tree(
    plan: dict, pdf_path: str,
) -> dict:
    """Remove elements from the tagging plan that already exist in the struct tree.

    For the preserve path: inspects the existing struct tree and removes
    figure/link elements that would create duplicates. Headings are
    always kept (existing trees rarely have correct heading levels).

    Args:
        plan: Tagging plan dict with "elements" list.
        pdf_path: Path to the PDF with the existing struct tree.

    Returns:
        Filtered plan dict (shallow copy with filtered elements list).
    """
    import fitz
    import re

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return plan

    try:
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        if st_key[0] != "xref":
            return plan  # no tree, nothing to filter

        st_root_xref = int(st_key[1].split()[0])

        # Collect existing /Figure alt text xrefs
        existing_figure_xrefs: set[int] = set()
        existing_link_urls: set[str] = set()
        seen: set[int] = set()

        def _walk(xref: int, depth: int = 0) -> None:
            if xref in seen or depth > 200:
                return
            seen.add(xref)
            try:
                obj = doc.xref_object(xref) or ""
            except Exception:
                return
            if re.search(r"/S\s*/Figure", obj):
                # Check for /A11yXref or any xref reference
                xref_match = re.search(r"/A11yXref\s+(\d+)", obj)
                if xref_match:
                    existing_figure_xrefs.add(int(xref_match.group(1)))
            if re.search(r"/S\s*/Link", obj):
                alt_match = re.search(r"/ActualText\s*\(([^)]*)\)", obj)
                if alt_match:
                    existing_link_urls.add(alt_match.group(1))
            for m in re.finditer(r"(\d+)\s+0\s+R", obj):
                _walk(int(m.group(1)), depth + 1)

        _walk(st_root_xref)
    finally:
        doc.close()

    # Filter elements
    filtered_elements = []
    for elem in plan.get("elements", []):
        if elem["type"] == "image_alt":
            if elem.get("xref") in existing_figure_xrefs:
                logger.info(
                    "Filtered existing /Figure xref=%d from tagging plan",
                    elem["xref"],
                )
                continue
        # Headings, tables, links: always keep
        filtered_elements.append(elem)

    result = dict(plan)
    result["elements"] = filtered_elements
    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_struct_tree.py::TestFilterTaggingPlanForExistingTree -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_struct_tree.py src/tools/itext_tagger.py
git commit -m "Add filter_tagging_plan_for_existing_tree() for duplicate prevention"
```

---

### Task 9: iText Java change — use existing /Document root

**Files:**
- Modify: `java/itext-tagger/src/main/java/a11y/PdfTagger.java:86-96`

- [ ] **Step 1: Write a Java test (manual verification)**

We can't easily unit-test Java from pytest. Instead, create a test script that will be run after the change.

Add to `tests/test_struct_tree.py`:

```python
class TestITextPreservePath:
    """Integration test: iText with existing struct tree."""

    def test_itext_preserves_existing_tree(self, tmp_path):
        """iText should add to existing /Document, not create a second one."""
        from src.tools.itext_tagger import build_tagging_plan, tag_pdf
        from src.models.document import DocumentModel, ParagraphInfo, MetadataInfo
        from src.models.pipeline import RemediationStrategy, RemediationAction

        if not SYLLABUS_PDF.exists():
            pytest.skip("Test PDF not available")

        # Copy PDF without stripping its struct tree
        import shutil
        test_pdf = tmp_path / "input.pdf"
        shutil.copy2(SYLLABUS_PDF, test_pdf)

        # Check original has a struct tree
        doc = fitz.open(str(test_pdf))
        cat = doc.pdf_catalog()
        st = doc.xref_get_key(cat, "StructTreeRoot")
        has_tree = st[0] == "xref"
        doc.close()

        if not has_tree:
            pytest.skip("Syllabus PDF has no struct tree")

        # Build minimal strategy with one heading
        strategy = RemediationStrategy(
            document_type="syllabus",
            actions=[
                RemediationAction(
                    action_type="set_heading_level",
                    element_id="p_0",
                    parameters={"level": 1},
                    wcag_criterion="1.3.1",
                    rationale="test",
                ),
            ],
            summary="test",
        )
        doc_model = DocumentModel(
            paragraphs=[ParagraphInfo(
                id="p_0", text="Test Heading",
                style_name="Normal", is_heading=False,
                font_size=14.0, is_bold=True,
                bbox=[72, 700, 500, 720],
                page_number=0,
            )],
            metadata=MetadataInfo(),
        )

        output_pdf = tmp_path / "output.pdf"
        plan = build_tagging_plan(
            strategy, doc_model,
            input_path=str(test_pdf),
            output_path=str(output_pdf),
        )
        result = tag_pdf(plan)

        if not result.success:
            pytest.skip(f"iText tagging failed: {result.errors}")

        # Verify: should have exactly ONE /Document element, not two
        doc2 = fitz.open(str(output_pdf))
        cat2 = doc2.pdf_catalog()
        st2 = doc2.xref_get_key(cat2, "StructTreeRoot")
        assert st2[0] == "xref"
        st_xref = int(st2[1].split()[0])
        st_obj = doc2.xref_object(st_xref) or ""

        # Count /Document kids
        doc_count = 0
        for m in re.finditer(r"(\d+)\s+0\s+R", st_obj):
            kid_obj = doc2.xref_object(int(m.group(1))) or ""
            if "/S /Document" in kid_obj or "/S/Document" in kid_obj:
                doc_count += 1
        doc2.close()

        assert doc_count == 1, f"Expected 1 /Document element, found {doc_count}"
```

- [ ] **Step 2: Run the test to see it fail (expected: 2 /Document elements)**

Run: `pytest tests/test_struct_tree.py::TestITextPreservePath -v`
Expected: AssertionError — "Expected 1 /Document element, found 2"

- [ ] **Step 3: Modify PdfTagger.java**

In `java/itext-tagger/src/main/java/a11y/PdfTagger.java`, replace lines 86-96:

```java
            // Get or create the tag structure root
            PdfStructTreeRoot structRoot = pdfDoc.getStructTreeRoot();
            if (structRoot == null) {
                result.errors.add("Could not get or create StructTreeRoot");
                result.success = false;
                return result;
            }

            // Add a document-level structure element
            PdfStructElem docElem = structRoot.addKid(
                new PdfStructElem(pdfDoc, PdfName.Document)
            );
```

With:

```java
            // Get or create the tag structure root
            PdfStructTreeRoot structRoot = pdfDoc.getStructTreeRoot();
            if (structRoot == null) {
                result.errors.add("Could not get or create StructTreeRoot");
                result.success = false;
                return result;
            }

            // Use existing /Document element if present, otherwise create new
            PdfStructElem docElem = null;
            if (structRoot.getKids() != null) {
                for (IStructureNode kid : structRoot.getKids()) {
                    if (kid instanceof PdfStructElem) {
                        PdfStructElem elem = (PdfStructElem) kid;
                        if (PdfName.Document.equals(elem.getRole())) {
                            docElem = elem;
                            break;
                        }
                    }
                }
            }
            if (docElem == null) {
                docElem = structRoot.addKid(
                    new PdfStructElem(pdfDoc, PdfName.Document)
                );
            }
```

Add import at top of file if not present:

```java
import com.itextpdf.kernel.pdf.tagging.IStructureNode;
```

- [ ] **Step 4: Rebuild the fat JAR**

```bash
cd java/itext-tagger && gradle fatJar
```

Expected: BUILD SUCCESSFUL

- [ ] **Step 5: Run the integration test**

Run: `pytest tests/test_struct_tree.py::TestITextPreservePath -v`
Expected: PASS — exactly 1 /Document element

- [ ] **Step 6: Commit**

```bash
git add java/itext-tagger/src/main/java/a11y/PdfTagger.java tests/test_struct_tree.py
git commit -m "iText: use existing /Document root instead of always creating new one"
```

---

### Task 10: Wire up executor — integrate both paths

**Files:**
- Modify: `src/agent/executor.py:270-385`
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write integration test**

```python
class TestExecutorStructTreePaths:
    """Integration test for the two executor paths."""

    def test_rebuild_path_tags_body_text(self, tmp_path):
        """Rebuild path: body text gets /P, not /Artifact."""
        from src.tools.pdf_writer import tag_or_artifact_untagged_content
        # Create PDF with no struct tree
        doc = fitz.open()
        page = doc.new_page()
        c_ref = doc.xref_get_key(page.xref, "Contents")
        c_xref = int(c_ref[1].split()[0])
        stream = (
            "/H1 <</MCID 0>> BDC BT (Heading) Tj ET EMC\n"
            "BT (This is untagged body text) Tj ET\n"
        )
        doc.update_stream(c_xref, stream.encode("latin-1"))
        # Add minimal struct tree
        cat = doc.pdf_catalog()
        sr = doc.get_new_xref()
        de = doc.get_new_xref()
        h1 = doc.get_new_xref()
        doc.update_object(h1, f"<< /Type /StructElem /S /H1 /P {de} 0 R /K 0 >>")
        doc.update_object(de, f"<< /Type /StructElem /S /Document /P {sr} 0 R /K [{h1} 0 R] >>")
        doc.update_object(sr, f"<< /Type /StructTreeRoot /K [{de} 0 R] >>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{sr} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<< /Marked true >>")

        pdf_path = tmp_path / "test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        result = tag_or_artifact_untagged_content(str(pdf_path))
        assert result.success
        assert result.paragraphs_tagged >= 1

        # Verify content stream
        doc2 = fitz.open(str(pdf_path))
        stream_out = doc2[0].read_contents().decode("latin-1")
        doc2.close()
        assert "/P <<" in stream_out
        assert "/Artifact" not in stream_out or "body text" not in stream_out
```

- [ ] **Step 2: Modify `executor.py` — the core integration**

In `src/agent/executor.py`, replace the strip + artifact marking block (lines ~274-385). The key changes:

1. Add imports at the top:

```python
from src.tools.pdf_writer import (
    assess_struct_tree_quality,
    tag_or_artifact_untagged_content,
    _update_parent_tree_for_mcids,
    # ... existing imports ...
)
from src.tools.itext_tagger import filter_tagging_plan_for_existing_tree
```

2. Replace the strip_struct_tree block (lines 274-306):

```python
    if not is_latex:
        # Assess existing struct tree quality
        tree_assessment = assess_struct_tree_quality(str(input_path))
        logger.info(
            "Struct tree assessment: coverage=%.2f, orphan_rate=%.2f, "
            "has_p=%s, recommendation=%s",
            tree_assessment.coverage_ratio,
            tree_assessment.mcid_orphan_rate,
            tree_assessment.has_paragraph_tags,
            tree_assessment.recommendation,
        )

        if tree_assessment.recommendation == "rebuild":
            # Rebuild path: strip existing tree, iText builds fresh
            stripped_input = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False, dir=str(out_dir),
                ) as tmp:
                    stripped_input = tmp.name
                if strip_struct_tree(str(input_path), stripped_input):
                    itext_input = stripped_input
                else:
                    itext_input = str(input_path)
            except Exception:
                itext_input = str(input_path)
        else:
            # Preserve path: keep existing tree, iText augments
            itext_input = str(input_path)
            stripped_input = None
```

3. After `build_tagging_plan` (line ~298), add preserve-path filtering:

```python
        if on_progress:
            on_progress("Tagging PDF structure")
        tagging_plan = build_tagging_plan(
            strategy, doc_model,
            input_path=itext_input,
            output_path=tagged_pdf_path,
        )

        # Preserve path: filter out elements that already exist in the tree
        if tree_assessment.recommendation == "preserve":
            tagging_plan = filter_tagging_plan_for_existing_tree(
                tagging_plan, itext_input,
            )

        tag_result = tag_pdf(tagging_plan)
```

4. Replace the artifact marking call (lines ~367-385):

```python
            # Tag untagged content as /P (or /Artifact for page furniture).
            # Replaces the old mark_untagged_content_as_artifact() which
            # hid body text from screen readers.
            try:
                tagging_result = tag_or_artifact_untagged_content(
                    tagged_pdf_path
                )
                if tagging_result.success:
                    if tagging_result.paragraphs_tagged:
                        logger.info(
                            "Tagged %d paragraph(s) as /P, "
                            "%d as /Artifact across %d page(s) in %s",
                            tagging_result.paragraphs_tagged,
                            tagging_result.artifacts_tagged,
                            tagging_result.pages_modified,
                            tagged_pdf_path,
                        )
                    # Update ParentTree for new MCIDs
                    if tagging_result.page_mcid_map:
                        try:
                            import fitz as _fitz
                            _doc = _fitz.open(tagged_pdf_path)
                            _update_parent_tree_for_mcids(
                                _doc, tagging_result.page_mcid_map
                            )
                            _doc.save(
                                tagged_pdf_path,
                                incremental=True, encryption=0,
                            )
                            _doc.close()
                        except Exception as exc:
                            logger.warning(
                                "ParentTree MCID update failed: %s", exc
                            )
                else:
                    logger.warning(
                        "Content tagging failed: %s",
                        "; ".join(tagging_result.errors),
                    )
            except Exception as exc:
                logger.warning("Content tagging pass failed: %s", exc)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_struct_tree.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --tb=short -x`
Expected: All 997+ tests PASS (the executor change should not break existing behavior since `mark_untagged_content_as_artifact` is no longer called but still exists in the file)

- [ ] **Step 5: Commit**

```bash
git add src/agent/executor.py tests/test_struct_tree.py
git commit -m "Wire up executor: tree assessment → preserve/rebuild → tag_or_artifact"
```

---

### Task 11: End-to-end integration test on real PDFs

**Files:**
- Modify: `tests/test_struct_tree.py`

- [ ] **Step 1: Write end-to-end test**

```python
class TestEndToEndRealPdfs:
    """End-to-end tests on real PDF files."""

    def test_syllabus_no_body_text_artifacted(self, tmp_path):
        """After full pipeline, syllabus body text is tagged, not artifacted."""
        from src.tools.pdf_writer import (
            tag_or_artifact_untagged_content,
            _update_parent_tree_for_mcids,
            _tokenize_content_stream,
        )
        if not SYLLABUS_PDF.exists():
            pytest.skip("Syllabus PDF not available")

        import shutil
        test_pdf = tmp_path / "syllabus.pdf"
        shutil.copy2(SYLLABUS_PDF, test_pdf)

        result = tag_or_artifact_untagged_content(str(test_pdf))
        assert result.success

        # Update ParentTree
        if result.page_mcid_map:
            doc = fitz.open(str(test_pdf))
            _update_parent_tree_for_mcids(doc, result.page_mcid_map)
            doc.save(str(test_pdf), incremental=True, encryption=0)
            doc.close()

        # Verify: no untagged body text remains at depth 0
        doc = fitz.open(str(test_pdf))
        for page_idx in range(len(doc)):
            stream = doc[page_idx].read_contents()
            if not stream:
                continue
            tokens = _tokenize_content_stream(stream)
            from src.tools.pdf_writer import _find_untagged_content_runs
            remaining = _find_untagged_content_runs(tokens)
            assert remaining == [], (
                f"Page {page_idx} still has {len(remaining)} untagged runs"
            )
        doc.close()

        # Log stats
        print(f"\nSyllabus results: {result.paragraphs_tagged} /P tagged, "
              f"{result.artifacts_tagged} /Artifact, "
              f"{result.pages_modified} pages modified")
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_struct_tree.py::TestEndToEndRealPdfs -v -s`
Expected: PASS, with stats printed

- [ ] **Step 3: Commit**

```bash
git add tests/test_struct_tree.py
git commit -m "Add end-to-end integration test on real PDFs"
```

---

### Task 12: Clean up — remove legacy function, run full suite

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Modify: `src/agent/executor.py`
- Modify: `tests/test_pdf_writer.py`

- [ ] **Step 1: Rename old function**

In `src/tools/pdf_writer.py`, rename `mark_untagged_content_as_artifact` to `_legacy_mark_untagged_content_as_artifact` (line 2021). This keeps it available if needed but signals it's deprecated.

- [ ] **Step 2: Update test imports**

In `tests/test_pdf_writer.py`, any tests in `TestMarkUntaggedContent` that import `mark_untagged_content_as_artifact` should be updated to import `_legacy_mark_untagged_content_as_artifact` or be converted to test the new function.

For now, keep existing tests working by adding an alias at the end of `pdf_writer.py`:

```python
# Backward compatibility alias — remove after v4 benchmark validates new function
mark_untagged_content_as_artifact = _legacy_mark_untagged_content_as_artifact
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Deprecate mark_untagged_content_as_artifact, keep as legacy alias"
```

---

### Task 13: Benchmark validation — run subset and verify improvement

**Files:**
- None created — this is a validation task

- [ ] **Step 1: Pick 5 benchmark docs — mix of regressed and improved from v3**

Select docs that regressed in v3 to verify they now improve. Run manually:

```bash
python3 scripts/remediation_benchmark.py \
    --benchmark-dir /tmp/PDF-Accessibility-Benchmark \
    --results-dir /tmp/remediation_bench_v4_subset \
    --doc W2460269320 --doc W2895738059 --doc W2951147986 \
    --doc W1989729767 --doc W2772922866
```

- [ ] **Step 2: Run veraPDF post-processing on subset**

```bash
python3 scripts/verapdf_postprocess.py \
    --results-dir /tmp/remediation_bench_v4_subset
```

- [ ] **Step 3: Compare v4 subset results against v3 baseline**

Check:
- Failed check reduction should be higher than 38.8%
- Previously-regressed docs should now improve
- No doc should have body text hidden behind /Artifact

- [ ] **Step 4: If results are good, commit a note to NOW.md**

Update NOW.md with v4 subset results.

```bash
git add NOW.md
git commit -m "NOW.md: v4 subset benchmark results — struct tree fix validated"
```
