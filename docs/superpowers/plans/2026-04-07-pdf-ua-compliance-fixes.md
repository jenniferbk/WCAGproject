# PDF/UA Compliance Post-Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement two standalone post-processors (`apply_pdf_ua_metadata` and `mark_untagged_content_as_artifact`) that eliminate the majority of veraPDF PDF/UA-1 failed checks on already-tagged PDFs — targeting the 136 metadata failures and 179,291 content-tagging failures that dominate the benchmark.

**Architecture:** Two pure functions added to `src/tools/pdf_writer.py` that modify a given PDF in place. An orchestration script `scripts/apply_ua_fixes.py` applies them to a directory of PDFs with per-track verification gates (text extraction, veraPDF rule delta, optional visual diff). Once validated, both functions wire into `src/agent/executor.py::execute_pdf()` as post-tagging steps.

**Tech Stack:** Python 3.14, PyMuPDF (fitz) 1.27.1, lxml.etree, veraPDF CLI 1.28.2 (already installed), existing `_tokenize_content_stream` / `_reassemble_stream` infrastructure in `pdf_writer.py`.

**Spec:** `docs/superpowers/specs/2026-04-07-pdf-ua-compliance-fixes-design.md`

---

## File Structure

**Modify:**
- `src/tools/pdf_writer.py` — add `apply_pdf_ua_metadata()`, `mark_untagged_content_as_artifact()`, and private helpers. Current file is 1569 lines; additions go at the end. No splits in this plan (preserve caller stability; split is future work).
- `src/agent/executor.py` — wire both new functions into `execute_pdf()` after iText tagging (near line ~317 where contrast fixes are applied).
- `tests/test_pdf_writer.py` — add new unit test classes. Existing 49 tests stay untouched.

**Create:**
- `scripts/apply_ua_fixes.py` — orchestration script for applying fixes to a directory of remediated PDFs with verification gates.

**Reuse (no modification):**
- `src/tools/pdf_writer.py::_tokenize_content_stream()` — existing tokenizer battle-tested on 125 benchmark docs for contrast fixes.
- `src/tools/pdf_writer.py::_reassemble_stream()` — existing stream reassembly.
- `src/tools/pdf_writer.py::_pixel_diff()`, `_render_page()` — existing visual diff infrastructure.
- `src/tools/verapdf_checker.py::check_pdf_ua()` — just fixed in commit `e9b6361` for v1.28 JSON output.

---

## Task 1: Track C — helper to read or synthesize XMP

**Files:**
- Modify: `src/tools/pdf_writer.py` (append)
- Modify: `tests/test_pdf_writer.py` (append new test class)

**Why this task first:** Track C's XMP logic is simpler than Track A and serves as a smoke test for the new-file-additions workflow. We'll have the whole file layout in place before tackling the complex content-stream walker.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_writer.py`:

```python
class TestPdfUaMetadata:
    """Tests for apply_pdf_ua_metadata() — Track C."""

    def _make_minimal_pdf(self, tmp_path, with_xmp: bool = True) -> Path:
        """Create a minimal test PDF with or without an XMP metadata stream."""
        import fitz
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata({"title": "Test Title", "author": "Test Author"})
        if with_xmp:
            # Force fitz to emit an XMP stream by setting xml metadata
            doc.set_xml_metadata(
                '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
                '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description rdf:about="" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Test Title</rdf:li></rdf:Alt></dc:title>'
                '</rdf:Description>'
                '</rdf:RDF>'
                '</x:xmpmeta>'
                '<?xpacket end="w"?>'
            )
        out = tmp_path / "minimal.pdf"
        doc.save(str(out))
        doc.close()
        return out

    def test_helper_reads_existing_xmp(self, tmp_path):
        from src.tools.pdf_writer import _read_or_synthesize_xmp
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        doc = fitz.open(str(pdf))
        xmp_bytes = _read_or_synthesize_xmp(doc)
        doc.close()
        assert b"dc:title" in xmp_bytes
        assert b"Test Title" in xmp_bytes

    def test_helper_synthesizes_when_no_xmp(self, tmp_path):
        from src.tools.pdf_writer import _read_or_synthesize_xmp
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=False)
        doc = fitz.open(str(pdf))
        xmp_bytes = _read_or_synthesize_xmp(doc)
        doc.close()
        # Synthesized XMP should at least have rdf:RDF wrapper
        assert b"rdf:RDF" in xmp_bytes
        assert b"rdf:Description" in xmp_bytes
```

- [ ] **Step 2: Run test to verify it fails**

```
python3 -m pytest tests/test_pdf_writer.py::TestPdfUaMetadata -v
```

Expected: FAIL with `ImportError: cannot import name '_read_or_synthesize_xmp'`.

- [ ] **Step 3: Implement `_read_or_synthesize_xmp`**

Append to the end of `src/tools/pdf_writer.py`:

```python
# ─────────────────────────────────────────────────────────────────────
# PDF/UA post-processing: metadata (Track C) and content-stream
# artifact marking (Track A). See
# docs/superpowers/specs/2026-04-07-pdf-ua-compliance-fixes-design.md
# ─────────────────────────────────────────────────────────────────────


