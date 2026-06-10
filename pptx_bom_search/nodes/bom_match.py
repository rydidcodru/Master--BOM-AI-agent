from __future__ import annotations

"""
PPTX 변경점 → Base BOM 부품 매칭 노드.

흐름:
  1. Base BOM Excel 로드 → 부품 목록 + 계층 트리 구성
  2. 레벨 순서대로 LLM 호출 (Level 1 → 2 → 3 → ...)
     - 각 레벨에서 아직 매칭 안 된 변경점만 재시도
     - NEW 타입은 모든 레벨에서 매칭 실패해도 정상
  3. 매칭된 Part No 기준으로 하위 트리 재귀 전개
  4. change_point에 결과 채워 반환
"""

import json
import re
import time
from pathlib import Path

import openpyxl
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from state import BOMSearchState, BomRow

# ── 컬럼 인덱스 (Base BOM ag-grid 포맷 기준, 0-based 헤더 행 제외) ──
COL_PART_NO     = 1
COL_LVL         = 2
COL_IS          = 3
COL_PARENT      = 9
COL_DESCRIPTION = 11
COL_QTY         = 13
COL_UOM         = 14
COL_SUPPLY_TYPE = 16
COL_CKD         = 20
COL_MAKER       = 27
COL_TYPE        = 33
COL_TECH_SPEC   = 12

# 레벨 순서 (점 개수 기준, 특수 레벨은 마지막)
LEVEL_ORDER = [".1", "..2", "...3", "....4", ".....5"]
MAX_LEVEL   = 4   # 최대 탐색 레벨 인덱스 (LEVEL_ORDER 기준)

SYSTEM_PROMPT = """당신은 LG전자 오븐/주방가전 BOM 전문가입니다.
개발심의회 PPTX에서 추출된 부품 변경점 목록과 Base BOM 부품 목록이 주어집니다.
각 변경점이 Base BOM의 어느 부품에 해당하는지 매칭해주세요.

반환 형식 (JSON 배열만, 설명 없이):
[
  {{
    "idx": 0,
    "base_part_no": "MCK71660202",
    "confidence": "high",
    "reason": "PPTX의 'Conv. Fan Cover'는 Base BOM의 'Cover,Heater'와 동일 부품 (팬 커버)"
  }}
]

규칙:
- idx: 입력 변경점의 원래 순서 번호 (변경 없이 그대로 사용)
- base_part_no: Base BOM의 Part No. 매칭 불가 시 "" (빈 문자열)
- confidence: high / medium / low
- reason: 매칭 근거 1문장
- type이 "NEW"인 경우: Base BOM에 없는 신규 부품이므로 base_part_no=""가 자연스러움
- type이 "Changing" 또는 "삭제"인 경우: 최대한 Base BOM에서 찾을 것
- 부품명 약어/표기 차이 허용: Conv.→Convection, Assy→Assembly, Fan Cover→Cover,Heater 등
- 동일 부품명이 여러 개면 변경내역/모듈 맥락으로 가장 적합한 것 선택
- 확실하지 않으면 confidence="low"로 표시하되 최선의 후보를 반환할 것"""

HUMAN_TEMPLATE = """=== 변경점 목록 ===
{change_points_text}

=== Base BOM 부품 목록 (Level {level}) ===
{bom_list_text}

각 변경점을 위 부품 목록에서 매칭해주세요. 매칭이 불가능하면 base_part_no=""로 반환하세요."""

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", HUMAN_TEMPLATE),
        ])
        _chain = prompt | llm | JsonOutputParser()
    return _chain


