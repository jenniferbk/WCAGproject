# Complete Struct Tree Tagging — Design Spec

**Date:** 2026-04-13
**Problem:** Benchmark regression from 86.7% to 38.8% failed-check reduction. iText only tags headings, figures, tables, and links. Body text, lists, captions — everything else — gets marked as /Artifact, hiding it from screen readers.

**Approach:** Python-centric (Approach 2). Minimal iText changes. Invert artifact marking logic in `pdf_writer.py` so untagged content becomes /P (or appropriate semantic type) instead of /Artifact. Hybrid path selection: preserve good existing struct trees, rebuild bad ones.

## Architecture Overview

```
assess_struct_tree_quality(pdf)
    │
    ├─ "rebuild": strip tree → iText tag → gap-fill (/P) → post-processing
    │
    └─ "preserve": filter plan → iText tag (into existing tree) → gap-fill (/P) → post-processing
```

Both paths converge at `tag_or_artifact_untagged_content()`, which replaces the current `mark_untagged_content_as_artifact()`. The key behavioral change: untagged content stream runs default to /P struct elements, not /Artifact. Only identified page furniture (page numbers, repeated headers/footers) gets /Artifact.

## Section 1: Tree Quality Assessment

**New function:** `assess_struct_tree_quality(pdf_path) → TreeAssessment` in `pdf_writer.py`.

```python
@dataclass
class TreeAssessment:
    has_tree: bool
    coverage_ratio: float          # 0.0-1.0, MCIDs-with-content / total text objects
    has_paragraph_tags: bool       # tree contains /P elements
    mcid_orphan_rate: float        # 0.0-1.0, orphan MCIDs / total MCIDs
    page_refs_valid: bool          # all struct tree page refs within doc page count
    role_distribution: dict[str, int]  # e.g. {"/P": 45, "/H1": 3, "/Figure": 8}
    tag_content_mismatches: int    # headings with 500 words, /P with 1 word, etc.
    total_text_objects: int
    tagged_text_objects: int
    recommendation: str            # "preserve" | "rebuild"
```

### Four validation checks

1. **MCID orphan rate** — Walk content stream BDCs and struct tree MCIDs. Any MCID in one but not the other is an orphan. Orphan rate > 20% triggers rebuild.

2. **Text-under-tag sanity** — For /H elements, extract text inside their BDC/EMC spans. Heading with >50 words = mismatch. /P with <2 chars = mismatch. More than 30% mismatches triggers rebuild.

3. **Page count match** — Struct tree elements reference /Pg keys. Any reference to a page index beyond `doc.page_count` sets `page_refs_valid = False`, triggers rebuild.

4. **Role distribution sanity** — A tree with 0 /P tags, or 100% one non-standard type (all /Span, all /Figure, all /Slide), or no standard role types at all triggers rebuild.

### Decision logic

```
rebuild if:
  - no tree
  - coverage_ratio < 0.5
  - mcid_orphan_rate > 0.2
  - not page_refs_valid
  - not has_paragraph_tags and no /P in role_distribution
  - tag_content_mismatches > 30% of sampled elements
else: preserve
```

## Section 2: The Two Paths in the Executor

### Rebuild path (enhanced current path)

1. Strip struct tree via `strip_struct_tree()` (as today)
2. iText tags headings, figures, tables, links (as today)
3. **Changed:** `tag_or_artifact_untagged_content()` replaces `mark_untagged_content_as_artifact()` — untagged text runs get /P struct elements + BDC/EMC. Only page furniture gets /Artifact.
4. Rest of pipeline unchanged (link parent tree, tail polish, etc.)

### Preserve path (new)

1. **Skip** `strip_struct_tree()` — pass original PDF directly to iText
2. **Filter tagging plan** — `filter_tagging_plan_for_existing_tree()` removes elements that already exist in the tree (duplicate prevention)
3. iText operates on the existing tree — adds elements as children of the existing /Document root rather than creating a new one
4. **Gap-fill:** same `tag_or_artifact_untagged_content()` tags any remaining depth-0 content as /P
5. **Update existing elements:** update /Alt on existing /Figure tags, update /ActualText on existing /Link tags

