-- ============================================================
-- 001 — Agentic RAG 가산 마이그레이션 (2026-05-29)
--
-- 절대 원칙: 기존 4테이블(source_files/ingestion_log/form_registry/
-- dev_part_master) 파괴적 변경 0. 보조 테이블/컬럼 추가만.
--
-- idempotent: 전부 CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.
-- 재실행해도 안전 (engine.init_db가 base schema 뒤에 매번 적용).
--
-- 롤백: 001_agentic_rag_additive.rollback.sql 참조 (DROP). 기존 4테이블·데이터는
-- 건드리지 않으므로 롤백해도 ETL/검색 경로 영향 없음. dev_part_master.change_intent
-- 컬럼만 DROP하면 원상 복귀.
--
-- 구조 A = bom_edge (실데이터 DAG 확정: 553품번 중 81개 다중부모). model 스코핑 키 =
--          BOM 루트(Lvl-0) 품번 (base_bom: new_model NULL이므로 루트 품번 사용).
-- 구조 B = change_event/change_line — 스키마만 (적재 보류, 그룹핑 전문가 확인).
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ── 구조 A: bom_edge — 정적 BOM 트리(DAG) cascade 순회용 ────────
-- 스코핑 키 = file_id (한 BOM 파일 = 한 워크 범위). 실데이터 BOM 파일이 multi-root라
-- 단일 루트=model 가정이 안 맞아 file_id로 스코핑. model은 best-effort 라벨(루트 품번 등).
-- 같은 child가 같은 파일 내 여러 parent를 가질 수 있음(DAG). 다중 occurrence는
-- (file_id, parent, child) UNIQUE로 1엣지 통합.
CREATE TABLE IF NOT EXISTS bom_edge (
    edge_id       BIGSERIAL PRIMARY KEY,
    file_id       BIGINT REFERENCES source_files(file_id) ON DELETE CASCADE,
    model         TEXT,                 -- best-effort 라벨 (스코핑은 file_id)
    parent_pno    TEXT NOT NULL,
    child_pno     TEXT NOT NULL,
    bom_level     INTEGER,              -- child의 depth
    qty           NUMERIC,
    source_doc_id BIGINT REFERENCES dev_part_master(doc_id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_bom_edge UNIQUE (file_id, parent_pno, child_pno)
);
CREATE INDEX IF NOT EXISTS idx_bom_edge_parent ON bom_edge (file_id, parent_pno);
CREATE INDEX IF NOT EXISTS idx_bom_edge_child  ON bom_edge (file_id, child_pno);


-- ── 구조 B: change_event — 변경 이벤트 마스터 (적재 보류: 스키마만) ─
CREATE TABLE IF NOT EXISTS change_event (
    event_id    BIGSERIAL PRIMARY KEY,
    base_model  TEXT,
    base_grade  TEXT,
    new_model   TEXT,
    new_grade   TEXT,
    event       TEXT,
    reason      TEXT,
    raw_text    TEXT,
    source_file TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);


-- ── 구조 B: change_line — 이벤트별 영향 부품 라인 (적재 보류) ────
-- 같은 event_id 라인 = 동시변경 신호 + (event, lines) = 골든 평가 라벨.
CREATE TABLE IF NOT EXISTS change_line (
    line_id         BIGSERIAL PRIMARY KEY,
    event_id        BIGINT REFERENCES change_event(event_id) ON DELETE CASCADE,
    seq             INTEGER,
    bom_level       INTEGER,
    part_type       TEXT,
    base_pno        TEXT,
    new_pno         TEXT,
    changepoint     TEXT,
    embedding_dense vector(1024),
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_change_line_event ON change_line (event_id);


-- ── L2 도구 트레이스 ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tool_call_log (
    call_id       BIGSERIAL PRIMARY KEY,
    session_id    TEXT,               -- 한 에이전트 실행 단위
    tool_name     TEXT NOT NULL,
    arguments     JSONB,
    result_count  INTEGER,
    latency_ms    INTEGER,
    status        TEXT,               -- ok / error
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tool_call_log_session ON tool_call_log (session_id);


-- ── 에이전트 피드백 (채택/거절) ───────────────────────────────
CREATE TABLE IF NOT EXISTS agent_feedback (
    feedback_id BIGSERIAL PRIMARY KEY,
    session_id  TEXT,
    doc_id      BIGINT REFERENCES dev_part_master(doc_id) ON DELETE SET NULL,
    part_no     TEXT,
    decision    TEXT,                 -- accept / reject
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);


-- ── dev_part_master.change_intent: L1 결과 캐시 (additive 컬럼) ──
ALTER TABLE dev_part_master ADD COLUMN IF NOT EXISTS change_intent JSONB;
CREATE INDEX IF NOT EXISTS idx_dpm_change_intent
    ON dev_part_master USING gin (change_intent)
    WHERE change_intent IS NOT NULL;
