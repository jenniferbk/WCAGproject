# ToUnicode Ligature Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill missing ligature entries (`ff`, `ffi`, `ffl`, `fi`, `fl`) in existing `/ToUnicode` CMaps of subset fonts, so text extraction, screen readers, and search indexers receive correct Unicode for ligature glyphs.

**Architecture:** New function `fill_tounicode_ligature_gaps(pdf_path)` in `src/tools/pdf_writer.py` that walks fonts with `/Differences` arrays, identifies missing ligature entries in their `/ToUnicode` CMaps, and adds correct Unicode mappings. Wired into `src/agent/executor.py` PDF post-processing. Pure CMap edit, no font-program changes, no visual impact.

**Tech Stack:** Python 3.11+, PyMuPDF (fitz ≥1.23), pytest.

**Spec:** `docs/superpowers/specs/2026-04-14-tounicode-ligature-fill-design.md`

---

## File Structure

Files touched:

- **Modify:** `src/tools/pdf_writer.py` — add `LigatureFillResult` dataclass, `LIGATURE_TABLE` constant, helpers (`_parse_differences_array`, `_parse_tounicode_cmap`, `_serialize_tounicode_cmap`, `_is_pua_mapping`), and the main `fill_tounicode_ligature_gaps` function. Following the existing convention where post-processing tools live alongside each other in this file.
- **Modify:** `src/agent/executor.py` — wire the new function into the PDF post-processing pass.
- **Modify:** `tests/test_pdf_writer.py` — add `TestFillToUnicodeLigatureGaps` class with unit + E2E tests.

---

## Task 1: Ligature table + result dataclass

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_writer.py`:

```python
class TestFillToUnicodeLigatureGaps:
    """Tests for fill_tounicode_ligature_gaps()."""

    def test_ligature_table_has_expected_entries(self):
        from src.tools.pdf_writer import LIGATURE_TABLE
        assert LIGATURE_TABLE["ff"] == "\u0066\u0066"
        assert LIGATURE_TABLE["fi"] == "\u0066\u0069"
        assert LIGATURE_TABLE["fl"] == "\u0066\u006c"
        assert LIGATURE_TABLE["ffi"] == "\u0066\u0066\u0069"
        assert LIGATURE_TABLE["ffl"] == "\u0066\u0066\u006c"

    def test_result_dataclass_defaults(self):
        from src.tools.pdf_writer import LigatureFillResult
        r = LigatureFillResult(success=True)
        assert r.success is True
        assert r.fonts_scanned == 0
        assert r.fonts_modified == 0
        assert r.ligature_entries_added == 0
        assert r.fonts_skipped_no_encoding == 0
        assert r.fonts_skipped_parse_error == 0
        assert r.error == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v
```

Expected: both tests FAIL with `ImportError` on `LIGATURE_TABLE` / `LigatureFillResult`.

- [ ] **Step 3: Add the constant and dataclass**

Find a good insertion point in `src/tools/pdf_writer.py` — near the existing `LinkContentsResult` dataclass (around line 2980). Add:

```python
# Ligature glyph names (as they appear in /Encoding /Differences arrays of
# TeX-origin fonts) mapped to the Unicode sequence that should appear in
# /ToUnicode CMap entries. Keyed by canonical glyph name (no leading slash).
LIGATURE_TABLE: dict[str, str] = {
    "ff": "\u0066\u0066",
    "fi": "\u0066\u0069",
    "fl": "\u0066\u006c",
    "ffi": "\u0066\u0066\u0069",
    "ffl": "\u0066\u0066\u006c",
}


@dataclass
class LigatureFillResult:
    """Result of fill_tounicode_ligature_gaps()."""
    success: bool
    fonts_scanned: int = 0
    fonts_modified: int = 0
    ligature_entries_added: int = 0
    fonts_skipped_no_encoding: int = 0
    fonts_skipped_parse_error: int = 0
    error: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add LIGATURE_TABLE constant and LigatureFillResult dataclass"
```

---

## Task 2: `_parse_differences_array` helper

The `/Differences` array in a PDF `/Encoding` dict has format `[base_code /name1 /name2 ... base_code2 /nameN ...]`. Integer entries reset the current code; name entries consume consecutive codes.

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `TestFillToUnicodeLigatureGaps`:

```python
    def test_parse_differences_simple(self):
        from src.tools.pdf_writer import _parse_differences_array
        # /Differences [11 /ff /fi /fl]  → codes 11, 12, 13
        result = _parse_differences_array("[11 /ff /fi /fl]")
        assert result == {11: "ff", 12: "fi", 13: "fl"}

    def test_parse_differences_multiple_ranges(self):
        from src.tools.pdf_writer import _parse_differences_array
        # Two base-code jumps
        result = _parse_differences_array("[11 /ff /fi 20 /bullet]")
        assert result == {11: "ff", 12: "fi", 20: "bullet"}

    def test_parse_differences_extra_whitespace(self):
        from src.tools.pdf_writer import _parse_differences_array
        result = _parse_differences_array("[ 11  /ff   /fi  ]")
        assert result == {11: "ff", 12: "fi"}

    def test_parse_differences_empty(self):
        from src.tools.pdf_writer import _parse_differences_array
        assert _parse_differences_array("[]") == {}

    def test_parse_differences_malformed(self):
        from src.tools.pdf_writer import _parse_differences_array
        # No brackets — return empty, never raise
        assert _parse_differences_array("not an array") == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k parse_differences
