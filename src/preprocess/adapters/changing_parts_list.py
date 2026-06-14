"""v2.0 §8-1 — 변경부품 list family 어댑터 (91/95/96/97 sub-variant 통합).

같은 양식이 시간에 따라 91 → 97 컬럼으로 진화. 위치 기반 매핑은 깨지므로
**헤더 이름 기반 매칭** 필수.

헤더 구조 (4행):
  행1: 대분류 (공통/DRBFM/부품/친환경/금형 — 'a'/'b' 접미사)
  행2: 중분류 + Base/New 모델 정보
  행3: Buyer명
  행4: Brand + 실제 컬럼명 시작
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.ontology import axioms
from src.preprocess.adapters.base import (
    ExtractedRow,
    SheetMeta,
    build_extracted_row,
    cell_at,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
    parse_multi_header,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

# 실측 분석 (2026-05-26): 합성 fixture는 row 2+4가 헤더지만 실데이터의 진짜 헤더는
# row 8~ (대분류) + row 9 (leaf 컬럼명), 데이터는 row 13부터.
# → "Common"/"공통"이 col 2에 있는 row를 동적으로 찾아 anchor로 삼고
#   다음 행(leaf)을 결합해 헤더 path 구성.
# Fallback (anchor 못 찾으면 v2.0 합성 fixture 호환을 위해 [2, 4] 사용).
DEFAULT_HEADER_ROWS = [2, 4]
DEFAULT_DATA_START_ROW = 5

# 대분류 anchor 후보 — 한국어 + 영어.
_GROUP_ANCHORS = {"공통", "Common", "공통 a", "공통 b", "공통a", "공통b"}
# anchor 다음 leaf 헤더와 데이터 사이의 빈 행/추가 메타 행 허용 폭.
_LEAF_GAP_TOLERANCE = 4


def _detect_header_anchor(sheet: SheetData) -> tuple[list[int], int] | None:
    """대분류 행을 동적으로 탐색.

    Returns:
        (header_rows, data_start_row_1based) 또는 None (못 찾으면).
    """
    # row 1~15까지 col 2 셀이 anchor 후보인지 검사
    for r in range(1, min(16, len(sheet.rows) + 1)):
        cell = normalize_cell_text(cell_at(sheet.rows, r, 2))
        if not cell:
            continue
        # exact 또는 대소문자 무시 매칭
        if cell in _GROUP_ANCHORS or cell.lower() in {a.lower() for a in _GROUP_ANCHORS}:
            # anchor 다음 행에 leaf 헤더가 있다고 가정
            leaf_row = r + 1
            # leaf row에 충분한 non-empty 컬럼이 있는지 검증
            leaf_cells = [
                cell_at(sheet.rows, leaf_row, c)
                for c in range(1, sheet.max_col + 1)
            ]
            non_empty = sum(1 for v in leaf_cells if v is not None and str(v).strip())
            if non_empty >= 5:
                # data_start_row는 leaf row + 1, 단 빈 행 skip
                data_row = leaf_row + 1
                for offset in range(_LEAF_GAP_TOLERANCE):
                    test_row = leaf_row + 1 + offset
                    row_vals = sheet.rows[test_row - 1] if test_row - 1 < len(sheet.rows) else []
                    if any(
                        isinstance(v, str)
                        and v.strip()
                        and v.strip().startswith(("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"))
                        and any(ch.isdigit() for ch in v[:11])
                        for v in row_vals
                    ):
                        data_row = test_row
                        break
                return [r, leaf_row], data_row
    return None


def _meta_cell(
    sheet: SheetData,
    cdict: ColumnDictionary,
    field: str,
    default_row: int,
    default_col: int,
) -> str | None:
    """column_dictionary의 sheet_meta_path가 명시되면 그 위치를, 아니면 default 사용.

    IMP-7: 위치 하드코딩 제거. column_dictionary.yaml 수정 시 어댑터 자동 추종.
    """
    entry = cdict.fields.get(field)
    row = default_row
    col = default_col
    if entry and entry.sheet_meta_path:
        row = int(entry.sheet_meta_path.get("row", default_row))
        col = int(entry.sheet_meta_path.get("col", default_col))
    return normalize_cell_text(cell_at(sheet.rows, row, col)) or None


def _extract_sheet_meta(sheet: SheetData, cdict: ColumnDictionary) -> SheetMeta:
    """시트 상단(행1~4)에서 base_model_code / new_model_code / buyer 추출.

    IMP-7: 셀 위치를 column_dictionary.yaml의 sheet_meta_path에서 읽음.
    기본값(spec)은 row=2, col=9(base) / col=12(new) — column_dictionary에 명시
    없으면 fallback.
    """
    meta = SheetMeta()
    meta.base_model_code = _meta_cell(sheet, cdict, "base_model_code", 2, 9)
    meta.new_model_code = _meta_cell(sheet, cdict, "new_model_code", 2, 12)
    # buyer는 column_dictionary에 entry가 없으므로 default 그대로
    meta.buyer_base = normalize_cell_text(cell_at(sheet.rows, 3, 9)) or None
    meta.buyer_new = normalize_cell_text(cell_at(sheet.rows, 3, 12)) or None
    meta.sheet_grade = axioms.parse_grade_from_sheet_name(sheet.name)

    meta.raw_meta = {
        "_meta_base_model_code": meta.base_model_code,
        "_meta_new_model_code": meta.new_model_code,
        "_meta_buyer_base": meta.buyer_base,
        "_meta_buyer_new": meta.buyer_new,
        "_meta_sheet_grade": meta.sheet_grade,
    }
    return meta


def _derive_form_version(max_col: int) -> str:
    """D-012: form_id 명명을 form_registry (changing_parts_list_NN) 와 일치시킴."""
    if max_col <= 92:
        return "changing_parts_list_91"
    if max_col <= 95:
        return "changing_parts_list_95"
    if max_col == 96:
        return "changing_parts_list_96"
    return "changing_parts_list_97"


def extract_changing_parts_list_family(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    """변경부품 list (91/95/96/97) 시트 1개 → ExtractedRow iterator.

    Yields:
        :class:`ExtractedRow` 인스턴스들 (빈 행 자동 skip).
    """
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    form_version = _derive_form_version(sheet.max_col)

    # 실데이터 대응 — 대분류 anchor를 동적 탐색해 진짜 헤더 위치 결정.
    detected = _detect_header_anchor(sheet)
    if detected is not None:
        header_rows, data_start = detected
        log.info(
            "adapter.changing_parts.header_detected",
            file=file_path.name,
            sheet=sheet.name,
            anchor_row=header_rows[0],
            leaf_row=header_rows[1],
            data_start=data_start,
        )
    else:
        header_rows, data_start = DEFAULT_HEADER_ROWS, DEFAULT_DATA_START_ROW

    headers = parse_multi_header(sheet, header_rows)
    meta = _extract_sheet_meta(sheet, cdict)

    if not headers:
        log.warning(
            "adapter.changing_parts.no_headers",
            file=file_path.name,
            sheet=sheet.name,
        )
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, data_start):
        if is_blank_row(row):
            continue
        core: dict[str, Any] = {}
        payload: dict[str, Any] = {}

        for col_idx in range(1, len(row) + 1):
            header_path = headers.get(col_idx)
            if not header_path:
                continue
            value = row[col_idx - 1]
            if value is None or (isinstance(value, str) and not value.strip()):
                payload[header_path] = None
                continue
            payload[header_path] = value

            core_field = cdict.lookup(header_path)
            if core_field is not None:
                core[core_field] = cdict.map_cell_value(core_field, value)

        # 시트 메타 주입 (위치 9/12 fallback)
        if not core.get("base_model_code") and meta.base_model_code:
            core["base_model_code"] = meta.base_model_code
        if not core.get("new_model_code") and meta.new_model_code:
            core["new_model_code"] = meta.new_model_code
        if not core.get("grade") and meta.sheet_grade:
            core["grade"] = meta.sheet_grade
        if not core.get("region") and meta.buyer_new:
            region = cdict.region_from_buyer(meta.buyer_new)
            if region:
                core["region"] = region

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_id": form_version,
            **meta.raw_meta,
            **file_meta,
        }

        yield build_extracted_row(core, payload, source_meta, cdict)
        rows_yielded += 1

    log.info(
        "adapter.changing_parts.extracted",
        file=file_path.name,
        sheet=sheet.name,
        form_version=form_version,
        rows=rows_yielded,
    )
