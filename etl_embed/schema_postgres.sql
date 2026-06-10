-- =============================================================================
-- 개발부품 변경이력 ETL — Postgres + pgvector 스키마
-- Target: PostgreSQL 16 + pgvector 0.6+
-- 현재 스코프: Group A (dev_part_master) 만. 나머지 그룹은 별도 테이블 추가.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- ENUMS
-- =============================================================================
DO $$ BEGIN
    CREATE TYPE form_type AS ENUM (
        'dev_part_master',
        'saudi_part_change',
        'saudi_part_qualification_test',
        'saudi_new_part_registration',
        'oven_simple_change',
        'northam_dev_activity',
        'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE ingest_status AS ENUM ('pending', 'running', 'completed', 'failed', 'skipped');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- =============================================================================
-- form_registry — 양식 정의 + 라우팅
-- =============================================================================
CREATE TABLE IF NOT EXISTS form_registry (
    form_id              TEXT PRIMARY KEY,
    form_type            form_type NOT NULL,
    target_table         TEXT NOT NULL,
    display_name         TEXT NOT NULL,
    description          TEXT,
    header_fingerprint   TEXT,
    header_row_index     INT,
    data_start_row       INT,
    column_mapping       JSONB NOT NULL,
    metadata_extractors  JSONB,
    sheet_name_patterns  TEXT[],
    created_by_llm       BOOLEAN DEFAULT FALSE,
    human_reviewed       BOOLEAN DEFAULT FALSE,
    is_active            BOOLEAN DEFAULT TRUE,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_form_registry_fingerprint ON form_registry (header_fingerprint);

-- =============================================================================
-- source_files
-- =============================================================================
CREATE TABLE IF NOT EXISTS source_files (
    file_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_name        TEXT NOT NULL,
    file_hash        TEXT UNIQUE,
    file_size        BIGINT,
    region           TEXT,
    detected_forms   TEXT[],
    ingested_at      TIMESTAMPTZ DEFAULT NOW(),
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_files_region ON source_files (region);

-- =============================================================================
-- ingestion_log
-- =============================================================================
CREATE TABLE IF NOT EXISTS ingestion_log (
    batch_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id               UUID REFERENCES source_files(file_id) ON DELETE CASCADE,
    sheet_name            TEXT,
    form_id               TEXT REFERENCES form_registry(form_id),
    target_table          TEXT,
    rows_total            INT,
    rows_inserted         INT,
    rows_skipped          INT,
    status                ingest_status DEFAULT 'pending',
    error_message         TEXT,
    started_at            TIMESTAMPTZ DEFAULT NOW(),
    finished_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ingestion_log_file ON ingestion_log (file_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_log_status ON ingestion_log (status);

-- =============================================================================
-- dev_part_master (Group A)
-- =============================================================================
CREATE TABLE IF NOT EXISTS dev_part_master (
    doc_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id             UUID NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
    form_id             TEXT NOT NULL REFERENCES form_registry(form_id),
    sheet_name          TEXT NOT NULL,
    source_row          INT NOT NULL,

    -- 폼 메타데이터
    region              TEXT,
    base_model          TEXT,
    base_grade          TEXT,
    new_model           TEXT,
    new_grade           TEXT,
    event               TEXT,
    buyer               TEXT,
    brand               TEXT,
    set_part_no         TEXT,
    production_date     TEXT,

    -- 본문
    no                  INT,
    bom_level_raw       TEXT,
    bom_depth           SMALLINT,
    part_type           TEXT,
    part_no_base        TEXT,
    part_no_new         TEXT,
    part_name           TEXT,
    qty_base            INT,
    qty_new             INT,
    change_point_raw    TEXT,
    change_reason_raw   TEXT,
    supplier            TEXT,
    classification      TEXT,
    mold_dev            CHAR(1),
    in_house            CHAR(1),
    designer            TEXT,
    fig_ref             TEXT,
    part_grade          TEXT,
    remark              TEXT,

    -- 양식 변형별 고유 컬럼 (JSONB)
    extra_fields        JSONB,

    -- LLM 추출 (현재는 placeholder)
    change_from         TEXT,
    change_to           TEXT,
    change_category     TEXT,
    change_summary      TEXT,
    extraction_evidence JSONB,
    extraction_confidence TEXT,

    -- 임베딩
    --   dense   : BGE-M3 1024-d (XLM-R 기반, 다국어). HNSW + cosine.
    --   sparse  : BGE-M3 lexical weights (token_id → weight).
    --             XLM-R vocab = 250002. 부품번호/모델코드 exact match 보강.
    embedding_text      TEXT,
    embedding_dense     vector(1024),
    embedding_sparse    sparsevec(250002),

    -- 행 종류 자동 분류 (trigger로 채워짐):
    --   'change_history' : 진짜 변경 이력 (RAG 주 검색 대상)
    --   'bom_entry'      : 일반 BOM 항목 (변경 사유 없음)
    --   'marker'         : 'X'/'←'/'-' 같은 마커 또는 part_no_new 없음
    row_kind            TEXT,

    ingested_at         TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uq_dpm_source UNIQUE (file_id, sheet_name, source_row)
);

-- 이미 존재하는 테이블에 sparse 컬럼 추가 (멱등 마이그레이션)
--   pgvector >= 0.7.0 필요. pgvector/pgvector:pg16 이미지는 0.8+ 포함.
ALTER TABLE dev_part_master
    ADD COLUMN IF NOT EXISTS embedding_sparse sparsevec(250002);

-- 정형 컬럼 인덱스
CREATE INDEX IF NOT EXISTS idx_dpm_region ON dev_part_master (region);
CREATE INDEX IF NOT EXISTS idx_dpm_classification ON dev_part_master (classification);
CREATE INDEX IF NOT EXISTS idx_dpm_part_no_new ON dev_part_master (part_no_new);
CREATE INDEX IF NOT EXISTS idx_dpm_part_no_base ON dev_part_master (part_no_base);
CREATE INDEX IF NOT EXISTS idx_dpm_row_kind ON dev_part_master (row_kind);

-- Trigram 인덱스 (ILIKE 가속)
CREATE INDEX IF NOT EXISTS idx_dpm_part_name_trgm
    ON dev_part_master USING gin (part_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_dpm_change_reason_trgm
    ON dev_part_master USING gin (change_reason_raw gin_trgm_ops);

-- JSONB GIN (extra_fields 키 검색)
CREATE INDEX IF NOT EXISTS idx_dpm_extra_gin
    ON dev_part_master USING gin (extra_fields);

-- HNSW 벡터 인덱스 (임베딩 채워진 후 사용; 임베딩 없을 때도 인덱스 생성 자체는 OK)
CREATE INDEX IF NOT EXISTS idx_dpm_embedding_hnsw
    ON dev_part_master USING hnsw (embedding_dense vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Sparse 벡터 인덱스 (pgvector 0.7+; sparsevec_ip_ops = 내적 = BGE-M3 lexical score)
CREATE INDEX IF NOT EXISTS idx_dpm_embedding_sparse_hnsw
    ON dev_part_master USING hnsw (embedding_sparse sparsevec_ip_ops)
    WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- 자동 분류 트리거: INSERT/UPDATE 시 row_kind 채움
-- =============================================================================
CREATE OR REPLACE FUNCTION classify_row_kind()
RETURNS TRIGGER AS $$
BEGIN
    NEW.row_kind := CASE
        -- marker: part_no_new가 비어있거나 마커 문자
        WHEN NEW.part_no_new IS NULL
             OR NEW.part_no_new IN ('X', '←', '-', '?')
          THEN 'marker'
        -- change_history: 의미 있는 변경 사유 텍스트가 있는 행
        WHEN NEW.change_reason_raw IS NOT NULL
             AND length(NEW.change_reason_raw) > 5
             AND NEW.change_reason_raw NOT IN ('-', '_', '/', 'N/A')
          THEN 'change_history'
        -- 그 외: 일반 BOM
        ELSE 'bom_entry'
    END;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_dpm_classify ON dev_part_master;
CREATE TRIGGER trg_dpm_classify
    BEFORE INSERT OR UPDATE OF change_reason_raw, part_no_new, classification
    ON dev_part_master
    FOR EACH ROW
    EXECUTE FUNCTION classify_row_kind();

-- =============================================================================
-- 시드: 양식 등록
-- =============================================================================
INSERT INTO form_registry
    (form_id, form_type, target_table, display_name, description,
     header_row_index, data_start_row, column_mapping, sheet_name_patterns)
VALUES
    (
        'dev_part_master_v1',
        'dev_part_master',
        'dev_part_master',
        '통합 Master (이중헤더)',
        '동유럽/싱가포르 양식. row 8=메인 헤더, row 9=서브(Base/New).',
        8, 10,
        '{"BOM Level": "bom_level_raw", "Part Type": "part_type", "P/No": "_pno_group", "부품명": "part_name", "변경점": "change_point_raw", "변경사유": "change_reason_raw", "양산처": "supplier", "Classification": "classification"}'::jsonb,
        ARRAY['Good-%', 'Master(%']
    ),
    (
        'dev_part_master_v2',
        'dev_part_master',
        'dev_part_master',
        '통합 Master (단일헤더, DRBFM 변형)',
        '모리셔스/북유럽/유럽 양식. row 9=단일헤더.',
        9, 10,
        '{"BOM Level": "bom_level_raw", "Part Type": "part_type", "Base P/No": "part_no_base", "New P/No": "part_no_new", "Class Desc.": "part_name", "변경점": "change_point_raw", "변경사유": "change_reason_raw", "구분": "classification"}'::jsonb,
        ARRAY['변경부품 list%', 'WSED%', 'WS7D%', 'WS9D%']
    ),
    (
        'dev_part_master_dynamic',
        'dev_part_master',
        'dev_part_master',
        '동적 개발부품/변경부품 양식',
        '상위 20행에서 헤더를 탐색하고 P/no., Part No, Desc., Lvl 등의 alias를 매핑.',
        NULL, NULL,
        '{"Lvl": "bom_level_raw", "Level": "bom_level_raw", "P/no.": "part_no_new", "Part No": "part_no_new", "Desc.": "part_name", "Part": "part_name", "변경 내역": "change_point_raw", "변경 사유": "change_reason_raw", "신규": "classification"}'::jsonb,
        ARRAY['%']
    )
ON CONFLICT (form_id) DO NOTHING;
