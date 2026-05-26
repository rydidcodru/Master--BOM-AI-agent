"""embedding_text → (embedding_dense, embedding_sparse) 백필.

사용:
    python -m parsers.backfill_embeddings              # NULL인 행만
    python -m parsers.backfill_embeddings --all        # 강제 재계산
    python -m parsers.backfill_embeddings --batch 32   # 배치 크기 조정

진행 상황은 stderr로 출력.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Iterator

import psycopg

from .db import connect
from .embedder import BGEM3Embedder, dense_to_pg, sparse_to_pg

SELECT_PENDING = """
SELECT doc_id, embedding_text
FROM dev_part_master
WHERE embedding_text IS NOT NULL
  AND length(embedding_text) > 0
  AND ({extra_filter})
ORDER BY doc_id
"""

SELECT_PENDING_PAGE = """
SELECT doc_id, embedding_text
FROM dev_part_master
WHERE embedding_text IS NOT NULL
  AND length(embedding_text) > 0
  AND ({extra_filter})
  {page_filter}
ORDER BY doc_id
LIMIT %s
"""

UPDATE_SQL = """
UPDATE dev_part_master
SET embedding_dense  = %s::vector,
    embedding_sparse = %s::sparsevec
WHERE doc_id = %s
"""


def iter_pending(
    con: psycopg.Connection,
    *,
    force: bool,
    chunk: int = 500,
) -> Iterator[list[tuple[str, str]]]:
    """커밋 사이에 안전한 keyset pagination으로 (doc_id, text) 청크 yield."""
    extra = "TRUE" if force else "embedding_dense IS NULL"
    last_doc_id: str | None = None
    while True:
        page_filter = "" if last_doc_id is None else "AND doc_id > %s"
        sql = SELECT_PENDING_PAGE.format(
            extra_filter=extra,
            page_filter=page_filter,
        )
        params = (chunk,) if last_doc_id is None else (last_doc_id, chunk)
        with con.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        if not rows:
            break

        batch = [(str(doc_id), text) for doc_id, text in rows]
        last_doc_id = batch[-1][0]
        yield batch


def count_pending(con: psycopg.Connection, *, force: bool) -> int:
    extra = "TRUE" if force else "embedding_dense IS NULL"
    sql = f"""
        SELECT COUNT(*) FROM dev_part_master
        WHERE embedding_text IS NOT NULL AND length(embedding_text) > 0
          AND ({extra})
    """
    with con.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def backfill(
    *,
    batch_size: int = 16,
    chunk_size: int = 500,
    force: bool = False,
) -> None:
    embedder = BGEM3Embedder()
    started = time.time()
    done = 0

    with connect() as con:
        total = count_pending(con, force=force)
        print(f"[embed] pending rows: {total}", file=sys.stderr)
        if total == 0:
            return

        # 모델 워밍업 (첫 청크 처리 전에 로드 시간 가시화)
        print("[embed] warming up BGE-M3 ...", file=sys.stderr)
        embedder.encode(["warmup"], batch_size=1)
        print(f"[embed] model ready ({time.time() - started:.1f}s)", file=sys.stderr)

        # iterate / encode / write
        with con.cursor() as write_cur:
            for chunk in iter_pending(con, force=force, chunk=chunk_size):
                ids = [row[0] for row in chunk]
                texts = [row[1] for row in chunk]
                t0 = time.time()
                enc = embedder.encode(texts, batch_size=batch_size)
                t_enc = time.time() - t0

                params = [
                    (dense_to_pg(d), sparse_to_pg(s), doc_id)
                    for d, s, doc_id in zip(enc.dense, enc.sparse, ids)
                ]
                t0 = time.time()
                write_cur.executemany(UPDATE_SQL, params)
                con.commit()
                t_db = time.time() - t0

                done += len(chunk)
                pct = done / total * 100
                elapsed = time.time() - started
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(
                    f"[embed] {done}/{total} ({pct:5.1f}%) "
                    f"encode={t_enc:5.2f}s db={t_db:4.2f}s "
                    f"rate={rate:6.1f}/s ETA={eta/60:5.1f}min",
                    file=sys.stderr,
                )

    print(f"[embed] done. {done} rows in {(time.time()-started)/60:.1f} min",
          file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", type=int, default=16,
                   help="encoder batch size (default: 16; A100/4090: 32-64)")
    p.add_argument("--chunk", type=int, default=500,
                   help="DB fetch/commit chunk size (default: 500)")
    p.add_argument("--all", action="store_true",
                   help="force re-embed even if embedding_dense is set")
    args = p.parse_args(argv)
    backfill(batch_size=args.batch, chunk_size=args.chunk, force=args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
