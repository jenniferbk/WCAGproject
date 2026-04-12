# Link Text Harvest: Pass Agent-Improved Link Text into PDF Struct Tree

**Date:** 2026-04-12
**Status:** Design approved

## Problem

After remediation, iText creates orphaned `/Link` struct elements with descriptive `/ActualText` (e.g., "UGA Writing Center") but no connection to annotations or content stream. `populate_link_parent_tree()` then creates properly-connected `/Link` elements (OBJR → annotation) but only has raw URLs for text. The parser reads content-stream text (also raw URLs). Result: post-remediation reports show false WCAG 2.4.4 "bare URL" failures for links the agent improved.

## Root Cause

The agent's improved link text flows through iText into orphaned struct elements, while the annotation-connected struct elements are created later by `populate_link_parent_tree()` which doesn't know about the agent's text.

## Fix

Pass the agent's URL → improved text mapping from the executor into `populate_link_parent_tree()`, so the connected `/Link` elements carry the good text.

### Changes

**1. `src/agent/executor.py` (~10 lines)**

After all actions are processed but before calling `populate_link_parent_tree`, build a `dict[str, str]` mapping URL → new_text from executed `set_link_text` actions:

```python
link_text_overrides = {}
for action in updated_actions:
    if (action.get("action_type") == "set_link_text"
            and action.get("status") == "executed"):
        # Get the URL from the original link
        eid = action["element_id"]
        link = link_by_id.get(eid)
        if link:
            link_text_overrides[link.url] = action["parameters"]["new_text"]

populate_link_parent_tree(tagged_pdf_path, link_text_overrides=link_text_overrides)
```

**2. `src/tools/pdf_writer.py` (~15 lines)**

Add `link_text_overrides: dict[str, str] | None = None` parameter to `populate_link_parent_tree()` and `_populate_link_parent_tree_inner()`.

In `_populate_link_parent_tree_inner`, when calling `_get_annot_alt_text()`, check overrides first:

```python
# Get alt text — prefer agent-improved text over raw URL
raw_alt = _get_annot_alt_text(doc, annot_xref)
if link_text_overrides:
    uri = _extract_uri_from_annotation(doc, annot_xref)
    alt_text = link_text_overrides.get(uri, raw_alt)
else:
    alt_text = raw_alt
```

**3. `src/tools/pdf_parser.py` (~20 lines)**

Add `_get_link_accessible_name(doc, annot_xref) -> str | None` that resolves `/StructParent` → `/ParentTree` → `/Link` → `/ActualText`. Call it during link extraction; if it returns non-URL text, use it as `LinkInfo.text`.

This handles re-parsing: when anyone parses the remediated PDF (including the pipeline's review phase, external validators, or users downloading and checking), they'll see the improved text because it's now in the connected `/Link` struct element AND readable via `/StructParent` resolution.

### What does NOT change

- `_apply_struct_tag_fixes()` in the orchestrator — still works as before for the in-pipeline review
- Validator logic (`links.py`, `validator.py`) — reads `LinkInfo.text` which now has the right value
- iText Java code — no changes needed
- Links the agent didn't improve — correctly reported as raw URLs

### Edge Cases

- **URL normalization:** Annotation URI might have trailing slashes, different casing, etc. compared to the executor's URL. Use exact match first; if no hit, try stripping trailing `/` and comparing.
- **Multiple annotations for one URL:** Wrapped links create multiple annotations with the same URI. All get the same improved text. This is correct.
- **No overrides passed:** When `link_text_overrides` is None (e.g., called from `apply_ua_fixes.py` standalone script), behavior is unchanged — raw URL fallback.

### Testing

- Unit test: `populate_link_parent_tree` with `link_text_overrides` produces `/Link` elements with improved `/ActualText`
- Unit test: `_get_link_accessible_name` resolves `/StructParent` → `/ActualText`
- Integration test: full pipeline on syllabus → fresh re-parse shows improved link text → 2.4.4 issues reduced
- Regression: existing tests still pass (no behavior change when overrides not provided)

### Files Modified

| File | Change |
|------|--------|
| `src/agent/executor.py` | Build `link_text_overrides` dict, pass to `populate_link_parent_tree` |
| `src/tools/pdf_writer.py` | Accept and use `link_text_overrides` in `populate_link_parent_tree` |
| `src/tools/pdf_parser.py` | Add `_get_link_accessible_name()`, use during link extraction |
| `tests/test_links.py` or new test file | Unit + integration tests |
