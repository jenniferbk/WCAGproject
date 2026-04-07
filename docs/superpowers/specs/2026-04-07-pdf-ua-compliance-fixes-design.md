# PDF/UA Compliance Post-Processing — Design

**Status:** Draft for review
**Date:** 2026-04-07
**Context:** Post [Full remediation benchmark results](../../benchmark_results/remediation_benchmark_results_with_verapdf.md) — 125/125 docs remediated, 1076 → 672 veraPDF failed rules (−37.5%). This spec designs the next iteration: targeted post-processing of already-tagged outputs to eliminate the majority of the remaining 197,574 failed checks, with exact impact measured empirically.

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

1. **Primary:** implement a content-stream walker that marks untagged content as `/Artifact` on all 125 benchmark outputs, eliminating the majority of rule 7.1-3's 179,291 failed checks. The exact residual (from form XObject content we don't recurse into) is measured empirically and documented.
2. **Secondary:** eliminate all 136 failed checks from rules 5-1, 7.1-8, and 7.1-10 via PDF/UA XMP metadata polish. This is a deterministic target.
3. **No new rule types introduced.** Every doc's set of failing rule IDs after post-processing must be a subset of its set before. If our wrapping ever triggers a new rule on any doc, that doc reverts.
4. **Text extraction preserved.** `fitz.page.get_text()` must produce byte-exact output before and after both tracks on every page of every doc. (Text extraction in PyMuPDF reads content-stream operators regardless of BDC marking, so /Artifact wrapping should not affect it — we verify this early in the iteration loop.)
5. **Safe for production:** the two new functions work as standalone post-processors (no dependency on fresh iText runs) so they can be validated on the existing 125 output PDFs in a fast iteration loop before integration into the main executor.

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
    pages_modified: int             # pages where at least one wrapper was inserted
    artifact_wrappers_inserted: int # total count of /Artifact BDC/EMC pairs added
    pages_skipped: int              # empty streams, already-clean pages
    errors: list[str]

def mark_untagged_content_as_artifact(pdf_path: str | Path) -> ArtifactMarkingResult:
    ...
```

### Behavior

For each page in the document:

1. **Read the content stream(s).** PyMuPDF exposes `page.read_contents()` which returns the concatenated content. For write-back, the page's `/Contents` can be a single stream xref or an array `[s1 s2 s3]`. Strategy: read concatenated, rewrite, write the new content into the first stream xref, zero out the others (set their length to 0 via stream update).
2. **Tokenize** using the existing `_tokenize_content_stream(bytes)` function already in `pdf_writer.py` (line 1071).
3. **Walk tokens maintaining BDC depth.** Track a counter: increment on `BDC`, decrement on `EMC`. BDC opens when the previous tokens emit a tag name followed by `BDC` (e.g. `/P BDC` or `/Artifact BDC` or `/Span << /MCID 5 >> BDC`). Our existing tokenizer already understands BDC/EMC.
4. **Classify each operator as content-producing or state-setting.** See the precise operator list below. This is the critical distinction: state operators don't violate rule 7.1-3 and don't need wrapping; content-producing operators do.
5. **Walk tokens maintaining BDC depth and current run state.** See "Depth-0 content run detection" below for the exact state machine.
6. **Wrap each contiguous depth-0 run in `/Artifact BDC ... EMC`.** Insert `/Artifact BDC\n` before the first token of the run and `EMC\n` after the last. Multiple disjoint runs on the same page get separate wrappers.
7. **Reassemble** with the existing `_reassemble_stream(tokens)` function, then write back via PyMuPDF's content-stream update API (verify exact name during impl — candidate is `doc.update_stream(xref, new_bytes)`).
8. **Track per-page counters** for the result dataclass.

### Operator classification

**Content-producing** (these MUST be inside a BDC/EMC pair to satisfy 7.1-3):
- Text showing inside `BT...ET`: `Tj TJ ' "`
- Inline image: the `BI ... ID ... EI` triple (treat as one atom)
- XObject reference: `Do` (includes image XObjects and form XObjects)
- Path painting: `S s f F f* B B* b b*`
- Shading: `sh`

Note: the path construction operators (`m l c v y re h`) and clipping operators (`W W*`) do not themselves produce output — only the painting operator at the end of a path sequence does. For wrapping purposes we treat a path construction + painting sequence as a content unit anchored by the painting op.

**State-setting** (do NOT produce output, do NOT require tagging):
- Graphics state save/restore: `q Q`
- Transform: `cm`
- Text state: `Tf Tr Tc Tw Tz TL Ts`
- Text positioning: `Td TD Tm T*`
- Graphics state parameter: `gs`
- Color: `rg RG g G k K sc SC scn SCN cs CS`
- Path style: `w J j M d ri i`
- Clipping flags: `W W*` (cause subsequent path to clip; no output on their own)
- Path construction: `m l c v y re h n`
- Text object markers: `BT ET`

