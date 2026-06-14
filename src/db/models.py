"""D-012 — SQLAlchemy ORM for dev_part_master schema (팀원 ETL_PG 통합).

4 테이블 (이전 9 → 5 → 4로 축소):
  source_files    — 원본 파일 메타 (file_hash 기준 dedup)
  ingestion_log   — 시트별 처리 결과
  form_registry   — 지원 양식 등록
  dev_part_master — 메인 데이터 (Core 13 + extra_fields + narrative + embedding)

벡터 컬럼(``embedding_dense vector(1024)``)은 Postgres 전용 — pgvector 확장.
SQLite 단위 테스트에서는 Vector → JSON으로 fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import SPARSEVEC, Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Portable JSONB: Postgres = JSONB, SQLite = JSON.
_JSONB = JSONB().with_variant(JSON, "sqlite")

# SQLite는 BIGINT 컬럼에 AUTOINCREMENT를 적용하지 않음 — INTEGER로 대체해야
# autoincrement 동작.
_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """모든 테이블의 declarative base."""


# ── source_files ─────────────────────────────────────────────


class SourceFile(Base):
    """원본 엑셀 파일 메타. ``file_hash``로 중복 적재 방지."""

    __tablename__ = "source_files"

    file_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, default=None)
    region: Mapped[str | None] = mapped_column(Text, default=None)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── ingestion_log ────────────────────────────────────────────


class IngestionLog(Base):
    """시트별 처리 결과. status='ok'/'error'/'empty' 등."""

    __tablename__ = "ingestion_log"

    log_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE")
    )
    sheet_name: Mapped[str] = mapped_column(Text, nullable=False)
    form_id: Mapped[str] = mapped_column(Text, nullable=False)
    rows_total: Mapped[int | None] = mapped_column(Integer, default=None)
    rows_inserted: Mapped[int | None] = mapped_column(Integer, default=None)
    # 행 멱등성 가산 (2026-06-08, migrations/004) — 재적재 시 중복으로 스킵된 행 수 가시화
    # (ms ON CONFLICT DO NOTHING 차용). 컬럼은 migration SQL과 1:1.
    rows_skipped: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── form_registry ────────────────────────────────────────────


class FormRegistry(Base):
    """지원 양식 등록. schema_dev_part_master.sql이 seed."""

    __tablename__ = "form_registry"

    form_id: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)


# ── dev_part_master ──────────────────────────────────────────


class DevPartMaster(Base):
    """메인 테이블 (한 row = 한 부품 변경/신규 이벤트 또는 BOM 부품).

    팀원 ETL_PG 스키마 그대로 + ``extra_fields`` JSONB + RAG용 ``embedding_text`` /
    ``embedding_dense``.

    벡터 컬럼은 Postgres에서만 활성화. SQLite 단위 테스트에선 None.
    """

    __tablename__ = "dev_part_master"

    doc_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE")
    )
    form_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("form_registry.form_id", ondelete="RESTRICT"), default=None
    )
    sheet_name: Mapped[str | None] = mapped_column(Text, default=None)
    source_row: Mapped[int | None] = mapped_column(Integer, default=None)

    # 팀원 dev_part_master 컬럼
    region: Mapped[str | None] = mapped_column(Text, default=None)
    base_model: Mapped[str | None] = mapped_column(Text, default=None)
    new_model: Mapped[str | None] = mapped_column(Text, default=None)
    event: Mapped[str | None] = mapped_column(Text, default=None)
    bom_level_raw: Mapped[str | None] = mapped_column(Text, default=None)
    bom_depth: Mapped[int | None] = mapped_column(Integer, default=None)
    part_type: Mapped[str | None] = mapped_column(Text, default=None)
    part_no_base: Mapped[str | None] = mapped_column(Text, default=None)
    part_no_new: Mapped[str | None] = mapped_column(Text, default=None)
    part_name: Mapped[str | None] = mapped_column(Text, default=None)
    qty_base: Mapped[float | None] = mapped_column(Numeric, default=None)
    qty_new: Mapped[float | None] = mapped_column(Numeric, default=None)
    change_point_raw: Mapped[str | None] = mapped_column(Text, default=None)
    change_reason_raw: Mapped[str | None] = mapped_column(Text, default=None)
    supplier: Mapped[str | None] = mapped_column(Text, default=None)
    classification: Mapped[str | None] = mapped_column(Text, default=None)

    # 표준 매핑 안 된 컬럼 (grade, event_stage, 양식 잔여 헤더 등)
    extra_fields: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)

    # RAG 검색용
    embedding_text: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_dense = mapped_column(
        Vector(1024).with_variant(JSON, "sqlite"), nullable=True, default=None
    )

    # Agentic RAG 가산 (2026-05-29): L1 ChangeIntent 결과 캐시 (additive 컬럼).
    change_intent: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)

    # raw_json 출처보존 가산 (2026-06-08, migrations/003_raw_json_provenance.sql) —
    # ms(Master--BOM-AI-agent) BomLine.rawJson 차용: 원본 행 전체를 정의된 스키마(JSONB)로
    # 보존. extra_fields(미매핑 잔여 컬럼만)와 달리 매핑 컬럼까지 포함 → 정규화 오류 시
    # 감사·복구(raw_json fallback). 컬럼은 migration SQL과 1:1.
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)

    # 출처 프로젝트 태그 가산 (2026-06-08, migrations/006) — 'lg'(본 파이프라인) vs 'ms'
    # (Master--BOM-AI-agent tagged import). 검색 필터·출처표기·깨끗한 A/B 비교용.
    source_project: Mapped[str | None] = mapped_column(Text, default="lg")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 행 단위 멱등성 가산 (2026-06-08, migrations/005) — ms ON CONFLICT 차용. 실 어댑터는
    # source_row를 행마다 고유 부여하므로 (file_id, sheet_name, source_row)가 고유키.
    # NULL 키(sheet/row 미설정 행)는 NULL-distinct라 충돌하지 않음.
    __table_args__ = (
        UniqueConstraint("file_id", "sheet_name", "source_row", name="uq_dpm_source"),
    )


# ════════════════════════════════════════════════════════════════
# Agentic RAG 가산 테이블 (2026-05-29, migrations/001_agentic_rag_additive.sql)
# 기존 4테이블 파괴적 변경 0 — 보조 테이블만. Postgres에선 migration SQL이,
# SQLite 단위 테스트에선 create_all이 생성. 컬럼은 migration SQL과 1:1 정합.
# ════════════════════════════════════════════════════════════════


class BomEdge(Base):
    """구조 A — 정적 BOM 트리(DAG) 엣지. 한 (file_id, parent, child) = 한 엣지.

    실데이터 DAG 확정(553품번 중 81개 다중부모)이라 단일 부모 컬럼이 아닌 엣지 테이블.
    스코핑 키 = ``file_id``(한 BOM 파일 = 한 워크 범위; 실데이터 BOM이 multi-root라
    단일 루트 가정 불가). ``model``은 best-effort 라벨. ``walk_subtree`` 재귀 CTE 대상.
    """

    __tablename__ = "bom_edge"

    edge_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE"), default=None
    )
    model: Mapped[str | None] = mapped_column(Text, default=None)
    parent_pno: Mapped[str] = mapped_column(Text, nullable=False)
    child_pno: Mapped[str] = mapped_column(Text, nullable=False)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    qty: Mapped[float | None] = mapped_column(Numeric, default=None)
    source_doc_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("dev_part_master.doc_id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("file_id", "parent_pno", "child_pno", name="uq_bom_edge"),
    )


class ChangeEvent(Base):
    """구조 B — 변경 이벤트 마스터. 한 이벤트 = 한 변경사유(부품들을 묶는 키).

    의미 인덱스 = ``reason_embedding``(= change_log + change_reason 임베딩). 부품명/모델은
    이 임베딩엔 미포함이지만, 검색 시 parts 채널(``search_events``, change_line.part_name/
    base_pno/new_pno + base_model/new_model word_similarity)로 매칭한다(2026-06-10 개정).
    적재 로더 = ``db/load_change_events.py``
    (그룹핑: file_id × base_model × new_model × change_reason — 사유별 분할, 002 마이그레이션).
    """

    __tablename__ = "change_event"

    event_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    base_model: Mapped[str | None] = mapped_column(Text, default=None)
    base_grade: Mapped[str | None] = mapped_column(Text, default=None)
    new_model: Mapped[str | None] = mapped_column(Text, default=None)
    new_grade: Mapped[str | None] = mapped_column(Text, default=None)
    event: Mapped[str | None] = mapped_column(Text, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    source_file: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 002 가산 — 검색 인덱스. 컬럼은 migration SQL과 1:1.
    file_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE"), default=None
    )
    change_log: Mapped[str | None] = mapped_column(Text, default=None)
    change_reason: Mapped[str | None] = mapped_column(Text, default=None)
    reason_embedding = mapped_column(
        Vector(1024).with_variant(JSON, "sqlite"), nullable=True, default=None
    )
    # sparse(BGE-M3 lexical) 검색 채널 가산 (2026-06-08, migrations/007) — ms etl_embed 차용.
    # XLM-RoBERTa vocab(250002) 위 lexical weight. dense(cosine)+trgm에 더해 sparse(inner product)
    # RRF로 부품코드/모델 exact-match 보강. Postgres(pgvector>=0.7) 전용 — SQLite는 Text fallback.
    reason_sparse = mapped_column(
        SPARSEVEC(250002).with_variant(Text, "sqlite"), nullable=True, default=None
    )
    source_ref: Mapped[str | None] = mapped_column(Text, default=None)

    # 출처 프로젝트 태그 가산 (006) — dpm 멤버의 source_project 전파('lg'/'ms'). 검색 필터용.
    source_project: Mapped[str | None] = mapped_column(Text, default="lg")

    # 구조 스코프 가산 (008) — 모듈 태그. 컬럼만 준비(ETL 역추적은 후속 Phase).
    module_tags = mapped_column(
        ARRAY(Text).with_variant(JSON, "sqlite"), nullable=True, default=None
    )


class ChangeLine(Base):
    """구조 B — 이벤트별 영향 부품 라인. 같은 event_id 라인 = 한 사유로 함께 바뀐 부품 세트.

    ``changepoint``(=변경내역/변경점)는 기존 컬럼 재사용. part_name/qty/classification/
    supplier/금형·사내·시험(yn)/source_ref는 출력·트리 전개용 보존 필드(002 마이그레이션).
    """

    __tablename__ = "change_line"

    line_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("change_event.event_id", ondelete="CASCADE"), default=None
    )
    seq: Mapped[int | None] = mapped_column(Integer, default=None)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    part_type: Mapped[str | None] = mapped_column(Text, default=None)
    base_pno: Mapped[str | None] = mapped_column(Text, default=None)
    new_pno: Mapped[str | None] = mapped_column(Text, default=None)
    changepoint: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_dense = mapped_column(
        Vector(1024).with_variant(JSON, "sqlite"), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 002 가산 — 보존 필드(출력/전개). 컬럼은 migration SQL과 1:1.
    part_name: Mapped[str | None] = mapped_column(Text, default=None)
    qty_base: Mapped[float | None] = mapped_column(Numeric, default=None)
    qty_new: Mapped[float | None] = mapped_column(Numeric, default=None)
    classification: Mapped[str | None] = mapped_column(Text, default=None)
    supplier: Mapped[str | None] = mapped_column(Text, default=None)
    mold_yn: Mapped[str | None] = mapped_column(Text, default=None)
    inhouse_yn: Mapped[str | None] = mapped_column(Text, default=None)
    approval_test_yn: Mapped[str | None] = mapped_column(Text, default=None)
    source_ref: Mapped[str | None] = mapped_column(Text, default=None)

    # 구조 스코프 가산 (008) — canon 부품명(--recanon 백필) + 모듈 귀속(컬럼만 준비).
    part_name_canon: Mapped[str | None] = mapped_column(Text, default=None)
    module_pno: Mapped[str | None] = mapped_column(Text, default=None)
    module_name: Mapped[str | None] = mapped_column(Text, default=None)


class ToolCallLog(Base):
    """L2 도구 트레이스. 한 에이전트 실행 = 한 session_id."""

    __tablename__ = "tool_call_log"

    call_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(Text, default=None)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)
    result_count: Mapped[int | None] = mapped_column(Integer, default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentFeedback(Base):
    """에이전트 추천에 대한 채택/거절 피드백."""

    __tablename__ = "agent_feedback"

    feedback_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(Text, default=None)
    doc_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("dev_part_master.doc_id", ondelete="SET NULL"), default=None
    )
    part_no: Mapped[str | None] = mapped_column(Text, default=None)
    decision: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 002 가산 — HITL 확정 게이트(Phase 2)용. 컬럼은 migration SQL과 1:1.
    change_point: Mapped[str | None] = mapped_column(Text, default=None)
    new_pno_input: Mapped[str | None] = mapped_column(Text, default=None)
    is_anchor: Mapped[bool | None] = mapped_column(Boolean, default=None)


class ConfirmFeedback(Base):
    """P8(009) — HITL 확정 1회당 검색·확정 컨텍스트 스냅샷 (운영 = 평가 데이터).

    ``agent_feedback``(행 단위 채택/거절)과 달리, 한 확정 행위의 **쿼리·후보 순위·채택
    결과**를 통째로 남긴다. **기록 전용** — 검색·매핑·판정 어느 경로도 이 테이블을 읽지
    않는다(읽기는 ``db feedback-report`` CLI만). 기록 실패는 본 흐름을 막지 않는다.
    """

    __tablename__ = "confirm_feedback"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    query_text: Mapped[str | None] = mapped_column(Text, default=None)
    intent_json: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)
    candidates: Mapped[Any | None] = mapped_column(_JSONB, default=None)
    adopted_event_ids = mapped_column(
        ARRAY(Integer).with_variant(JSON, "sqlite"), nullable=True, default=None
    )
    manual_added_event_ids = mapped_column(
        ARRAY(Integer).with_variant(JSON, "sqlite"), nullable=True, default=None
    )
    low_confidence: Mapped[bool | None] = mapped_column(Boolean, default=None)
    source_ref: Mapped[str | None] = mapped_column(Text, default=None)


__all__ = [
    "AgentFeedback",
    "Base",
    "BomEdge",
    "ChangeEvent",
    "ChangeLine",
    "DevPartMaster",
    "FormRegistry",
    "IngestionLog",
    "SourceFile",
    "ToolCallLog",
]
