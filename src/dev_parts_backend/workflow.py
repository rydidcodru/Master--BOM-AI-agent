import math
import re
import sqlite3
from typing import Any

from dev_parts_backend.rag.search import multi_vector_scores


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

IDENTITY_RECALL_WEIGHTS = {
    "dense_part_name": 0.45,
    "module_part_bm25": 0.30,
    "identity_text": 0.15,
    "dense_combined": 0.10,
}
# 과거 데이터의 변경사유(47%)/변경점(24%)이 희소하고, prefix+description 게이트가
# 이미 부품 정체성을 보장하므로 identity 비중을 높이고 노이즈가 큰
# change_intent_text 의존을 낮췄다. 변경텍스트 신호는 의미 있는 rerank로 유지하되
# identity+mapping(0.60)을 압도하지 않게 한다. (가중치는 사용자 피드백으로 튜닝 가능)
RERANK_WEIGHTS = {
    "identity_recall": 0.45,
    "dense_changing_point": 0.12,
    "dense_reason": 0.10,
    "change_bm25": 0.18,
    "change_intent_text": 0.22,
    "mapping_prefix": 0.15,
}
COMBINED_DENSE_INCLUDE_THRESHOLD = 0.88
CHANGE_SYNONYM_GROUPS = [
    {"치수", "사이즈", "폭", "너비", "높이", "길이", "mm", "dimension", "width", "height", "size"},
    {"축소", "줄임", "감소", "down", "reduce", "reduced", "shorten"},
    {"확대", "증가", "늘림", "up", "increase", "increased", "extend"},
    {"착탈", "탈착", "착용", "분리", "착탈식", "removable", "detachable", "attach", "detach"},
    {"구조", "structure", "mechanism"},
    {"스팀", "steam", "stema"},
    {"색상", "컬러", "외관", "color", "colour"},
    {"재질", "소재", "material"},
]


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(text(item) for item in value if text(item))
    return str(value).strip()


def normalize_part_no(value: Any) -> str:
    return re.sub(r"[\s\-_/\.]+", "", text(value).upper())


def tokens(value: Any) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text(value)) if len(token) >= 2}


def normalize_keywords(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"[,;/|]+", text(value))
    return [text(item) for item in values if text(item)]


def expanded_change_intent_text(change: dict[str, Any]) -> str:
    parts = [
        text(change.get("change_type")),
        text(change.get("target_value")),
        text(change.get("change_point")),
        text(change.get("changing_point")),
        text(change.get("change_reason")),
        text(change.get("changing_reason")),
        " ".join(normalize_keywords(change.get("change_intent_keywords"))),
    ]
    base_tokens = tokens(" ".join(parts))
    expanded = set(base_tokens)
    for group in CHANGE_SYNONYM_GROUPS:
        if base_tokens & group:
            expanded.update(group)
    return " ".join([*parts, *sorted(expanded)])


def row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