**Known limitation — reading order:** Gap-filled /P elements are added as children of the /Document root, regardless of where they appear in the page content. If the existing tree has nested structure (`/Document → /Part → /Sect → /P, /H1, /P`), gap-filled paragraphs will appear after all existing content in the struct tree's reading order. For a doc with 80% coverage, ~20% of paragraphs will read last from a screen reader perspective. This is acceptable for v1 — the content is tagged (not artifacted), which is the primary goal. Reading order optimization on the preserve path is deferred.

### Duplicate prevention (preserve path)

New function `filter_tagging_plan_for_existing_tree()` in `itext_tagger.py`:
- Inspect existing struct tree via PyMuPDF before building tagging plan
- Collect existing /Figure xrefs, /Link annotations, heading MCIDs
- Remove from tagging plan any elements that already exist
- For figures with existing tags but wrong/missing alt text, change action to "update" (handled by `update_existing_figure_alt_texts()` post-iText)

## Section 3: `tag_or_artifact_untagged_content()` Implementation

Replaces `mark_untagged_content_as_artifact()` in `pdf_writer.py`. Same entry point in the pipeline.

### Core logic

`_find_untagged_content_runs()` stays unchanged — already correctly identifies depth-0 untagged content runs. What changes is what we do with each run:

```python
for run in untagged_runs:
    text = _extract_text_from_run(tokens, run)
    if _is_page_furniture(text, page_idx, page_bbox, furniture_set):
        # wrap as /Artifact <</Type /Pagination>> BDC ... EMC
    else:
        mcid = get_next_mcid(page_idx)
        # wrap as /P <</MCID {mcid}>> BDC ... EMC
        # create struct element in StructTreeRoot
```

### `_extract_text_from_run(tokens, run) -> str`

Walk tokens in the run range, collect string operands from Tj, TJ, ', " operators. Text decoding is best-effort: raw bytes from Tj/TJ operands may be font-encoded (not Unicode). For page furniture detection, ASCII-range matching (digits, common short words) is sufficient — we don't need full font-to-Unicode mapping. If a run's text can't be decoded, default to /P (safe fallback).

### `_is_page_furniture(text, page_idx, page_bbox, furniture_set) -> bool`

Returns True if content is page decoration, not real content:

- **Page number:** bare integer or roman numeral, optionally with dashes/dots ("- 3 -", "iii")
- **Repeated header/footer:** text appears in `furniture_set` (pre-computed across all pages)
- **Position-based:** text bbox in top or bottom 5% of page and short (<15 chars)
- **Empty/whitespace-only:** no visible content

### Pre-pass: repeated header/footer detection

Before the per-page tagging loop, scan all pages:
1. Extract short text (<50 chars) at top/bottom margins (top/bottom 10% of page)
2. Group by normalized text content
3. Any text appearing on 3+ pages at similar y-coordinates → add to `furniture_set`
4. This set is passed to `_is_page_furniture()` for each page

### Struct tree integration for /P elements

For each run classified as real content:

1. **Get next MCID** — scan content stream BDCs on this page for the highest existing MCID (catches both iText-assigned and pre-existing MCIDs), take max + 1. Content streams are the authoritative source — don't rely solely on the struct tree, which may not reflect all MCIDs. **Important:** MCIDs are per-page, not global. Track max MCID separately for each page.
2. **Inject BDC/EMC** — `/P <</MCID N>> BDC ... EMC` wrapping the run tokens. This requires a new `_apply_content_tag_wrappers()` function (see below) — NOT a reuse of `_apply_artifact_wrappers()`, because each run needs a different BDC (unique MCID for /P runs, no MCID for /Artifact runs).
3. **Add struct element** — create new xref object for struct element via PyMuPDF, add as kid of /Document element in StructTreeRoot, with /Pg pointing to page and /K containing MCID.
4. **Update ParentTree** — add MCID→struct element mapping to the ParentTree (see Section 3a below). Without this, veraPDF will flag every new /P element as a ParentTree violation.

### `_apply_content_tag_wrappers()` — new function

Unlike `_apply_artifact_wrappers()` which applies the same `/Artifact` wrapper to all runs, this function handles mixed tagging where each run gets a different wrapper:

