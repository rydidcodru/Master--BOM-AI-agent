"""OpenAI text-embedding-3-small → dev_parts.db changing_embedding 백필.

사용:
    python backfill_embeddings_sqlite.py                # NULL/빈값인 행만
    python backfill_embeddings_sqlite.py --all          # 강제 재계산
    python backfill_embeddings_sqlite.py --batch 100    # 배치 크기 조정

임베딩 소스: changing_text (없으면 part_name + changing_point + changing_reason 조합)
저장 형식: JSON 직렬화 float 배열 (text-embedding-3-small → 1536차원)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from openai import OpenAI

DB_PATH = Path(__file__).parent.parent / "dev_parts.db"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


def _build_text(row: dict) -> str:
    """임베딩할 텍스트 구성. changing_text 우선, 없으면 필드 조합."""
    if row.get("changing_text") and row["changing_text"].strip():
        return row["changing_text"].strip()
    parts = []
    if row.get("part_name"):
        parts.append(f"부품명: {row['part_name']}")
    if row.get("changing_point"):
        parts.append(f"변경점: {row['changing_point']}")
    if row.get("changing_reason"):
        parts.append(f"변경사유: {row['changing_reason']}")
    return "\n".join(parts)


def _fetch_pending(con: sqlite3.Connection, force: bool) -> list[dict]:
    if force:
        where = "WHERE changing_text IS NOT NULL AND changing_text != ''"
    else:
        where = """WHERE (changing_embedding IS NULL
                       OR changing_embedding = ''
                       OR changing_embedding = '[]')
                     AND (changing_text IS NOT NULL AND changing_text != ''
                          OR (changing_point IS NOT NULL AND changing_point != ''))"""
    cur = con.execute(f"""
        SELECT detail_id, part_name, changing_point, changing_reason, changing_text
        FROM dev_part_detail
        {where}
        ORDER BY detail_id
    """)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def backfill(*, batch_size: int = 100, force: bool = False) -> None:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    rows = _fetch_pending(con, force=force)
    total = len(rows)
    print(f"[embed] 처리 대상: {total}건", file=sys.stderr)
    if total == 0:
        con.close()
        return

    done = 0
    started = time.time()

    for i in range(0, total, batch_size):
        batch = rows[i: i + batch_size]
        texts = [_build_text(r) for r in batch]
        ids = [r["detail_id"] for r in batch]

        t0 = time.time()
        resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
        t_enc = time.time() - t0

        embeddings = [item.embedding for item in resp.data]

        t0 = time.time()
        con.executemany(
            "UPDATE dev_part_detail SET changing_embedding = ? WHERE detail_id = ?",
            [(json.dumps(emb), did) for emb, did in zip(embeddings, ids)],
        )
        con.commit()
        t_db = time.time() - t0

        done += len(batch)
        elapsed = time.time() - started
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate if rate > 0 else 0
        print(
            f"[embed] {done}/{total} ({done/total*100:5.1f}%) "
            f"encode={t_enc:.2f}s db={t_db:.2f}s "
            f"rate={rate:.1f}/s ETA={eta/60:.1f}min",
            file=sys.stderr,
        )

    con.close()
    print(
        f"[embed] 완료. {done}건 / {(time.time()-started)/60:.1f}분",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", type=int, default=100, help="API 배치 크기 (default: 100)")
    p.add_argument("--all", action="store_true", help="기존 임베딩도 강제 재계산")
    args = p.parse_args(argv)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        env_path = Path(__file__).parent.parent / "pptx_bom_search" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip()
                    break

    backfill(batch_size=args.batch, force=args.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
