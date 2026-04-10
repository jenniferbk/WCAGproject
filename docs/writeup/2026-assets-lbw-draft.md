# Beyond Detection: Measuring PDF Accessibility Remediation on the ASSETS 2025 Benchmark

**Target:** ASSETS 2026 Late-Breaking Work (4+1 pages)
**Deadline:** April 22, 2026
**Status:** Working draft (2026-04-10)

---

## Abstract (~150 words)

The Kumar et al. (ASSETS 2025) PDF Accessibility Benchmark provides 125 expert-labeled documents for evaluating *detection* of accessibility failures. Detection alone, however, does not address the problem faculty face under the U.S. DOJ Title II ADA rule: producing accessible documents by April 2026. We present the first *remediation-outcomes* measurement on this benchmark. Our agentic pipeline reduces independently-verified veraPDF PDF/UA-1 failed checks by 86.7% across all 125 documents at $0.13/document, with zero regressions. We also report a methodological observation: 13 of 125 benchmark items use byte-identical PDFs across label categories, with labels differentiated by test-time evidence provided to the evaluator rather than by document content (confirmed by the benchmark authors). When these pairs are consolidated as single items, our detector scores 87.5% (98/112), compared to GPT-4-Turbo's published 85% on the full benchmark. We discuss the implications for benchmark design and for the gap between detection and remediation.

---

## 1. Introduction (~0.5 page)

### Hook
The U.S. Department of Justice Title II ADA rule (April 2024) requires public universities to meet WCAG 2.1 Level AA for all digital content by April 24, 2026. Manual remediation costs $3-4/page; a single semester of materials at a mid-sized university runs into hundreds of thousands of dollars.

### The detection-remediation gap
Recent work has focused on automated *detection* of accessibility failures (Kumar et al. ASSETS 2025). Detection answers "is this document broken?" but not "how do we fix it?" — and for universities racing the ADA deadline, remediation is the bottleneck.

### Contributions
1. **First remediation-outcomes measurement** on the Kumar et al. benchmark: 86.7% reduction in veraPDF PDF/UA-1 failed checks across 125 documents, $0.13/doc, zero regressions.
2. **Benchmark methodology observation**: 13 of 125 items use byte-identical PDFs across label categories (confirmed by benchmark authors). When these constrained pairs are consolidated as single items, our detector scores 87.5% (98/112) vs GPT-4-Turbo's published 85%.
3. **Two empirical findings** about PDF/UA tooling: bare `/Artifact BDC` is silently ignored without a property dict, and iText's struct-tree rebuild leaves orphan BDC markers accounting for 90%+ of pre-fix rule 7.1-3 failures.

---

## 2. Background (~0.5 page)

### The benchmark
Kumar et al. (2025) released 125 expert-labeled PDFs across seven WCAG/PDF-UA criteria with four labels (passed, failed, not_present, cannot_tell). Published LLM baselines range from GPT-4-Turbo at 85% to Llama-3.2 at 42%.

### LLM evaluation methodology
The benchmark's LLM evaluation uses structured prompts with five components (task definition, WCAG guidelines, sub-criteria checklist, label definitions, output format). Critically, criterion-specific evidence is selectively provided or withheld depending on the target label: for "cannot_tell" items, the evidence needed for confident judgment is omitted from the prompt, simulating scenarios where extraction pipelines fail. The authors confirm this design choice (personal communication, April 2026).

### PDF/UA-1 and veraPDF
PDF/UA-1 (ISO 14289-1) is the formal accessibility standard. veraPDF is the PDF Association's reference validator implementing ~100 rules. We use it for independent remediation verification.

---

## 3. System Architecture (~0.75 page)

### Pipeline overview
```
Document In → Comprehend (Gemini 2.5 Flash) → Strategize (Claude)
→ Execute (deterministic tools) → iText structure tagging
→ Post-processing (Tracks A, C, Phase 2b) → Review → Output
```

### Key insight: agentic vs deterministic
Existing tools apply fixed checklists. Our pipeline lets an LLM *understand* document context before deciding how to remediate. A bold "Example 3.2" in a math textbook is a sub-heading; the same bold in an email is emphasis.

### Post-processing pipeline (this paper's primary technical contribution)
- **Track A**: Walks content streams, wraps untagged content as `/Artifact`, converts orphan BDC markers from stripped struct trees
- **Track C**: Sets PDF/UA version in XMP, `/DisplayDocTitle`, catalog `/Metadata`
- **Phase 2b**: Creates bidirectional `/StructParent` ↔ `/ParentTree` ↔ `/OBJR` links for every annotation, fixing rules 7.18.1-2 and 7.18.5-1
- **Tail polish**: Catalog `/Lang`, page `/Tabs /S`, decorative figure `/Alt`
- **URI repair**: Fixes double-indirect action references in link annotations

### Cost
$0.13/document average. Post-processing (Tracks A, C, Phase 2b) is purely local PDF byte manipulation with zero API cost.

---

## 4. Evaluation (~1 page)

### 4.1 Remediation results

All 125 documents processed end-to-end with independent veraPDF PDF/UA-1 validation.

| Metric | Baseline | After remediation | Delta |
|---|---:|---:|---:|
| **Total failed checks** | **194,394** | **25,882** | **-86.7%** |
| Total failed rules | 731 | 537 | -26.5% |
| Documents improved | — | 125/125 | — |
| Documents regressed | — | 0 | — |
| Documents fully compliant | 0 | 0 | — |

**No document regressed.** Every benchmark PDF has fewer veraPDF failures after remediation.

