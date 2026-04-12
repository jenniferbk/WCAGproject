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
- **PDF link text validation blind spot** — validator reads raw URLs from content stream, ignores iText's `/Link` struct element accessible names. Fix via `/StructParent` → `/ParentTree` resolution.
- **Font repair** (rules 7.21.x) — ~5,543 remaining checks, fontTools-based. Would push remediation to ~91.5%.
- **Deeper form XObject recursion** — ~16,186 remaining 7.1-3 checks.

## Architecture Quick Reference
- Detection: `scripts/benchmark.py` + `scripts/struct_tree_probe.py` (heuristic + Gemini vision hybrid)
- Remediation: `src/agent/orchestrator.py` (comprehend → strategize → execute → review)
- PDF post-processing: Track A (artifact marking) + Track C (PDF/UA metadata) in `src/tools/pdf_writer.py`
- iText structure tagging: `java/itext-tagger/` fat JAR
- Web app: FastAPI at `src/web/app.py`, deployed via Caddy on Oracle Cloud
