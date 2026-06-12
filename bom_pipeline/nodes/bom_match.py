"""
BOM 매핑 노드.

타입A (base_part_no 있음):
  → base_bom에서 해당 part_no 확인 + 하위 트리 전개

타입B (base_part_no 없음):
  ① pptx module 키워드로 base_bom .1 레벨 자동 매핑
     실패 시 → LLM이 .1 목록 보고 매핑
  ② 매핑된 .1 part_no 하위 트리 전개
  ③ LLM: 트리 + change_detail → 실제 변경 부품 특정 → base_part_no 채움
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import openpyxl
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from state import BOMPipelineState

# ── Base BOM 컬럼 인덱스 (0-based) ───────────────────────────────────────
COL_PART_NO   = 1
COL_LVL       = 2
COL_PARENT    = 9
COL_DESC      = 11
COL_QTY       = 13
COL_UOM       = 14
COL_SUPPLY    = 16
COL_CKD       = 20
COL_MAKER     = 27
COL_TYPE      = 33


# ── Base BOM 로드 ─────────────────────────────────────────────────────────

class BomRow:
    __slots__ = ("part_no", "lvl", "parent_no", "description",
                 "qty", "uom", "maker", "part_type", "supply_type", "ckd")

    def __init__(self, part_no, lvl, parent_no, description,
                 qty, uom, maker, part_type, supply_type, ckd):
        self.part_no     = part_no
        self.lvl         = lvl
        self.parent_no   = parent_no
        self.description = description
        self.qty         = qty
        self.uom         = uom
        self.maker       = maker
        self.part_type   = part_type
        self.supply_type = supply_type
        self.ckd         = ckd

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def load_base_bom(bom_path: str) -> tuple[list[BomRow], dict[str, list[BomRow]]]:
    """
    base_bom.xlsx 로드.
    반환: (전체 행 리스트, parent_no → [child BomRow] 매핑)
    """
    wb = openpyxl.load_workbook(bom_path, read_only=True, data_only=True)
    ws = wb.active

    rows: list[BomRow] = []
    children: dict[str, list[BomRow]] = {}

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue

        def _v(idx: int) -> str:
            v = row[idx] if idx < len(row) else None
            if v is None:
                return ""
            s = str(v).strip()
            return "" if s.lower() in ("nan", "none") else s

        part_no = _v(COL_PART_NO)
        if not part_no:
            continue

        r = BomRow(
            part_no    = part_no,
            lvl        = _v(COL_LVL),
            parent_no  = _v(COL_PARENT),
            description= _v(COL_DESC),
            qty        = _v(COL_QTY),
            uom        = _v(COL_UOM),
            maker      = _v(COL_MAKER),
            part_type  = _v(COL_TYPE),
            supply_type= _v(COL_SUPPLY),
            ckd        = _v(COL_CKD),
        )
        rows.append(r)
        if r.parent_no:
            children.setdefault(r.parent_no, []).append(r)

    wb.close()
    return rows, children


def expand_subtree(part_no: str,
                   rows_by_pno: dict[str, BomRow],
                   children: dict[str, list[BomRow]],
                   depth: int = 0) -> list[dict]:
    """part_no 루트의 하위 트리를 DFS로 전개."""
    if depth > 10:
        return []
    result = []
    root = rows_by_pno.get(part_no)
    if root:
        result.append(root.to_dict())
    for child in children.get(part_no, []):
        result.extend(expand_subtree(child.part_no, rows_by_pno, children, depth + 1))
    return result


# ── 타입B 1단계: module → .1 자동 매핑 ───────────────────────────────────

def _auto_map_module(module: str, level1_rows: list[BomRow]) -> list[BomRow]:
    """
    pptx 모듈명 키워드로 .1 레벨 부품 자동 매핑.
    - 모듈명의 모든 단어를 AND 조건으로 검색 (첫 단어만 → 전체 단어)
    - 실패 시 첫 단어만으로 재시도
    """
    words = [w.lower() for w in module.split() if len(w) > 1]

    # 전체 단어 AND 매칭
    results = [
        r for r in level1_rows
        if all(w in r.description.lower() for w in words)
    ]
    if results:
        return results

    # 첫 단어만으로 재시도
    if words:
        results = [r for r in level1_rows if words[0] in r.description.lower()]
    return results


# ── LLM: .1 매핑 실패 시 폴백 ────────────────────────────────────────────

_MAP_SYSTEM = """당신은 LG전자 BOM 전문가입니다.
pptx의 모듈명과 base_bom의 .1 레벨 부품 목록이 주어집니다.
이 모듈명에 해당하는 .1 레벨 부품을 골라 JSON 배열로만 반환하세요.

