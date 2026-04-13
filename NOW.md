# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Benchmark detection**: **96.8%** (121/125, Kumar methodology replication, beats GPT-4-Turbo 85% by 11.8pp). Raw-PDF analysis: **80.0%** (theoretical ceiling).
- **Remediation**: **86.7% PDF/UA failed-check reduction** on 125 docs
- **Tests**: 977 passing
- **Publication**: arXiv preprint + blog post + TACCESS journal (no deadline pressure)
- **Kumar collaboration**: Lucy Wang confirmed methodology findings, ongoing email exchange

## Key Numbers for Publication
| Metric | Value |
|--------|-------|
| Detection accuracy (Kumar replication) | 96.8% (121/125) |
| Detection accuracy (raw-PDF analysis) | 80.0% |
| GPT-4-Turbo published baseline | 85.0% |
| PDF/UA failed-check reduction | 86.7% (−165,076 checks) |
| Docs improved | 113/125 (90.4%) |
| Docs regressed | 4/125 |
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

## CRITICAL: Struct Tree Architecture Problem (discovered 2026-04-13)

Benchmark v3 results: **38.8% failed-check reduction** (down from 86.7%). 72/125 docs REGRESSED.

**Root cause:** iText only tags headings, figures, tables, links. Body text (/P), lists (/L), captions, formulas, blockquotes — all untagged. Artifact marking then labels body text as /Artifact, hiding it from screen readers. On docs with existing struct trees (92/120), we strip good /P tags and replace with an incomplete tree.

**Required fix (two parts):**
1. **Complete tagging** — whether building from scratch or augmenting, every content item must get the appropriate tag for what it is. Body text → /P, lists → /L+/LI, captions → /Caption, math → /Formula, etc. No content should be left untagged (and thus artifact-marked).
2. **Preserve existing trees** — don't strip well-tagged trees. Augment them with our improvements (headings, alt text, link text) instead.

**Benchmark v3 results** (`/tmp/remediation_bench_v3`): 125/125 succeeded, 6 fully compliant, 52,544→32,146 failed checks (38.8% reduction). Top remaining rules: 7.1-3 (4,808), 7.18.x (1,952), 7.21.x (1,884).

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
- PDF post-processing: Track A (artifact marking) + Track C (PDF/UA metadata) in `src/tools/pdf_writer.py`
- iText structure tagging: `java/itext-tagger/` fat JAR
- Web app: FastAPI at `src/web/app.py`, deployed via Caddy on Oracle Cloud
