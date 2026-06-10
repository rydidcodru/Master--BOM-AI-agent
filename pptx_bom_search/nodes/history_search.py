from __future__ import annotations

"""
과거 변경이력 조회 노드.

흐름:
  1. change_point의 base_part_no로 Neo4j CSV에서 과거 케이스 검색
  2. 케이스별 변경 부품 목록 구성
  3. LLM: Top 5 케이스 선정 + 선정이유 + 연동 부품 판단 (1회 호출)
  4. 규칙 기반 1차 필터 후 LLM 최종 판단
  5. change_point에 history_candidates 채워서 반환
"""

import csv
import json
import re
from pathlib import Path

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from state import BOMSearchState, HistoryCandidate, LinkedPart

# Neo4j CSV 경로 (ETL_neo4j 출력)
_CSV_DIR = Path(__file__).parent.parent.parent / "ETL_neo4j" / "outputs" / "neo4j_csv"

# 체결/라벨류 제외 키워드 (소문자)
_SKIP_KEYWORDS = {
    "nut", "screw", "washer", "bolt", "rivet", "label", "barcode",
    "rating", "carton", "warranty", "card", "manual", "bag", "tape",
    "clip", "pin", "ring", "seal", "gasket",
}

SYSTEM_PROMPT = """당신은 LG전자 오븐/주방가전 BOM 변경이력 분석 전문가입니다.

현재 부품 변경점 하나와, 그 부품이 과거에 변경된 케이스 목록이 주어집니다.
아래 두 가지를 한 번에 수행하세요:

1. 현재 변경점과 가장 유사한 과거 케이스 Top 5를 선정하고 선정이유를 작성
2. 각 선정된 케이스에서 "현재 변경점에 누락되었을 가능성이 있는 연동 부품"을 판단

반환 형식 (JSON 배열, 설명 없이):
[
  {{
    "case_id": "case:xxxx",
    "rank": 1,
    "select_reason": "BLDC 전환 시 Cover,Heater가 동시 변경된 사례로 현재 변경과 동일 패턴",
    "linked_parts": [
      {{
        "part_no": "EAU65090301",
        "part_name": "Motor Assembly,BLDC",
        "change_type": "추가",
        "relevance_reason": "BLDC Motor 적용 시 AC Fan Motor 대체 부품으로 항상 동시 추가됨"
      }}
    ]
  }}
]

규칙:
- rank는 1(가장 유사)~5 순서
- select_reason: 현재 변경점과 어떤 점이 유사한지 1문장
- linked_parts: 현재 변경점 목록에 없는 부품 중 이번 변경에서도 필요할 것 같은 것만
- 체결류(Nut, Screw, Washer), 라벨류(Label, Barcode)는 linked_parts에서 제외
- 관련 없는 부품은 linked_parts에 넣지 말 것
- linked_parts가 없으면 빈 배열 []
- 같은 모델의 케이스가 중복 선정되지 않도록 할 것 (모델명이 같으면 1개만)
- 후보 케이스가 5개 미만이면 있는 것만 반환"""

HUMAN_TEMPLATE = """=== 현재 변경점 ===
부품명: {part}
변경내역: {change_detail}
변경사유: {change_reason}
변경유형: {change_type}
Base P/No: {base_part_no}

=== 현재 변경점 목록 (이미 포함된 부품들) ===
{current_parts}

=== 과거 이력 케이스 후보 ===
{case_list}

Top 5를 선정하고 각 케이스의 연동 부품을 판단해주세요."""

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


