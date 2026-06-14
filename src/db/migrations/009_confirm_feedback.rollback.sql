-- 009 rollback — confirm_feedback 제거. 검색/판정 경로 영향 없음(기록 전용 가산 테이블).
DROP INDEX IF EXISTS idx_confirm_feedback_created;
DROP TABLE IF EXISTS confirm_feedback;
