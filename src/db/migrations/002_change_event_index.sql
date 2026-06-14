-- ============================================================
-- 002 — change_event 검색 인덱스 신축 (2026-05-29)
--
-- 목적: CLAUDE.md 구조 B를 실제로 살린다. 검색 인덱스 = change_event.reason_embedding
--       (= change_log + change_reason 임베딩). 부품명/모델은 임베딩에 미포함.
--       change_line은 "한 사유로 함께 바뀐 부품 세트"(HITL 게이트가 확정할 단위).
--
-- 절대 원칙: 기존 4테이블 파괴적 변경 0. 001에서 만든 change_event/change_line/
--           agent_feedback에 **컬럼 추가만**. idempotent(ADD COLUMN IF NOT EXISTS).
--
-- 그룹핑 규칙(사용자 확정 2026-05-29): **변경사유별 분할**.
--   한 change_event = (file_id, base_model, new_model, change_reason) 한 묶음.
--   그 안의 dev_part_master 변경행 각각 = 한 change_line.
--
-- 롤백: 002_change_event_index.rollback.sql (추가 컬럼/인덱스 DROP).
-- ============================================================

-- ── change_event: 검색 인덱스 컬럼 가산 ───────────────────────
-- file_id: 파일 스코핑 + 로더 idempotency(재적재 시 file 단위 delete-reinsert).
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS file_id BIGINT
    REFERENCES source_files(file_id) ON DELETE CASCADE;
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS change_log TEXT;
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS change_reason TEXT;
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS reason_embedding vector(1024);
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS source_ref TEXT;

CREATE INDEX IF NOT EXISTS idx_change_event_file_id ON change_event (file_id);

-- 검색 인덱스: reason_embedding HNSW (dev_part_master와 동일 패턴).
CREATE INDEX IF NOT EXISTS idx_change_event_reason_embedding
    ON change_event USING hnsw (reason_embedding vector_cosine_ops);

-- lexical(변경내역/사유 word_similarity)용 trigram GIN.
CREATE INDEX IF NOT EXISTS idx_change_event_change_log_trgm
    ON change_event USING gin (change_log gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_change_event_change_reason_trgm
    ON change_event USING gin (change_reason gin_trgm_ops);


-- ── change_line: 영향 부품 라인 컬럼 가산 (CLAUDE.md 라인 스키마) ─
-- 기존 changepoint(=변경내역/변경점)는 그대로 재사용. 아래는 출력·전개에 필요한 보존 필드.
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS part_name TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS qty_base NUMERIC;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS qty_new NUMERIC;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS classification TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS supplier TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS mold_yn TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS inhouse_yn TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS approval_test_yn TEXT;
ALTER TABLE change_line ADD COLUMN IF NOT EXISTS source_ref TEXT;


-- ── agent_feedback: HITL 확정 게이트 컬럼 가산 (Phase 2에서 사용) ─
ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS change_point TEXT;
ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS new_pno_input TEXT;
ALTER TABLE agent_feedback ADD COLUMN IF NOT EXISTS is_anchor BOOLEAN;
