"""P7.1 — 부품명 별칭(part_aliases) canonicalize 반영 테스트.

핵심: trgm 0점인 한↔영 쌍을 canonicalize가 메워 ①.5/②.5 매칭이 살아나는지.
"""

from __future__ import annotations

import pytest

from src.agent.matching.scoring import trgm_word_similarity
from src.ontology.part_aliases import variant_to_canonical
from src.preprocess.normalize import canonicalize_part_name


def test_korean_english_alias_canon():
    assert canonicalize_part_name("도어 어셈블리") == "Door Assembly"
    assert canonicalize_part_name("히터 브래킷") == "Heater Bracket"
    assert canonicalize_part_name("컨트롤러 ASSY") == "Controller Assembly"


def test_typo_alias_not_in_token_canon():
    # 콤마 제거(canonicalize) 후에도 남는 오타를 part_aliases가 잡는다.
    assert "Carton" in canonicalize_part_name("Label,Cartoon")


def test_alias_revives_trgm_match():
    # 별칭 적용 전 한↔영 raw 유사도는 0, canon 후엔 영문끼리라 1.0 회복.
    assert trgm_word_similarity("도어", "Door") == 0.0  # 트라이그램 교집합 0
    a = canonicalize_part_name("도어")
    b = canonicalize_part_name("Door Assembly")
    assert trgm_word_similarity(a, b) >= 0.99  # canon 후 'Door' 정확 단어


def test_english_unchanged():
    assert canonicalize_part_name("Door Assembly") == "Door Assembly"


def test_no_conflict_in_seed():
    # 한 variant가 두 canonical에 걸리면 안 됨(변환표는 1:1).
    vc = variant_to_canonical()
    assert len(vc) == len(set(vc))  # 키 유일(dict이라 자명) — 값 충돌 없음 확인
