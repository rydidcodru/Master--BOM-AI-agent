"""v2.0 §8-6 — BOM ag-grid (36col) 어댑터.

D-012 변경:
  - 이전: (parts, bom_edges) 분리 출력 → bom_edges 테이블 별도 적재.
  - 신규: ExtractedRow 단일 스트림 — 한 부품 한 row. bom_edges 테이블이 사라졌으므로
    parent_part_no/qty/level은 ExtractedRow.extra_fields에 보존.
  - form_id="bom_ag_grid_36" (form_registry seed와 일치).

특이점: event 컬럼은 None (BOM 부품은 변경 이벤트가 아님). retrieve 단에서는
form_id로 필터링해 BOM부품 vs 변경부품을 구분할 수 있다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    cell_at,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

HEADER_ROW = 1
DATA_START_ROW = 2
FORM_ID = "bom_ag_grid_36"

PART_NO_KEYS = ("P/No", "P/No.", "Part No", "PartNo", "PartNumber")
DESC_KEYS = ("Desc.", "Desc", "Description", "품명", "부품명", "Part Name(자)", "Part Name")
PARENT_KEYS = (
    "부모 P/No", "Parent P/No", "ParentPartNo", "상위 P/No",
    "Parent Part No(모)", "Parent Part No",
)
LEVEL_KEYS = ("Lvl", "Level", "BOM Level", "Lv")
QTY_KEYS = ("Qty", "수량", "Quantity")
MODEL_KEYS = ("모델", "Model", "Model Code", "ModelCode")
CHANGE_IN_KEYS = ("Change In", "ChangeIn", "투입일")
CHANGE_OUT_KEYS = ("Change Out", "ChangeOut", "탈락일")
PART_TYPE_KEYS = ("MechanicalPart", "Part Type", "부품 유형", "Type")


def _find_col(headers: dict[int, str], keys: tuple[str, ...]) -> int | None:
    norm = {c: h.strip() for c, h in headers.items()}
    for c, h in norm.items():
        if h in keys:
            return c
    lower = {c: h.lower() for c, h in norm.items()}
    targets = [k.lower() for k in keys]
    for c, h in lower.items():
        if h in targets:
            return c
    return None


def _read_headers(sheet: SheetData) -> dict[int, str]:
    return {
        c: normalize_cell_text(cell_at(sheet.rows, HEADER_ROW, c))
        for c in range(1, sheet.max_col + 1)
        if normalize_cell_text(cell_at(sheet.rows, HEADER_ROW, c))
    }


def _model_from_filename(file_name: str) -> str | None:
    m = re.match(r"^([A-Z]{3,5}\d{3,5}[A-Z]?(?:\.[A-Z0-9.]+)?)", file_name)
    return m.group(1) if m else None


def _parse_level(raw: Any) -> int | None:
    """'.  .2' / '...3' / 2 / '2' → depth int."""
    if raw in (None, ""):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        pass
    s = str(raw).strip()
    prefix = s.lstrip(".")
    if prefix:
        try:
            return int(prefix)
        except ValueError:
            pass
    return s.count(".") or None


def extract_bom_ag_grid(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    """BOM ag-grid 시트 → ExtractedRow iterator (한 부품 한 row).

    bom_edges 테이블이 사라졌으므로 parent_part_no/qty/change_in/change_out은
    ExtractedRow.extra_fields에 보존된다.
    """
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}

    headers = _read_headers(sheet)
    if not headers:
        log.warning("adapter.bom.no_headers", file=file_path.name)
        return

    col_part = _find_col(headers, PART_NO_KEYS)
    col_desc = _find_col(headers, DESC_KEYS)
    col_parent = _find_col(headers, PARENT_KEYS)
    col_level = _find_col(headers, LEVEL_KEYS)
    col_qty = _find_col(headers, QTY_KEYS)
    col_model = _find_col(headers, MODEL_KEYS)
    col_change_in = _find_col(headers, CHANGE_IN_KEYS)
    col_change_out = _find_col(headers, CHANGE_OUT_KEYS)
    col_ptype = _find_col(headers, PART_TYPE_KEYS)

    file_model = _model_from_filename(file_path.stem)
    parent_stack: list[tuple[int, str]] = []  # (level, part_no)

    def _safe_cell(row: list[Any], col: int | None) -> Any:
        if col is None or col - 1 < 0 or col - 1 >= len(row):
            return None
        return row[col - 1]

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, DATA_START_ROW):
        if is_blank_row(row):
            continue
        part_no = normalize_cell_text(_safe_cell(row, col_part))
        if not part_no:
            continue

        desc = normalize_cell_text(_safe_cell(row, col_desc))
        level_raw = _safe_cell(row, col_level)
        level = _parse_level(level_raw)
        qty_raw = _safe_cell(row, col_qty)
        try:
            qty = float(qty_raw) if qty_raw not in (None, "") else None
        except (TypeError, ValueError):
            qty = None
        model = normalize_cell_text(_safe_cell(row, col_model)) if col_model else None
        if not model:
            model = file_model
        change_in = normalize_cell_text(_safe_cell(row, col_change_in)) if col_change_in else None
        change_out = normalize_cell_text(_safe_cell(row, col_change_out)) if col_change_out else None
        ptype = normalize_cell_text(_safe_cell(row, col_ptype)) if col_ptype else None

        # parent 결정
        parent: str | None = None
        if col_parent:
            parent = normalize_cell_text(_safe_cell(row, col_parent)) or None
        elif level is not None:
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()
            if parent_stack:
                parent = parent_stack[-1][1]
            parent_stack.append((level, part_no))

        # D-012: BOM 부품은 event 컬럼이 NULL인 것이 의미상 정상.
        # form_id="bom_ag_grid_36"으로 다른 변경/신규 양식과 구분.
        # 명시적 None을 둬서 ExtractedRow 스키마 일관성 보강 (L193 filter가
        # 어차피 거르므로 동작은 동일).
        dpm_fields: dict[str, Any] = {
            "part_no_new": part_no,
            "part_name": desc or part_no,
            "new_model": model,
            "event": None,
            "bom_depth": level,
            "bom_level_raw": str(level_raw) if level_raw not in (None, "") else None,
            "part_type": ptype,
            "qty_new": qty,
        }

        extra_fields: dict[str, Any] = {}
        if parent and parent != part_no:
            extra_fields["bom_parent_part_no"] = parent
        if change_in:
            extra_fields["bom_change_in"] = change_in
        if change_out:
            extra_fields["bom_change_out"] = change_out

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_id": FORM_ID,
            **file_meta,
        }

        yield ExtractedRow(
            dev_part_master_fields={k: v for k, v in dpm_fields.items() if v is not None},
            extra_fields=extra_fields,
            source_meta=source_meta,
        )
        rows_yielded += 1

    log.info(
        "adapter.bom.extracted",
        file=file_path.name,
        rows=rows_yielded,
    )
