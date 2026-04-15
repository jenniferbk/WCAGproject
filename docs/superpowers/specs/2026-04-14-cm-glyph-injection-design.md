# CM Font Glyph Injection — Design Spec

**Date:** 2026-04-14
**Status:** Draft for review
**Scope:** Font-program repair "A" for TeX Computer Modern subset fonts. Supersedes the deferred "B" spec (`2026-04-14-tounicode-ligature-fill-design.md`) because this project's ToUnicode-patch step handles the ligature-gap case.

## Problem

Academic PDFs in the Kumar benchmark (and the broader corpus of TeX-origin documents) ship with subset Computer Modern fonts that are broken in two overlapping ways:

1. **Missing glyphs in the embedded font program.** The content stream references a glyph (e.g. the `ff` ligature at character code `0x0B`) but the subset's FontFile3 (CFF) stream does not contain a CharString for it. PDF viewers draw a *gap* where the glyph should appear.
2. **Incomplete `/ToUnicode` CMap.** The same code is either absent from the CMap or mapped to the Unicode Private Use Area (`U+E000–F8FF`). Text extraction, screen readers, and search all lose the ligature.

Combined impact on a real document (`W2991007371`):

- Sighted readers see "di erences" instead of "differences" (gap in rendered text).
- Screen-reader users hear "dee—silence—erences".
- Copy/paste and full-text search drop every `ff` / `ffi` / `ffl` occurrence.
- veraPDF flags the PDF with:
  - 460 × 7.21.4.1-2 (missing glyph in font program)
  - 460 × 7.21.5-1 (Widths array inconsistent with font program widths)
  - 1 × 7.21.7-1 (character code cannot be mapped to Unicode)

98.2% of font-class violations in our benchmark smoke set are inherited from source PDFs; our pipeline did not introduce them, but faculty hand them to students and see visibly broken output.

## Goal

Restore missing glyphs to CM-family subset fonts by copying CharStrings from complete public-domain `cm-unicode` source fonts. Simultaneously fill ToUnicode CMap entries for the injected glyphs. The result: sighted readers see the correct glyphs, screen readers and search indexers extract the correct Unicode.

## Success criteria

- For `W2991007371`, the rendered page shows "differences" (no visual gap) and text extraction yields "differences" (no missing ff).
- veraPDF 7.21.4.1-2 violation count on the v4b smoke set drops substantially for CM-subset-font docs (target: ≥80% reduction of 7.21.4.1-2 violations attributable to CM-family text fonts).
- veraPDF 7.21.7-1 drops to zero for injected ligatures.
- No per-doc visual regression: automated pixel-diff check passes on every modified page at or below the configured threshold.
- No regression in the existing `tests/test_pdf_writer.py` suite.

## Non-goals

- **Math fonts (CMMI, CMSY, CMEX, CMMIB, CMBSY, etc.).** Their glyph names lack clean Unicode equivalents and their positioning is finicky; scope them into a follow-up spec only if corpus measurement shows meaningful breakage.
- **Non-CM fonts** (Calibri, Times New Roman, Helvetica, publisher-specific families). Follow-up if data supports expansion.
- **Widths array rewriting** (rule 7.21.5-1). Rewriting `/Widths` to match internal font-program widths risks double-advance glyph positioning where content-stream TJ operators are tuned against the subset's original Widths. Document 7.21.5-1 as inherited compliance theater in the report; revisit when we have a rendering-diff validator able to catch position regressions sub-pixel.
- **Full font replacement** (approach γ). The injection approach preserves existing encoding and content-stream codes; replacement would require rewriting `/Encoding /Differences` per font and carries much higher risk.
- **Subsets with unnamed glyphs** (e.g. `C0_11` instead of `/ff`). Detect, skip, count. No name-matching heuristic.

## Design

### Supported CM text-font families

| BaseFont prefix pattern | Source font file | Role |
|---|---|---|
| `CMR<NN>` | `cmunrm.otf` | Computer Modern Roman (text medium) |
| `CMBX<NN>` | `cmunbx.otf` | Computer Modern Bold Extended |
| `CMTI<NN>` | `cmunti.otf` | Computer Modern Text Italic |
| `CMSL<NN>` | `cmunsl.otf` | Computer Modern Slanted |
| `CMTT<NN>` | `cmuntt.otf` | Computer Modern Typewriter Text |

Detection regex: `^[A-Z]{6}\+CM(R|BX|TI|SL|TT)\d+$` applied to `/BaseFont`. The six-letter prefix is the PDF subset tag.

The mapping table above is the full first-release scope. Additional CM text variants (CMCSC small caps, CMDUNH, etc.) can be added in place as new entries.

### Vendored source fonts

Vendor the five source OTFs into `src/tools/fonts/cm-unicode/`:

```
src/tools/fonts/cm-unicode/
  cmunrm.otf    # Roman (medium)
  cmunbx.otf    # Bold Extended
  cmunti.otf    # Text Italic
  cmunsl.otf    # Slanted
  cmuntt.otf    # Typewriter Text
  LICENSE       # Knuth-style public-domain notice from cm-unicode
  README.md     # provenance: upstream URL + version
```