```

Expected: 5 tests FAIL with `ImportError` on `_parse_differences_array`.

- [ ] **Step 3: Implement the helper**

Add to `src/tools/pdf_writer.py` near other private helpers (e.g. near `_extract_uri_from_annotation`):

```python
def _parse_differences_array(diffs_text: str) -> dict[int, str]:
    """Parse a PDF /Differences array string into {code: glyph_name}.

    The /Differences array format is:
        [ <int_code> /<name1> /<name2> ... <int_code2> /<nameN> ... ]

    Each integer resets the current code. Each subsequent name consumes
    one code, then increments. Returns empty dict on malformed input.
    """
    import re
    # Must be wrapped in brackets
    m = re.search(r"\[(.*)\]", diffs_text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1)
    # Tokenize: integers and /names
    tokens = re.findall(r"\d+|/[^\s/\[\]()<>]+", body)
    result: dict[int, str] = {}
    current_code: int | None = None
    for tok in tokens:
        if tok.startswith("/"):
            if current_code is None:
                continue  # name without a prior code; skip
            result[current_code] = tok[1:]  # strip leading slash
            current_code += 1
        else:
            try:
                current_code = int(tok)
            except ValueError:
                continue
    return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k parse_differences
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _parse_differences_array helper for /Encoding /Differences parsing"
```

---

## Task 3: `_parse_tounicode_cmap` helper

A `/ToUnicode` CMap is a PostScript-like stream with `beginbfchar` ... `endbfchar` blocks (and optionally `beginbfrange` ... `endbfrange`). Each `bfchar` line is `<HEX_CODE> <HEX_UNICODE>`.

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `TestFillToUnicodeLigatureGaps`:

```python
    def test_parse_tounicode_single_bfchar_block(self):
        from src.tools.pdf_writer import _parse_tounicode_cmap
        cmap = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
3 beginbfchar
<00> <FFFD>
<41> <0041>
<42> <0042>
endbfchar
endcmap"""
        header, entries = _parse_tounicode_cmap(cmap)
        assert entries == {0: "\ufffd", 0x41: "A", 0x42: "B"}
        assert b"begincmap" in header

    def test_parse_tounicode_multichar_unicode(self):
        from src.tools.pdf_writer import _parse_tounicode_cmap
        # Ligature already mapped to fi: <0C> <00660069>
        cmap = b"""begincmap
1 beginbfchar
<0C> <00660069>
endbfchar
endcmap"""
        _, entries = _parse_tounicode_cmap(cmap)
        assert entries == {0x0C: "fi"}

    def test_parse_tounicode_bfrange(self):
        from src.tools.pdf_writer import _parse_tounicode_cmap
        # <20> <7E> <0020>  means codes 0x20-0x7E map to U+0020 onward
        cmap = b"""begincmap
