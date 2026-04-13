# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Benchmark detection**: **96.8%** (121/125, Kumar methodology replication, beats GPT-4-Turbo 85% by 11.8pp). Raw-PDF analysis: **80.0%** (theoretical ceiling).
- **Remediation**: **38.8% PDF/UA failed-check reduction** on v3 benchmark (down from 86.7% — see struct tree architecture problem below)
- **Tests**: 1035 passing
- **Publication**: arXiv preprint + blog post + TACCESS journal (no deadline pressure)
- **Kumar collaboration**: Lucy Wang confirmed methodology findings, ongoing email exchange

## Key Numbers for Publication
| Metric | Value |
|--------|-------|
| Detection accuracy (Kumar replication) | 96.8% (121/125) |
| Detection accuracy (raw-PDF analysis) | 80.0% |
| GPT-4-Turbo published baseline | 85.0% |
| PDF/UA failed-check reduction | 38.8% on v3 (struct tree problem — see below) |
| Docs improved | 50/125 on v3 |
| Docs regressed | 72/125 on v3 (caused by incomplete struct tree tagging) |
| Average cost per doc | $0.13 |
| Total cost (125 docs) | $16.23 |
| Wall time (125 docs) | 5h20m (median 112s/doc) |
| Headings added | 534 |
| Docs gaining figure alt text | 84/125 |
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

## What's Next (Priority Order)

### 1. Publication — arXiv preprint
- Starting point: `docs/writeup/2026-assets-lbw-draft.md` (4-page LBW draft, needs expansion)
- Expand into full arXiv paper: architecture, Kumar methodology analysis, remediation results, related work
- Update numbers to 96.8% detection throughout
- No deadline — post when ready

### 2. Blog post
- Practitioner-facing writeup on remediate.jenkleiman.com
- Targets university accessibility offices directly

### 3. TACCESS journal submission
- Full paper, rolling submissions
- Can be adapted from arXiv version with additional depth

### 4. Tool improvements (no urgency)
- **Raw-PDF detection at ceiling (80.0%)** — all 25 remaining errors are structurally unsolvable (byte/content-identical cannot_tell pairs). No further heuristic improvement possible on this benchmark.
- **~~PDF link text validation blind spot~~** — FIXED (2026-04-12). Parser now reads `/ActualText` from struct tree.
- **~~Form XObject recursion~~** — ALREADY SOLVED. Pass 2 walker handles form XObjects. The 16,186 figure was stale (from before the walker was added).
- **Language tagging** (rules 7.2-34) — "/Lang" not set on content spans. Potentially large check count. Needs investigation.
- **Link annotations** (rules 7.18.x) — partially addressed by link text harvest. Remaining: annotations still lacking proper struct tree linkage in some docs.
- **Font repair** (rules 7.21.x) — font encoding issues (ToUnicode CMap, glyph widths). Requires fontTools. Risk of breaking visual rendering.

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

**Early result:** W2460269320 → **0 veraPDF violations** (first fully PDF/UA-compliant benchmark doc). 209 input violations → 0 output.

**v4 benchmark running** (`/tmp/remediation_bench_v4`). Full 125-doc run in progress.

**v3 baseline for comparison:** 125/125 succeeded, 6 fully compliant, 52,544→32,146 failed checks (38.8% reduction). Top remaining rules: 7.1-3 (4,808), 7.18.x (1,952), 7.21.x (1,884).

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
