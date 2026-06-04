from __future__ import annotations

"""
후보 선정 결과를 바탕으로 Master BOM 행을 조립하고
변경사유를 GPT-4o로 생성하는 모듈.
"""

import io
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

SYSTEM_PROMPT = """당신은 LG전자 오븐/주방가전 BOM 담당자입니다.
Master BOM 문서의 '변경사유(Changing Reason)' 컬럼을 작성합니다.

규칙:
- 과거 유사이력의 문체와 간결함을 따를 것
- 이번 변경의 실제 내용(변경점, 변경사유)을 반영할 것
- 한 문장, 30자 내외
- 설명 없이 문장만 출력"""

HUMAN_TEMPLATE = """이번 변경점: {change_detail}
이번 변경사유(PPTX): {change_reason}
과거 유사이력 변경사유 참고:
{past_reasons}

위를 참고해 이번 건의 변경사유를 한 문장으로 작성하세요."""

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", HUMAN_TEMPLATE),
        ])
        _chain = prompt | llm | StrOutputParser()
    return _chain


def _generate_changing_reason(change_detail: str, change_reason: str,
                               selected_candidates: list[dict]) -> str:
    past_reasons = [
        c.get("changingReason", "") for c in selected_candidates
        if c.get("changingReason")
    ]
    if not past_reasons:
        return change_reason or change_detail

    past_text = "\n".join(f"- {r}" for r in past_reasons[:3])
    try:
        return _get_chain().invoke({
            "change_detail": change_detail,
            "change_reason": change_reason,
            "past_reasons": past_text,
        }).strip()
    except Exception as e:
        print(f"[WARNING] 변경사유 생성 실패: {e}")
        return change_reason or change_detail


def _type_to_part_type(raw_type: str) -> str:
    mapping = {
        "MechanicalAssemblyPart": "Structure Assy",
        "MechanicalPart":         "MD Part",
        "PCBAssemblyPart":        "PD part",
        "PCBPart":                "PD part",
        "CircuitComponentPart":   "PD part",
        "ManualPart":             "Printing Material",
        "LabelPart":              "Printing Material",
        "SoftwarePart":           "SW",
        "MaterialPart":           "MD Part",
        "FastenerPart":           "MD Part",
        "BoxPart":                "Printing Material",
    }
    return mapping.get(raw_type or "", raw_type or "")


def build_master_rows_from_confirmed(confirmed_selections: list[dict]) -> list[dict]:
    """
    확정 부품 + 선택 이력 → Master BOM 행 목록 조립.

    confirmed_selections: [
      {
        "confirmed_part": {part_no, lvl, description, type, maker, change_point_ref},
        "selected_history": history_dict or None,
        "custom_reason": str   # 이력 없을 때 직접 입력한 변경사유
      }, ...
    ]
    """
    rows = []
    no = 1
    seen_pno: set[str] = set()

    for sel in confirmed_selections:
        part    = sel["confirmed_part"]
        history = sel.get("selected_history")
        custom  = sel.get("custom_reason", "")
        cp_ref  = part.get("change_point_ref", {})

        pno = part.get("part_no", "")
        if pno in seen_pno:
            continue
        seen_pno.add(pno)

        # 변경점: 원본 PPTX change_detail
        changing_point = cp_ref.get("change_detail", "") or "하위 부품 변경"

        # 변경사유: 이력 있으면 LLM 생성, 없으면 직접 입력값
        if history:
            changing_reason = _generate_changing_reason(
                changing_point,
                cp_ref.get("change_reason", ""),
                [history],
            )
        else:
            changing_reason = custom or changing_point

        rows.append({
            "No":              no,
            "BOM_Level":       part.get("lvl", ""),
            "Part_Type":       _type_to_part_type(part.get("type", "")),
            "Base_PNo":        pno,
            "New_PNo":         "←",
            "Class_Desc":      part.get("description", ""),
            "Qty_Base":        part.get("qty") or 1,
            "Qty_New":         "←",
            "Changing_Point":  changing_point,
            "Changing_Reason": changing_reason,
            "Supplier":        part.get("maker", ""),
            "Classification":  history.get("classification", "") if history else "",
            "source_pptx":     cp_ref.get("source_pptx", ""),
            "module":          cp_ref.get("module", ""),
            "part":            cp_ref.get("part", ""),
        })
        no += 1

    return rows


