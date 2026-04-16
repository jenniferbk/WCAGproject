# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Benchmark detection**: **96.8%** (121/125, Kumar methodology replication, beats GPT-4-Turbo 85% by 11.8pp). Raw-PDF analysis: **80.0%** (theoretical ceiling).
- **Remediation**: **63.9% PDF/UA violation reduction** on v4 benchmark (21,623→7,797). 4 fully compliant, 116/125 improved, 9 regressed.
- **Tests**: 1035 passing
- **Publication**: arXiv preprint + blog post + TACCESS journal (no deadline pressure)
- **Kumar collaboration**: Lucy Wang confirmed methodology findings, ongoing email exchange

## Key Numbers for Publication
| Metric | Value |
|--------|-------|
| Detection accuracy (Kumar replication) | 96.8% (121/125) |
| Detection accuracy (raw-PDF analysis) | 80.0% |
| GPT-4-Turbo published baseline | 85.0% |
| PDF/UA violation reduction | 63.9% on v4 (21,623→7,797) |
| Fully PDF/UA compliant | 4/125 (v4) |
| Docs improved | 116/125 on v4 |
| Docs regressed | 9/125 on v4 (4 unique PDFs) |
| Average cost per doc | ~$0.11 |
| Kumar byte-identical finding | 13/125 items share PDFs across labels |

## Per-Task Detection Scores (96.8%)
| Task | Us | GPT-4-Turbo | Delta |
|------|---:|---:|---:|
| color_contrast | **100%** | 93% | +7 |
| fonts_readability | **100%** | 100% | tie |
| functional_hyperlinks | **100%** | 80% | +20 |
| semantic_tagging | **100%** | 85% | +15 |
| table_structure | **100%** | 100% | tie |
| alt_text_quality | **95%** | 70% | +25 |
| logical_reading_order | **80%** | 67% | +13 |

## What's Next: Path to Zero veraPDF Violations

### v4 benchmark error analysis (39,032 remaining violations)

| Priority | Rule | Violations | % | Fix approach | Exemplar (small) | Exemplar (high count) |
|----------|------|---:|---:|---|---|---|
| 1 | 7.1-3 | 31,383 | 80% | ParentTree gaps on preserve-path docs; untagged content in complex layouts | `alt_text_quality_passed_W2460269320` (1v) | `table_structure_cannot_tell_W2296421107` (54v) |
| 2 | 7.18.1-2 + 7.18.5-2 | 1,994 | 5% | Extend `populate_link_parent_tree` to all annotation types | `fonts_readability_failed_W2805701040` (2v) | `table_structure_passed_W2922538610` (58+58v) |
| 3 | 7.1-1 + 7.1-2 | 943 | 2% | Form XObject nesting in color_contrast docs | `color_contrast_cannot_tell_W2642438850` (6v) | `color_contrast_failed_W2642438850` (59+61v) |
| 4 | 7.21.x (fonts) | 4,397 | 11% | fontTools: embed fonts, fix glyph widths, add ToUnicode CMaps | `functional_hyperlinks_cannot_tell_W2893185172` (2v) | `functional_hyperlinks_not_present_W2991007371` (460v) |
| 5 | 7.1-5 | 113 | <1% | Add `/RoleMap` for non-standard types (Footnote, Textbox, etc.) | `functional_hyperlinks_not_present_W2893185172` (15v) | `functional_hyperlinks_passed_W3069372847` (45v) |
| 6 | 7.3-1 | 139 | <1% | Figure alt text gaps — edge cases in alt text pipeline | `alt_text_quality_not_present_W3005755974` (1v) | `functional_hyperlinks_passed_W2991007371` (18v) |
| 7 | Other (7.4, 7.5, 7.9, 7.2, 6.2) | 63 | <1% | Table structure, document structure, notes, MarkInfo | various | various |

**By task (violations):** functional_hyperlinks: 30,484 (78%) · table_structure: 6,728 · color_contrast: 1,138 · logical_reading_order: 274 · semantic_tagging: 231 · alt_text_quality: 132 · fonts_readability: 45

