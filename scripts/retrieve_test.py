"""Retriever 테스트 — dev_part_master에서 의미/필터/하이브리드 검색.

ENABLE_EMBEDDING=1 + Ollama (bge-m3) 필요.
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("ENABLE_EMBEDDING", "1")

from sqlalchemy import text  # noqa: E402

from src.db.engine import make_engine, session_factory  # noqa: E402
from src.embed.embedder import embed_texts  # noqa: E402


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def semantic_search(
    session,
    query: str,
    top_k: int = 5,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
) -> list[Any]:
    """벡터 cosine similarity + 선택적 메타 필터."""
    vec = embed_texts([query])[0]
    where_parts = ["embedding_dense IS NOT NULL"]
    params: dict[str, Any] = {"v": _vec_str(vec)}
    if form_id_like:
        where_parts.append("form_id LIKE :fid")
        params["fid"] = form_id_like
    if event:
        where_parts.append("event = :evt")
        params["evt"] = event
    if region:
        where_parts.append("region = :reg")
        params["reg"] = region
    where_sql = " AND ".join(where_parts)
    sql = f"""
        SELECT doc_id, part_no_new, part_name, new_model, event, region, form_id,
               left(embedding_text, 160) AS snippet,
               1 - (embedding_dense <=> CAST(:v AS vector)) AS score
        FROM dev_part_master
        WHERE {where_sql}
        ORDER BY embedding_dense <=> CAST(:v AS vector)
        LIMIT {top_k}
    """
    return session.execute(text(sql), params).all()


def lexical_search(
    session,
    keyword: str,
    columns: tuple[str, ...] = ("part_name", "change_point_raw", "change_reason_raw"),
    top_k: int = 10,
) -> list[Any]:
    """단순 ILIKE 검색 — bge-m3 안 써도 됨."""
    like = f"%{keyword}%"
    or_parts = " OR ".join(f"{c} ILIKE :kw" for c in columns)
    sql = f"""
        SELECT doc_id, part_no_new, part_name, new_model, event, region, form_id,
               COALESCE(change_point_raw, change_reason_raw, part_name) AS hit_text
        FROM dev_part_master
        WHERE {or_parts}
        LIMIT {top_k}
    """
    return session.execute(text(sql), {"kw": like}).all()


def show(rows: list[Any], header: str) -> None:
    print(f"\n=== {header} ({len(rows)} hits) ===")
    for r in rows:
        score = f"  [{r.score:.3f}]" if hasattr(r, "score") else ""
        print(
            f"{score} {r.part_no_new or '?':<14} | {r.event or '-':<10} | "
            f"{(r.new_model or '-')[:18]:<18} | {(r.part_name or '-')[:30]:<30} "
            f"| {r.form_id}"
        )
        if hasattr(r, "snippet") and r.snippet:
            print(f"           {r.snippet[:140]}...")


def run_demo():
    eng = make_engine()
    Session = session_factory(eng)
    with Session() as s:
        # 1) 의미검색 — 자연어 query
        for q in [
            "내열성 강화 패킹 변경",
            "DRBFM 외관 STS 변경",
            "도어 힌지 신규 부품",
            "Cavity Assembly 변경",
            "전선 커넥터 안전 규제 대응",
        ]:
            rows = semantic_search(s, q, top_k=3)
            show(rows, f"의미검색: '{q}'")

        # 2) 의미검색 + form 필터
        rows = semantic_search(
            s, "부품 등급 변경", top_k=3, form_id_like="changing_parts_list%"
        )
        show(rows, "의미검색 + form 필터 (changing_parts_list만)")

        # 3) 의미검색 + event 필터
        rows = semantic_search(s, "재질 변경", top_k=3, event="Change")
        show(rows, "의미검색 + event=Change")

        # 4) lexical (ILIKE) — 의미검색 대안
        rows = lexical_search(s, "내열", top_k=5)
        show(rows, "Lexical ILIKE: '내열'")

        rows = lexical_search(s, "STS", top_k=5)
        show(rows, "Lexical ILIKE: 'STS'")

        # 5) 통계
        print("\n=== 적재 통계 ===")
        stats = s.execute(
            text(
                """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE embedding_dense IS NOT NULL) AS embedded,
                count(*) FILTER (WHERE part_no_new IS NOT NULL) AS pno_filled,
                count(*) FILTER (WHERE event IN ('New','Change','Carry-over')) AS event_classified
            FROM dev_part_master
        """
            )
        ).one()
        print(f"  total: {stats.total}")
        print(f"  embedded: {stats.embedded}")
        print(f"  pno_filled: {stats.pno_filled}")
        print(f"  event_classified: {stats.event_classified}")


if __name__ == "__main__":
    run_demo()
