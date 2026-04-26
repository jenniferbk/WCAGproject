# NOW - Current Session State

## NEW (2026-04-25): Major university inquiry — production-readiness pivot
A major university inquired about using the tool. This shifts priorities from research toward production-readiness for institutional deployment. Audit findings + 10-item gap inventory in `memory/project_university_inquiry.md`. Headline gaps: concurrency=1 hardcoded (`src/web/app.py:98`), no real job queue (Python threads), SQLite write contention, single-server ARM, one shared API key (just bit us with Gemini Tier 1 verification gating), no per-org quotas, no FERPA story, no SAML, no storage cleanup, no cost cap. Tackle before signing.

## Today's session (2026-04-25)

**Morning — strategy mapper:**
- **Strategy mapper experiment shipped** (`docs/experiments/2026-04-25-strategy-mapper-comparison.md`). Findings: ~70% of LLM strategy work is template-following, ~25% is judgment work that comprehension *should* do but doesn't, ~5% harmful (alt-text hallucination on images comprehension couldn't see).
- **Comprehension prompt extension** drafted, applied, and smoke-tested on the EMAT 8030 syllabus (no images). All 3 new fields populate correctly: BCP-47 language ("en"), heading_level on every convert_to_heading (24/24), link_text_proposals (5/5 raw-URL links). Image-heavy validation blocked by Gemini identity verification (1-3 days).
- **Mapper updated** to consume new comprehension fields. 960/961 tests pass, no regressions.
- **Phase B (full pipeline both modes + veraPDF)** queued, blocked on verification.

**Afternoon — UGA pivot + Tier 0 hardening (branch `hardening/tier0`):**
- **DOJ extension verified** — Title II compliance moved from 2026-04-24 to **2027-04-26** by IFR published 2026-04-20 (`memory/project_doj_title_ii_deadline_extension.md`). UGA pitch reframed as "controlled rollout" instead of "deadline crisis."
- **EITS letter drafted** (`docs/uga/eits-saml-request.md`) — Shibboleth SP registration request to `idm@uga.edu`, with Dean Spangler as originating contact. UGA EITS process verified live via WebFetch (`docs/uga/eits-process-verification.md`).
- **Spangler confirmation email drafted** (`docs/uga/spangler-confirmation-email.md`).
- **R4/Y4 retention + audit-log policy** drafted, Gemini-reviewed (`docs/uga/retention-audit-policy.md`). Five red-flag fixes applied: tightened FERPA framing, bounded job-record retention at 18 months, US data-residency / breach-notification commitments in subprocessor section, institutional-first incident notification, removed "best-effort" from right-to-deletion.
- **Y5 cost cap + kill switch** shipped (`src/web/cost_cap.py`). Daily/weekly USD ceiling, env-driven, with `/api/admin/cost-status` endpoint. 19 tests.
- **MAX_CONCURRENT_JOBS** env var — replaces hardcoded `Semaphore(1)`. Default unchanged at 1; raise after observing prod memory + Anthropic ITPM headroom. 8 tests.
- **Per-user concurrent + hourly caps** (`src/web/user_caps.py`). Defaults 5 concurrent / 30 per hour, admins exempt. 16 tests.
- **Storage retention loop** (`src/web/retention.py`). Background daemon deletes old uploads + outputs, never touches active-job files, retains SQLite job records. `POST /api/admin/retention/cleanup` for ad-hoc runs. 15 tests.
- **Observability** (`src/web/observability.py`). Request-ID middleware (UUID4 or trusted upstream), threaded into log records via ContextVar + filter. Enhanced `/api/health` returns liveness + DB + queue depth + free disk + version. 12 tests.
- **Preprint reconciliation memo** (`docs/writeup/numbers-reconciliation.md`) — preprint's 86.7% (veraPDF failedChecks, full 125) and NOW.md v5's 86.1% (orchestrator issues, 48 unique) are different metrics, not contradictory. Action items captured for the post-Gemini-verification rerun.
- **Total**: 9 commits on `hardening/tier0`, 232 tests pass, no regressions.

