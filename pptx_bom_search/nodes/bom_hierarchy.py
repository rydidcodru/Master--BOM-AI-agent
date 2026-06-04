from __future__ import annotations

"""
base_bom.xlsx를 파싱하여 부품 계층을 탐색하는 모듈.

주요 함수:
  load_bom(bom_bytes)         : xlsx bytes → dict 리스트
  find_matching_parts(rows, keywords) : 키워드로 부품 검색
  expand_hierarchy(rows, part_no)     : 하위 계층 재귀 전개
"""

import io
from typing import Optional


# ── 내부 상수 ───────────────────────────────────────────────
# base_bom 컬럼 인덱스 (0-based)
_COL_PART_NO    = 1   # Part No
_COL_LVL        = 2   # Lvl (.1, ..2, ...3 ...)
_COL_PARENT_NO  = 9   # Parent Part No(모)
_COL_DESC       = 11  # Description (부품명)
_COL_QTY        = 13  # Qty
_COL_MAKER      = 27  # Maker
_COL_TYPE       = 33  # Type (MechanicalPart, MaterialPart ...)


def load_bom(bom_bytes: bytes) -> list[dict]:
    """
    xlsx bytes를 읽어 전체 BOM 행을 dict 리스트로 반환.

    반환 dict 키:
      part_no, lvl, parent_pno, description, qty, maker, type
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl이 필요합니다: pip install openpyxl")

    wb = openpyxl.load_workbook(io.BytesIO(bom_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # 헤더 스킵
        part_no = str(row[_COL_PART_NO] or "").strip()
        if not part_no:
            continue
        rows.append({
            "part_no":     part_no,
            "lvl":         str(row[_COL_LVL]    or "").strip(),
            "parent_pno":  str(row[_COL_PARENT_NO] or "").strip(),
            "description": str(row[_COL_DESC]   or "").strip(),
            "qty":         row[_COL_QTY],
            "maker":       str(row[_COL_MAKER]  or "").strip(),
            "type":        str(row[_COL_TYPE]   or "").strip(),
        })
    return rows


def find_matching_parts(bom_rows: list[dict], keywords: list[str]) -> list[dict]:
    """
    keywords(canonical_part + aliases)로 BOM Description 매칭.

    매칭 우선순위:
      1차: Description이 keyword와 완전 일치 (case-insensitive)
      2차: Description에 keyword가 포함 (case-insensitive)

    반환: 매칭된 행 리스트 (중복 제거, part_no 기준)
    """
    if not keywords:
        return []

    exact: list[dict] = []
    contains: list[dict] = []
    seen: set[str] = set()

    kw_lower = [k.lower() for k in keywords if k]

    for row in bom_rows:
        desc_lower = row["description"].lower()
        pno = row["part_no"]
        if pno in seen:
            continue

        for kw in kw_lower:
            if not kw:
                continue
            if desc_lower == kw:
                exact.append(row)
                seen.add(pno)
                break
            elif kw in desc_lower or desc_lower in kw:
                contains.append(row)
                seen.add(pno)
                break

    return exact if exact else contains


def expand_hierarchy(bom_rows: list[dict], part_no: str) -> list[dict]:
    """
    part_no를 루트로 하위 계층을 재귀 전개.

    탐색 방법: parent_pno == part_no 인 행을 찾아 재귀.
    반환: [자신 포함 전체 계층 행], lvl 점 개수 오름차순.
    """
    # part_no → row 매핑 (빠른 조회)
    pno_to_row: dict[str, dict] = {r["part_no"]: r for r in bom_rows}

    # parent_pno → children 매핑 (빠른 자식 조회)
    children_map: dict[str, list[dict]] = {}
    for row in bom_rows:
        parent = row["parent_pno"]
        children_map.setdefault(parent, []).append(row)

    result: list[dict] = []
    seen: set[str] = set()

    def _recurse(pno: str) -> None:
        if pno in seen:
            return
        seen.add(pno)
        row = pno_to_row.get(pno)
        if row:
            result.append(row)
        for child in children_map.get(pno, []):
            _recurse(child["part_no"])

    _recurse(part_no)

    # lvl 점 개수 기준 오름차순 정렬 (상위→하위)
    def _lvl_depth(lvl: str) -> int:
        return len(lvl) - len(lvl.lstrip("."))

    # *S*, *Q* 같은 특수 레벨 제외 (실제 BOM 계층 행만)
    result = [r for r in result if r["lvl"] and r["lvl"][0] in ".0123456789"]
    result.sort(key=lambda r: _lvl_depth(r["lvl"]))
    return result


def is_material(row: dict) -> bool:
    """MaterialPart 여부 판단."""
    return "Material" in (row.get("type") or "")


def build_bom_dict(bom_rows: list[dict]) -> dict[str, str]:
    """
    base_bom에서 매칭 사전을 생성한다.

    반환: {normalized_key: original_description}
      normalized_key = description을 소문자로 변환한 값

    용도: PPTX canonical_part → base_bom 실제 Description 변환
    """
    bom_dict: dict[str, str] = {}
    for row in bom_rows:
        desc = row.get("description", "").strip()
        if desc:
            bom_dict[desc.lower()] = desc
    return bom_dict


def resolve_to_bom_description(canonical_part: str,
                                aliases: list[str],
                                bom_dict: dict[str, str]) -> str:
    """
    canonical_part / aliases를 base_bom 사전에서 lookup하여
    base_bom의 실제 Description으로 반환한다.

    매칭 우선순위:
      1차: canonical_part 또는 alias가 bom_dict 키와 exact match
      2차: bom_dict 키 중 canonical_part를 포함하는 것
           (여러 개면 가장 짧은 것 = 상위 Assembly 우선)
      3차: canonical_part의 첫 토큰이 bom_dict 키에 포함되는 것
           (여러 개면 lvl이 낮은 것 우선은 불가 — 짧은 것 우선)
      실패 시: canonical_part 그대로 반환
    """
    if not canonical_part:
        return canonical_part

    keywords = [canonical_part] + (aliases or [])

    # 1차: exact match
    for kw in keywords:
        if kw.lower() in bom_dict:
            return bom_dict[kw.lower()]

    cp_lower = canonical_part.lower()

    # 2차: bom key가 canonical_part를 포함 (예: "Controller Assembly,Tact Dial" ⊃ "Controller Assembly")
    candidates = [
        desc for key, desc in bom_dict.items()
        if cp_lower in key
    ]
    if candidates:
        return min(candidates, key=len)  # 가장 짧은 것 = 상위 Assembly

    # 3차: canonical_part가 bom key를 포함 (예: "Cavity Assembly Coating" ⊃ "Cavity Assembly")
    candidates = [
        desc for key, desc in bom_dict.items()
        if key in cp_lower and len(key) >= 5  # 너무 짧은 키 제외
    ]
    if candidates:
        return max(candidates, key=len)  # 가장 긴 것 = 더 구체적인 매칭

    return canonical_part
