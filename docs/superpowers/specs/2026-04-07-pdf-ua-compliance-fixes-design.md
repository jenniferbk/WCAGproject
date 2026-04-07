# PDF/UA Compliance Post-Processing — Design

**Status:** Draft for review
**Date:** 2026-04-07
**Context:** Post [Full remediation benchmark results](../../benchmark_results/remediation_benchmark_results_with_verapdf.md) — 125/125 docs remediated, 1076 → 672 veraPDF failed rules (−37.5%). This spec designs the next iteration: reducing the 672 remaining rule failures by ~70% via targeted post-processing of already-tagged outputs.

## Problem

Our full 125-doc remediation benchmark leaves 672 veraPDF PDF/UA-1 failed rules (197,574 failed *checks*) across the 125 outputs. Per-rule analysis reveals a single rule dominates:

| Rule | Failed checks | % of total | Description |
|---|---:|---:|---|
| **7.1-3** | **179,291** | **90.7%** | Content shall be marked as Artifact or tagged as real content |
| 7.2-34 | 4,140 | 2.1% | Natural language for text in page content |
| 7.18.1-2 | 3,148 | 1.6% | Annotation in struct tree |
| 7.18.5-1 | 3,146 | 1.6% | Links tagged per ISO 32000 |
| 7.18.5-2 | 3,146 | 1.6% | Links contain alt description via /Contents |
| 7.21.x (fonts) | 4,290 | 2.2% | Font embedding, CIDSet, glyph widths |
| 5-1 | 118 | 0.06% | PDF/UA version in XMP |
| 7.1-10 | 11 | — | ViewerPreferences /DisplayDocTitle |
| 7.1-8 | 7 | — | Catalog /Metadata stream |
| all others | ~179 | — | |
| **TOTAL** | **197,574** | 100% | |

**Fixing 7.1-3 alone reduces failed checks by ~90%.** This single rule — "every content item in the page stream must be either tagged as real content or marked as /Artifact" — is dormant on untagged PDFs and fires aggressively once iText adds a struct tree. The 4 "regressions" we saw in the benchmark (documents that got *worse* by failed-rule count) are all from this rule activating on previously-untagged content. iText is not producing the artifact markers; we need to add them in a post-processing pass.

The secondary opportunity is XMP metadata: rules 5-1, 7.1-8, and 7.1-10 are trivial one-shot fixes that together eliminate ~136 failed checks with no risk.

## Goals

1. **Primary:** reduce veraPDF failed checks from 197,574 → ~18,000 (−91%) across the 125-doc benchmark by implementing a content-stream walker that marks untagged content as `/Artifact`.
2. **Secondary:** reduce failed checks by an additional ~136 via PDF/UA XMP metadata polish.
3. **Do not regress** any document — every doc's failed-check count must stay the same or decrease after post-processing.
4. **Safe for production:** the two new functions must work as standalone post-processors (no-dependency on fresh iText runs) so they can be validated on the existing 125 output PDFs in a fast iteration loop before being integrated into the main pipeline.

## Non-goals

- **Rule 7.2-34 (natural language on text runs).** Requires struct tree modification to add `/Lang` on Span elements; tracked under a future "Tier B" spec, pending discoveries from Track A's edge cases.
- **Rules 7.18.1-2, 7.18.5-1, 7.18.5-2 (link tagging polish).** Same rationale — tracked for a future spec.
- **Font rules 7.21.x** (~4,290 failed checks). Requires font repair / re-embedding tooling we don't have. Explicitly deferred.
- **Full PDF/UA-1 compliance for every document.** The source PDFs are deeply broken (scanned academic papers, 20-year-old magazines). Reaching zero failed rules for all 125 is not the goal of this spec. Reducing the aggregate failure count by ~70% of the remaining baseline *is*.

## Architecture

Two standalone functions in `src/tools/pdf_writer.py`, each taking `(pdf_path) → Result` and modifying the PDF in place. An orchestration script `scripts/apply_ua_fixes.py` applies them sequentially to a directory of PDFs and verifies each output.

