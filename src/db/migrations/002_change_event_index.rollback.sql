-- ============================================================
-- 002 ROLLBACK — change_event 검색 인덱스 신축 되돌리기.
--
-- 수동 실행 전용 (init_db는 *.rollback.sql을 건너뜀: 파일명 필터).
-- 001에서 만든 테이블/데이터는 건드리지 않음. 002가 추가한 컬럼/인덱스만 DROP.
-- DROP 순서: 인덱스 → 컬럼.
-- ============================================================

DROP INDEX IF EXISTS idx_change_event_change_reason_trgm;
DROP INDEX IF EXISTS idx_change_event_change_log_trgm;
DROP INDEX IF EXISTS idx_change_event_reason_embedding;
DROP INDEX IF EXISTS idx_change_event_file_id;

ALTER TABLE change_event DROP COLUMN IF EXISTS source_ref;
ALTER TABLE change_event DROP COLUMN IF EXISTS reason_embedding;
ALTER TABLE change_event DROP COLUMN IF EXISTS change_reason;
ALTER TABLE change_event DROP COLUMN IF EXISTS change_log;
ALTER TABLE change_event DROP COLUMN IF EXISTS file_id;

ALTER TABLE change_line DROP COLUMN IF EXISTS source_ref;
ALTER TABLE change_line DROP COLUMN IF EXISTS approval_test_yn;
ALTER TABLE change_line DROP COLUMN IF EXISTS inhouse_yn;
ALTER TABLE change_line DROP COLUMN IF EXISTS mold_yn;
ALTER TABLE change_line DROP COLUMN IF EXISTS supplier;
ALTER TABLE change_line DROP COLUMN IF EXISTS classification;
ALTER TABLE change_line DROP COLUMN IF EXISTS qty_new;
ALTER TABLE change_line DROP COLUMN IF EXISTS qty_base;
ALTER TABLE change_line DROP COLUMN IF EXISTS part_name;

ALTER TABLE agent_feedback DROP COLUMN IF EXISTS is_anchor;
ALTER TABLE agent_feedback DROP COLUMN IF EXISTS new_pno_input;
ALTER TABLE agent_feedback DROP COLUMN IF EXISTS change_point;
