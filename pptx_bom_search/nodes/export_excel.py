from __future__ import annotations

"""
변경부품리스트.xlsx 생성 모듈.

참고용_Extra_결과.xlsx 포맷 기준:
  Row 1     : 공통\nCommon
  Row 2     : Base Model/Grade
  Row 3     : New Model/Grade
  Row 4     : Event
  Row 8     : 컬럼 헤더
  Row 9     : 서브헤더 (Base P/No / New P/No / Base qty / New qty)
  Row 10~   : 데이터
"""

import io
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


# ── 컬럼 정의 (1-based index) ─────────────────────────────────
# B=2  No.
# C=3  BOM Level
# D=4  Part Type
# E=5  Base P/No
# F=6  New P/No
# G=7  부품명
# H=8  Q'ty Base
# I=9  Q'ty New
# J=10 변경점
# K=11 변경사유
# L=12 양산처
# M=13 Classification
# N=14 금형개발/수정
# O=15 사내제작
# P=16 부품인정시험

_COL = {
    "no":           2,
    "bom_level":    3,
    "part_type":    4,
    "base_pno":     5,
    "new_pno":      6,
    "part_name":    7,
    "qty_base":     8,
    "qty_new":      9,
    "change_point": 10,
    "change_reason":11,
    "supplier":     12,
    "classification":13,
    "mold":         14,
    "inhouse":      15,
    "approval_test":16,
}
_MAX_COL = 16

# ── 스타일 헬퍼 ───────────────────────────────────────────────
_THIN = Side(style="thin")
_THICK = Side(style="medium")

def _border(top=None, bottom=None, left=None, right=None) -> Border:
    return Border(top=top, bottom=bottom, left=left, right=right)

def _all_thin() -> Border:
    return Border(top=_THIN, bottom=_THIN, left=_THIN, right=_THIN)

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, size=10, color="000000") -> Font:
    return Font(bold=bold, size=size, color=color, name="Calibri")

def _align(h="center", v="center", wrap=True) -> Alignment:
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


# ── 헤더 행 작성 ──────────────────────────────────────────────

def _write_meta_header(ws, base_model: str, new_model: str, event: str) -> None:
    """Row 1~7: 메타정보 헤더."""
    # Row 1: 공통\nCommon
    c = ws.cell(1, 2, "공통\nCommon")
    c.font = _font(bold=True, size=11)
    c.alignment = _align()
    ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=_MAX_COL)

    # Row 2: Base Model
    ws.cell(2, 2, "Base Model/Grade").font = _font(bold=True)
    ws.cell(2, 2).alignment = _align()
    ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=4)
    c = ws.cell(2, 5, base_model)
    c.alignment = _align(h="left")
    ws.merge_cells(start_row=2, start_column=5, end_row=2, end_column=_MAX_COL)

    # Row 3: New Model
    ws.cell(3, 2, "New Model/Grade").font = _font(bold=True)
    ws.cell(3, 2).alignment = _align()
    ws.merge_cells(start_row=3, start_column=2, end_row=3, end_column=4)
    c = ws.cell(3, 5, new_model)
    c.alignment = _align(h="left")
    ws.merge_cells(start_row=3, start_column=5, end_row=3, end_column=_MAX_COL)

    # Row 4: Event
    ws.cell(4, 2, "Event").font = _font(bold=True)
    ws.cell(4, 2).alignment = _align()
    ws.merge_cells(start_row=4, start_column=2, end_row=4, end_column=4)
    c = ws.cell(4, 5, event)
    c.alignment = _align(h="left")
    ws.merge_cells(start_row=4, start_column=5, end_row=4, end_column=_MAX_COL)


