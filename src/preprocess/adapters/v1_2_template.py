"""v2.0 §8-3 — v1.2 통합 마스터 (빈 템플릿, 59col) 어댑터.

D-012: ExtractedRow → dev_part_master_fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    build_extracted_row,
    detect_header_by_dict,
    headers_from_single_row,
    is_blank_row,
    iter_data_rows,
    parse_multi_header,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

HEADER_ROWS = [1, 2, 3]
DATA_START_ROW = 4
FORM_ID = "v1_2_template_59"


def extract_v1_2_template(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}

    # 실데이터(메타블록 아래 row~8 헤더) 대응 — 동적 탐색 우선, 실패 시 합성 템플릿 [1,2,3].
    detected = detect_header_by_dict(sheet, cdict)
    if detected is not None:
        header_row, data_start = detected
        headers = headers_from_single_row(sheet, header_row)
    else:
        headers = parse_multi_header(sheet, HEADER_ROWS)
        data_start = DATA_START_ROW

    log.info(
        "adapter.v1_2.headers_discovered",
        file=file_path.name,
        sheet=sheet.name,
        header_count=len(headers),
        dynamic=detected is not None,
        data_start=data_start,
    )

    if not headers:
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
    if rows_yielded:
        log.info("adapter.v1_2.extracted", file=file_path.name, rows=rows_yielded)