State operators at depth 0 are **left alone** — we do not wrap them, they stay in place.

### Depth-0 content run detection — precise state machine

```
state: depth = 0, run_start = None, run_end = None

for each token T in stream:
    if T is BDC (opens tag):
        if run_start is not None:
            emit /Artifact BDC before run_start, EMC after run_end
            run_start, run_end = None, None
        depth += 1

    elif T is EMC (closes tag):
        depth -= 1
        # After EMC, we may be back at depth 0 and a new run can start

    elif depth == 0 and T is content-producing:
        if run_start is None:
            run_start = T          # start a new run
        run_end = T                # extend the run

    elif depth == 0 and T is state-setting and run_start is not None:
        run_end = T                # state op joins an open run (but doesn't start one)

    # Otherwise: token at depth > 0 OR state op with no open run → skip

at end of stream:
    if run_start is not None:
        emit /Artifact BDC before run_start, EMC after run_end
```

Key points:
- **State ops only join an open run; they never start one.** A sequence like `q cm Q` at depth 0 with no content between is left completely untouched.
- **Runs extend from the first content-producing op through the last consecutive content-or-state op before a BDC/EMC boundary.** Graphics state between content ops is preserved inside the wrapper.
- **Pure whitespace and comments** between tokens are preserved byte-for-byte (the tokenizer keeps them).
- **BT...ET text objects containing only depth-0 content:** the BT token itself is a state-setting op (text object start), so it joins runs but doesn't start them. In practice, inside a BT we'll have text-showing ops (Tj) which DO start runs, so the run starts there and includes the surrounding BT/ET as state joiners. Result: `BT ... (Tj) ... ET` becomes `/Artifact BDC BT ... (Tj) ... ET EMC` — the desired outcome.
- **BT...ET containing a mix of depth-0 content AND a nested BDC:** the inner BDC closes any open run before incrementing depth. The content inside the BDC is tagged; content after the EMC (still inside BT) starts a new run. Two separate wrappers emitted inside one BT/ET.

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

We cannot precisely predict the residual because the remaining 7.1-3 failures will come from content we choose not to touch in v1: form XObject internals, inline images nested inside form XObjects, and any pathological cases the walker can't handle safely.

Rule | Failed checks before | Failed checks after
---|---:|---:
7.1-3 | 179,291 | **unknown residual — measured empirically in the iteration loop**

The honest framing: this spec is aimed at eliminating the **majority** of 7.1-3 failures. Whether "majority" means 70% or 95% depends on how much of the 179,291 lives inside form XObjects that v1 doesn't recurse into. The iteration loop measures this directly.

## Verification — gated validation

Every call to either function must pass verification before we accept the output. Three gates, graduated by strictness:

### Gate A — minimal (always-on for both tracks)

After saving the modified PDF:
1. **Re-open in PyMuPDF.** If `fitz.open()` fails, revert. The PDF is corrupted.
2. **Extract text via `page.get_text()` on each page.** Compare against the pre-fix text extraction. If any page's text changed, revert. Our modifications should not affect visible text.
3. **Check page count unchanged.**

### Gate B — structural (iteration loop)

Gate A plus:

4. **Run veraPDF on the modified PDF.** Compare the failed-rules set and failed-checks count against the pre-fix baseline. The acceptance criteria differ per track:

**For Track C (XMP metadata):**
- **Expected drop:** rules 5-1, 7.1-8, 7.1-10 should each transition from "fails for this doc" to "passes for this doc" if the doc was previously failing them. Other rules should be unchanged.
- **Accept if:** the targeted rules drop AND no new rule types appear.
- **Revert if:** any expected rule drop is missing, OR any new rule type appears, OR failed-check count rose.

**For Track A (artifact marking):**
- **Expected drop:** rule 7.1-3 failed checks decrease (ideally by a large amount). Other rules should be unchanged, though we accept that the rule *instance count* for 7.1-3 may not change to zero in a single pass.
- **Accept if:** 7.1-3 failed checks decrease AND no new rule types appear AND no OTHER rule's failed checks rose.
- **Revert if:** 7.1-3 failed checks did not decrease, OR any new rule type appears, OR any other rule's failed checks rose (indicating our wrapping broke something).

**Combined (after both tracks on one doc):**
- **Accept if:** all per-track gates accepted AND the set of failing rule IDs post-fix is a subset of the set pre-fix.
- **Revert strategy:** if combined gate fails, revert the most recently applied track and re-verify. If the earlier track alone passes, keep it and leave the other off for that doc. Log the reason.