**Fully compliant docs (0 violations):** `alt_text_quality_cannot_tell_W3005755974`, `alt_text_quality_not_present_W2460269320`, `alt_text_quality_passed_W3005755974`, `semantic_tagging_failed_W2067815167`

**Regressed docs (4 unique PDFs):**
- W1974692547 (table_structure ×3): +70-72 each, top rule 7.1-3
- W3005911753 (functional_hyperlinks ×2): +39-55, top rule 7.21 (font)
- W4230438091 (logical_reading_order): +44, top rule 7.1-3
- W2895738059 (semantic_tagging): +43, top rule 7.1-3

**Quick-test subsets** (run just these instead of full 125):
- **Content tagging (7.1-3):** W2460269320, W2296421107, W1974692547, W4230438091, W2895738059
- **Link annotations (7.18):** W2922538610, W2805701040
- **Artifact nesting (7.1-1/2):** W2642438850
- **Fonts (7.21):** W2991007371, W2893185172
- **Role mapping (7.1-5):** W2893185172, W3069372847
- **Smoke test (all categories):** W2460269320, W2642438850, W2922538610, W2991007371, W3069372847

### Publication

- **arXiv preprint**: `docs/writeup/2026-assets-lbw-draft.md` — update with v4 numbers (63.9% reduction, 4 compliant), expand architecture + Kumar analysis
- **Blog post**: practitioner-facing writeup for remediate.jenkleiman.com
- **TACCESS journal**: full paper, rolling submissions, adapted from arXiv

### Font engineering (Project A — future)

Spec: `docs/superpowers/specs/2026-04-14-cm-glyph-injection-design.md`

98.2% of font violations (7.21.x) are **inherited from source PDFs**, not introduced by our pipeline. These are TeX-origin CM subset fonts with:
- Missing ligature glyphs (ff, ffi, ffl) — visibly renders as gaps ("di erences")
- Widths array disagreements with font program internal widths
- Missing glyphs referenced by content stream

ToUnicode CMap ligature fill (Project B, shipped 2026-04-15) writes correct CMap entries but veraPDF 7.21.7-1 checks font-program encoding, not /ToUnicode. Project A (glyph injection from cm-unicode) is needed for visual + compliance fix. Deferred to separate spec/plan.

### Other
- **Raw-PDF detection at ceiling (80.0%)** — all 25 remaining errors are structurally unsolvable
- **~~PDF link text validation~~** — FIXED (2026-04-12)
- **~~Struct tree architecture~~** — FIXED (2026-04-13)

## Shipped: v4c Fixes — 7.3-1 / 7.18 / ToUnicode Ligature Fill (2026-04-14/15)

**Three fixes** shipped across two sessions. v4c smoke results (19 exemplar docs):

| Metric | v4 (source) | v4b | v4c |
|---|---:|---:|---:|
| Total violations | 20,145 | 2,444 | **1,867** |
| Reduction from source | — | 87.9% | **90.7%** |
| Delta from v4b | — | — | **−577 (−23.6%)** |
| Fully compliant | 0 | 4 | 4 |

**What dropped:**
- `7.18.1-2 + 7.18.5-2`: 410 → **0** — annotation-level `/Dest` link /Contents fill (`511c8c9`)
- `7.3-1`: 169 → **0** — empty `/Alt ()` detection + fill (`ab11623`)
- `7.21.7-1`: 199 → **199** (unchanged) — ligature ToUnicode CMap fill structurally correct but veraPDF checks font-program encoding, not /ToUnicode. Real-world benefit is for Acrobat/screen readers/search, not veraPDF.

**Remaining v4c rule breakdown (1,867 total):**
- 7.21 fonts (inherited): 1,483 (79.4%) — needs Project A (glyph injection)
- 7.1-3 content tagging: 319 (17.1%)
- 7.1-1/7.1-2 form xobject: 59 (3.2%)
- Other: 6 (0.3%)

