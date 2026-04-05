"""Tests for OCR table deduplication in orchestrator."""

import pytest

from src.models.document import CellInfo, TableInfo
from src.agent.orchestrator import _deduplicate_ocr_tables


class TestDeduplicateOcrTables:
    def _make_table(self, id: str, cells: list[list[str]], page: int = 0) -> TableInfo:
        rows = []
        for row_cells in cells:
            rows.append([CellInfo(text=c, paragraphs=[c]) for c in row_cells])
        return TableInfo(
            id=id,
            rows=rows,
            header_row_count=1,
            has_header_style=True,
            row_count=len(rows),
            col_count=max(len(r) for r in rows) if rows else 0,
            page_number=page,
        )

    def test_removes_exact_duplicate(self):
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"]])
        t2 = self._make_table("tbl_1", [["A", "B"], ["C", "D"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 1
        assert result[0].id == "tbl_0"

    def test_removes_near_duplicate(self):
        t1 = self._make_table("tbl_0", [["Theme", "Example"], ["Mind as system", "Like computer"]])
        t2 = self._make_table("tbl_1", [["Theme", "Example"], ["Mind as system", "Like computer"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 1

    def test_keeps_distinct_tables(self):
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"]])
        t2 = self._make_table("tbl_1", [["X", "Y"], ["Z", "W"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 2

    def test_keeps_single_table(self):
        t1 = self._make_table("tbl_0", [["A", "B"]])
        result = _deduplicate_ocr_tables([t1])
        assert len(result) == 1

    def test_empty_list(self):
        result = _deduplicate_ocr_tables([])
        assert result == []

    def test_partial_overlap_kept(self):
        t1 = self._make_table("tbl_0", [["A", "B"], ["C", "D"], ["E", "F"]])
        t2 = self._make_table("tbl_1", [["A", "B"], ["X", "Y"], ["Z", "W"]])
        result = _deduplicate_ocr_tables([t1, t2])
        assert len(result) == 2