1 beginbfrange
<20> <22> <0020>
endbfrange
endcmap"""
        _, entries = _parse_tounicode_cmap(cmap)
        assert entries[0x20] == " "
        assert entries[0x21] == "!"
        assert entries[0x22] == '"'

    def test_parse_tounicode_empty(self):
        from src.tools.pdf_writer import _parse_tounicode_cmap
        _, entries = _parse_tounicode_cmap(b"begincmap endcmap")
        assert entries == {}

    def test_parse_tounicode_malformed(self):
        from src.tools.pdf_writer import _parse_tounicode_cmap
        # Returns empty entries, never raises
        _, entries = _parse_tounicode_cmap(b"\x00\x01\x02 garbage")
        assert entries == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k parse_tounicode
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the parser**

Add to `src/tools/pdf_writer.py`:

```python
def _parse_tounicode_cmap(stream_bytes: bytes) -> tuple[bytes, dict[int, str]]:
    """Parse a PDF /ToUnicode CMap stream into (header_bytes, entries).

    Returns:
        - header_bytes: everything up to and including `begincmap`
          (preserved verbatim on re-serialize)
        - entries: dict mapping character code (int) → Unicode string

    Both ``bfchar`` and ``bfrange`` blocks are parsed. Unicode values are
    decoded from UTF-16BE hex (PDF CMap convention). Unrecognized or
    malformed content returns an empty entries dict; never raises.
    """
    import re
    entries: dict[int, str] = {}
    header = stream_bytes
    try:
        text = stream_bytes.decode("latin-1", errors="replace")
    except Exception:
        return header, entries

    # Preserve header up to first "begincmap" if present
    hdr_match = re.search(r"begincmap", text)
    if hdr_match:
        header = stream_bytes[: hdr_match.end()]

    def _hex_to_unicode(hex_str: str) -> str:
        raw = bytes.fromhex(hex_str)
        # UTF-16BE; pad odd length (shouldn't happen but defensive)
        if len(raw) % 2 == 1:
            raw = raw + b"\x00"
        return raw.decode("utf-16-be", errors="replace")

    # bfchar blocks: `N beginbfchar ... endbfchar`
    for bfchar_block in re.finditer(
        r"beginbfchar(.*?)endbfchar", text, re.DOTALL
    ):
        for line in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", bfchar_block.group(1)
        ):
            try:
                code = int(line.group(1), 16)
                entries[code] = _hex_to_unicode(line.group(2))
            except (ValueError, UnicodeDecodeError):
                continue

    # bfrange blocks: `N beginbfrange ... endbfrange`
    # Format: <start> <end> <unicode_start>
    # Codes start..end map to unicode_start, unicode_start+1, ...
    for bfrange_block in re.finditer(
        r"beginbfrange(.*?)endbfrange", text, re.DOTALL
    ):
        for line in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            bfrange_block.group(1),
        ):
            try:
                start = int(line.group(1), 16)
                end = int(line.group(2), 16)
                uni_start_bytes = bytes.fromhex(line.group(3))
                if len(uni_start_bytes) % 2 == 1:
                    uni_start_bytes += b"\x00"
                uni_start = int.from_bytes(uni_start_bytes, "big")
                for i in range(end - start + 1):
                    cp = uni_start + i
                    try:
                        entries[start + i] = chr(cp)
                    except (ValueError, OverflowError):
                        continue
            except (ValueError, UnicodeDecodeError):
                continue

    return header, entries
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k parse_tounicode
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _parse_tounicode_cmap helper for CMap stream parsing"
```

---

## Task 4: `_serialize_tounicode_cmap` helper

Build a valid CMap stream from the parsed entries. Emit a single `bfchar` block (discard bfrange; simpler to serialize as individual entries).

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `TestFillToUnicodeLigatureGaps`:

```python
    def test_serialize_tounicode_roundtrip(self):
        from src.tools.pdf_writer import (
            _parse_tounicode_cmap,
            _serialize_tounicode_cmap,
        )
        header = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap"""
        entries = {0x41: "A", 0x42: "B", 0x0C: "fi"}
        out = _serialize_tounicode_cmap(header, entries)
        # Round-trip must preserve entries
        _, reparsed = _parse_tounicode_cmap(out)
        assert reparsed == entries

    def test_serialize_tounicode_multichar_unicode(self):
        from src.tools.pdf_writer import (
            _parse_tounicode_cmap,
            _serialize_tounicode_cmap,
        )
        header = b"begincmap"
        # ff ligature: two-codepoint mapping
        entries = {0x0B: "ff", 0x0C: "ffi"}
        out = _serialize_tounicode_cmap(header, entries)
        _, reparsed = _parse_tounicode_cmap(out)
        assert reparsed == entries

    def test_serialize_tounicode_empty(self):
        from src.tools.pdf_writer import _serialize_tounicode_cmap
        out = _serialize_tounicode_cmap(b"begincmap", {})
        # Valid CMap with zero entries
        assert b"endcmap" in out

    def test_serialize_tounicode_contains_required_sections(self):
        from src.tools.pdf_writer import _serialize_tounicode_cmap
        out = _serialize_tounicode_cmap(b"begincmap", {0x41: "A"})
        assert b"codespacerange" in out
        assert b"beginbfchar" in out
        assert b"endbfchar" in out
        assert b"endcmap" in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k serialize_tounicode
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the serializer**

Add to `src/tools/pdf_writer.py`:

