"""L2 멀티쿼리 확장 (opt-in, LLM query expansion / multi-query retrieval).

검색 1단계(search_events) 전에, '변경점' 텍스트를 LLM이 **부품군·동의어·핵심사유** 중심의
재작성 쿼리 2~3개로 확장한다 — multi-query / RAG-Fusion 계열. 원본·결정론 쿼리를 항상 앞에
보존하고 LLM 재작성을 뒤에 더해, RRF 융합(propose_candidates)의 입력 질의 집합을 넓혀 recall을
올린다. **HyDE 아님** — 가상 문서를 생성하지 않고 질의 표현만 바꾼다(CLAUDE.md ② multi-query).

원칙(CLAUDE.md 준수):
 - 변경내용/변경사유 텍스트만 확장한다. part_no/model 같은 **식별자를 새로 주입하지 않는다**
   (절대원칙: 검색 키는 변경내역+변경사유; [[feedback-search-by-reason-not-id]]).
 - 기본 OFF(결정론 단일/3쿼리가 기본). ``LLM_PROVIDER`` opt-in + ``expand=True``일 때만 호출.
 - LLM 실패/파싱오류 시 입력 ``base_queries`` 그대로(결정론 fallback, 쿼리 손실 0).
"""
from __future__ import annotations

from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)

_SYSTEM = (
    "너는 가전 BOM 변경영향 분석의 검색 질의 재작성기다. '새 변경점' 한국어 설명을 받아, "
    "과거 유사 변경사례를 더 잘 찾기 위한 재작성 검색 문구를 만든다. 각 문구는 부품군·동의어·"
    "핵심 변경사유 중심으로 표현을 바꾼 것이어야 한다. "
    "품번(P/No)·모델코드 같은 식별자를 새로 지어내거나 넣지 말고, 변경 내용/사유 의미만 다룬다. "
    "가상의 사례 문서를 만들지 말고 짧은 검색 문구만 낸다."
)


def _norm_key(s: str) -> str:
    return " ".join((s or "").split()).lower()


def expand_queries(
    query: str,
    llm: Any,
    *,
    base_queries: list[str] | None = None,
    max_extra: int = 3,
    max_total: int = 6,
) -> list[str]:
    """LLM 멀티쿼리 확장. base_queries(원본/결정론)를 앞에 보존, LLM 재작성을 뒤에 추가.

    Args:
        query: 새 변경점 텍스트(변경내용 + 변경사유, 식별자 제외).
        llm: ``complete_json`` 인터페이스 LLM(없으면 base_queries 그대로).
        base_queries: 항상 보존할 결정론 쿼리(없으면 ``[query]``).
        max_extra: LLM 재작성 최대 개수.
        max_total: 최종 질의 집합 상한(RRF 입력 폭주 방지).

    Returns:
        중복 제거된 질의 리스트(base 우선, 그다음 신규 재작성). 실패 시 base 그대로.
    """
    base = [q for q in (base_queries or ([query] if query else [])) if (q or "").strip()]
    if llm is None or not (query or "").strip() or max_extra <= 0:
        return base[:max_total]

    prompt = (
        f'새 변경점: "{query}"\n\n'
        f"위 변경과 같은 부품군/변경유형/사유의 과거 사례를 찾기 위한 재작성 검색 문구 "
        f"{max_extra}개를 만들어라(서로 다른 표현·동의어·부품군 일반화). "
        '반드시 JSON만: {"queries": ["문구1", "문구2", ...]}'
    )
    try:
        data = llm.complete_json(prompt, system=_SYSTEM)
        raw = data.get("queries", []) if isinstance(data, dict) else []
        extra = [str(q).strip() for q in raw if str(q).strip()][:max_extra]
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 결정론 fallback
        log.warning("l2.expand_failed", error=str(exc)[:160])
        return base[:max_total]

    out: list[str] = []
    seen: set[str] = set()
    for q in [*base, *extra]:
        k = _norm_key(q)
        if k and k not in seen:
            seen.add(k)
            out.append(q)
    log.info("l2.expand.done", base=len(base), extra=len(extra), total=len(out[:max_total]))
    return out[:max_total]
