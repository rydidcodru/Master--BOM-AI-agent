-- 003 rollback — raw_json 가산 컬럼 DROP.
-- 가산 컬럼이라 롤백해도 기존 4테이블 데이터/ETL·검색 경로 영향 없음.
ALTER TABLE dev_part_master DROP COLUMN IF EXISTS raw_json;
