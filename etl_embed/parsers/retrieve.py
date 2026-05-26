"""dev_part_master 하이브리드 검색 (dense kNN + sparse lexical → RRF 결합).

전략:
  - BGE-M3 dense (cosine, HNSW): 의미 검색.
  - BGE-M3 sparse (inner product, HNSW): 부품번호·모델코드 exact match 보강.
  - 두 결과를 Reciprocal Rank Fusion (RRF)으로 합산.
      score = sum( 1 / (k + rank_i) )   for i in {dense, sparse}
    Cormack et al., 2009 — 단순하지만 정규화 불필요해서 점수 스케일이 다른
    두 retriever 결합에 가장 안정적.

옵션:
  - row_kind = 'change_history' 만 검색 (RAG 주 타겟).
  - region, base_model 등 컬럼 prefilter.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .db import connect
from .embedder import BGEM3Embedder, dense_to_pg, sparse_to_pg


HYBRID_SQL = """
WITH q_dense AS (
    SELECT doc_id,
           row_number() OVER (ORDER BY embedding_dense <=> %(q_dense)s::vector) AS rk
    FROM dev_part_master
    WHERE embedding_dense IS NOT NULL
      {filter_clause}
    ORDER BY embedding_dense <=> %(q_dense)s::vector
    LIMIT %(topk_each)s
),
q_sparse AS (
    SELECT doc_id,
           row_number() OVER (ORDER BY embedding_sparse <#> %(q_sparse)s::sparsevec) AS rk
    FROM dev_part_master
    WHERE embedding_sparse IS NOT NULL
      {filter_clause}
    ORDER BY embedding_sparse <#> %(q_sparse)s::sparsevec
    LIMIT %(topk_each)s
),
fused AS (
    SELECT doc_id, SUM(1.0 / (%(rrf_k)s + rk)) AS score FROM (
        SELECT * FROM q_dense
        UNION ALL
        SELECT * FROM q_sparse
    ) u GROUP BY doc_id
)
SELECT f.score, d.doc_id, d.region, d.base_model, d.new_model,
       d.part_no_base, d.part_no_new, d.part_name,
       d.change_point_raw, d.change_reason_raw, d.row_kind
FROM fused f JOIN dev_part_master d USING (doc_id)
ORDER BY f.score DESC
LIMIT %(topk)s
"""


@dataclass
class Hit:
    score: float
    doc_id: str
    region: str | None
    base_model: str | None
    new_model: str | None
    part_no_base: str | None
    part_no_new: str | None
    part_name: str | None
    change_point: str | None
    change_reason: str | None
    row_kind: str | None


def hybrid_search(
    query: str,
    *,
    topk: int = 10,
    topk_each: int = 50,
    rrf_k: int = 60,
    only_change_history: bool = False,
    region: str | None = None,
    embedder: BGEM3Embedder | None = None,
) -> list[Hit]:
    embedder = embedder or BGEM3Embedder()
    enc = embedder.encode([query], batch_size=1)
    q_dense = dense_to_pg(enc.dense[0])
    q_sparse = sparse_to_pg(enc.sparse[0])

    clauses = []
    params: dict[str, Any] = {
        "q_dense": q_dense,
        "q_sparse": q_sparse,
        "topk": topk,
        "topk_each": topk_each,
        "rrf_k": rrf_k,
    }
    if only_change_history:
        clauses.append("AND row_kind = 'change_history'")
    if region:
        clauses.append("AND region = %(region)s")
        params["region"] = region
    filter_clause = " ".join(clauses)
    sql = HYBRID_SQL.format(filter_clause=filter_clause)

    with connect() as con, con.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        Hit(
            score=float(r["score"]),
            doc_id=str(r["doc_id"]),
            region=r["region"],
            base_model=r["base_model"],
            new_model=r["new_model"],
            part_no_base=r["part_no_base"],
            part_no_new=r["part_no_new"],
            part_name=r["part_name"],
            change_point=r["change_point_raw"],
            change_reason=r["change_reason_raw"],
            row_kind=r["row_kind"],
        )
        for r in rows
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BGE-M3 hybrid search over dev_part_master")
    p.add_argument("query", nargs="+", help="검색어")
    p.add_argument("-k", "--topk", type=int, default=10)
    p.add_argument("--each", type=int, default=50, help="각 retriever의 후보 수")
    p.add_argument("--rrf-k", type=int, default=60)
    p.add_argument("--changes-only", action="store_true",
                   help="row_kind='change_history'만 검색")
    p.add_argument("--region", default=None)
    args = p.parse_args(argv)

    q = " ".join(args.query)
    hits = hybrid_search(
        q,
        topk=args.topk,
        topk_each=args.each,
        rrf_k=args.rrf_k,
        only_change_history=args.changes_only,
        region=args.region,
    )

    print(f"\nQuery: {q}\n")
    for i, h in enumerate(hits, 1):
        print(f"[{i}] score={h.score:.4f}  region={h.region}  kind={h.row_kind}")
        if h.base_model or h.new_model:
            print(f"     model: {h.base_model} → {h.new_model}")
        if h.part_no_base or h.part_no_new:
            print(f"     part : {h.part_no_base} → {h.part_no_new}  {h.part_name or ''}")
        if h.change_point:
            print(f"     변경점: {h.change_point[:100]}")
        if h.change_reason:
            print(f"     사유  : {h.change_reason[:100]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