```python
def _serialize_tounicode_cmap(
    header_bytes: bytes, entries: dict[int, str]
) -> bytes:
    """Serialize (header, entries) back into a /ToUnicode CMap stream.

    Emits a single ``bfchar`` block. Each entry is a line
    ``<HEX_CODE> <HEX_UTF16BE_UNICODE>``. The header is written verbatim;
    if it's empty or missing ``begincmap``, a minimal header is synthesized.
    """
    hdr_text = header_bytes.decode("latin-1", errors="replace")
    if "begincmap" not in hdr_text:
        hdr_text = (
            "/CIDInit /ProcSet findresource begin\n"
            "12 dict begin\n"
            "begincmap\n"
        )
    # Always append a codespacerange after the preserved header.
    parts: list[str] = [hdr_text.rstrip("\n"), ""]
    parts.append(
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) "
        "/Supplement 0 >> def"
    )
    parts.append("/CMapName /Adobe-Identity-UCS def")
    parts.append("/CMapType 2 def")
    parts.append("1 begincodespacerange")
    parts.append("<00> <FFFF>")
    parts.append("endcodespacerange")

    def _unicode_hex(s: str) -> str:
        # UTF-16BE hex without BOM
        return s.encode("utf-16-be").hex().upper()

    def _code_hex(code: int) -> str:
        # 2 hex digits if ≤0xFF else 4
        return f"{code:02X}" if code <= 0xFF else f"{code:04X}"

    sorted_entries = sorted(entries.items())
    # bfchar blocks limit 100 entries per block per PDF spec. Chunk.
    for i in range(0, len(sorted_entries), 100):
        chunk = sorted_entries[i : i + 100]
        parts.append(f"{len(chunk)} beginbfchar")
        for code, uni in chunk:
            parts.append(f"<{_code_hex(code)}> <{_unicode_hex(uni)}>")
        parts.append("endbfchar")

    parts.append("endcmap")
    parts.append("CMapName currentdict /CMap defineresource pop")
    parts.append("end")
    parts.append("end")
    out = "\n".join(parts) + "\n"
    return out.encode("latin-1", errors="replace")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k serialize_tounicode
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _serialize_tounicode_cmap helper for CMap stream generation"
```

---

## Task 5: `_is_pua_mapping` helper

Private Use Area detection: `U+E000–U+F8FF`, plus supplementary PUAs `U+F0000–U+FFFFD` and `U+100000–U+10FFFD`. Our spec treats PUA mappings as "missing" (replaceable).

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `TestFillToUnicodeLigatureGaps`:

