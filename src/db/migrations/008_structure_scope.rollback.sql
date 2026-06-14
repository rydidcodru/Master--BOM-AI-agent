-- 008 rollback — 구조 스코프 보조 컬럼/인덱스 DROP. reason 검색 경로 영향 없음(가산).
DROP INDEX IF EXISTS idx_change_line_pname_canon_trgm;
DROP INDEX IF EXISTS idx_change_event_module_tags;
ALTER TABLE change_line  DROP COLUMN IF EXISTS part_name_canon;
ALTER TABLE change_line  DROP COLUMN IF EXISTS module_pno;
ALTER TABLE change_line  DROP COLUMN IF EXISTS module_name;
ALTER TABLE change_event DROP COLUMN IF EXISTS module_tags;