```python
@dataclass
class TaggedRun:
    start: int          # token index
    end: int            # token index
    tag_type: str       # "/P", "/L", "/Artifact"
    mcid: int | None    # MCID for struct-tagged runs, None for /Artifact

def _apply_content_tag_wrappers(
    tokens: list[Token],
    tagged_runs: list[TaggedRun],
) -> bytes:
    """Reassemble token list with per-run BDC/EMC wrappers.

    /P runs get: /P <</MCID N>> BDC ... EMC
    /Artifact runs get: /Artifact <</Type /Pagination>> BDC ... EMC
    """
```

Returns the rewritten content stream bytes. The caller uses the `TaggedRun.mcid` values to create corresponding struct elements and ParentTree entries.

### Semantic type selection

Default to /P, upgrade when the DocumentModel provides more info:

- Paragraphs identified as list items (from `convert_list` actions in strategy) → /L wrapper with /LI + /LBody children
- v1 stretch: /Caption, /BlockQuote, /Note where model identifies them
- Fallback: everything unclassified → /P

## Section 3a: ParentTree Management for MCID-Based Elements

**This is critical for PDF/UA compliance.** Every struct element linked via MCID must have a corresponding ParentTree entry, or veraPDF will fail.

### How ParentTree works for MCIDs (vs annotations)

The ParentTree serves two purposes in PDF:

1. **Annotations** (already handled by `populate_link_parent_tree()`): each annotation gets `/StructParent N`, and ParentTree entry `N` points to a single struct element xref.
2. **Page content MCIDs** (NOT yet handled — this is new): each page gets `/StructParents N`, and ParentTree entry `N` points to an **array** of struct element xrefs, where `Array[MCID]` = the struct element for that MCID on that page.

The existing codebase handles case 1. This section describes case 2.

### Per-page MCID→struct element mapping

`tag_or_artifact_untagged_content()` must build a per-page mapping as it processes each page:

```python
# Built during the per-page tagging loop
page_mcid_map: dict[int, list[tuple[int, int]]]  
# page_idx → [(mcid, struct_elem_xref), ...]
```

For each /P run tagged on a page, record `(mcid, struct_elem_xref)` in the map.

### Reading iText's existing ParentTree entries

After iText runs, pages it tagged will already have `/StructParents` values and corresponding ParentTree array entries. Before adding our entries, we must:

1. Read each page's existing `/StructParents` value (if any)
2. Read the corresponding ParentTree array entry
3. Extend that array with our new MCID→struct element mappings
4. For pages iText didn't touch (no `/StructParents`), assign a new `/StructParents` number and create a fresh ParentTree array entry

### Implementation: `_update_parent_tree_for_mcids()`

New function in `pdf_writer.py`:

```python
def _update_parent_tree_for_mcids(
    doc: fitz.Document,
    page_mcid_map: dict[int, list[tuple[int, int]]],
) -> int:
    """Update ParentTree with MCID→struct element mappings for gap-filled content.

    For each page with new MCIDs:
    1. Check if page already has /StructParents → read existing array
    2. Extend array so that array[mcid] = struct_elem_xref for each new MCID
    3. If page has no /StructParents, assign next available number
    4. Write updated ParentTree entries and ParentTreeNextKey

    Returns count of entries added.
    """
```

The ParentTree array for a page must be large enough that `array[max_mcid]` is valid. If iText assigned MCIDs 0-5 and we add MCID 6, the array needs 7 entries. Gaps in the array (if any MCIDs are skipped) should be filled with `null`.

### Ordering constraint

`_update_parent_tree_for_mcids()` must run AFTER `tag_or_artifact_untagged_content()` (which creates the struct elements and assigns MCIDs) but BEFORE `populate_link_parent_tree()` (which also writes to the ParentTree). Both functions must read/merge existing ParentTree state, not overwrite it.

Updated pipeline ordering:
```
... → tag_or_artifact_untagged_content → update_parent_tree_for_mcids →
link annotations → link parent tree → tail polish
```

## Section 4: iText Changes for the Preserve Path

### Required Java change in `PdfTagger.java`

