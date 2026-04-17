"""Probe a PDF's structure tree (StructTreeRoot) for aggregated accessibility facts.

Returns tag types and counts, presence of proper headings, table headers (TH),
figure alt text, link annotations, etc. This is independent of our parser — it
reads the raw struct tree directly via PyMuPDF xref operations.

Used by:
- scripts/benchmark.py — detection-task features
- src/agent/comprehension.py — skip vision pass for figures that already have
  good /Alt (saves Gemini API cost on already-accessible PDFs)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz

# Standard PDF/UA tag types we recognize as "good" semantic tagging
STANDARD_TAGS = {
    "Document", "Part", "Art", "Article", "Sect", "Div",
    "P", "BlockQuote", "Caption", "TOC", "TOCI", "Index",
    "L", "LI", "Lbl", "LBody",
    "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot",
    "H1", "H2", "H3", "H4", "H5", "H6", "H",
    "Span", "Quote", "Note", "Reference", "BibEntry", "Code",
    "Link", "Annot", "Form", "Formula",
    "Figure", "Artifact",
}


@dataclass
class StructFacts:
    has_struct_tree: bool = False
    is_marked: bool = False
    tag_counts: dict = field(default_factory=dict)
    custom_tag_counts: dict = field(default_factory=dict)
    figure_count: int = 0
    figures_with_alt: int = 0
    figures_with_actual_text: int = 0
    figure_alt_texts: list = field(default_factory=list)  # actual alt text strings
    table_count: int = 0
    tables_with_th: int = 0
    table_th_count: int = 0
    heading_count: int = 0
    heading_levels: list = field(default_factory=list)
    link_count: int = 0
    p_count: int = 0
    span_count: int = 0
    list_count: int = 0
    # PDF link annotations (independent of struct tree)
    annot_link_count: int = 0
    annot_links_with_valid_uri: int = 0
    annot_links_with_alt: int = 0  # link annotations with /Contents
    annot_links_with_struct_parent: int = 0  # link annotations tied to struct tree
    error: str = ""

    @property
    def total_tagged_elements(self) -> int:
        return sum(self.tag_counts.values()) + sum(self.custom_tag_counts.values())

    @property
    def custom_tag_ratio(self) -> float:
        total = self.total_tagged_elements
        if total == 0:
            return 0.0
        return sum(self.custom_tag_counts.values()) / total


def _get_obj_text(doc, xref: int) -> str:
    try:
        return doc.xref_object(xref) or ""
    except Exception:
        return ""


def _decode_utf16be_hex(hex_str: str) -> str:
    """Decode a UTF-16 BE hex string (with optional FEFF BOM)."""
    try:
        # Strip whitespace and any leading FEFF BOM marker
        hex_str = hex_str.replace(" ", "").replace("\n", "")
        if hex_str.upper().startswith("FEFF"):
            hex_str = hex_str[4:]
        if len(hex_str) % 4 != 0:
            return ""
        bytes_data = bytes.fromhex(hex_str)
        return bytes_data.decode("utf-16-be", errors="replace").rstrip("\x00").strip()
    except Exception:
        return ""


def _get_alt_attribute(doc, xref: int, obj_text: str) -> str:
    """Extract /Alt or /ActualText from a struct element. Returns empty if absent.

    Handles both:
    - Plain string literals: /Alt (some text)
    - UTF-16 BE hex strings: /Alt <FEFF0041006C0074>
    - Indirect references: /Alt 42 0 R
    """
    # Inline /Alt as plain string
    m = re.search(r"/Alt\s*\(([^)]*)\)", obj_text)
    if m:
        return m.group(1)
    # Inline /Alt as hex string
    m = re.search(r"/Alt\s*<([0-9A-Fa-f\s]+)>", obj_text)
    if m:
        decoded = _decode_utf16be_hex(m.group(1))
        if decoded:
            return decoded
        return m.group(1)[:200]  # raw hex as fallback signal
    # /Alt indirect reference to a string object
    m = re.search(r"/Alt\s+(\d+)\s+0\s+R", obj_text)
    if m:
        ref_text = _get_obj_text(doc, int(m.group(1)))
        m2 = re.search(r"\(([^)]*)\)", ref_text)
        if m2:
            return m2.group(1)
        m2 = re.search(r"<([0-9A-Fa-f\s]+)>", ref_text)
        if m2:
            decoded = _decode_utf16be_hex(m2.group(1))
            if decoded:
                return decoded
    # /ActualText (same patterns)
    m = re.search(r"/ActualText\s*\(([^)]*)\)", obj_text)
    if m:
        return m.group(1)
    m = re.search(r"/ActualText\s*<([0-9A-Fa-f\s]+)>", obj_text)
    if m:
        decoded = _decode_utf16be_hex(m.group(1))
        if decoded:
            return decoded
    return ""


def _walk(doc, xref: int, facts: StructFacts, depth: int = 0, seen: set | None = None) -> None:
    if seen is None:
        seen = set()
    if xref in seen or depth > 200:
        return
    seen.add(xref)

    obj_text = _get_obj_text(doc, xref)
    if not obj_text:
        return

    # Get tag type from /S /TagName
    tag_match = re.search(r"/S\s*/([A-Za-z_][A-Za-z0-9_]*)", obj_text)
    tag = None
    if tag_match:
        tag = tag_match.group(1)

        if tag in STANDARD_TAGS:
            facts.tag_counts[tag] = facts.tag_counts.get(tag, 0) + 1
        else:
            facts.custom_tag_counts[tag] = facts.custom_tag_counts.get(tag, 0) + 1

        # Specific counters
        if tag == "Figure":
            facts.figure_count += 1
            alt = _get_alt_attribute(doc, xref, obj_text)
            if alt:
                facts.figures_with_alt += 1
                facts.figure_alt_texts.append(alt)
            if "/ActualText" in obj_text:
                facts.figures_with_actual_text += 1
        elif tag == "Table":
            facts.table_count += 1
        elif tag == "TH":
            facts.table_th_count += 1
        elif tag == "Link":
            facts.link_count += 1
        elif tag == "P":
            facts.p_count += 1
        elif tag == "Span":
            facts.span_count += 1
        elif tag == "L":
            facts.list_count += 1
        elif tag in ("H1", "H2", "H3", "H4", "H5", "H6", "H"):
            facts.heading_count += 1
            level = int(tag[1:]) if len(tag) > 1 and tag[1:].isdigit() else 0
            facts.heading_levels.append(level)

    # Recurse via /K children
    # /K can be: a single int (MCID), an array of children, or a single ref
    # We care about the references which point to other struct elements
    k_pos = obj_text.find("/K")
    if k_pos != -1:
        # Look for a single ref `/K N 0 R` or array `/K [ ... ]`
        single_ref_match = re.search(r"/K\s+(\d+)\s+0\s+R", obj_text)
        if single_ref_match:
            _walk(doc, int(single_ref_match.group(1)), facts, depth + 1, seen)
        else:
            arr_match = re.search(r"/K\s*\[([^\]]*)\]", obj_text, re.DOTALL)
            if arr_match:
                inner = arr_match.group(1)
                for ref_match in re.finditer(r"(\d+)\s+0\s+R", inner):
                    _walk(doc, int(ref_match.group(1)), facts, depth + 1, seen)


def probe_struct_tree(pdf_path: str) -> StructFacts:
    """Walk a PDF's structure tree and return aggregated facts."""
    facts = StructFacts()
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        facts.error = f"Failed to open: {e}"
        return facts

    try:
        if not doc.is_pdf:
            facts.error = "Not a PDF"
            return facts

        cat = doc.pdf_catalog()

        # MarkInfo present?
        mark_info = doc.xref_get_key(cat, "MarkInfo")
        facts.is_marked = mark_info[0] != "null"

        # StructTreeRoot present?
        struct_tree = doc.xref_get_key(cat, "StructTreeRoot")
        if struct_tree[0] != "xref":
            facts.has_struct_tree = False
            return facts

        facts.has_struct_tree = True
        root_xref = int(struct_tree[1].split()[0])

        # Walk the tree
        _walk(doc, root_xref, facts)

        # Count tables that have at least one TH child anywhere
        # (We can't directly link THs to parent tables without more graph
        # walking, so we use a global presence: if a doc has tables AND THs,
        # we count it as "tables_with_th")
        if facts.table_count > 0 and facts.table_th_count > 0:
            facts.tables_with_th = facts.table_count  # all tables get credit

    finally:
        # Always probe link annotations (independent of struct tree)
        try:
            for page in doc:
                for link in page.links():
                    facts.annot_link_count += 1
                    uri = link.get("uri", "") or link.get("URI", "")
                    if uri and (uri.startswith("http") or uri.startswith("mailto:")):
                        facts.annot_links_with_valid_uri += 1
                    # Check raw object for /StructParent (links tagged to struct tree)
                    xref = link.get("xref")
                    if xref:
                        try:
                            obj_text = doc.xref_object(xref) or ""
                            if "/StructParent" in obj_text:
                                facts.annot_links_with_struct_parent += 1
                            if "/Contents" in obj_text or "/Alt" in obj_text:
                                facts.annot_links_with_alt += 1
                        except Exception:
                            pass
        except Exception:
            pass
        doc.close()

    return facts