def _load_csv_data() -> tuple[dict, dict, dict]:
    """
    CSV 로드.
    반환:
      cases: {caseId → {modelName, baseModel}}
      records: {caseId → [{basePartNoRaw, newPartNoRaw, changingPoint, changingReason, lineId}]}
      lines: {lineId → {partNameRaw, partType, ...}}
    """
    cases: dict[str, dict] = {}
    with open(_CSV_DIR / "review_cases.csv") as f:
        for r in csv.DictReader(f):
            cases[r["caseId"]] = {
                "model_name": r.get("modelName", ""),
                "base_model": r.get("baseModel", ""),
            }

    records: dict[str, list[dict]] = {}
    with open(_CSV_DIR / "change_records.csv") as f:
        for r in csv.DictReader(f):
            cid = r["caseId"]
            records.setdefault(cid, []).append({
                "base_pno":       r.get("basePartNoRaw", ""),
                "new_pno":        r.get("newPartNoRaw", ""),
                "changing_point": r.get("changingPoint", ""),
                "changing_reason": r.get("changingReason", ""),
                "line_id":        r.get("lineId", ""),
                "classification": r.get("classification", ""),
            })

    lines: dict[str, dict] = {}
    with open(_CSV_DIR / "bom_lines.csv") as f:
        for r in csv.DictReader(f):
            lines[r["lineId"]] = {
                "part_name": r.get("partNameRaw", ""),
                "part_type": r.get("partType", ""),
            }

    return cases, records, lines


def _find_related_cases(base_part_no: str,
                        records: dict[str, list[dict]]) -> list[str]:
    """base_part_no가 포함된 caseId 목록 반환."""
    result = []
    for cid, recs in records.items():
        for r in recs:
            if r["base_pno"] == base_part_no or r["new_pno"] == base_part_no:
                result.append(cid)
                break
    return result


def _is_skip_part(part_name: str) -> bool:
    """체결/라벨류 여부 판단."""
    lower = part_name.lower()
    return any(k in lower for k in _SKIP_KEYWORDS)


def _build_case_summary(cid: str,
                        cases: dict,
                        records: dict,
                        lines: dict,
                        subtree_pnos: set[str],
                        current_pnos: set[str]) -> dict:
    """
    케이스 요약 구성.
    linked_part_candidates: 규칙 기반 1차 필터 통과한 연동 부품 후보
    """
    meta = cases.get(cid, {})
    case_records = records.get(cid, [])

    # 변경 부품 목록 구성
    changed_parts = []
    for r in case_records:
        line = lines.get(r["line_id"], {})
        pname = line.get("part_name", "")
        base  = r["base_pno"]
        new   = r["new_pno"]

        # change_type 추론
        if base in ("-", "", None) and new not in ("-", "", None, "X"):
            ctype = "추가"
        elif new in ("X", "x") or (new == "" and base not in ("", "-")):
            ctype = "삭제"
        else:
            ctype = "변경"

        changed_parts.append({
            "base_pno": base,
            "new_pno":  new,
            "name":     pname,
            "type":     ctype,
            "point":    r["changing_point"],
        })

    # 연동 부품 후보: 규칙 기반 1차 필터
    linked_candidates = []
    for p in changed_parts:
        pno  = p["new_pno"] if p["type"] == "추가" else p["base_pno"]
        name = p["name"]

        # 현재 subtree나 change_points에 이미 있으면 제외
        if pno in subtree_pnos or pno in current_pnos:
            continue
        if not name or _is_skip_part(name):
            continue

        linked_candidates.append({
            "part_no":     pno,
            "part_name":   name,
            "change_type": p["type"],
            "point":       p["point"],
        })

    return {
        "case_id":    cid,
        "model_name": meta.get("model_name", ""),
        "base_model": meta.get("base_model", ""),
        "changed_parts_count": len(changed_parts),
        "linked_candidates": linked_candidates,
    }


def _format_case_list(case_summaries: list[dict]) -> str:
    lines = []
    for cs in case_summaries:
        lines.append(
            f"케이스ID: {cs['case_id']} | 모델: {cs['model_name']} → {cs['base_model']} | "
            f"총 변경부품: {cs['changed_parts_count']}개"
        )
        if cs["linked_candidates"]:
            lines.append("  연동부품후보:")
            for p in cs["linked_candidates"][:15]:
                lines.append(
                    f"    - [{p['change_type']}] {p['part_no']:15s} {p['part_name']:30s}"
                    + (f" | {p['point'][:40]}" if p['point'] else "")
                )
        else:
            lines.append("  연동부품후보: 없음")
        lines.append("")
    return "\n".join(lines)


