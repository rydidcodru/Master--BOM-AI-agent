"""D-012 — dev_part_master 검색 모듈.

3 검색 모드:
  semantic_search   bge-m3 임베딩 cosine 거리 (HNSW 인덱스 사용)
  lexical_search    pg_trgm word_similarity (embedding_text의 GIN trgm 인덱스 사용)
  hybrid_search     RRF (Reciprocal Rank Fusion)로 위 둘을 결합

공통 필터: form_id / event / region / file_id / form_id_like.

호출 패턴 (BOM Agent retrieve 노드 또는 CLI):
    from src.db.retrieve import hybrid_search
    with Session() as s:
        hits = hybrid_search(s, '내열 강화 패킹 변경', top_k=10, event='Change')
        for h in hits:
            print(h.score_rrf, h.part_no_new, h.part_name)

ENABLE_EMBEDDING=1 필요 (Ollama bge-m3). lexical_search는 임베딩 없이 사용 가능.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from src.db.models import ChangeLine
from src.utils.logging import get_logger

log = get_logger(__name__)

# RRF 상수 — 표준값 60. 작을수록 top rank 가중 증가, 클수록 평탄화.
_RRF_K = 60

# 검색 후보 풀 크기 (각 모달리티별 raw top-N → RRF 후 top-k)
_DEFAULT_CANDIDATE_POOL = 30


@dataclass
class Hit:
    """검색 결과 1건. 세 모달리티 점수를 모두 노출 (디버깅 + UI에 도움)."""

    doc_id: int
    part_no_new: str | None
    part_name: str | None
    new_model: str | None
    event: str | None
    region: str | None
    form_id: str | None
    file_id: int
    embedding_text: str | None
    # D-012: BOM Agent UI (generate_proposals_from_docs)가 RSN/CHG 태그
    # 매칭에 사용. retrieve 결과에서 raw 변경점/사유 그대로 노출.
    part_no_base: str | None = None
    change_point_raw: str | None = None
    change_reason_raw: str | None = None

    # 점수 (모달리티별, 없으면 None)
    score_semantic: float | None = None  # 1 - cosine_distance, 0~1
    score_lexical: float | None = None   # pg_trgm word_similarity, 0~1
    score_rrf: float | None = None       # RRF 융합, 보통 0~0.04

    # rank (모달리티별, 0-based)
    rank_semantic: int | None = None
    rank_lexical: int | None = None


@dataclass
class EventHit:
    """change_event 검색 결과 1건 (구조 B). 검색 인덱스 = reason_embedding.

    부품 라인은 별도 ``lookup_lines_by_event(event_id)``로 회수 — 한 event = 한 사유로
    함께 바뀐 부품 세트(HITL 게이트가 확정할 단위).
    """

    event_id: int
    base_model: str | None
    new_model: str | None
    event: str | None
    change_log: str | None
    change_reason: str | None
    source_ref: str | None
    file_id: int | None

    score_semantic: float | None = None
    score_lexical: float | None = None
    score_sparse: float | None = None  # BGE-M3 lexical sparse inner product (007)
    score_parts: float | None = None   # 부품명/품번/모델 trgm word_similarity (parts 채널)
    score_rrf: float | None = None
    # ①.5 구조 스코프 부스트 — 모듈 하위트리(S) 정합 점수 (SEARCH_INCLUDE_STRUCT 게이트,
    # additive — UI 표시는 후속 범위, 필드만).
    struct_score: float | None = None
    rank_semantic: int | None = None
    rank_lexical: int | None = None
    rank_sparse: int | None = None
    rank_parts: int | None = None


# ---------------------------------------------------------------------------
# 내부 helpers
# ---------------------------------------------------------------------------


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _parts_enabled_default() -> bool:
    """parts 채널(부품명/품번/모델 매칭) 기본 on. ``SEARCH_INCLUDE_PARTS=0``으로 끌 수 있다."""
    return os.environ.get("SEARCH_INCLUDE_PARTS", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def _excluded_file_ids_default() -> list[int]:
    """검색에서 제외할 source_files.file_id 목록. env ``SEARCH_EXCLUDE_FILE_IDS``(쉼표 구분).

    데이터를 지우지 않고 **검색에서만** 특정 파일 출처를 빼기 위한 게이트(되돌리기 쉬움 —
    env를 비우면 즉시 복원). 정수만 인정, 그 외 토큰은 무시.
    """
    raw = os.environ.get("SEARCH_EXCLUDE_FILE_IDS", "").strip()
    out: list[int] = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if tok.isdigit() and int(tok) not in out:
            out.append(int(tok))
    return out


def _build_filter_sql(
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """필터 조건들을 WHERE clause로 묶음."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if form_id:
        where.append("form_id = :form_id")
        params["form_id"] = form_id
    if form_id_like:
        where.append("form_id LIKE :form_id_like")
        params["form_id_like"] = form_id_like
    if event:
        where.append("event = :event")
        params["event"] = event
    if region:
        where.append("region = :region")
        params["region"] = region
    if file_id is not None:
        where.append("file_id = :file_id")
        params["file_id"] = file_id
    sql = (" AND " + " AND ".join(where)) if where else ""
    return sql, params


