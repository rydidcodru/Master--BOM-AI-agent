-- ============================================================
-- 001 ROLLBACK — Agentic RAG 가산 마이그레이션 되돌리기.
--
-- 수동 실행 전용 (init_db는 *.rollback.sql을 건너뜀: 파일명 필터).
-- 기존 4테이블·데이터는 건드리지 않음. 보조 테이블 DROP + change_intent 컬럼 DROP만.
-- DROP 순서: FK 의존 자식(change_line) → 부모(change_event). bom_edge/agent_feedback/
-- tool_call_log는 독립.
-- ============================================================

DROP INDEX IF EXISTS idx_dpm_change_intent;
ALTER TABLE dev_part_master DROP COLUMN IF EXISTS change_intent;

DROP TABLE IF EXISTS agent_feedback;
DROP TABLE IF EXISTS tool_call_log;
DROP TABLE IF EXISTS change_line;
DROP TABLE IF EXISTS change_event;
DROP TABLE IF EXISTS bom_edge;
