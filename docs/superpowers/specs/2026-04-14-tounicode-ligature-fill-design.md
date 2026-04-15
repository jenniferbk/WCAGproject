# ToUnicode Ligature Gap Fill — Design Spec

**Date:** 2026-04-14
**Status:** Draft for review
**Scope:** Accessibility fix "B" (from the Apr 14 font brainstorm). Visual glyph correctness is out of scope and lives in a separate spec "A".

## Problem

PDFs in the Kumar benchmark (and the broader academic corpus) routinely ship with subsetted Computer Modern and other TeX-origin fonts whose `/ToUnicode` CMaps are incomplete. The canonical failure: a font's content stream references a ligature glyph (`/ff`, `/ffi`, `/ffl`) but the CMap either omits an entry for that character code or maps it to the Unicode Private Use Area (PUA, `U+E000–F8FF`).

Downstream impact:
- **Screen readers** announce a gap where the ligature should be ("di—silence—erences" in place of "differences").
- **Copy/paste** drops the ligature entirely.
- **Full-text search** fails to match any word containing a ligature.
- **LLM pipelines** get corrupted input.

In the smoke set of 19 benchmark PDFs, these gaps drive the ~199 remaining veraPDF 7.21.7-1 violations (plus invisible but real degradation not all of which veraPDF catches).

Font-program-level repair (adding the missing glyph, correcting Widths) is the larger project "A" and is out of scope here.

## Goal

Fill ligature entries in existing `/ToUnicode` CMaps so that text extraction, screen readers, and search engines receive the correct Unicode for ligature glyphs that are already drawn (or drawn-as-gap) in the content stream. No font-program changes, no visual changes.

## Success criteria

- For every font in a processed PDF whose `/Encoding /Differences` array names a supported ligature glyph at some character code, the font's `/ToUnicode` CMap contains a correct entry for that code.
- Extracting text from `W2991007371` (source: `/tmp/PDF-Accessibility-Benchmark/data/processed/functional_hyperlinks/not_present/W2991007371.pdf`) after running the fix yields `differences` / `different` / `effective` / `sufficient` without internal gaps.
- veraPDF 7.21.7-1 violation count on the v4b smoke set drops (some fraction of the 199 current violations are ligature gaps; others have different causes that belong to "A"). We commit to a measurable drop, not a specific percentage, because we don't yet know how many 7.21.7-1 failures in this corpus are ligature-caused.
- No regressions in existing `tests/test_pdf_writer.py` suite.

## Non-goals