def _row_to_hit(row: Any, **score_kwargs: Any) -> Hit:
    return Hit(
        doc_id=row.doc_id,
        part_no_new=row.part_no_new,
        part_name=row.part_name,
        new_model=row.new_model,
        event=row.event,
        region=row.region,
        form_id=row.form_id,
        file_id=row.file_id,
        embedding_text=row.embedding_text,
        part_no_base=getattr(row, "part_no_base", None),
        change_point_raw=getattr(row, "change_point_raw", None),
        change_reason_raw=getattr(row, "change_reason_raw", None),
        **score_kwargs,
    )


def _embed(query: str) -> list[float]:
    """쿼리 임베딩. ENABLE_EMBEDDING=1 + Ollama bge-m3 필요."""
    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError(
            "Set ENABLE_EMBEDDING=1 and start Ollama bge-m3 for semantic search."
        )
    from src.embed.embedder import embed_texts

    return embed_texts([query])[0]


# ---------------------------------------------------------------------------
# Public retrievers
# ---------------------------------------------------------------------------


def semantic_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
    query_vec: list[float] | None = None,
) -> list[Hit]:
    """벡터 cosine 거리 기준 검색 (HNSW 인덱스 사용).

    Args:
        query: 자연어 쿼리.
        top_k: 최종 반환 행 수.
        form_id / form_id_like / event / region / file_id: 메타 필터.
        query_vec: 외부에서 미리 임베딩한 벡터 (없으면 자동 임베딩).

    Returns:
        :class:`Hit` 리스트. score_semantic은 1 - cosine_distance, rank_semantic은 0-based.
    """
    vec = query_vec or _embed(query)
    flt_sql, params = _build_filter_sql(form_id, form_id_like, event, region, file_id)
    params["v"] = _vec_str(vec)
    params["k"] = top_k

    sql = text(
        f"""
        SELECT doc_id, part_no_new, part_no_base, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               change_point_raw, change_reason_raw,
               1 - (embedding_dense <=> CAST(:v AS vector)) AS score
        FROM dev_part_master
        WHERE embedding_dense IS NOT NULL{flt_sql}
        ORDER BY embedding_dense <=> CAST(:v AS vector)
        LIMIT :k
        """
    )
    rows = session.execute(sql, params).all()
    return [
        _row_to_hit(r, score_semantic=float(r.score), rank_semantic=i)
        for i, r in enumerate(rows)
    ]