def _safe_parse(raw) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("items", "results", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
    if isinstance(raw, str):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return []


def _parse_level(lvl: str) -> int:
    """'.1' → 1, '..2' → 2, '...3' → 3, 기타 → 99"""
    if not lvl:
        return 99
    dots = len(lvl) - len(lvl.lstrip("."))
    return dots if dots > 0 else 99


def load_base_bom(bom_path: str) -> tuple[list[BomRow], dict[str, list[BomRow]]]:
    """
    Base BOM Excel 로드.
    반환:
      rows: 전체 행 리스트
      children_map: parent_part_no → [child BomRow, ...] 매핑
    """
    wb = openpyxl.load_workbook(bom_path, read_only=True, data_only=True)
    ws = wb.active

    rows: list[BomRow] = []
    children_map: dict[str, list[BomRow]] = {}

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue

        def _v(idx: int) -> str:
            v = row[idx] if idx < len(row) else None
            if v is None:
                return ""
            s = str(v).strip()
            return "" if s.lower() == "nan" else s

        part_no = _v(COL_PART_NO)
        if not part_no:
            continue

        bom_row: BomRow = {
            "part_no":        part_no,
            "lvl":            _v(COL_LVL),
            "is_order":       int(_v(COL_IS)) if _v(COL_IS).isdigit() else 0,
            "parent_part_no": _v(COL_PARENT),
            "description":    _v(COL_DESCRIPTION),
            "qty":            _v(COL_QTY),
            "uom":            _v(COL_UOM),
            "maker":          _v(COL_MAKER),
            "part_type":      _v(COL_TYPE),
            "supply_type":    _v(COL_SUPPLY_TYPE),
            "ckd":            _v(COL_CKD),
            "technical_spec": _v(COL_TECH_SPEC),
        }
        rows.append(bom_row)

        parent = bom_row["parent_part_no"]
        if parent:
            children_map.setdefault(parent, []).append(bom_row)

    return rows, children_map


def _expand_subtree(part_no: str, children_map: dict[str, list[BomRow]],
                    rows_by_pno: dict[str, BomRow], depth: int = 0) -> list[BomRow]:
    """part_no를 루트로 하는 서브트리를 DFS로 전개."""
    if depth > 10:
        return []
    result = []
    root = rows_by_pno.get(part_no)
    if root:
        result.append(root)
    for child in children_map.get(part_no, []):
        result.extend(_expand_subtree(child["part_no"], children_map, rows_by_pno, depth + 1))
    return result


def _format_bom_list(rows: list[BomRow]) -> str:
    """LLM 입력용 Base BOM 목록 텍스트 생성 — Part No + Description만."""
    lines = []
    for r in rows:
        lines.append(f"{r['part_no']} | {r['description']}")
    return "\n".join(lines)


def _format_change_points(indexed_cps: list[tuple[int, dict]]) -> str:
    """LLM 입력용 변경점 텍스트. (원래 idx, cp) 쌍을 받아 idx 유지."""
    lines = []
    for orig_idx, cp in indexed_cps:
        lines.append(
            f"[{orig_idx}] type={cp.get('type','')} | module={cp.get('module','')} | "
            f"part={cp.get('part','')} | "
            f"변경내역={cp.get('change_detail','')} | "
            f"변경사유={cp.get('change_reason','')}"
        )
    return "\n".join(lines)


def _llm_match(indexed_cps: list[tuple[int, dict]],
               bom_rows: list[BomRow],
               level_label: str) -> dict[int, dict]:
    """
    LLM 호출 → {원래 idx: 매칭 결과} 반환.
    매칭 실패 항목은 결과에 포함되지 않음.
    """
    if not indexed_cps or not bom_rows:
        return {}

    cp_text  = _format_change_points(indexed_cps)
    bom_text = _format_bom_list(bom_rows)

    try:
        raw = _get_chain().invoke({
            "change_points_text": cp_text,
            "bom_list_text":      bom_text,
            "level":              level_label,
        })
        results = _safe_parse(raw)
    except Exception as e:
        print(f"  [WARNING] LLM 호출 실패 (Level {level_label}): {e}")
        return {}

    matched: dict[int, dict] = {}
    for r in results:
        idx = r.get("idx", -1)
        pno = r.get("base_part_no", "")
        if idx >= 0 and pno:
            matched[idx] = r
    return matched


def _get_descendant_pnos(part_no: str, children_map: dict[str, list[BomRow]]) -> set[str]:
    """part_no 하위 전체 자손 Part No 집합 반환 (자신 포함)."""
    result = {part_no}
    for child in children_map.get(part_no, []):
        result |= _get_descendant_pnos(child["part_no"], children_map)
    return result


def bom_match_node(state: BOMSearchState) -> dict:
    change_points = state.get("change_points", [])
    base_bom_path = state.get("base_bom_path", "")

    if not change_points:
        return {"change_points": []}

    # Base BOM 없으면 기본값만 채워서 반환
    if not base_bom_path or not Path(base_bom_path).exists():
        print("[bom_match] Base BOM 없음 — 매칭 스킵")
        for cp in change_points:
            cp.setdefault("base_part_no", "")
            cp.setdefault("bom_level", "")
            cp.setdefault("part_type", "")
            cp.setdefault("qty", "")
            cp.setdefault("supplier", "")
            cp.setdefault("supply_type", "")
            cp.setdefault("ckd", "")
            cp.setdefault("bom_matched", False)
            cp.setdefault("match_confidence", "")
            cp.setdefault("match_reason", "")
            cp.setdefault("matched_subtree", [])
        return {"change_points": change_points}

    print(f"[bom_match] Base BOM 로드 중: {Path(base_bom_path).name}")
    rows, children_map = load_base_bom(base_bom_path)
    rows_by_pno: dict[str, BomRow] = {r["part_no"]: r for r in rows}

    # 레벨별 행 그룹화
    rows_by_level: dict[int, list[BomRow]] = {}
    for r in rows:
        lv = _parse_level(r["lvl"])
        rows_by_level.setdefault(lv, []).append(r)

    print(f"[bom_match] {len(rows)}행 로드 완료 | 레벨별: "
          + ", ".join(f"L{lv}={len(v)}행" for lv, v in sorted(rows_by_level.items())))

    # 매칭 결과 누적 {원래 idx → 매칭 결과}
    # "확정"과 "임시(상위 어셈블리)" 를 구분해서 관리
    final_match: dict[int, dict] = {}      # 확정된 매칭
    tentative_match: dict[int, dict] = {}  # 상위 레벨에서 잡힌 임시 매칭

    pending: list[tuple[int, dict]] = list(enumerate(change_points))

    for level_idx, level_str in enumerate(LEVEL_ORDER[:MAX_LEVEL + 1]):
        lv_num = level_idx + 1
        bom_at_level = rows_by_level.get(lv_num, [])

        if not bom_at_level:
            continue

        # 아직 확정 안 된 변경점만 시도
        still_pending = [(i, cp) for i, cp in pending if i not in final_match]
        if not still_pending:
            break

        print(f"[bom_match] Level {lv_num} ({level_str}) — "
              f"BOM {len(bom_at_level)}행 vs 미확정 {len(still_pending)}개 변경점")

        matched = _llm_match(still_pending, bom_at_level, level_str)

        for i, m in matched.items():
            pno = m.get("base_part_no", "")
            if not pno:
                continue

            if i in tentative_match:
                # 이미 상위 레벨에서 임시 매칭된 상태
                # 새 매칭이 임시 매칭의 자손이면 → 더 구체적이므로 교체 확정
                tentative_pno = tentative_match[i].get("base_part_no", "")
                descendants = _get_descendant_pnos(tentative_pno, children_map)
                if pno in descendants:
                    print(f"  [{change_points[i].get('part','')}] 정밀화: "
                          f"{tentative_pno} → {pno} ({rows_by_pno.get(pno,{}).get('description','')})")
                    final_match[i] = m
                else:
                    # 자손 관계 아님 → 기존 임시 매칭 확정하고 새 것은 무시
                    final_match[i] = tentative_match[i]
            else:
                # 처음 매칭됨 — 하위에서 더 정밀한 게 나올 수 있으므로 임시 보류
                tentative_match[i] = m

        newly_confirmed = [i for i in matched if i in final_match]
        newly_tentative = [i for i in matched if i not in final_match and i in tentative_match]
        if newly_confirmed:
            print(f"  → 확정 {len(newly_confirmed)}개: "
                  + ", ".join(f"[{i}]{change_points[i].get('part','')}" for i in newly_confirmed))
        if newly_tentative:
            print(f"  → 임시 {len(newly_tentative)}개 (하위 탐색 계속): "
                  + ", ".join(f"[{i}]{change_points[i].get('part','')}" for i in newly_tentative))

        # TPM 초과 방지 — 레벨 간 짧은 대기
        if level_idx < MAX_LEVEL:
            time.sleep(2)

    # 마지막 레벨까지 임시로만 남은 것 → 확정
    for i, m in tentative_match.items():
        if i not in final_match:
            final_match[i] = m
            print(f"  [{change_points[i].get('part','')}] 임시→확정 (하위에서 정밀화 없음): "
                  f"{m.get('base_part_no','')}")

    # 결과 채우기
    updated = []
    for i, cp in enumerate(change_points):
        m        = final_match.get(i, {})
        base_pno = m.get("base_part_no", "")
        matched  = bool(base_pno)
        bom_row  = rows_by_pno.get(base_pno, {})

        subtree: list[BomRow] = []
        if matched:
            subtree = _expand_subtree(base_pno, children_map, rows_by_pno)
            print(f"  [{cp.get('part','')}] → {base_pno} ({bom_row.get('description','')}) "
                  f"하위 {len(subtree)}행 | confidence={m.get('confidence','')}")
        else:
            print(f"  [{cp.get('part','')}] → 매칭 없음 (type={cp.get('type','')})")

        cp["base_part_no"]     = base_pno
        cp["bom_level"]        = bom_row.get("lvl", "")
        cp["part_type"]        = bom_row.get("part_type", "")
        cp["qty"]              = bom_row.get("qty", "")
        cp["supplier"]         = bom_row.get("maker", "")
        cp["supply_type"]      = bom_row.get("supply_type", "")
        cp["ckd"]              = bom_row.get("ckd", "")
        cp["bom_matched"]      = matched
        cp["match_confidence"] = m.get("confidence", "")
        cp["match_reason"]     = m.get("reason", "")
        cp["matched_subtree"]  = subtree
        updated.append(cp)

    matched_cnt = sum(1 for cp in updated if cp["bom_matched"])
    print(f"[bom_match] 완료: {matched_cnt}/{len(updated)}개 매칭")
    return {"change_points": updated}