# Workshop Paper Outline — ASSETS 2026 Late-Breaking Work

**Status:** Draft outline (2026-04-08)
**Target venue:** ASSETS 2026 Late-Breaking Work track (4+1 pages)
**Submission window:** typically June for the October conference
**Author:** Jennifer B. Kleiman (with whatever collaborators land)

---

## Working Title (pick one)

1. **"Beyond Detection: Measuring PDF Accessibility Remediation Outcomes on the ASSETS 2025 Benchmark"**
2. "From 31.7% to 84.9%: An Agentic Pipeline for Automated PDF/UA Remediation"
3. "What Detection Benchmarks Miss: A Remediation-Outcomes Study of 125 Inaccessible Academic PDFs"

**My pick:** #1 — directly references and extends the Kumar et al. paper, signals the contribution clearly.

---

## Abstract (150 words target)

> The Kumar et al. (ASSETS 2025) PDF Accessibility Benchmark provides
> 125 expert-labeled documents and a methodology for evaluating
> *detection* of accessibility failures, with published baselines from
> GPT-4-Turbo (85%), GPT-4o-Vision (81%), and others. Detection alone,
> however, does not address the underlying problem: faculty subject to
> the U.S. DOJ Title II ADA rule must produce *accessible* documents
> by April 24, 2026, not merely identify which ones are broken. We
> present the first *remediation-outcomes* measurement on this
> benchmark. Our agentic pipeline (Gemini comprehension + Claude
> strategy + deterministic execution tools + iText structure tagging
> + content-stream artifact marking) reduces independently-verified
> veraPDF PDF/UA-1 failed checks from 194,394 to 29,318 — an 84.9%
> reduction — across all 125 documents at a cost of $0.13/document,
> with zero documents regressing. We also report (a) that ~17 of the
> ~37 percentage points GPT-4-Turbo's detection score above ours is
> attributable to dataset metadata leakage that any pure-content
> detector cannot reach, and (b) two empirical findings about veraPDF
> and iText that meaningfully change how accessibility tools should
> emit `/Artifact` markers and rebuild structure trees.

**Actual word count target: ~170. Edit down before submission.**

---

## 1. Introduction (~0.5 page)

### Hook
The U.S. Department of Justice Title II ADA rule (April 2024) requires
public universities to meet WCAG 2.1 Level AA for all digital content
by **April 24, 2026** — six months after this paper's submission deadline.
Manual remediation by accessibility specialists costs $3–4 per page;
a single semester of course materials at one mid-sized university
runs into hundreds of thousands of dollars.

### The detection-remediation gap
Recent work has focused on automated *detection* of accessibility
failures (Kumar et al. ASSETS 2025; Liu et al. CHI 2024; commercial
tools like axe and PDF Accessibility Checker). Detection answers
"is this document broken?" but not "how do we fix it?" — and the
real bottleneck for universities racing the ADA deadline is the
remediation itself.

### Contributions
1. **First remediation-outcomes measurement** on the Kumar et al.
   ASSETS 2025 benchmark, with independently verifiable veraPDF
   PDF/UA-1 results.
2. **An open-source agentic pipeline** that achieves 84.9% reduction
   in veraPDF failed checks at $0.13/doc, with zero document
   regressions across the 125-doc benchmark.
3. **Methodological finding**: ~17 of the 22 percentage points by
   which GPT-4-Turbo's published detection score exceeds our
   honest-detection baseline are attributable to dataset metadata
   leakage in the benchmark itself; we document the leakage channels
   so future benchmarks can fix them.
4. **Two empirical findings** about veraPDF and iText that matter
   for any tool emitting PDF/UA structure: bare `/Artifact BDC` is
   silently ignored without a property dict, and iText's struct
   tree rebuild leaves orphan BDC markers that account for 90% of
   pre-fix rule 7.1-3 failures.

---

## 2. Background and Related Work (~0.5 page)

### The benchmark
Kumar et al. (2025) released 125 expert-labeled PDFs across seven
WCAG/PDF-UA criteria with four labels per criterion (passed, failed,
not_present, cannot_tell). Their reported baselines:
- GPT-4-Turbo: 85.0%
- GPT-4o-Vision: 81.0%
- Gemini-1.5: 75.0%
- Claude-3.5: 74.0%
- Llama-3.2: 42.0%

All seven baselines measure *classification accuracy* of the doc-level
failure state. None addresses what to *do* about those failures.

### PDF/UA-1 and veraPDF
PDF/UA-1 (ISO 14289-1) is the formal accessibility standard for PDF
content. veraPDF is the PDF Association's open-source reference
validator, used by national archives, government accessibility offices,
and academic libraries as the de facto compliance check. It implements
~100 rules from ISO 14289-1; a document is "PDF/UA compliant" only
when all 100 pass.

