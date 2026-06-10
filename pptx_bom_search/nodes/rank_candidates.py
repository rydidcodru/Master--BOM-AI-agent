from __future__ import annotations

import json

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from state import BOMSearchState

SYSTEM_PROMPT = """당신은 LG전자 오븐/주방가전 BOM 변경 이력 전문가입니다.

새 심의회에서 나온 변경점 하나와, Neo4j에서 검색된 과거 이력 후보들을 비교하여
의미적으로 유사한 후보를 선정하고 순위를 매겨주세요.

판단 기준:
1. 부품 종류가 같거나 유사한가 (ex. Motor ↔ Motor Assembly, Fan ↔ Fan,Convection)
2. 변경 행위가 유사한가 (ex. AC→BLDC 교체 ↔ BLDC 모터 적용)
3. 변경 목적/사유가 유사한가 (ex. 요리 성능 개선 ↔ 신규 BLDC 모터 적용)

반환 형식 (JSON 배열만, 설명 없이):
[
  {{
    "rank": 1,
    "changeId": "...",
    "partName": "...",
    "level": 1,
    "partType": "...",
    "changingPoint": "...",
    "changingReason": "...",
    "classification": "...",
    "basePartNoRaw": "...",
    "newPartNoRaw": "...",
    "modelName": "...",
    "similarity_reason": "BLDC 모터로의 교체라는 변경 행위와 성능 개선 목적이 일치"
  }}
]

규칙:
- 상위 5건만 반환 (없으면 있는 만큼)
- 전혀 관련 없는 후보는 제외
- similarity_reason은 1~2문장으로 구체적으로 작성
- changeId는 후보 원본 그대로 복사"""

HUMAN_TEMPLATE = """=== 새 심의회 변경점 ===
부품명: {part}
변경내역: {change_detail}
변경사유: {change_reason}
모듈: {module}

=== 과거 이력 후보 ({count}건) ===
{candidates_text}

위 후보들 중 새 심의회 변경점과 의미적으로 유사한 것을 선정하고 순위를 매겨주세요."""

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


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"[{i}] changeId={c.get('changeId', '')}\n"
            f"    부품: {c.get('partName', '')} | level={c.get('level', '')} | {c.get('classification', '')}\n"
            f"    changingPoint: {c.get('changingPoint', '')}\n"
            f"    changingReason: {c.get('changingReason', '')}\n"
            f"    baseP/No: {c.get('basePartNoRaw', '')} → newP/No: {c.get('newPartNoRaw', '')}\n"
            f"    model: {c.get('modelName', '')}"
        )
    return "\n\n".join(lines)


def _safe_parse(raw) -> list[dict]:
    import re
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("items", "results", "candidates", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return [raw]
    if isinstance(raw, str):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return []


def rank_candidates_node(state: BOMSearchState) -> dict:
    search_results = state.get("search_results", [])
    if not search_results:
        return {}

    chain = _get_chain()
    updated = []

    for sr in search_results:
        cp = sr["change_point"]
        candidates = sr.get("candidates", [])

        # 후보 없으면 스킵
        if not candidates:
            sr["ranked"] = []
            updated.append(sr)
            continue

        part = cp.get("part", "")
        print(f"[rank] '{part}' — 후보 {len(candidates)}건 LLM 판단 중...")

        try:
            raw = chain.invoke({
                "part": part,
                "change_detail": cp.get("change_detail", ""),
                "change_reason": cp.get("change_reason", ""),
                "module": cp.get("module", ""),
                "count": len(candidates),
                "candidates_text": _format_candidates(candidates),
            })
            ranked = _safe_parse(raw)
        except Exception as e:
            print(f"  [WARNING] LLM 랭킹 실패 ({part}): {e}")
            ranked = []

        # changeId로 원본 candidates 역매핑 → lineId/documentId 보완
        cand_map = {c.get("changeId"): c for c in candidates if c.get("changeId")}
        for r in ranked:
            orig = cand_map.get(r.get("changeId"), {})
            r.setdefault("lineId",     orig.get("lineId", ""))
            r.setdefault("documentId", orig.get("documentId", ""))

        sr["ranked"] = ranked
        print(f"  → 유사 후보 {len(ranked)}건 선정")
        updated.append(sr)

    return {"search_results": updated}