def lexical_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
    min_similarity: float = 0.05,
) -> list[Hit]:
    """pg_trgm word_similarity 기준 검색 (embedding_text의 GIN trgm 인덱스 사용).

    Postgres 전용. SQLite는 미지원 (단위 테스트는 semantic만).

    Args:
        query: 키워드 또는 짧은 자연어.
        top_k: 최종 반환 행 수.
        min_similarity: 이 값 미만은 cutoff (잡음 제거).

    Returns:
        :class:`Hit` 리스트. score_lexical은 0~1, rank_lexical은 0-based.
    """
    flt_sql, params = _build_filter_sql(form_id, form_id_like, event, region, file_id)
    params["q"] = query
    params["mn"] = min_similarity
    params["k"] = top_k

    # word_similarity는 query가 text의 일부와 얼마나 닮았나. trgm GIN 인덱스가
    # `<%`(left-arg-word-sim) 연산자로 prefilter, similarity 정렬에 사용.
    sql = text(
        f"""
        SELECT doc_id, part_no_new, part_no_base, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               change_point_raw, change_reason_raw,
               word_similarity(:q, embedding_text) AS score
        FROM dev_part_master
        WHERE embedding_text IS NOT NULL
          AND word_similarity(:q, embedding_text) >= :mn{flt_sql}
        ORDER BY word_similarity(:q, embedding_text) DESC
        LIMIT :k
        """
    )
    rows = session.execute(sql, params).all()
    return [
        _row_to_hit(r, score_lexical=float(r.score), rank_lexical=i)
        for i, r in enumerate(rows)
    ]


def hybrid_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    candidate_pool: int = _DEFAULT_CANDIDATE_POOL,
    rrf_k: int = _RRF_K,
    semantic_weight: float = 1.0,
    lexical_weight: float = 1.0,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
) -> list[Hit]:
    """Semantic + Lexical RRF 융합.

    동작:
      1. semantic / lexical 각각 ``candidate_pool``(=30) 만큼 top-N 후보 수집.
      2. RRF 점수 = Σ weight_i / (rrf_k + rank_i)  (각 모달리티에 등장한 doc만 합)
      3. RRF 점수 desc 정렬 후 top_k 반환.

    Args:
        query: 자연어 쿼리.
        top_k: 최종 반환 행 수 (기본 10).
        candidate_pool: 각 모달리티 raw top-N (기본 30).
        rrf_k: RRF 상수 (기본 60; 낮을수록 top rank 가중↑).
        semantic_weight / lexical_weight: 모달리티 가중치 (1.0이 균등).
        그 외 필터: form_id 등.

    Returns:
        :class:`Hit` 리스트. score_rrf 정렬 desc. semantic/lexical 점수와
        rank도 동시 노출 — 디버깅·UI에 활용.
    """
    sem = semantic_search(
        session,
        query,
        top_k=candidate_pool,
        form_id=form_id,
        form_id_like=form_id_like,
        event=event,
        region=region,
        file_id=file_id,
    )
    lex = lexical_search(
        session,
        query,
        top_k=candidate_pool,
        form_id=form_id,
        form_id_like=form_id_like,
        event=event,
        region=region,
        file_id=file_id,
    )

    # doc_id → 통합 Hit dict
    merged: dict[int, Hit] = {}
    for h in sem:
        merged[h.doc_id] = h  # semantic Hit (lexical 정보는 아직 없음)
    for h in lex:
        if h.doc_id in merged:
            # 같은 doc — lexical 점수 추가
            cur = merged[h.doc_id]
            cur.score_lexical = h.score_lexical
            cur.rank_lexical = h.rank_lexical
        else:
            merged[h.doc_id] = h

    # RRF 점수
    for h in merged.values():
        rrf = 0.0
        if h.rank_semantic is not None:
            rrf += semantic_weight / (rrf_k + h.rank_semantic + 1)
        if h.rank_lexical is not None:
            rrf += lexical_weight / (rrf_k + h.rank_lexical + 1)
        h.score_rrf = rrf

    ranked = sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Change-only retriever — ARCHITECTURE.md §3-1 reason_query/change_point_query
# ---------------------------------------------------------------------------
# 사용 동기 (사용자 피드백 2026-05-27):
#   기본 hybrid_search는 embedding_text(narrative 전체)를 매칭 — 부품명/모델/품번
#   도 같이 임베딩에 포함돼 "Motor" 쿼리가 변경 의도가 아니라 part_name=
#   "Motor, AC Synchronous"와 매칭돼 결과가 dominate.
#
#   search_changes는:
#   - 후보 풀을 change_point_raw 또는 change_reason_raw가 있는 row에 한정
#     (현재 287/8506 — 변경이력 있는 부품만)
#   - Semantic: 기존 embedding_dense 사용. narrative 전체 임베딩이지만 후보
#     풀이 작아 부품명 noise dominant 효과가 줄어듦. (별도 change_dense
#     컬럼 backfill은 후속 작업으로 분리 — D-013 후속)
#   - Lexical: change_point_raw + change_reason_raw 두 컬럼의
#     word_similarity를 GREATEST로 합쳐 사용. 부품명/모델은 매칭 대상 X.
#   - RRF로 결합 (hybrid_search와 동일 k=60)