At initialization, detect whether the PDF already has a struct tree:

```java
// Instead of always creating new /Document:
PdfStructTreeRoot structRoot = pdfDoc.getStructTreeRoot();
if (structRoot != null && structRoot.getKids() != null && !structRoot.getKids().isEmpty()) {
    // Find existing /Document element, use as parent
    documentElement = findExistingDocumentElement(structRoot);
} else {
    // Create new /Document as today
    documentElement = new PdfStructElem(pdfDoc, PdfName.Document);
    structRoot.addKid(documentElement);
}
```

Additionally, `pdfDoc.setTagged()` (line 78) is called before tree operations. On an already-tagged PDF (preserve path), this should only set `/Marked true` in MarkInfo without clearing the existing tree — but this assumption must be verified with a test. If `setTagged()` clears or replaces the existing StructTreeRoot, we'll need to conditionally skip it on the preserve path.

**Test case:** Open a PDF with an existing struct tree via iText, call `setTagged()`, verify StructTreeRoot and its children are preserved.

This is the only other Java consideration. iText still handles the same 4 element types (headings, figures, tables, links), just parented under the existing tree when one exists.

## Section 5: Pipeline Orchestration

### Current flow in `executor.py`
```
strip_struct_tree → iText tag → contrast → URI repair → metadata →
mark_untagged_as_artifact → link annotations → link parent tree → tail polish
```

### New flow
```
assess_struct_tree_quality
    │
    ├─ "rebuild":
    │   strip_struct_tree → iText tag → contrast → URI repair → metadata →
    │   tag_or_artifact_untagged_content → update_parent_tree_for_mcids →
    │   link annotations → link parent tree → tail polish
    │
    └─ "preserve":
        filter_tagging_plan → iText tag (into existing tree) → contrast → URI repair →
        metadata → tag_or_artifact_untagged_content → update_parent_tree_for_mcids →
        link annotations → link parent tree → tail polish
```

Only differences between paths:
1. Rebuild strips the tree; preserve doesn't
2. Preserve filters the tagging plan for duplicates
3. Both converge at `tag_or_artifact_untagged_content()`
4. Both run `update_parent_tree_for_mcids()` after gap-fill to register new MCIDs in the ParentTree

### Return data

```python
@dataclass
class ContentTaggingResult:
    success: bool
    pages_modified: int = 0
    paragraphs_tagged: int = 0        # runs tagged as /P
    lists_tagged: int = 0             # runs tagged as /L
    artifacts_tagged: int = 0         # runs tagged as /Artifact (page furniture)
    pages_skipped: int = 0
    form_xobjects_modified: int = 0
    tree_assessment: TreeAssessment | None = None
    errors: list[str] = field(default_factory=list)
```

### Logging

- "Struct tree assessment: coverage=0.82, orphan_rate=0.03, recommendation=preserve"
- "Preserve path: filtered 12 existing figures, 3 existing links from tagging plan"
- "Gap-fill: tagged 47 runs as /P, 3 as /Artifact (page furniture)"

## Section 6: Testing Strategy

### Unit tests

**Tree assessment:**
- PDF with good struct tree (high coverage, /P tags, valid page refs) → "preserve"
- PDF with no struct tree → "rebuild"
- PDF with bad tree (orphaned MCIDs, wrong page refs, all /Span) → "rebuild"
- PDF with tree from wrong doc (page refs beyond page count) → "rebuild"
- PDF with high coverage but no /P tags (PowerPoint /Slide tree) → "rebuild"

**Content classification:**
- Bare page number "3" at bottom of page → artifact
- Body paragraph text → /P
- Text appearing on 5+ pages at same y-position → artifact (header/footer)
- Empty/whitespace run → artifact

**Gap-fill tagging:**
- After iText tags headings + figures, remaining body text gets /P BDC/EMC + struct elements
- MCIDs don't collide with iText-assigned MCIDs (per-page tracking, not global)
- Struct elements properly parented under /Document
- ParentTree array entries created for each page with new MCIDs
- ParentTree array correctly extends iText's existing entries (no overwrites)
- Pages iText didn't touch get new /StructParents + ParentTree entries
- ParentTree array gaps (skipped MCIDs) filled with null

