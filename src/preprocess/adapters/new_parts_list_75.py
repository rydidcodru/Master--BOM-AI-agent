"""v2.0 §8-2 — 신규부품리스트 (75col, BO24 패밀리) 어댑터.

D-011: 담당자 15회 슬롯 직렬화 제거. 담당자 컬럼은 extra_fields에 보존.
D-012: ExtractedRow → dev_part_master_fields 직접 매핑.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    build_extracted_row,
    is_blank_row,
    iter_data_rows,
    parse_multi_header,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

HEADER_ROWS = [3]
DATA_START_ROW = 5
FORM_ID = "new_parts_list_75"


def extract_new_parts_list_75(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    headers = parse_multi_header(sheet, HEADER_ROWS)

    if not headers:
        log.warning("adapter.new_parts.no_headers", file=file_path.name, sheet=sheet.name)
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, DATA_START_ROW):
        if is_blank_row(row):
            continue
        core: dict[str, Any] = {}
        payload: dict[str, Any] = {}

        for col_idx in range(1, len(row) + 1):
            header_path = headers.get(col_idx)
            if not header_path:
                continue
            value = row[col_idx - 1]
            payload[header_path] = value
            core_field = cdict.lookup(header_path)
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

    log.info(
        "adapter.new_parts.extracted",
        file=file_path.name,
        sheet=sheet.name,
        rows=rows_yielded,
    )