반환 형식:
[{{"part_no": "ABU74574508", "description": "Cavity Assembly,Coating"}}]

규칙:
- 확실하지 않아도 가능성 있는 것 모두 포함
- 모듈명과 부품명이 완전히 일치하지 않아도 의미상 같으면 포함
  예) "Out Case Assembly" → "Cover Assembly,Rear" (외장 케이스 의미 동일)
  예) "Controller" → "Controller Assembly,Tact Dial"
- 반드시 1개 이상 반환할 것 (가장 유사한 것이라도)
- 설명 없이 JSON만 출력"""

_MAP_HUMAN = """모듈명: {module}

.1 레벨 부품 목록:
{level1_text}"""

_IDENTIFY_SYSTEM = """당신은 LG전자 BOM 전문가입니다.
pptx 변경내역과 base_bom 하위 트리, 그리고 BOM 전체 .1 레벨 목록이 주어집니다.
이 변경내역이 어느 부품에 해당하는지 특정하여 JSON 배열로만 반환하세요.

반환 형식:
[
  {{
    "part_no": "ABU34863907",
    "description": "Cavity Assembly,welding",
    "lvl": "..2",
    "reason": "치수 변경은 welding 어셈블리에서 발생",
    "change_scope": "partial",
    "change_type": "변경",
    "preserve_parts": []
  }}
]

change_scope 값:
- "partial" : 트리 일부 부품만 변경
- "full"    : 모듈 전체 교체 (치수 변경, 모델 변경 등 모듈 전반에 영향)

change_type 값:
- "변경" : 부품 사양 변경
- "삭제" : 부품 삭제 (BOM에서 제거)
- "추가" : 신규 부품 추가

preserve_parts:
- change_scope="full" 일 때, 해당 트리 내에서 어셈블리가 교체되더라도 삭제하면 안 되는 공용 소재/원자재 품번 목록
- 여러 어셈블리에서 공용으로 사용되는 소재류 (예: POWDER,ENAMEL / Paint,Powder / Resin,EPS / Coil,Steel 등)는 어셈블리 교체와 무관하게 BOM에 남아있어야 함
- 트리를 보고 공용 소재/원자재로 판단되는 품번을 배열로 반환
- change_scope="partial" 이거나 해당 없으면 빈 배열 []

규칙:
- 변경내역이 "Size 변경", "치수 변경", "형상 변경", "조립 방식 변경" 등 모듈 전반에 영향이면 .1 루트 자체를 반환하고 change_scope="full", change_type="변경"
- 변경내역에 특정 부품의 "삭제"가 명시된 경우 (예: "Conv Heater 삭제", "Fan 삭제"):
  → 이 경우는 반드시 change_scope="partial" 로 처리 (모듈 전체 교체가 아님)
  1. .1 루트를 change_scope="partial", change_type="변경" 으로 먼저 반환
  2. 하위 트리에서 삭제 대상 부품들을 찾아 change_type="삭제" 로 추가 반환
  3. .1 레벨 전체 목록에서도 키워드 매칭으로 추가 삭제 대상 검색
     (예: "Conv Heater 삭제" → "Heater,Sheath", "Cover,Heater", "Fan,Convection" 등 연관 부품)
  4. 삭제 대상이 여러 개이면 모두 배열에 포함
- 특정 하위 부품이 명확히 지목되는 경우만 해당 부품 반환하고 change_scope="partial"
- 변경내역에서 언급된 부품명(예: Hinge Assembly)이 트리에 있으면 그 부품 반환
- 반드시 1개 이상 반환할 것 (가장 관련성 높은 것)
- 설명 없이 JSON만 출력"""

_IDENTIFY_HUMAN = """변경내역: {change_detail}
변경사유: {change_reason}
모듈: {module}

[모듈 하위 트리]
{tree_text}