#### Per-rule contributions
Track A (artifact marking) accounts for the bulk: rule 7.1-3 drops from 179,291 to ~13,000 checks. Track C eliminates rules 5-1 and 7.1-10 entirely. Phase 2b eliminates rules 7.18.1-2 and 7.18.5-1 on all 65 documents with link annotations.

Zero documents reach full PDF/UA-1 compliance because every benchmark PDF has font-embedding issues (rule family 7.21.x, ~5,500 checks) that exist in the source bytes and require font repair tooling beyond our current scope.

### 4.2 Detection results and benchmark methodology

We also ran the Kumar et al. detection task using our PDF analysis tools (deterministic heuristics, no LLM calls for detection).

#### Byte-identical pairs

We discovered that 13 of 125 benchmark items use byte-identical PDFs across label categories (verified via SHA-256 checksums):

| Task | Identical pairs | Pattern |
|---|---|---|
| semantic_tagging | 5 of 5 docs | failed = cannot_tell |
| logical_reading_order | 4 of 5 docs | cannot_tell = passed |
| color_contrast | 2 of 5 docs | cannot_tell = passed |
| table_structure | 1 of 5 docs | cannot_tell = passed |
| alt_text_quality | 1 doc | not_present = cannot_tell |

The benchmark authors confirm (personal communication) that these items use the same PDFs with different test-time evidence provided to the LLM: "we assume that something failed during [criterion] extraction such that the information is not available to the LLM to assess [criterion] quality." This is a reasonable evaluation of whether LLMs correctly abstain under incomplete evidence, but any tool analyzing full PDFs will produce the same prediction for both items in a byte-identical pair.

#### Consolidated scoring

Since a content-based tool must predict identically for byte-identical documents, we consolidate each pair as a single item, crediting it if either label matches. This yields 112 items (99 unpaired + 13 consolidated pairs).

| System | Basis | Items | Score |
|---|---|---:|---:|
| GPT-4-Turbo (Kumar et al.) | LLM + controlled evidence | 125 | 85.0% |
| **Our tool (consolidated)** | **Heuristics, full PDF** | **112** | **87.5%** |
| Our tool (full benchmark) | Heuristics, full PDF | 125 | 78.4% |

On the 99 unpaired items (where no byte-identity constraint exists), our accuracy is 87.9%. The two consolidated-pair errors are documents where neither label in the pair matches our prediction.

We emphasize that GPT-4-Turbo's 85% and our 78.4% measure different things: their score reflects LLM classification with selectively provided evidence; ours reflects deterministic heuristics applied to complete documents. The consolidated 87.5% adjusts for the 13 items where the benchmark's evidence-withholding methodology creates predictions that are correct-by-construction for LLMs but impossible for document-analysis tools.

#### Implication for future benchmarks

The Kumar et al. benchmark effectively evaluates two capabilities: (1) LLM evidence-interpretation, including appropriate abstention, and (2) document-level accessibility assessment. These are both valuable, but tools designed for (2) cannot be fairly scored on items designed for (1). We suggest future benchmarks separate these evaluation goals, or at minimum flag items where labels are differentiated by test-time input rather than document content.

---

## 5. Discussion (~0.5 page)

### The detection-remediation gap
A tool scoring 100% on detection produces zero accessible documents. Our pipeline scoring 78.4% on detection but eliminating 168,512 individual veraPDF failures is the more useful artifact for university accessibility offices. Future benchmarks should pair detection labels with remediation difficulty labels.

### Empirical findings for PDF tooling implementers
1. **Bare `/Artifact BDC` is silently ignored** by veraPDF without a property dict (`<</Type /Pagination>>`). Neither the PDF/UA standard nor iText documentation flags this.
2. **iText's strip-and-retag leaves orphan BDC markers** in content streams. Walking the new struct tree to collect referenced MCIDs and converting orphaned BDCs to artifacts accounts for the bulk of our 86.7% reduction.

### Limitations
- Zero documents reach full compliance (font-embedding issues in source PDFs)
- Form XObject content we don't recurse into accounts for ~13,000 remaining 7.1-3 checks
- Visual fidelity of regenerated PDFs (scanned documents) differs from originals

---

## 6. Future Work

1. Form XObject deeper recursion (estimated push to ~92% reduction)
2. Font repair via fontTools for rule family 7.21.x (gating piece for full compliance)
3. Live faculty pilot at a university accessibility office
4. Collaboration with Kumar et al. on a v2 benchmark separating document-level and evidence-interpretation evaluation

---

## 7. Reproducibility

Code and live deployment at [URLs anonymized for review]. Benchmark at github.com/Anukriti12/PDF-Accessibility-Benchmark. Total cost to reproduce: $16.23 for 125 documents.

---

## References

- Kumar, A. et al. (2025). "Benchmarking PDF Accessibility Evaluation." ASSETS '25.
- ISO 14289-1:2014 (PDF/UA-1)
- veraPDF: github.com/veraPDF
- iText 9.1.0: itextpdf.com
- DOJ Title II ADA Rule, 89 FR 31740 (April 2024)
- WCAG 2.1 (W3C 2018)

---

## Pre-submission checklist

- [ ] Update remediation numbers if they change before submission
- [ ] Verify 87.5% calculation: 98/112 consolidated, 11/13 pairs correct, 87/99 unpaired
- [ ] One hero figure: per-rule before/after bar chart
- [ ] Anonymize for double-blind review (strip GitHub URLs, author info)
- [ ] Cut to 4 pages — Discussion can compress, per-rule table can go to supplementary
- [ ] Confirm Lucy is okay with "personal communication" citation
- [ ] Co-author solicitation: UGA accessibility specialist?