Combined size: ~2-3 MB. Public-domain license (Knuth's original CM terms + cm-unicode maintainer modifications).

### New function

`inject_cm_glyphs_and_patch_tounicode(pdf_path: str | Path) -> CMInjectionResult` in `src/tools/pdf_writer.py`.

Result dataclass:

```python
@dataclass
class CMInjectionResult:
    success: bool
    cm_fonts_detected: int = 0
    fonts_injected: int = 0
    glyphs_injected: int = 0
    tounicode_entries_added: int = 0
    fonts_reverted_visual_regression: int = 0
    fonts_skipped_no_source: int = 0        # no matching cm-unicode family
    fonts_skipped_parse_error: int = 0      # CFF parse failure
    fonts_skipped_unnamed_glyphs: int = 0   # subset uses C0_NN, no names
    pages_pixel_diff_above_threshold: int = 0
    error: str = ""
```

### Algorithm

```
open pdf (fitz)

for each font xref in doc:
    read /BaseFont
    detect_match = CM_SUBSET_RE.match(basefont)
    if not detect_match: continue
    cm_fonts_detected += 1

    resolve source_otf via family-suffix lookup
    if source_otf missing: skip (fonts_skipped_no_source); continue

    extract FontFile3 (CFF) stream
    try: parse with fontTools.ttLib.TTFont(..., fontNumber=0)
    except: skip (fonts_skipped_parse_error); continue

    read /Encoding, resolve /Differences array
    if no /Differences or all names are C0_NN style:
        skip (fonts_skipped_unnamed_glyphs); continue

    build code→glyph_name map

    subset_cs = subset.CFF['CharStrings']
    source_cs = source_otf.CFF['CharStrings']
    missing = [name for (code, name) in differences
               if name not in subset_cs and name in source_cs]

    for name in missing:
        copy CharString bytes from source_cs to subset_cs
        glyphs_injected += 1

    re-serialize modified CFF to bytes
    snapshot original FontFile3 bytes (for revert)
    replace FontFile3 stream with modified CFF bytes

    update /ToUnicode CMap (adding entries for injected ligature glyphs
      using the ligature table below)

    fonts_injected += 1

# Visual-regression gate (V3): render every page touched and diff
    (see "Validation" section below)
```

### ToUnicode patch (inline sub-step)

After a font's CFF has been updated, open its `/ToUnicode` stream (or create one if missing), parse existing `bfchar` entries, and add entries for every glyph we injected that appears in this ligature table:

| Glyph name | Unicode sequence |
|---|---|
| `/ff` | `U+0066 U+0066` |
| `/fi` | `U+0066 U+0069` |
| `/fl` | `U+0066 U+006C` |
| `/ffi` | `U+0066 U+0066 U+0069` |
| `/ffl` | `U+0066 U+0066 U+006C` |

For injected glyphs whose name is **not** in the ligature table (ordinary letters, digits, etc.), no ToUnicode entry is added — existing CMap entries for normal characters are expected to already exist and be correct.

### Validation (V4)

**Gate V3 (go/no-go on each font injection):**

To enable per-font reversion precisely, we inject fonts **one at a time** with a diff check between each:

1. Open a working copy of the PDF (we never modify the input in-place).
2. For each CM-family subset font to inject:
   a. Identify which pages reference this font (via `page.get_fonts()` or a content-stream scan at setup).
   b. Render those pages at 200 DPI **before** injection → snapshot pre-bitmaps.
   c. Apply injection (update FontFile3 stream + ToUnicode entries for this one font).
   d. Render the same pages at 200 DPI **after** injection.
   e. Compute pixel diff via existing `pixel_diff` utility in `pdf_writer.py`.
   f. If diff fraction > 0.005 (0.5% of pixels differ) on any affected page → **revert** (restore the font's original FontFile3 stream bytes and original ToUnicode entries snapshotted in step c), increment `fonts_reverted_visual_regression` and `pages_pixel_diff_above_threshold`. Continue to the next font.
   g. If diff is within threshold, commit the injection (keep the changes in the working doc) and continue.

Threshold 0.5% is initial; tune empirically. Sequential per-font injection avoids the "which font caused this diff?" ambiguity at the cost of extra renders (N renders per affected page where N = number of injected fonts on that page; in practice 3-5 fonts per page is typical for CM docs).

**Gate V2 (post-injection verification):**

After V3 passes on all fonts in a doc, extract text with `page.get_text()` and record:
- count of `di erence` / `e ect` / `su cient` / similar ff-gapped words before
- count of same strings after

Success if post-extraction gap count ≤ pre-extraction gap count for every injected doc. Log any doc where gap count goes up (shouldn't happen but would indicate we broke something subtle).

### Integration point

In `src/agent/executor.py`, inside the PDF post-processing block, **before** iText tagging. Ordering rationale: iText's struct-tree work doesn't touch font programs, but running injection first means subsequent stages see the fixed fonts. If we ran injection after struct-tree work, the incremental save chain would be slightly more complicated. Simplest: make injection the first font-modifying step.

```python
cm_result = inject_cm_glyphs_and_patch_tounicode(pdf_path)
if cm_result.success and cm_result.glyphs_injected:
    logger.info(
        "CM glyph injection: detected=%d injected=%d glyphs=%d "
        "tounicode=%d reverted=%d",
        cm_result.cm_fonts_detected,
        cm_result.fonts_injected,
        cm_result.glyphs_injected,
        cm_result.tounicode_entries_added,
        cm_result.fonts_reverted_visual_regression,
    )
```

### Error handling

Every failure path is non-fatal. Per-font errors increment the appropriate skip counter. Catastrophic failure (can't open PDF, catastrophic fontTools crash) returns `CMInjectionResult(success=False, error=...)`. The executor logs and continues — font breakage is a quality-of-output issue, not a pipeline-killer.

### Testing

**Unit tests** (new class `TestCMGlyphInjection` in `tests/test_pdf_writer.py`):

1. **Detects CM subset by BaseFont name.** Synthetic font dicts with BaseFont `ABCDEF+CMR10` → detected; `Times-Roman` → not detected.
2. **Injects missing glyph.** Build a synthetic CFF font lacking `/ff`, run injection against a real `cmunrm.otf` source, assert `ff` is now in CharStrings and draws the expected outline.
3. **ToUnicode entry added for ligature.** After injection, assert CMap contains `<0B> <00660066>` (if `0x0B` was the ff code in the test's /Differences).
4. **Subset without /Differences.** Skipped with `fonts_skipped_unnamed_glyphs++`.
5. **Subset with C0_NN glyph names.** Skipped with `fonts_skipped_unnamed_glyphs++`.
6. **Source font missing.** If `cmun*.otf` not present, skip with `fonts_skipped_no_source++`.
7. **Pixel-diff revert path.** Monkey-patch injection to inject a deliberately-corrupt glyph, confirm revert path restores original FontFile3.
8. **Existing ToUnicode preserved.** Non-ligature entries in an existing CMap are unchanged after injection.

**End-to-end tests** (in `tests/test_pdf_writer.py` or new `tests/test_cm_injection_e2e.py`):

9. Apply injection to a copy of `W2991007371.pdf`. Assert:
   - Text extraction yields `differences` (not `di erences`)
   - Rendered page 0 visibly contains "differences" (pixel-level spot check: no big gap in the span containing the word)
   - veraPDF 7.21.4.1-2 violation count drops
   - veraPDF 7.21.7-1 count drops

## Observability

Per-doc log line with counts. Aggregate counts flow through the existing `on_phase` callback — no new plumbing. Compliance report (HTML) gets a new "Font repairs" section summarizing injected glyphs and any reverted fonts.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| CFF binary manipulation corrupts font | High | V3 pixel-diff auto-revert per font |
| Subset glyph name differs from cm-unicode name | Medium | Strict name matching only; unknown → skip |
| Unnamed-glyph subsets (`C0_11`) common in corpus | Medium | Measure corpus fraction, add name-recovery path only if needed |
| Pixel-diff threshold (0.5%) miscalibrated | Low | Start conservative, tune empirically per-doc during implementation |
| fontTools version quirks in CFF serialization | Low | Pin fontTools version; unit test round-trip serialization |
| Injected glyph's internal width doesn't match subset's Widths entry | Medium | Injection preserves Widths; font renderer uses dict Widths for positioning, so glyph draws correctly even if font-program width differs |
| Incremental save after FontFile3 stream replacement | Low | PyMuPDF supports replacing stream contents via `update_stream`; if it doesn't work, fall back to full save |

## Dependencies

- `fontTools` (already installed, version 4.61.1). Pin in `pyproject.toml`: `fonttools>=4.60.0,<5.0.0`.
- Vendor fonts: download `cm-unicode` OTFs from CTAN (<https://ctan.org/pkg/cm-unicode>) during initial setup; check in as `.otf` binary assets.

## Rollout

1. Vendor cm-unicode fonts. Commit the five OTF files + LICENSE + README.
2. Implement `inject_cm_glyphs_and_patch_tounicode`, unit tests 1-8.
3. E2E test 9 on `W2991007371` copy; iterate on edge cases.
4. Wire into executor.
5. Run v4b smoke set; confirm veraPDF drops, visual rendering improves, text extraction improves.
6. Commit + push.
7. Consider extending to math fonts (separate spec) once impact data for text-only case is measured.

## Future work

Items for separate specs once corpus measurement shows demand:

- **Math font injection.** CMMI, CMSY, CMEX with curated glyph-name → Unicode table (~100-200 entries covering math italic letters, Greek, common symbols).
- **Non-CM font families.** Calibri/Times/Arial if broken; use DejaVu/Liberation as free replacements. Needs font-family detection system beyond CM regex.
- **Widths-array reconciliation** (rule 7.21.5-1). Requires fine-grained rendering diff to catch position regressions from Widths changes. Not worth the risk until we have sub-pixel diff tooling.
- **C0_NN glyph-name recovery.** If a large fraction of corpus subsets use unnamed glyphs, add a recovery path that matches by glyph outline similarity or by CharString bytes. Currently skipped.
