"""Resilient Excel reading: openpyxl primary, calamine fallback.

Real-world LG masters sometimes contain malformed XML in defined names (e.g.
``#N/A`` as a print-titles definition) that openpyxl refuses to parse. Falling
back to ``python-calamine`` (Rust-backed) gets the rows regardless and gives
the rest of the pipeline a single uniform interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl


@dataclass
class SheetData:
    """All rows of one sheet, plus its observed dimensions."""

    name: str
    rows: list[list[Any]]

    @property
    def max_row(self) -> int:
        return len(self.rows)

    @property
    def max_col(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def _strip_leading_empty(rows: list[list[Any]]) -> list[list[Any]]:
    """Drop fully-empty leading rows so openpyxl matches calamine's compact view."""
    while rows and all(c is None or c == "" for c in rows[0]):
        rows.pop(0)
    return rows


def _openpyxl_sheets(path: Path) -> list[SheetData]:
    """Read every sheet via openpyxl (may raise on malformed workbooks)."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets: list[SheetData] = []
        for sheet in workbook.worksheets:
            rows: list[list[Any]] = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
            sheets.append(
                SheetData(name=sheet.title, rows=_strip_leading_empty(rows))
            )
        return sheets
    finally:
        workbook.close()


def _calamine_sheets(path: Path) -> list[SheetData]:
    """Read every sheet via python-calamine (used as fallback)."""
    from python_calamine import CalamineWorkbook

    workbook = CalamineWorkbook.from_path(path)
    return [
        SheetData(
            name=name,
            rows=_strip_leading_empty(
                workbook.get_sheet_by_name(name).to_python()
            ),
        )
        for name in workbook.sheet_names
    ]


def read_workbook(path: Path) -> list[SheetData]:
    """Return every sheet of ``path`` as :class:`SheetData`.

    Tries openpyxl first; on any failure (e.g. malformed defined names) falls
    back to calamine. The two readers are kept independent so a future env
    without ``python-calamine`` still works on healthy workbooks.

    Args:
        path: Path to the Excel file.

    Returns:
        One :class:`SheetData` per worksheet, in workbook order.
    """
    try:
        return _openpyxl_sheets(path)
    except Exception:  # noqa: BLE001 — broad, both readers expected
        return _calamine_sheets(path)