```
scripts/apply_ua_fixes.py DIR
  └─ for each PDF:
      ├─ apply_pdf_ua_metadata(pdf)             # Track C — fast, safe
      ├─ mark_untagged_content_as_artifact(pdf) # Track A — content stream walker
      └─ verify(pdf)                            # Gate B (iteration) or Gate C (commit)
```

Both functions operate on already-iText-tagged PDFs. They do not re-run the full remediation pipeline. Once validated on the 125 existing outputs (fast iteration loop, ~15 min per cycle via `scripts/verapdf_postprocess.py`), they will be integrated into `src/agent/executor.py::execute_pdf()` as post-tagging steps.

## Track C — XMP metadata polish

### Function signature

```python
@dataclass
class MetadataResult:
    success: bool
    changes: list[str]          # human-readable change log
    error: str = ""

def apply_pdf_ua_metadata(pdf_path: str | Path) -> MetadataResult:
    ...
```

### Behavior

Reads and modifies three things in a single save:

**1. XMP metadata stream** (rule 5-1, "PDF/UA version shall be specified"):
   - Locate `/Metadata` stream on the document catalog
   - Parse with `lxml.etree` as XMP (which is RDF/XML)
   - Ensure the `pdfuaid` namespace is declared: `xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"`
   - Add or update `<pdfuaid:part>1</pdfuaid:part>` inside the first `rdf:Description` element
   - Preserve every existing element (title, author, subject, keywords, dc:*, xmp:*, pdf:*)
   - Serialize and write back via `doc.update_stream(xref, new_bytes, new=True)`

