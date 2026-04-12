# Link Text Harvest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `populate_link_parent_tree()` write agent-improved link text (instead of raw URLs) into connected `/Link` struct elements, and make the parser read it back via `/StructParent` resolution.

**Architecture:** The executor builds a URL→text mapping from executed `set_link_text` actions and passes it into `populate_link_parent_tree()`. The PDF writer uses it when creating `/Link` struct elements. The parser resolves `/StructParent` → `/ParentTree` → `/ActualText` during re-parse so validators see the improved text.

**Tech Stack:** Python, PyMuPDF (fitz), existing pdf_writer.py / pdf_parser.py / executor.py

---

### Task 1: Add `link_text_overrides` parameter to `populate_link_parent_tree`

**Files:**
- Modify: `src/tools/pdf_writer.py:2509-2511` (function signature)
- Modify: `src/tools/pdf_writer.py:2565-2566` (inner function signature)
- Modify: `src/tools/pdf_writer.py:2625-2626` (alt text selection)
- Test: `tests/test_pdf_writer.py` (add to `TestLinkParentTree`)

- [ ] **Step 1: Write the failing test**

In `tests/test_pdf_writer.py`, add to `TestLinkParentTree`:

```python
def test_link_text_overrides(self, tmp_path):
    """When overrides are provided, /Link elements use improved text."""
    from src.tools.pdf_writer import populate_link_parent_tree
    import re

    pdf_path = self._make_pdf_with_links(tmp_path, num_links=2)
    overrides = {
        "https://example.com/link0": "Example Homepage",
        # link1 has no override — should keep raw URL
    }
    result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)

    assert result.success
    assert result.annotations_linked == 2

    # Read back the /Link struct elements and check /ActualText
    doc = fitz.open(str(pdf_path))
    actual_texts = []
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if not obj or "/S /Link" not in obj:
            continue
        m = re.search(r"/ActualText\s*\(([^)]*)\)", obj)
        if m:
            actual_texts.append(m.group(1))
    doc.close()

    assert "Example Homepage" in actual_texts, f"Override text not found in {actual_texts}"
    # link1 should have raw URL
    assert any("example.com/link1" in t for t in actual_texts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree::test_link_text_overrides -v`
Expected: FAIL with `TypeError: populate_link_parent_tree() got an unexpected keyword argument 'link_text_overrides'`

- [ ] **Step 3: Add parameter to both functions and use it**

In `src/tools/pdf_writer.py`, change the outer function signature (line 2509):

```python
def populate_link_parent_tree(
    pdf_path: "str | Path",
    link_text_overrides: dict[str, str] | None = None,
) -> LinkParentTreeResult:
```

Change the inner call (inside `populate_link_parent_tree`, around line 2553):

```python
        return _populate_link_parent_tree_inner(doc, path, link_text_overrides)
```

Change the inner function signature (line 2565):

```python
def _populate_link_parent_tree_inner(
    doc: "fitz.Document", path: Path,
    link_text_overrides: dict[str, str] | None = None,
) -> LinkParentTreeResult:
```

Replace the alt text selection block (line 2625-2626):

```python
            # Get alt text — prefer agent-improved text over raw URL
            raw_alt = _get_annot_alt_text(doc, annot_xref)
            if link_text_overrides:
                uri = _extract_uri_from_annotation(doc, annot_xref)
                alt_text = link_text_overrides.get(uri, raw_alt)
            else:
                alt_text = raw_alt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree::test_link_text_overrides -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree -v`
Expected: All existing tests PASS (no behavior change when `link_text_overrides` is None)

- [ ] **Step 6: Commit**

```bash
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "feat: add link_text_overrides to populate_link_parent_tree"
```

---

### Task 2: Build overrides in executor and pass to `populate_link_parent_tree`

**Files:**
- Modify: `src/agent/executor.py:403-410` (build overrides, pass to call)

- [ ] **Step 1: Write the failing test**

In `tests/test_pdf_writer.py`, add to `TestLinkParentTree`:

```python
def test_url_normalization_trailing_slash(self, tmp_path):
    """Overrides match even with trailing slash differences."""
    from src.tools.pdf_writer import populate_link_parent_tree
    import re

    # Create PDF with a URL that has no trailing slash
    pdf_path = self._make_pdf_with_links(tmp_path, num_links=1)
    # Override with trailing slash version
    overrides = {
        "https://example.com/link0/": "Example with slash",
    }
    result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)

    assert result.success

    # Check that override was applied despite slash difference
    doc = fitz.open(str(pdf_path))
    actual_texts = []
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if not obj or "/S /Link" not in obj:
            continue
        m = re.search(r"/ActualText\s*\(([^)]*)\)", obj)
        if m:
            actual_texts.append(m.group(1))
    doc.close()

    assert "Example with slash" in actual_texts, f"Trailing-slash override not matched: {actual_texts}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree::test_url_normalization_trailing_slash -v`
Expected: FAIL — the trailing-slash URL won't match the annotation's URL (no trailing slash)

