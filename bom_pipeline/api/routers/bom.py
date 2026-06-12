"""BOM 조회 / 확정 / xlsx 다운로드 엔드포인트."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

# ── 요청/응답 스키마 ──────────────────────────────────────────────────────

class SelectionItem(BaseModel):
    change_point_idx:   int
    part:               str
    base_part_no:       str
    new_part_no:        str
    change_detail:      str
    change_reason:      str
    change_type:        str
    discipline:         str
    bom_level:          str
    selected_cases:     list[int]
    added_linked_parts: list[dict]
    skipped:            bool = False


class BomBuildRequest(BaseModel):
    change_points: list[dict]
    selections:    list[SelectionItem]
    bom_rows:      list[dict] = []
    fixture:       str | None = None   # "quickzone" | "compact_oven"


class BomRow(BaseModel):
    part_no:      str
    lvl:          str
    parent_no:    str
    description:  str
    qty:          str
    uom:          str
    maker:        str
    part_type:    str
    change_status: str  # "" | "변경" | "추가" | "삭제"
    change_note:  str
    new_part_no_suggested: str
    new_part_no_confirmed: str
    original_part_no: str  # 변경 행의 변경 전 p/no (before 표시용)
    row_id:       str      # 그리드 고유 키
    confirmed:    bool     # 사용자 확정 여부 (변경/추가/삭제 행 기본 False)
    excluded:     bool     # 제외 여부


class BomBuildResponse(BaseModel):
    rows:    list[dict]
    summary: dict  # {"변경": int, "추가": int, "삭제": int, "미확정": int}


# ── 경로 상수 ─────────────────────────────────────────────────────────────

FIXTURE_BASE_BOM = {
    "quickzone":    "시연참고용/LTIS7338XE.ARSLLGA@CVZ.EKHQ 1.0.xlsx",
    "compact_oven": "input/WSED7613S.ASTQEUR@CVZ.EKHQ 1.0 (1).xlsx",
}
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # bom_pipeline/api/routers → repo root

# ── BOM 빌드 ─────────────────────────────────────────────────────────────

COL_PART_NO_IDX = 1   # xlsx 컬럼 B (0-based)
COL_LVL_IDX     = 2
COL_PARENT_IDX  = 9
COL_NAME_IDX    = 10
COL_DESC_IDX    = 11
COL_QTY_IDX     = 13
COL_UOM_IDX     = 14
COL_MAKER_IDX   = 27
COL_TYPE_IDX    = 33


def _row_to_dict(row_data: list, status: str, note: str, idx: int) -> dict:
    """analyze_bom_changes 결과 행 → API 응답 dict 변환."""
    def v(i: int) -> str:
        return str(row_data[i]).strip() if i < len(row_data) and row_data[i] is not None else ""

    # 변경 행은 마지막 원소에 original_part_no가 추가돼 있음
    original_pno = v(len(row_data) - 1) if status == "변경" and len(row_data) > 36 else ""

    part_no = v(COL_PART_NO_IDX)
    return {
        "part_no":               part_no,
        "lvl":                   v(COL_LVL_IDX),
        "parent_no":             v(COL_PARENT_IDX),
        "description":           v(COL_DESC_IDX),
        "qty":                   v(COL_QTY_IDX),
        "uom":                   v(COL_UOM_IDX),
        "maker":                 v(COL_MAKER_IDX),
        "part_type":             v(COL_TYPE_IDX),
        "change_status":         status,
        "change_note":           note,
        "new_part_no_suggested": part_no if status == "변경" else (part_no if status == "추가" else ""),
        "new_part_no_confirmed": part_no if status == "변경" else (part_no if status == "추가" else ""),
        "original_part_no":      original_pno or (part_no if status != "변경" else ""),
        "row_id":                f"{part_no or 'add'}_{idx}",
        "confirmed":             status == "",    # 변경 없는 행은 자동 확정
        "excluded":              False,
    }


@router.post("/build", response_model=BomBuildResponse)
def build_bom(req: BomBuildRequest):
    """
    change_points + selections → 변경 마킹된 전체 BOM 행 반환.
    fixture 또는 bom_rows 제공 시 실제 base BOM 기반으로 analyze_bom_changes 실행.
    """
    from nodes.write_bom import analyze_bom_changes

    bom_path: str | None = None

    # fixture 경로 결정
    if req.fixture and req.fixture in FIXTURE_BASE_BOM:
        candidate = _REPO_ROOT / FIXTURE_BASE_BOM[req.fixture]
        if candidate.exists():
            bom_path = str(candidate)

    if bom_path:
        _, result_rows = analyze_bom_changes(
            base_bom_path=bom_path,
            change_points=req.change_points,
            selections=[s.model_dump() for s in req.selections],
        )
        rows = [_row_to_dict(rd, st, note, i) for i, (rd, st, note) in enumerate(result_rows)]

    else:
        # bom_rows 기반 폴백 (base BOM 없을 때)
        raw = [dict(r) for r in req.bom_rows] if req.bom_rows else []
        pno_to_sel = {s.base_part_no: s for s in req.selections if not s.skipped and s.base_part_no}
        pno_to_cp  = {cp.get("base_part_no",""): cp for cp in req.change_points if cp.get("base_part_no")}
        deleted_map = {
            dp.get("part_no",""): f"{cp.get('change_detail','')} — 삭제 대상"
            for cp in req.change_points
            for dp in cp.get("deleted_parts", [])
            if dp.get("part_no")
        }
        for i, row in enumerate(raw):
            pno = row.get("part_no","")
            row.setdefault("original_part_no", pno)
            row.setdefault("row_id", f"{pno}_{i}")
            row.setdefault("excluded", False)
            if pno in deleted_map:
                row.update({"change_status":"삭제","change_note":deleted_map[pno],"confirmed":False})
            elif pno in pno_to_sel:
                sel = pno_to_sel[pno]; new_pno = sel.new_part_no or "TBD"
                row.update({
                    "change_status":         sel.change_type or "변경",
                    "change_note":           f"{sel.change_detail} — {new_pno}",
                    "new_part_no_suggested": new_pno,
                    "new_part_no_confirmed": new_pno if new_pno != "TBD" else "",
                    "confirmed":             False,
                })
            else:
                row.setdefault("change_status",""); row.setdefault("change_note","")
                row.setdefault("new_part_no_suggested",""); row.setdefault("new_part_no_confirmed","")
                row["confirmed"] = True
        rows = raw

    summary = {"변경": 0, "추가": 0, "삭제": 0, "미확정": 0}
    for r in rows:
        st = r.get("change_status","")
        if st in summary:
            summary[st] += 1
        if st and not r.get("confirmed") and not r.get("excluded"):
            summary["미확정"] += 1

    return BomBuildResponse(rows=rows, summary=summary)


# ── xlsx 다운로드 (write_bom 기반) ───────────────────────────────────────────


class ExportRequest(BaseModel):
    change_points: list[dict]
    selections:    list[SelectionItem]
    bom_rows:      list[dict] = []
    fixture:       str | None = None   # "quickzone" | "compact_oven"


@router.post("/export")
async def export_xlsx(
    req_json: str = "",
    base_bom: UploadFile | None = File(None),
):
    """
    확정된 변경점 + base BOM → 실제 새 BOM xlsx 반환.
    우선순위: ① 업로드 파일 ② fixture 서버 경로 ③ 마킹 요약 xlsx 폴백
    """
    import json as _json
    from nodes.write_bom import write_bom

    req = ExportRequest(**_json.loads(req_json)) if req_json else None
    if req is None:
        return StreamingResponse(io.BytesIO(b""), status_code=400)

    tmp_path: str | None = None

    try:
        # ① 업로드 파일 우선
        if base_bom:
            content = await base_bom.read()
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(content)
                tmp_path = f.name
            bom_path = tmp_path

        # ② fixture 서버 경로
        elif req.fixture and req.fixture in FIXTURE_BASE_BOM:
            candidate = _REPO_ROOT / FIXTURE_BASE_BOM[req.fixture]
            if candidate.exists():
                bom_path = str(candidate)
            else:
                bom_path = None

        else:
            bom_path = None

        if bom_path:
            xlsx_bytes = write_bom(
                base_bom_path=bom_path,
                change_points=req.change_points,
                selections=[s.model_dump() for s in req.selections],
            )
        else:
            # ③ 마킹된 행만 간이 xlsx
            xlsx_bytes = _export_marked_rows(req.bom_rows)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=new_bom.xlsx"},
    )


@router.post("/export/simple")
def export_xlsx_simple(rows: list[dict]):
    """마킹된 BOM 행만 빠르게 xlsx로 반환 (base BOM 없을 때)."""
    return StreamingResponse(
        io.BytesIO(_export_marked_rows(rows)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=new_bom.xlsx"},
    )


def _export_marked_rows(rows: list[dict]) -> bytes:
    """마킹된 BOM 행 → 컬러 xlsx bytes (간이 버전)."""
    import openpyxl
    from openpyxl.styles import PatternFill, Font as OFont

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"

    headers  = ["Part No", "레벨", "Parent No", "부품명", "수량", "단위",
                "메이커", "유형", "변경상태", "New P/No (확정)", "변경 내용"]
    col_keys = ["part_no", "lvl", "parent_no", "description", "qty", "uom",
                "maker", "part_type", "change_status", "new_part_no_confirmed", "change_note"]

    fill_map = {
        "변경": PatternFill("solid", fgColor="FFF3CD"),
        "추가": PatternFill("solid", fgColor="D4EDDA"),
        "NEW":  PatternFill("solid", fgColor="D4EDDA"),
        "삭제": PatternFill("solid", fgColor="F8D7DA"),
    }

    for ci, h in enumerate(headers, 1):
        ws.cell(row=1, column=ci, value=h).font = OFont(bold=True)

    for ri, row in enumerate(rows, 2):
        status = str(row.get("change_status", ""))
        fill   = fill_map.get(status)
        for ci, key in enumerate(col_keys, 1):
            cell = ws.cell(row=ri, column=ci, value=row.get(key, ""))
            if fill:
                cell.fill = fill
        if status == "삭제":
            for ci in range(1, len(col_keys) + 1):
                ws.cell(row=ri, column=ci).font = OFont(strike=True, color="888888")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()