def _make_row(no: int, hier_row: dict, cp: dict,
              changing_point: str, changing_reason: str,
              supplier: str) -> dict:
    """계층 행 하나를 Master BOM row dict로 변환."""
    raw_level = hier_row.get("rawLevel") or ""
    part_type = _type_to_part_type(hier_row.get("partType") or "")
    return {
        "No":              no,
        "BOM_Level":       raw_level,
        "Part_Type":       part_type,
        "Base_PNo":        hier_row.get("basePartNoRaw") or "",
        "New_PNo":         hier_row.get("newPartNoRaw") or "",
        "Class_Desc":      hier_row.get("partName") or "",
        "Qty_Base":        1,
        "Qty_New":         1,
        "Changing_Point":  changing_point,
        "Changing_Reason": changing_reason,
        "Supplier":        supplier,
        "Classification":  hier_row.get("classification") or "",
        # 메타
        "source_pptx":     cp.get("source_pptx", ""),
        "module":          cp.get("module", ""),
        "part":            cp.get("part", ""),
    }


def build_master_rows(search_results: list[dict],
                      selections: dict[int, list[dict]]) -> list[dict]:
    """
    선정된 후보를 기반으로 계층 구조 포함 Master BOM 행 목록을 조립한다.

    흐름:
      선택된 후보 → lineId/documentId로 Neo4j 계층 조회
      → 상위 Assembly + 자신 + 하위 부품 행 생성
      → 자신 행: PPTX 변경점 + LLM 변경사유
      → 상위 행: "하위 부품 변경" 고정
      → 하위 행: 과거 이력 그대로
    """
    from nodes.fetch_hierarchy import fetch_hierarchy

    rows = []
    no = 1
    seen_lines: set[str] = set()  # 중복 lineId 방지

    for sr_idx, selected_cands in sorted(selections.items()):
        if not selected_cands:
            continue

        sr = search_results[sr_idx]
        cp = sr["change_point"]
        change_detail = cp.get("change_detail", "")
        change_reason = cp.get("change_reason", "")

        for cand in selected_cands:
            line_id = cand.get("lineId") or ""
            doc_id  = cand.get("documentId") or ""
            supplier = cand.get("modelName") or ""

            # ── 계층 조회 ──────────────────────────────────────
            hier_rows = fetch_hierarchy(line_id, doc_id) if line_id and doc_id else []

            if not hier_rows:
                # 계층 정보 없으면 후보 행 단독으로 생성
                print(f"[write_master] '{cp.get('part','')}' 계층 없음 — 단독 행 생성")
                print(f"[write_master] '{cp.get('part','')}' 변경사유 생성 중...")
                reason = _generate_changing_reason(change_detail, change_reason, [cand])
                rows.append(_make_row(no, {
                    "rawLevel":      cand.get("rawLevel") or str(cand.get("level", "")),
                    "partType":      cand.get("partType") or "",
                    "basePartNoRaw": cand.get("basePartNoRaw") or "",
                    "newPartNoRaw":  cand.get("newPartNoRaw") or "",
                    "partName":      cand.get("partName") or cp.get("part", ""),
                }, cp, change_detail, reason, supplier))
                no += 1
                continue

            # ── 계층 행별 변경사유 생성 ───────────────────────
            # 선택된 후보(자신)와 같은 lineId인 행만 LLM 생성
            print(f"[write_master] '{cp.get('part','')}' 계층 {len(hier_rows)}행 처리 중...")
            print(f"[write_master] '{cp.get('part','')}' 변경사유 생성 중...")
            self_reason = _generate_changing_reason(change_detail, change_reason, [cand])

            for h in hier_rows:
                lid = h.get("lineId", "")
                if lid in seen_lines:
                    continue
                seen_lines.add(lid)

                is_self = (lid == line_id)
                is_ancestor = (h.get("level", 0) < (cand.get("level") or 0))

                if is_self:
                    # 자신: PPTX 변경점 + LLM 변경사유
                    c_point  = change_detail
                    c_reason = self_reason
                elif is_ancestor:
                    # 상위 Assembly: 과거 이력 변경점 유지, 변경사유는 "하위 부품 변경"
                    c_point  = h.get("changingPoint") or "하위 부품 변경"
                    c_reason = h.get("changingReason") or "하위 부품 변경"
                else:
                    # 하위: 과거 이력 그대로
                    c_point  = h.get("changingPoint") or ""
                    c_reason = h.get("changingReason") or ""

                rows.append(_make_row(no, h, cp, c_point, c_reason, supplier))
                no += 1

    return rows


