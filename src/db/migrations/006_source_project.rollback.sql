-- 006 rollback — source_project 태그 컬럼/인덱스 DROP. 가산이라 비파괴.
DROP INDEX IF EXISTS idx_dpm_source_project;
DROP INDEX IF EXISTS idx_ce_source_project;
ALTER TABLE dev_part_master DROP COLUMN IF EXISTS source_project;
ALTER TABLE change_event    DROP COLUMN IF EXISTS source_project;
