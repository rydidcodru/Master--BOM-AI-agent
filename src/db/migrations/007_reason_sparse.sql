-- ============================================================
-- 007 — change_event.reason_sparse (BGE-M3 lexical sparse 채널) (2026-06-08)
--
-- 차용: Master--BOM-AI-agent etl_embed의 embedding_sparse sparsevec(250002) + sparsevec_ip_ops.
-- 동기: ms 데이터를 lg에 dense만 임베딩해 들이면서 ms의 lexical(sparse) 채널이 손실 →
-- ms_only 0.92→0.80 하락(재측정). dense(cosine)+trgm에 더해 sparse(inner product)를 RRF에
-- 합쳐 부품코드/모델 exact-match recall을 회복·보강한다.
--
-- sparse = XLM-RoBERTa vocab(250002) 위 lexical weight. 질의·코퍼스 모두 FlagEmbedding
-- BGEM3FlagModel로 인코딩(src/embed/sparse_embedder.py). 적재는 update_event_sparse_embeddings.
--
-- 요구: pgvector >= 0.7 (lg=0.8.2 확인). 기존 4테이블 파괴 0 — change_event 컬럼/인덱스 추가만.
-- idempotent: IF NOT EXISTS. 롤백: 007_*.rollback.sql.
-- ============================================================

ALTER TABLE change_event ADD COLUMN IF NOT EXISTS reason_sparse sparsevec(250002);

-- HNSW(inner product) 인덱스 — 코퍼스가 작으면(수백~수천) exact scan도 충분하지만 ms와 정합.
CREATE INDEX IF NOT EXISTS idx_ce_reason_sparse
    ON change_event USING hnsw (reason_sparse sparsevec_ip_ops);

COMMENT ON COLUMN change_event.reason_sparse IS
    'BGE-M3 lexical sparse(250002). dense+trgm에 더해 RRF 융합 — 코드/모델 exact-match 보강(007).';