### Gate C — visual (final commit)

Gate A + Gate B plus:
5. **Render each page to a 150 DPI PNG before and after.** Compute `_pixel_diff()` (already exists in `pdf_writer.py`). If any page has >0.5% pixel diff, revert and log.

**Gating strategy:** use Gate B during iteration (~15-20 min total for 125 docs). Use Gate C for the final full re-run before committing results. Visual diff is slow (~30 min for 125 docs × 2 renders) so we only pay that cost once.

## Orchestration — `scripts/apply_ua_fixes.py`

```
usage: apply_ua_fixes.py --results-dir DIR [--verification {A,B,C}] [--limit N]
```

Reads a `remediation_benchmark_results.json` from DIR, iterates every successful doc, applies Track C then Track A with per-track verification, and writes an enriched results file `ua_fixes_results.json` plus a markdown report `ua_fixes_results.md`.

### Per-doc processing sequence (with blame attribution)

For each doc:

```
1. snapshot_input   = copy of the output_path PDF
2. baseline_text    = [page.get_text() for page in fitz.open(output_path)]
3. baseline_verapdf = check_pdf_ua(output_path) snapshot of failed-rules set and check count

4. apply Track C in place on output_path
5. post_c_text      = [page.get_text() for page in fitz.open(output_path)]
6. if post_c_text != baseline_text:
       revert to snapshot_input, log "Track C broke text extraction", skip to next doc
7. post_c_verapdf   = check_pdf_ua(output_path)
8. if Track C gate B fails (expected rules did not drop, or new rules appeared):
       revert to snapshot_input, log, skip to next doc

9. apply Track A in place on output_path
10. post_a_text     = [page.get_text() for page in fitz.open(output_path)]
11. if post_a_text != baseline_text:
        restore post-C snapshot (i.e. revert ONLY Track A, keep Track C),
        log "Track A broke text extraction", move on
12. post_a_verapdf  = check_pdf_ua(output_path)
13. if Track A gate B fails:
        restore post-C snapshot, log, move on

14. (if Gate C selected) render pages pre/post, pixel diff, revert-if-regression
15. record per-doc result: which tracks applied, per-track expected-vs-actual deltas
```

Text snapshot comparison happens *per track* so blame attribution is unambiguous. The snapshot-before-Track-A is the post-Track-C state, which lets us revert just Track A without losing Track C.

### Failure handling

Every revert path must itself succeed; if a revert fails (disk full, file locked, etc.) we log the doc as "unsafe state" and halt the script rather than continue with a half-modified PDF.

### Resumability

After each successful doc, append its result to `ua_fixes_results.json` via atomic rewrite. If the script is killed and restarted, skip docs that already have a result entry.

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
3. If delta matches expected (136 failed checks eliminated across rules 5-1, 7.1-8, 7.1-10), commit Track C
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

**Total estimated effort: 8–12 hours** of focused work including edge-case debugging on real PDFs, three verification gates, unit tests, and executor integration. The 4-6 hour earlier estimate was optimistic — Track A alone with its content-stream edge cases is likely 4-6 hours on its own.

**Zero API cost for the iteration loop.** Unlike the $16 full-remediation run, Track C and Track A are purely local operations — no API calls, no model costs. We can re-run the 125-doc iteration as many times as we need while debugging edge cases.

## Testing

### Unit tests (added to `tests/test_pdf_writer.py`)

- `test_apply_pdf_ua_metadata_adds_pdfuaid_part` — create a PDF with XMP, apply, verify pdfuaid element present
- `test_apply_pdf_ua_metadata_preserves_existing_xmp` — verify dc:title, dc:creator survive
- `test_apply_pdf_ua_metadata_synthesizes_when_no_xmp` — PDF without /Metadata gets a fresh XMP with pdfuaid
- `test_apply_pdf_ua_metadata_sets_display_doc_title` — ViewerPreferences/DisplayDocTitle=true
- `test_apply_pdf_ua_metadata_preserves_other_viewer_prefs` — existing FitWindow=true survives
- `test_apply_pdf_ua_metadata_idempotent` — running the function twice does not add a second `<pdfuaid:part>` element (the count stays at 1). Byte-exact PDF output is NOT tested because PyMuPDF rewrites xref tables on each save.

