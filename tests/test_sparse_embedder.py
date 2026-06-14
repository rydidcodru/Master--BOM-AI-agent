"""sparse_embedder.sparse_to_pg — BGE-M3 lexical weight → pgvector sparsevec 리터럴 (ms 정합)."""

from __future__ import annotations

from src.embed.sparse_embedder import BGE_M3_VOCAB_SIZE, sparse_to_pg


def test_sparse_to_pg_one_based_sorted():
    # token_id 0-based → sparsevec 1-based (+1), index 오름차순 정렬.
    lit = sparse_to_pg({5: 0.8, 1: 0.3, 2: 0.5})
    assert lit == "{2:0.300000,3:0.500000,6:0.800000}/" + str(BGE_M3_VOCAB_SIZE)


def test_sparse_to_pg_drops_nonpositive():
    lit = sparse_to_pg({0: 0.0, 1: -0.2, 2: 0.4})
    assert lit == "{3:0.400000}/" + str(BGE_M3_VOCAB_SIZE)  # 1번(0)·2번(음수) 제외, token2→idx3


def test_sparse_to_pg_empty():
    assert sparse_to_pg({}) == "{}/" + str(BGE_M3_VOCAB_SIZE)
    assert sparse_to_pg({0: 0.0}) == "{}/" + str(BGE_M3_VOCAB_SIZE)


def test_sparse_to_pg_custom_dim():
    assert sparse_to_pg({0: 1.0}, dim=10) == "{1:1.000000}/10"
