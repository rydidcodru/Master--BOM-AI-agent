-- ============================================================
-- 003 — raw_json 출처보존 가산 마이그레이션 (2026-06-08)
--
-- 차용 출처: Master--BOM-AI-agent(ms) Neo4j ``BomLine.rawJson`` — 원본 엑셀 행 전체를
-- JSON으로 필수 보존하고, 정규화 오류 시 rawJson fallback 질의로 복구하는 설계.
--
-- 동기: lg ``dev_part_master.extra_fields``는 '표준 매핑 안 된 잔여 컬럼'만 담아 매핑된
-- 컬럼(part_name 등)은 빠지고 스키마도 미정의 → 감사 추적의 원본 복원력이 약함(비교 기준 #2).
-- ``raw_json``은 매핑·미매핑을 모두 포함한 원본 행 전체 스냅샷(정의된 단일 컬럼)을 보존한다.
--
-- 절대 원칙: 기존 4테이블 파괴적 변경 0 — dev_part_master 컬럼 1개 추가만(CLAUDE.md 허용).
-- idempotent: ADD COLUMN IF NOT EXISTS. 재실행 안전. 롤백: 003_*.rollback.sql.
-- 기존 행은 NULL(이번 적재분부터 채워짐 — 백필은 원본 parquet 재적재 시).
-- ============================================================

ALTER TABLE dev_part_master ADD COLUMN IF NOT EXISTS raw_json JSONB;

COMMENT ON COLUMN dev_part_master.raw_json IS
    '원본 행 전체 스냅샷(매핑+미매핑, 정의 스키마). 감사·복구용. ms BomLine.rawJson 차용(003).';
