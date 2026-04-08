# PDF/UA Post-Processing Results — 125-doc Benchmark

Applied Track C (XMP pdfuaid metadata) and Track A (artifact-mark untagged
content + convert orphan BDCs) to all 125 already-iText-tagged outputs from
the Kumar et al. PDF Accessibility Benchmark in
`/tmp/remediation_bench_full/`.

## Headline (true veraPDF `failedChecks` aggregate)

| Metric | Before | After | Δ | % |
|---|---:|---:|---:|---:|
| **Total failed checks** | **194,394** | **84,199** | **−110,195** | **−56.7%** |
| Total failed rules | 731 | 536 | −195 | −26.7% |
| Fully PDF/UA-1 compliant docs | 0 | 0 | 0 | — |

The remaining 84,199 failed checks are mostly form XObject content (which
v1 of Track A does not recurse into) plus font issues (rules 7.21.x) that
neither track addresses. See spec §"Out of scope (v1 limits)".

## Per-status breakdown

| Status | Count |
|---|---:|
| `success` (both tracks applied) | 122 |
| `track_a_no_improvement_kept_c` (Track C kept, Track A reverted) | 3 |
| **Total** | **125** |

Three documents had Track A reverted because the artifact-marking pass did
not produce a measurable veraPDF improvement on them. Track C still applied
to all 125 docs.

## Per-rule failing-doc count (before → after)

| Rule | Description | Before | After |
|---|---|---:|---:|
| **5-1** | PDF/UA version in XMP | **119** | **0** |
| **7.1-10** | ViewerPreferences /DisplayDocTitle | **14** | **0** |
| 7.1-3 | Content tagged or artifact | 125 | 125* |
| 7.18.1-2 | Annotation in struct tree | 65 | 58 |
| 7.18.5-1 | Links tagged per ISO 32000 | 65 | 57 |
| 7.18.5-2 | Links contain /Contents | 65 | 57 |
| 7.21.4.1-1 | Font programs embedded | 64 | 56 |
| 7.4.2-1 | Weakly structured doc | 38 | 22 |
| 7.21.4.2-2 | CIDSet stream | 27 | 19 |
| 7.21.4.1-2 | Embedded font glyphs | 23 | 15 |
| 7.3-1 | Figure tags need alt | 21 | 23 |
| 7.21.5-1 | Glyph widths | 19 | 16 |
| 7.21.7-1 | Font character map | 18 | 23 |
| 7.21.8-1 | .notdef glyph | 10 | 4 |

*Rule 7.1-3 still fires on every doc because every benchmark PDF has some
residual untagged content in form XObjects that Track A v1 does not enter.
The per-doc *failed check count* for this rule dropped dramatically — from
~1,600 to ~600 average — even though the binary "rule fails" flag stays at
125/125. Track A's win is captured by the 110,195 check reduction, not the
rule count.

## Track C results

Track C eliminated **rules 5-1 and 7.1-10 across every doc that previously
failed them** — exactly as predicted by the spec. 119 docs got a fresh
`<pdfuaid:part>1</pdfuaid:part>` element in XMP, and 14 got a new
`/ViewerPreferences /DisplayDocTitle true`.

## Notes on the 3 partial cases

The Track A walker reverts when its post-fix veraPDF check shows no
improvement over the baseline. The 3 docs that hit this gate are:
- `functional_hyperlinks/cannot_tell/W2991007371`
- `functional_hyperlinks/failed/W2991007371`
- `table_structure/not_present/W2810718311`

These are all documents where the orphan BDCs are inside form XObjects
(which we do not recurse into) and Track A's depth-0 walker found nothing
new to wrap. Track C still applied successfully.

## Cost

**Zero API cost.** Both tracks are local PDF byte manipulation. Wall time
was 26 minutes for 125 docs (sequential, dominated by the 250 verapdf
verification calls — 2 per doc).

## Reproducing

```bash
python3 scripts/apply_ua_fixes.py --results-dir /tmp/remediation_bench_full
```

Reads `remediation_benchmark_results.json` from the directory, applies
both tracks to every successful doc, writes `ua_fixes_results.json` and
`ua_fixes_results.md` (an aggregate using the wrapper's checks-array
counts, which are capped at 100/rule and so understate the true reduction
for high-volume rules like 7.1-3). For the true `failedChecks` aggregate
shown above, see the diagnostic in this commit's message.

## Next steps

- **Form XObject recursion** is the obvious v2 work — would push 7.1-3
  closer to elimination.
- **Font repair (rules 7.21.x)** is the larger remaining blocker for
  full PDF/UA-1 compliance. Requires font tooling we don't have.