- [ ] **Step 3: Add URL normalization to override lookup**

In `src/tools/pdf_writer.py`, update the override lookup in `_populate_link_parent_tree_inner` (the block from Task 1 Step 3):

```python
            # Get alt text — prefer agent-improved text over raw URL
            raw_alt = _get_annot_alt_text(doc, annot_xref)
            if link_text_overrides:
                uri = _extract_uri_from_annotation(doc, annot_xref)
                alt_text = link_text_overrides.get(uri)
                if alt_text is None:
                    # Try with/without trailing slash
                    alt_text = link_text_overrides.get(uri.rstrip("/"))
                if alt_text is None:
                    alt_text = link_text_overrides.get(uri.rstrip("/") + "/")
                if alt_text is None:
                    alt_text = raw_alt
            else:
                alt_text = raw_alt
```

- [ ] **Step 4: Run both override tests**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree::test_link_text_overrides tests/test_pdf_writer.py::TestLinkParentTree::test_url_normalization_trailing_slash -v`
Expected: Both PASS

- [ ] **Step 5: Wire up in executor**

In `src/agent/executor.py`, before the `populate_link_parent_tree` call (around line 403), add:

```python
            # Build URL → improved text mapping from executed set_link_text actions
            link_text_overrides: dict[str, str] = {}
            for ua in updated_actions:
                if (ua.get("action_type") == "set_link_text"
                        and ua.get("status") == "executed"):
                    eid = ua["element_id"]
                    idx = link_id_to_idx.get(eid)
                    if idx is not None:
                        link_url = model_dict.get("links", [])[idx].get("url", "")
                        if link_url:
                            link_text_overrides[link_url] = ua["parameters"]["new_text"]
```

Then change the call at line 410:

```python
                pt_result = populate_link_parent_tree(
                    tagged_pdf_path,
                    link_text_overrides=link_text_overrides or None,
                )