**2. ViewerPreferences/DisplayDocTitle** (rule 7.1-10, "window title shall show the document title"):
   - Set `/ViewerPreferences << /DisplayDocTitle true >>` on the catalog via `doc.xref_set_key`
   - Preserve any existing ViewerPreferences entries (don't clobber)

**3. Catalog /Metadata key** (rule 7.1-8, "catalog shall contain a Metadata key"):
   - Ensure the catalog has a `/Metadata` entry pointing to the XMP stream
   - This is usually already true if (1) worked, but some PDFs have a catalog without a Metadata link

### Edge cases

- **No existing XMP stream**: synthesize one from `doc.metadata` (title, author, creator, keywords available via fitz) with the pdfuaid element included from the start.
- **XMP exists but is not valid XML**: fall back to synthesis. Log a warning.
- **Already has `pdfuaid:part`**: no-op.
- **ViewerPreferences already has DisplayDocTitle false**: overwrite to `true`.
- **ViewerPreferences has other entries (Direction, FitWindow, etc.)**: preserve them.

### Expected impact

Rule | Failed checks before | Failed checks after
---|---:|---:
5-1 | 118 | 0
7.1-8 | 7 | 0
7.1-10 | 11 | 0

Combined: **−136 failed checks and −136 failed rule-instances** across the 125 outputs (each of these rules fires once per doc, so checks and rule-instances decrease by the same amount).

## Track A — content-stream artifact walker

### Function signature

```python
@dataclass
class ArtifactMarkingResult:
    success: bool
    pages_modified: int
    tokens_wrapped: int
    pages_skipped: int          # empty streams, already-clean pages
    errors: list[str]

def mark_untagged_content_as_artifact(pdf_path: str | Path) -> ArtifactMarkingResult:
    ...
```

### Behavior

For each page in the document:

1. **Read the content stream(s).** PyMuPDF exposes `page.read_contents()` which returns the concatenated content. For write-back, the page's `/Contents` can be a single stream xref or an array `[s1 s2 s3]`. Strategy: read concatenated, rewrite, write the new content into the first stream xref, zero out the others (set their length to 0 via stream update).
2. **Tokenize** using the existing `_tokenize_content_stream(bytes)` function already in `pdf_writer.py` (line 1071).
3. **Walk tokens maintaining BDC depth.** Track a counter: increment on `BDC`, decrement on `EMC`. BDC opens when the previous tokens emit a tag name followed by `BDC` (e.g. `/P BDC` or `/Artifact BDC` or `/Span << /MCID 5 >> BDC`). Our existing tokenizer already understands BDC/EMC.
4. **Identify depth-0 content runs.** A token is "content-producing" if its operator is one of: `Tj TJ ' " Do S s f F f* B b b* B* BT BI/ID/EI` plus path-painting (`m l c v y re h n S s f F f* B B* b b* n`). Plus state operators (`q Q cm Tf Tr Tc Tw Tz TL Ts gs`) that fall between content operators — we keep them inside the wrapper so the wrapped run preserves its own graphics state scope.
5. **Wrap each contiguous depth-0 run in `/Artifact BDC ... EMC`.** Insert `/Artifact BDC\n` before the first content token of the run and `EMC\n` after the last. Multiple disjoint runs on the same page get separate wrappers.
6. **Reassemble** with the existing `_reassemble_stream(tokens)` function, then write back via `doc.update_stream(xref, new_bytes, new=True)`.
7. **Track per-page counters** for the result dataclass.

### Depth-0 content run detection — precise rules

A "run" starts when, at BDC depth 0, we encounter any content-producing or state-setting operator. The run continues through subsequent content-producing and state-setting operators until we hit:
- A `BDC` (opens a tag — run ends)
- An `EMC` at depth 0 (shouldn't happen — invalid PDF — but defensively end the run)
- End of content stream

Pure whitespace between tokens is preserved byte-for-byte (the tokenizer keeps it).

### Edge cases

- **Page with no content stream**: skip. Count in `pages_skipped`.
- **Page entirely inside an existing BDC**: no depth-0 content runs found; skip. Count in `pages_skipped`.
- **Inline images** (`BI ... ID ... EI`): treat as a single content unit. Our tokenizer already groups these correctly.
- **Content stream array** (`/Contents [ref1 ref2 ref3]`): read via `page.read_contents()` which concatenates. Write back: put the full rewritten content into stream `ref1`, update streams `ref2..refN` to empty. This collapses multi-stream pages into single-stream, which is legal and simpler.
- **`Do` operator referencing a form XObject**: wrap the `Do` at page level. **Do not recurse into the XObject's own content stream in v1.** If XObject recursion turns out to be a significant source of remaining 7.1-3 failures, add it in v2.
- **Nested BDC (depth > 1)**: depth counter handles this. Only depth-0 content gets wrapped.
- **MCID-referenced content already inside BDC**: untouched. Our depth tracker guarantees we only ever touch depth-0 content.
- **Graphics state `q...Q` at depth 0**: the `q` is a state operator, it's part of a content run and gets included inside the wrapper. The `Q` closes the state scope and is similarly included. This preserves the page's visual appearance.
- **Encrypted PDFs**: fitz handles decryption transparently if the password is empty. Error out with `success=False` otherwise.
- **PDF with damaged content stream that fails tokenization**: log error, skip page, continue with other pages.

### Out of scope (v1 limits — document explicitly)

- **Form XObject recursion.** We do not enter `Do`-referenced XObjects to apply the same treatment to their content streams. If an XObject has untagged content, its failures stay. Add in v2 if measurements show this is material.
- **Deeply nested content stream arrays (>10 streams)**: collapsed into stream[0]. Preserves behavior but loses the original chunking.

### Expected impact

Rule | Failed checks before | Failed checks after (estimate)
---|---:|---:
7.1-3 | 179,291 | ~200 (from XObject content we don't recurse into)

This is **−179,091 failed checks** — the headline number of this spec.

## Verification — gated validation

Every call to either function must pass verification before we accept the output. Three gates, graduated by strictness:

### Gate A — minimal (always-on for both tracks)

After saving the modified PDF:
1. **Re-open in PyMuPDF.** If `fitz.open()` fails, revert. The PDF is corrupted.
2. **Extract text via `page.get_text()` on each page.** Compare against the pre-fix text extraction. If any page's text changed, revert. Our modifications should not affect visible text.
3. **Check page count unchanged.**

### Gate B — structural (iteration loop)

Gate A plus:
4. **Run veraPDF on the modified PDF.** Compare failed-rules set against the pre-fix set.
   - If any new rule appears in the post set that wasn't in the pre set → revert and log
   - If the targeted rule's failed checks did not decrease → log (not fatal)
   - If total failed checks decreased → accept

### Gate C — visual (final commit)

Gate A + Gate B plus:
5. **Render each page to a 150 DPI PNG before and after.** Compute `_pixel_diff()` (already exists in `pdf_writer.py`). If any page has >0.5% pixel diff, revert and log.

**Gating strategy:** use Gate B during iteration (~15-20 min total for 125 docs). Use Gate C for the final full re-run before committing results. Visual diff is slow (~30 min for 125 docs × 2 renders) so we only pay that cost once.

## Orchestration — `scripts/apply_ua_fixes.py`

```
usage: apply_ua_fixes.py --results-dir DIR [--verification {A,B,C}] [--limit N]
```

Reads a `remediation_benchmark_results.json` from DIR, iterates every successful doc, applies Track C then Track A to its `output_path`, runs the selected verification gate, and writes an enriched results file `ua_fixes_results.json` plus a markdown report `ua_fixes_results.md`.

### Failure handling

If Track A fails Gate A or B for a specific doc:
1. Restore the pre-Track-A copy of the PDF (we keep a temp copy)
2. Keep the Track C changes (they're independent)
3. Log the failure with doc ID and reason
4. Continue with the rest

Track C failures (rare) are treated the same way — restore pre-Track-C copy, skip, continue.

### Output report sections

1. **Aggregate**: total failed checks before/after, rule-level deltas, success/revert counts per track
2. **Per-rule impact**: rule-by-rule checks eliminated, matching the research table above
3. **Reverts**: documents where verification failed, with the verification reason
4. **Per-doc detail**: docs listed with pre/post failed checks and which tracks applied

## Iteration loop

```
Day 1 session:
1. Implement Track C                                    [~45 min]
2. Run Track C on /tmp/remediation_bench_full,          [~20 min]
   verify Gate B, measure delta
3. If delta matches expected (~136 checks, ~250 rules), commit track C
4. Implement Track A                                    [~2-3 hours]
5. Smoke: 2 diverse docs manually, inspect output       [~15 min]
6. Run Track A on 5-doc diverse set, Gate C (visual)    [~10 min]
7. If clean, run Track A on all 125 with Gate B         [~25 min]
8. Measure final delta vs. expected
9. If on target, commit Track A
10. Executor integration for both                       [~30 min]
11. 5-doc end-to-end smoke through execute_pdf          [~15 min]
12. Commit executor integration
```

**Total estimated effort: 4-6 hours** of focused work including edge case debugging.

## Testing

### Unit tests (added to `tests/test_pdf_writer.py`)

- `test_apply_pdf_ua_metadata_adds_pdfuaid_part` — create a PDF with XMP, apply, verify pdfuaid element present
- `test_apply_pdf_ua_metadata_preserves_existing_xmp` — verify dc:title, dc:creator survive
- `test_apply_pdf_ua_metadata_synthesizes_when_no_xmp` — PDF without /Metadata gets a fresh XMP with pdfuaid
- `test_apply_pdf_ua_metadata_sets_display_doc_title` — ViewerPreferences/DisplayDocTitle=true
- `test_apply_pdf_ua_metadata_preserves_other_viewer_prefs` — existing FitWindow=true survives
- `test_apply_pdf_ua_metadata_idempotent` — running twice produces the same output

- `test_mark_untagged_content_wraps_page_footer` — PDF with a tagged body and an untagged page footer, verify footer gets wrapped in /Artifact
- `test_mark_untagged_content_preserves_tagged_content` — MCID-marked content inside /P BDC stays untouched
- `test_mark_untagged_content_handles_nested_bdc` — /P BDC /Span BDC content EMC EMC stays untouched
- `test_mark_untagged_content_handles_graphics_state` — q/Q wraps preserve text appearance
- `test_mark_untagged_content_empty_stream` — page with no content stream is skipped gracefully
- `test_mark_untagged_content_already_all_tagged` — no-op for fully tagged page
- `test_mark_untagged_content_round_trip_text_extraction` — text extraction matches byte-for-byte before and after
- `test_mark_untagged_content_inline_image` — inline image (BI/ID/EI) wrapped as single unit
- `test_mark_untagged_content_do_operator` — Do operator at page level wrapped

### End-to-end tests

- `test_ua_fixes_on_benchmark_doc` — apply both tracks to one benchmark output, verify veraPDF failed checks drop, text extraction identical. Uses the Kumar et al. benchmark PDF (already verified available in earlier test work).

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Content-stream walker corrupts PDFs | Medium | High | Three-gate verification; revert-on-failure per doc; unit tests cover each edge case |
| Visual regression (text renders differently) | Low | High | Gate C pixel diff on final commit run |
| iText already marks some content as artifact and we double-wrap | Low | Medium | Depth tracker sees existing /Artifact BDC as a BDC — won't re-wrap |
| XMP parse failure on a real benchmark doc | Medium | Low | Fall back to synthesis; log but continue |
| Our changes interact badly with executor's later phases (review, reparse) | Medium | Medium | 5-doc end-to-end smoke test after executor integration |
| XObject recursion non-impl leaves residual 7.1-3 failures | Certain | Low | Measured explicitly in v1 output; added in v2 if material |
| The `_tokenize_content_stream` we rely on has edge case bugs | Low | Medium | Already battle-tested on 125 docs during contrast fixes; add targeted unit tests for artifact-relevant cases |

## Expected final numbers

Arithmetic: Track C removes one failing rule-instance per doc it fixes (118 for 5-1 + 7 for 7.1-8 + 11 for 7.1-10 = 136 rule-instances, 136 checks). Track A removes one 7.1-3 failing rule-instance per doc (125 total) and ~179,000 of 179,291 failed checks. Assuming independence of fixes:

| Metric | Baseline (source PDFs) | After current pipeline (commit a7b139b) | After Track C | After Track A | After both |
|---|---:|---:|---:|---:|---:|
| Total failed rules | 1,076 | 672 | ~536 | ~547 | **~411** |
| Total failed checks | ~400,000+ (unmeasured) | 197,574 | ~197,438 | ~18,574 | **~18,438** |
| % rule reduction from 1076 baseline | — | 37.5% | 50.2% | 49.2% | **61.8%** |
| % check reduction from 197,574 post-pipeline | — | — | 0.07% | 90.6% | **90.7%** |
| Docs fully PDF/UA compliant | 0 | 0 | 0 | maybe 1-3 | **1-3** |
| Docs improved over input | — | 113 | ~120 | ~120 | **~120+** |

**Two honest progress indicators:**
- **Failed rule-instances** — the coarse metric everyone reports. Both tracks together reduce this by ~40% of the post-pipeline baseline (672 → 411).
- **Failed checks** — the fine-grained violation count. Track A alone dominates this metric because 7.1-3 fires ~1,434 checks per doc on average. Both tracks together reduce checks by ~90.7%.

The two metrics diverge because 7.1-3 is one rule but thousands of check instances per doc. Fixing it shrinks checks dramatically but only subtracts ~125 rule-instances. Conversely, the small metadata rules (5-1 et al.) subtract from both equally.

Neither "fully compliant" count is expected to be high. The source documents have structural font issues (rules 7.21.x, ~4,290 failed checks across 56-plus docs) that neither track touches. Reaching zero failed rules on a doc requires either pristine source fonts or deferred Tier D font work.

## Success criteria

1. **Track C applied to all 125 docs reduces 5-1 failures from 118 to 0.** Independently verifiable via veraPDF.
2. **Track A applied to all 125 docs reduces 7.1-3 failed checks by ≥85%** (allowing for some residual from XObjects we don't recurse into).
3. **Zero docs regressed on failed-check count** after both tracks.
4. **Zero docs with >0.5% pixel difference** in the visual diff gate.
5. **Text extraction byte-exact** pre/post for every page of every doc.
6. **Executor integration passes a 5-doc end-to-end smoke** without new failures.

If we hit all six, we commit and update NOW.md with the new headline number.
