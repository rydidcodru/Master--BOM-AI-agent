"""BGE-M3 임베딩 인코더 (dense 1024 + sparse lexical).

전략 근거:
  BGE-M3 (Chen et al., 2024 — "M3-Embedding: Multi-Linguality, Multi-Functionality,
  Multi-Granularity Text Embeddings Through Self-Knowledge Distillation")는
  한 모델에서 dense / sparse(SPLADE-like) / multi-vector(ColBERT) 표현을 동시에 생성.
  이 ETL은 부품번호·모델코드처럼 exact match가 중요한 텍스트가 섞여 있어
  dense 단일보다 dense+sparse 하이브리드가 recall에서 유리.

  - dense: 1024차원, cosine.  스키마의 vector(1024)와 일치.
  - sparse: XLM-RoBERTa 토큰 vocab(250002) 위의 lexical weight.
            PG sparsevec(250002)에 저장, inner product = lexical score.

기본 모델: BAAI/bge-m3 (≈2.2GB, FP16 GPU 권장).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

# XLM-RoBERTa vocabulary size used by BGE-M3.
BGE_M3_VOCAB_SIZE = 250002
BGE_M3_DENSE_DIM = 1024
DEFAULT_MODEL = "BAAI/bge-m3"


@dataclass
class EncodedBatch:
    """배치 1개의 인코딩 결과."""
    dense: list[list[float]]          # [N, 1024]
    sparse: list[dict[int, float]]    # [N], {token_id: weight}


class BGEM3Embedder:
    """BGE-M3 wrapper. 첫 호출 시 모델을 lazy-load."""

    def __init__(
        self,
        model_name: str | None = None,
        use_fp16: bool = True,
        device: str | None = None,
        max_length: int = 512,
    ):
        self.model_name = model_name or os.getenv("EMBED_MODEL", DEFAULT_MODEL)
        self.use_fp16 = use_fp16
        self.device = device
        self.max_length = max_length
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        # 지연 import: FlagEmbedding은 무거우므로 사용 시점까지 미룸.
        from FlagEmbedding import BGEM3FlagModel  # type: ignore

        kwargs: dict = {"use_fp16": self.use_fp16}
        if self.device:
            kwargs["device"] = self.device
        self._model = BGEM3FlagModel(self.model_name, **kwargs)
        return self._model

    def encode(self, texts: list[str], batch_size: int = 16) -> EncodedBatch:
        model = self._load()
        out = model.encode(
            texts,
            batch_size=batch_size,
            max_length=self.max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = out["dense_vecs"]
        lex = out["lexical_weights"]  # list[dict[str, float]]

        dense_list: list[list[float]] = [vec.tolist() for vec in dense]
        sparse_list: list[dict[int, float]] = []
        for lw in lex:
            d: dict[int, float] = {}
            for tok_id, weight in lw.items():
                w = float(weight)
                if w <= 0.0:
                    continue
                d[int(tok_id)] = w
            sparse_list.append(d)
        return EncodedBatch(dense=dense_list, sparse=sparse_list)


# ---------- pgvector serialization helpers ----------

def dense_to_pg(vec: list[float]) -> str:
    """vector(1024) 리터럴: '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def sparse_to_pg(weights: dict[int, float], dim: int = BGE_M3_VOCAB_SIZE) -> str:
    """sparsevec 리터럴: '{idx:val,...}/dim'. 빈 dict면 빈 sparsevec."""
    if not weights:
        return "{}/" + str(dim)
    # pgvector sparsevec은 1-based index 사용. BGE-M3 lexical_weights는 token_id (0-base).
    parts = ",".join(
        f"{idx + 1}:{val:.6f}"
        for idx, val in sorted(weights.items())
    )
    return "{" + parts + "}/" + str(dim)


def encode_for_pg(
    embedder: BGEM3Embedder,
    texts: list[str],
    batch_size: int = 16,
) -> Iterable[tuple[str, str]]:
    """텍스트 리스트 → (dense_literal, sparse_literal) 튜플 generator."""
    enc = embedder.encode(texts, batch_size=batch_size)
    for d, s in zip(enc.dense, enc.sparse):
        yield dense_to_pg(d), sparse_to_pg(s)
