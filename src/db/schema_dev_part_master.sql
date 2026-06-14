-- ============================================================
-- D-012 — 팀원 ETL_PG 스키마(dev_part_master)로 통합.
--
-- 4 테이블 (이전 9 / D-011후 5 → 4로 축소):
--   source_files     — 원본 파일 메타 (file_hash 기준 dedup)
--   ingestion_log    — 시트별 처리 결과 (성공/실패)
--   form_registry   — 지원 양식 등록
--   dev_part_master — 메인 데이터 (한 row = 한 부품 변경/신규 이벤트)
--
-- 변경 사항 (D-011 → D-012):
--   - run_id batch handle → file_id (파일 단위 lifecycle)
--   - parts/models/bom_edges/change_events/preprocessing_runs → dev_part_master 단일
--   - Core 13 + extra_fields(JSONB) 그대로 유지하되 컬럼명 팀원 스키마로 변경
--   - 5 multi-vector → embedding_dense 1개 (1024-dim, bge-m3)
--   - embedding_text 컬럼 추가 (narrative_text가 여기로 이동)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ── source_files: 원본 파일 메타 ──────────────────────────────
CREATE TABLE IF NOT EXISTS source_files (
    file_id      BIGSERIAL PRIMARY KEY,
    file_name    TEXT NOT NULL,
    file_hash    TEXT NOT NULL UNIQUE,
    file_size    BIGINT,
    region       TEXT,
    ingested_at  TIMESTAMPTZ DEFAULT now()
);


-- ── ingestion_log: 시트별 처리 결과 ──────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_log (
    log_id          BIGSERIAL PRIMARY KEY,
    file_id         BIGINT REFERENCES source_files(file_id) ON DELETE CASCADE,
    sheet_name      TEXT NOT NULL,
    form_id         TEXT NOT NULL,
    rows_total      INTEGER,
    rows_inserted   INTEGER,
    status          TEXT,
    error_message   TEXT,
    logged_at       TIMESTAMPTZ DEFAULT now()
);


-- ── form_registry: 지원 양식 등록 ─────────────────────────────
CREATE TABLE IF NOT EXISTS form_registry (
    form_id      TEXT PRIMARY KEY,
    description  TEXT
);

INSERT INTO form_registry (form_id, description) VALUES
    ('changing_parts_list_91', '변경부품 list 91컬럼'),
    ('changing_parts_list_95', '변경부품 list 95컬럼'),
    ('changing_parts_list_96', '변경부품 list 96컬럼'),
    ('changing_parts_list_97', '변경부품 list 97컬럼'),
    ('new_parts_list_75',      '신규부품리스트 75컬럼'),
    ('base_master_24',         '구버전 24컬럼'),
    ('uae_dev_list',           'UAE 신규개발리스트'),
    ('bom_ag_grid_36',         'BOM ag-grid 36컬럼'),
    ('v1_2_template_59',       'v1.2 통합 마스터 (빈 템플릿)')
ON CONFLICT (form_id) DO NOTHING;


-- ── dev_part_master: 메인 테이블 (팀원 스키마 그대로) ──────────
CREATE TABLE IF NOT EXISTS dev_part_master (
    doc_id            BIGSERIAL PRIMARY KEY,
    file_id           BIGINT REFERENCES source_files(file_id) ON DELETE CASCADE,
    form_id           TEXT REFERENCES form_registry(form_id) ON DELETE RESTRICT,
    sheet_name        TEXT,
    source_row        INTEGER,

    -- 팀원 dev_part_master 컬럼 (그대로)
    region            TEXT,
    base_model        TEXT,
    new_model         TEXT,
    event             TEXT,
    bom_level_raw     TEXT,
    bom_depth         INTEGER,
    part_type         TEXT,
    part_no_base      TEXT,
    part_no_new       TEXT,
    part_name         TEXT,
    qty_base          NUMERIC,
    qty_new           NUMERIC,
    change_point_raw  TEXT,
    change_reason_raw TEXT,
    supplier          TEXT,
    classification    TEXT,

    -- 추가: 표준 매핑 안 된 컬럼 보존 (grade, event_stage 등 + 양식 잔여 헤더)
    extra_fields      JSONB,

    -- 추가: RAG 검색용
    embedding_text    TEXT,            -- narrative_text를 여기에
    embedding_dense   vector(1024),    -- bge-m3 임베딩 (Ollama)

    created_at        TIMESTAMPTZ DEFAULT now()
);


-- ── 인덱스 ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_dpm_part_no_new
    ON dev_part_master (part_no_new);
CREATE INDEX IF NOT EXISTS idx_dpm_part_no_base
    ON dev_part_master (part_no_base);
CREATE INDEX IF NOT EXISTS idx_dpm_new_model
    ON dev_part_master (new_model);
CREATE INDEX IF NOT EXISTS idx_dpm_region
    ON dev_part_master (region);
CREATE INDEX IF NOT EXISTS idx_dpm_form_id
    ON dev_part_master (form_id);
CREATE INDEX IF NOT EXISTS idx_dpm_file_id
    ON dev_part_master (file_id);

-- HNSW 벡터 인덱스 (m=16, ef_construction=200 — pgvector 0.5+)
CREATE INDEX IF NOT EXISTS idx_dpm_embedding_dense
    ON dev_part_master USING hnsw (embedding_dense vector_cosine_ops);

-- Trigram GIN 인덱스 (ILIKE 가속)
CREATE INDEX IF NOT EXISTS idx_dpm_embedding_text_trgm
    ON dev_part_master USING gin (embedding_text gin_trgm_ops);
