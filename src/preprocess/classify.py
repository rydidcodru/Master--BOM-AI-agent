"""v2.0 Step 1 — 시트 단위 결정론적 양식 분류 (preprocessing_v2.md §1, §8-8).

파일 단위 분류는 v1.0이었음. v2.0은 **시트 단위 분류** — 한 파일에 여러
양식 시트가 섞여있음(실측 19파일에서 발견). LLM 0회.

신호 3종을 가중 합산:
  1. 시트명 정규식 (예: ``^변경.?부품 list``, ``^ag-grid$``)
  2. 행1/행2 마커 (예: col 2 == ``"New & Changing Part"``)
  3. max_col 범위 (예: 91~97 = 변경부품 list family)

신호 ≥2 매칭 → confidence 1.0
신호 1개만   → confidence 0.7
0개          → ``"unknown"`` (quarantine)

invalid XML 파일(BDO30 SKS 케이스)은 ``read_workbook``의 calamine fallback
으로 자동 회복; 둘 다 실패하면 ``error``로 결과 채우고 다음 파일 계속.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.utils.excel import SheetData, read_workbook
from src.utils.logging import get_logger
from src.utils.paths import FORM_SIGNATURES_PATH

log = get_logger(__name__)

_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}


# --- Result types --------------------------------------------------------


@dataclass
class SheetClassification:
    """한 시트의 분류 결과."""

    file_path: str
    sheet_name: str
    form_id: str
    confidence: float
    signals_matched: list[str] = field(default_factory=list)
    max_col: int = 0
    max_row: int = 0
    needs_review: bool = False
    error: str | None = None


@dataclass
class ClassificationResult:
    """한 파일의 시트별 분류 결과 묶음 (CLI 호환성을 위한 wrapper)."""

    file_path: str
    form_version: str             # 파일 대표 form_id (가장 높은 confidence)
    confidence: float
    needs_review: bool = False
    evidence: dict[str, float] = field(default_factory=dict)
    sheet_results: dict[str, SheetClassification] = field(default_factory=dict)
    error: str | None = None


# --- Config loading ------------------------------------------------------


_PRIORITY_FALLBACK = (
    "변경부품_list_family",
    "신규부품리스트_75",
    "BOM_ag_grid_36",
    "v1_2_template_59",
    "UAE_신규개발_58",
    "base_master_24",
)  # D-011: activity_master_meta 제거.


def load_signatures(path: Path = FORM_SIGNATURES_PATH) -> dict[str, Any]:
    """form_signatures.yaml 로드."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


# --- Signal scoring ------------------------------------------------------


def _row_cell(rows: list[list[Any]], row_idx_1based: int, col_idx_1based: int) -> Any:
    """1-based 인덱싱으로 셀 값 반환 (없으면 None)."""
    r = row_idx_1based - 1
    c = col_idx_1based - 1
    if r < 0 or r >= len(rows):
        return None
    row = rows[r]
    if c < 0 or c >= len(row):
        return None
    return row[c]


def _string_cells_in_row(
    rows: list[list[Any]], row_idx_1based: int
) -> list[str]:
    r = row_idx_1based - 1
    if r < 0 or r >= len(rows):
        return []
    return [str(c).strip() for c in rows[r] if c is not None and str(c).strip()]


def _matches_sheet_name(name: str, patterns: list[str]) -> bool:
    for p in patterns or []:
        if re.search(p, name):
            return True
    return False


def _matches_file_name(file_name: str, patterns: list[str]) -> bool:
    for p in patterns or []:
        if re.search(p, file_name):
            return True
    return False


