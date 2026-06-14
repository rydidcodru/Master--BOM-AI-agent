-- ============================================================
-- 008 — 구조 스코프 검색 부스트 + 후보-BOM 매핑 보조 컬럼 (2026-06-12)
--
-- 동기: ①.5 구조 스코프 부스트(SEARCH_INCLUDE_STRUCT)와 ②.5 후보-BOM 매핑이
-- change_line.part_name의 canonical 형태(part_name_canon)와 모듈 귀속(module_pno/
-- module_name/module_tags)을 사용한다. canon은 `db change-events --recanon`으로 백필
-- (canonicalize_part_name — token_canon 오타/변이 정규화 + 모델코드/치수 토큰 제거).
--
-- ⚠️ 모듈 태깅(module_pno/module_name/module_tags 채우기)은 이번 범위에서 **컬럼만
-- 준비**하고 ETL 역추적 로직은 구현하지 않는다 — 후속 Phase로 명시적 보류.
--
-- 기존 4테이블 파괴 0 — additive·멱등만(ADD COLUMN IF NOT EXISTS / CREATE INDEX IF
-- NOT EXISTS). 롤백: 008_*.rollback.sql.
-- ============================================================

ALTER TABLE change_line  ADD COLUMN IF NOT EXISTS part_name_canon TEXT;
ALTER TABLE change_line  ADD COLUMN IF NOT EXISTS module_pno  TEXT;
ALTER TABLE change_line  ADD COLUMN IF NOT EXISTS module_name TEXT;
ALTER TABLE change_event ADD COLUMN IF NOT EXISTS module_tags TEXT[];

CREATE INDEX IF NOT EXISTS idx_change_line_pname_canon_trgm
  ON change_line USING gin (part_name_canon gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_change_event_module_tags
  ON change_event USING gin (module_tags);

COMMENT ON COLUMN change_line.part_name_canon IS
    'canonicalize_part_name(part_name) — 오타/변이 정규화 + 모델코드·치수 토큰 제거. --recanon 백필(008).';
COMMENT ON COLUMN change_line.module_pno IS
    '라인이 속한 모듈 품번 (ETL 역추적 — 컬럼만 준비, 후속 Phase에서 채움; 008).';
COMMENT ON COLUMN change_line.module_name IS
    '라인이 속한 모듈명 (ETL 역추적 — 컬럼만 준비, 후속 Phase에서 채움; 008).';
COMMENT ON COLUMN change_event.module_tags IS
    '이벤트의 모듈 태그 (ETL 역추적 — 컬럼만 준비, 후속 Phase에서 채움; 008).';
