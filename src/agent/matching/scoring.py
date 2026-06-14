"""①.5 ↔ ②.5 공용 매칭 자 — pg_trgm 근사 + 모듈 하위트리(S) 인덱스.

``trgm_word_similarity``는 pg_trgm ``word_similarity()``의 순수 파이썬 근사
(DB 왕복 없음, 패리티 테스트로 라이브 PG와 ±0.05 이내 검증). ``ScopeIndex``는
walk_subtree 결과(모듈 하위트리 S)의 인덱스로, walk 직후 1회 생성해 ①.5 구조
부스트와 ②.5 후보-BOM 매핑이 **같은 인스턴스를 공유**한다(이중 생성 금지).

순수 결정론 — LLM import 0회 (단언 테스트로 핀).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from src.preprocess.normalize import canonicalize_part_name


def _words(s: str) -> list[str]:
    """pg_trgm 어휘화 근사 — 영숫자/한글 연속을 단어로, casefold."""
    return re.findall(r"[0-9a-z가-힣]+", str(s or "").casefold())


def _trigrams(w: str) -> set[str]:
    w = f"  {w} "  # pg_trgm 패딩 규약(앞2·뒤1)
    return {w[i : i + 3] for i in range(len(w) - 2)}


def _word_trigrams(text: str) -> list[set[str]]:
    """단어별 트라이그램 집합 리스트 (캐싱용 — needle/hay 공통)."""
    return [_trigrams(w) for w in _words(text)]


def _wts_from_grams(nw_grams: list[set[str]], hw_grams: list[set[str]]) -> float:
    """단어 트라이그램 집합으로 word_similarity 근사 (집합 사전계산 재사용)."""
    if not nw_grams or not hw_grams:
        return 0.0
    tn: set[str] = set().union(*nw_grams)
    nlen = len(nw_grams)
    best = 0.0
    for i in range(len(hw_grams)):
        for j in range(i + 1, min(i + nlen + 2, len(hw_grams)) + 1):
            th: set[str] = set().union(*hw_grams[i:j])
            inter = len(tn & th)
            if inter:
                best = max(best, inter / len(tn | th))
    return best


def trgm_word_similarity(needle: str, hay: str) -> float:
    """pg_trgm word_similarity 근사: needle 트라이그램 vs hay의 연속 단어창 최대 자카드.

    순수 결정론(DB 왕복 없음). 패리티 테스트로 DB 값과 ±0.05 이내 검증.
    """
    return _wts_from_grams(_word_trigrams(needle), _word_trigrams(hay))


@dataclass(frozen=True)
class MatchThresholds:
    """매칭 임계 — env 게이트(MATCH_*)로 코드 변경 없이 튜닝."""

    tau_hi: float = 0.75
    tau_lo: float = 0.45
    eps: float = 0.08
    theta_coherence: float = 0.50
    type_score: float = 0.20

    @classmethod
    def from_env(cls) -> "MatchThresholds":
        def g(k: str, d: float) -> float:
            try:
                return float(os.getenv(k, d))
            except ValueError:
                return d

        return cls(
            g("MATCH_TAU_HI", 0.75),
            g("MATCH_TAU_LO", 0.45),
            g("MATCH_EPS", 0.08),
            g("MATCH_THETA_COHERENCE", 0.50),
            g("MATCH_TYPE_SCORE", 0.20),
        )


@dataclass(frozen=True)
class ScopeNode:
    """walk_subtree 결과의 최소 사영 (S의 노드 1개)."""

    pno: str
    part_name: str
    part_name_canon: str
    part_type: str | None
    depth: int
    path: str


@dataclass
class LineScore:
    """과거 change_line 1건 vs S 매칭 점수."""

    best_node: ScopeNode | None
    best: float = 0.0
    second: float = 0.0
    kind: str = "none"  # pno_exact | canon_trgm | type_match | none
    evidence: list = field(default_factory=list)


@dataclass
class ScopeMeta:
    """①.5 스코프 구축 관측 정보 (P7.4) — env_panel 표시·디버깅용. 점수에 영향 없음."""

    anchors: list = field(default_factory=list)  # [(name, score)]
    fail_reason: str | None = None  # "file_not_found" | "module_no_match" | "empty_walk"
    best_no_match_score: float | None = None
    ambiguous_anchor: bool = False


class ScopeIndex:
    """모듈 하위트리 S의 인덱스. walk 직후 1회 생성해 ①.5 부스트와 ②.5 매핑이 공유.

    P7.4: 노드별 part_name_canon 트라이그램을 ``__init__``에서 1회 사전계산해
    ``score_line``/``top_nodes``가 재사용한다(O(라인×노드) 재계산 제거 — 점수 동일).
    ``meta``는 구축 관측 정보(점수 무관).
    """

    def __init__(
        self, nodes: list[ScopeNode], th: MatchThresholds, meta: ScopeMeta | None = None
    ) -> None:
        self.nodes, self.th = nodes, th
        self.by_pno = {n.pno: n for n in nodes if n.pno}
        self.types = {n.part_type for n in nodes if n.part_type}
        self.meta = meta or ScopeMeta()
        # 노드 canon 트라이그램 사전계산(캐시).
        self._node_grams = [_word_trigrams(n.part_name_canon) for n in nodes]

    def _ranked(self, canon: str) -> list[tuple[float, ScopeNode]]:
        ng = _word_trigrams(canon)
        rank = [
            (_wts_from_grams(ng, self._node_grams[i]), n)
            for i, n in enumerate(self.nodes)
        ]
        rank.sort(key=lambda t: -t[0])
        return rank

    def score_line(
        self,
        *,
        part_name: str | None = None,
        base_pno: str | None = None,
        new_pno: str | None = None,
        part_type: str | None = None,
        **_ignored: Any,
    ) -> LineScore:
        """우선순위: pno 정확일치 > 부품명 canon trgm > part_type 멤버십 > none."""
        for p in (base_pno, new_pno):
            if p and p in self.by_pno:
                return LineScore(self.by_pno[p], 1.0, 0.0, "pno_exact", [f"pno_exact:{p}"])
        canon = canonicalize_part_name(part_name or "")
        rank = self._ranked(canon)
        best, node = rank[0] if rank else (0.0, None)
        second = rank[1][0] if len(rank) > 1 else 0.0
        if best >= self.th.type_score:
            return LineScore(node, best, second, "canon_trgm", [f"canon_trgm:{canon}~{best:.2f}"])
        if part_type and part_type in self.types:
            return LineScore(None, self.th.type_score, 0.0, "type_match", [f"type:{part_type}"])
        return LineScore(None, best, second, "none", [])

    def event_structure_score(self, lines: list[dict]) -> float:
        """이벤트(라인 세트) 단위 구조 정합 — 0.6×커버리지 + 0.4×최고점."""
        s = [self.score_line(**line).best for line in lines] or [0.0]
        cov = sum(1 for x in s if x >= self.th.tau_lo) / len(s)
        return 0.6 * cov + 0.4 * max(s)

    def top_nodes(self, part_name: str | None, k: int = 3) -> list[tuple[float, ScopeNode]]:
        """부품명 canon trgm 상위 k개 (점수 desc) — ②.5 ambiguous 후보 노출용."""
        return self._ranked(canonicalize_part_name(part_name or ""))[:k]


__all__ = [
    "LineScore",
    "MatchThresholds",
    "ScopeIndex",
    "ScopeMeta",
    "ScopeNode",
    "trgm_word_similarity",
]
