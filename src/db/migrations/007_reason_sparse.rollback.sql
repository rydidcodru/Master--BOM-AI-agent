-- 007 rollback — sparse 채널 인덱스/컬럼 DROP. dense+trgm 검색 경로 영향 없음(가산).
DROP INDEX IF EXISTS idx_ce_reason_sparse;
ALTER TABLE change_event DROP COLUMN IF EXISTS reason_sparse;