def compact_candidate(
    row: dict[str, Any],
    *,
    score: float | None = None,
    reasons: list[str] | None = None,
    score_components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = {
        "detail_id": row.get("detail_id"),
        "master_id": row.get("master_id"),
        "source_file": row.get("source_file"),  # 출처 파일(스코어 옆 표시용)
        "line_order": row.get("line_order"),
        "level": row.get("bom_depth"),
        "bom_level": row.get("bom_level"),
        "part_name": row.get("part_name"),
        "base_part_no": row.get("base_part_no"),
        "new_part_no": row.get("new_part_no"),
        "change_point": row.get("changing_point"),
        "change_reason": row.get("changing_reason"),
        # 과거 데이터는 변경사유/변경점이 비어 있는 경우가 많다.
        # 매칭 실패가 아니라 데이터 공백임을 UI에서 구분할 수 있게 표시한다.
        "has_change_text": bool(text(row.get("changing_point")) or text(row.get("changing_reason"))),
        "classification": row.get("classification"),
        "past_model": {
            "source_file": row.get("source_file"),
            "source_sheet": row.get("source_sheet"),
            "project_name": row.get("project_name"),
            "base_model": row.get("base_model"),
            "new_model": row.get("new_model"),
            "region": row.get("region"),
        },
    }
    if score is not None:
        candidate["score"] = round(score, 4)
    if reasons:
        candidate["score_reasons"] = reasons[:6]
    if score_components:
        candidate["score_components"] = score_components
    return candidate


def history_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
               m.base_model, m.new_model
        FROM dev_part_detail d
        JOIN dev_part_master m ON m.master_id = d.master_id
        ORDER BY d.master_id, d.line_order
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def candidate_history_rows(conn: sqlite3.Connection, change: dict[str, Any]) -> list[dict[str, Any]]:
    prefixes = mapped_prefixes(change)
    if not prefixes:
        return history_rows(conn)

    clauses = []
    params: list[str] = []
    for prefix in prefixes:
        like_value = f"{prefix}%"
        clauses.append("(UPPER(COALESCE(d.base_part_no, '')) LIKE ? OR UPPER(COALESCE(d.new_part_no, '')) LIKE ?)")
        params.extend([like_value, like_value])

    rows = conn.execute(
        f"""
        SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
               m.base_model, m.new_model
        FROM dev_part_detail d
        JOIN dev_part_master m ON m.master_id = d.master_id
        WHERE {" OR ".join(clauses)}
        ORDER BY d.master_id, d.line_order
        """,
        params,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def score_history_row(change: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    base_part_no = normalize_part_no(change.get("base_part_no"))
    row_part_numbers = {
        normalize_part_no(row.get("base_part_no")),
        normalize_part_no(row.get("new_part_no")),
    }
    if base_part_no and base_part_no in row_part_numbers:
        score += 90.0
        reasons.append("base_part_no matched")

    part_name_tokens = tokens(change.get("part_name"))
    module_tokens = tokens(change.get("module_name"))
    change_point_tokens = tokens(change.get("change_point"))
    change_reason_tokens = tokens(change.get("change_reason"))

    row_part_tokens = tokens(row.get("part_name"))
    row_change_tokens = tokens(f"{row.get('changing_point', '')} {row.get('changing_reason', '')}")
    row_all_tokens = tokens(
        " ".join(
            text(row.get(field))
            for field in (
                "part_name",
                "base_part_no",
                "new_part_no",
                "changing_point",
                "changing_reason",
                "classification",
                "project_name",
            )
        )
    )

    part_overlap = part_name_tokens & row_part_tokens
    if part_overlap:
        gain = 18.0 + len(part_overlap) * 3.0
        score += gain
        reasons.append(f"part_name tokens: {', '.join(sorted(part_overlap)[:4])}")

    module_overlap = module_tokens & row_all_tokens
    if module_overlap:
        gain = 10.0 + len(module_overlap) * 2.0
        score += gain
        reasons.append(f"module tokens: {', '.join(sorted(module_overlap)[:4])}")

    change_point_overlap = change_point_tokens & row_change_tokens
    if change_point_overlap:
        gain = 12.0 + len(change_point_overlap) * 2.0
        score += gain
        reasons.append(f"change_point tokens: {', '.join(sorted(change_point_overlap)[:4])}")

    change_reason_overlap = change_reason_tokens & row_change_tokens
    if change_reason_overlap:
        gain = 8.0 + len(change_reason_overlap) * 1.5
        score += gain
        reasons.append(f"change_reason tokens: {', '.join(sorted(change_reason_overlap)[:4])}")

    part_name = text(change.get("part_name")).lower()
    module_name = text(change.get("module_name")).lower()
    row_part_name = text(row.get("part_name")).lower()
    if part_name and part_name in row_part_name:
        score += 10.0
        reasons.append("part_name phrase matched")
    if module_name and module_name in row_part_name:
        score += 8.0
        reasons.append("module phrase matched part_name")

    if text(row.get("changing_point")) or text(row.get("changing_reason")):
        score += 2.0
    if text(row.get("classification")).lower() in {"change", "add", "delete"}:
        score += 1.5

    return score, reasons


def collect_query_embeddings(change: dict[str, Any]) -> dict[str, list[float]]:
    query_embeddings: dict[str, list[float]] = {}
    for field in (
        "combined_embedding",
        "changing_point_embedding",
        "part_name_embedding",
        "changing_reason_embedding",
    ):
        value = change.get(field)
        if isinstance(value, list) and value:
            query_embeddings[field] = [float(item) for item in value]
    old_query_embedding = change.get("query_embedding")
    if "combined_embedding" not in query_embeddings and isinstance(old_query_embedding, list) and old_query_embedding:
        query_embeddings["combined_embedding"] = [float(item) for item in old_query_embedding]
    return query_embeddings


def bm25_query(
    change: dict[str, Any],
    *,
    source_fields: tuple[str, ...],
    fts_columns: tuple[str, ...],
) -> str:
    query_tokens = set()
    for field in source_fields:
        query_tokens.update(tokens(change.get(field)))
    clauses = []
    for token in sorted(query_tokens):
        for column in fts_columns:
            clauses.append(f"{column}:{token}")
    return " OR ".join(clauses)


def bm25_scores(
    conn: sqlite3.Connection,
    change: dict[str, Any],
    *,
    source_fields: tuple[str, ...],
    fts_columns: tuple[str, ...],
    limit: int = 200,
) -> dict[int, float]:
    query = bm25_query(change, source_fields=source_fields, fts_columns=fts_columns)
    if not query:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT d.detail_id, bm25(dev_part_detail_fts) AS rank
            FROM dev_part_detail_fts f
            JOIN dev_part_detail d ON d.detail_id = f.detail_id
            WHERE dev_part_detail_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}

    best_rank = float(rows[0]["rank"])
    scores: dict[int, float] = {}
    for row in rows:
        rank = float(row["rank"])
        scores[int(row["detail_id"])] = 1.0 / (1.0 + max(rank - best_rank, 0.0))
    return scores


def module_part_bm25_scores(conn: sqlite3.Connection, change: dict[str, Any], *, limit: int = 200) -> dict[int, float]:
    return bm25_scores(
        conn,
        change,
        source_fields=("module_name", "part_name", "base_part_no", "new_part_no"),
        fts_columns=("part_name", "base_part_no", "new_part_no"),
        limit=limit,
    )


def change_bm25_scores(conn: sqlite3.Connection, change: dict[str, Any], *, limit: int = 200) -> dict[int, float]:
    intent_change = dict(change)
    intent_change["_change_intent_search_text"] = expanded_change_intent_text(change)
    return bm25_scores(
        conn,
        intent_change,
        source_fields=("_change_intent_search_text",),
        fts_columns=("changing_point", "changing_reason"),
        limit=limit,
    )


def exact_part_no_match(change: dict[str, Any], row: dict[str, Any]) -> bool:
    base_part_no = normalize_part_no(change.get("base_part_no"))
    if not base_part_no:
        return False
    return base_part_no in {
        normalize_part_no(row.get("base_part_no")),
        normalize_part_no(row.get("new_part_no")),
    }


def mapped_prefixes(change: dict[str, Any]) -> list[str]:
    raw_prefixes = change.get("mapped_prefixes") or []
    prefixes = []
    for prefix in raw_prefixes:
        clean_prefix = normalize_part_no(prefix)
        if clean_prefix:
            prefixes.append(clean_prefix)
    return prefixes


def mapping_description_tokens(change: dict[str, Any]) -> set[str]:
    mapping = change.get("mapping") or {}
    raw = mapping.get("primary_description_tokens") or []
    return {token.lower() for token in raw if isinstance(token, str) and len(token) >= 2}


def description_match(change: dict[str, Any], row: dict[str, Any]) -> bool:
    """매핑된 description 핵심 토큰이 행 part_name에 들어있는지.

    혼합 prefix 버킷(예: RAA = Coil/Sheet/Resin)에서 입력과 다른 부품 종류를
    떨어뜨리는 데 쓴다. HIGH 순수 prefix(예: ADC = Door)는 모든 행이 토큰을
    포함하므로 영향이 없다.
    """
    target_tokens = mapping_description_tokens(change)
    if not target_tokens:
        # 매핑 description이 없으면 정밀 필터를 적용하지 않는다(기존 동작 유지).
        return True
    return bool(target_tokens & tokens(row.get("part_name")))


def mapped_prefix_score(change: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    prefixes = mapped_prefixes(change)
    if not prefixes:
        return 0.0, []

    row_part_numbers = [
        normalize_part_no(row.get("base_part_no")),
        normalize_part_no(row.get("new_part_no")),
    ]
    for prefix in prefixes:
        if any(part_no.startswith(prefix) for part_no in row_part_numbers if part_no):
            return 1.0, [f"mapped prefix matched: {prefix}"]
    return 0.0, []


def identity_text_score(change: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    query_tokens = tokens(change.get("module_name")) | tokens(change.get("part_name"))
    row_identity_tokens = (
        tokens(row.get("part_name"))
        | tokens(row.get("base_part_no"))
        | tokens(row.get("new_part_no"))
    )
    score = 0.0
    overlap = query_tokens & row_identity_tokens
    if query_tokens and overlap:
        score += min(len(overlap) / len(query_tokens), 1.0) * 0.70
        reasons.append(f"module/part tokens: {', '.join(sorted(overlap)[:4])}")

    part_name = text(change.get("part_name")).lower()
    module_name = text(change.get("module_name")).lower()
    row_part_name = text(row.get("part_name")).lower()
    if part_name and part_name in row_part_name:
        score += 0.20
        reasons.append("part_name phrase matched")
    if module_name and module_name in row_part_name:
        score += 0.15
        reasons.append("module phrase matched part_name")
    if exact_part_no_match(change, row):
        score += 1.0
        reasons.append("base_part_no exact match")
    return min(score, 1.0), reasons


def change_intent_text_score(change: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    query_text = expanded_change_intent_text(change)
    query_tokens = tokens(query_text)
    row_text = f"{row.get('changing_point', '')} {row.get('changing_reason', '')}"
    row_tokens = tokens(row_text)
    score = 0.0

    overlap = query_tokens & row_tokens
    if query_tokens and overlap:
        score += min(len(overlap) / max(len(query_tokens), 1), 1.0) * 0.65
        reasons.append(f"change intent tokens: {', '.join(sorted(overlap)[:5])}")

    target_value = text(change.get("target_value")).lower().replace(" ", "")
    normalized_row_text = text(row_text).lower().replace(" ", "")
    if target_value and target_value in normalized_row_text:
        score += 0.45
        reasons.append(f"target value matched: {change.get('target_value')}")

    change_type_tokens = tokens(change.get("change_type"))
    if change_type_tokens and change_type_tokens & row_tokens:
        score += 0.20
        reasons.append("change_type matched")

    keyword_tokens = tokens(" ".join(normalize_keywords(change.get("change_intent_keywords"))))
    keyword_overlap = keyword_tokens & row_tokens
    if keyword_tokens and keyword_overlap:
        score += min(len(keyword_overlap) / max(len(keyword_tokens), 1), 1.0) * 0.25
        reasons.append(f"intent keywords: {', '.join(sorted(keyword_overlap)[:5])}")

    return min(score, 1.0), reasons


def hybrid_score(
    *,
    dense: dict[str, float],
    module_part_bm25: float,
    change_bm25: float,
    identity_text: float,
    change_intent_text: float,
    mapping_prefix: float,
    exact_part_no: bool,
    allow_fallback: bool,
) -> tuple[float, dict[str, Any], list[str]]:
    dense_components = {
        "dense_combined": dense.get("combined_embedding", 0.0),
        "dense_changing_point": dense.get("changing_point_embedding", 0.0),
        "dense_part_name": dense.get("part_name_embedding", 0.0),
        "dense_reason": dense.get("changing_reason_embedding", 0.0),
    }
    identity_recall = (
        IDENTITY_RECALL_WEIGHTS["dense_part_name"] * dense_components["dense_part_name"]
        + IDENTITY_RECALL_WEIGHTS["module_part_bm25"] * module_part_bm25
        + IDENTITY_RECALL_WEIGHTS["identity_text"] * identity_text
        + IDENTITY_RECALL_WEIGHTS["dense_combined"] * dense_components["dense_combined"]
    )
    rerank_score = (
        RERANK_WEIGHTS["identity_recall"] * identity_recall
        + RERANK_WEIGHTS["dense_changing_point"] * dense_components["dense_changing_point"]
        + RERANK_WEIGHTS["dense_reason"] * dense_components["dense_reason"]
        + RERANK_WEIGHTS["change_bm25"] * change_bm25
        + RERANK_WEIGHTS["change_intent_text"] * change_intent_text
        + RERANK_WEIGHTS["mapping_prefix"] * mapping_prefix
    )
    fallback = identity_text if allow_fallback else 0.0
    score = rerank_score + fallback
    if exact_part_no:
        score += 1.0

    components: dict[str, Any] = {
        **{key: round(value, 4) for key, value in dense_components.items()},
        "module_part_bm25": round(module_part_bm25, 4),
        "change_bm25": round(change_bm25, 4),
        "identity_text": round(identity_text, 4),
        "change_intent_text": round(change_intent_text, 4),
        "mapping_prefix": round(mapping_prefix, 4),
        "identity_recall": round(identity_recall, 4),
        "rerank_score": round(rerank_score, 4),
        "fallback_text": round(fallback, 4),
        "exact_part_no": exact_part_no,
    }
    reasons = []
    if identity_recall > 0:
        reasons.append("module/part recall matched")
    if dense_components["dense_changing_point"] > 0 or dense_components["dense_reason"] > 0 or change_bm25 > 0:
        reasons.append("change text reranked")
    if change_intent_text > 0:
        reasons.append("change intent matched")
    if fallback > 0:
        reasons.append("fallback text score used")
    if exact_part_no:
        reasons.append("base_part_no exact match")
    if mapping_prefix > 0:
        reasons.append("mapping prefix gate matched")
    return score, components, reasons


def has_identity_query(change: dict[str, Any]) -> bool:
    return bool(
        tokens(change.get("module_name"))
        or tokens(change.get("part_name"))
        or tokens(change.get("description"))
        or normalize_part_no(change.get("base_part_no"))
        or normalize_part_no(change.get("new_part_no"))
        or mapped_prefixes(change)
    )


def include_candidate(components: dict[str, Any], *, identity_query: bool, prefix_gate: bool) -> bool:
    if not identity_query:
        return False
    if prefix_gate:
        return bool(components.get("exact_part_no") or components.get("mapping_prefix", 0.0) > 0)
    return bool(
        components.get("exact_part_no")
        or components.get("identity_recall", 0.0) >= 0.08
        or components.get("dense_part_name", 0.0) >= 0.45
        or components.get("module_part_bm25", 0.0) >= 0.20
        or components.get("identity_text", 0.0) >= 0.20
        or components.get("dense_combined", 0.0) >= COMBINED_DENSE_INCLUDE_THRESHOLD
    )


# ── 후보 추천 RRF 융합 노브 (ablation 결론 반영, 2026-06-16) ───────────────────
# lg+sqlite ablation 결론: 후보 회수는 dense(의미) + parts(부품정체) 2채널이 최강.
# change-text lexical(BM25/intent/synonym)·LLM 재정렬은 회수를 희석/저하시키고,
# prefix 하드게이트는 정답을 통째로 제외한다(골든셋 보고서 found@10 1.00→0.66).
# 따라서: 2채널 RRF(k=60, 동등가중) + prefix는 soft 가산점 + exact는 강한 보너스.
RRF_K = 60
RRF_DENSE_WEIGHT = 1.0
RRF_PARTS_WEIGHT = 1.0          # parts 과가중(>1.5)은 lg에서 회수 저하 → 1.0 고정
RRF_PREFIX_SOFT_WEIGHT = 0.4    # prefix 일치 soft 가산점(코퍼스 제외 없음)
EXACT_PART_NO_BONUS = 1.0       # base_part_no exact는 항상 최상위


def _rrf_rank_map(score_by_id: dict[int, float]) -> dict[int, int]:
    """detail_id → 1-based rank (점수 내림차순, 동점은 id로 안정 정렬)."""
    ordered = sorted(score_by_id.items(), key=lambda kv: (-kv[1], kv[0]))
    return {detail_id: i + 1 for i, (detail_id, _score) in enumerate(ordered)}


def recommend_change_candidates(
    conn: sqlite3.Connection,
    change: dict[str, Any],
    *,
    limit: int = 5,
    related_limit: int = 20,
) -> dict[str, Any]:
    """과거 변경이력에서 후보 부품을 회수 — **dense + parts 2채널 RRF**.

    ablation(lg Postgres/BGE-M3 + 이 백엔드 SQLite/OpenAI 교차검증) 최적 구성:
      - 채널1 dense: combined 임베딩(변경 전체 의미; 없으면 가용 dense 평균).
      - 채널2 parts: 부품 정체(part_name 임베딩·part_no/name BM25·토큰겹침의 최댓값).
      - RRF(k=60) 동등가중 융합. change-text BM25/intent/synonym 채널은 제거(회수 희석).
      - prefix는 **하드게이트 폐기 → soft 가산점**(전체 코퍼스 검색, 정답 제외 없음).
      - exact base_part_no는 강한 보너스. LLM 재정렬 없음.
      - 회수 깊이(limit)가 최대 회수 레버 — 호출측 top_k를 키우면 recall↑(precision↕).
    반환 계약(change_id/input/lookup_mode/candidates, related_parts/subtree)은 불변.
    """
    query_embeddings = collect_query_embeddings(change)
    candidate_rows = history_rows(conn)  # ★ soft 정책: 항상 전체 코퍼스(하드 prefix 제외 폐기)
    rows_by_id = {int(row["detail_id"]): row for row in candidate_rows}

    dense_scores = multi_vector_scores(conn, query_embeddings)  # {id: {field: cos}}
    identity_bm25 = module_part_bm25_scores(conn, change, limit=max(limit * 40, 400))

    # 채널 1 — dense 의미 (combined; 없으면 가용 dense 필드 평균)
    dense_by_id: dict[int, float] = {}
    for detail_id, fields in dense_scores.items():
        value = fields.get("combined_embedding")
        if value is None and fields:
            value = sum(fields.values()) / len(fields)
        if value and value > 0:
            dense_by_id[detail_id] = value

    # 채널 2 — parts 정체 (부품명 dense · 부품명/품번 BM25 · 토큰겹침/exact 중 최댓값)
    parts_by_id: dict[int, float] = {}
    for detail_id, row in rows_by_id.items():
        name_dense = dense_scores.get(detail_id, {}).get("part_name_embedding", 0.0)
        name_lex = identity_bm25.get(detail_id, 0.0)
        id_text, _ = identity_text_score(change, row)
        parts_score = max(name_dense, name_lex, id_text)
        if parts_score > 0:
            parts_by_id[detail_id] = parts_score

    dense_rank = _rrf_rank_map(dense_by_id)
    parts_rank = _rrf_rank_map(parts_by_id)

    scored: list[tuple[float, list[str], dict[str, Any], dict[str, Any]]] = []
    for detail_id, row in rows_by_id.items():
        rrf = 0.0
        components: dict[str, Any] = {}
        reasons: list[str] = []
        if detail_id in dense_rank:
            rrf += RRF_DENSE_WEIGHT / (RRF_K + dense_rank[detail_id])
            components["dense"] = round(dense_by_id[detail_id], 4)
            components["dense_rank"] = dense_rank[detail_id]
            reasons.append("dense(semantic) match")
        if detail_id in parts_rank:
            rrf += RRF_PARTS_WEIGHT / (RRF_K + parts_rank[detail_id])
            components["parts"] = round(parts_by_id[detail_id], 4)
            components["parts_rank"] = parts_rank[detail_id]
            reasons.append("part-identity match")
        prefix_score, prefix_reasons = mapped_prefix_score(change, row)
        if prefix_score > 0:  # soft boost (게이트 아님 — 코퍼스에서 제외하지 않는다)
            rrf += RRF_PREFIX_SOFT_WEIGHT / RRF_K
            components["mapping_prefix_soft"] = True
            reasons.extend(prefix_reasons)
        if exact_part_no_match(change, row):
            rrf += EXACT_PART_NO_BONUS
            components["exact_part_no"] = True
            reasons.append("base_part_no exact match")
        if rrf <= 0 or not math.isfinite(rrf):
            continue
        components["rrf_score"] = round(rrf, 6)
        scored.append((rrf, reasons, row, components))

    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = []
    for rank, (score, reasons, row, components) in enumerate(scored[:limit]):
        candidate = compact_candidate(row, score=score, reasons=reasons, score_components=components)
        # 연관 부품/subtree는 1순위 후보만 미리 계산(비용 폭증 방지). 2순위 이하는 온디맨드.
        if related_limit > 0 and row.get("detail_id") is not None and rank == 0:
            candidate["related_parts"] = get_related_parts(conn, int(row["detail_id"]), limit=related_limit)
            candidate["candidate_subtree"] = get_candidate_subtree_summary(
                conn,
                int(row["detail_id"]),
                limit=max(related_limit, 20),
            )
        candidates.append(candidate)

    if text(change.get("base_part_no")):
        lookup_mode = "base_part_no"
    elif dense_by_id and parts_by_id:
        lookup_mode = "dense_parts_rrf"
    elif dense_by_id:
        lookup_mode = "dense_only"
    elif parts_by_id:
        lookup_mode = "parts_only"
    else:
        lookup_mode = "empty"
    return {
        "change_id": change.get("change_id"),
        "input": change,
        "lookup_mode": lookup_mode,
        "candidates": candidates,
    }


def _depth_from_level(level: Any) -> int | None:
    match = re.search(r"(\d+)\s*$", text(level))
    return int(match.group(1)) if match else None


def _index_by_part_no(rows: list[dict[str, Any]], base_pno: str) -> int | None:
    if not base_pno:
        return None
    for index, row in enumerate(rows):
        if base_pno in {normalize_part_no(row.get("base_part_no")), normalize_part_no(row.get("new_part_no"))}:
            return index
    return None


def master_change_action(change: dict[str, Any]) -> str:
    """변경점(Design Points) 한 줄의 액션을 추정한다.

    - 명시 action(change/add/delete)이 있으면 그대로.
    - 상세 변경점에 '삭제'가 있으면 delete.
    - base 품번이 없으면 add(신규 부품).
    - New P/No == base 품번이면 keep(공용, BOM 변화 없음 → 건너뜀).
    - 그 외 change.
    """
    explicit = text(change.get("action")).lower()
    if explicit in {"change", "add", "delete"}:
        return explicit
    change_point = text(change.get("change_point")).lower()
    if "삭제" in change_point or "delete" in change_point:
        return "delete"
    base = normalize_part_no(change.get("base_part_no"))
    new = normalize_part_no(change.get("new_part_no"))
    if not base:
        return "add"
    if new and new == base:
        return "keep"  # 공용: new=base → BOM 변화 없음
    return "change"


def _master_added_row(change: dict[str, Any], *, line_order: int) -> dict[str, Any]:
    new_pno = text(change.get("new_part_no"))
    return {
        "detail_id": None,
        "source_candidate_detail_id": None,
        "parent_detail_id": None,
        "master_id": None,
        "line_order": line_order,
        "row_no": None,
        "bom_level": text(change.get("bom_level")) or text(change.get("lev")),
        "bom_depth": _depth_from_level(change.get("bom_level") or change.get("lev")),
        "part_type": text(change.get("part_type")),
        "base_part_no": text(change.get("base_part_no")),
        "new_part_no": new_pno,
        "normalized_new_part_no": normalize_part_no(new_pno),
        "part_name": text(change.get("part_name")),
        "base_qty": "",
        "new_qty": text(change.get("qty") or change.get("new_qty")),
        "normalized_new_qty": text(change.get("qty") or change.get("new_qty")),
        "changing_point": text(change.get("change_point")),
        "changing_reason": text(change.get("change_reason")),
        "supplier": text(change.get("supplier")),
        "classification": text(change.get("classification")) or "New",
        "applied_action": "add",
    }


def apply_master_changes(
    conn: sqlite3.Connection,
    *,
    base_master_id: int | None = None,
    base_bom: list[dict[str, Any]] | None = None,
    changes: list[dict[str, Any]],
    propagate_change_upward: bool = True,
) -> dict[str, Any]:
    """심의표 변경점(master)을 base BOM에 직접 반영한다.

    apply_selected_changes는 과거 이력 후보(candidate_detail_id, DB 조회)가 필요하지만,
    여기서는 변경점 행 자체가 base/new 품번을 갖고 있으므로 DB 후보 없이 바로 적용한다.
    공용(new=base)은 건너뛰고, 신규/변경은 new_part_no(TBD 포함)로 덮어쓰며 상위 전파한다.
    """
    if base_bom is None:
        if base_master_id is None:
            raise ValueError("Either base_master_id or base_bom is required")
        rows = load_bom_rows(conn, base_master_id)
    else:
        rows = [dict(row) for row in base_bom]

    change_list: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    skipped_common = 0

    for change in changes:
        action = master_change_action(change)
        if action == "keep":
            skipped_common += 1
            continue

        base_pno = normalize_part_no(change.get("base_part_no"))
        new_pno = text(change.get("new_part_no"))
        target_index = _index_by_part_no(rows, base_pno)

        if action == "delete":
            if target_index is None:
                unmatched.append({"change": change, "reason": "target not found"})
                continue
            removed_indexes = descendant_indexes(rows, target_index)
            removed_rows = [rows[i] for i in removed_indexes]
            for i in reversed(removed_indexes):
                rows.pop(i)
            change_list.append({
                "change_id": change.get("no"),
                "action": "delete",
                "part_name": change.get("part_name"),
                "base_part_no": change.get("base_part_no"),
                "removed": removed_rows,
            })
            continue

        if action == "add":
            # 끝에 추가(신규 부품). target_index가 있으면 그 뒤에.
            line_order = max([int(r.get("line_order") or 0) for r in rows] or [0]) + 1
            added = _master_added_row(change, line_order=line_order)
            insert_at = len(rows) if target_index is None else target_index + 1
            rows.insert(insert_at, added)
            change_list.append({
                "change_id": change.get("no"),
                "action": "add",
                "part_name": change.get("part_name"),
                "added": added,
                "change_point": change.get("change_point"),
            })
            continue

        # change: 대상이 base BOM에 없으면 조용히 추가하지 않고 unmatched로 보고한다.
        if target_index is None:
            unmatched.append({
                "change": change,
                "reason": "base_part_no가 base BOM에 없음(버전/데이터 불일치 확인)",
            })
            continue

        before = dict(rows[target_index])
        rows[target_index].update({
            "new_part_no": new_pno or rows[target_index].get("new_part_no"),
            "normalized_new_part_no": normalize_part_no(new_pno),
            "new_qty": text(change.get("qty")) or rows[target_index].get("new_qty"),
            "changing_point": text(change.get("change_point")) or rows[target_index].get("changing_point"),
            "changing_reason": text(change.get("change_reason")) or rows[target_index].get("changing_reason"),
            "classification": text(change.get("classification")) or "Change",
            "applied_action": "change",
        })
        change_list.append({
            "change_id": change.get("no"),
            "action": "change",
            "part_name": change.get("part_name"),
            "before": before,
            "after": dict(rows[target_index]),
            "change_point": change.get("change_point"),
        })
        if propagate_change_upward:
            propagate_change_to_ancestors(
                rows,
                target_index,
                change_id=change.get("no"),
                reason=text(change.get("part_name")),
                change_list=change_list,
            )

    return {
        "base_master_id": base_master_id,
        "new_bom": rows,
        "change_list": change_list,
        "unmatched": unmatched,
        "skipped_common": skipped_common,
    }


def get_detail(conn: sqlite3.Connection, detail_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
               m.base_model, m.new_model
        FROM dev_part_detail d
        JOIN dev_part_master m ON m.master_id = d.master_id
        WHERE d.detail_id = ?
        """,
        (detail_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_subtree_rows(conn: sqlite3.Connection, detail_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    target = get_detail(conn, detail_id)
    if not target:
        return []
    target_depth = target.get("bom_depth")
    rows = [target]
    if target_depth is None:
        return rows[:limit]

    # subtree는 line_order 순으로 깊이가 target 이하가 나오기 전까지다.
    # SQL LIMIT으로 fetch 자체를 묶어, 큰 master 상단 후보가 나머지 전체를
    # materialize하지 않게 한다. (+1은 경계 행 확인용)
    child_rows = conn.execute(
        """
        SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
               m.base_model, m.new_model
        FROM dev_part_detail d
        JOIN dev_part_master m ON m.master_id = d.master_id
        WHERE d.master_id = ?
          AND d.line_order > ?
        ORDER BY d.line_order
        LIMIT ?
        """,
        (target["master_id"], target["line_order"], max(limit, 1)),
    ).fetchall()
    for row in child_rows:
        item = row_to_dict(row)
        depth = item.get("bom_depth")
        if depth is not None and depth <= target_depth:
            break
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def compact_subtree(row: dict[str, Any], *, root_depth: int | None = None, relation: str = "descendant") -> dict[str, Any]:
    depth = row.get("bom_depth")
    relative_depth = None
    if depth is not None and root_depth is not None:
        relative_depth = int(depth) - int(root_depth)
    return {
        "relation": relation,
        "detail_id": row.get("detail_id"),
        "parent_detail_id": row.get("parent_detail_id"),
        "master_id": row.get("master_id"),
        "line_order": row.get("line_order"),
        "level": depth,
        "relative_depth": relative_depth,
        "bom_level": row.get("bom_level"),
        "part_type": row.get("part_type"),
        "part_name": row.get("part_name"),
        "base_part_no": row.get("base_part_no"),
        "new_part_no": row.get("new_part_no"),
        "base_qty": row.get("base_qty"),
        "new_qty": row.get("new_qty"),
        "change_point": row.get("changing_point"),
        "change_reason": row.get("changing_reason"),
        "classification": row.get("classification"),
    }


def get_candidate_subtree_summary(conn: sqlite3.Connection, detail_id: int, *, limit: int = 30) -> dict[str, Any]:
    # 전체 subtree를 한 번만 스캔하고, 표시는 limit개로 자른다(중복 스캔 제거).
    full_rows = get_subtree_rows(conn, detail_id, limit=1000)
    if not full_rows:
        return {"root": None, "descendant_count": 0, "parts": []}
    full_count = len(full_rows)
    rows = full_rows[: max(limit, 1)]
    root_depth = rows[0].get("bom_depth")
    parts = [
        compact_subtree(row, root_depth=root_depth, relation="target" if index == 0 else "descendant")
        for index, row in enumerate(rows)
    ]
    return {
        "root": parts[0],
        "descendant_count": max(full_count - 1, 0),
        "shown_count": len(parts),
        "parts": parts,
    }


def get_related_parts(conn: sqlite3.Connection, detail_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    target = get_detail(conn, detail_id)
    if not target:
        return []

    related: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add_related(row: dict[str, Any], relation: str) -> None:
        row_detail_id = row.get("detail_id")
        if row_detail_id is None:
            return
        detail_key = int(row_detail_id)
        if detail_key in seen or len(related) >= limit:
            return
        seen.add(detail_key)
        item = dict(row)
        item["relation"] = relation
        related.append(compact_related(item))

    parent_id = target.get("parent_detail_id")
    ancestors: list[dict[str, Any]] = []
    while parent_id:
        parent = get_detail(conn, int(parent_id))
        if not parent:
            break
        ancestors.append(parent)
        parent_id = parent.get("parent_detail_id")

    for row in reversed(ancestors):
        add_related(row, "ancestor")

    add_related(target, "target")

    target_depth = target.get("bom_depth")
    if target_depth is not None:
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
                   m.base_model, m.new_model
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE d.master_id = ?
              AND d.line_order > ?
            ORDER BY d.line_order
            """,
            (target["master_id"], target["line_order"]),
        ).fetchall()
        for row in rows:
            item = row_to_dict(row)
            depth = item.get("bom_depth")
            if depth is not None and depth <= target_depth:
                break
            add_related(item, "descendant")
            if len(related) >= limit:
                break

    if len(related) < limit and target.get("parent_detail_id"):
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
                   m.base_model, m.new_model
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE d.master_id = ?
              AND d.parent_detail_id = ?
            ORDER BY d.line_order
            """,
            (target["master_id"], target["parent_detail_id"]),
        ).fetchall()
        for row in rows:
            add_related(row_to_dict(row), "sibling")
            if len(related) >= limit:
                break

    change_point = text(target.get("changing_point"))
    change_reason = text(target.get("changing_reason"))
    if len(related) < limit and (change_point or change_reason):
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
                   m.base_model, m.new_model
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE d.master_id = ?
              AND (
                    (? <> '' AND COALESCE(d.changing_point, '') = ?)
                 OR (? <> '' AND COALESCE(d.changing_reason, '') = ?)
              )
            ORDER BY d.line_order
            """,
            (target["master_id"], change_point, change_point, change_reason, change_reason),
        ).fetchall()
        for row in rows:
            add_related(row_to_dict(row), "same_change")
            if len(related) >= limit:
                break

    return related[:limit]


def get_connected_parts(conn: sqlite3.Connection, detail_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """신규 부품 추가 시 함께 추가할 후보(연동 주변부품)를 과거 이력에서 찾는다.

    회의록 유형(2): 모터 추가 시 고정 브라켓/나사류처럼, 과거 BOM에서 이 부품과
    같은 부모 아래 있던 형제(sibling) + 이 부품의 하위(descendant)를 묶어 제안한다.
    실제 추가 여부는 사용자(HITL)가 선택한다.
    """
    target = get_detail(conn, detail_id)
    if not target:
        return []

    connected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(row: dict[str, Any], relation: str) -> None:
        rid = row.get("detail_id")
        if rid is None or int(rid) in seen or int(rid) == detail_id or len(connected) >= limit:
            return
        seen.add(int(rid))
        item = compact_related({**row, "relation": relation})
        connected.append(item)

    # 같은 부모 아래 형제(함께 쓰이는 주변 부품).
    if target.get("parent_detail_id"):
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
                   m.base_model, m.new_model
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE d.master_id = ? AND d.parent_detail_id = ?
            ORDER BY d.line_order
            """,
            (target["master_id"], target["parent_detail_id"]),
        ).fetchall()
        for row in rows:
            add(row_to_dict(row), "sibling")

    # 이 부품의 하위(서브 어셈블리 구성품).
    target_depth = target.get("bom_depth")
    if target_depth is not None and len(connected) < limit:
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region,
                   m.base_model, m.new_model
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE d.master_id = ? AND d.line_order > ?
            ORDER BY d.line_order
            LIMIT ?
            """,
            (target["master_id"], target["line_order"], max(limit * 4, 40)),
        ).fetchall()
        for row in rows:
            item = row_to_dict(row)
            depth = item.get("bom_depth")
            if depth is not None and depth <= target_depth:
                break
            add(item, "descendant")
            if len(connected) >= limit:
                break

    return connected[:limit]


def compact_related(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation": row.get("relation"),
        "detail_id": row.get("detail_id"),
        "master_id": row.get("master_id"),
        "line_order": row.get("line_order"),
        "level": row.get("bom_depth"),
        "bom_level": row.get("bom_level"),
        "part_name": row.get("part_name"),
        "base_part_no": row.get("base_part_no"),
        "new_part_no": row.get("new_part_no"),
        "new_qty": row.get("new_qty"),
        "change_point": row.get("changing_point"),
        "change_reason": row.get("changing_reason"),
        "classification": row.get("classification"),
    }


def load_bom_rows(conn: sqlite3.Connection, master_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT detail_id, parent_detail_id, master_id, line_order, row_no,
               bom_level, bom_depth, part_type, base_part_no, new_part_no,
               normalized_new_part_no, part_name, base_qty, new_qty,
               normalized_new_qty, changing_point, changing_reason,
               supplier, classification
        FROM dev_part_detail
        WHERE master_id = ?
        ORDER BY line_order
        """,
        (master_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def infer_action(selection: dict[str, Any], candidate: dict[str, Any]) -> str:
    action = text(selection.get("action")).lower()
    if action in {"change", "add", "delete"}:
        return action
    classification = text(candidate.get("classification")).lower()
    if "delete" in classification or "remove" in classification:
        return "delete"
    if "add" in classification or not text(candidate.get("base_part_no")):
        return "add"
    return "change"


def find_target_index(rows: list[dict[str, Any]], selection: dict[str, Any], candidate: dict[str, Any]) -> int | None:
    target_part_no = normalize_part_no(selection.get("target_base_part_no"))
    if not target_part_no:
        target_part_no = normalize_part_no(candidate.get("base_part_no"))
    if target_part_no:
        for index, row in enumerate(rows):
            if target_part_no in {
                normalize_part_no(row.get("base_part_no")),
                normalize_part_no(row.get("new_part_no")),
            }:
                return index

    target_part_name = text(selection.get("target_part_name")) or text(candidate.get("part_name"))
    if target_part_name:
        needle = target_part_name.lower()
        for index, row in enumerate(rows):
            if needle and needle in text(row.get("part_name")).lower():
                return index
    return None


def ancestor_indexes(rows: list[dict[str, Any]], index: int) -> list[int]:
    """line-order BOM에서 index 행의 상위(부모→루트) 인덱스 체인을 깊이 기준으로 찾는다."""
    base_depth = rows[index].get("bom_depth")
    if base_depth is None:
        return []
    ancestors: list[int] = []
    current_depth = int(base_depth)
    for prev_index in range(index - 1, -1, -1):
        depth = rows[prev_index].get("bom_depth")
        if depth is None:
            continue
        depth = int(depth)
        if depth < current_depth:
            ancestors.append(prev_index)
            current_depth = depth
            if current_depth <= 0:
                break
    return ancestors


# 직접 변경/추가된 행은 상위 전파로 덮어쓰지 않는다.
DIRECT_CHANGE_ACTIONS = {"change", "add", "subtree_replace", "subtree_child"}


def propagate_change_to_ancestors(
    rows: list[dict[str, Any]],
    target_index: int,
    *,
    change_id: Any,
    reason: str,
    change_list: list[dict[str, Any]],
) -> None:
    """하위 부품 변경을 상위 Assembly로 도미노 전파(회의록 유형1 계층 변경 규칙).

    상위 어셈블리는 내용이 바뀌었으므로 품번 재발행이 필요하다. 실제 신규 품번은
    PLM에서 부여되므로 new_part_no를 'TBD'로 표시하고 변경 리스트에 남긴다.
    """
    for anc_index in ancestor_indexes(rows, target_index):
        row = rows[anc_index]
        applied = text(row.get("applied_action"))
        # 직접 변경/추가 대상이거나 이미 전파된 행은 건너뛴다.
        if applied in DIRECT_CHANGE_ACTIONS or applied == "propagated_change":
            continue
        before = dict(row)
        existing_reason = text(row.get("changing_reason"))
        row.update(
            {
                "new_part_no": "TBD",
                "normalized_new_part_no": "",
                "classification": "Change(하위전파)",
                "applied_action": "propagated_change",
                "changing_point": text(row.get("changing_point")) or "하위 부품 변경에 따른 상위 Assembly 재발행",
                "changing_reason": existing_reason or (f"하위 변경 전파: {reason}" if reason else "하위 부품 변경 전파"),
            }
        )
        change_list.append(
            {
                "change_id": change_id,
                "action": "propagated_change",
                "ancestor_part_name": row.get("part_name"),
                "ancestor_base_part_no": row.get("base_part_no"),
                "level": row.get("bom_depth"),
                "before": before,
                "after": dict(row),
            }
        )


def descendant_indexes(rows: list[dict[str, Any]], index: int) -> list[int]:
    base_depth = rows[index].get("bom_depth")
    if base_depth is None:
        return [index]
    indexes = [index]
    for next_index in range(index + 1, len(rows)):
        depth = rows[next_index].get("bom_depth")
        if depth is not None and depth <= base_depth:
            break
        indexes.append(next_index)
    return indexes


def subtree_rows_from_bom(rows: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    return [rows[row_index] for row_index in descendant_indexes(rows, index)]


def subtree_identity(row: dict[str, Any]) -> str:
    return text(row.get("part_name")).lower() or normalize_part_no(row.get("new_part_no")) or normalize_part_no(row.get("base_part_no"))


def compare_subtrees(base_subtree: list[dict[str, Any]], candidate_subtree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_by_key = {subtree_identity(row): row for row in base_subtree if subtree_identity(row)}
    candidate_by_key = {subtree_identity(row): row for row in candidate_subtree if subtree_identity(row)}
    diff: list[dict[str, Any]] = []

    for key, candidate_row in candidate_by_key.items():
        base_row = base_by_key.get(key)
        if not base_row:
            diff.append({"action": "add", "part_name": candidate_row.get("part_name"), "candidate": compact_subtree(candidate_row)})
            continue
        base_part_no = normalize_part_no(base_row.get("new_part_no") or base_row.get("base_part_no"))
        candidate_part_no = normalize_part_no(candidate_row.get("new_part_no") or candidate_row.get("base_part_no"))
        base_qty = text(base_row.get("new_qty") or base_row.get("base_qty"))
        candidate_qty = text(candidate_row.get("new_qty") or candidate_row.get("base_qty"))
        if base_part_no != candidate_part_no or base_qty != candidate_qty:
            diff.append(
                {
                    "action": "change",
                    "part_name": candidate_row.get("part_name"),
                    "base_part_no": base_row.get("base_part_no"),
                    "base_new_part_no": base_row.get("new_part_no"),
                    "candidate_base_part_no": candidate_row.get("base_part_no"),
                    "candidate_new_part_no": candidate_row.get("new_part_no"),
                    "base_qty": base_qty,
                    "candidate_qty": candidate_qty,
                }
            )
        else:
            diff.append({"action": "keep", "part_name": candidate_row.get("part_name")})

    for key, base_row in base_by_key.items():
        if key not in candidate_by_key:
            diff.append({"action": "delete", "part_name": base_row.get("part_name"), "base": compact_subtree(base_row)})

    return diff


def build_subtree_replacement_preview(
    conn: sqlite3.Connection,
    *,
    base_master_id: int,
    selection: dict[str, Any],
) -> dict[str, Any]:
    rows = load_bom_rows(conn, base_master_id)
    candidate = get_detail(conn, int(selection["candidate_detail_id"]))
    if not candidate:
        return {"status": "not_found", "reason": "candidate_detail_id was not found"}
    target_index = find_target_index(rows, selection, candidate)
    if target_index is None:
        return {"status": "target_not_found", "candidate": compact_candidate(candidate), "reason": "target row not found"}

    base_subtree = subtree_rows_from_bom(rows, target_index)
    candidate_subtree = get_subtree_rows(conn, int(candidate["detail_id"]), limit=500)
    diff = compare_subtrees(base_subtree, candidate_subtree)
    return {
        "status": "ok",
        "target": compact_subtree(rows[target_index], relation="target"),
        "candidate": compact_candidate(candidate),
        "base_subtree_count": len(base_subtree),
        "candidate_subtree_count": len(candidate_subtree),
        "base_subtree": [compact_subtree(row, root_depth=rows[target_index].get("bom_depth"), relation="target" if index == 0 else "descendant") for index, row in enumerate(base_subtree)],
        "candidate_subtree": [compact_subtree(row, root_depth=candidate_subtree[0].get("bom_depth") if candidate_subtree else None, relation="target" if index == 0 else "descendant") for index, row in enumerate(candidate_subtree)],
        "diff": diff,
        "summary": {
            "change": sum(1 for item in diff if item.get("action") == "change"),
            "add": sum(1 for item in diff if item.get("action") == "add"),
            "delete": sum(1 for item in diff if item.get("action") == "delete"),
            "keep": sum(1 for item in diff if item.get("action") == "keep"),
        },
    }


def clone_candidate_for_bom(candidate: dict[str, Any], *, line_order: int) -> dict[str, Any]:
    return {
        "detail_id": None,
        "source_candidate_detail_id": candidate.get("detail_id"),
        "parent_detail_id": None,
        "master_id": None,
        "line_order": line_order,
        "row_no": None,
        "bom_level": candidate.get("bom_level"),
        "bom_depth": candidate.get("level") or candidate.get("bom_depth"),
        "part_type": candidate.get("part_type", ""),
        "base_part_no": candidate.get("base_part_no"),
        "new_part_no": candidate.get("new_part_no"),
        "normalized_new_part_no": normalize_part_no(candidate.get("new_part_no")),
        "part_name": candidate.get("part_name"),
        "base_qty": candidate.get("base_qty", ""),
        "new_qty": candidate.get("new_qty", ""),
        "normalized_new_qty": text(candidate.get("new_qty")),
        "changing_point": candidate.get("change_point") or candidate.get("changing_point"),
        "changing_reason": candidate.get("change_reason") or candidate.get("changing_reason"),
        "supplier": candidate.get("supplier", ""),
        "classification": candidate.get("classification") or "Add",
        "applied_action": "add",
    }


def clone_candidate_subtree_for_bom(
    candidate_subtree: list[dict[str, Any]],
    *,
    target_row: dict[str, Any],
    line_order_start: int,
) -> list[dict[str, Any]]:
    if not candidate_subtree:
        return []
    root_depth = candidate_subtree[0].get("bom_depth") or 0
    target_depth = target_row.get("bom_depth") or root_depth
    cloned = []
    for offset, row in enumerate(candidate_subtree):
        row_depth = row.get("bom_depth") if row.get("bom_depth") is not None else root_depth
        new_depth = int(target_depth) + (int(row_depth) - int(root_depth))
        cloned.append(
            {
                "detail_id": None,
                "source_candidate_detail_id": row.get("detail_id"),
                "parent_detail_id": target_row.get("parent_detail_id") if offset == 0 else None,
                "master_id": target_row.get("master_id"),
                "line_order": line_order_start + offset,
                "row_no": None,
                "bom_level": row.get("bom_level"),
                "bom_depth": new_depth,
                "part_type": row.get("part_type", ""),
                "base_part_no": row.get("base_part_no"),
                "new_part_no": row.get("new_part_no"),
                "normalized_new_part_no": normalize_part_no(row.get("new_part_no")),
                "part_name": row.get("part_name"),
                "base_qty": row.get("base_qty", ""),
                "new_qty": row.get("new_qty", ""),
                "normalized_new_qty": text(row.get("new_qty")),
                "changing_point": row.get("changing_point"),
                "changing_reason": row.get("changing_reason"),
                "supplier": row.get("supplier", ""),
                "classification": row.get("classification") or ("Change" if offset == 0 else ""),
                "applied_action": "subtree_replace" if offset == 0 else "subtree_child",
            }
        )
    return cloned


def apply_selected_changes(
    conn: sqlite3.Connection,
    *,
    base_master_id: int | None = None,
    base_bom: list[dict[str, Any]] | None = None,
    selections: list[dict[str, Any]],
    propagate_change_upward: bool = True,
) -> dict[str, Any]:
    if base_bom is None:
        if base_master_id is None:
            raise ValueError("Either base_master_id or base_bom is required")
        rows = load_bom_rows(conn, base_master_id)
    else:
        rows = [dict(row) for row in base_bom]

    change_list: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for selection in selections:
        detail_id = selection.get("candidate_detail_id")
        if detail_id is None:
            unmatched.append({"selection": selection, "reason": "candidate_detail_id is required"})
            continue

        candidate = get_detail(conn, int(detail_id))
        if not candidate:
            unmatched.append({"selection": selection, "reason": "candidate_detail_id was not found"})
            continue

        action = infer_action(selection, candidate)
        target_index = find_target_index(rows, selection, candidate)
        include_subtree = bool(selection.get("include_subtree"))

        if action == "delete":
            if target_index is None:
                unmatched.append({"selection": selection, "candidate": compact_candidate(candidate), "reason": "target row not found"})
                continue
            removed_indexes = descendant_indexes(rows, target_index)
            removed_rows = [rows[index] for index in removed_indexes]
            for index in reversed(removed_indexes):
                rows.pop(index)
            change_list.append(
                {
                    "change_id": selection.get("change_id"),
                    "action": action,
                    "candidate": compact_candidate(candidate),
                    "removed": removed_rows,
                }
            )
            continue

        if action == "add":
            insert_at = len(rows) if target_index is None else target_index + 1
            line_order = max([int(row.get("line_order") or 0) for row in rows] or [0]) + 1
            added = clone_candidate_for_bom(candidate, line_order=line_order)
            rows.insert(insert_at, added)
            change_list.append(
                {
                    "change_id": selection.get("change_id"),
                    "action": action,
                    "candidate": compact_candidate(candidate),
                    "added": added,
                    "inserted_after_index": None if target_index is None else target_index,
                }
            )
            # 회의록 유형(2): 사용자가 승인한 연동 주변부품(번들)도 함께 추가.
            bundle_ids = selection.get("bundle_detail_ids") or []
            for bundle_id in bundle_ids:
                try:
                    bundle_detail = get_detail(conn, int(bundle_id))
                except (TypeError, ValueError):
                    bundle_detail = None
                if not bundle_detail:
                    unmatched.append({"selection": selection, "reason": f"bundle detail {bundle_id} not found"})
                    continue
                insert_at += 1
                line_order += 1
                bundle_added = clone_candidate_for_bom(bundle_detail, line_order=line_order)
                bundle_added["applied_action"] = "add_bundle"
                rows.insert(insert_at, bundle_added)
                change_list.append(
                    {
                        "change_id": selection.get("change_id"),
                        "action": "add_bundle",
                        "candidate": compact_candidate(bundle_detail),
                        "added": bundle_added,
                        "bundle_of_detail_id": candidate.get("detail_id"),
                    }
                )
            continue

        if target_index is None:
            unmatched.append({"selection": selection, "candidate": compact_candidate(candidate), "reason": "target row not found"})
            continue

        if include_subtree:
            before_indexes = descendant_indexes(rows, target_index)
            before_subtree = [rows[index] for index in before_indexes]
            candidate_subtree = get_subtree_rows(conn, int(candidate["detail_id"]), limit=500)
            replacement = clone_candidate_subtree_for_bom(
                candidate_subtree,
                target_row=rows[target_index],
                line_order_start=int(rows[target_index].get("line_order") or target_index + 1),
            )
            for index in reversed(before_indexes):
                rows.pop(index)
            for offset, item in enumerate(replacement):
                rows.insert(target_index + offset, item)
            change_list.append(
                {
                    "change_id": selection.get("change_id"),
                    "action": "subtree_replace",
                    "candidate": compact_candidate(candidate),
                    "before_subtree": before_subtree,
                    "after_subtree": replacement,
                    "subtree_diff": compare_subtrees(before_subtree, candidate_subtree),
                }
            )
            if propagate_change_upward:
                propagate_change_to_ancestors(
                    rows,
                    target_index,
                    change_id=selection.get("change_id"),
                    reason=text(rows[target_index].get("part_name")),
                    change_list=change_list,
                )
            continue

        before = dict(rows[target_index])
        rows[target_index].update(
            {
                "new_part_no": candidate.get("new_part_no") or rows[target_index].get("new_part_no"),
                "normalized_new_part_no": normalize_part_no(candidate.get("new_part_no")),
                "new_qty": candidate.get("new_qty") or rows[target_index].get("new_qty"),
                "changing_point": candidate.get("changing_point") or selection.get("change_point"),
                "changing_reason": candidate.get("changing_reason") or selection.get("change_reason"),
                "classification": "Change",
                "source_candidate_detail_id": candidate.get("detail_id"),
                "applied_action": "change",
            }
        )
        after = dict(rows[target_index])
        change_list.append(
            {
                "change_id": selection.get("change_id"),
                "action": action,
                "candidate": compact_candidate(candidate),
                "before": before,
                "after": after,
            }
        )
        if propagate_change_upward:
            propagate_change_to_ancestors(
                rows,
                target_index,
                change_id=selection.get("change_id"),
                reason=text(after.get("part_name")),
                change_list=change_list,
            )

    return {
        "base_master_id": base_master_id,
        "new_bom": rows,
        "change_list": change_list,
        "unmatched": unmatched,
    }
