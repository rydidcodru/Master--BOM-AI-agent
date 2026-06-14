"""D-012 — bge-m3 임베딩 (Ollama batch endpoint, sentence-transformers fallback).

D-012 변경:
  - Ollama `/api/embed` (multi-prompt batch) 사용. 이전 `/api/embeddings`(단건)
    대비 CPU 3~5x, GPU 10x 가까이 빨라짐.
  - 빈 텍스트는 zero-vector slot으로 채워 batch 호출에서 position 보존.
  - 504/timeout 시 batch 크기를 자동으로 절반으로 줄여 retry.

ENABLE_EMBEDDING=1 미설정 시 RuntimeError. import만으로 모델 다운로드 / 네트워크
호출 발생하지 않음 (D-004 게이트).
"""

from __future__ import annotations

import os
import time
from functools import lru_cache

import requests

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "bge-m3"
EMBEDDING_DIM = 1024
DEFAULT_BATCH = 64       # bge-m3 + Ollama batch 호출 최적 (실측, GPU 시 ↑ 가능)
MIN_BATCH = 4            # 504 발생 시 절반으로 줄이다가 최저
HTTP_TIMEOUT = 300       # 큰 batch는 GPU 워밍업 포함 ~수십초 가능


def embedding_enabled() -> bool:
    return os.environ.get("ENABLE_EMBEDDING", "0") == "1"


def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def _embed_batch_call(texts: list[str], model: str, host: str) -> list[list[float]]:
    """`/api/embed` 한 번 호출 → embeddings list. 빈 입력 zero-vector 처리."""
    if not texts:
        return []

    # Ollama는 빈 string도 받지만, 응답 일관성 위해 zero-pad
    nonempty_pairs = [(i, t) for i, t in enumerate(texts) if t]
    if not nonempty_pairs:
        return [[0.0] * EMBEDDING_DIM] * len(texts)

    indices, inputs = zip(*nonempty_pairs)
    resp = requests.post(
        f"{host}/api/embed",
        json={"model": model, "input": list(inputs)},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    embs = data.get("embeddings") or []
    if len(embs) != len(inputs):
        raise RuntimeError(
            f"Ollama returned {len(embs)} embeddings for {len(inputs)} inputs"
        )

    out: list[list[float]] = [[0.0] * EMBEDDING_DIM] * len(texts)
    for idx, emb in zip(indices, embs):
        out[idx] = emb
    return out


def _embed_ollama(texts: list[str], model: str, batch: int = DEFAULT_BATCH) -> list[list[float]]:
    """Ollama batch endpoint 호출. 504/timeout 시 batch 크기 자동 축소.

    Args:
        texts: 임베딩할 텍스트 리스트.
        model: ``bge-m3`` 등.
        batch: 한 HTTP 요청당 최대 입력 수.
    """
    host = _ollama_host()
    out: list[list[float]] = []
    current_batch = max(batch, MIN_BATCH)
    i = 0
    total = len(texts)
    while i < total:
        chunk = texts[i : i + current_batch]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                embs = _embed_batch_call(chunk, model, host)
                out.extend(embs)
                i += len(chunk)
                last_exc = None
                break
            except requests.exceptions.HTTPError as exc:
                # 큰 batch에서 OOM/timeout이면 절반으로 축소 후 재시도
                last_exc = exc
                code = exc.response.status_code if exc.response else 0
                if code in (413, 500, 502, 503, 504) and current_batch > MIN_BATCH:
                    current_batch = max(current_batch // 2, MIN_BATCH)
                    log.warning(
                        "embed.ollama_batch_shrink", attempt=attempt,
                        new_batch=current_batch, http=code,
                    )
                    continue
                log.warning("embed.ollama_retry", attempt=attempt, error=str(exc))
                time.sleep(2**attempt)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if current_batch > MIN_BATCH:
                    current_batch = max(current_batch // 2, MIN_BATCH)
                    log.warning("embed.ollama_batch_timeout_shrink", new_batch=current_batch)
                    continue
                log.warning("embed.ollama_retry", attempt=attempt, error=str(exc))
                time.sleep(2**attempt)
        else:
            raise RuntimeError(f"Ollama batch embedding failed after retries: {last_exc}")
    return out


@lru_cache(maxsize=1)
def _load_st_model(model_name: str) -> object:
    """sentence-transformers fallback (~2GB download). lazy."""
    from sentence_transformers import SentenceTransformer

    log.info("embed.load_st_model", model=model_name)
    return SentenceTransformer(model_name)


def _embed_st(texts: list[str], model_name: str) -> list[list[float]]:
    model = _load_st_model(model_name)
    return model.encode(texts, normalize_embeddings=True).tolist()  # type: ignore[attr-defined, no-any-return]


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """텍스트 리스트 → 임베딩 리스트. Ollama batch 우선, 실패 시 ST fallback.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    if not embedding_enabled():
        raise RuntimeError(
            "Embedding disabled. Set ENABLE_EMBEDDING=1 and run Ollama "
            "(or install 'embed' extra) to embed text."
        )
    if not texts:
        return []
    name = model or os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    try:
        return _embed_ollama(texts, name)
    except Exception as exc:  # noqa: BLE001
        log.warning("embed.ollama_failed", error=str(exc))
        st_model = os.environ.get("EMBED_ST_MODEL", "BAAI/bge-m3")
        return _embed_st(texts, st_model)