```

- [ ] **Step 6: Run all link parent tree tests**

Run: `pytest tests/test_pdf_writer.py::TestLinkParentTree -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/tools/pdf_writer.py src/agent/executor.py tests/test_pdf_writer.py
git commit -m "feat: executor passes improved link text to populate_link_parent_tree"
```

---

### Task 3: Parser reads `/ActualText` via `/StructParent` resolution

**Files:**
- Modify: `src/tools/pdf_parser.py:810-833` (link extraction)
- Test: `tests/test_parser.py` (or `tests/test_pdf_writer.py` — whichever has PDF parsing tests)

- [ ] **Step 1: Write the failing test**

In `tests/test_pdf_writer.py`, add a new test class:

```python
class TestLinkAccessibleName:
    """Test that the parser reads /ActualText from /Link struct elements."""

    def test_parser_reads_struct_tree_link_text(self, tmp_path):
        """After populate_link_parent_tree with overrides, parser extracts improved text."""
        from src.tools.pdf_writer import populate_link_parent_tree
        from src.tools.pdf_parser import parse_pdf

        # Create PDF with a link annotation and struct tree
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 150), "Visit https://example.com/test for details")
        page.insert_link({
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(72, 135, 400, 155),
            "uri": "https://example.com/test",
        })

        # Create struct tree
        cat = doc.pdf_catalog()
        doc_elem_xref = doc.get_new_xref()
        st_root_xref = doc.get_new_xref()
        doc.update_object(
            doc_elem_xref,
            f"<< /Type /StructElem /S /Document /P {st_root_xref} 0 R /K [] >>",
        )
        doc.update_object(
            st_root_xref,
            f"<< /Type /StructTreeRoot /K {doc_elem_xref} 0 R >>",
        )
        doc.xref_set_key(cat, "StructTreeRoot", f"{st_root_xref} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")

        pdf_path = tmp_path / "link_with_struct.pdf"
        doc.save(str(pdf_path))
        doc.close()

        # Run populate_link_parent_tree with override
        overrides = {"https://example.com/test": "Example Test Page"}
        result = populate_link_parent_tree(pdf_path, link_text_overrides=overrides)
        assert result.success

        # Now parse the PDF and check link text
        parse_result = parse_pdf(str(pdf_path))
        assert parse_result.success

        # Find the link with this URL
        matching = [l for l in parse_result.document.links if l.url == "https://example.com/test"]
        assert len(matching) >= 1, f"No link found for test URL. Links: {parse_result.document.links}"
        assert matching[0].text == "Example Test Page", (
            f"Expected 'Example Test Page' but got {matching[0].text!r}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pdf_writer.py::TestLinkAccessibleName::test_parser_reads_struct_tree_link_text -v`
Expected: FAIL — parser returns content-stream text, not "Example Test Page"

- [ ] **Step 3: Implement `_get_link_accessible_name` in pdf_parser.py**

Add this function in `src/tools/pdf_parser.py` (before `_extract_text_blocks`):

```python
def _get_link_accessible_name(
    doc: "fitz.Document", page: "fitz.Page", link_rect: "fitz.Rect"
) -> str | None:
    """Resolve a link annotation's /StructParent → /ParentTree → /ActualText.

    Returns the /ActualText if it's descriptive (not a raw URL), else None.
    """
    import re

    # Find the annotation xref that matches this link rect
    annot = page.first_annot
    target_xref = None
    while annot:
        if annot.type[0] == 2:  # Link annotation
            annot_rect = annot.rect
            if abs(annot_rect.x0 - link_rect.x0) < 2 and abs(annot_rect.y0 - link_rect.y0) < 2:
                target_xref = annot.xref
                break
        annot = annot.next

    if target_xref is None:
        return None

    # Read /StructParent
    try:
        sp = doc.xref_get_key(target_xref, "StructParent")
        if sp[0] in ("null", "undefined"):
            return None
        sp_num = int(sp[1])
    except (ValueError, TypeError):
        return None

    # Resolve /ParentTree
    try:
        cat = doc.pdf_catalog()
        st_key = doc.xref_get_key(cat, "StructTreeRoot")
        if st_key[0] != "xref":
            return None
        st_root = int(st_key[1].split()[0])
        pt_key = doc.xref_get_key(st_root, "ParentTree")
        if pt_key[0] != "xref":
            return None
        pt_xref = int(pt_key[1].split()[0])
        pt_obj = doc.xref_object(pt_xref) or ""

        # Parse /Nums array to find our entry
        nums_match = re.search(r"/Nums\s*\[([^\]]*)\]", pt_obj, re.DOTALL)
        if not nums_match:
            return None

        # Find the entry for our StructParent number
        nums_str = nums_match.group(1)
        pairs = re.findall(r"(\d+)\s+(\d+)\s+0\s+R", nums_str)
        elem_xref = None
        for k, v in pairs:
            if int(k) == sp_num:
                elem_xref = int(v)
                break

        if elem_xref is None:
            return None

        # Read /ActualText from the struct element
        elem_obj = doc.xref_object(elem_xref) or ""
        at_match = re.search(r"/ActualText\s*\(([^)]*)\)", elem_obj)
        if not at_match:
            return None

        text = at_match.group(1).strip()
        # Only return if it's NOT a raw URL (otherwise content-stream text is fine)
        if text and not text.startswith(("http://", "https://", "mailto:", "ftp://")):
            return text
    except Exception:
        pass

    return None
```

- [ ] **Step 4: Use it in link extraction**

In `src/tools/pdf_parser.py`, in `_extract_text_blocks` around line 817, after getting `link_text`:

```python
                        link_text = page.get_textbox(link_rect).strip()
                        # Check struct tree for improved accessible name
                        accessible_name = _get_link_accessible_name(doc, page, link_rect)
                        if accessible_name:
                            link_text = accessible_name
```

Note: `doc` (the fitz.Document) needs to be available in this scope. Check if `_extract_text_blocks` receives it. If not, it receives `page` which has `page.parent` → the document.

Update the line to:

```python
                        accessible_name = _get_link_accessible_name(page.parent, page, link_rect)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_pdf_writer.py::TestLinkAccessibleName::test_parser_reads_struct_tree_link_text -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All tests pass (995+)

- [ ] **Step 7: Commit**

```bash
git add src/tools/pdf_parser.py tests/test_pdf_writer.py
git commit -m "feat: parser reads /ActualText from /Link struct elements via /StructParent"
```

---

### Task 4: End-to-end verification

**Files:**
- No code changes — verification only

- [ ] **Step 1: Run the syllabus through the full pipeline**

```bash
python3 -c "
import os, logging
logging.basicConfig(level=logging.WARNING)
from src.agent.orchestrator import process
from src.models.pipeline import RemediationRequest, CourseContext

req = RemediationRequest(
    document_path='testdocs/EMAT 8030 syllabus spring 2026.pdf',
    output_dir='/tmp/link_test_e2e',
    course_context=CourseContext(course_name='EMAT 8030', subject='Mathematics Education'),
)
os.makedirs('/tmp/link_test_e2e', exist_ok=True)
result = process(req)
print(f'Success: {result.success}')
print(f'Issues: {result.issues_before} -> {result.issues_after}')

# Fresh re-parse
from src.tools.pdf_parser import parse_pdf
from src.tools.validator import validate_document
post = parse_pdf(result.output_path)
if post.success:
    report = validate_document(post.document)
    for check in report.checks:
        if '2.4.4' in check.criterion:
            print(f'2.4.4 [{check.status}] issues={check.issue_count}')
            for issue in check.issues:
                print(f'  {issue}')
"
```

Expected: Fewer 2.4.4 issues than the 6 we saw pre-fix. Links the agent improved (plagiarism, writing center, honesty policy, DOI references) should show descriptive text.

- [ ] **Step 2: Commit final state and update NOW.md**

```bash
git add NOW.md
git commit -m "Link text harvest: end-to-end verified, update NOW.md"
```