**Deferred / scoped for next sessions:**
- Y1 Postgres migration (task #9) — bigger architectural shift, design conversation needed
- R1 ARQ + Redis queue (task #10) — depends on Postgres
- LLM retry-policy uniformization audit (task #12)
- 18-month job-record deletion job (task #13)
- Vertical scale OCI 2/12 → 4/24 (needs OCI console)
- Hostname decision (UGA subdomain vs independent)

**Evening — e2e suite + Postgres + ARQ (continued, all merged to master):**
- **E2E regression suite shipped** (`tests/e2e/`, 44 tests, ~10s). Mocks the orchestrator pipeline so it runs without LLM API spend. Coverage: auth flow, upload flow, caps + limits, admin endpoints, observability. Safety net for the bigger refactors. Marked `pytest.mark.e2e` for selective running. (commit `c9a732c`)
- **Y1 Postgres in-place migration shipped** (`src/web/db.py`, `migration/postgres-inplace`). New abstraction layer routes to SQLite (default) or Postgres (when `DATABASE_URL=postgresql://...`). `?` placeholders translated to `%s` automatically. `Row` class supports both positional and named access on both backends. Schema diffs encapsulated in `column_exists()`, `table_columns()`, `is_integrity_error()`, `begin_immediate()`. `psycopg[binary]` is an optional dep. Migration script `scripts/migrate_sqlite_to_postgres.py` verified end-to-end on real production data (2 users / 6 jobs / 1 transaction migrated cleanly). 25 unit tests + 10 live Postgres smoke tests. Not deployed — needs Postgres provisioned on Oracle host first. (commits `4079530`, `69b417c`)
- **R1 ARQ + Redis queue shipped** (`src/web/queue.py`, `queue/arq-redis`). Gated on `QUEUE_BACKEND=arq`; default remains threading.Thread for backward compat. New `_dispatch_job()` seam in app.py routes to ARQ or threading. Defensive fallback to threading if ARQ enqueue raises. Worker entry point at `scripts/run_arq_worker.py`. Verified live against local Redis (5/5 smoke tests including dedup). 12/12 unit tests without Redis. 44/44 e2e still pass. Not deployed — needs Redis provisioned + worker systemd unit. (commits `1a4aede`, `f704d8d`)

**Session totals (2026-04-25):**
- Commits to master: 14
- Net diff: ~3,500 lines (mostly tests + docs)
- Test suite: 1225 SQLite tests pass + 10 Postgres + 5 ARQ live
- Three new opt-in env vars at production-readiness boundary: `DATABASE_URL`, `QUEUE_BACKEND`, `MAX_CONCURRENT_JOBS`. All default to current behavior.
- Architecture-significant changes (Y1, R1) staged opt-in so a single env var flip switches them on, no code redeploy required.

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132 (2 OCPU / 12GB ARM — too small for university scale)
- **Benchmark detection (v5 raw-PDF, 2026-04-17, fresh clone)**: **80.00%** (100/125) with no dataset-specific metadata signals. Exactly matches the 2026-04-11 ceiling — predictor logic has not regressed. Per-task: functional_hyperlinks 100%, fonts_readability 93.33%, table_structure 80%, semantic_tagging 75%, color_contrast 73.33%, alt_text_quality 70%, logical_reading_order 66.67%. Benchmark data at `/tmp/PDF-Accessibility-Benchmark-fresh/`.
- **Remediation (v5 — just completed 2026-04-17, `/tmp/remediation_bench_v5`)**: **86.1% PDF/UA violation reduction** (6,227→865, veraPDF-verified), **7/48 fully compliant**, 47 improved, 1 unchanged, **0 regressed**. Run covers 48 unique PDFs — `remediation_benchmark.py` dedups byte-identical files across the 125 Kumar items, so "48" is the real content count, not a subset. $5.87 total, $0.12/doc, 118 min wall time. This is the first full measurement since v4c fixes (7.3-1, 7.18 /Dest, ToUnicode ligature fill) — real improvement over v4's 63.9% reduction / 4 compliant / 9 regressions.
- **Tests**: 1053 passing, 2 skipped, 3 pre-existing failures (`TestTailPolishFigureAlt`, unrelated to current session)
- **Publication**: arXiv preprint + blog post + TACCESS journal (no deadline pressure)
- **Kumar collaboration**: Lucy Wang + Anukriti Kumar confirmed methodology (byte-identical cannot_tell pairs are intentional — evidence is withheld at test time, not document-level). Anukriti offered co-authorship; meeting TBD.

## Detection methodology — what we actually measure
The Kumar benchmark labels items by withholding criterion-specific evidence at test time (confirmed by authors). Several items are byte-identical across labels, so a tool that reads the full PDF will produce the same prediction for both — this is design, not flaw. We report two numbers:
- **Raw-PDF analysis** (the meaningful floor): heuristics on struct tree + validator output only. **80.00% on 2026-04-17 fresh-clone measurement** (100/125). Generalizes to real faculty uploads.
- **Kumar-replication** (the benchmark-max): adds dataset-specific signals like PDF ModifyDate fingerprints left by the dataset creators. Good for replication-score comparisons, does NOT generalize. Not used as our headline.

## Key Numbers for Publication
| Metric | Value |
|--------|-------|
| **PDF/UA violation reduction (v5, 2026-04-17)** | **86.1%** (6,227 → 865, veraPDF-verified) |
| Fully PDF/UA compliant (v5) | 7/48 unique docs (14.6%) |
| Docs improved / unchanged / regressed (v5) | 47 / 1 / 0 |
| Average cost per doc (v5) | $0.12 (118 min wall time / 48 docs) |
| Detection accuracy (raw-PDF analysis, 2026-04-17) | 80.00% (100/125, fresh clone) |
| GPT-4-Turbo published baseline (for reference) | 85.0% |
| Kumar byte-identical finding | 13/125 items share PDFs across labels (intentional — authors confirmed) |

**v4 → v5 deltas:** 63.9% → 86.1% reduction, 4 → 7 compliant, 9 → 0 regressions. Note the item-count change: v4 counted 125 per-label items (with duplicates); v5 counts 48 unique files (dedup by resolved path in `remediation_benchmark.py:_collect_unique_docs`). For apples-to-apples comparison, v5's 48 covers the same document content as v4's 125 — Kumar reuses PDFs across label categories.

## Just shipped (2026-04-17)

- **v5 remediation benchmark complete** — 86.1% veraPDF violation reduction (6,227→865), 7/48 compliant, 0 regressions. `/tmp/remediation_bench_v5/v5_report_with_verapdf.md`.
- **v5 detection benchmark (fresh clone)** — **80.00% (100/125)**, matches the 2026-04-11 ceiling exactly. Predictor logic confirmed unchanged. `/tmp/detection_v5_raw_pdf_full.md`.
- **Root cause of the 80→68.75 "regression"**: our local `/tmp/PDF-Accessibility-Benchmark/` had been trimmed from 868 to 48 PDFs at some point; only 48 of 125 dataset items could resolve. Fresh clone restored all files. **Not a predictor issue.** Going forward: point benchmarks at `/tmp/PDF-Accessibility-Benchmark-fresh/`.
- **Bug fixed in `scripts/benchmark.py:1895`** — fallback path resolution now tries `data/inputs/` in addition to `inputs/`, and falls back to byte-identical duplicates across labels for the same openalex_id. Safety net if the dataset dir is ever partially trimmed again.
- **`dataset.json` restored** in trimmed `/tmp/PDF-Accessibility-Benchmark/` (original file) before the fresh clone — from github.com/Anukriti12/PDF-Accessibility-Benchmark raw contents.
- **Heuristic port 1/4 shipped**: `score_alt_text_quality` (from `scripts/benchmark.py:977`) now in `src/tools/validator.py`. Production `_check_1_1_1_alt_text` now FAIL-bad-quality / WARN-borderline / PASS-good. **"Figure 3" or "image.png" style captions now fail validation instead of silently passing.**
- **Heuristic port 2/4 shipped**: `struct_tree_probe` promoted from `scripts/` to `src/tools/`; comprehension now skips Gemini vision pass for images that already carry "good" alt text (per `score_alt_text_quality`). Port-1 and port-2 compose — the scorer decides which images skip captioning. Expected savings: a PDF with 10 good-alt figures previously made 3 Gemini batch calls; now makes 0. Skip logic smoke-tested; needs a proper integration test.
- **Heuristic port 3/4 shipped**: `per_table_th_counts` promoted from `scripts/benchmark.py:1636` to `src/tools/struct_tree_probe.py`. `_check_1_3_1_structure` now probes the PDF struct tree and surfaces "Struct tree /Table #N has 0 TH children (others in doc have headers — this specific table is malformed)" — actionable signal for documents where one table is broken while others look fine. Single-table malformed tables still caught by the existing aggregate check.
- **Heuristic port 4/4 shipped**: `is_severe_contrast_failure` added to `src/tools/contrast.py` (ported from `scripts/benchmark.py:1127`). `_check_1_4_3_contrast` now prefixes yellow-on-white (and similar unambiguous severe) failures with `SEVERE:` and prepends a summary line `⚠ N severe contrast failure(s) — fix these first`. Ordinary borderline ratios unchanged. Caught 4 archetypal patterns in unit tests.

## Heuristic ports — all shipped 2026-04-17
1. ~~`_alt_quality_score` → production validator~~ ✓
2. ~~`StructFacts` probe → production + skip-vision optimization in comprehension~~ ✓
3. ~~`per_table_th_counts` → production validator 1.3.1 (per-table struct-tree TH check)~~ ✓
4. ~~Yellow-on-white severe-contrast flag → production validator 1.4.3~~ ✓

No heuristics left in benchmark-only limbo. Any future "benchmark-chasing" finding should be promoted to production as part of the same change.

## Queued for overnight
- **Remediation benchmark rerun on fresh clone** (107 unique content hashes vs 48 in v5, ~$13 and ~4.5 hrs). Not urgent — v5 @ 48 unique is a valid measurement; overnight run expands coverage.

## Separate bug to track
- `TestTailPolishFigureAlt` (3 tests in `test_pdf_writer.py`): `apply_pdf_ua_tail_polish` returns `None` in test scenario, causing `'NoneType' object has no attribute 'success'`. Also surfaces as "PDF/UA tail polish failed" warning during v5 remediation. Pre-existing, not caused by this session's edits.

## What we discovered this session (not yet actioned)

### 1. Heuristic gaps: benchmark-only signal not used in production
Four scoring signals developed for `scripts/benchmark.py` are not wired into the production pipeline. Promoting them helps real faculty output, not just benchmark numbers. Memory: `project_heuristic_gaps_benchmark_to_production.md`.
- `_alt_quality_score` (benchmark.py:977): tiered good/borderline/bad alt scoring. Production validator only catches *missing* alt and a few auto-caption patterns; a vision caption returning "Figure 3" silently passes today.
- `StructFacts` probe (`scripts/struct_tree_probe.py`): seven struct-tree signals (has_struct_tree, figure_count, figures_with_alt, figure_alt_texts, heading_count, table_count, table_th_count). Zero production files import these. Payoff: comprehension can detect "input already has good alt on every figure" and skip Gemini vision pass.
- `_per_table_th_counts` (benchmark.py:1636): per-table TH counts, not aggregate. Catches one malformed `/Table` hidden among good ones.
- Yellow-on-white contrast flag (benchmark.py:1127): unambiguous severe contrast surfaced to prominence regardless of total count.

Estimated wiring: 1-2 days each. Start with `_alt_quality_score` — most mechanical, clearest downstream use (reject weak LLM outputs, prioritize human-review UI).

### 2. LLM reallocation: the strategy + review calls are waste
Memory: `project_llm_reallocation_plan.md`.
- **Strategize (Claude)** is a template — the prompt prescribes "EVERY image → `set_alt_text`, always `set_title` + `set_language`, contrast failures → `fix_all_contrast`." A 20-line deterministic mapper would produce the same output.
- **Review (Claude)** is asked to judge alt-text accuracy without seeing the images. It hallucinates narrative around veraPDF output that is already authoritative.
- **Redirect** the current review budget to a second vision pass that verifies alt text against images: "does this accurately describe the image? rate 1-5; if <4, what's missing?" Same cost envelope, real verification. Feeds downstream: items below threshold auto-flag for human review UI.
- Other LLM wins worth funding: math-image → MathML, language-of-parts (WCAG 3.1.2), complex-table summaries, reading-order repair for multi-column PDFs (judge already exists in benchmark).

### 3. Measurement gap: we optimize veraPDF, not student usability
Memory: `project_measurement_gap.md`.
- veraPDF correlates with usability but isn't identical. A 0-violation PDF can have useless alt ("image.png"); a 1000-violation PDF can read fine.
- We have zero ground-truth alt-accuracy, zero screen-reader testing, zero real faculty trials.
- Project A (font glyph injection) is sized against a metric with no confirmed student-impact link. Before committing weeks of fontTools work, run a small user-study (3-5 blind/low-vision sessions on real outputs, ~1 week). If missing ligatures matter, try cm-unicode *substitution* (~2 days) before glyph injection.

## Plan (order of operations)

1. **Now** — benchmark v5 rerun in flight. Await output; update numbers above.
2. **Next** — wire `_alt_quality_score` + `StructFacts` into production validator and comprehension (1-2 days).
3. **Then** — replace reviewer LLM call with vision-based alt verification (same cost, real work).
4. **Parallel when opportunity allows** — schedule a small user-study to anchor priorities before committing to Project A font engineering.
5. **Publication** — rewrite headline around remediation numbers + raw-PDF detection floor. Drop Kumar-protocol 96.8% framing.

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