def _write_col_header(ws) -> None:
    """Row 8~9: 컬럼 헤더."""
    header_fill = _fill("BDD7EE")  # 연파랑
    bold_font   = _font(bold=True, size=9)
    center      = _align()

    headers_r8 = {
        _COL["no"]:            "No.",
        _COL["bom_level"]:     "BOM\nLevel",
        _COL["part_type"]:     "Part Type",
        _COL["base_pno"]:      "P/No",          # merged with new_pno col header area
        _COL["part_name"]:     "부품명\nClass Desc.(Part Name)",
        _COL["qty_base"]:      "Q'ty",           # merged with qty_new
        _COL["change_point"]:  "변경점\nChanging Point",
        _COL["change_reason"]: "변경사유\nChanging Reason",
        _COL["supplier"]:      "양산처\nSupplier",
        _COL["classification"]:"신규/변경\n부품 대상\nClassification",
        _COL["mold"]:          "금형\n개발/수정\nMold Dev/Modify",
        _COL["inhouse"]:       "사내\n제작\nIn-house Prod.",
        _COL["approval_test"]: "부품 인정시험\n실시 여부",
    }

    # Row 8: 헤더 (P/No 와 Q'ty는 row8-9 병합)
    for col, label in headers_r8.items():
        c = ws.cell(8, col, label)
        c.font      = bold_font
        c.fill      = header_fill
        c.alignment = center
        c.border    = _all_thin()

    # P/No 헤더 → E8:F8 병합
    ws.merge_cells(start_row=8, start_column=_COL["base_pno"],
                   end_row=8,   end_column=_COL["new_pno"])

    # Q'ty 헤더 → H8:I8 병합
    ws.merge_cells(start_row=8, start_column=_COL["qty_base"],
                   end_row=8,   end_column=_COL["qty_new"])

    # No., BOM Level, Part Type, 부품명, 변경점, 변경사유, 양산처, Classification, 금형, 사내, 인정시험
    # → row 8~9 병합
    merge_single_cols = [
        _COL["no"], _COL["bom_level"], _COL["part_type"],
        _COL["part_name"], _COL["change_point"], _COL["change_reason"],
        _COL["supplier"], _COL["classification"],
        _COL["mold"], _COL["inhouse"], _COL["approval_test"],
    ]
    for col in merge_single_cols:
        ws.merge_cells(start_row=8, start_column=col,
                       end_row=9,   end_column=col)
        ws.cell(8, col).alignment = center

    # Row 9: 서브헤더 (Base P/No, New P/No, Base qty, New qty)
    sub_fill = _fill("DDEBF7")
    for col, label in [
        (_COL["base_pno"],  "Base P/No"),
        (_COL["new_pno"],   "New P/No"),
        (_COL["qty_base"],  "Base"),
        (_COL["qty_new"],   "New"),
    ]:
        c = ws.cell(9, col, label)
        c.font      = _font(bold=True, size=9)
        c.fill      = sub_fill
        c.alignment = center
        c.border    = _all_thin()


# ── 데이터 행 작성 ────────────────────────────────────────────

def _classification(cp: dict) -> str:
    t = cp.get("type", "")
    if t == "NEW":
        return "New"
    if t == "삭제":
        return "Delete"
    return "Change"


def _write_data_rows(ws, rows: list[dict], start_row: int = 10) -> None:
    """데이터 행 작성. rows는 export_rows() 반환값."""
    center = _align()
    left   = _align(h="left")

    for r_idx, row in enumerate(rows, start=start_row):
        data = {
            _COL["no"]:            row.get("no", r_idx - start_row + 1),
            _COL["bom_level"]:     row.get("bom_level", ""),
            _COL["part_type"]:     row.get("part_type", ""),
            _COL["base_pno"]:      row.get("base_pno", "-"),
            _COL["new_pno"]:       row.get("new_pno", "TBD"),
            _COL["part_name"]:     row.get("part_name", ""),
            _COL["qty_base"]:      row.get("qty_base", ""),
            _COL["qty_new"]:       "←",
            _COL["change_point"]:  row.get("change_point", ""),
            _COL["change_reason"]: row.get("change_reason", ""),
            _COL["supplier"]:      row.get("supplier", ""),
            _COL["classification"]:row.get("classification", ""),
            _COL["mold"]:          "",
            _COL["inhouse"]:       "",
            _COL["approval_test"]: "",
        }

        # 분류별 행 색상
        cls = row.get("classification", "")
        if cls == "New":
            row_fill = _fill("EBF3E8")      # 연초록
        elif cls == "Delete":
            row_fill = _fill("FCE4D6")      # 연주황
        else:
            row_fill = None

        for col, val in data.items():
            c = ws.cell(r_idx, col, val)
            c.border = _all_thin()
            c.font   = _font(size=9)
            if row_fill:
                c.fill = row_fill
            if col in (_COL["part_name"], _COL["change_point"], _COL["change_reason"]):
                c.alignment = left
            else:
                c.alignment = center

        ws.row_dimensions[r_idx].height = 16


