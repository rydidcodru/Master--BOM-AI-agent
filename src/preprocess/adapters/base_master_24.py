"""v2.0 §8-4 — base_master (24col, 구버전) 어댑터.

D-012: ExtractedRow → dev_part_master_fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    build_extracted_row,
    cell_at,
    detect_header_by_dict,
    headers_from_single_row,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

HEADER_ROW = 1
DATA_START_ROW = 3
FORM_ID = "base_master_24"


def extract_base_master_24(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}

    # 실데이터(메타블록 아래 row~10 헤더) 대응 — column_dict 매칭 최대 행을 우선 탐색.
    detected = detect_header_by_dict(sheet, cdict)
    if detected is not None:
        header_row, data_start = detected
    else:
        # fallback: row 1~3 중 'No.' 가 첫 번째 등장한 행이 헤더(합성/구버전 호환).
        header_row = HEADER_ROW
        data_start = DATA_START_ROW
        for r in range(1, 4):
            first_cell = normalize_cell_text(cell_at(sheet.rows, r, 2))
            if first_cell in {"No.", "No", "no."}:
                header_row = r
                data_start = r + 2
                break

    headers = headers_from_single_row(sheet, header_row)

    if not headers:
        log.warning("adapter.base_master.no_headers", file=file_path.name, sheet=sheet.name)
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, data_start):
        if is_blank_row(row):
            continue
        core: dict[str, Any] = {}
        payload: dict[str, Any] = {}

        for col_idx in range(1, len(row) + 1):
            header = headers.get(col_idx)
            if not header:
                continue
            value = row[col_idx - 1]
            payload[header] = value
            core_field = cdict.lookup(header)
            if core_field and value not in (None, ""):
                core[core_field] = cdict.map_cell_value(core_field, value)

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_id": FORM_ID,
            **file_meta,
        }
        yield build_extracted_row(core, payload, source_meta, cdict)
        rows_yielded += 1

    log.info("adapter.base_master.extracted", file=file_path.name, rows=rows_yielded)