def _format_current_parts(change_points: list[dict]) -> str:
    lines = []
    for cp in change_points:
        pno = cp.get("base_part_no", "")
        if pno:
            lines.append(f"  {pno:16s} | {cp.get('part','')}")
    return "\n".join(lines) if lines else "  (없음)"


def history_search_node(state: BOMSearchState) -> dict:
    change_points = state.get("change_points", [])
    if not change_points:
        return {"change_points": []}

    print("[history_search] CSV 로드 중...")
    cases, records, lines = _load_csv_data()

    # 현재 change_points의 base_part_no 전체 집합
    current_pnos: set[str] = {
        cp.get("base_part_no", "") for cp in change_points
        if cp.get("base_part_no", "")
    }

    updated = []
    for i, cp in enumerate(change_points):
        base_pno = cp.get("base_part_no", "")

        # 매칭 안 됐거나 NEW면 스킵
        if not base_pno or not cp.get("bom_matched", False):
            cp.setdefault("history_candidates", [])
            updated.append(cp)
            continue

        print(f"[history_search] [{i}] {cp.get('part','')} ({base_pno}) 과거이력 조회 중...")

        # 관련 케이스 검색
        related_cids = _find_related_cases(base_pno, records)
        if not related_cids:
            print(f"  → 관련 케이스 없음")
            cp["history_candidates"] = []
            updated.append(cp)
            continue

        print(f"  → 관련 케이스 {len(related_cids)}개 발견")

        # 현재 subtree의 part_no 집합
        subtree_pnos: set[str] = {
            r["part_no"] for r in cp.get("matched_subtree", [])
        }

        # 케이스별 요약 구성
        case_summaries = [
            _build_case_summary(cid, cases, records, lines, subtree_pnos, current_pnos)
            for cid in related_cids
        ]

        # LLM 호출 — Top 5 선정 + 연동 부품 판단
        try:
            raw = _get_chain().invoke({
                "part":           cp.get("part", ""),
                "change_detail":  cp.get("change_detail", ""),
                "change_reason":  cp.get("change_reason", ""),
                "change_type":    cp.get("type", ""),
                "base_part_no":   base_pno,
                "current_parts":  _format_current_parts(change_points),
                "case_list":      _format_case_list(case_summaries),
            })
            results = _safe_parse(raw)
        except Exception as e:
            print(f"  [WARNING] LLM 호출 실패: {e}")
            results = []

        # HistoryCandidate 구성
        candidates: list[HistoryCandidate] = []
        for r in results[:5]:
            cid    = r.get("case_id", "")
            meta   = cases.get(cid, {})
            lparts: list[LinkedPart] = []
            for lp in r.get("linked_parts", []):
                lparts.append(LinkedPart(
                    part_no=lp.get("part_no", ""),
                    part_name=lp.get("part_name", ""),
                    change_type=lp.get("change_type", ""),
                    relevance_reason=lp.get("relevance_reason", ""),
                ))
            candidates.append(HistoryCandidate(
                case_id=cid,
                model_name=meta.get("model_name", r.get("model_name", "")),
                base_model=meta.get("base_model", ""),
                rank=r.get("rank", len(candidates) + 1),
                select_reason=r.get("select_reason", ""),
                linked_parts=lparts,
            ))

        cp["history_candidates"] = candidates
        linked_count = sum(len(c["linked_parts"]) for c in candidates)
        print(f"  → Top {len(candidates)}개 케이스 선정 | 연동 부품 후보 총 {linked_count}개")
        updated.append(cp)

    return {"change_points": updated}