**Commits:** `ab11623` (7.3-1 fix), `511c8c9` (7.18 /Dest fix), `56439bc` (benchmark --ids), `f8b0f03` (ToUnicode ligature fill merge — 12 commits, 981 lines)

**Kumar collaboration update:** Anukriti offered co-authorship + meeting (2026-04-14 email). She's leading a multi-persona agentic web a11y system with direct overlap to our approach. Meeting TBD.

## Shipped: Complete Struct Tree Tagging (2026-04-13)

**Fixed** the benchmark regression from 86.7% to 38.8% caused by `mark_untagged_content_as_artifact()` hiding body text from screen readers.

**What changed:**
- `tag_or_artifact_untagged_content()` replaces artifact marking — body text → /P struct elements with MCIDs, page furniture (page numbers, repeated headers/footers) → /Artifact
- `assess_struct_tree_quality()` decides whether to preserve or rebuild existing struct trees (4 validation checks: coverage ratio, MCID orphan rate, page ref validity, role distribution)
- `_update_parent_tree_for_mcids()` creates ParentTree entries for ALL MCIDs (including iText's — iText leaves its ParentTree empty)
- `_collect_struct_tree_mcid_mappings()` walks entire struct tree to find all MCID→struct element mappings
- iText reuses existing /Document root on preserve path (no more duplicate /Document elements)
- `filter_tagging_plan_for_existing_tree()` prevents duplicate /Figure elements on preserve path
- Form XObject `Do` runs classified as artifact (not /P) to avoid nested artifact-inside-tagged violations
- Form XObject pass 2 artifact wrapping removed (content inherits parent tagged context)
- 38 new tests, 1035 total passing, 0 regressions

**Spec:** `docs/superpowers/specs/2026-04-13-struct-tree-complete-tagging-design.md`
**Plan:** `docs/superpowers/plans/2026-04-13-struct-tree-complete-tagging.md`

**v4 benchmark results** (`/tmp/remediation_bench_v4`):
- 21,623 → 7,797 violations (**63.9% reduction**)
- 4 fully PDF/UA compliant (0 in v3)
- 116/125 improved, 9 regressed (vs 50 improved, 72 regressed in v3)
- v3 baseline was: 52,544→32,146 (38.8% reduction)

## Shipped: /Suspect → /Artifact Conversion (2026-04-12)

Non-standard `/Suspect` BDC markers (from Adobe OCR) converted to `/Artifact` in artifact marking pass. Skinner: 629 → 132 veraPDF failures (−79%). 474 markers converted across 10 pages.

## Shipped: Link Text Harvest (2026-04-12)

Fixed false WCAG 2.4.4 failures where validator saw raw URLs despite agent having improved link text. Three changes:
1. `populate_link_parent_tree()` accepts `link_text_overrides` dict — uses descriptive text instead of raw URLs for `/Link` struct element `/ActualText`
2. Executor builds URL→text mapping from executed `set_link_text` actions, passes to `populate_link_parent_tree`
3. Parser resolves `/StructParent` → `/ParentTree` → `/ActualText` during link extraction

**Result:** Syllabus 2.4.4 issues 6 → 0 on fresh re-parse. All links show descriptive text ("UGA Writing Center", "What Is Plagiarism?", "DOI: 10.3389/...") instead of raw URLs.

## Architecture Quick Reference
- Detection: `scripts/benchmark.py` + `scripts/struct_tree_probe.py` (heuristic + Gemini vision hybrid)
- Remediation: `src/agent/orchestrator.py` (comprehend → strategize → execute → review)
- PDF post-processing: Track A (content tagging + artifact marking) + Track C (PDF/UA metadata) in `src/tools/pdf_writer.py`
- iText structure tagging: `java/itext-tagger/` fat JAR
- Web app: FastAPI at `src/web/app.py`, deployed via Caddy on Oracle Cloud
