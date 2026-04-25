---
status: reconciliation memo — read before rewriting the preprint
last_edited: 2026-04-25
purpose: explain why preprint numbers and NOW.md numbers don't match
---

# Preprint v4/v5 Numbers Reconciliation

## TL;DR

The "86.7% reduction" in the current preprint draft and the "86.1% reduction" in NOW.md are **two different measurements with different denominators**. They are not contradictory. They measure different things.

| Metric | Source | Counts | Denominator | Latest result |
|---|---|---|---|---|
| **veraPDF `failedChecks`** | preprint draft, `ua_fixes_results.md` | every element-level violation | full 125-item benchmark (with byte-identical duplicates) | **194,394 → 25,882 (86.7%)** |
| **Orchestrator `issues`** | NOW.md v5, `scripts/remediation_benchmark.py` | internal-validator-reported issues | 48 unique PDFs (Kumar dedup) | **6,227 → 865 (86.1%)** |

The numbers happen to be near each other in percentage terms; they are not the same numbers expressed differently. A document with one badly-tagged element can produce 100+ failed checks but a single internal-validator issue.

## Why this matters for the preprint

**veraPDF `failedChecks` is the right metric to publish.** It's independently verifiable, doesn't depend on our internal validator's correctness, and is what reviewers will reproduce. The orchestrator's `issues` count is for internal tracking and triage.

The preprint already uses the right metric. The problem is the underlying measurement is stale.

## What's stale in the preprint

The 194,394 → 25,882 measurement reflects the pipeline as of **pre-2026-04-17**, on the full 125-item benchmark. Since that measurement, three material changes have shipped:

1. **2026-04-14/15: v4c fixes** (commits `ab11623`, `511c8c9`, `f8b0f03`) — 7.3-1 empty-Alt fill, 7.18 /Dest annotation /Contents fill, ToUnicode ligature gap fill. Per NOW.md "v4c smoke" on 19 exemplar docs: 2,444 → 1,867 violations (a 23.6% drop *after* the preprint baseline measurement). Extrapolated to the full 125-item set, this should push the preprint's 25,882 figure noticeably lower.

2. **2026-04-17: heuristic ports** (commit `f5d07b9`) — alt_quality scorer + StructFacts probe + per-table TH probe + severe contrast flag promoted from benchmark to production validator. Affects the orchestrator's `issues` metric, not veraPDF directly, but may reduce LLM hallucination on alt text → marginal indirect effect on PDF/UA.

3. **2026-04-25: strategy-mapper migration** (commit `d7fe400`) — moved ~70% of strategy-phase template work into comprehension. Not yet measured. Could go either way on PDF/UA numbers; expected slight improvement (less alt-text hallucination).

## What needs to happen before publication

Per NOW.md (already queued, blocked on Gemini identity verification):

1. **Fresh-clone full-125 benchmark run with the current pipeline**, capturing veraPDF `failedChecks` aggregate. Estimated $13, ~4.5 hrs overnight, 107 unique content hashes (broader coverage than v5's 48).
2. **Run Phase B comparison** (pre-mapper-migration vs post-) on that same benchmark to validate the 2026-04-25 migration didn't regress numbers.
3. **Update preprint** with the resulting numbers. Most likely: a number lower than 25,882 / better than 86.7%, but on the same metric.

Until then: the preprint as-currently-written isn't *wrong*, but it's optimistic about how recent the measurement is and conservative about how good the numbers are now.

## Stat hygiene for the rewrite

- Always say which metric. Don't claim "86% reduction" without saying *of what*.
- Headline metric is **veraPDF `failedChecks`** because it's independently verifiable.
- Orchestrator `issues` belongs in §3 (system architecture) as an internal-validator description, NOT in §4 (evaluation).
- Document counts: full 125 (with 13 byte-identical pairs) for veraPDF runs is the published-benchmark choice; 48 unique for content-deduped analysis is also legitimate but should be labeled clearly.
- Cost / wall-time / per-doc figures derive cleanly from the same run regardless of which metric we headline.

## Action items

- [ ] (blocked) Run the fresh-clone full-125 benchmark when Gemini verification clears.
- [ ] After the run, write up a single canonical "v6 results" report capturing veraPDF metrics + orchestrator metrics + Phase B comparison. File at `docs/benchmark_results/v6_results.md`.
- [ ] Update preprint §4.1 from 194,394 → 25,882 to v6 numbers, with metric clearly labeled.
- [ ] Update NOW.md headline to use veraPDF metric (not orchestrator `issues`) for any externally-cited figure.
- [ ] Add a one-line "metric: veraPDF failedChecks across N documents" caption to every benchmark table going forward.
