-- ============================================================
-- 005 — dev_part_master 행 단위 UNIQUE (2026-06-08)
--
-- 차용: ms ON CONFLICT (file_id, sheet_name, source_row). 004의 코드 멱등 스킵에 더해
-- DB 레벨 안전망(제약)을 건다.
--
-- 선행 조건(완료): master_import(비-파이프라인 ms 스크래치, 2,860행) 제거 +
-- base_master_24 재적재 중복(204행, 내용 동일) 정리 → dev_part_master 중복 0 확인 후 적용.
-- 실 어댑터는 source_row를 행마다 고유 부여하므로 NULL이 아닌 키는 모두 고유.
-- NULL 키(sheet/row 미설정)는 PG NULL-distinct라 다중 허용.
--
-- 기존 4테이블 파괴적 변경 0 — 제약(인덱스) 추가만. idempotent: IF NOT EXISTS. 롤백: 005_*.rollback.sql.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_dpm_source
    ON dev_part_master (file_id, sheet_name, source_row);