def _read_or_synthesize_xmp(doc: "fitz.Document") -> bytes:
    """Return the document's XMP metadata stream bytes, or synthesize one.

    fitz's ``get_xml_metadata()`` returns a decoded string for existing
    streams and an empty string when no /Metadata exists. We return raw
    bytes so downstream XML parsing handles encoding itself. When no
    XMP exists, we synthesize a minimal RDF/XML skeleton pre-populated
    from the doc's core metadata (title, author, subject, creator).
    """
    existing = doc.get_xml_metadata() or ""
    if existing.strip():
        return existing.encode("utf-8")

    md = doc.metadata or {}
    title = (md.get("title") or "").strip()
    author = (md.get("author") or "").strip()
    subject = (md.get("subject") or "").strip()

    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    dc_title = (
        f"<dc:title><rdf:Alt><rdf:li xml:lang=\"x-default\">{esc(title)}</rdf:li></rdf:Alt></dc:title>"
        if title else ""
    )
    dc_creator = (
        f"<dc:creator><rdf:Seq><rdf:li>{esc(author)}</rdf:li></rdf:Seq></dc:creator>"
        if author else ""
    )
    dc_description = (
        f"<dc:description><rdf:Alt><rdf:li xml:lang=\"x-default\">{esc(subject)}</rdf:li></rdf:Alt></dc:description>"
        if subject else ""
    )

    synthesized = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="A11yRemediate">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"{dc_title}{dc_creator}{dc_description}"
        '</rdf:Description>'
        '</rdf:RDF>'
        '</x:xmpmeta>'
        '<?xpacket end="w"?>'
    )
    return synthesized.encode("utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/test_pdf_writer.py::TestPdfUaMetadata -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _read_or_synthesize_xmp helper for Track C metadata fixes"
```

---

## Task 2: Track C — `apply_pdf_ua_metadata()` core

**Files:**
- Modify: `src/tools/pdf_writer.py` (append)
- Modify: `tests/test_pdf_writer.py` (append to TestPdfUaMetadata)

- [ ] **Step 1: Write the failing test**

Append to `TestPdfUaMetadata`:

```python
    def test_apply_adds_pdfuaid_part(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        result = apply_pdf_ua_metadata(pdf)
        assert result.success
        # Re-open and verify pdfuaid:part=1 is present in XMP
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        assert "pdfuaid" in xmp.lower()
        assert "<pdfuaid:part>1</pdfuaid:part>" in xmp

    def test_apply_preserves_existing_dc_title(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        assert "Test Title" in xmp
```

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/test_pdf_writer.py::TestPdfUaMetadata::test_apply_adds_pdfuaid_part -v
```

Expected: FAIL with `ImportError: cannot import name 'apply_pdf_ua_metadata'`.

- [ ] **Step 3: Implement `apply_pdf_ua_metadata()` — metadata-only version**

Append to `src/tools/pdf_writer.py`:

```python
from dataclasses import dataclass, field
# ^ only if not already imported at top of file — check first

# If the import is not already at the top of pdf_writer.py, leave the
# existing imports alone; the dataclass/field imports are present in
# the existing file since we already define PdfWriteResult as a
# dataclass. The snippet below assumes they are available.


@dataclass
class MetadataResult:
    """Result of apply_pdf_ua_metadata()."""
    success: bool
    changes: list[str] = field(default_factory=list)
    error: str = ""


# XMP namespaces we care about.
_PDFUAID_NS = "http://www.aiim.org/pdfua/ns/id/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def apply_pdf_ua_metadata(pdf_path: str | Path) -> MetadataResult:
    """Apply Track C — PDF/UA metadata fixes to a PDF.

    Writes the document's XMP ``pdfuaid:part=1`` (rule 5-1), sets
    ``/ViewerPreferences /DisplayDocTitle true`` on the catalog (rule
    7.1-10), and ensures the catalog has a ``/Metadata`` key (rule
    7.1-8). Safe to run on any PDF — the function preserves existing
    XMP elements and ViewerPreferences entries.

    Args:
        pdf_path: Path to the PDF file. Modified in place.

    Returns:
        MetadataResult with success flag and a human-readable change log.
    """
    import fitz
    from lxml import etree

    path = Path(pdf_path)
    if not path.exists():
        return MetadataResult(success=False, error=f"File not found: {path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return MetadataResult(success=False, error=f"Open failed: {exc}")

    changes: list[str] = []
    try:
        # 1. XMP: add pdfuaid:part=1 (rule 5-1)
        xmp_bytes = _read_or_synthesize_xmp(doc)
        new_xmp, xmp_changed = _ensure_pdfuaid_in_xmp(xmp_bytes)
        if xmp_changed:
            # fitz expects str input
            doc.set_xml_metadata(new_xmp.decode("utf-8"))
            changes.append("xmp:pdfuaid:part=1")

        # 2. ViewerPreferences/DisplayDocTitle (rule 7.1-10)
        cat_xref = doc.pdf_catalog()
        vp_raw = doc.xref_get_key(cat_xref, "ViewerPreferences")
        if vp_raw[0] == "dict":
            # Parse existing dict and add DisplayDocTitle while
            # preserving other keys.
            new_vp = _ensure_display_doc_title(vp_raw[1])
        else:
            new_vp = "<< /DisplayDocTitle true >>"
        doc.xref_set_key(cat_xref, "ViewerPreferences", new_vp)
        changes.append("catalog:ViewerPreferences/DisplayDocTitle=true")

        # 3. Ensure /Metadata key on catalog (rule 7.1-8). fitz's
        #    set_xml_metadata() already wires up the /Metadata entry,
        #    so this is typically already satisfied after step 1. We
        #    verify it as a defensive check.
        md_raw = doc.xref_get_key(cat_xref, "Metadata")
        if md_raw[0] != "xref":
            changes.append("catalog:Metadata=missing(unexpected)")

        doc.save(str(path), incremental=True, encryption=0)
    except Exception as exc:
        doc.close()
        return MetadataResult(
            success=False, error=f"apply_pdf_ua_metadata failed: {exc}"
        )
    finally:
        if not doc.is_closed:
            doc.close()

    return MetadataResult(success=True, changes=changes)


def _ensure_pdfuaid_in_xmp(xmp_bytes: bytes) -> tuple[bytes, bool]:
    """Return (possibly-modified XMP bytes, changed flag).

    Adds a ``<pdfuaid:part>1</pdfuaid:part>`` element into the first
    rdf:Description. Preserves every other element. If pdfuaid:part is
    already present (with any value), updates it to 1. If parsing fails,
    returns original bytes and False.
    """
    from lxml import etree

    try:
        # lxml is strict; XMP has an xpacket PI wrapper we need to strip
        # for parsing and restore for serialization.
        xmp_str = xmp_bytes.decode("utf-8", errors="replace")
        # Find the xmpmeta element start and end so we can parse just
        # the XML payload without the xpacket PIs.
        start = xmp_str.find("<x:xmpmeta")
        end = xmp_str.find("</x:xmpmeta>")
        if start == -1 or end == -1:
            # No recognizable xmpmeta wrapper; parse whole thing as XML.
            payload = xmp_str.strip()
            prefix = ""
            suffix = ""
        else:
            end += len("</x:xmpmeta>")
            prefix = xmp_str[:start]
            payload = xmp_str[start:end]
            suffix = xmp_str[end:]

        parser = etree.XMLParser(remove_blank_text=False, recover=True)
        root = etree.fromstring(payload.encode("utf-8"), parser)
        if root is None:
            return xmp_bytes, False

        nsmap = {"rdf": _RDF_NS, "pdfuaid": _PDFUAID_NS}
        descriptions = root.findall(f".//{{{_RDF_NS}}}Description")
        if not descriptions:
            return xmp_bytes, False
        target = descriptions[0]

        # Check for existing pdfuaid:part (in any attribute or child form)
        existing_part = target.find(f"{{{_PDFUAID_NS}}}part")
        if existing_part is not None:
            if existing_part.text == "1":
                return xmp_bytes, False
            existing_part.text = "1"
        else:
            # Attribute form sometimes appears: rdf:Description pdfuaid:part="1"
            attr_key = f"{{{_PDFUAID_NS}}}part"
            if target.get(attr_key) == "1":
                return xmp_bytes, False
            # Add as child element.
            part_elem = etree.SubElement(target, f"{{{_PDFUAID_NS}}}part")
            part_elem.text = "1"

        new_payload = etree.tostring(
            root, encoding="utf-8", xml_declaration=False
        ).decode("utf-8")
        new_full = prefix + new_payload + suffix
        return new_full.encode("utf-8"), True
    except Exception:
        return xmp_bytes, False


def _ensure_display_doc_title(vp_dict_str: str) -> str:
    """Given the raw PDF object string for /ViewerPreferences, return a
    new dict string with /DisplayDocTitle set to true, preserving all
    other keys.
    """
    # PyMuPDF returns the dict contents without the surrounding << >>.
    # Normalise by trimming whitespace.
    body = vp_dict_str.strip()
    if body.startswith("<<"):
        body = body[2:]
    if body.endswith(">>"):
        body = body[:-2]
    body = body.strip()

    # Remove any existing DisplayDocTitle entry.
    import re as _re
    body = _re.sub(r"/DisplayDocTitle\s+(true|false)\s*", "", body).strip()

    if body:
        return f"<< {body} /DisplayDocTitle true >>"
    return "<< /DisplayDocTitle true >>"
```

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/test_pdf_writer.py::TestPdfUaMetadata -v
```

Expected: 4 passed (2 helper tests from Task 1, 2 new).

- [ ] **Step 5: Commit**

```
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add apply_pdf_ua_metadata() for PDF/UA Track C (rules 5-1, 7.1-8, 7.1-10)"
```

---

## Task 3: Track C — ViewerPreferences and idempotency tests

**Files:**
- Modify: `tests/test_pdf_writer.py` (append to TestPdfUaMetadata)

- [ ] **Step 1: Write the failing tests**

Append to `TestPdfUaMetadata`:

```python
    def test_apply_sets_display_doc_title(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        vp = doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")
        doc.close()
        assert vp[0] == "dict"
        assert "DisplayDocTitle" in vp[1]
        assert "true" in vp[1]

    def test_apply_preserves_other_viewer_prefs(self, tmp_path):
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        # Pre-populate ViewerPreferences with a non-DisplayDocTitle entry
        doc = fitz.open(str(pdf))
        doc.xref_set_key(
            doc.pdf_catalog(),
            "ViewerPreferences",
            "<< /FitWindow true >>",
        )
        doc.save(str(pdf), incremental=True, encryption=0)
        doc.close()

        apply_pdf_ua_metadata(pdf)

        doc = fitz.open(str(pdf))
        vp = doc.xref_get_key(doc.pdf_catalog(), "ViewerPreferences")
        doc.close()
        assert "FitWindow" in vp[1]
        assert "DisplayDocTitle" in vp[1]

    def test_apply_is_idempotent(self, tmp_path):
        """Running twice must not add a second pdfuaid:part element."""
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=True)
        apply_pdf_ua_metadata(pdf)
        apply_pdf_ua_metadata(pdf)
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        # pdfuaid:part element appears exactly once
        assert xmp.count("<pdfuaid:part>") == 1
        assert xmp.count("</pdfuaid:part>") == 1

    def test_apply_synthesizes_xmp_for_bare_pdf(self, tmp_path):
        """A PDF with no /Metadata stream gets a fresh XMP with pdfuaid."""
        from src.tools.pdf_writer import apply_pdf_ua_metadata
        import fitz
        pdf = self._make_minimal_pdf(tmp_path, with_xmp=False)
        result = apply_pdf_ua_metadata(pdf)
        assert result.success
        doc = fitz.open(str(pdf))
        xmp = doc.get_xml_metadata() or ""
        doc.close()
        assert "<pdfuaid:part>1</pdfuaid:part>" in xmp
```

- [ ] **Step 2: Run tests to verify they pass**

Track C implementation should already satisfy these (we wrote it completely in Task 2). Run:

```
python3 -m pytest tests/test_pdf_writer.py::TestPdfUaMetadata -v
```

Expected: 8 passed.

If any fail, fix the implementation in `pdf_writer.py` until they pass. Do not commit until all green.

- [ ] **Step 3: Commit**

```
git add tests/test_pdf_writer.py
git commit -m "Test Track C metadata: ViewerPreferences preservation and idempotency"
```

---

## Task 4: Track A — operator classification helpers

**Files:**
- Modify: `src/tools/pdf_writer.py` (append)
- Modify: `tests/test_pdf_writer.py` (append new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pdf_writer.py`:

```python
class TestArtifactMarkingHelpers:
    """Tests for Track A operator classification."""

    def test_tj_is_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        assert _is_content_producing_op("Tj")
        assert _is_content_producing_op("TJ")
        assert _is_content_producing_op("'")
        assert _is_content_producing_op('"')

    def test_path_painting_is_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        for op in ["S", "s", "f", "F", "f*", "B", "B*", "b", "b*"]:
            assert _is_content_producing_op(op), f"{op} should be content-producing"

    def test_do_and_sh_are_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        assert _is_content_producing_op("Do")
        assert _is_content_producing_op("sh")

    def test_state_ops_not_content_producing(self):
        from src.tools.pdf_writer import _is_content_producing_op
        for op in ["q", "Q", "cm", "Tf", "Td", "TD", "Tm", "T*",
                   "gs", "rg", "RG", "g", "G", "k", "K", "sc", "SC",
                   "scn", "SCN", "cs", "CS", "w", "J", "j", "M", "d",
                   "ri", "i", "m", "l", "c", "v", "y", "re", "h", "n",
                   "BT", "ET", "W", "W*"]:
            assert not _is_content_producing_op(op), f"{op} should NOT be content-producing"

    def test_state_ops_classified_as_state(self):
        from src.tools.pdf_writer import _is_state_setting_op
        for op in ["q", "Q", "cm", "Tf", "Td", "gs", "rg", "BT", "ET"]:
            assert _is_state_setting_op(op), f"{op} should be state-setting"

    def test_bdc_emc_not_classified_as_either(self):
        from src.tools.pdf_writer import _is_content_producing_op, _is_state_setting_op
        # BDC/EMC are marked-content operators, not content/state
        for op in ["BDC", "BMC", "EMC"]:
            assert not _is_content_producing_op(op)
            assert not _is_state_setting_op(op)
```

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/test_pdf_writer.py::TestArtifactMarkingHelpers -v
```

Expected: FAIL with `ImportError: cannot import name '_is_content_producing_op'`.

- [ ] **Step 3: Implement classifier helpers**

Append to `src/tools/pdf_writer.py`:

```python
# Operator classification for Track A artifact marking.
# See spec §"Operator classification" for the rationale.

_CONTENT_PRODUCING_OPS = frozenset({
    # Text showing (inside BT/ET)
    "Tj", "TJ", "'", '"',
    # Path painting
    "S", "s", "f", "F", "f*", "B", "B*", "b", "b*",
    # Shading
    "sh",
    # XObject reference
    "Do",
})

_STATE_SETTING_OPS = frozenset({
    # Graphics state save/restore
    "q", "Q",
    # Transform
    "cm",
    # Text state
    "Tf", "Tr", "Tc", "Tw", "Tz", "TL", "Ts",
    # Text positioning
    "Td", "TD", "Tm", "T*",
    # Graphics state parameter
    "gs",
    # Color
    "rg", "RG", "g", "G", "k", "K",
    "sc", "SC", "scn", "SCN", "cs", "CS",
    # Path style
    "w", "J", "j", "M", "d", "ri", "i",
    # Path construction (no output on their own)
    "m", "l", "c", "v", "y", "re", "h", "n",
    # Clipping flags (no output; affect subsequent path)
    "W", "W*",
    # Text object markers (no output on their own)
    "BT", "ET",
})


def _is_content_producing_op(op: str) -> bool:
    """Return True if the operator produces visible marks on the page.

    Inline images (BI/ID/EI) are handled by the tokenizer as a single
    atom and this function is not called on them individually — the
    walker treats the atom as content-producing.
    """
    return op in _CONTENT_PRODUCING_OPS


def _is_state_setting_op(op: str) -> bool:
    """Return True if the operator sets graphics state without producing marks."""
    return op in _STATE_SETTING_OPS
```

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/test_pdf_writer.py::TestArtifactMarkingHelpers -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add operator classification helpers for Track A artifact marking"
```

---

## Task 5: Track A — run detector state machine

**Files:**
- Modify: `src/tools/pdf_writer.py` (append)
- Modify: `tests/test_pdf_writer.py` (append new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pdf_writer.py`:

```python
class TestFindUntaggedRuns:
    """Tests for _find_untagged_content_runs() — Track A state machine."""

    def _tokenize(self, stream_str: str):
        from src.tools.pdf_writer import _tokenize_content_stream
        return _tokenize_content_stream(stream_str.encode("latin-1"))

    def test_empty_stream_no_runs(self):
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("")
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_state_ops_alone_no_runs(self):
        """A page with only state ops at depth 0 — no content to wrap."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("q\n1 0 0 1 0 0 cm\nQ\n")
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_content_at_depth_0_yields_run(self):
        """A simple BT/ET text object at depth 0 — one run covering it."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("BT\n/F0 10 Tf\n72 720 Td\n(hi) Tj\nET\n")
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1
        # Run should include the Tj token (the content-producer)
        start, end = runs[0]
        ops_in_run = [t.op for t in tokens[start:end + 1] if t.op]
        assert "Tj" in ops_in_run

    def test_content_inside_bdc_not_wrapped(self):
        """Content inside /P BDC is at depth 1 — yields no run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = "/P << /MCID 0 >> BDC\nBT (hi) Tj ET\nEMC\n"
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_mixed_tagged_and_untagged(self):
        """Tagged body plus untagged footer — only the footer becomes a run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = (
            "/P << /MCID 0 >> BDC\n"
            "BT (body) Tj ET\n"
            "EMC\n"
            "BT (footer) Tj ET\n"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1
        # The run should cover the footer's Tj
        start, end = runs[0]
        run_text = "".join(t.raw for t in tokens[start:end + 1])
        assert "footer" in run_text
        assert "body" not in run_text

    def test_nested_bdc_handled(self):
        """/P BDC /Span BDC content EMC EMC — untouched."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        stream = (
            "/P BDC /Span BDC BT (x) Tj ET EMC EMC\n"
        )
        tokens = self._tokenize(stream)
        runs = _find_untagged_content_runs(tokens)
        assert runs == []

    def test_do_operator_at_depth_0_yields_run(self):
        """Form XObject call (Do) at depth 0 produces a run."""
        from src.tools.pdf_writer import _find_untagged_content_runs
        tokens = self._tokenize("/Fm0 Do\n")
        runs = _find_untagged_content_runs(tokens)
        assert len(runs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/test_pdf_writer.py::TestFindUntaggedRuns -v
```

Expected: FAIL with `ImportError: cannot import name '_find_untagged_content_runs'`.

- [ ] **Step 3: Implement the state machine**

Append to `src/tools/pdf_writer.py`:

```python
def _find_untagged_content_runs(tokens: list["Token"]) -> list[tuple[int, int]]:
    """Find runs of depth-0 untagged content that need /Artifact wrapping.

    Walks the token list maintaining BDC nesting depth. A "run" is a
    contiguous sequence of tokens at depth 0 that begins with a
    content-producing operator and may include subsequent state-setting
    operators. State-only sequences at depth 0 do not start a run —
    they are left untouched.

    Args:
        tokens: Output of ``_tokenize_content_stream``.

    Returns:
        List of ``(start_index, end_index)`` pairs, inclusive on both
        ends, where each pair denotes a run to wrap in /Artifact BDC / EMC.
    """
    runs: list[tuple[int, int]] = []
    depth = 0
    run_start: int | None = None
    run_end: int | None = None

    def _close_run():
        nonlocal run_start, run_end
        if run_start is not None and run_end is not None:
            runs.append((run_start, run_end))
        run_start = None
        run_end = None

    for i, token in enumerate(tokens):
        op = token.op
        if op is None:
            # Non-op token (operand, whitespace, comment). If we're
            # inside an open run, it stays part of the run implicitly
            # because run_start..run_end is an index range that
            # includes everything between.
            continue

        if op == "BDC" or op == "BMC":
            _close_run()
            depth += 1
            continue

        if op == "EMC":
            depth -= 1
            # After closing a tag we remain at the new depth; don't
            # start a new run unless we hit a content-producing op.
            continue

        if depth != 0:
            # We're inside a tagged region; leave it alone.
            continue

        if _is_content_producing_op(op):
            if run_start is None:
                run_start = i
            run_end = i
        elif _is_state_setting_op(op):
            if run_start is not None:
                # State op extends an open run; doesn't start one.
                run_end = i
        # Unknown / BI/ID/EI / other: if the tokenizer has grouped
        # inline images as single tokens with op None or a synthetic
        # marker, handle them via token.raw inspection below.

        # Handle inline images explicitly: the tokenizer we reuse may
        # represent BI ... EI as either (a) three tokens BI, ID, EI or
        # (b) a single compound token. Either way, we treat the region
        # they cover as content-producing.
        if op in ("BI", "EI"):
            if run_start is None:
                run_start = i
            run_end = i

    _close_run()
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

```
python3 -m pytest tests/test_pdf_writer.py::TestFindUntaggedRuns -v
```

Expected: 7 passed. If inline image handling fails, inspect the token structure produced by `_tokenize_content_stream` and adjust accordingly — the test_do_operator test may need refinement depending on how `Do` is tokenized.

- [ ] **Step 5: If tokenizer treats `Do` or `BI/ID/EI` differently than assumed, adjust**

Run a quick inspection:

```
python3 -c "
from src.tools.pdf_writer import _tokenize_content_stream
toks = _tokenize_content_stream(b'/Fm0 Do\n')
for t in toks:
    print(repr(t))
"
```

If `Do` doesn't come through as a token with `op='Do'`, update either the classifier or the detector to match the tokenizer's actual output. Re-run tests until green.

- [ ] **Step 6: Commit**

```
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add _find_untagged_content_runs state machine for Track A"
```

---

## Task 6: Track A — wrapper emission + single-page end-to-end

**Files:**
- Modify: `src/tools/pdf_writer.py` (append)
- Modify: `tests/test_pdf_writer.py` (append new test class)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_writer.py`:

```python
class TestMarkUntaggedContent:
    """End-to-end tests for mark_untagged_content_as_artifact()."""

    def _pdf_with_untagged_footer(self, tmp_path) -> Path:
        """Create a PDF whose content stream has a BDC-tagged body and
        an untagged footer line, using only fitz primitives."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        # Page content stream initially has the body text added via
        # insert_text(). We'll then inject a /P BDC/EMC around it to
        # simulate iText tagging.
        page.insert_text((50, 50), "Body text")
        page.insert_text((50, 380), "Page 1")  # untagged footer
        out = tmp_path / "with_footer.pdf"
        doc.save(str(out))
        doc.close()

        # Post-process: wrap the first BT..ET in /P BDC/EMC so the body
        # is "tagged" and the footer is "untagged" at depth 0.
        doc = fitz.open(str(out))
        p = doc[0]
        content_xref = doc.get_page_xref_list(0)[0] if hasattr(doc, 'get_page_xref_list') else None
        # Simpler: read the stream bytes, wrap first BT/ET manually.
        import re as _re
        stream = p.read_contents()
        # Find first BT..ET span
        m = _re.search(rb"BT\b.*?ET", stream, flags=_re.DOTALL)
        if not m:
            doc.close()
            raise RuntimeError("Test fixture: no BT..ET in generated content stream")
        wrapped = (
            stream[:m.start()]
            + b"/P << /MCID 0 >> BDC\n"
            + m.group(0)
            + b"\nEMC\n"
            + stream[m.end():]
        )
        # Replace the first /Contents stream with the wrapped version.
        contents_ref = doc.xref_get_key(p.xref, "Contents")
        if contents_ref[0] == "xref":
            xref = int(contents_ref[1].split()[0])
        elif contents_ref[0] == "array":
            xref = int(contents_ref[1].strip("[]").split()[0])
        else:
            doc.close()
            raise RuntimeError(f"Unexpected /Contents type: {contents_ref}")
        doc.update_stream(xref, wrapped)
        # Mark page as tagged so /P BDC makes sense to validators.
        cat = doc.pdf_catalog()
        if doc.xref_get_key(cat, "StructTreeRoot")[0] != "xref":
            # Minimal struct tree so BDC refs don't look totally orphaned.
            # For the test we don't actually need veraPDF compliance —
            # just need the walker to see the structure.
            pass
        doc.save(str(out), incremental=True, encryption=0)
        doc.close()
        return out

    def test_wraps_untagged_footer(self, tmp_path):
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        import fitz
        pdf = self._pdf_with_untagged_footer(tmp_path)
        result = mark_untagged_content_as_artifact(pdf)
        assert result.success
        assert result.artifact_wrappers_inserted >= 1
        # Verify the output has /Artifact BDC in the content stream
        doc = fitz.open(str(pdf))
        content = doc[0].read_contents()
        doc.close()
        assert b"/Artifact BDC" in content

    def test_empty_pdf_no_op(self, tmp_path):
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        import fitz
        doc = fitz.open()
        doc.new_page()  # blank page, no content
        out = tmp_path / "empty.pdf"
        doc.save(str(out))
        doc.close()
        result = mark_untagged_content_as_artifact(out)
        assert result.success
        assert result.artifact_wrappers_inserted == 0

    def test_idempotent(self, tmp_path):
        """Running twice inserts zero wrappers on the second call."""
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        pdf = self._pdf_with_untagged_footer(tmp_path)
        first = mark_untagged_content_as_artifact(pdf)
        second = mark_untagged_content_as_artifact(pdf)
        assert first.success and second.success
        assert second.artifact_wrappers_inserted == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
python3 -m pytest tests/test_pdf_writer.py::TestMarkUntaggedContent -v
```

Expected: FAIL with `ImportError: cannot import name 'mark_untagged_content_as_artifact'`.

- [ ] **Step 3: Implement the main function**

Append to `src/tools/pdf_writer.py`:

```python
@dataclass
class ArtifactMarkingResult:
    """Result of mark_untagged_content_as_artifact()."""
    success: bool
    pages_modified: int = 0
    artifact_wrappers_inserted: int = 0
    pages_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def mark_untagged_content_as_artifact(pdf_path: str | Path) -> ArtifactMarkingResult:
    """Walk the PDF's content streams and wrap depth-0 untagged content
    in /Artifact BDC / EMC markers. Satisfies veraPDF rule 7.1-3
    ("Content shall be marked as Artifact or tagged as real content")
    for every content item the walker can reach.

    Does not recurse into form XObjects referenced via Do — that's a
    known v1 limitation (see spec §"Out of scope (v1 limits)").

    Args:
        pdf_path: Path to the PDF file. Modified in place.

    Returns:
        ArtifactMarkingResult with counts and per-page errors.
    """
    import fitz

    path = Path(pdf_path)
    if not path.exists():
        return ArtifactMarkingResult(
            success=False, errors=[f"File not found: {path}"]
        )

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return ArtifactMarkingResult(
            success=False, errors=[f"Open failed: {exc}"]
        )

    result = ArtifactMarkingResult(success=True)
    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            try:
                stream_bytes = page.read_contents()
            except Exception as exc:
                result.errors.append(
                    f"page {page_idx}: read_contents failed: {exc}"
                )
                result.pages_skipped += 1
                continue

            if not stream_bytes:
                result.pages_skipped += 1
                continue

            tokens = _tokenize_content_stream(stream_bytes)
            runs = _find_untagged_content_runs(tokens)

            if not runs:
                result.pages_skipped += 1
                continue

            new_stream = _apply_artifact_wrappers(tokens, runs)

            # Find the xref of the first /Contents stream and rewrite
            # it. If /Contents is an array we collapse into the first
            # stream and clear the rest.
            contents_ref = doc.xref_get_key(page.xref, "Contents")
            if contents_ref[0] == "xref":
                xref = int(contents_ref[1].split()[0])
                doc.update_stream(xref, new_stream)
            elif contents_ref[0] == "array":
                # Collapse into first ref
                refs = [
                    int(piece.split()[0])
                    for piece in contents_ref[1].strip("[]").split("R")
                    if piece.strip()
                ]
                if not refs:
                    result.errors.append(
                        f"page {page_idx}: empty /Contents array"
                    )
                    result.pages_skipped += 1
                    continue
                doc.update_stream(refs[0], new_stream)
                for xref_clear in refs[1:]:
                    doc.update_stream(xref_clear, b"")
            else:
                result.errors.append(
                    f"page {page_idx}: unexpected /Contents type "
                    f"{contents_ref[0]}"
                )
                result.pages_skipped += 1
                continue

            result.pages_modified += 1
            result.artifact_wrappers_inserted += len(runs)

        doc.save(str(path), incremental=True, encryption=0)
    except Exception as exc:
        result.success = False
        result.errors.append(f"mark_untagged_content_as_artifact: {exc}")
    finally:
        if not doc.is_closed:
            doc.close()

    return result


def _apply_artifact_wrappers(
    tokens: list["Token"],
    runs: list[tuple[int, int]],
) -> bytes:
    """Reassemble the token list with /Artifact BDC/EMC wrappers
    inserted around each run.
    """
    if not runs:
        return _reassemble_stream(tokens)

    # Insert in reverse so earlier indices remain valid while we edit.
    # But _reassemble_stream walks the list once, so simplest is to
    # build a new list with markers interleaved.
    bdc_open = Token(raw=b"/Artifact BDC\n", op="BDC")
    emc_close = Token(raw=b"EMC\n", op="EMC")

    result: list[Token] = []
    run_dict = {start: end for start, end in runs}
    end_set = {end for _, end in runs}
    i = 0
    while i < len(tokens):
        if i in run_dict:
            result.append(bdc_open)
            result.append(tokens[i])
            if i in end_set:
                result.append(emc_close)
            i += 1
        elif i - 1 in end_set and any(start <= i - 1 <= end for start, end in runs if start != i - 1):
            # Already handled by end_set at previous index
            result.append(tokens[i])
            i += 1
        else:
            result.append(tokens[i])
            if i in end_set and i not in run_dict:
                # End of a multi-token run (run_start < run_end); close
                result.append(emc_close)
            i += 1

    return _reassemble_stream(result)
```

- [ ] **Step 4: Run tests and iterate**

```
python3 -m pytest tests/test_pdf_writer.py::TestMarkUntaggedContent -v
```

Expected: 3 passed. The `_apply_artifact_wrappers` logic is tricky for multi-token runs; if tests fail, inspect the reassembled bytes and simplify the algorithm.

A known-good reference simplification if the above is buggy: build a set of `(start, end)` runs, then iterate tokens with a single loop that emits BDC before entering any run and EMC after leaving it, tracking an `in_run` flag.

- [ ] **Step 5: Commit once green**

```
git add src/tools/pdf_writer.py tests/test_pdf_writer.py
git commit -m "Add mark_untagged_content_as_artifact() — Track A core"
```

---

## Task 7: Track A — real benchmark PDF smoke test

**Files:**
- Modify: `tests/test_pdf_writer.py` (append to TestMarkUntaggedContent)

- [ ] **Step 1: Write the test**

Append to `TestMarkUntaggedContent`:

```python
    def test_on_real_benchmark_pdf(self, tmp_path):
        """Run on a real remediated benchmark PDF and verify:
        - text extraction is unchanged
        - veraPDF 7.1-3 failed checks decrease significantly
        - no new rule types appear
        """
        import shutil
        import fitz
        from src.tools.pdf_writer import mark_untagged_content_as_artifact
        from src.tools.verapdf_checker import check_pdf_ua

        src = Path(
            "/tmp/remediation_bench_full/"
            "semantic_tagging_passed_W2895738059/"
            "W2895738059_remediated.pdf"
        )
        if not src.exists():
            pytest.skip(f"Benchmark PDF not available: {src}")

        dst = tmp_path / "smoke.pdf"
        shutil.copy(src, dst)

        # Baseline text extraction
        doc = fitz.open(str(dst))
        baseline_text = [p.get_text() for p in doc]
        doc.close()

        # Baseline verapdf
        baseline_vera = check_pdf_ua(str(dst))
        assert baseline_vera.success

        # Apply Track A
        result = mark_untagged_content_as_artifact(dst)
        assert result.success, result.errors

        # Text must match byte-for-byte
        doc = fitz.open(str(dst))
        post_text = [p.get_text() for p in doc]
        doc.close()
        assert post_text == baseline_text, "Text extraction changed after Track A"

        # veraPDF 7.1-3 checks should decrease
        post_vera = check_pdf_ua(str(dst))
        assert post_vera.success
        # Total failed checks should drop
        assert post_vera.violation_count < baseline_vera.violation_count, (
            f"violations did not decrease: {baseline_vera.violation_count} "
            f"→ {post_vera.violation_count}"
        )
```

- [ ] **Step 2: Run the test**

```
python3 -m pytest tests/test_pdf_writer.py::TestMarkUntaggedContent::test_on_real_benchmark_pdf -v
```

Expected: PASS (or SKIP if the benchmark file is not in /tmp).

If it fails with a text-extraction mismatch, investigate: fitz's `get_text()` should not be affected by /Artifact wrapping (fitz extracts from the content stream regardless of BDC markers), so a mismatch indicates our wrapping corrupted the content stream order. Inspect the written bytes, identify the corruption, fix the walker.

If it fails with "violations did not decrease," inspect which rules were targeted and whether the walker actually emitted /Artifact BDCs — the stream may have been written but with the wrong tokens.

- [ ] **Step 3: Commit once green**

```
git add tests/test_pdf_writer.py
git commit -m "Add real-benchmark-PDF smoke test for Track A"
```

---

## Task 8: Orchestration script — `scripts/apply_ua_fixes.py`

**Files:**
- Create: `scripts/apply_ua_fixes.py`

- [ ] **Step 1: Write the orchestration script**

Create `scripts/apply_ua_fixes.py`:

```python
"""Apply Track C + Track A post-processing to remediated benchmark PDFs.

Reads a ``remediation_benchmark_results.json`` produced by
``scripts/remediation_benchmark.py``, iterates every successful doc,
applies:

  1. ``apply_pdf_ua_metadata()`` (Track C: XMP pdfuaid, DisplayDocTitle, /Metadata)
  2. ``mark_untagged_content_as_artifact()`` (Track A: /Artifact BDC for rule 7.1-3)

with per-track verification gates. Writes an enriched results file
``ua_fixes_results.json`` plus a markdown report.

Usage:
    python3 scripts/apply_ua_fixes.py --results-dir /tmp/remediation_bench_full
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz

from src.tools.pdf_writer import (
    apply_pdf_ua_metadata,
    mark_untagged_content_as_artifact,
)
from src.tools.verapdf_checker import check_pdf_ua

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("apply_ua_fixes")
logger.setLevel(logging.INFO)


def _text_snapshot(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        return [p.get_text() for p in doc]
    finally:
        doc.close()


def _vera_snapshot(pdf_path: str) -> dict:
    r = check_pdf_ua(pdf_path)
    return {
        "success": r.success,
        "compliant": r.compliant,
        "failed_rules": r.failed_rules,
        "passed_rules": r.passed_rules,
        "violation_count": r.violation_count,
        "rule_ids": sorted({v.rule_id for v in r.violations}),
    }


def _process_one(doc_info: dict, work_dir: Path) -> dict:
    """Apply both tracks to one doc with verification + blame attribution."""
    out_path = doc_info.get("output_path")
    if not out_path or not Path(out_path).exists():
        return {**doc_info, "ua_fix_status": "missing_output"}

    pdf_path = Path(out_path)
    backup = work_dir / f"{pdf_path.stem}.pre_ua_fix.pdf"
    post_c_backup = work_dir / f"{pdf_path.stem}.post_c.pdf"

    # Initial baseline
    shutil.copy(pdf_path, backup)
    baseline_text = _text_snapshot(str(pdf_path))
    baseline_vera = _vera_snapshot(str(pdf_path))

    record = {
        **doc_info,
        "ua_fix_status": "pending",
        "ua_fix_track_c": None,
        "ua_fix_track_a": None,
        "ua_vera_before": baseline_vera,
        "ua_vera_after": None,
    }

    # ── Track C ──
    try:
        c_result = apply_pdf_ua_metadata(pdf_path)
        record["ua_fix_track_c"] = {
            "success": c_result.success,
            "changes": c_result.changes,
            "error": c_result.error,
        }
        if not c_result.success:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_failed"
            return record

        # Verify text unchanged
        post_c_text = _text_snapshot(str(pdf_path))
        if post_c_text != baseline_text:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_text_mismatch"
            return record

        # Verify no new rules appeared
        post_c_vera = _vera_snapshot(str(pdf_path))
        new_rules = set(post_c_vera["rule_ids"]) - set(baseline_vera["rule_ids"])
        if new_rules:
            shutil.copy(backup, pdf_path)
            record["ua_fix_status"] = "track_c_new_rules"
            record["new_rules"] = sorted(new_rules)
            return record

        shutil.copy(pdf_path, post_c_backup)
    except Exception as exc:
        shutil.copy(backup, pdf_path)
        record["ua_fix_status"] = f"track_c_exception:{exc}"
        record["traceback"] = traceback.format_exc(limit=5)
        return record

    # ── Track A ──
    try:
        a_result = mark_untagged_content_as_artifact(pdf_path)
        record["ua_fix_track_a"] = {
            "success": a_result.success,
            "pages_modified": a_result.pages_modified,
            "artifact_wrappers_inserted": a_result.artifact_wrappers_inserted,
            "pages_skipped": a_result.pages_skipped,
            "errors": a_result.errors,
        }
        if not a_result.success:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_failed_kept_c"
            return record

        # Verify text unchanged (compared to ORIGINAL baseline)
        post_a_text = _text_snapshot(str(pdf_path))
        if post_a_text != baseline_text:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_text_mismatch_kept_c"
            return record

        # Verify no new rules
        post_a_vera = _vera_snapshot(str(pdf_path))
        new_rules = set(post_a_vera["rule_ids"]) - set(baseline_vera["rule_ids"])
        if new_rules:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_new_rules_kept_c"
            record["new_rules"] = sorted(new_rules)
            return record

        # Verify total checks decreased
        if post_a_vera["violation_count"] >= baseline_vera["violation_count"]:
            shutil.copy(post_c_backup, pdf_path)
            record["ua_fix_status"] = "track_a_no_improvement_kept_c"
            return record

        record["ua_vera_after"] = post_a_vera
        record["ua_fix_status"] = "success"
        return record
    except Exception as exc:
        shutil.copy(post_c_backup, pdf_path)
        record["ua_fix_status"] = f"track_a_exception_kept_c:{exc}"
        record["traceback"] = traceback.format_exc(limit=5)
        return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument(
        "--json-name", default="remediation_benchmark_results.json"
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    input_json = args.results_dir / args.json_name
    if not input_json.exists():
        print(f"ERROR: {input_json} not found", file=sys.stderr)
        sys.exit(1)

    data = json.loads(input_json.read_text())
    if args.limit:
        data = data[: args.limit]

    work_dir = args.results_dir / "ua_fixes_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    out_json = args.results_dir / "ua_fixes_results.json"
    results: list[dict] = []

    # Resume support: if out_json exists, load already-processed records
    # and skip them.
    done_ids: set[str] = set()
    if out_json.exists():
        try:
            results = json.loads(out_json.read_text())
            done_ids = {r.get("openalex_id", "") + "|" + r.get("task", "") + "|" + r.get("label", "") for r in results if r.get("ua_fix_status")}
            print(f"Resuming: {len(done_ids)} already processed")
        except Exception:
            results = []
            done_ids = set()

    start = time.time()
    for i, doc_info in enumerate(data, 1):
        key = doc_info.get("openalex_id", "") + "|" + doc_info.get("task", "") + "|" + doc_info.get("label", "")
        if key in done_ids:
            continue
        print(f"[{i}/{len(data)}] {doc_info.get('task')}/{doc_info.get('label')}/{doc_info.get('openalex_id')}")
        rec = _process_one(doc_info, work_dir)
        results.append(rec)
        status = rec["ua_fix_status"]
        vc_before = rec["ua_vera_before"].get("violation_count", "?")
        vc_after = (rec.get("ua_vera_after") or {}).get("violation_count", "-")
        print(f"    {status}: violations {vc_before} → {vc_after}")

        # Atomic rewrite of the results file so we can resume.
        tmp = out_json.with_suffix(".tmp")
        tmp.write_text(json.dumps(results, indent=2, default=str))
        tmp.replace(out_json)

    elapsed = time.time() - start

    # Markdown report
    report = args.results_dir / "ua_fixes_results.md"
    successes = [r for r in results if r["ua_fix_status"] == "success"]
    lines: list[str] = []
    lines.append("# PDF/UA Post-Processing Results")
    lines.append("")
    lines.append(f"- Total docs: {len(results)}")
    lines.append(f"- Full success (both tracks applied): {len(successes)}")

    statuses: dict[str, int] = {}
    for r in results:
        statuses[r["ua_fix_status"]] = statuses.get(r["ua_fix_status"], 0) + 1
    for status, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {status}: {n}")

    total_vc_before = sum(r["ua_vera_before"].get("violation_count", 0) for r in results)
    total_vc_after = sum(
        (r.get("ua_vera_after") or r["ua_vera_before"]).get("violation_count", 0)
        for r in results
    )
    lines.append("")
    lines.append(f"- **Total failed checks before:** {total_vc_before}")
    lines.append(f"- **Total failed checks after:** {total_vc_after}")
    lines.append(f"- **Reduction:** {total_vc_before - total_vc_after}")
    if total_vc_before:
        lines.append(f"- **% reduction:** {(total_vc_before - total_vc_after) / total_vc_before:.1%}")
    lines.append("")
    lines.append(f"Wall time: {elapsed:.0f}s")

    report.write_text("\n".join(lines))
    print(f"\nReport: {report}")
    print(f"JSON:   {out_json}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test on 2 docs**

```
python3 scripts/apply_ua_fixes.py --results-dir /tmp/remediation_bench_full --limit 2
```

Expected output: per-doc status line showing "success" (or a specific reason for revert). Verify the resulting `ua_fixes_results.md` exists and shows the per-status counts.

- [ ] **Step 3: Commit**

```
git add scripts/apply_ua_fixes.py
git commit -m "Add scripts/apply_ua_fixes.py orchestration with per-track verification gates"
```

---

## Task 9: Run Track C + A on all 125 benchmark outputs

**Files:** none (execution only, writes artifacts under `/tmp/remediation_bench_full`)

- [ ] **Step 1: Reset the 125 outputs from the backup source PDFs**

Because previous smoke tests may have modified some docs, restore from the original remediation artifacts. If `/tmp/remediation_bench_full/` has been modified since the last commit, re-run the remediation benchmark OR restore from a backup you made before starting. **If in doubt, skip this task and use the existing modified outputs** — the verification gates are designed to handle already-processed PDFs (idempotent Track C + Track A).

- [ ] **Step 2: Run the orchestration on all 125**

```
python3 scripts/apply_ua_fixes.py --results-dir /tmp/remediation_bench_full
```

Expected runtime: ~25-40 min (125 docs × verapdf calls × both tracks).

Expected outcome: `ua_fixes_results.md` showing per-status counts and aggregate violation-check reduction. Track C should succeed on ~125 docs. Track A should succeed on the majority; any reverts will be categorised by status (text_mismatch, new_rules, no_improvement).

- [ ] **Step 3: Inspect the results report**

```
cat /tmp/remediation_bench_full/ua_fixes_results.md
```

Verify:
- At least 100 of 125 docs have status `success`
- Violation-check total dropped substantially (expect >50% reduction based on spec estimates)
- If too many reverts: identify the common status and return to the relevant test task to fix the walker.

- [ ] **Step 4: Commit results artifacts into the repo**

```
cp /tmp/remediation_bench_full/ua_fixes_results.md docs/benchmark_results/
cp /tmp/remediation_bench_full/ua_fixes_results.json docs/benchmark_results/
git add docs/benchmark_results/ua_fixes_results.md docs/benchmark_results/ua_fixes_results.json
git commit -m "Full 125-doc UA post-processing results"
```

---

## Task 10: Executor integration

**Files:**
- Modify: `src/agent/executor.py`

- [ ] **Step 1: Read the current execute_pdf function structure**

```
grep -n "def execute_pdf\|apply_contrast_fixes_to_pdf\|repair_broken_uris_in_pdf" src/agent/executor.py
```

You need to know where to insert the new calls. They should go **after** `apply_contrast_fixes_to_pdf` and `repair_broken_uris_in_pdf` (already in the file from commit `d4d116b`), so the order becomes:

  1. iText tagging
  2. contrast fixes
  3. URI repair (from commit d4d116b)
  4. **Track C: apply_pdf_ua_metadata()** — NEW
  5. **Track A: mark_untagged_content_as_artifact()** — NEW

- [ ] **Step 2: Add imports**

Update the existing `from src.tools.pdf_writer import ...` line at the top of `executor.py` to include the two new functions:

```python
from src.tools.pdf_writer import (
    apply_contrast_fixes_to_pdf,
    apply_pdf_fixes,
    apply_pdf_ua_metadata,                  # NEW
    mark_untagged_content_as_artifact,      # NEW
    repair_broken_uris_in_pdf,
    strip_struct_tree,
    update_existing_figure_alt_texts,
)
```

- [ ] **Step 3: Add the two calls after URI repair**

Locate the existing block (inserted in commit `d4d116b`) that looks like:

```python
            try:
                n_fixed, uri_repairs = repair_broken_uris_in_pdf(tagged_pdf_path)
                if n_fixed:
                    logger.info(
                        "Repaired %d broken link URI(s) in %s",
                        n_fixed, tagged_pdf_path,
                    )
                    for before, after in uri_repairs[:5]:
                        logger.info("  %r → %r", before[:80], after[:80])
            except Exception as exc:
                logger.warning("URI repair pass failed: %s", exc)
```

Insert immediately after it:

```python
            # PDF/UA Track C — XMP metadata, DisplayDocTitle, /Metadata key
            try:
                meta_result = apply_pdf_ua_metadata(tagged_pdf_path)
                if meta_result.success and meta_result.changes:
                    logger.info(
                        "PDF/UA metadata fixes applied to %s: %s",
                        tagged_pdf_path, ", ".join(meta_result.changes),
                    )
                elif not meta_result.success:
                    logger.warning(
                        "PDF/UA metadata fixes failed: %s", meta_result.error
                    )
            except Exception as exc:
                logger.warning("PDF/UA metadata pass failed: %s", exc)

            # PDF/UA Track A — mark untagged content as /Artifact
            try:
                artifact_result = mark_untagged_content_as_artifact(tagged_pdf_path)
                if artifact_result.success:
                    if artifact_result.artifact_wrappers_inserted:
                        logger.info(
                            "Artifact-wrapped %d untagged content run(s) "
                            "across %d page(s) in %s",
                            artifact_result.artifact_wrappers_inserted,
                            artifact_result.pages_modified,
                            tagged_pdf_path,
                        )
                else:
                    logger.warning(
                        "Artifact marking failed: %s",
                        "; ".join(artifact_result.errors),
                    )
            except Exception as exc:
                logger.warning("Artifact marking pass failed: %s", exc)
```

- [ ] **Step 4: Run the full test suite to verify nothing broke**

```
python3 -m pytest tests/ -q --ignore=tests/test_docs -x
```

Expected: all tests pass. Count should be >= 938 (baseline from commit `a7b139b`) plus however many new tests we added in Tasks 1-7.

- [ ] **Step 5: End-to-end smoke test on 5 docs**

```
mkdir -p /tmp/remediation_bench_executor_smoke
python3 scripts/remediation_benchmark.py \
    --benchmark-dir /tmp/PDF-Accessibility-Benchmark \
    --output-dir /tmp/remediation_bench_executor_smoke \
    --limit 5
```

Expected: 5/5 success, log messages show Track C and Track A being applied ("PDF/UA metadata fixes applied", "Artifact-wrapped N untagged content run(s)").

- [ ] **Step 6: Verify the 5 outputs pass veraPDF with improved numbers**

```
python3 scripts/verapdf_postprocess.py --results-dir /tmp/remediation_bench_executor_smoke
cat /tmp/remediation_bench_executor_smoke/remediation_benchmark_results_with_verapdf.md
```

Expected: failed-rule and violation counts are substantially lower than the pre-commit smoke test from earlier in the session (which showed 1076 → 672 for the full 125; the 5-doc smoke should show a similar directional drop).

- [ ] **Step 7: Commit**

```
git add src/agent/executor.py
git commit -m "Wire PDF/UA Track C and Track A into execute_pdf() after iText tagging"
```

---

## Task 11: Update NOW.md with headline results

**Files:**
- Modify: `NOW.md`

- [ ] **Step 1: Read current NOW.md and locate the benchmark-status section**

```
grep -n "Full remediation benchmark" NOW.md
```

- [ ] **Step 2: Update NOW.md with new numbers**

Add a new section under the existing "Full remediation benchmark results" section with the headline from `docs/benchmark_results/ua_fixes_results.md`. The format should match the existing section style. Include:
- Absolute before/after violation counts
- Percentage reduction
- Count of docs that fully passed both tracks
- Count of docs that reverted, by status
- Fully compliant doc count (if any non-zero)

- [ ] **Step 3: Commit**

```
git add NOW.md
git commit -m "NOW.md: record PDF/UA post-processing results"
```

---

## Self-Review Checklist

Before marking this plan complete, verify:

1. **Spec coverage:**
   - Track C (XMP, DisplayDocTitle, Metadata): Tasks 1-3 ✓
   - Track A (content-stream walker): Tasks 4-7 ✓
   - Orchestration + resumability: Task 8 ✓
   - Full 125-doc run: Task 9 ✓
   - Executor integration: Task 10 ✓
   - NOW.md update: Task 11 ✓
   - Unit tests per spec §Testing: Tasks 1-7 all include named tests matching the spec ✓
   - Verification gates (A/B/C): Gates A and B implemented in the orchestration script (Task 8). Gate C (visual diff) is NOT implemented in this plan — it's deferred as a "final commit safety check" that can be added if Gate B reveals concerns. Spec §Gate C acknowledges this is optional. ✓

2. **Placeholder scan:** No "TODO" or "implement later" markers in the plan. Every step has concrete code or commands.

3. **Type consistency:**
   - `MetadataResult` and `ArtifactMarkingResult` defined in Tasks 2 and 6 respectively, imported/used consistently in Task 8 and Task 10.
   - `_find_untagged_content_runs` returns `list[tuple[int, int]]`, consumed the same way in `_apply_artifact_wrappers` and `mark_untagged_content_as_artifact`.
   - `apply_pdf_ua_metadata` takes `pdf_path: str | Path` consistently in signature, test, and orchestrator use.

4. **Gate C (visual diff):** Explicitly noted as deferred in self-review item 1. If you want it, add after Task 10 as: open each remediated PDF, render pre/post via `_render_page`, call `_pixel_diff`, assert < 0.5% — but note this is already covered by the spec's success criterion #5, which the existing text-extraction check (bytewise equality in orchestration) is a stronger proxy for on these non-visual edits.

5. **Run the test suite check in Task 10** — this is your end-to-end safety net.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
