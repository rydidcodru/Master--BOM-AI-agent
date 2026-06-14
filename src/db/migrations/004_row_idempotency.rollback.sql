-- 004 rollback — ingestion_log.rows_skipped 가산 컬럼 DROP. 가산이라 비파괴.
ALTER TABLE ingestion_log DROP COLUMN IF EXISTS rows_skipped;
