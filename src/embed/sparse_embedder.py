"""BGE-M3 sparse(lexical) 임베딩 — pgvector ``sparsevec(250002)`` 리터럴 생성.

차용: Master--BOM-AI-agent etl_embed/parsers/embedder.py. BGE-M3는 한 모델에서 dense +
sparse(SPLADE-like lexical weight)를 동시 생성한다. lg는 dense를 Ollama/sentence-transformers로
이미 쓰고, 여기서는 **sparse 채널만** FlagEmbedding ``BGEM3FlagModel``로 추가한다(검색키=변경
내역+변경사유 텍스트의 lexical weight → 코드/모델 토큰 exact-match 보강).

- sparse = XLM-RoBERTa vocab(250002) 위 {token_id: weight}.
- pgvector ``sparsevec``는 1-based index → ``sparse_to_pg``가 ``idx+1`` 변환(ms와 정합).
- ``ENABLE_EMBEDDING=1`` 게이트. 모델 lazy-load(첫 호출 시 ~2GB). import만으로는 네트워크/로드 0.
"""

from __future__ import annotations

import os
from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)

BGE_M3_VOCAB_SIZE = 250002
DEFAULT_ST_MODEL = "BAAI/bge-m3"

_model: Any = None


def sparse_embedding_enabled() -> bool:
    return os.environ.get("ENABLE_EMBEDDING", "0") == "1"


def _load_model() -> Any:
    """FlagEmbedding ``BGEM3FlagModel`` lazy-load (싱글톤)."""
    global _model
    if _model is not None:
        return _model
    from FlagEmbedding import BGEM3FlagModel  # 지연 import — 무거운 의존

    name = os.environ.get("EMBED_ST_MODEL", DEFAULT_ST_MODEL)
    log.info("sparse_embed.load_model", model=name)
    _model = BGEM3FlagModel(name, use_fp16=False)
    return _model


def sparse_to_pg(weights: dict[int, float], dim: int = BGE_M3_VOCAB_SIZE) -> str:
    """``{token_id: weight}`` → pgvector sparsevec 리터럴 ``'{idx:val,...}/dim'`` (1-based, ms 정합)."""
    pos = {int(t) + 1: float(w) for t, w in weights.items() if float(w) > 0.0}
    if not pos:
        return "{}/" + str(dim)
    parts = ",".join(f"{idx}:{val:.6f}" for idx, val in sorted(pos.items()))
    return "{" + parts + "}/" + str(dim)


def embed_sparse(texts: list[str], *, batch_size: int = 16, max_length: int = 512) -> list[str]:
    """텍스트 리스트 → sparsevec 리터럴 리스트. ENABLE_EMBEDDING=1 필요.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    if not sparse_embedding_enabled():
        raise RuntimeError("Set ENABLE_EMBEDDING=1 to compute BGE-M3 sparse embeddings.")
    if not texts:
        return []
    model = _load_model()
    out = model.encode(
        texts,
        batch_size=batch_size,
        max_length=max_length,
        return_dense=False,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    lex = out["lexical_weights"]  # list[dict[token_id, weight]]
    return [sparse_to_pg(lw) for lw in lex]


def embed_sparse_one(text: str) -> str:
    """단일 텍스트 → sparsevec 리터럴 (질의용)."""
    return embed_sparse([text])[0]


__all__ = [
    "BGE_M3_VOCAB_SIZE",
    "embed_sparse",
    "embed_sparse_one",
    "sparse_embedding_enabled",
    "sparse_to_pg",
]
