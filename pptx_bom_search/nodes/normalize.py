from __future__ import annotations

import json
import re

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from state import BOMSearchState

SYSTEM_PROMPT = """당신은 제조업 BOM(Bill of Materials) 부품명 정규화 전문가입니다.
부품명 목록을 받아 각 항목에 canonical name(표준명)과 aliases(동의어 목록)를 생성하세요.

반환 형식 (JSON 배열만, 설명 없이):
[
  {{
    "original": "Conv. Motor",
    "canonical": "Convection Fan Motor",
    "aliases": ["Conv. Motor", "Convection Motor", "Fan Motor", "BLDC Motor",
                "컨벡션 모터", "컨벡션 팬 모터", "Fan", "Motor"]
  }}
]

규칙:
1. 약어 풀이: Conv.→Convection, Assy→Assembly, Ctrl→Control, Brkt→Bracket, Temp→Temperature
2. 형식 정규화: "Panel,Control" → canonical "Control Panel" (쉼표+역순을 자연어로)
3. 한국어 동의어: Motor→모터, Fan→팬, Panel→패널, Controller→컨트롤러, Bracket→브라켓, Heater→히터, Assembly→어셈블리
4. 상위 부품도 aliases에 포함: "Fan Cover" → aliases에 "Fan", "Cover" 추가
5. aliases 최대 8개, original이 첫 항목으로 포함
6. canonical은 영문 표준명 (LG BOM 표기: "명사+분류어" 순서)
7. 품번(영숫자 코드) aliases 제외"""

HUMAN_TEMPLATE = "아래 부품명 목록을 정규화해주세요:\n{part_names_json}"

BATCH_SIZE = 25

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
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


def normalize_node(state: BOMSearchState) -> dict:
    change_points = state.get("change_points", [])
    if not change_points:
        return {"change_points": []}

    # 중복 제거 후 배치 정규화
    unique_parts = list(dict.fromkeys(cp["part"] for cp in change_points if cp.get("part")))
    chain = _get_chain()
    norm_map: dict[str, dict] = {}

    for i in range(0, len(unique_parts), BATCH_SIZE):
        batch = unique_parts[i:i + BATCH_SIZE]
        print(f"[normalize] {i+1}~{i+len(batch)} / {len(unique_parts)}개 정규화 중...")
        try:
            raw = chain.invoke({"part_names_json": json.dumps(batch, ensure_ascii=False)})
            items = _safe_parse(raw)
        except Exception as e:
            print(f"[WARNING] 정규화 실패: {e}")
            items = []

        input_set = set(batch)
        for item in items:
            orig = item.get("original", "")
            if orig in input_set:
                norm_map[orig] = item

    # change_points에 canonical_part, aliases 채우기
    updated = []
    for cp in change_points:
        part = cp.get("part", "")
        norm = norm_map.get(part, {})
        cp["canonical_part"] = norm.get("canonical", part)
        cp["aliases"] = norm.get("aliases", [part]) if norm else [part]
        updated.append(cp)

    print(f"[normalize] {len(updated)}개 부품 정규화 완료")
    return {"change_points": updated}