**Mixed BDC wrapping:**
- `_apply_content_tag_wrappers()` correctly assigns unique MCIDs to /P runs
- /Artifact runs get no MCID
- Mixed pages (some /P, some /Artifact) produce valid content streams

**Preserve path:**
- Existing /Figure with alt text not duplicated
- Existing /Figure with missing alt text gets updated
- New headings added alongside existing tree elements
- Existing /P tags preserved, gaps filled
- `pdfDoc.setTagged()` does not clear existing struct tree (Java test)

### Integration tests

- Run 5-10 benchmark docs through both paths, verify zero body text wrapped in /Artifact
- Round-trip test: PDF with good existing tree through preserve path, verify original /P tags intact
- veraPDF rule 7.1-3 violations decrease on both paths

### Benchmark validation

Re-run full 125-doc benchmark with `verapdf_postprocess.py`, compare against v3 baseline:
- Failed check reduction well above 38.8%
- Regressed docs (72 in v3) drop significantly
- No doc has body text artifacted

## Section 7: Scope & Risks

### In scope
- Tree quality assessment with 4 validation checks
- Two-path executor (preserve vs rebuild)
- `tag_or_artifact_untagged_content()` replacing `mark_untagged_content_as_artifact()`
- Page furniture detection (page numbers, repeated headers/footers)
- /P tagging for all body text, /L for identified lists
- Duplicate prevention on preserve path
- Minimal iText change (use existing /Document root)
- Tests for both paths

### Out of scope (v1)
- /Caption, /BlockQuote, /Note semantic types (default to /P)
- /Section or /Div grouping elements (tree stays flat under /Document)
- Form XObject content tagging: the existing pass 2 form XObject walker will be updated to use /P instead of /Artifact for untagged content (same logic as page content streams), but no new struct tree integration for form XObject content in v1
- Font-aware text extraction for `_extract_text_from_run()` — best-effort decoding
- Reading order optimization (struct tree kid order = insertion order)

### Known limitations (v1)

- **Preserve path reading order** — gap-filled /P elements are appended to /Document root, not inserted at their logical position in a nested tree. Screen readers will encounter gap-filled content after all existing tree content.

### Risks

1. **MCID collisions** — Python gap-fill runs after iText. Mitigated by scanning content streams per-page for max MCID (MCIDs are per-page, not global). Do NOT reuse the existing `_collect_struct_tree_mcids()` which returns a global set.

2. **ParentTree corruption** — Multiple passes write to the ParentTree (gap-fill MCIDs, then link annotations). Each pass must read-then-merge, not overwrite. Ordering constraint: `update_parent_tree_for_mcids` → `populate_link_parent_tree`. Mitigated by integration tests that run veraPDF and check for ParentTree violations.

3. **Preserve path + iText interaction** — iText operating on a tree it didn't create could produce unexpected results. Specific concern: `pdfDoc.setTagged()` may behave unexpectedly on already-tagged PDFs. Mitigated by explicit Java test case and integration tests on real docs.

4. **Page furniture false positives** — Conservative heuristic might tag some page numbers as /P. Low risk: screen reader announcing "3" between paragraphs is mildly annoying, not a compliance failure. Far better than hiding body text.

5. **Performance** — Header/footer pre-pass adds a scan of all pages. Should be fast (text extraction only, no rendering), worth monitoring on large docs.

### Files changed

| File | Change |
|------|--------|
| `src/tools/pdf_writer.py` | Add `assess_struct_tree_quality()`, `TreeAssessment`, `tag_or_artifact_untagged_content()`, `ContentTaggingResult`, `_apply_content_tag_wrappers()`, `TaggedRun`, `_extract_text_from_run()`, `_is_page_furniture()`, `_update_parent_tree_for_mcids()`, header/footer pre-pass |
| `src/tools/itext_tagger.py` | Add `filter_tagging_plan_for_existing_tree()` |
| `src/agent/executor.py` | Branch on tree assessment, call appropriate path |
| `java/.../PdfTagger.java` | Detect existing /Document root, use it instead of creating new one |
| `tests/` | New test file or additions to existing test files |
