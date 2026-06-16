import json
import math
import sqlite3
from typing import Any

try:
    import numpy as np
except ImportError:  # numpy 없으면(기본 배포) 순수 파이썬 fallback으로 동작.
    np = None


EMBEDDING_FIELDS = [
    "changing_reason_embedding",
    "changing_point_embedding",
    "part_name_embedding",
    "combined_embedding",
]


def parse_embedding(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [float(x) for x in parsed if isinstance(x, (int, float))]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def cosine_with_norms(query: list[float], query_norm: float, vector: list[float], vector_norm_value: float) -> float:
    if not query or not vector or query_norm == 0 or vector_norm_value == 0 or len(query) != len(vector):
        return 0.0
    return sum(x * y for x, y in zip(query, vector)) / (query_norm * vector_norm_value)


def _unit(vector: list[float], norm: float) -> list[float]:
    return [x / norm for x in vector] if norm else vector


def _embedding_cache(conn: sqlite3.Connection) -> dict[str, Any]:
    """DB의 detail 임베딩을 한 번만 파싱·단위정규화해 conn에 캐싱한다.

    /changes/recommend 한 요청은 단일 conn을 공유하므로, 여러 변경 입력이
    같은 DB 벡터를 매번 재파싱하던 N배 중복 작업을 제거한다.
    numpy가 있으면 필드별 (행렬, id배열)로 저장해 행렬곱으로 코사인을 한 번에 계산한다.
    """
    cache = getattr(conn, "_multi_vector_cache", None)
    if cache is not None:
        return cache

    parsed: dict[str, list[tuple[int, list[float]]]] = {field: [] for field in EMBEDDING_FIELDS}
    select_columns = ", ".join(["detail_id", *EMBEDDING_FIELDS])
    rows = conn.execute(f"SELECT {select_columns} FROM dev_part_detail").fetchall()
    for row in rows:
        detail_id = int(row["detail_id"])
        for field in EMBEDDING_FIELDS:
            vector = parse_embedding(row[field])
            if vector:
                parsed[field].append((detail_id, vector))

    cache: dict[str, Any] = {}
    for field, items in parsed.items():
        if not items:
            cache[field] = None
            continue
        if np is not None:
            # 같은 차원 벡터만 행렬로 쌓고, 행 단위로 단위정규화.
            dim = len(items[0][1])
            ids = [did for did, vec in items if len(vec) == dim]
            matrix = np.asarray([vec for did, vec in items if len(vec) == dim], dtype="float32")
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            cache[field] = {"ids": np.asarray(ids), "matrix": matrix / norms, "numpy": True}
        else:
            unit_map = {did: _unit(vec, vector_norm(vec)) for did, vec in items}
            cache[field] = {"unit": unit_map, "numpy": False}

    try:
        conn._multi_vector_cache = cache  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass
    return cache


def vector_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region
        FROM dev_part_detail d
        JOIN dev_part_master m ON m.master_id = d.master_id
        WHERE d.changing_embedding IS NOT NULL
          AND d.changing_embedding <> '[]'
        """
    ).fetchall()
    scored = []
    for row in rows:
        item = dict(row)
        score = cosine_similarity(query_embedding, parse_embedding(item.get("changing_embedding")))
        if score > 0:
            item["score"] = score
            scored.append(item)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def multi_vector_scores(
    conn: sqlite3.Connection,
    query_embeddings: dict[str, list[float]],
    *,
    detail_ids: set[int] | None = None,
) -> dict[int, dict[str, float]]:
    fields = [field for field in EMBEDDING_FIELDS if query_embeddings.get(field)]
    if not fields:
        return {}

    cache = _embedding_cache(conn)
    scores: dict[int, dict[str, float]] = {}

    for field in fields:
        field_cache = cache.get(field)
        if not field_cache:
            continue
        query_vector = query_embeddings[field]
        query_norm = vector_norm(query_vector)
        if query_norm == 0:
            continue

        if field_cache.get("numpy"):
            ids = field_cache["ids"]
            sims = field_cache["matrix"] @ (np.asarray(query_vector, dtype="float32") / query_norm)
            for idx in np.nonzero(sims > 0)[0]:
                detail_id = int(ids[idx])
                if detail_ids is not None and detail_id not in detail_ids:
                    continue
                scores.setdefault(detail_id, {})[field] = float(sims[idx])
        else:
            query_unit = _unit(query_vector, query_norm)
            unit_map = field_cache["unit"]
            entries = (
                ((did, unit_map[did]) for did in detail_ids if did in unit_map)
                if detail_ids is not None
                else unit_map.items()
            )
            for detail_id, row_unit in entries:
                score = sum(x * y for x, y in zip(query_unit, row_unit))
                if score > 0:
                    scores.setdefault(detail_id, {})[field] = score
    return scores
