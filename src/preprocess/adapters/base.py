"""D-012 — 어댑터 공통 dataclass + helper.

ExtractedRow shape change:
  before (v2.0): (core, payload, semantic, source_meta)
  after  (D-012): (dev_part_master_fields, extra_fields, source_meta)

어댑터는 여전히 column_dictionary 기반으로 (core, payload) 중간 dict를
만들지만, 행 마지막에 ``build_extracted_row(core, payload, source_meta, cdict)``
를 호출해 팀원 dev_part_master 컬럼명으로 변환된 단일 ExtractedRow를 반환한다.

BOM 어댑터(bom_ag_grid)도 동일한 ExtractedRow 스트림으로 통일 — bom_edges
테이블이 사라졌으므로 부품 간 hierarchy 정보는 extra_fields의 parent_part_no
키에 보존.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from src.db._mapping import coerce_bom_depth, map_core_to_dpm
from src.ontology import axioms
from src.utils.excel import SheetData

_WS_RE = re.compile(r"\s+")


@dataclass
class SheetMeta:
    """시트 상단에서 추출한 메타 (base/new model, buyer 등)."""

    base_model_code: str | None = None
    new_model_code: str | None = None
    buyer_base: str | None = None
    buyer_new: str | None = None
    sheet_grade: str | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedRow:
    """어댑터가 반환하는 한 행 (D-012).

    dev_part_master_fields: 팀원 컬럼명으로 매핑된 dict (part_no_new, event 등).
    extra_fields: Core 13에 매핑된 헤더 제외 + Core 잔여 (grade, event_stage).
    source_meta: source_file, source_sheet, source_row, form_id.
    """

    dev_part_master_fields: dict[str, Any]
    extra_fields: dict[str, Any]
    source_meta: dict[str, Any]


# --- Helpers -------------------------------------------------------------


def normalize_cell_text(value: object) -> str:
    """셀 값 → strip + NFC + 다중공백 정리된 문자열."""
    if value is None:
        return ""
    nfc = unicodedata.normalize("NFC", str(value))
    return _WS_RE.sub(" ", nfc.strip())


def cell_at(rows: list[list[Any]], row_1: int, col_1: int) -> Any:
    """1-based 인덱스 셀 (없으면 None)."""
    r = row_1 - 1
    c = col_1 - 1
    if r < 0 or r >= len(rows):
        return None
    row = rows[r]
    if c < 0 or c >= len(row):
        return None
    return row[c]


def iter_data_rows(
    sheet: SheetData, start_row_1based: int
) -> Iterator[tuple[int, list[Any]]]:
    """``start_row_1based``부터 끝까지 (row_index_1based, row) iterator."""
    for idx, row in enumerate(sheet.rows[start_row_1based - 1 :], start=start_row_1based):
        yield idx, row


def is_blank_row(row: list[Any]) -> bool:
    return all(c is None or (isinstance(c, str) and not c.strip()) for c in row)


def parse_multi_header(
    sheet: SheetData,
    header_rows_1based: list[int],
    separator: str = " > ",
    fill_cap: int = 8,
    min_values_to_fill: int = 2,
) -> dict[int, str]:
    """멀티 헤더 행들을 결합해 ``{col_idx_1based: "대분류 > 중분류 > 컬럼명"}`` 반환.

    forward-fill 규칙은 base 모듈 v2.0과 동일.
    """
    if not header_rows_1based:
        return {}
    n_cols = sheet.max_col
    per_row: list[list[str]] = []
    for r in header_rows_1based:
        row_vals = [normalize_cell_text(cell_at(sheet.rows, r, c)) for c in range(1, n_cols + 1)]
        present_count = sum(1 for v in row_vals if v)
        if present_count < min_values_to_fill:
            per_row.append(row_vals)
            continue
        last = ""
        gap = 0
        filled: list[str] = []
        for v in row_vals:
            if v:
                last = v
                gap = 0
                filled.append(v)
            else:
                gap += 1
                if last and gap <= fill_cap:
                    filled.append(last)
                else:
                    filled.append("")
        per_row.append(filled)

    out: dict[int, str] = {}
    for c in range(1, n_cols + 1):
        parts = [row[c - 1] for row in per_row if row[c - 1]]
        deduped: list[str] = []
        for p in parts:
            if not deduped or deduped[-1] != p:
                deduped.append(p)
        if deduped:
            out[c] = separator.join(deduped)
    return out


def detect_header_by_dict(
    sheet: SheetData,
    cdict: Any,
    *,
    max_scan: int = 16,
    min_matches: int = 4,
    require_field: str | None = "part_no",
    probe_rows: int = 25,
    min_data_frac: float = 0.2,
) -> tuple[int, int] | None:
    """헤더 행을 **column_dictionary 매칭 수가 최대**인 행으로 동적 탐색.

    실데이터 마스터/통합 양식은 상단 메타블록(Base/New Model, Event 등) 아래 row 7~10에
    진짜 컬럼 헤더(변경점/변경사유/P/No 등)가 온다. 어댑터가 헤더 행을 하드코딩하면 그
    아래 변경 컬럼을 통째로 놓친다. 이 helper는 상단 ``max_scan`` 행 중 Core 필드로 매핑되는
    셀이 가장 많은 행을 헤더로 본다.

    **정밀도 가드**: 헤더 후보 아래 데이터 행을 probe해 *유효 P/No 또는 변경 텍스트*가 든
    행 비율이 ``min_data_frac`` 미만이면 None을 돌려준다 — DQMS/History 등 부품표가 아닌
    보조 시트에 잘못 발화해 노이즈 행(→ 격리율↑, axiom_violation_rate 게이트 실패)을
    쏟아내는 것을 막는다. UAE 신규리스트처럼 P/No가 '-'여도 변경 텍스트가 있으면 통과.

    Returns:
        (header_row_1based, data_start_1based) 또는 None(어댑터가 자체 fallback 사용).
    """
    best_row: int | None = None
    best_score = 0
    best_fields: set[str] = set()
    upto = min(max_scan, len(sheet.rows))
    for r in range(1, upto + 1):
        fields: set[str] = set()
        for c in range(1, sheet.max_col + 1):
            h = normalize_cell_text(cell_at(sheet.rows, r, c))
            if h:
                f = cdict.lookup(h)
                if f:
                    fields.add(f)
        if len(fields) > best_score:
            best_score = len(fields)
            best_row = r
            best_fields = fields
    if best_row is None or best_score < min_matches:
        return None
    if require_field and require_field not in best_fields:
        return None

    # 헤더 후보의 컬럼→필드 (part_no / 변경 컬럼 위치 파악)
    col_field: dict[int, str] = {}
    for c in range(1, sheet.max_col + 1):
        h = normalize_cell_text(cell_at(sheet.rows, best_row, c))
        if h:
            f = cdict.lookup(h)
            if f and f not in col_field.values():
                col_field[c] = f
    part_col = next((c for c, f in col_field.items() if f == "part_no"), None)
    change_cols = [c for c, f in col_field.items() if f in ("change_point", "change_reason")]

    # 데이터 probe — 진짜 부품/변경 행 비율
    valid = total = 0
    for r in range(best_row + 1, min(best_row + 1 + probe_rows, len(sheet.rows) + 1)):
        cells = [cell_at(sheet.rows, r, c) for c in range(1, sheet.max_col + 1)]
        if is_blank_row(cells):
            continue
        total += 1
        ok = False
        if part_col is not None:
            pv = normalize_cell_text(cell_at(sheet.rows, r, part_col))
            if pv and axioms.validate_part_no(pv):
                ok = True
        if not ok and any(
            normalize_cell_text(cell_at(sheet.rows, r, cc)) for cc in change_cols
        ):
            ok = True
        if ok:
            valid += 1
    if total and (valid / total) < min_data_frac:
        return None
    return best_row, best_row + 1


def headers_from_single_row(sheet: SheetData, header_row_1based: int) -> dict[int, str]:
    """단일 헤더 행 → {col_1based: 헤더텍스트}. forward-fill 없음(단일 leaf 헤더용)."""
    out: dict[int, str] = {}
    for c in range(1, sheet.max_col + 1):
        h = normalize_cell_text(cell_at(sheet.rows, header_row_1based, c))
        if h:
            out[c] = h
    return out


# --- ExtractedRow assembly ------------------------------------------------


def build_extracted_row(
    core: dict[str, Any],
    payload: dict[str, Any],
    source_meta: dict[str, Any],
    cdict: Any,
    extra_native: dict[str, Any] | None = None,
) -> ExtractedRow:
    """Adapter helper: column_dict 기반 (core, payload) → dev_part_master 형식.

    Args:
        core: column_dictionary.lookup으로 매핑된 필드 dict (part_no, change_type 등).
        payload: 원본 헤더 path → 셀 값 (Core 매핑 여부 무관).
        source_meta: source_file/sheet/row/form_id 등.
        cdict: ColumnDictionary (None 헤더가 mapped됐는지 판정용).
        extra_native: 어댑터가 dev_part_master 컬럼명으로 직접 채운 값
            (예: bom_ag_grid의 parent_part_no, supplier 등).

    Returns:
        :class:`ExtractedRow`.
    """
    extra_native = dict(extra_native or {})

    # 1) Core → dpm 컬럼명 변환 + 잔여(grade, event_stage)
    dpm, residual = map_core_to_dpm(core, extra_native)

    # 2) bom_level (Core) → bom_depth + bom_level_raw
    if core.get("bom_level") is not None:
        dpm.setdefault("bom_depth", coerce_bom_depth(core["bom_level"]))
        if "bom_level_raw" not in dpm:
            dpm["bom_level_raw"] = str(core["bom_level"])

    # 3) extra_fields: Core 13에 매핑 안 된 원본 헤더만 + Core 잔여 (grade/event_stage)
    extra_fields: dict[str, Any] = {}
    for header, value in (payload or {}).items():
        if cdict.lookup(header) is None and value is not None:
            extra_fields[header] = value
    extra_fields.update(residual)

    return ExtractedRow(
        dev_part_master_fields=dpm,
        extra_fields=extra_fields,
        source_meta=source_meta,
    )
