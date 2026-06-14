"""Hybrid retriever demo — semantic / lexical / RRF 3-way compare.

각 쿼리에 대해 세 모달리티 top-5 결과를 나란히 출력. RRF가 어떤 doc을
어떤 모달리티 점수로 끌어올렸는지 가시화 — 한국어 도메인 / 영어 부품명 /
모델코드 / 의미만 매치 / 정확 키워드 매치 등 다양한 쿼리 패턴 테스트.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENABLE_EMBEDDING", "1")

from src.db.engine import make_engine, session_factory  # noqa: E402
from src.db.retrieve import hybrid_search, lexical_search, semantic_search  # noqa: E402


QUERIES = [
    ("내열 강화 패킹 변경", {}, "한국어 도메인 자연어"),
    ("Cavity Assembly coating", {}, "영어 부품명"),
    ("도어 외관 STS 색상 변경", {"event": "Change"}, "한국어 + event 필터"),
    ("WSED7613B 부품", {}, "모델코드 포함 쿼리"),
    ("BOM Level 변경", {"form_id": "bom_ag_grid_36"}, "BOM 한정"),
    ("신규 부품 등록", {"event": "New"}, "이벤트 필터"),
]


def _format(h):
    parts = []
    if h.score_rrf is not None:
        parts.append(f"rrf={h.score_rrf:.4f}")
    if h.score_semantic is not None:
        parts.append(f"sem={h.score_semantic:.3f}")
    if h.score_lexical is not None:
        parts.append(f"lex={h.score_lexical:.3f}")
    ranks = []
    if h.rank_semantic is not None:
        ranks.append(f"S#{h.rank_semantic + 1}")
    if h.rank_lexical is not None:
        ranks.append(f"L#{h.rank_lexical + 1}")
    return (
        f"  {','.join(parts):<35} {','.join(ranks):<12} "
        f"{(h.part_no_new or '?'):<14} | {(h.event or '-'):<10} | "
        f"{(h.part_name or '-')[:30]:<30} | {h.form_id}"
    )


def run():
    eng = make_engine()
    Session = session_factory(eng)
    with Session() as s:
        for q, flt, label in QUERIES:
            print()
            print("=" * 100)
            print(f"Q: \"{q}\"   ({label}, filter={flt or 'none'})")
            print("=" * 100)

            sem_hits = semantic_search(s, q, top_k=5, **flt)
            print("\n-- Semantic only (top 5) --")
            for h in sem_hits:
                print(_format(h))

            lex_hits = lexical_search(s, q, top_k=5, **flt)
            print("\n-- Lexical only (top 5) --")
            if lex_hits:
                for h in lex_hits:
                    print(_format(h))
            else:
                print("  (no lexical hits — query gram이 narrative에 없음)")

            hyb_hits = hybrid_search(s, q, top_k=5, **flt)
            print("\n-- Hybrid RRF (top 5) --")
            for h in hyb_hits:
                print(_format(h))


if __name__ == "__main__":
    run()