def per_table_th_counts(pdf_path: str) -> list[int]:
    """Walk the struct tree and return the TH count for each Table element.

    Aggregate TH count can hide a malformed table that has zero headers when
    other tables in the same doc have plenty. A single empty ``/Table`` is a
    strong signal that one specific table is broken and needs remediation.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []
    try:
        cat = doc.pdf_catalog()
        st = doc.xref_get_key(cat, "StructTreeRoot")
        if st[0] != "xref":
            return []
        root = int(st[1].split()[0])
    except Exception:
        doc.close()
        return []

    tables: list[dict] = []

    def walk(xref: int, cur_table: dict | None, seen: set, depth: int) -> None:
        if xref in seen or depth > 300:
            return
        seen.add(xref)
        obj = _get_obj_text(doc, xref)
        if not obj:
            return
        m = re.search(r"/S\s*/([A-Za-z_][A-Za-z0-9_]*)", obj)
        tag = m.group(1) if m else None
        new_table = cur_table
        if tag == "Table":
            new_table = {"th": 0, "td": 0, "tr": 0}
            tables.append(new_table)
        elif cur_table is not None and tag in ("TH", "TD", "TR"):
            cur_table[tag.lower()] += 1
        single = re.search(r"/K\s+(\d+)\s+0\s+R", obj)
        if single:
            walk(int(single.group(1)), new_table, seen, depth + 1)
        else:
            arr = re.search(r"/K\s*\[([^\]]*)\]", obj, re.DOTALL)
            if arr:
                for rm in re.finditer(r"(\d+)\s+0\s+R", arr.group(1)):
                    walk(int(rm.group(1)), new_table, seen, depth + 1)

    try:
        walk(root, None, set(), 0)
    finally:
        doc.close()
    return [t["th"] for t in tables]