def _matches_row_marker(rows: list[list[Any]], markers: list[dict[str, Any]]) -> bool:
    """행1/행2 마커 조건 중 하나라도 매치 시 True."""
    for m in markers or []:
        col = m.get("col")
        col_range = m.get("col_range")
        value = m.get("value")
        value_regex = m.get("value_regex")
        value_in = m.get("value_in")
        min_count = m.get("min_count", 1)
        row = m.get("_row", 1)  # 마커는 기본 행1, row2_markers는 외부에서 _row=2 주입

        if col is not None:
            cell = _row_cell(rows, row, col)
            cell_s = "" if cell is None else str(cell).strip()
            if value is not None and cell_s == value:
                return True
            if value_regex is not None and cell_s and re.search(value_regex, cell_s):
                return True
            if value_in is not None and cell_s in value_in:
                return True
        elif col_range is not None:
            start, end = col_range
            cells = []
            for c in range(start, end + 1):
                v = _row_cell(rows, row, c)
                if v is not None:
                    cells.append(str(v).strip())
            hits = sum(1 for c in cells if value_in and c in value_in)
            if hits >= min_count:
                return True
    return False


def _max_col_in_range(max_col: int, rng: list[int]) -> bool:
    if not rng or len(rng) != 2:
        return False
    return rng[0] <= max_col <= rng[1]


def _score_sheet_for_form(
    sheet: SheetData,
    file_path: Path,
    form_id: str,
    form_cfg: dict[str, Any],
) -> tuple[int, list[str]]:
    """3 신호(시트명/파일명/행마커/max_col) 합산. (matched_count, reasons)."""
    matched: list[str] = []

    # 1) sheet name pattern
    if _matches_sheet_name(sheet.name, form_cfg.get("sheet_name_patterns", [])):
        matched.append("sheet_name")

    # 2) file name pattern (있을 때만)
    file_patterns = form_cfg.get("file_name_patterns", [])
    if file_patterns and _matches_file_name(file_path.name, file_patterns):
        matched.append("file_name")

    # 3) row1 / row2 markers
    row1_markers = [dict(m, _row=1) for m in form_cfg.get("row1_markers", [])]
    row2_markers = [dict(m, _row=2) for m in form_cfg.get("row2_markers", [])]
    if row1_markers and _matches_row_marker(sheet.rows, row1_markers):
        matched.append("row1_marker")
    if row2_markers and _matches_row_marker(sheet.rows, row2_markers):
        matched.append("row2_marker")

    # 4) max_col range
    rng = form_cfg.get("max_col_range", [])
    if rng and _max_col_in_range(sheet.max_col, rng):
        matched.append("max_col_range")

    # min_row 제약 (예: BOM은 100행+)
    min_row = form_cfg.get("min_row")
    if min_row is not None and sheet.max_row < min_row:
        # min_row 미달이면 max_col_range 매치 무효화
        matched = [m for m in matched if m != "max_col_range"]

    return len(matched), matched


def _confidence_from_count(count: int, form_cfg: dict[str, Any]) -> float:
    if count >= 2:
        return float(form_cfg.get("confidence_full", 1.0))
    if count == 1:
        return float(form_cfg.get("confidence_partial", 0.7))
    return 0.0


def _resolve_sub_variant(form_id: str, max_col: int, form_cfg: dict[str, Any]) -> str:
    """변경부품_list_family 같이 max_col 기준 sub-variant 결정."""
    sub: dict[str, Any] = form_cfg.get("sub_variants", {}) or {}
    for variant, rng in sub.items():
        if rng[0] <= max_col <= rng[1]:
            return variant
    return form_id


# --- Public API ----------------------------------------------------------