```python
    def test_is_pua_mapping_true(self):
        from src.tools.pdf_writer import _is_pua_mapping
        assert _is_pua_mapping("\uE000") is True
        assert _is_pua_mapping("\uE00B") is True  # seen in real CMaps
        assert _is_pua_mapping("\uF8FF") is True

    def test_is_pua_mapping_false(self):
        from src.tools.pdf_writer import _is_pua_mapping
        assert _is_pua_mapping("A") is False
        assert _is_pua_mapping("fi") is False
        assert _is_pua_mapping("\u0066\u0066") is False  # ff ligature

    def test_is_pua_mapping_empty(self):
        from src.tools.pdf_writer import _is_pua_mapping
        assert _is_pua_mapping("") is True  # empty treated as missing

    def test_is_pua_mapping_mixed(self):
        from src.tools.pdf_writer import _is_pua_mapping
        # PUA followed by normal char — still PUA (treat as missing)
        assert _is_pua_mapping("\uE000A") is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k is_pua
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the helper**

Add to `src/tools/pdf_writer.py`:

```python
def _is_pua_mapping(unicode_str: str) -> bool:
    """Return True if the string is empty or contains any PUA codepoint.

    Private Use Areas (where custom/internal glyph mappings commonly live)
    are treated as "missing" for the purposes of ligature gap-fill: we're
    willing to overwrite such entries with canonical Unicode.

    PUA ranges (per Unicode standard):
        - BMP PUA:              U+E000 – U+F8FF
        - Supplementary PUA-A:  U+F0000 – U+FFFFD
        - Supplementary PUA-B:  U+100000 – U+10FFFD
    """
    if not unicode_str:
        return True
    for ch in unicode_str:
        cp = ord(ch)
        if 0xE000 <= cp <= 0xF8FF:
            return True
        if 0xF0000 <= cp <= 0xFFFFD:
            return True
        if 0x100000 <= cp <= 0x10FFFD:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k is_pua
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _is_pua_mapping helper for ToUnicode gap detection"
```

---

## Task 6: Main function `fill_tounicode_ligature_gaps`

Orchestrates: walks Font xrefs, detects `/Differences` array, parses its ToUnicode CMap, decides which ligature entries to add, serializes the updated CMap, writes it back via `update_stream`.

**Files:**
- Modify: `src/tools/pdf_writer.py`
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Write the failing tests**

Append to `TestFillToUnicodeLigatureGaps`. These are integration tests using synthetic PDFs built with PyMuPDF:

```python
    def _make_font_pdf(self, tmp_path, differences_body: str,
                       cmap_bytes: bytes,
                       font_xref_out: list):
        """Build a minimal PDF with one simple font whose /Encoding has
        a /Differences array and /ToUnicode CMap, returning the path and
        capturing the font xref via the out-list.
        """
        import fitz
        doc = fitz.open()
        page = doc.new_page()

        # Encoding dict with /Differences
        enc_xref = doc.get_new_xref()
        doc.update_object(
            enc_xref,
            f"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding "
            f"/Differences {differences_body} >>",
        )

        # ToUnicode stream
        tu_xref = doc.get_new_xref()
        doc.update_stream(tu_xref, cmap_bytes, new=True)

        # Font dict
        font_xref = doc.get_new_xref()
        doc.update_object(
            font_xref,
            f"<< /Type /Font /Subtype /Type1 /BaseFont /TestFont "
            f"/Encoding {enc_xref} 0 R /ToUnicode {tu_xref} 0 R >>",
        )
        font_xref_out.append(font_xref)

        out_path = tmp_path / "test.pdf"
        doc.save(str(out_path))
        doc.close()
        return out_path

    def test_fill_adds_missing_ff_entry(self, tmp_path):
        """Subset names /ff at code 0x0B; ToUnicode has no entry for 0x0B.
        After fill, ToUnicode has <0B> → 'ff'."""
        from src.tools.pdf_writer import (
            fill_tounicode_ligature_gaps, _parse_tounicode_cmap,
        )
        import fitz
        cmap = b"""/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
1 beginbfchar
<41> <0041>
endbfchar
endcmap"""
        font_xrefs: list = []
        pdf = self._make_font_pdf(
            tmp_path,
            "[11 /ff /fi /fl]",  # codes 0x0B=ff 0x0C=fi 0x0D=fl
            cmap,
            font_xrefs,
        )

        r = fill_tounicode_ligature_gaps(pdf)
        assert r.success is True
        assert r.fonts_scanned == 1
        assert r.fonts_modified == 1
        assert r.ligature_entries_added == 3  # ff, fi, fl

        # Verify the updated CMap has the entries
        doc = fitz.open(str(pdf))
        tu_key = doc.xref_get_key(font_xrefs[0], "ToUnicode")
        assert tu_key[0] == "xref"
        tu_xref = int(tu_key[1].split()[0])
        stream = doc.xref_stream(tu_xref)
        _, entries = _parse_tounicode_cmap(stream)
        assert entries[0x0B] == "ff"
        assert entries[0x0C] == "fi"
        assert entries[0x0D] == "fl"
        assert entries[0x41] == "A"  # preserved
        doc.close()

    def test_fill_replaces_pua_mapping(self, tmp_path):
        """Existing CMap maps /ff code to PUA; should be overwritten."""
        from src.tools.pdf_writer import (
            fill_tounicode_ligature_gaps, _parse_tounicode_cmap,
        )
        import fitz
        cmap = b"""begincmap
1 beginbfchar
<0B> <E00B>
endbfchar
endcmap"""
        font_xrefs: list = []
        pdf = self._make_font_pdf(
            tmp_path, "[11 /ff]", cmap, font_xrefs,
        )

        r = fill_tounicode_ligature_gaps(pdf)
        assert r.success is True
        assert r.ligature_entries_added == 1

        doc = fitz.open(str(pdf))
        tu_xref = int(
            doc.xref_get_key(font_xrefs[0], "ToUnicode")[1].split()[0]
        )
        _, entries = _parse_tounicode_cmap(doc.xref_stream(tu_xref))
        assert entries[0x0B] == "ff"
        doc.close()

    def test_fill_preserves_correct_mapping(self, tmp_path):
        """Existing CMap maps /ff code correctly; unchanged."""
        from src.tools.pdf_writer import fill_tounicode_ligature_gaps
        cmap = b"""begincmap
1 beginbfchar
<0B> <00660066>
endbfchar
endcmap"""
        pdf = self._make_font_pdf(tmp_path, "[11 /ff]", cmap, [])

        r = fill_tounicode_ligature_gaps(pdf)
        assert r.success is True
        assert r.fonts_modified == 0  # no change
        assert r.ligature_entries_added == 0

    def test_fill_skips_font_without_differences(self, tmp_path):
        """Font with /Encoding /WinAnsiEncoding (no /Differences) is
        skipped with fonts_skipped_no_encoding."""
        from src.tools.pdf_writer import fill_tounicode_ligature_gaps
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        tu_xref = doc.get_new_xref()
        doc.update_stream(
            tu_xref, b"begincmap 0 beginbfchar endbfchar endcmap", new=True
        )
        font_xref = doc.get_new_xref()
        doc.update_object(
            font_xref,
            f"<< /Type /Font /Subtype /Type1 /BaseFont /TestFont "
            f"/Encoding /WinAnsiEncoding /ToUnicode {tu_xref} 0 R >>",
        )
        out_path = tmp_path / "nodiff.pdf"
        doc.save(str(out_path))
        doc.close()

        r = fill_tounicode_ligature_gaps(out_path)
        assert r.success is True
        assert r.fonts_scanned == 1
        assert r.fonts_skipped_no_encoding == 1
        assert r.fonts_modified == 0

    def test_fill_skips_font_without_tounicode(self, tmp_path):
        """Font with no /ToUnicode at all is skipped (we don't manufacture
        a CMap from scratch — spec scope is existing-CMap patching)."""
        from src.tools.pdf_writer import fill_tounicode_ligature_gaps
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        enc_xref = doc.get_new_xref()
        doc.update_object(
            enc_xref,
            "<< /Type /Encoding /BaseEncoding /WinAnsiEncoding "
            "/Differences [11 /ff] >>",
        )
        font_xref = doc.get_new_xref()
        doc.update_object(
            font_xref,
            f"<< /Type /Font /Subtype /Type1 /BaseFont /TestFont "
            f"/Encoding {enc_xref} 0 R >>",
        )
        out_path = tmp_path / "notu.pdf"
        doc.save(str(out_path))
        doc.close()

        r = fill_tounicode_ligature_gaps(out_path)
        assert r.success is True
        assert r.fonts_scanned == 1
        assert r.fonts_modified == 0
        assert r.ligature_entries_added == 0

    def test_fill_missing_file(self, tmp_path):
        from src.tools.pdf_writer import fill_tounicode_ligature_gaps
        r = fill_tounicode_ligature_gaps(tmp_path / "nonexistent.pdf")
        assert r.success is False
        assert "not found" in r.error.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k fill_
