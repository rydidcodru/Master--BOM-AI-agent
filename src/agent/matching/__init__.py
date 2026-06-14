"""①.5 구조 스코프 부스트 ↔ ②.5 후보-BOM 매핑 공용 매칭 자(scoring).

순수 결정론 — LLM/DB import 0회.
"""

from src.agent.matching.scoring import (
    LineScore,
    MatchThresholds,
    ScopeIndex,
    ScopeNode,
    trgm_word_similarity,
)

__all__ = [
    "LineScore",
    "MatchThresholds",
    "ScopeIndex",
    "ScopeNode",
    "trgm_word_similarity",
]
