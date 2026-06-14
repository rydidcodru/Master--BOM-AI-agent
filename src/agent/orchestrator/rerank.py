"""L2 후보 LLM 재랭킹 (opt-in, RankGPT-style listwise rerank).

검색 1단계(결정론 RRF: search_events 멀티쿼리 융합)가 회수한 후보 세트를, LLM이 질의 관련도로
2단계 재정렬·중복강등한다 — 'retrieve-then-rerank'(Sun et al. 2023, "Is ChatGPT Good at
Search?" / RankGPT) 표준 패턴. 실측(에이전트 검색 비교)에서 후보 품질을 올린 가장 큰 레버.

원칙(CLAUDE.md 준수):
 - LLM은 후보를 **새로 만들지 않고 주어진 번호만 재정렬** → 신규 P/No 무생성·출처 보존.
 - 기본 OFF(결정론 RRF가 기본). ``LLM_PROVIDER`` opt-in + ``rerank=True``일 때만 호출.
 - LLM 실패/파싱오류 시 원본 RRF 순서로 **결정론 fallback**(후보 누락 0 — LLM이 빠뜨린 건 뒤에 보존).
"""
from __future__ import annotations

from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)

_SYSTEM = (
    "너는 가전 BOM 변경영향 분석의 검색 재랭커다. '새 변경점'과 '과거 변경 후보' 목록을 받아 "
    "관련도 높은 순서로 후보 번호를 재정렬한다. 같은 부품군이거나 같은 변경유형/사유면 관련이 높다. "
    "무관한 후보는 뒤로, 거의 동일한 중복 후보는 하나만 앞에 둔다. "
    "후보를 새로 만들거나 내용을 바꾸지 말고, 주어진 번호만 사용한다."
)


def _brief(cand: Any, max_parts: int = 6) -> str:
    ev = getattr(cand, "event", None)
    lines = getattr(cand, "lines", []) or []
    # 품번 - 부품명을 먼저 노출(new_pno/part_no_new 우선), 그다음 변경점·변경사유.
    parts: list[str] = []
    for ln in lines[:max_parts]:
        pno = (getattr(ln, "new_pno", "") or getattr(ln, "part_no_new", "") or "").strip()
        name = (getattr(ln, "part_name", "") or "").strip()
        label = f"{pno} {name}".strip() if pno else name
        if label:
            parts.append(label)
    point = (getattr(ev, "change_log", "") or getattr(ev, "changepoint", "") or "").strip()
    reason = (getattr(ev, "change_reason", "") or "").strip()
    return f"부품: {', '.join(parts) or '-'} | 변경점: {point or '-'} | 변경사유: {reason or '-'}"


def rerank_candidates(
    query: str,
    candidates: list[Any],
    llm: Any,
    *,
    max_keep: int | None = None,
) -> list[Any]:
    """LLM listwise 재랭킹. 실패 시 원본 순서 보존(결정론 fallback). 후보 재정렬만(무생성).

    Args:
        query: 새 변경점 텍스트(질의).
        candidates: CandidateSet 목록(``event``/``lines`` 속성, duck-typed).
        llm: ``complete_json`` 인터페이스 LLM(없으면 원본 반환).
        max_keep: 상한(지정 시 상위 N만).
    """
    if not candidates or llm is None or len(candidates) == 1:
        return candidates[:max_keep] if max_keep else candidates

    listing = "\n".join(f"{i + 1}. {_brief(c)}" for i, c in enumerate(candidates))
    prompt = (
        f'새 변경점: "{query}"\n\n과거 변경 후보:\n{listing}\n\n'
        "관련도 높은 순으로 후보 번호를 나열하라(무관은 뒤로, 거의 동일한 중복은 하나만). "
        '반드시 JSON만: {"ranked": [번호, ...]}'
    )
    try:
        data = llm.complete_json(prompt, system=_SYSTEM)
        raw = data.get("ranked", [])
        order = [int(x) for x in raw if str(x).strip().lstrip("-").isdigit()]
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 결정론 fallback
        log.warning("l2.rerank_failed", error=str(exc)[:160])
        return candidates[:max_keep] if max_keep else candidates

    n = len(candidates)
    seen: set[int] = set()
    out: list[Any] = []
    for idx in order:
        i = idx - 1
        if 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(candidates[i])
    # LLM이 누락/제외한 후보는 원래 RRF 순서로 뒤에 보존(소스 손실 0 — 안전망).
    for i, c in enumerate(candidates):
        if i not in seen:
            out.append(c)
    log.info("l2.rerank.done", n=n, llm_ranked=len(seen))
    return out[:max_keep] if max_keep else out
