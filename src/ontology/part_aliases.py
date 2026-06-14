"""부품/모듈명 별칭 로더 — ``config/part_aliases.yaml`` (P7.1).

``canonicalize_part_name``이 token_canon에 더해 적용하는 variant→canonical 사상.
주 목적은 trgm 유사도가 0인 **한↔영 쌍**을 메워 ①.5 닻 탐색·②.5 매칭의 무증상
실패를 막는 것. 순수 결정론 — LLM/DB import 0.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import yaml

from src.utils.paths import CONFIG_DIR

PART_ALIASES_PATH = CONFIG_DIR / "part_aliases.yaml"


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    if not PART_ALIASES_PATH.exists():
        return {}
    return cast(dict[str, Any], yaml.safe_load(PART_ALIASES_PATH.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=1)
def variant_to_canonical() -> dict[str, str]:
    """variant(casefold) → canonical 단어 사상. 1:N 충돌은 conflicts로 분리되어 제외됨."""
    out: dict[str, str] = {}
    for canonical, variants in (_raw().get("aliases") or {}).items():
        for v in variants or []:
            out[str(v).casefold()] = str(canonical)
    return out


@lru_cache(maxsize=1)
def _phrase_variants() -> list[tuple[str, str]]:
    """다단어 variant만 (최장 일치 우선 정렬). 단어 단위 치환으로 안 잡히는 구(phrase)용."""
    pairs = [
        (v.casefold(), c)
        for v, c in variant_to_canonical().items()
        if " " in v
    ]
    return sorted(pairs, key=lambda t: -len(t[0]))


def apply_aliases_token(token: str) -> str:
    """단일 토큰 별칭 치환 (casefold 매칭, 없으면 원본)."""
    return variant_to_canonical().get(token.casefold(), token)


def apply_aliases_phrase(text: str) -> str:
    """다단어 variant를 최장 일치로 치환. 단어 단위 치환 전 1회 적용용."""
    low = text.casefold()
    for v, c in _phrase_variants():
        if v in low:
            # casefold 위치 기반 단순 치환(부품명은 짧아 충돌 위험 낮음).
            idx = low.find(v)
            text = text[:idx] + c + text[idx + len(v):]
            low = text.casefold()
    return text


__all__ = [
    "PART_ALIASES_PATH",
    "apply_aliases_phrase",
    "apply_aliases_token",
    "variant_to_canonical",
]
