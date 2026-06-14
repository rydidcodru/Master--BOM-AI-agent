-- ============================================================
-- 004 — 행 단위 멱등성 가시화 (2026-06-08)
--
-- 차용 출처: Master--BOM-AI-agent(ms) ON CONFLICT (file_id, sheet_name, source_row)
-- DO NOTHING — 재적재 시 중복 행을 조용히 스킵하는 멱등 적재.
--
-- 본 마이그레이션은 가시화 컬럼만 추가한다(가산·안전):
--   ingestion_log.rows_skipped — load_run이 멱등성으로 스킵한 행 수.
--
-- 멱등 스킵 로직 자체는 코드(src/db/load.py load_run)에 구현됨 — 적재 전 기존
-- (file_id, sheet_name, source_row) 키를 조회해 중복 행을 건너뛴다. 실 전처리 어댑터는
-- source_row를 행마다 고유 부여하므로 안전하다(검증: bom_ag_grid 6,369행 dup 0).
--
-- ── 보류(별도 진행): DB 레벨 UNIQUE 제약 ──────────────────────────────
-- 다음 인덱스는 코드 스킵의 안전망이지만, 라이브 dev_part_master에 기존 중복
-- (재적재 base_master_24 204그룹 + 비-파이프라인 master_import 523그룹)이 있어
-- **중복 정리 전에는 생성이 실패한다**. 정리 방침 확정 후 005에서 적용 예정:
--   -- CREATE UNIQUE INDEX IF NOT EXISTS uq_dpm_source
--   --     ON dev_part_master (file_id, sheet_name, source_row);
--
-- idempotent: ADD COLUMN IF NOT EXISTS. 롤백: 004_*.rollback.sql.
-- ============================================================

ALTER TABLE ingestion_log ADD COLUMN IF NOT EXISTS rows_skipped INTEGER;

COMMENT ON COLUMN ingestion_log.rows_skipped IS
    '멱등성으로 스킵된 중복 행 수(재적재). ms ON CONFLICT DO NOTHING 차용(004).';
