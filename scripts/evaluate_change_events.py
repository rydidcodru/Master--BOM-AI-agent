"""Phase 3 — change_event Recall@5 측정 (silver 골든셋, 자기검색 일관성).

적재된 change_event/change_line에서 (사유 질의, 부품 세트) 골든 케이스를 도출하고
``search_events``(reason_embedding semantic + change_log/reason lexical, RRF)에 대해
이벤트 recall@k + 부품 세트 recall@k + MRR을 측정한다.

  query    = change_log + " " + change_reason  (= reason_embedding 내용)
  golden   = 그 이벤트 change_line의 부품 세트(new_pno|base_pno)
  retrieve = search_events top-k → event_id 목록
  part set = top-k 이벤트의 부품 합집합

silver(사람 라벨 아님): 질의를 같은 이벤트에서 도출하므로 검색 모드 비교·RRF 튜닝용.
ENABLE_EMBEDDING=1 + Ollama bge-m3 + change_event 적재(reason_embedding backfill) 필요.

Usage:
    python -m scripts.evaluate_change_events --k 5 --min-parts 2
    python -m scripts.evaluate_change_events --k 5 --dump data/golden/change_events_silver.jsonl
"""

from __future__ import annotations

import argparse

from src.agent.eval import (
    dump_golden_cases,
    evaluate_event_retrieval,
    golden_cases_from_change_events,
)
from src.db.engine import make_engine, session_factory
from src.db.retrieve import lookup_lines_by_event, search_events
from src.embed.embedder import embedding_enabled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument(
        "--min-parts",
        type=int,
        default=1,
        help="이 미만 부품 수 이벤트는 제외(세트로 의미 있는 케이스만)",
    )
    ap.add_argument("--candidate-pool", type=int, default=30)
    ap.add_argument("--dump", type=str, default=None, help="골든셋 JSONL 저장 경로")
    args = ap.parse_args()

    if not embedding_enabled():
        print("WARNING: ENABLE_EMBEDDING != 1 — search_events semantic 단계가 실패합니다.")

    engine = make_engine()
    SessionLocal = session_factory(engine)
    with SessionLocal() as session:
        cases = golden_cases_from_change_events(session, min_parts=args.min_parts)
        print(f"golden cases: {len(cases)} (min_parts={args.min_parts})")
        if not cases:
            print("change_event가 비었거나 부품 세트가 없습니다. 로더/임베딩을 먼저 실행하세요.")
            return

        if args.dump:
            n = dump_golden_cases(cases, args.dump)
            print(f"dumped {n} cases -> {args.dump}")

        def retrieve(query: str, k: int) -> list[int]:
            hits = search_events(
                session, query, top_k=k, candidate_pool=args.candidate_pool
            )
            return [h.event_id for h in hits]

        def expand(event_id: int) -> list[str]:
            return [
                (ln.new_pno or ln.base_pno or "").strip()
                for ln in lookup_lines_by_event(session, event_id)
                if (ln.new_pno or ln.base_pno or "").strip()
            ]

        m = evaluate_event_retrieval(cases, retrieve=retrieve, expand=expand, k=args.k)
        print(
            f"  n={m.n_cases}  event_recall@{m.k}={m.event_recall_at_k:.3f}  "
            f"event_mrr={m.event_mrr:.3f}  part_recall@{m.k}={m.part_recall_at_k:.3f}"
        )


if __name__ == "__main__":
    main()
