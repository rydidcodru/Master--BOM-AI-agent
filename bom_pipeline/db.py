"""dev_parts.db 연결 및 쿼리 함수."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "dev_parts.db"


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ── 부품명 목록 ────────────────────────────────────────────────────────────

def get_all_part_names() -> list[str]:
    """DB에 존재하는 의미 있는 부품명 전체 (중복 제거)."""
    con = _connect()
    rows = con.execute("""
        SELECT DISTINCT part_name
        FROM dev_part_detail
        WHERE part_name IS NOT NULL
          AND part_name != ''
          AND part_name != '-'
          AND length(part_name) > 3
          AND part_name LIKE '% %'
        ORDER BY part_name
    """).fetchall()
    con.close()
    return [r["part_name"] for r in rows]


# ── part_no 기반 과거 이력 조회 ───────────────────────────────────────────

_SELECT_HISTORY = """
    SELECT
        d.detail_id,
        d.master_id,
        d.bom_level,
        d.bom_depth,
        d.part_name,
        d.base_part_no,
        d.new_part_no,
        d.part_type,
        d.changing_point,
        d.changing_reason,
        d.changing_text,
        d.changing_embedding,
        m.base_model,
        m.new_model,
        m.region,
        m.source_file
    FROM dev_part_detail d
    JOIN dev_part_master m ON d.master_id = m.master_id
    WHERE {where}
      AND d.changing_point IS NOT NULL
      AND d.changing_point != ''
    ORDER BY m.master_id, d.line_order