def search_changes(
    session: Session,
    query: str,
    *,
    top_k: int = 5,
    candidate_pool: int = 30,
    rrf_k: int = _RRF_K,
    semantic_weight: float = 1.0,
    lexical_weight: float = 1.0,
    min_lex_similarity: float = 0.05,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
    query_vec: list[float] | None = None,
) -> list[Hit]:
    """변경점/사유만 매칭하는 RAG 검색.

    Args:
        query: 자연어 쿼리 (변경 의도 텍스트).
        top_k: 최종 반환 행 수.
        candidate_pool: 각 모달리티 raw top-N (기본 30).
        rrf_k: RRF 상수.
        semantic_weight / lexical_weight: 모달리티 가중치.
        min_lex_similarity: lexical cutoff (잡음 제거).
        form_id / form_id_like / event / region / file_id: 메타 필터.
        query_vec: 외부 임베딩 (없으면 자동).

    Returns:
        :class:`Hit` 리스트. score_rrf desc 정렬. 후보는 변경이력이 있는
        row에 한정 — 부품명 단독 매칭은 제외.
    """
    vec = query_vec or _embed(query)
    flt_sql, params = _build_filter_sql(form_id, form_id_like, event, region, file_id)
    params["v"] = _vec_str(vec)
    params["q"] = query
    params["mn"] = min_lex_similarity
    params["k"] = candidate_pool

    # 후보 풀: change_point_raw 또는 change_reason_raw 있는 row
    base_change_filter = (
        " AND ((change_point_raw IS NOT NULL AND change_point_raw <> '')"
        "   OR (change_reason_raw IS NOT NULL AND change_reason_raw <> ''))"
    )

    # ── SEM: embedding_dense + change-only 후보 풀 ──
    sem_sql = text(
        f"""
        SELECT doc_id, part_no_new, part_no_base, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               change_point_raw, change_reason_raw,
               1 - (embedding_dense <=> CAST(:v AS vector)) AS score
        FROM dev_part_master
        WHERE embedding_dense IS NOT NULL{base_change_filter}{flt_sql}
        ORDER BY embedding_dense <=> CAST(:v AS vector)
        LIMIT :k
        """
    )
    sem_rows = session.execute(sem_sql, params).all()
    sem_hits = [
        _row_to_hit(r, score_semantic=float(r.score), rank_semantic=i)
        for i, r in enumerate(sem_rows)
    ]

    # ── LEX: change_point_raw + change_reason_raw 만 word_similarity ──
    # GREATEST로 두 컬럼 중 더 잘 맞는 쪽을 score로. COALESCE로 NULL → 0.
    lex_sql = text(
        f"""
        SELECT doc_id, part_no_new, part_no_base, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               change_point_raw, change_reason_raw,
               GREATEST(
                   COALESCE(word_similarity(:q, change_point_raw),  0),
                   COALESCE(word_similarity(:q, change_reason_raw), 0)
               ) AS score
        FROM dev_part_master
        WHERE TRUE{base_change_filter}{flt_sql}
          AND GREATEST(
                  COALESCE(word_similarity(:q, change_point_raw),  0),
                  COALESCE(word_similarity(:q, change_reason_raw), 0)
              ) >= :mn
        ORDER BY score DESC
        LIMIT :k
        """
    )
    lex_rows = session.execute(lex_sql, params).all()
    lex_hits = [
        _row_to_hit(r, score_lexical=float(r.score), rank_lexical=i)
        for i, r in enumerate(lex_rows)
    ]

    # ── RRF 융합 (hybrid_search와 동일 패턴) ──
    merged: dict[int, Hit] = {}
    for h in sem_hits:
        merged[h.doc_id] = h
    for h in lex_hits:
        if h.doc_id in merged:
            cur = merged[h.doc_id]
            cur.score_lexical = h.score_lexical
            cur.rank_lexical = h.rank_lexical
        else:
            merged[h.doc_id] = h

    for h in merged.values():
        rrf = 0.0
        if h.rank_semantic is not None:
            rrf += semantic_weight / (rrf_k + h.rank_semantic + 1)
        if h.rank_lexical is not None:
            rrf += lexical_weight / (rrf_k + h.rank_lexical + 1)
        h.score_rrf = rrf

    ranked = sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)

    # (pno, cp, rsn) 기준 dedup — 같은 부품의 동일 변경이력이 다른 file_id로
    # 여러 번 잡히는 노이즈 제거. top_k 채울 때까지 살린다.
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Hit] = []
    for h in ranked:
        key = (
            (h.part_no_new or "").strip().upper(),
            (h.change_point_raw or "").strip(),
            (h.change_reason_raw or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
        if len(deduped) >= top_k:
            break
    return deduped


# ---------------------------------------------------------------------------
# 구조 B — change_event 검색 (CLAUDE.md 검색 인덱스 = reason_embedding)
# ---------------------------------------------------------------------------
# search_changes(dev_part_master)와 달리, search_events는 사유 단위 change_event를
# 검색한다. semantic = reason_embedding(= change_log+change_reason 임베딩, 부품명/모델
# 미포함), lexical = change_log/change_reason word_similarity. 결과 1건 = 한 사유 묶음;
# 부품 라인은 lookup_lines_by_event로 회수.


def _build_event_filter_sql(
    base_model: str | None = None,
    new_model: str | None = None,
    event: str | None = None,
    file_id: int | None = None,
    source_project: str | None = None,
    exclude_file_ids: list[int] | None = None,
) -> tuple[str, dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    if base_model:
        where.append("base_model = :base_model")
        params["base_model"] = base_model
    if new_model:
        where.append("new_model = :new_model")
        params["new_model"] = new_model
    if event:
        where.append("event = :event")
        params["event"] = event
    if file_id is not None:
        where.append("file_id = :file_id")
        params["file_id"] = file_id
    if source_project:  # 'lg'(본 파이프라인) | 'ms'(tagged import). None이면 전체(production).
        where.append("source_project = :source_project")
        params["source_project"] = source_project
    if exclude_file_ids:  # 특정 파일 출처를 검색에서 제외(데이터 무삭제). NULL file_id는 유지.
        keys = []
        for i, fid in enumerate(exclude_file_ids):
            k = f"exf{i}"
            keys.append(f":{k}")
            params[k] = int(fid)
        where.append(f"(file_id IS NULL OR file_id NOT IN ({', '.join(keys)}))")
    sql = (" AND " + " AND ".join(where)) if where else ""
    return sql, params


def _row_to_event_hit(row: Any, **score_kwargs: Any) -> EventHit:
    return EventHit(
        event_id=row.event_id,
        base_model=row.base_model,
        new_model=row.new_model,
        event=row.event,
        change_log=row.change_log,
        change_reason=row.change_reason,
        source_ref=row.source_ref,
        file_id=row.file_id,
        **score_kwargs,
    )


def search_events(
    session: Session,
    query: str,
    *,
    top_k: int = 5,
    candidate_pool: int = _DEFAULT_CANDIDATE_POOL,
    rrf_k: int = _RRF_K,
    semantic_weight: float = 1.0,
    lexical_weight: float = 1.0,
    sparse_weight: float = 1.0,
    parts_weight: float = 1.0,
    min_lex_similarity: float = 0.05,
    min_parts_similarity: float = 0.10,
    base_model: str | None = None,
    new_model: str | None = None,
    event: str | None = None,
    file_id: int | None = None,
    source_project: str | None = None,
    exclude_file_ids: list[int] | None = None,
    query_vec: list[float] | None = None,
    query_sparse: str | None = None,
    use_sparse: bool = True,
    include_parts: bool | None = None,
    require_source: bool = True,
    lexical_concat: bool = True,
) -> list[EventHit]:
    """change_event를 reason_embedding(semantic) + change_log/reason(lexical)로 검색.

    Args:
        query: 자연어 쿼리(변경 의도 텍스트 — 변경내역+변경사유로 구성).
        top_k: 최종 반환 이벤트 수.
        candidate_pool: 각 모달리티 raw top-N.
        rrf_k: RRF 상수(기본 60).
        semantic_weight / lexical_weight / sparse_weight / parts_weight: 모달리티 가중치.
        min_lex_similarity: lexical cutoff. min_parts_similarity: parts 채널 cutoff.
        base_model / new_model / event / file_id: 메타 필터.
        exclude_file_ids: 검색에서 제외할 file_id 목록(데이터 무삭제). None이면 env
            ``SEARCH_EXCLUDE_FILE_IDS``(쉼표 구분)를 따른다. NULL file_id 행은 유지.
        query_vec: 외부 임베딩(없으면 자동).
        include_parts: parts 채널(부품명/품번/모델 word_similarity) 사용 여부. None(기본)이면
            env ``SEARCH_INCLUDE_PARTS``(기본 on)를 따른다. 이 채널은 reason 인덱스를 건드리지
            않고 change_line.part_name/base_pno/new_pno + change_event.base_model/new_model을
            쿼리와 trgm 매칭해 4번째 RRF 모달리티로 융합한다(2026-06-10 사용자 확정: 검색에
            부품명+식별자 포함). 쿼리에 부품 식별 토큰이 있어야 점수가 난다(intent_from_change의
            부품 보강 쿼리 참고).
        lexical_concat: True(기본)면 lexical을 **변경내역+변경사유를 붙인 한 문장**과
            word_similarity로 매칭(semantic과 동일하게 둘을 합쳐 봄). False면 두 컬럼을
            각각 매칭해 GREATEST(더 잘 맞는 쪽)를 사용(짧은 사유가 긴 내역에 묻히는 것 방지).

    Returns:
        :class:`EventHit` 리스트, score_rrf desc. 부품 라인은 lookup_lines_by_event로.
    """
    # 임베딩이 켜져 있을 때만 query vector 확보. 꺼져 있으면(query_vec도 없으면) semantic을
    # 건너뛰고 lexical-only로 우아하게 폴백 — 검수 UI가 Ollama 없이도 동작하도록.
    vec = query_vec
    if vec is None:
        from src.embed.embedder import embedding_enabled

        if embedding_enabled():
            vec = _embed(query)

    # sparse(BGE-M3 lexical) 질의 임베딩 — 켜져 있고 가능할 때만(없으면 dense+trgm으로 폴백).
    qs = query_sparse
    if qs is None and use_sparse:
        from src.embed.sparse_embedder import embed_sparse_one, sparse_embedding_enabled

        if sparse_embedding_enabled():
            try:
                qs = embed_sparse_one(query)
            except Exception as exc:  # noqa: BLE001 — sparse 실패는 dense+trgm로 우아하게 폴백
                # debug: 폴백이 정상 동작이라 매 검색마다 warning으로 STDOUT을 오염시키지 않는다
                # (FlagEmbedding 미설치 등). dense+trgm로 조용히 폴백.
                log.debug("retrieve.sparse_query_failed", error=str(exc)[:160])
                qs = None

    # exclude_file_ids: 명시값 우선, 없으면 env(SEARCH_EXCLUDE_FILE_IDS). 검색에서만 제외(무삭제).
    excl = exclude_file_ids if exclude_file_ids is not None else _excluded_file_ids_default()
    flt_sql, params = _build_event_filter_sql(
        base_model, new_model, event, file_id, source_project, exclude_file_ids=excl
    )
    # 출처 없는(합성/플레이스홀더 포함) 이벤트는 검색 제외 — CLAUDE.md 출처 원칙(기본 on).
    # ms_etl_import.xlsx 같은 합성명은 재import로 제거됐고, 향후 무출처 데이터 유입도 차단.
    if require_source:
        flt_sql += " AND source_ref IS NOT NULL AND source_ref <> ''"
    params["q"] = query
    params["mn"] = min_lex_similarity
    params["k"] = candidate_pool

    sem_hits: list[EventHit] = []
    if vec is not None:
        sem_sql = text(
            f"""
            SELECT event_id, base_model, new_model, event, change_log, change_reason,
                   source_ref, file_id,
                   1 - (reason_embedding <=> CAST(:v AS vector)) AS score
            FROM change_event
            WHERE reason_embedding IS NOT NULL{flt_sql}
            ORDER BY reason_embedding <=> CAST(:v AS vector)
            LIMIT :k
            """
        )
        sem_rows = session.execute(sem_sql, {**params, "v": _vec_str(vec)}).all()
        sem_hits = [
            _row_to_event_hit(r, score_semantic=float(r.score), rank_semantic=i)
            for i, r in enumerate(sem_rows)
        ]

    # lexical 점수식 — concat(붙인 한 문장) vs GREATEST(각 컬럼 max). 부품명/모델은 대상 X.
    # 식은 bool 플래그로만 구성(사용자 입력 미포함) → 파라미터화 안전.
    if lexical_concat:
        lex_expr = "word_similarity(:q, concat_ws(' ', change_log, change_reason))"
    else:
        lex_expr = (
            "GREATEST(COALESCE(word_similarity(:q, change_log), 0), "
            "COALESCE(word_similarity(:q, change_reason), 0))"
        )
    lex_sql = text(
        f"""
        SELECT event_id, base_model, new_model, event, change_log, change_reason,
               source_ref, file_id,
               {lex_expr} AS score
        FROM change_event
        WHERE TRUE{flt_sql}
          AND {lex_expr} >= :mn
        ORDER BY score DESC
        LIMIT :k
        """
    )
    lex_rows = session.execute(lex_sql, params).all()
    lex_hits = [
        _row_to_event_hit(r, score_lexical=float(r.score), rank_lexical=i)
        for i, r in enumerate(lex_rows)
    ]

    # ── SPARSE: BGE-M3 lexical inner product (007 차용, 코드/모델 exact-match 보강) ──
    sparse_hits: list[EventHit] = []
    if qs is not None:
        sp_sql = text(
            f"""
            SELECT event_id, base_model, new_model, event, change_log, change_reason,
                   source_ref, file_id,
                   (reason_sparse <#> CAST(:qs AS sparsevec)) * -1 AS score
            FROM change_event
            WHERE reason_sparse IS NOT NULL{flt_sql}
            ORDER BY reason_sparse <#> CAST(:qs AS sparsevec)
            LIMIT :k
            """
        )
        sp_rows = session.execute(sp_sql, {**params, "qs": qs}).all()
        sparse_hits = [
            _row_to_event_hit(r, score_sparse=float(r.score), rank_sparse=i)
            for i, r in enumerate(sp_rows)
        ]

    # ── PARTS: 부품명/품번/모델 trgm 매칭 (2026-06-10 — 검색에 부품명+식별자 포함) ──
    # reason_embedding 인덱스는 그대로 두고, change_line(part_name/base_pno/new_pno) +
    # change_event(base_model/new_model)을 쿼리와 word_similarity로 매칭하는 별도 채널.
    # 한 event의 라인 중 최고 점수(MAX)를 그 event 점수로 — 어떤 부품이라도 쿼리의 부품
    # 식별과 닮으면 회수. flt_sql은 change_event 컬럼만 참조하므로 CTE(ev) 안에서 비모호.
    do_parts = _parts_enabled_default() if include_parts is None else include_parts
    parts_hits: list[EventHit] = []
    if do_parts:
        # GREATEST(부품명, canon부품명, base품번, new품번, base모델, new모델 각각의
        # word_similarity). 방향 주의: word_similarity(식별토큰, 쿼리) — 식별토큰(짧음)이
        # arg1, 쿼리(긺)가 arg2. 식별 토큰이 쿼리에 통째로 나타나면 1.0(정확 매칭).
        # part_name_canon(008, --recanon 백필)은 오타/변이 정규화 부수효과 — 미백필(NULL)이면
        # COALESCE 0으로 기존 raw part_name 매칭과 동일(additive, 회귀 없음).
        _parts_expr = (
            "GREATEST("
            "COALESCE(word_similarity(cl.part_name, :q), 0), "
            "COALESCE(word_similarity(cl.part_name_canon, :q), 0), "
            "COALESCE(word_similarity(cl.base_pno, :q), 0), "
            "COALESCE(word_similarity(cl.new_pno, :q), 0), "
            "COALESCE(word_similarity(ev.base_model, :q), 0), "
            "COALESCE(word_similarity(ev.new_model, :q), 0))"
        )
        # score를 inner CTE(scored)에서 한 번만 계산하고 바깥에서 alias로 필터 — HAVING에
        # GREATEST(word_similarity*5)를 다시 평가하지 않도록(이중 평가 방지).
        parts_sql = text(
            f"""
            WITH ev AS (
                SELECT event_id, base_model, new_model, event, change_log, change_reason,
                       source_ref, file_id
                FROM change_event
                WHERE TRUE{flt_sql}
            ),
            scored AS (
                SELECT ev.event_id, ev.base_model, ev.new_model, ev.event, ev.change_log,
                       ev.change_reason, ev.source_ref, ev.file_id,
                       MAX({_parts_expr}) AS score
                FROM ev
                JOIN change_line cl ON cl.event_id = ev.event_id
                GROUP BY ev.event_id, ev.base_model, ev.new_model, ev.event, ev.change_log,
                         ev.change_reason, ev.source_ref, ev.file_id
            )
            SELECT event_id, base_model, new_model, event, change_log, change_reason,
                   source_ref, file_id, score
            FROM scored
            WHERE score >= :mnp
            ORDER BY score DESC
            LIMIT :k
            """
        )
        parts_rows = session.execute(parts_sql, {**params, "mnp": min_parts_similarity}).all()
        parts_hits = [
            _row_to_event_hit(r, score_parts=float(r.score), rank_parts=i)
            for i, r in enumerate(parts_rows)
        ]

    # RRF 융합 (event_id 기준 — dense + trgm + sparse + parts 중 존재하는 모달리티 합산).
    merged: dict[int, EventHit] = {}
    for h in sem_hits:
        merged[h.event_id] = h
    for h in lex_hits:
        if h.event_id in merged:
            cur = merged[h.event_id]
            cur.score_lexical = h.score_lexical
            cur.rank_lexical = h.rank_lexical
        else:
            merged[h.event_id] = h
    for h in sparse_hits:
        if h.event_id in merged:
            cur = merged[h.event_id]
            cur.score_sparse = h.score_sparse
            cur.rank_sparse = h.rank_sparse
        else:
            merged[h.event_id] = h
    for h in parts_hits:
        if h.event_id in merged:
            cur = merged[h.event_id]
            cur.score_parts = h.score_parts
            cur.rank_parts = h.rank_parts
        else:
            merged[h.event_id] = h

    for h in merged.values():
        rrf = 0.0
        if h.rank_semantic is not None:
            rrf += semantic_weight / (rrf_k + h.rank_semantic + 1)
        if h.rank_lexical is not None:
            rrf += lexical_weight / (rrf_k + h.rank_lexical + 1)
        if h.rank_sparse is not None:
            rrf += sparse_weight / (rrf_k + h.rank_sparse + 1)
        if h.rank_parts is not None:
            rrf += parts_weight / (rrf_k + h.rank_parts + 1)
        h.score_rrf = rrf

    ranked = sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)
    return ranked[:top_k]


def lookup_lines_by_event(session: Session, event_id: int) -> list[ChangeLine]:
    """한 change_event의 부품 라인 세트(seq 순). 순수 ORM — Postgres/SQLite 공통."""
    stmt = (
        select(ChangeLine)
        .where(ChangeLine.event_id == event_id)
        .order_by(ChangeLine.seq)
    )
    return list(session.execute(stmt).scalars().all())


__all__ = [
    "EventHit",
    "Hit",
    "hybrid_search",
    "lexical_search",
    "lookup_lines_by_event",
    "search_changes",
    "search_events",
    "semantic_search",
]
