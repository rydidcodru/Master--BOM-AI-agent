"""Phase 6 — 평가 골격 + Phase 3 change_event Recall@5 골든셋.

retrieval Top-k recall/precision/MRR + L3 룰 발화 FP(오발화) 측정. 목표치는 placeholder
— **측정 인프라 자체가 산출물**.

Phase 3: change_event(구조 B)가 적재되어 골든 라벨을 자동 도출한다.
  한 change_event = 한 사유로 함께 바뀐 부품 세트(change_line) → (사유 텍스트, 부품 세트)가
  골든 케이스. ``golden_cases_from_change_events``로 DB에서 빌드, ``evaluate_event_retrieval``
  로 search_events에 대한 Recall@k 측정. 이는 **silver**(자기검색 일관성)이다 — 질의를 같은
  이벤트에서 도출하므로 진짜 relevance가 아니라 검색 모드 비교·튜닝용. 사람 검증 골든은 별도.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from src.agent.impact.models import ImpactVerdict

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def recall_at_k(retrieved: list[str], golden: set[str], k: int) -> float:
    if not golden:
        return 0.0
    return len(set(retrieved[:k]) & golden) / len(golden)


def precision_at_k(retrieved: list[str], golden: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(retrieved[:k]) & golden) / k


def mrr(retrieved: list[str], golden: set[str]) -> float:
    for i, pno in enumerate(retrieved):
        if pno in golden:
            return 1.0 / (i + 1)
    return 0.0


def rule_fp_rate(verdicts: list[ImpactVerdict], should_keep: set[str]) -> float:
    """should_keep(유지여야 할) 부품 중 action != KEEP 비율 = 룰 오발화율."""
    relevant = [v for v in verdicts if v.part_no in should_keep]
    if not relevant:
        return 0.0
    fp = sum(1 for v in relevant if v.action != "KEEP")
    return fp / len(relevant)


@dataclass
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr: float
    k: int


def evaluate_retrieval(retrieved: list[str], golden: set[str], *, k: int = 5) -> RetrievalMetrics:
    return RetrievalMetrics(
        recall_at_k=recall_at_k(retrieved, golden, k),
        precision_at_k=precision_at_k(retrieved, golden, k),
        mrr=mrr(retrieved, golden),
        k=k,
    )


@dataclass
class GoldenCase:
    text: str
    golden_pnos: set[str]
    should_keep: set[str] = field(default_factory=set)


# 수기 미니 골든셋 — 사람 검증용(선택). 기본 평가는 change_event에서 자동 도출
# (golden_cases_from_change_events)한 silver 셋을 쓴다.
GOLDEN_MINI: list[GoldenCase] = []


# ════════════════════════════════════════════════════════════════
# Phase 3 — change_event 기반 Recall@5 골든셋 (silver, 자기검색 일관성).
# 한 change_event = 한 사유로 함께 바뀐 부품 세트 → (사유 질의, 부품 세트) 골든 케이스.
# ════════════════════════════════════════════════════════════════


@dataclass
class GoldenEventCase:
    """change_event 1건에서 도출한 골든 케이스.

    Attributes:
        event_id: 출처 change_event.
        query: 검색 질의(= change_log + change_reason, reason_embedding 내용과 일치).
        golden_pnos: 이 사유로 함께 바뀐 부품 세트(change_line.new_pno|base_pno).
        base_model / new_model: 표기·필터용(검색 질의에는 미포함).
    """

    event_id: int
    query: str
    golden_pnos: set[str]
    base_model: str | None = None
    new_model: str | None = None


def golden_cases_from_change_events(
    session: Session,
    *,
    min_parts: int = 1,
    file_ids: list[int] | None = None,
) -> list[GoldenEventCase]:
    """적재된 change_event/change_line → GoldenEventCase 목록 (순수 ORM, LLM 0).

    query = (change_log + " " + change_reason).strip() — reason_embedding 내용과 동일 구성.
    golden_pnos = 해당 이벤트 라인의 new_pno(없으면 base_pno). ``min_parts`` 미만은 제외
    (세트로서 의미 있는 케이스만). ``file_ids``로 스코프 한정 가능.

    Returns:
        event_id 오름차순 GoldenEventCase 목록(결정론).
    """
    from sqlalchemy import select

    from src.db.models import ChangeEvent, ChangeLine

    ev_stmt = select(ChangeEvent)
    if file_ids:
        ev_stmt = ev_stmt.where(ChangeEvent.file_id.in_(file_ids))
    events = session.execute(ev_stmt.order_by(ChangeEvent.event_id)).scalars().all()

    cases: list[GoldenEventCase] = []
    for ev in events:
        lines = session.execute(
            select(ChangeLine).where(ChangeLine.event_id == ev.event_id)
        ).scalars().all()
        pnos = {
            p.strip()
            for ln in lines
            if (p := (ln.new_pno or ln.base_pno or "")).strip()
        }
        if len(pnos) < min_parts:
            continue
        query = " ".join(
            x for x in ((ev.change_log or "").strip(), (ev.change_reason or "").strip()) if x
        ).strip()
        if not query:
            continue
        cases.append(
            GoldenEventCase(
                event_id=ev.event_id,
                query=query,
                golden_pnos=pnos,
                base_model=ev.base_model,
                new_model=ev.new_model,
            )
        )
    return cases


@dataclass
class EventRetrievalMetrics:
    """search_events Recall 집계 (이벤트 적중 + 부품 세트 커버리지)."""

    n_cases: int
    event_recall_at_k: float  # 골든 이벤트가 top-k 이벤트에 들어온 비율
    event_mrr: float          # 골든 이벤트 순위의 역수 평균
    part_recall_at_k: float   # top-k 이벤트 부품합집합 ∩ 골든부품 / 골든부품 (평균)
    k: int


def evaluate_event_retrieval(
    cases: Iterable[GoldenEventCase],
    *,
    retrieve: Callable[[str, int], list[int]],
    expand: Callable[[int], list[str]],
    k: int = 5,
) -> EventRetrievalMetrics:
    """주입형 search_events 평가 — 네트워크/DB 비의존(테스트는 fake 주입).

    Args:
        cases: GoldenEventCase 목록.
        retrieve: (query, top_k) → 순위순 event_id 목록 (운영=backend.search_events).
        expand: event_id → 부품번호 목록 (운영=lookup_lines_by_event → new_pno).
        k: top-k.

    Returns:
        :class:`EventRetrievalMetrics`. cases가 비면 0.

    NOTE: silver — retrieve가 골든 이벤트 자신을 포함할 수 있어 자기검색 일관성을 잰다.
    """
    cases = list(cases)
    if not cases:
        return EventRetrievalMetrics(0, 0.0, 0.0, 0.0, k)

    ev_recall_sum = 0.0
    ev_mrr_sum = 0.0
    part_recall_sum = 0.0
    for c in cases:
        top_events = list(retrieve(c.query, k))[:k]
        top_ids = [str(e) for e in top_events]
        ev_recall_sum += recall_at_k(top_ids, {str(c.event_id)}, k)
        ev_mrr_sum += mrr(top_ids, {str(c.event_id)})

        retrieved_parts: list[str] = []
        seen: set[str] = set()
        for eid in top_events:
            for p in expand(eid):
                if p and p not in seen:
                    seen.add(p)
                    retrieved_parts.append(p)
        if c.golden_pnos:
            part_recall_sum += len(seen & c.golden_pnos) / len(c.golden_pnos)

    n = len(cases)
    return EventRetrievalMetrics(
        n_cases=n,
        event_recall_at_k=ev_recall_sum / n,
        event_mrr=ev_mrr_sum / n,
        part_recall_at_k=part_recall_sum / n,
        k=k,
    )


def dump_golden_cases(cases: Iterable[GoldenEventCase], path: Path | str) -> int:
    """GoldenEventCase 목록 → JSONL 저장(set은 정렬 리스트로). 저장 건수 반환."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(
                json.dumps(
                    {
                        "event_id": c.event_id,
                        "query": c.query,
                        "golden_pnos": sorted(c.golden_pnos),
                        "base_model": c.base_model,
                        "new_model": c.new_model,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n


def load_golden_cases(path: Path | str) -> list[GoldenEventCase]:
    """JSONL → GoldenEventCase 목록 (dump_golden_cases 역변환)."""
    out: list[GoldenEventCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(
            GoldenEventCase(
                event_id=d["event_id"],
                query=d["query"],
                golden_pnos=set(d.get("golden_pnos") or []),
                base_model=d.get("base_model"),
                new_model=d.get("new_model"),
            )
        )
    return out