[BOM 전체 .1 레벨 목록 (삭제 대상 추가 검색용)]
{level1_text}"""

_llm_map_chain = None
_llm_identify_chain = None


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "rate_limit_exceeded" in msg or "Rate limit" in msg


_llm_retry = retry(
    retry=retry_if_exception(_is_rate_limit),
    wait=wait_exponential(multiplier=1, min=3, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


def _get_map_chain():
    global _llm_map_chain
    if _llm_map_chain is None:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", _MAP_SYSTEM),
            ("human", _MAP_HUMAN),
        ])
        _llm_map_chain = prompt | llm | JsonOutputParser()
    return _llm_map_chain


def _get_identify_chain():
    global _llm_identify_chain
    if _llm_identify_chain is None:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", _IDENTIFY_SYSTEM),
            ("human", _IDENTIFY_HUMAN),
        ])
        _llm_identify_chain = prompt | llm | JsonOutputParser()
    return _llm_identify_chain


def _format_level1_with_lvl(rows: list[BomRow]) -> str:
    return "\n".join(f"{r.part_no} | {r.description}" for r in rows)


def _safe_parse(raw) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("items", "results", "data"):
            if isinstance(raw.get(k), list):
                return raw[k]
    if isinstance(raw, str):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return []


def _format_level1(rows: list[BomRow]) -> str:
    return "\n".join(f"{r.part_no} | {r.description}" for r in rows)


def _format_tree(tree: list[dict]) -> str:
    lines = []
    for r in tree:
        indent = "  " * (int(r.get("lvl", "").count(".") or 1) - 1)
        lines.append(f"{indent}{r['lvl']:8s} | {r['part_no']:20s} | {r['description']}")
    return "\n".join(lines)


# ── 노드 ──────────────────────────────────────────────────────────────────

def _match_one(
    i: int,
    cp: dict,
    rows_by_pno: dict[str, "BomRow"],
    children: dict[str, list["BomRow"]],
    level1_rows: list["BomRow"],
    level1_text: str,
) -> tuple[int, dict]:
    """change_point 1개 매핑 처리. (병렬 실행용)"""
    base_pno = cp.get("base_part_no", "")

    # ── 타입A: part_no 이미 있음 ────────────────────────────────────────
    if base_pno:
        if base_pno in rows_by_pno:
            subtree = expand_subtree(base_pno, rows_by_pno, children)
            cp["matched_subtree"]  = subtree
            cp["match_confidence"] = "high"
            cp["match_reason"]     = "pptx에서 직접 추출된 part_no"
            print(f"  [A][{i}] {cp.get('part',''):30s} → {base_pno} (하위 {len(subtree)}행)")
        else:
            cp["matched_subtree"]  = []
            cp["match_confidence"] = "low"
            cp["match_reason"]     = "base_bom에 해당 part_no 없음"
            print(f"  [A][{i}] {cp.get('part',''):30s} → {base_pno} (base_bom에 없음)")
        return i, cp

    # ── 타입B: part_no 없음 → 3단계 처리 ───────────────────────────────
    module        = cp.get("module", "")
    change_detail = cp.get("change_detail", "")
    change_reason = cp.get("change_reason", "")
    print(f"  [B][{i}] module={module} | {change_detail[:40]}")

    # 1단계: 자동 매핑
    l1_candidates = _auto_map_module(module, level1_rows)

    # 자동 매핑 실패 → LLM 폴백
    if not l1_candidates:
        print(f"    [{i}] → 자동 매핑 실패, LLM 매핑 시도")
        try:
            raw = _llm_retry(_get_map_chain().invoke)({
                "module":       module,
                "level1_text":  level1_text,
            })
            mapped = _safe_parse(raw)
            l1_candidates = [
                rows_by_pno[m["part_no"]]
                for m in mapped
                if m.get("part_no") in rows_by_pno
            ]
        except Exception as e:
            print(f"    [{i}] [WARNING] LLM 매핑 실패: {e}")
            l1_candidates = []

    if not l1_candidates:
        print(f"    [{i}] → .1 매핑 실패, 스킵")
        cp["matched_subtree"]  = []
        cp["match_confidence"] = ""
        cp["match_reason"]     = "모듈 매핑 실패"
        return i, cp

    print(f"    [{i}] → .1 후보: {[r.description for r in l1_candidates]}")

    # 2단계: 하위 트리 전개
    full_tree: list[dict] = []
    for l1 in l1_candidates:
        full_tree.extend(expand_subtree(l1.part_no, rows_by_pno, children))

    if not full_tree:
        cp["matched_subtree"]  = []
        cp["match_confidence"] = ""
        cp["match_reason"]     = "하위 트리 없음"
        return i, cp

    # 3단계: LLM 부품 특정
    try:
        raw = _llm_retry(_get_identify_chain().invoke)({
            "module":        module,
            "change_detail": change_detail,
            "change_reason": change_reason,
            "tree_text":     _format_tree(full_tree),
            "level1_text":   level1_text,
        })
        identified = _safe_parse(raw)
    except Exception as e:
        print(f"    [{i}] [WARNING] LLM 부품 특정 실패: {e}")
        identified = []

    if identified:
        delete_items = [x for x in identified if x.get("change_type") == "삭제"]
        change_items = [x for x in identified if x.get("change_type") != "삭제"]
        best  = change_items[0] if change_items else identified[0]
        scope = best.get("change_scope", "partial")
        pno   = best.get("part_no", "")

        cp["base_part_no"] = pno
        cp["change_scope"] = scope
        cp["match_reason"] = best.get("reason", "")
        cp["change_type"]  = best.get("change_type", "변경")

        if delete_items:
            cp["deleted_parts"] = [
                {"part_no": d.get("part_no",""), "description": d.get("description",""),
                 "lvl": d.get("lvl",""), "reason": d.get("reason","")}
                for d in delete_items
            ]
            print(f"    [{i}] → 삭제 대상 {len(delete_items)}개: {[d.get('description','') for d in delete_items]}")

        if scope == "full":
            l1_root = l1_candidates[0]
            cp["base_part_no"]     = l1_root.part_no
            cp["match_confidence"] = "medium"
            cp["matched_subtree"]  = full_tree
            preserve = best.get("preserve_parts", [])
            if preserve:
                cp["preserve_parts"] = preserve
                print(f"    [{i}] → 보존 소재 {len(preserve)}개: {preserve[:3]}")
            print(f"    [{i}] → 전체교체: {l1_root.part_no} {l1_root.description} (하위 {len(full_tree)}행)")
        else:
            cp["match_confidence"] = "medium"
            cp["matched_subtree"]  = expand_subtree(pno, rows_by_pno, children)
            print(f"    [{i}] → 특정: {pno} {best.get('description','')} (하위 {len(cp['matched_subtree'])}행)")
    else:
        l1_root = l1_candidates[0]
        cp["base_part_no"]     = l1_root.part_no
        cp["match_confidence"] = "low"
        cp["match_reason"]     = ".1 루트로 대체 (하위 부품 특정 실패)"
        cp["change_scope"]     = "full"
        cp["change_type"]      = "변경"
        cp["matched_subtree"]  = full_tree
        print(f"    [{i}] → 특정 실패, .1 루트 사용: {l1_root.part_no} {l1_root.description}")

    return i, cp


def bom_match_node(state: BOMPipelineState) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    change_points = state.get("change_points", [])
    bom_path = state.get("base_bom_path", "")

    if not bom_path or not Path(bom_path).exists():
        print("[bom_match] base_bom 없음 — 매핑 스킵")
        for cp in change_points:
            cp.setdefault("matched_subtree", [])
            cp.setdefault("match_confidence", "")
            cp.setdefault("match_reason", "")
        return {"change_points": change_points}

    print(f"[bom_match] base_bom 로드: {Path(bom_path).name}")
    rows, children = load_base_bom(bom_path)
    rows_by_pno = {r.part_no: r for r in rows}
    level1_rows = [r for r in rows if r.lvl == ".1"]
    level1_text = _format_level1(level1_rows)   # 한 번만 생성, 모든 스레드에서 읽기 공유
    print(f"  총 {len(rows)}행 | .1 레벨 {len(level1_rows)}개")

    type_a = sum(1 for cp in change_points if cp.get("base_part_no"))
    type_b = len(change_points) - type_a
    print(f"  타입A(part_no 있음)={type_a} | 타입B(part_no 없음)={type_b}")

    # 타입A는 LLM 없이 즉시 완료, 타입B만 LLM 호출 — 모두 병렬 처리
    max_workers = min(8, len(change_points))
    results_map: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_match_one, i, cp, rows_by_pno, children, level1_rows, level1_text): i
            for i, cp in enumerate(change_points)
        }
        for future in as_completed(futures):
            try:
                idx, updated_cp = future.result()
                results_map[idx] = updated_cp
            except Exception as e:
                idx = futures[future]
                print(f"[bom_match] [{idx}] [ERROR] {e}")
                cp = change_points[idx]
                cp.setdefault("matched_subtree", [])
                cp.setdefault("match_confidence", "low")
                cp.setdefault("match_reason", f"오류: {e}")
                results_map[idx] = cp

    updated = [results_map[i] for i in range(len(change_points))]

    matched = sum(1 for cp in updated if cp.get("base_part_no"))
    print(f"[bom_match] 완료: {matched}/{len(updated)}개 part_no 확보")
    return {"change_points": updated}