"""

# LG 품번은 영문+숫자 조합. 앞 7자리가 부품 계열 식별자 역할을 함.
# 예) AGU74969449 → AGU7496 prefix로 AGU74969425, AGU74969430 등 계열 이력 검색
_PREFIX_LEN = 7


def fetch_history_by_part_no(base_part_no: str) -> list[dict]:
    """
    base_part_no로 과거 변경 이력 조회.
    1차: 정확 일치 (exact)
    2차: 앞 7자리 prefix LIKE 매칭 — 같은 부품 계열의 다른 모델 이력 포함
    """
    if not base_part_no or len(base_part_no) < _PREFIX_LEN:
        return []

    con = _connect()

    # 1차: 정확 일치
    rows = con.execute(
        _SELECT_HISTORY.format(where="d.base_part_no = ?"),
        (base_part_no,),
    ).fetchall()

    exact_results = [dict(r) for r in rows]
    seen_ids = {r["detail_id"] for r in exact_results}
    for r in exact_results:
        r["search_path"] = "exact"

    # 2차: prefix LIKE — 정확 일치 결과가 없을 때만 실행
    prefix_results: list[dict] = []
    if not exact_results:
        prefix = base_part_no[:_PREFIX_LEN] + "%"
        rows2 = con.execute(
            _SELECT_HISTORY.format(where="d.base_part_no LIKE ?"),
            (prefix,),
        ).fetchall()
        for r in rows2:
            d = dict(r)
            if d["detail_id"] not in seen_ids:
                seen_ids.add(d["detail_id"])
                d["search_path"] = "prefix"
                prefix_results.append(d)

    con.close()
    return exact_results + prefix_results


def fetch_subtree_by_part_no(base_part_no: str, master_id: int) -> list[dict]:
    """
    특정 master 내에서 base_part_no를 루트로 하는 하위 트리 반환.
    parent_detail_id 재귀로 전개.
    """
    con = _connect()

    # 루트 행 찾기
    root = con.execute("""
        SELECT detail_id FROM dev_part_detail
        WHERE base_part_no = ? AND master_id = ?
        LIMIT 1
    """, (base_part_no, master_id)).fetchone()

    if not root:
        con.close()
        return []

    root_id = root["detail_id"]

    # WITH RECURSIVE로 하위 트리 전개
    rows = con.execute("""
        WITH RECURSIVE subtree(detail_id) AS (
            SELECT ?
            UNION ALL
            SELECT d.detail_id
            FROM dev_part_detail d
            JOIN subtree s ON d.parent_detail_id = s.detail_id
        )
        SELECT d.detail_id, d.bom_level, d.bom_depth, d.part_name,
               d.base_part_no, d.new_part_no, d.part_type,
               d.changing_point, d.changing_reason
        FROM dev_part_detail d
        JOIN subtree s ON d.detail_id = s.detail_id
        ORDER BY d.line_order
    """, (root_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── FTS5 키워드 검색 ──────────────────────────────────────────────────────

def fts_search(query: str, limit: int = 30) -> list[dict]:
    """
    FTS5로 part_name / changing_point / changing_reason 전문 검색.
    query: 공백 구분 키워드 (자동으로 AND 검색)
    """
    # FTS5 구문: 각 토큰을 AND로 연결
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return []
    fts_query = " AND ".join(f'"{t}"' for t in tokens)

    con = _connect()
    try:
        rows = con.execute("""
            SELECT
                d.detail_id,
                d.master_id,
                d.bom_level,
                d.part_name,
                d.base_part_no,
                d.new_part_no,
                d.changing_point,
                d.changing_reason,
                d.changing_text,
                d.changing_embedding,
                m.base_model,
                m.new_model,
                m.region
            FROM dev_part_detail_fts f
            JOIN dev_part_detail d ON f.detail_id = d.detail_id
            JOIN dev_part_master m ON d.master_id = m.master_id
            WHERE dev_part_detail_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        # AND 검색 결과 없으면 OR로 재시도
        fts_query_or = " OR ".join(f'"{t}"' for t in tokens)
        try:
            rows = con.execute("""
                SELECT
                    d.detail_id, d.master_id, d.bom_level, d.part_name,
                    d.base_part_no, d.new_part_no,
                    d.changing_point, d.changing_reason,
                    d.changing_text, d.changing_embedding,
                    m.base_model, m.new_model, m.region
                FROM dev_part_detail_fts f
                JOIN dev_part_detail d ON f.detail_id = d.detail_id
                JOIN dev_part_master m ON d.master_id = m.master_id
                WHERE dev_part_detail_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query_or, limit)).fetchall()
        except Exception:
            rows = []
    con.close()
    return [dict(r) for r in rows]


# ── 임베딩 코사인 유사도 검색 ─────────────────────────────────────────────

def embedding_search(query_embedding: list[float], limit: int = 30) -> list[dict]:
    """
    changing_embedding과 query_embedding의 코사인 유사도로 검색.
    numpy in-memory 계산 (pgvector 없이).
    """
    import numpy as np

    con = _connect()
    rows = con.execute("""
        SELECT
            d.detail_id, d.master_id, d.bom_level, d.part_name,
            d.base_part_no, d.new_part_no,
            d.changing_point, d.changing_reason,
            d.changing_text, d.changing_embedding,
            m.base_model, m.new_model, m.region
        FROM dev_part_detail d
        JOIN dev_part_master m ON d.master_id = m.master_id
        WHERE d.changing_embedding IS NOT NULL
          AND d.changing_embedding != ''
          AND d.changing_embedding != '[]'
    """).fetchall()
    con.close()

    if not rows:
        return []

    q = np.array(query_embedding, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-9)

    scored = []
    for r in rows:
        try:
            emb = json.loads(r["changing_embedding"])
            v = np.array(emb, dtype=np.float32)
            v_norm = v / (np.linalg.norm(v) + 1e-9)
            score = float(np.dot(q_norm, v_norm))
            scored.append((score, dict(r)))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, row in scored[:limit]:
        row["similarity"] = score
        results.append(row)
    return results


# ── RRF 하이브리드 검색 ───────────────────────────────────────────────────

def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    top_k: int = 20,
    rrf_k: int = 60,
) -> list[dict]:
    """
    FTS5 + 임베딩 코사인을 RRF로 합산한 하이브리드 검색.
    """
    fts_results = fts_search(query_text, limit=top_k * 2)
    emb_results = embedding_search(query_embedding, limit=top_k * 2)

    # detail_id 기준 RRF 스코어 계산
    scores: dict[int, float] = {}
    id_to_row: dict[int, dict] = {}

    for rank, row in enumerate(fts_results):
        did = row["detail_id"]
        scores[did] = scores.get(did, 0.0) + 1.0 / (rrf_k + rank + 1)
        id_to_row[did] = row

    for rank, row in enumerate(emb_results):
        did = row["detail_id"]
        scores[did] = scores.get(did, 0.0) + 1.0 / (rrf_k + rank + 1)
        id_to_row[did] = row

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for did, score in ranked[:top_k]:
        row = id_to_row[did]
        row["rrf_score"] = score
        results.append(row)
    return results