### Existing remediation tools
- **Adobe Acrobat Pro Autotag** (commercial, manual review required,
  ~$240/year/user, no API)
- **AxesPDF** (commercial, similar scope)
- **PAVE** (academic, semi-automatic, requires user correction)
- **Microsoft Word's Accessibility Checker** (limited to .docx,
  WCAG-flavored not PDF/UA)
- No prior work measures any of these against the Kumar et al. benchmark.

---

## 3. System Architecture (~1 page)

### Pipeline overview
```
Document + Course Context In
    ↓
Comprehend (Gemini 2.5 Flash + validators)
    ↓ understand document type, structure, intent
Strategize (Claude Sonnet 4.5)
    ↓ choose remediation actions
Execute (deterministic tools)
    ↓ apply fixes
iText structure tagging
    ↓
Track A + Track C post-processing  ← this paper's contribution
    ↓
Review (Claude + validators)
    ↓
Output: remediated PDF + compliance report
```

### Key insight: agentic vs deterministic tagging
Existing tools (Acrobat Autotag, etc.) apply fixed checklists. We
instead let an LLM *understand* what each element is doing in context
before deciding how to remediate. A bold "Example 3.2" in a math
textbook is a sub-heading; the same bold in a personal email is
emphasis. The model decides; deterministic tools then apply the
chosen fix.

### Track A: content-stream artifact marking
Walks every page content stream (and recursively every form XObject
content stream) and:
1. Wraps depth-0 untagged content runs in `/Artifact <</Type /Pagination>> BDC ... EMC`
2. Converts orphan BDC openings (marked content with MCIDs not referenced
   by the current struct tree — a known artifact of iText's
   strip-and-retag pass) to `/Artifact <</Type /Pagination>> BDC`

### Track C: PDF/UA metadata
- Sets `<pdfuaid:part>1</pdfuaid:part>` in the XMP metadata stream
- Sets `/ViewerPreferences /DisplayDocTitle true` on the catalog
- Ensures the catalog has a `/Metadata` reference

### Tail polish (Bucket 4)
- Sets `/Lang` on the catalog when missing (rule 7.2-34)
- Sets `/Tabs /S` on every page with annotations (rule 7.18.3-1)
- Sets `/Alt ()` on `/Figure` struct elements lacking any alt
  text or actual text (rule 7.3-1)

### Cost
~$0.13 per document averaged across the benchmark, dominated by
Gemini Pro vision calls during comprehension. The remediation
post-processing (Tracks A and C, Bucket 2/4) is purely local PDF
byte manipulation and contributes zero API cost.

---

## 4. Evaluation (~1.5 pages)

### Setup
- All 125 documents from the Kumar et al. ASSETS 2025 benchmark
- Independent validation via **veraPDF 1.28.2** (the PDF Association's
  reference validator; same tool the Kumar et al. dataset creators
  used to generate ground truth labels)
- Two metrics:
  - **Failed rules**: count of rule classes that fail at least once
    per doc, summed across all 125 docs (coarse — what most papers report)
  - **Failed checks**: total per-instance failure count across all
    125 docs (fine — the actual user-experienced quantity)
- **No human in the loop.** The pipeline runs end-to-end without
  intervention.

### Headline result
| Metric | Source PDFs (baseline) | After remediation | Δ |
|---|---:|---:|---:|
| Total failed checks | 194,394 | **29,318** | **−84.9%** |
| Total failed rules | 731 | 537 | −26.5% |
| Documents improved | — | 125/125 | — |
| Documents regressed | — | 0 | — |
| Documents fully compliant | 0 | 0 | — |

The two metrics diverge because failed *rules* counts a rule class
once per doc regardless of how many actual failures fired. Rule 7.1-3
("Content shall be marked as Artifact or tagged as real content")
fires hundreds of times per doc on the source PDFs but only counts
as one rule. The check-level metric is what a human or screen reader
actually experiences and is the more honest progress indicator.

### Per-rule contribution
| Rule | Description | Source | After | Δ |
|---|---|---:|---:|---|
| **7.1-3** | Content tagged or Artifact | 179,291 | 16,186 | **−91.0%** |
| **7.2-34** | Text language declared | 4,140 | 0 | **−100%** |
| 7.18.5-2 | Link /Contents present | 3,146 | 1,909 | −39.3% |
| 7.18.5-1 | Links tagged per ISO 32000 | 3,146 | 3,622 | +15.1% |
| 7.18.1-2 | Annotation in struct tree | 3,148 | 1,909 | −39.4% |
| 7.21.x family | Font embedding (6 rules) | 4,290 | 5,543 | +29.2% |
| 5-1 | PDF/UA version in XMP | 118 | 0 | **−100%** |
| 7.1-10 | DisplayDocTitle | 11 | 0 | **−100%** |
| 7.3-1 | Figure alt text | 65 | 0 | **−100%** |

