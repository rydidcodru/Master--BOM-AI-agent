"""
write_bom 노드.

확정된 변경점(selections) + base BOM → 새 BOM xlsx 생성.

처리 순서:
  1. base BOM 전체 행을 로드 (원본 컬럼 구조 그대로)
  2. deleted_parts → 해당 부품 + 하위 트리 전체 행 제거
  3. changed parts → Part No / Description 컬럼에 new_part_no_confirmed 반영,
                     Change 컬럼에 변경 이유 기록
  4. linked_parts (added_linked_parts) → 해당 부모 부품 바로 아래 행 삽입
  5. 변경 행에 색상 마킹 후 xlsx 저장
"""
from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

# ── 컬럼 인덱스 (0-based, 헤더 행 기준) ─────────────────────────────────────
COL_PART_NO   = 1
COL_LVL       = 2
COL_IS        = 3
COL_PARENT    = 9
COL_NAME      = 10
COL_DESC      = 11
COL_QTY       = 13
COL_UOM       = 14
COL_SUPPLY    = 16
COL_MAKER     = 27
COL_CHANGE    = 7   # "Change" 컬럼 — 변경 사유 기록

FILL_CHANGED = PatternFill("solid", fgColor="FFF3CD")   # 노란
FILL_ADDED   = PatternFill("solid", fgColor="D4EDDA")   # 초록
FILL_DELETED = PatternFill("solid", fgColor="F8D7DA")   # 빨강


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _v(row: tuple, idx: int) -> str:
    if idx >= len(row) or row[idx] is None:
        return ""
    s = str(row[idx]).strip()
    return "" if s.lower() in ("nan", "none") else s


def _collect_subtree(root_pno: str, children: dict[str, list[str]]) -> set[str]:
    """root_pno 포함 모든 하위 part_no 집합."""
    result: set[str] = set()
    stack = [root_pno]
    while stack:
        cur = stack.pop()
        result.add(cur)
        stack.extend(children.get(cur, []))
    return result


def _expand_preserve_by_desc(
    preserve_pnos: set[str],
    all_rows: list[list[Any]],
) -> set[str]:
    """
    LLM이 지정한 preserve_parts 품번의 description에서 소재 키워드를 추출하여
    동일 소재류 품번을 전체 BOM에서 확장.
    예) RAB33372905 (Paint,Powder) → 모든 Paint,Powder / POWDER,ENAMEL 품번 추가
        (Paint ↔ Powder ↔ ENAMEL은 같은 소재 계열로 묶음)
    """
    if not preserve_pnos:
        return preserve_pnos

    # 소재 계열 동의어 그룹 — LLM이 하나만 지정해도 같은 그룹은 전부 보존
    _MATERIAL_GROUPS = [
        {"powder", "enamel", "paint"},
        {"resin", "eps", "pe", "pp", "abs", "pbt", "rubber"},
        {"coil", "steel", "alcot"},
        {"adhesive", "tape", "grease", "oil"},
    ]

    def _desc_keywords(desc: str) -> set[str]:
        return {w.lower() for w in desc.replace(",", " ").split() if len(w) > 2}

    # 보존 품번들의 키워드 수집 → 해당하는 소재 그룹 확정
    active_groups: list[set[str]] = []
    seed_descs: set[str] = set()

    for row in all_rows:
        pno  = str(row[COL_PART_NO] or "").strip()
        desc = str(row[COL_DESC]    or "").strip()
        if pno not in preserve_pnos or not desc:
            continue
        seed_descs.add(desc.lower())
        kws = _desc_keywords(desc)
        for grp in _MATERIAL_GROUPS:
            if kws & grp and grp not in active_groups:
                active_groups.append(grp)

    # 전체 BOM에서 active_groups 또는 seed_descs에 해당하는 품번 확장
    expanded = set(preserve_pnos)
    for row in all_rows:
        pno  = str(row[COL_PART_NO] or "").strip()
        desc = str(row[COL_DESC]    or "").strip()
        if not pno or not desc:
            continue
        desc_low = desc.lower()
        kws = _desc_keywords(desc)
        # exact desc 매칭 또는 소재 그룹 매칭
        if desc_low in seed_descs or any(kws & grp for grp in active_groups):
            expanded.add(pno)

    return expanded


