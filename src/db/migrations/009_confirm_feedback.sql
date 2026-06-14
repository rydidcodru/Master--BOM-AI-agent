-- ============================================================
-- 009 — confirm_feedback (P8: HITL 확정 = 평가 데이터) (2026-06-12)
--
-- 한 확정 행위당 1행: 쿼리·의도·후보 순위·채택/수동보강 결과를 스냅샷으로 남긴다.
-- "사람의 확정이 곧 정확도 체크"라는 운영 원리를 그대로 기록 — 수작업 골든셋 없이
-- db feedback-report가 운영 중 자동 생성되는 recall/precision 대시보드가 된다.
--
-- ⚠️ 기록 전용 — 검색·매핑·판정 경로는 이 테이블을 절대 읽지 않는다(읽기는 리포트 CLI만).
-- 기존 4테이블 파괴 0 — additive·멱등(CREATE TABLE/INDEX IF NOT EXISTS).
-- 롤백: 009_*.rollback.sql.
-- ============================================================

CREATE TABLE IF NOT EXISTS confirm_feedback (
  id BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT now(),
  query_text TEXT,
  intent_json JSONB,
  candidates JSONB,              -- [{event_id, rank, rrf, struct, low_confidence, rescued}]
  adopted_event_ids INT[],
  manual_added_event_ids INT[],  -- 7.2(c) 경유 채택분
  low_confidence BOOLEAN,
  source_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_confirm_feedback_created
  ON confirm_feedback (created_at);

COMMENT ON TABLE confirm_feedback IS
    'P8 HITL 확정 스냅샷(기록 전용 — 검색/판정 경로 미참조). db feedback-report로만 읽음.';