- `test_mark_untagged_content_wraps_page_footer` — PDF with a tagged body and an untagged page footer, verify footer gets wrapped in /Artifact
- `test_mark_untagged_content_preserves_tagged_content` — MCID-marked content inside /P BDC stays untouched
- `test_mark_untagged_content_handles_nested_bdc` — /P BDC /Span BDC content EMC EMC stays untouched
- `test_mark_untagged_content_handles_graphics_state` — q/Q wraps preserve text appearance
- `test_mark_untagged_content_empty_stream` — page with no content stream is skipped gracefully
- `test_mark_untagged_content_already_all_tagged` — no-op for fully tagged page
- `test_mark_untagged_content_round_trip_text_extraction` — text extraction matches byte-for-byte before and after
- `test_mark_untagged_content_inline_image` — inline image (BI/ID/EI) wrapped as single unit
- `test_mark_untagged_content_do_operator` — Do operator at page level wrapped
- `test_mark_untagged_content_state_ops_alone_not_wrapped` — a page with only state ops (`q cm Q`) at depth 0 and no content is left completely untouched (zero wrappers inserted)
- `test_mark_untagged_content_idempotent` — running the function twice on the same PDF produces the same `artifact_wrappers_inserted` count on the first call and zero on the second call. After one pass, previously-untagged content is inside `/Artifact BDC` (at depth 1 during the second walk), so no new wrappers are added.

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

**Track C is deterministic: we know the exact target.** 136 failed checks and 136 failed rule-instances eliminated if the function works correctly on all 125 docs. No guessing.

**Track A is empirical: we don't know the exact target.** The 179,291 failed checks for rule 7.1-3 split into two buckets we can't distinguish without implementing and measuring:
- **Bucket 1:** content in the top-level page content stream that we walk and wrap. This bucket goes to ~zero.
- **Bucket 2:** content inside form XObjects that we don't recurse into in v1. This bucket is unchanged.

We don't know the ratio. Plausible ranges based on what academic paper PDFs typically look like:
- Optimistic (80%+ in bucket 1): residual ~35,000 failed checks, final total ~53,000
- Realistic (60-80% in bucket 1): residual ~35,000–70,000, final total ~53,000–88,000
- Pessimistic (<60% in bucket 1): residual >70,000, final total >88,000

| Metric | Source PDFs | After current pipeline | After both tracks (optimistic) | After both tracks (pessimistic) |
|---|---:|---:|---:|---:|
| Failed rules | ~1076+ | 672 | ~410 | ~550 |
| Failed checks | unmeasured | 197,574 | ~53,000 | ~100,000 |
| % check reduction | — | — | ~73% | ~49% |
| Docs fully PDF/UA compliant | 0 | 0 | 1-3 | 0-1 |

**The true number is measured, not predicted.** This spec commits to implementing the fix correctly and reporting the measured result — not to hitting a specific number.

**Why "fully compliant" count stays low either way:** source documents have structural font issues (rules 7.21.x, ~4,290 failed checks across 56+ docs) that this spec does not touch. Reaching zero failed rules on a doc requires either pristine source fonts or deferred Tier D font repair work.

The two progress indicators diverge: Track A may shrink *checks* dramatically while moving *rule-instances* only modestly, because 7.1-3 is one rule class that fires hundreds of check instances per doc. Report both metrics to avoid misleading framings.

## Success criteria

Hard gates (binary pass/fail — all must pass to ship):

1. **Track C exactly eliminates rule 5-1 failures.** 118 failing docs → 0 failing docs. No exceptions; this is a simple metadata fix and there's no reason for any doc to escape it.
2. **Track C exactly eliminates rules 7.1-8 and 7.1-10 failures.** Same reasoning.
3. **No new rule types appear.** For every doc, the set of failing rule IDs after both tracks must be a subset of the set before. If any doc has a new rule ID post-fix that wasn't present pre-fix, that doc reverts.
4. **Text extraction byte-exact** pre/post for every page of every doc (fitz's `page.get_text()` is not PDF/UA-aware so /Artifact marking does not change its output — verified during early smoke test).
5. **Zero docs with >0.5% pixel difference** in the Gate C visual diff.
6. **Executor integration passes a 5-doc end-to-end smoke** without new failures in the orchestrator pipeline (parse → comprehend → strategize → execute → review → our post-process → reparse → report).

Soft targets (report at the end, document residuals):

7. **Track A reduces total 7.1-3 failed checks by the majority**: exact percentage measured and reported. No pre-commitment to a number — we report what we measure.
8. **XObject residual breakdown**: for docs that still fail 7.1-3 after Track A, categorize whether the residual is in form XObject content, inline image content, or elsewhere. This sizes the v2 work.
9. **Aggregate rule count** reported as the new headline: `672 → X`.

If all hard gates pass, we commit. Soft targets are reported in the commit message and NOW.md but don't block the commit.
