-- ============================================================
-- 006 — source_project 출처 태그 (2026-06-08)
--
-- 목적: ms(Master--BOM-AI-agent) 데이터를 lg 코퍼스에 **tagged import**해 production 검색
-- recall을 올리되, 'lg' vs 'ms'를 구분해 (a) 깨끗한 A/B 비교(필터), (b) 출력 출처표기를 유지.
-- (이전 master_import는 무태그라 56% 조용한 오염을 일으켰음 — 그 재발 방지.)
--
-- dev_part_master / change_event에 source_project TEXT 추가. 기존 행은 전부 본 파이프라인
-- 산출이므로 'lg'로 백필. 이후 lg 적재는 기본 'lg', ms tagged import는 'ms'.
--
-- 기존 4테이블 파괴적 변경 0 — 컬럼 추가 + 값 백필(가산). idempotent. 롤백: 006_*.rollback.sql.
-- ============================================================

ALTER TABLE dev_part_master ADD COLUMN IF NOT EXISTS source_project TEXT;
ALTER TABLE change_event    ADD COLUMN IF NOT EXISTS source_project TEXT;

-- 기존 행 = 본 파이프라인(lg) 산출 → 'lg' 백필 (NULL인 것만).
UPDATE dev_part_master SET source_project = 'lg' WHERE source_project IS NULL;
UPDATE change_event    SET source_project = 'lg' WHERE source_project IS NULL;

-- 검색 필터(source_project = 'lg' | 'ms')용 인덱스.
CREATE INDEX IF NOT EXISTS idx_dpm_source_project ON dev_part_master (source_project);
CREATE INDEX IF NOT EXISTS idx_ce_source_project  ON change_event (source_project);

COMMENT ON COLUMN dev_part_master.source_project IS
    '출처 프로젝트: lg(본 파이프라인) | ms(Master tagged import). 필터·출처표기·A/B비교(006).';