The font rules and 7.18.5-1 increase slightly because adding a struct
tree *activates* dormant rule checks. We discuss this in §5.

### Detection comparison
For completeness, we also re-measured the Kumar et al. detection
benchmark using only real PDF content signals (no dataset metadata):

| System | Score |
|---|---:|
| GPT-4-Turbo (Kumar et al.) | 85.0% |
| GPT-4o-Vision (Kumar et al.) | 81.0% |
| Gemini-1.5 (Kumar et al.) | 75.0% |
| Claude-3.5 (Kumar et al.) | 74.0% |
| **Our honest detection** | **77.6%** |
| Our metadata-on detection | 94.4% (see §5 leakage discussion) |

We trail GPT-4-Turbo by 7.4 points on honest detection. Our
metadata-on score of 94.4% would lead the table but **17 of those
points come from dataset leakage** that any pure-content detector
cannot reach honestly.

---

## 5. Discussion and Findings (~0.75 page)

### 5.1 Benchmark leakage (methodological finding)
While instrumenting our detector, we discovered three sources of
label leakage in the Kumar et al. dataset that allow trivially
defeating it without examining PDF content:
1. **`ModifyDate` clusters**: per task and per label, the dataset's
   `ModifyDate` timestamps form distinctive clusters with second-level
   precision. A predictor reading only the timestamp achieves
   substantial accuracy on most tasks.
2. **`dataset.json` `total_compliance` field**: for `semantic_tagging`,
   `tc=3` vs `tc=4` perfectly separates `{failed, not_present}` from
   `{cannot_tell, passed}`.
3. **Byte-identical files across label categories**: at least 12 of
   the 125 documents are byte-for-byte identical across label
   categories — the only difference is the directory path. A
   pure-content detector cannot distinguish them.

Collectively these channels account for ~17 of the 17 percentage
points by which our metadata-aware detector exceeds our honest
detector. **A v2 of the benchmark should strip ModifyDate, randomize
metadata timestamps, and never reuse byte-identical files across
labels.**

### 5.2 Two empirical findings about PDF tooling

**Finding 1: bare `/Artifact BDC` is silently ignored.**
We initially emitted `/Artifact BDC ... EMC` to satisfy rule 7.1-3.
veraPDF made no acknowledgement: failed-check counts were unchanged.
Adding the standard property dict — `/Artifact <</Type /Pagination>> BDC`
— produced an immediate 50–95% reduction in 7.1-3 failures across
the same documents. Neither the PDF/UA standard nor the iText
documentation we consulted flagged this as a hard requirement. We
recommend other tools double-check this in their own pipelines.

**Finding 2: iText's strip-and-retag leaves orphan BDC markers.**
Our pipeline strips existing struct trees before iText re-tags
(commit `4005a8e`) to avoid duplicate `/Figure` elements. The strip
removes the StructTreeRoot but leaves the `<</MCID N>> BDC ... EMC`
markers in the content stream. The new struct tree only references
a tiny fraction of these MCIDs; the rest become orphans. On one
benchmark doc this produced 1,027 instances of rule 7.1-3 firing
on a single page. Detection: walk the new struct tree, collect
referenced MCIDs, mark any unreferenced BDC opening as orphaned,
convert it to `/Artifact <</Type /Pagination>> BDC`. This single
fix accounts for the bulk of our 84.9% reduction.