def classify_sheet(
    file_path: Path,
    sheet: SheetData,
    signatures: dict[str, Any] | None = None,
) -> SheetClassification:
    """시트 1개 분류.

    Args:
        file_path: 파일 경로 (file_name_patterns 매치용).
        sheet: 시트 데이터.
        signatures: form_signatures dict (None이면 자동 로드).

    Returns:
        :class:`SheetClassification`.
    """
    sigs = signatures or load_signatures()
    forms = sigs.get("forms", {})
    priority = sigs.get("priority", list(_PRIORITY_FALLBACK))

    scored: list[tuple[str, int, list[str]]] = []
    for form_id in priority:
        cfg = forms.get(form_id)
        if not cfg:
            continue
        count, matched = _score_sheet_for_form(sheet, file_path, form_id, cfg)
        if count > 0:
            scored.append((form_id, count, matched))

    if not scored:
        return SheetClassification(
            file_path=str(file_path),
            sheet_name=sheet.name,
            form_id="unknown",
            confidence=0.0,
            max_col=sheet.max_col,
            max_row=sheet.max_row,
            needs_review=True,
        )

    # 가장 많은 신호 + priority 순서 우선
    scored.sort(key=lambda t: (-t[1], priority.index(t[0])))
    form_id, count, matched = scored[0]
    cfg = forms[form_id]
    confidence = _confidence_from_count(count, cfg)
    resolved = _resolve_sub_variant(form_id, sheet.max_col, cfg)

    return SheetClassification(
        file_path=str(file_path),
        sheet_name=sheet.name,
        form_id=resolved,
        confidence=confidence,
        signals_matched=matched,
        max_col=sheet.max_col,
        max_row=sheet.max_row,
        needs_review=confidence < 0.95,
    )


def classify_file(
    file_path: Path,
    signatures: dict[str, Any] | None = None,
) -> tuple[ClassificationResult, list[SheetClassification]]:
    """한 파일의 모든 시트를 분류.

    Returns:
        (파일 대표 결과, 시트별 결과 리스트).
    """
    sigs = signatures or load_signatures()
    try:
        sheets = read_workbook(file_path)
    except Exception as exc:  # noqa: BLE001 — invalid XML 등
        log.warning("classify.read_failed", file=str(file_path), error=str(exc))
        result = ClassificationResult(
            file_path=str(file_path),
            form_version="error",
            confidence=0.0,
            needs_review=True,
            error=str(exc),
        )
        return result, []

    sheet_results: list[SheetClassification] = [
        classify_sheet(file_path, s, sigs) for s in sheets
    ]

    # 파일 대표 form_version = 가장 높은 confidence (동점 시 첫 매칭)
    if not sheet_results:
        file_form = "unknown"
        file_conf = 0.0
    else:
        best = max(sheet_results, key=lambda r: (r.confidence, -sheet_results.index(r)))
        file_form = best.form_id
        file_conf = best.confidence

    evidence: dict[str, float] = {}
    for r in sheet_results:
        if r.form_id != "unknown":
            evidence[r.form_id] = max(evidence.get(r.form_id, 0.0), r.confidence)

    file_result = ClassificationResult(
        file_path=str(file_path),
        form_version=file_form,
        confidence=file_conf,
        needs_review=file_conf < 0.95,
        evidence=evidence,
        sheet_results={r.sheet_name: r for r in sheet_results},
    )
    return file_result, sheet_results


def classify_form(file_path: Path) -> ClassificationResult:
    """CLI 호환: 파일 단위 분류 결과(시트별 breakdown 포함)."""
    result, _ = classify_file(file_path)
    return result


def classify_dir(directory: Path) -> list[ClassificationResult]:
    """디렉토리 모든 엑셀 파일 분류."""
    results: list[ClassificationResult] = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in _EXCEL_SUFFIXES:
            continue
        result, _ = classify_file(path)
        results.append(result)
        log.info(
            "classify.file",
            file=path.name,
            form=result.form_version,
            conf=result.confidence,
            sheets=len(result.sheet_results),
        )
    return results


def classify_all_sheets(directory: Path) -> list[SheetClassification]:
    """디렉토리의 모든 시트를 평탄화해서 반환 (어댑터 dispatch용)."""
    sigs = load_signatures()
    out: list[SheetClassification] = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in _EXCEL_SUFFIXES:
            continue
        _, sheet_results = classify_file(path, sigs)
        out.extend(sheet_results)
    return out