def _make_new_row(
    n_cols: int,
    part_no: str,
    lvl: str,
    parent_no: str,
    description: str,
    qty: str = "1",
    uom: str = "EA",
    change_note: str = "",
) -> list[Any]:
    """신규 행 리스트 생성 (원본 컬럼 수에 맞춤)."""
    row: list[Any] = [None] * n_cols
    row[COL_PART_NO] = part_no
    row[COL_LVL]     = lvl
    row[COL_PARENT]  = parent_no
    row[COL_NAME]    = part_no
    row[COL_DESC]    = description
    row[COL_QTY]     = qty
    row[COL_UOM]     = uom
    row[COL_CHANGE]  = change_note
    return row


# ── 변경 계획 분석 (프리뷰/빌드 공통) ───────────────────────────────────────

def analyze_bom_changes(
    base_bom_path: str,
    change_points: list[dict],
    selections: list[dict],
) -> tuple[list[Any], list[tuple[list[Any], str, str]]]:
    """
    base BOM + 변경점 → (header, [(row_data, status, change_note), ...]) 반환.
    xlsx 생성 없이 변경 계획만 계산. /bom/build API에서 프리뷰용으로 사용.
    status: "" | "변경" | "추가" | "삭제"
    change_note: AI 추천 문구
    """
    wb_src = openpyxl.load_workbook(base_bom_path, read_only=True, data_only=True)
    ws_src = wb_src.active
    all_rows: list[list[Any]] = []
    header: list[Any] = []
    for i, row in enumerate(ws_src.iter_rows(values_only=True)):
        if i == 0:
            header = list(row)
        else:
            all_rows.append(list(row))
    wb_src.close()

    pno_to_idx: dict[str, int] = {}
    children:   dict[str, list[str]] = {}
    for idx, row in enumerate(all_rows):
        pno    = _v(row, COL_PART_NO)
        parent = _v(row, COL_PARENT)
        if pno:
            pno_to_idx[pno] = idx
        if pno and parent:
            children.setdefault(parent, []).append(pno)

    pno_to_cp:  dict[str, dict] = {}
    pno_to_sel: dict[str, dict] = {}
    preserve_pnos: set[str] = set()

    for cp in change_points:
        bpno = cp.get("base_part_no", "")
        if bpno:
            pno_to_cp[bpno] = cp
        for pno in cp.get("preserve_parts", []):
            preserve_pnos.add(pno)

    preserve_pnos = _expand_preserve_by_desc(preserve_pnos, all_rows)
    for pno in list(preserve_pnos):
        preserve_pnos.update(_collect_subtree(pno, children))

    for sel in selections:
        if not sel.get("skipped"):
            bpno = sel.get("base_part_no", "")
            if bpno:
                pno_to_sel[bpno] = sel

    case_change_map:  dict[str, tuple[str, str]] = {}
    case_add_list:    list[tuple[str, str, str, str]] = []
    case_delete_pnos: set[str] = set()

    for sel in selections:
        if sel.get("skipped"):
            continue
        cp_idx   = sel.get("change_point_idx", -1)
        sel_bpno = sel.get("base_part_no", "")
        selected_cases = set(sel.get("selected_cases", []))
        if not selected_cases:
            continue
        cp = change_points[cp_idx] if 0 <= cp_idx < len(change_points) else {}
        parent_pno  = cp.get("base_part_no", sel_bpno)
        subtree_pnos = {p["part_no"] for p in cp.get("matched_subtree", []) if p.get("part_no")}

        for cand in cp.get("history_candidates", []):
            if cand.get("master_id") not in selected_cases:
                continue
            note_prefix = f"[이력#{cand.get('rank','')}] "
            for p in cand.get("case_parts", []):
                base_p = (p.get("base_part_no") or "").strip().strip("-")
                new_p  = (p.get("new_part_no")  or "").strip().strip("-")
                pname  = p.get("part_name", "")
                detail = p.get("changing_point", "") or cp.get("change_detail", "")
                note   = note_prefix + detail
                if base_p and new_p:
                    if base_p in pno_to_idx and (not subtree_pnos or base_p in subtree_pnos):
                        if base_p not in case_change_map:
                            case_change_map[base_p] = (new_p, note)
                elif not base_p and new_p:
                    if new_p not in pno_to_idx:
                        case_add_list.append((parent_pno, pname, f"TBD({new_p})", note))
                elif base_p and not new_p:
                    if base_p in pno_to_idx and (not subtree_pnos or base_p in subtree_pnos):
                        case_delete_pnos.update(_collect_subtree(base_p, children) - preserve_pnos)

    delete_pnos: set[str] = set(case_delete_pnos)
    for cp in change_points:
        for dp in cp.get("deleted_parts", []):
            dpno = dp.get("part_no", "")
            if dpno:
                delete_pnos.update(_collect_subtree(dpno, children) - preserve_pnos)

    for sel in selections:
        if sel.get("skipped"):
            continue
        bpno = sel.get("base_part_no", "")
        cp = pno_to_cp.get(bpno, {})
        if cp.get("change_scope") == "full" and bpno:
            delete_pnos.update(_collect_subtree(bpno, children) - preserve_pnos)

    delete_pnos -= set(case_change_map.keys())

    # 변경 전 part_no 기록 (변경 행에서 before/after 표시용)
    result: list[tuple[list[Any], str, str]] = []

    for row in all_rows:
        pno = _v(row, COL_PART_NO)
        if pno in delete_pnos:
            r = list(row); r[COL_CHANGE] = "삭제"
            result.append((r, "삭제", _v(row, COL_CHANGE) or "삭제"))
            continue
        if pno in case_change_map:
            new_pno, note = case_change_map[pno]
            r = list(row); r[COL_PART_NO] = new_pno; r[COL_NAME] = new_pno; r[COL_CHANGE] = note
            # 원본 part_no를 별도 필드로 추가 (before/after 표시용)
            result.append((r, "변경", note))
            # row에 원본 part_no 기록
            result[-1][0].append(pno)  # 마지막 원소 = original_part_no
            continue
        if pno in pno_to_sel:
            sel = pno_to_sel[pno]
            cp  = pno_to_cp.get(pno, {})
            npno = sel.get("new_part_no_confirmed") or sel.get("new_part_no", "")
            if cp.get("change_scope") == "partial" and npno and npno not in ("TBD", ""):
                r = list(row); r[COL_PART_NO] = npno; r[COL_NAME] = npno
                r[COL_CHANGE] = sel.get("change_detail", "변경")
                result.append((r, "변경", r[COL_CHANGE]))
                result[-1][0].append(pno)
                continue
            # scope 없어도 base→new 직접 교체
            if npno and npno not in ("TBD", "") and pno in pno_to_idx:
                r = list(row); r[COL_PART_NO] = npno; r[COL_NAME] = npno
                r[COL_CHANGE] = sel.get("change_detail", "변경")
                result.append((r, "변경", r[COL_CHANGE]))
                result[-1][0].append(pno)
                continue
        result.append((list(row), "", ""))

    seen_add: set[str] = set()
    for parent_pno, pname, new_pno, note in case_add_list:
        if not new_pno or new_pno in seen_add:
            continue
        seen_add.add(new_pno)
        parent_row = all_rows[pno_to_idx[parent_pno]] if parent_pno in pno_to_idx else None
        parent_lvl = _v(parent_row, COL_LVL) if parent_row else ".1"
        child_lvl  = {"0": ".1", ".1": "..2", "..2": "...3"}.get(parent_lvl, "..2")
        r = _make_new_row(len(header), new_pno or "TBD", child_lvl, parent_pno, pname, change_note=note)
        result.append((r, "추가", note))

    for sel in selections:
        if sel.get("skipped"):
            continue
        parent_pno = sel.get("base_part_no", "")
        for lp in sel.get("added_linked_parts", []):
            lpno  = lp.get("base_part_no") or lp.get("new_part_no") or "TBD"
            ldesc = lp.get("part_name", "")
            note  = f"연동부품 추가 — {lp.get('relevance_reason','')}"
            r = _make_new_row(len(header), lpno, "..2", parent_pno, ldesc, change_note=note)
            result.append((r, "추가", note))

    return header, result


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def write_bom(
    base_bom_path: str,
    change_points: list[dict],
    selections: list[dict],
    output_path: str | None = None,
) -> bytes:
    """
    base BOM + 확정 변경점 → 새 BOM xlsx bytes 반환.
    output_path 지정 시 파일로도 저장.

    처리 우선순위:
      A. deleted_parts 명시 삭제 (서브트리 포함)
      B. change_scope=full → 모듈 서브트리 삭제 + history case_parts로 추가/변경
      C. change_scope=partial → case_parts로 인라인 변경/삭제/추가
      D. added_linked_parts → 신규 행 삽입
    """
    # ── 1. base BOM 로드 ────────────────────────────────────────────────────
    wb_src = openpyxl.load_workbook(base_bom_path, read_only=True, data_only=True)
    ws_src = wb_src.active
    all_rows: list[list[Any]] = []
    header: list[Any] = []

    for i, row in enumerate(ws_src.iter_rows(values_only=True)):
        if i == 0:
            header = list(row)
            continue
        all_rows.append(list(row))

    wb_src.close()
    n_cols = len(header)

    # part_no 인덱스 및 부모-자식 관계 구성
    pno_to_idx: dict[str, int] = {}          # part_no → all_rows 인덱스
    children:   dict[str, list[str]] = {}    # parent_pno → [child_pno, ...]

    for idx, row in enumerate(all_rows):
        pno    = _v(row, COL_PART_NO)
        parent = _v(row, COL_PARENT)
        if pno:
            pno_to_idx[pno] = idx
        if pno and parent:
            children.setdefault(parent, []).append(pno)

    # ── 2. cp / sel 인덱스 구성 ─────────────────────────────────────────────
    pno_to_cp:  dict[str, dict] = {}
    pno_to_sel: dict[str, dict] = {}
    preserve_pnos: set[str] = set()

    # cp는 같은 base_part_no가 여러 개 올 수 있으므로 리스트로 관리
    pno_to_cps: dict[str, list[dict]] = {}

    for cp in change_points:
        bpno = cp.get("base_part_no", "")
        if bpno:
            pno_to_cp[bpno] = cp
            pno_to_cps.setdefault(bpno, []).append(cp)
        for pno in cp.get("preserve_parts", []):
            preserve_pnos.add(pno)

    preserve_pnos = _expand_preserve_by_desc(preserve_pnos, all_rows)
    subtree_preserve: set[str] = set()
    for pno in list(preserve_pnos):
        subtree_preserve.update(_collect_subtree(pno, children))
    preserve_pnos = preserve_pnos | subtree_preserve

    for sel in selections:
        if sel.get("skipped"):
            continue
        bpno = sel.get("base_part_no", "")
        if bpno:
            pno_to_sel[bpno] = sel

    # ── 3. history case_parts 기반 변경 계획 수집 ───────────────────────────
    # case_change_map[base_pno] = (new_pno, note)  — base가 현재 BOM에 있을 때만
    # case_add_list  = [(parent_pno, part_name, new_pno, note)]  — base 없는 순수 신규
    # case_delete_pnos = {pno}  — new가 없는 명시 삭제
    #
    # 핵심 필터: case_parts의 base_part_no가 현재 base BOM에 없으면 무시
    # (다른 모델 이력 케이스의 부품이 잘못 추가되는 것 방지)
    case_change_map:  dict[str, tuple[str, str]] = {}
    case_add_list:    list[tuple[str, str, str, str]] = []
    case_delete_pnos: set[str] = set()

    for sel in selections:
        if sel.get("skipped"):
            continue
        cp_idx   = sel.get("change_point_idx", -1)
        sel_bpno = sel.get("base_part_no", "")
        selected_cases = set(sel.get("selected_cases", []))
        if not selected_cases:
            continue

        cp = change_points[cp_idx] if 0 <= cp_idx < len(change_points) else {}
        parent_pno = cp.get("base_part_no", sel_bpno)

        # 이 cp의 matched_subtree part_no 집합 — 같은 모듈 범위 내 부품만 처리
        subtree_pnos = {p["part_no"] for p in cp.get("matched_subtree", []) if p.get("part_no")}

        for cand in cp.get("history_candidates", []):
            if cand.get("master_id") not in selected_cases:
                continue
            note_prefix = f"[이력#{cand.get('rank','')}] "

            for p in cand.get("case_parts", []):
                base_p = (p.get("base_part_no") or "").strip().strip("-")
                new_p  = (p.get("new_part_no")  or "").strip().strip("-")
                pname  = p.get("part_name", "")
                detail = p.get("changing_point", "") or cp.get("change_detail", "")
                note   = note_prefix + detail

                if base_p and new_p:
                    # base가 현재 BOM에 있고 해당 모듈 서브트리 안에 있어야 변경 적용
                    if base_p in pno_to_idx and (not subtree_pnos or base_p in subtree_pnos):
                        if base_p not in case_change_map:
                            case_change_map[base_p] = (new_p, note)
                elif not base_p and new_p:
                    # 순수 신규: new_p가 이미 BOM에 없어야 하고,
                    # history 이력의 P/No를 그대로 적용하면 다른 모델 부품이 섞일 수 있으므로
                    # P/No는 TBD로 남기고 참고 정보만 기록
                    if new_p not in pno_to_idx:
                        case_add_list.append((parent_pno, pname, f"TBD({new_p})", note))
                elif base_p and not new_p:
                    # 명시 삭제: base가 현재 BOM + 모듈 범위 안에 있어야 함
                    if base_p in pno_to_idx and (not subtree_pnos or base_p in subtree_pnos):
                        case_delete_pnos.update(_collect_subtree(base_p, children) - preserve_pnos)

    # ── 4. 삭제 대상 최종 수집 ──────────────────────────────────────────────
    delete_pnos: set[str] = set(case_delete_pnos)

    # A. deleted_parts 명시 삭제
    for cp in change_points:
        for dp in cp.get("deleted_parts", []):
            dpno = dp.get("part_no", "")
            if dpno:
                delete_pnos.update(_collect_subtree(dpno, children) - preserve_pnos)

    # B. change_scope=full → 모듈 서브트리 삭제
    #    (case_parts에서 새 부품을 추가하므로 서브트리 전체를 비워야 함)
    full_scope_pnos: set[str] = set()  # full replace 대상 모듈 루트
    for sel in selections:
        if sel.get("skipped"):
            continue
        bpno = sel.get("base_part_no", "")
        cp = pno_to_cp.get(bpno, {})
        if cp.get("change_scope") == "full" and bpno:
            full_scope_pnos.add(bpno)
            delete_pnos.update(_collect_subtree(bpno, children) - preserve_pnos)

    # case_change_map에서 변경 대상 부품은 삭제 대상에서 제외 (인라인 변경 처리)
    delete_pnos -= set(case_change_map.keys())

    # ── 5. 행 처리 ──────────────────────────────────────────────────────────
    result_rows: list[tuple[list[Any], str]] = []

    for row in all_rows:
        pno = _v(row, COL_PART_NO)

        if pno in delete_pnos:
            deleted_row = list(row)
            deleted_row[COL_CHANGE] = "삭제"
            result_rows.append((deleted_row, "삭제"))
            continue

        if pno in case_change_map:
            new_pno, note = case_change_map[pno]
            new_row = list(row)
            new_row[COL_PART_NO] = new_pno
            new_row[COL_NAME]    = new_pno
            new_row[COL_CHANGE]  = note
            result_rows.append((new_row, "변경"))
            continue

        # partial scope: new_part_no_confirmed 직접 지정된 경우
        if pno in pno_to_sel:
            sel = pno_to_sel[pno]
            cp  = pno_to_cp.get(pno, {})
            npno = sel.get("new_part_no_confirmed") or sel.get("new_part_no", "")
            if cp.get("change_scope") == "partial" and npno and npno not in ("TBD", ""):
                new_row = list(row)
                new_row[COL_PART_NO] = npno
                new_row[COL_NAME]    = npno
                new_row[COL_CHANGE]  = sel.get("change_detail", "변경")
                result_rows.append((new_row, "변경"))
                continue

        result_rows.append((list(row), ""))

    # ── 6. case_add_list → 신규 행 삽입 ────────────────────────────────────
    seen_add: set[str] = set()
    for parent_pno, pname, new_pno, note in case_add_list:
        if not new_pno or new_pno in seen_add:
            continue
        seen_add.add(new_pno)
        # 부모의 레벨을 찾아 하위 레벨 계산
        parent_row = all_rows[pno_to_idx[parent_pno]] if parent_pno in pno_to_idx else None
        parent_lvl = _v(parent_row, COL_LVL) if parent_row else ".1"
        child_lvl  = parent_lvl + ".1" if parent_lvl and not parent_lvl.endswith(".1") else (parent_lvl or "") + "."
        # 단순하게 .1 하위는 ..2 로
        lvl_map_simple = {"0": ".1", ".1": "..2", "..2": "...3"}
        child_lvl = lvl_map_simple.get(parent_lvl, "..2")

        new_row = _make_new_row(
            n_cols, new_pno or "TBD", child_lvl, parent_pno, pname,
            change_note=note,
        )
        result_rows.append((new_row, "추가"))

    # ── 7. added_linked_parts → 신규 행 삽입 ────────────────────────────────
    for sel in selections:
        if sel.get("skipped"):
            continue
        parent_pno = sel.get("base_part_no", "")
        for lp in sel.get("added_linked_parts", []):
            lpno = lp.get("base_part_no") or lp.get("new_part_no") or "TBD"
            ldesc = lp.get("part_name", "")
            new_row = _make_new_row(
                n_cols, lpno, "..2", parent_pno, ldesc,
                change_note=f"연동부품 추가 — {lp.get('relevance_reason','')}"
            )
            result_rows.append((new_row, "추가"))

    # ── 6. xlsx 생성 ────────────────────────────────────────────────────────
    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    ws_out.title = "BOM"

    # 헤더
    for ci, h in enumerate(header, 1):
        cell = ws_out.cell(row=1, column=ci, value=h)
        cell.font = Font(bold=True)

    fill_map = {
        "변경": FILL_CHANGED,
        "추가": FILL_ADDED,
        "삭제": FILL_DELETED,
    }

    for ri, (row_data, status) in enumerate(result_rows, 2):
        fill = fill_map.get(status)
        for ci, val in enumerate(row_data, 1):
            cell = ws_out.cell(row=ri, column=ci, value=val)
            if fill:
                cell.fill = fill
        if status == "삭제":
            for ci in range(1, n_cols + 1):
                ws_out.cell(row=ri, column=ci).font = Font(strike=True, color="888888")

    # 열 너비 자동 조정 (주요 컬럼만)
    ws_out.column_dimensions["B"].width = 24   # Part No
    ws_out.column_dimensions["C"].width = 6    # Lvl
    ws_out.column_dimensions["J"].width = 24   # Parent
    ws_out.column_dimensions["K"].width = 24   # Part Name
    ws_out.column_dimensions["L"].width = 40   # Description
    ws_out.column_dimensions["H"].width = 30   # Change

    buf = io.BytesIO()
    wb_out.save(buf)
    buf.seek(0)
    data = buf.read()

    if output_path:
        Path(output_path).write_bytes(data)

    # 변경 요약 출력
    status_counts = {"변경": 0, "추가": 0, "삭제": 0}
    for _, s in result_rows:
        if s in status_counts:
            status_counts[s] += 1
    print(
        f"[write_bom] 완료: 전체 {len(result_rows)}행 "
        f"(변경 {status_counts['변경']} / 추가 {status_counts['추가']} / 삭제 {status_counts['삭제']})"
    )

    return data