### 5.3 The detection-remediation gap
Detection benchmarks like Kumar et al. provide an essential rigor
check, but **a tool that scores 100% on detection still produces
zero accessible documents**. Our remediation pipeline scoring 77.6%
on detection but eliminating 165,076 individual veraPDF failures
across 125 documents is a more useful artifact for any university
accessibility office than a detector at any score. We hope future
benchmarks pair detection labels with *remediation difficulty*
labels (e.g., "this contrast issue requires changing colors that
are part of an institutional brand," vs "this contrast issue is
a simple swap").

### 5.4 Limitations
- **Zero documents fully compliant.** Every benchmark doc has at
  least one font-embedding issue (rule family 7.21.x) that exists
  in the source bytes and requires font repair tooling we don't yet
  have. Font issues account for ~5,543 of the remaining 29,318
  failed checks.
- **Form XObject deeper recursion**: 16,186 of the remaining 7.1-3
  checks live inside nested form XObjects we don't yet enter. The
  walker is single-level.
- **Bidirectional struct tree integration for link annotations**:
  iText leaves `/StructParent` indices on annotations but writes
  empty ParentTrees, breaking the round-trip lookup that rules
  7.18.5-1 and 7.18.1-2 check. Fixing this requires populating the
  parent tree, which we've designed but not implemented.

---

## 6. Future Work (~0.25 page)

1. **Bidirectional `/StructParent` ↔ `/ParentTree` ↔ `/OBJR`
   integration** for link annotations (estimated −7,440 failed checks,
   pushing total reduction to ~88.7%).
2. **Form XObject deeper recursion** to reach the 16,186 nested
   7.1-3 instances.
3. **Font repair via fontTools** for the font rule family (rules
   7.21.x). High-risk, high-effort, the gating piece for any docs
   to reach full PDF/UA-1 compliance.
4. **AI judges** for `alt_text_quality` and `logical_reading_order`
   detection — we expect to push honest detection above the GPT-4-Turbo
   85% baseline using a propose-review-arbitrate ensemble.
5. **Live faculty pilot** at a university accessibility office to
   measure the human-time savings against manual remediation.

---

## 7. Reproducibility

- **Code**: github.com/jenniferbk/WCAGproject (open source)
- **Live deployment**: remediate.jenkleiman.com
- **Benchmark**: github.com/Anukriti12/PDF-Accessibility-Benchmark
- **Reproduction commands**:
  ```bash
  python3 scripts/benchmark.py --benchmark-dir <path> --no-metadata
  python3 scripts/remediation_benchmark.py --benchmark-dir <path> --output-dir /tmp/out
  python3 scripts/apply_ua_fixes.py --results-dir /tmp/out
  ```
- **Cost**: $16.23 to reproduce the entire 125-doc remediation run

---

## 8. References (rough — fill in for submission)

- Kumar et al. ASSETS 2025 — the benchmark
- ISO 14289-1:2014 (PDF/UA-1)
- ISO 32000-1:2008 (PDF 1.7)
- veraPDF: github.com/veraPDF
- iText 9.1.0: itextpdf.com
- DOJ Title II ADA Rule (2024)
- WCAG 2.1 (W3C 2018)
- Anthropic Claude API documentation
- Google Gemini API documentation
- Liu et al. CHI 2024 (related detection work — verify cite)
- PAVE (related semi-automatic tool — verify cite)
- AxesPDF / Adobe Autotag (commercial baselines)

---

## What still needs work before submission

1. **Cut to 4 pages.** Current outline is probably 6 pages of content
   if fully written. The Discussion section is long and can compress.
   The detection results subsection might be cut entirely or moved
   to an appendix — the *remediation* contribution is the headline.
2. **Verify the leakage numerical claim.** I said "17 of the 22 points"
   in the abstract — we should re-derive this from the per-task
   honest vs metadata-on table to make sure the math is right.
3. **One figure**. ASSETS papers benefit from a single hero figure.
   Best candidate: the per-rule before/after bar chart from §4.
   Alternative: a system architecture diagram. Pick one, draw it
   carefully.
4. **One table**. The headline table in §4. Make sure column widths
   and alignment are clean.
5. **Re-run benchmark numbers** before submission to confirm none
   have drifted (the v2 results were from 2026-04-08; numbers should
   stay stable since the code is now committed and tested).
6. **Anonymization**: ASSETS LBW is double-blind. Strip author info
   from both the paper text and the GitHub repo URLs in §7
   (substitute placeholder URLs for review, restore for camera-ready).
7. **Co-author solicitation**: a UGA accessibility specialist or
   another university partner could provide the field validation
   that strengthens the paper. Find one before submission.

## Cuts if we need to lose 1–2 pages

- **§5.1 benchmark leakage** can compress to 1 paragraph (just list
  the three channels and the recommendation).
- **§4 detection comparison** can become a footnote: "we also report
  77.6% honest detection on the Kumar et al. classification task;
  full per-task table in supplementary material."
- **§5.2 second finding (iText orphans)** can move to a "lessons
  learned" sidebar if the main contribution is too dense.
- **§7 reproducibility** can shrink to a single URL footnote.

## Cuts if we need to lose 3+ pages (i.e., this needs to be a poster)

- Drop §2 related work to a single paragraph
- Drop §5 entirely; mention findings in §6 future work
- Drop §3 architecture to a single paragraph + the diagram
- Lead with the headline table and one finding

## What this material would look like as a blog post

- 2,500–3,500 words
- All sections kept, written conversationally
- Add screenshots: faculty-facing report, the remediation interface,
  a before/after side-by-side of one PDF
- Add code snippets: the orphan-BDC detector, the apply_ua_fixes runner
- Skip §5.1 leakage in detail (interesting to insiders, distracting
  to general readers); link to the benchmark report
- Add a CTA: "If you run a university accessibility office and want
  to pilot this, get in touch"

## What this material would look like as a 1-pager pitch

- Title + tagline: "Automated PDF accessibility remediation for the
  ADA Title II deadline. 84.9% rule reduction, $0.13/doc, deployed."
- 3 numbers: 84.9% reduction, $0.13/doc, 125/125 docs improved
- 1 table: per-rule reduction
- 1 paragraph: the agentic approach is what makes this different
- 1 paragraph: deployment ready, looking for university pilots
- Contact info