def rows_to_excel(rows: list[dict], new_model: str = "", base_model: str = "") -> bytes:
    """Master BOM 행을 이미지 포맷의 Excel 파일로 변환, bytes 반환."""
    try:
        import openpyxl
        from openpyxl.styles import (Alignment, Border, Font, PatternFill,
                                      Side)
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError("openpyxl이 필요합니다: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Master BOM"

    # ── 스타일 정의 ───────────────────────────────────────────
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left   = Alignment(horizontal="left",   vertical="center", wrap_text=True)

    hdr_fill   = PatternFill("solid", fgColor="D9E1F2")  # 헤더 파란빛
    model_fill = PatternFill("solid", fgColor="F2F2F2")  # 모델 행 회색

    def cell(ws, r, c, value="", bold=False, fill=None, align=None):
        cell = ws.cell(row=r, column=c, value=value)
        cell.border = border
        cell.alignment = align or center
        cell.font = Font(bold=bold, size=9)
        if fill:
            cell.fill = fill
        return cell

    # ── Common 헤더 (행 1~3) ──────────────────────────────────
    ws.merge_cells("A1:C1")
    ws.cell(row=1, column=1, value="Common").font = Font(bold=True, size=9)
    ws.cell(row=1, column=1).alignment = center

    for r, label, value in [
        (2, "Base Model/Grade", base_model),
        (3, "New Model/Grade",  new_model),
        (4, "Event",            "CP DV PV PreMP"),
    ]:
        ws.merge_cells(f"A{r}:C{r}")
        ws.merge_cells(f"D{r}:H{r}")
        cell(ws, r, 1, label, bold=True, fill=model_fill)
        cell(ws, r, 4, value, fill=model_fill)

    # ── 빈 행 ─────────────────────────────────────────────────
    ws.append([])
    ws.append([])

    # ── 컬럼 헤더 (행 7~8) ────────────────────────────────────
    HEADERS = [
        "No", "BOM\nLevel", "Part Type",
        "Base P/No", "New P/No",
        "부품명\nClass Desc.(Part Name)",
        "Q'ty\nBase", "Q'ty\nNew",
        "변경점\nChanging Point",
        "변경사유\nChanging Reason",
        "양산처\nSupplier\n(CKD 유무 포함)",
        "신규/변경\n부품 대상\nClassification",
        "금형\n개발/수정\nMold Dev/\nModify\n(○/X)",
        "사내\n제작\nIn-house\nProd.\n(○/X)",
        "부품 인정시험\n실시 여부\nPart Approval\nTest\n(○/X)",
    ]
    HDR_ROW = 7
    for col_i, h in enumerate(HEADERS, 1):
        c = cell(ws, HDR_ROW, col_i, h, bold=True, fill=hdr_fill)
        c.alignment = Alignment(horizontal="center", vertical="center",
                                wrap_text=True)

    # ── 데이터 행 ─────────────────────────────────────────────
    DATA_START = HDR_ROW + 1
    for i, row in enumerate(rows):
        r = DATA_START + i
        cls = row.get("Classification", "")
        data = [
            row["No"],
            row["BOM_Level"],
            row["Part_Type"],
            row["Base_PNo"],
            row["New_PNo"],
            row["Class_Desc"],
            row["Qty_Base"],
            row["Qty_New"],
            row["Changing_Point"],
            row["Changing_Reason"],
            row["Supplier"],
            cls,
            "X",   # 금형
            "○",   # 사내제작
            "X",   # 부품인정시험
        ]
        aligns = [center, center, left, center, center,
                  left, center, center, left, left,
                  center, center, center, center, center]
        for col_i, (val, aln) in enumerate(zip(data, aligns), 1):
            cell(ws, r, col_i, val, align=aln)

    # ── 컬럼 너비 ─────────────────────────────────────────────
    col_widths = [5, 8, 18, 16, 16, 28, 7, 7, 30, 35, 16, 14, 10, 10, 12]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 헤더 행 높이
    ws.row_dimensions[HDR_ROW].height = 60
    for r in range(DATA_START, DATA_START + len(rows)):
        ws.row_dimensions[r].height = 30

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