# ── 열 너비 설정 ──────────────────────────────────────────────

def _set_col_widths(ws) -> None:
    widths = {
        _COL["no"]:            5,
        _COL["bom_level"]:     7,
        _COL["part_type"]:     10,
        _COL["base_pno"]:      15,
        _COL["new_pno"]:       15,
        _COL["part_name"]:     30,
        _COL["qty_base"]:      6,
        _COL["qty_new"]:       6,
        _COL["change_point"]:  30,
        _COL["change_reason"]: 30,
        _COL["supplier"]:      14,
        _COL["classification"]:10,
        _COL["mold"]:          10,
        _COL["inhouse"]:       8,
        _COL["approval_test"]: 12,
    }
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


# ── change_points → 행 목록 변환 ─────────────────────────────

def build_export_rows(
    change_points: list[dict],
    selected_linked: list[dict],   # [{part_no, part_name, change_type, ...}]
) -> list[dict]:
    """
    change_points + 선택된 연동 부품 → Excel 데이터 행 목록.

    정렬 기준: BOM 매칭된 것 먼저 (is_order), 신규/미매칭, 연동 부품.
    """
    rows: list[dict] = []

    # ── change_points 행 ──────────────────────────────────────
    matched_cps = sorted(
        [cp for cp in change_points if cp.get("bom_matched")],
        key=lambda x: int(x.get("is_order", 9999) or 9999),
    )
    other_cps = [cp for cp in change_points if not cp.get("bom_matched")]

    for cp in matched_cps + other_cps:
        rows.append({
            "bom_level":     cp.get("bom_level", ""),
            "part_type":     cp.get("part_type", ""),
            "base_pno":      cp.get("base_part_no", "") or "-",
            "new_pno":       "TBD",
            "part_name":     cp.get("part", ""),
            "qty_base":      cp.get("qty", ""),
            "change_point":  cp.get("change_detail", ""),
            "change_reason": cp.get("change_reason", ""),
            "supplier":      cp.get("supplier", ""),
            "classification": _classification(cp),
            "source":        "change_point",
        })

    # ── 선택된 연동 부품 행 ────────────────────────────────────
    # 중복 방지: change_points의 base_part_no 또는 part명과 겹치면 제외
    existing_pnos = {r["base_pno"] for r in rows if r["base_pno"] not in ("-", "")}
    existing_names = {str(r["part_name"]).lower() for r in rows if r["part_name"]}

    for lp in selected_linked:
        pno  = lp.get("part_no", "")
        name = lp.get("part_name", "")
        if pno and pno in existing_pnos:
            continue
        if name and name.lower() in existing_names:
            continue
        ctype = lp.get("change_type", "변경")
        rows.append({
            "bom_level":     "",
            "part_type":     "",
            "base_pno":      pno if ctype != "추가" else "-",
            "new_pno":       "TBD" if ctype == "추가" else "TBD",
            "part_name":     lp.get("part_name", ""),
            "qty_base":      "",
            "change_point":  lp.get("relevance_reason", ""),
            "change_reason": "",
            "supplier":      "",
            "classification": {"추가": "New", "삭제": "Delete"}.get(ctype, "Change"),
            "source":        "linked_part",
        })
        if pno:
            existing_pnos.add(pno)

    # 순번 부여
    for idx, row in enumerate(rows, start=1):
        row["no"] = idx

    return rows


# ── 최종 Excel 바이트 생성 ────────────────────────────────────

def generate_excel(
    change_points: list[dict],
    selected_linked: list[dict],
    base_model: str = "",
    new_model: str  = "TBD",
    event: str      = "",
) -> bytes:
    """완성된 변경부품리스트 xlsx를 bytes로 반환 (st.download_button에 직접 사용)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "변경부품리스트"

    # 행 높이 기본값
    for r in range(1, 10):
        ws.row_dimensions[r].height = 18

    _write_meta_header(ws, base_model, new_model, event)
    _write_col_header(ws)

    export_rows = build_export_rows(change_points, selected_linked)
    _write_data_rows(ws, export_rows, start_row=10)
    _set_col_widths(ws)

    # 틀 고정: 9행 아래
    ws.freeze_panes = "B10"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