```

Expected: FAIL with `ImportError` on `fill_tounicode_ligature_gaps`.

- [ ] **Step 3: Implement the main function**

Add to `src/tools/pdf_writer.py` near the other post-processing entry points (e.g. after `populate_link_annotation_contents`):

```python
def fill_tounicode_ligature_gaps(
    pdf_path: "str | Path",
) -> LigatureFillResult:
    """Fill missing ligature entries in fonts' /ToUnicode CMaps.

    For every font with a /ToUnicode CMap and an /Encoding /Differences
    array that names a supported ligature glyph (/ff, /fi, /fl, /ffi,
    /ffl), ensure the CMap has a correct Unicode entry for the
    corresponding character code. Existing non-PUA entries are preserved;
    PUA-mapped entries are treated as missing and overwritten.

    Pure CMap edit — no font-program changes, no visual impact. Safe
    to run unconditionally; fonts that don't meet criteria are skipped.

    Args:
        pdf_path: Path to the PDF file. Modified in place via incremental
            save.

    Returns:
        LigatureFillResult with counts.
    """
    path = Path(pdf_path)
    if not path.exists():
        return LigatureFillResult(success=False, error=f"File not found: {path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return LigatureFillResult(success=False, error=f"Open failed: {exc}")

    result = LigatureFillResult(success=True)
    try:
        for xref in range(1, doc.xref_length()):
            try:
                t = doc.xref_get_key(xref, "Type")
            except Exception:
                continue
            if t[0] != "name" or t[1] != "/Font":
                continue
            result.fonts_scanned += 1

            # Resolve /ToUnicode
            tu_key = doc.xref_get_key(xref, "ToUnicode")
            if tu_key[0] != "xref":
                continue  # no CMap; skip silently (not counted)
            try:
                tu_xref = int(tu_key[1].split()[0])
                tu_stream = doc.xref_stream(tu_xref) or b""
            except Exception:
                result.fonts_skipped_parse_error += 1
                continue

            # Resolve /Encoding /Differences
            enc_key = doc.xref_get_key(xref, "Encoding")
            if enc_key[0] == "xref":
                try:
                    enc_xref = int(enc_key[1].split()[0])
                    enc_obj = doc.xref_object(enc_xref) or ""
                except Exception:
                    result.fonts_skipped_no_encoding += 1
                    continue
            elif enc_key[0] == "dict":
                enc_obj = enc_key[1]
            else:
                # /Encoding is a name like /WinAnsiEncoding (no Differences)
                result.fonts_skipped_no_encoding += 1
                continue

            import re
            diff_m = re.search(
                r"/Differences\s*(\[[^\]]*\])", enc_obj, re.DOTALL
            )
            if not diff_m:
                result.fonts_skipped_no_encoding += 1
                continue
            code_to_name = _parse_differences_array(diff_m.group(1))
            if not code_to_name:
                result.fonts_skipped_no_encoding += 1
                continue

            # Parse existing CMap
            try:
                header, entries = _parse_tounicode_cmap(tu_stream)
            except Exception:
                result.fonts_skipped_parse_error += 1
                continue

            # Determine entries to add
            added_this_font = 0
            for code, glyph_name in code_to_name.items():
                if glyph_name not in LIGATURE_TABLE:
                    continue
                existing = entries.get(code, "")
                if existing and not _is_pua_mapping(existing):
                    continue  # already correct; preserve
                entries[code] = LIGATURE_TABLE[glyph_name]
                added_this_font += 1

            if added_this_font == 0:
                continue

            # Serialize updated CMap and write back
            try:
                new_stream = _serialize_tounicode_cmap(header, entries)
                doc.update_stream(tu_xref, new_stream)
            except Exception:
                result.fonts_skipped_parse_error += 1
                continue

            result.fonts_modified += 1
            result.ligature_entries_added += added_this_font

        doc.save(str(path), incremental=True, encryption=0)
    except Exception as exc:
        return LigatureFillResult(
            success=False,
            error=f"fill_tounicode_ligature_gaps: {exc}",
            fonts_scanned=result.fonts_scanned,
        )
    finally:
        try:
            doc.close()
        except Exception:
            pass

    return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v -k fill_
```

Expected: all 6 PASS.

- [ ] **Step 5: Run full TestFillToUnicodeLigatureGaps suite**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps -v
```

Expected: all tests PASS (roughly 20+ tests across tasks 1-6).

- [ ] **Step 6: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add fill_tounicode_ligature_gaps to fix ligature CMap gaps"
```

---

## Task 7: Wire into executor

**Files:**
- Modify: `src/agent/executor.py`
- Test: manual verification via existing test PDF

- [ ] **Step 1: Find the integration point**

```bash
grep -n "populate_link_annotation_contents\|apply_pdf_ua_tail_polish" src/agent/executor.py
```

Expected: find line(s) where other PDF post-processing calls live. The new function slots in after `populate_link_annotation_contents` and before `apply_pdf_ua_tail_polish`.

- [ ] **Step 2: Add the import**

In `src/agent/executor.py`, find the existing import block from `src.tools.pdf_writer` and add `fill_tounicode_ligature_gaps`:

```python
from src.tools.pdf_writer import (
    # ... existing imports ...
    fill_tounicode_ligature_gaps,
    # ... existing imports ...
)
```

- [ ] **Step 3: Add the call site**

After the `populate_link_annotation_contents` call (and its result handling), insert:

```python
lig_result = fill_tounicode_ligature_gaps(pdf_path)
if lig_result.success and lig_result.ligature_entries_added:
    logger.info(
        "ToUnicode ligature fill: scanned=%d modified=%d added=%d",
        lig_result.fonts_scanned,
        lig_result.fonts_modified,
        lig_result.ligature_entries_added,
    )
elif not lig_result.success:
    logger.warning(
        "ToUnicode ligature fill failed: %s", lig_result.error
    )
```

- [ ] **Step 4: Verify the executor still runs cleanly**

Run an existing executor-level test (any PDF-handling test) to confirm no regressions:

```bash
python3 -m pytest tests/test_pdf_writer.py -v 2>&1 | tail -5
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/executor.py
git commit -m "Wire fill_tounicode_ligature_gaps into executor post-processing"
```

---

## Task 8: End-to-end validation on real PDF

Verify the fix against the actual W2991007371 document we've been investigating. Confirms the ligature gap scenario is really fixed.

**Files:**
- Test: `tests/test_pdf_writer.py`

- [ ] **Step 1: Check that the source PDF is available**

```bash
ls /tmp/PDF-Accessibility-Benchmark/data/processed/functional_hyperlinks/not_present/W2991007371.pdf
```

If missing, skip Task 8 or point to an equivalent TeX-origin PDF. Otherwise proceed.

- [ ] **Step 2: Write the E2E test**

Append to `TestFillToUnicodeLigatureGaps`:

```python
    def test_e2e_w2991007371_text_extraction(self, tmp_path):
        """End-to-end: apply fill to a real TeX PDF known to have
        ff-ligature gaps. Verify text extraction improves.

        Skips automatically if the benchmark corpus is not on the host.
        """
        import shutil, fitz
        source = (
            "/tmp/PDF-Accessibility-Benchmark/data/processed/"
            "functional_hyperlinks/not_present/W2991007371.pdf"
        )
        import os
        if not os.path.exists(source):
            import pytest
            pytest.skip(f"Benchmark PDF not available at {source}")

        dst = tmp_path / "W2991007371.pdf"
        shutil.copy(source, dst)

        # Baseline: count ff-gapped words before fix
        def count_ff_gaps(p):
            doc = fitz.open(str(p))
            text = "".join(pg.get_text() for pg in doc)
            doc.close()
            import re
            patterns = [r"\bdi erent", r"\bdi erence",
                        r"\be ect", r"\bsu cient"]
            return sum(len(re.findall(pat, text)) for pat in patterns)

        gaps_before = count_ff_gaps(dst)
        assert gaps_before > 0, (
            "Expected real PDF to have ff-gaps before fix; check fixture"
        )

        from src.tools.pdf_writer import fill_tounicode_ligature_gaps
        r = fill_tounicode_ligature_gaps(dst)
        assert r.success is True
        assert r.ligature_entries_added > 0

        gaps_after = count_ff_gaps(dst)
        # Fix must not introduce new gaps and should remove at least some
        assert gaps_after < gaps_before, (
            f"ff-gap count did not decrease: "
            f"before={gaps_before} after={gaps_after}"
        )
```

- [ ] **Step 3: Run the E2E test**

```bash
python3 -m pytest tests/test_pdf_writer.py::TestFillToUnicodeLigatureGaps::test_e2e_w2991007371_text_extraction -v
```

Expected: PASS (or SKIP if corpus not on host).

- [ ] **Step 4: Sanity-check text extraction manually**

Useful smoke confirmation:

```bash
python3 -c "
import fitz
doc = fitz.open('/tmp/PDF-Accessibility-Benchmark/data/processed/functional_hyperlinks/not_present/W2991007371.pdf')
text = doc[0].get_text()[:1500]
doc.close()
import re
print('BEFORE FIX:', len(re.findall(r'di erent|di erence', text)), 'gaps')
"
```

Copy the PDF, run `fill_tounicode_ligature_gaps`, re-run the one-liner with the new path. Manually confirm the 'gaps' count drops.

- [ ] **Step 5: Run the full test suite**

```bash
python3 -m pytest tests/test_pdf_writer.py -v 2>&1 | tail -5
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_pdf_writer.py
git commit -m "Add E2E test for ToUnicode ligature fill on real PDF"
```

---

## Task 9: Run the smoke benchmark to measure impact

Validate the real-world benefit by running the v4b subset smoke test with the fix in place and comparing veraPDF counts.

**Files:**
- No code changes; this is a measurement task.

- [ ] **Step 1: Run the 5-doc smoke benchmark**

```bash
rm -rf /tmp/remediation_bench_v4c_smoke
python3 scripts/remediation_benchmark.py \
  --benchmark-dir /tmp/PDF-Accessibility-Benchmark \
  --output-dir /tmp/remediation_bench_v4c_smoke \
  --ids W2460269320,W2642438850,W2922538610,W2991007371,W3069372847
```

This runs the full pipeline (including our new ligature fill) on the five exemplar IDs.

- [ ] **Step 2: Run veraPDF on every output PDF**

```bash
bash /tmp/run_verapdf_smoke.sh
```

(This is the script created earlier in the session. If missing, recreate it from the session history: walks `/tmp/remediation_bench_v4c_smoke/*/`, runs `verapdf -f ua1 --format json` on each `*_remediated.pdf`, writes a TSV summary.)

Update the script's `OUT` variable to point at `/tmp/remediation_bench_v4c_smoke` before running.

- [ ] **Step 3: Compare v4b vs v4c**

Run a one-off comparison script:

```bash
python3 << 'EOF'
import json, os
b = {}
c = {}
for name, d in [("b", "/tmp/remediation_bench_v4b_smoke"),
                ("c", "/tmp/remediation_bench_v4c_smoke")]:
    target = b if name == "b" else c
    for sub in sorted(os.listdir(d)):
        p = f"{d}/{sub}/verapdf.json"
        if not os.path.exists(p):
            continue
        data = json.load(open(p))
        jobs = data.get("report", {}).get("jobs", []) or data.get("jobs", [])
        total = 0
        r7217 = 0
        for j in jobs:
            vr = j.get("validationResult", [])
            if isinstance(vr, list):
                vr = vr[0] if vr else {}
            for r in vr.get("details", {}).get("ruleSummaries", []):
                fc = r.get("failedChecks", 0)
                total += fc
                if r.get("clause") == "7.21.7" and r.get("testNumber") == 1:
                    r7217 += fc
        target[sub] = (total, r7217)
print(f"{'Doc':<50}{'v4b total':>12}{'v4c total':>12}{'Δ':>8}"
      f"{'7.21.7 Δ':>12}")
for doc in sorted(set(b) | set(c)):
    bt, b7 = b.get(doc, (0, 0))
    ct, c7 = c.get(doc, (0, 0))
    print(f"{doc:<50}{bt:>12}{ct:>12}{ct - bt:>+8}{c7 - b7:>+12}")
EOF
```

Expected: some docs show 7.21.7-1 drops; no doc regresses total violations substantially.

- [ ] **Step 4: Record the numbers**

Update `NOW.md` with the v4c smoke results: what 7.21.7-1 dropped by, any accessibility-affecting text-extraction improvements.

- [ ] **Step 5: Commit if NOW.md updated**

```bash
git add NOW.md
git commit -m "Record v4c smoke results: ToUnicode ligature fill impact"
```

- [ ] **Step 6: Push**

```bash
git push origin master
```

---

## Self-review (for the plan author)

After writing a plan, verify:

**Spec coverage:**
- ✓ LIGATURE_TABLE with 5 ligatures → Task 1
- ✓ LigatureFillResult dataclass → Task 1
- ✓ /Differences parsing → Task 2
- ✓ /ToUnicode CMap parsing → Task 3
- ✓ /ToUnicode CMap serialization → Task 4
- ✓ PUA detection → Task 5
- ✓ Main function with all skip/count paths → Task 6
- ✓ Executor integration → Task 7
- ✓ E2E test on W2991007371 → Task 8
- ✓ Smoke benchmark measurement → Task 9

**Placeholders:** none (all code steps contain full code).

**Type consistency:** `LigatureFillResult.ligature_entries_added` used consistently. `_parse_differences_array` returns `dict[int, str]` matching its test assertions. `_parse_tounicode_cmap` returns `tuple[bytes, dict[int, str]]` matching serializer input.