- Adding missing glyphs to font programs. (That's "A".)
- Fixing `/Widths` array inconsistency with font program widths. (That's "A".)
- Handling math-font glyphs (CMMI, CMSY) whose glyph names don't have clean Unicode equivalents.
- Handling composite (Type0 / CID) fonts. The ligature problem in this corpus sits in simple Type1/CFF fonts with `/Differences` encodings.
- Retroactively improving non-ligature Unicode mappings that look suspicious. Too easy to break.

## Design

### Supported ligatures

| Glyph name | Unicode sequence |
|---|---|
| `/ff` | `U+0066 U+0066` |
| `/fi` | `U+0066 U+0069` |
| `/fl` | `U+0066 U+006C` |
| `/ffi` | `U+0066 U+0066 U+0069` |
| `/ffl` | `U+0066 U+0066 U+006C` |

Hardcoded table in the new module. Archaic/language-specific ligatures (`ſt`, `ß`, `œ`, etc.) are deferred until we see meaningful volume.

### New function

`fill_tounicode_ligature_gaps(pdf_path: str | Path) -> LigatureFillResult`

Lives in `src/tools/pdf_writer.py` alongside existing PDF post-processing functions.

Result dataclass:

```python
@dataclass
class LigatureFillResult:
    success: bool
    fonts_scanned: int = 0
    fonts_modified: int = 0
    ligature_entries_added: int = 0
    fonts_skipped_no_encoding: int = 0
    fonts_skipped_parse_error: int = 0
    error: str = ""
```

### Algorithm

```
open pdf
for each font xref in doc:
    if no /ToUnicode: skip (counts as no_encoding if also no /Differences)
    read /Encoding → /Differences if present
    build code → glyph_name map from /Differences
    for each (code, glyph_name) where glyph_name in LIGATURE_TABLE:
        parse existing /ToUnicode CMap
        if CMap already has a non-PUA entry for this code: skip this entry
        add or replace mapping: code → LIGATURE_TABLE[glyph_name]
        mark this font as modified
    if this font modified:
        serialize updated CMap
        write new stream, repoint /ToUnicode on font dict
save incrementally
return result
```

### CMap parsing / writing

The existing CMap stream is a text PostScript program with `beginbfchar`/`endbfchar` (and optionally `beginbfrange`/`endbfrange`) blocks. We:

1. Parse existing `bfchar` entries as `<HEX_CODE> <HEX_UNICODE>` pairs into a dict.
2. Preserve all existing non-PUA entries unchanged.
3. Add or overwrite (if PUA) ligature entries.
4. Emit a single `bfchar` block covering all entries, discarding any `bfrange` blocks we parsed (they'll be emitted as individual `bfchar` entries for simplicity).

The CMap header (CIDSystemInfo, CMapName, codespacerange) is preserved verbatim — we only touch the body.

If CMap parsing fails (malformed stream), skip this font with `fonts_skipped_parse_error`.

### Encoding parsing

Simple-font `/Encoding` can be:

1. A name object like `/WinAnsiEncoding` — no `/Differences`, nothing to do.
2. A dict with `/BaseEncoding` and `/Differences`.
3. An indirect reference to that dict.

We only look at `/Differences`, which is an array like `[11 /ff 12 /fi 13 /fl]` (integer-glyphname pairs with running counters). Parse into a `code → glyph_name` dict.

Fonts without a `/Differences` array cannot have ligatures added because we have no way to know which code is a ligature. Count as `fonts_skipped_no_encoding`.

### Integration point

`src/agent/executor.py`, inside the PDF post-processing block that runs after iText tagging. Inserted adjacent to the existing `populate_link_annotation_contents` call:

```python
lig_result = fill_tounicode_ligature_gaps(pdf_path)
if lig_result.success and lig_result.ligature_entries_added:
    logger.info(
        "ToUnicode ligature fill: scanned=%d modified=%d added=%d",
        lig_result.fonts_scanned,
        lig_result.fonts_modified,
        lig_result.ligature_entries_added,
    )
```

Ordering: after iText tagging (so subset fonts are final) and after link contents population (unrelated, but keeps the "annotation-or-CMap metadata edits" functions grouped). Before `apply_pdf_ua_tail_polish`.

### Error handling

Every failure path is non-fatal. The function never raises out to the caller. On unrecoverable error (e.g. corrupt PDF that prevents open), return `LigatureFillResult(success=False, error=...)`. On per-font errors (CMap parse, encoding parse), increment the appropriate skip counter and move on.

### Testing

Unit tests (`tests/test_pdf_writer.py`, new class `TestFillToUnicodeLigatureGaps`):

1. **Missing entry fill:** synthetic Type1 font with `/Differences [11 /ff]` and a `/ToUnicode` CMap that has entries for normal letters but nothing for code `0x0B` → after fix, CMap has `<0B> <00660066>`.
2. **PUA replacement:** same as above but CMap already has `<0B> <E00B>` (PUA) → after fix, `<0B> <00660066>`.
3. **Already-correct preservation:** CMap already has `<0B> <00660066>` → unchanged, font not counted as modified.
4. **No /Differences:** font with `/Encoding /WinAnsiEncoding` → skipped, counted as `fonts_skipped_no_encoding`.
5. **Multiple ligatures in one font:** `/Differences [11 /ff 12 /fi 13 /fl 14 /ffi 15 /ffl]` → all five added.
6. **Malformed CMap:** corrupt stream → counted as parse error, other fonts in same PDF still processed.

End-to-end (new test or integration in existing e2e fixture):

7. Apply the function to a copy of `W2991007371.pdf`, extract text with PyMuPDF, assert "differences" appears and "di erences" does not.

## Observability

- Per-doc log line with counts (above).
- Aggregate counts reach the benchmark report through the existing `on_phase` callback — no new plumbing.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| CMap parse/write breaks an existing valid CMap | High | Per-font try/except; never overwrite existing non-PUA entries; add unit tests for round-trip preservation |
| Wrong-guessed mapping produces misleading screen-reader text | Medium | Only add mappings for glyphs whose names are in the hardcoded, unambiguous ligature table. No heuristic guessing |
| Some PDFs name ligatures differently (`f_f` instead of `/ff`, etc.) | Low | Normalize glyph names (strip leading slash, lowercase, replace underscores with nothing) before lookup. Skip unknowns |
| veraPDF rejects the rewritten CMap due to formatting | Low | Mirror the exact structure of existing CMaps we observe in the corpus; verify on W2991007371 before shipping |

## Rollout

1. Implement `fill_tounicode_ligature_gaps` + unit tests.
2. Wire into executor.
3. Run v4b smoke on the 19 docs; confirm 7.21.7-1 drops and text extraction improves. No visual regression (we aren't touching rendering).
4. Commit + push.
5. Optionally run full 125-doc benchmark to quantify impact for the paper.

## Future work — project A

After B ships, a separate spec covers the larger font-program engineering:

- Glyph injection into subset font programs (pull missing ligature glyphs from complete source fonts like `cm-super` or `cm-unicode`).
- `/Widths` array reconciliation.
- Math-font glyph-name → Unicode curation for CMMI / CMSY / etc.
- Handling composite (Type0) fonts.
- Rendering diff validation: render before/after and assert no glyph position change above a threshold.

That project is an order of magnitude larger and deserves its own brainstorm, spec, and plan.
