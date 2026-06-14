"""Phase 2 — 공용 매칭 자(matching/scoring) 테스트.

trgm 근사 패리티(라이브 PG 가용 시 word_similarity SQL과 ±0.05, 미가용 시 skip),
score_line 우선순위(pno > canon > type), canonicalize_part_name, LLM import 0 단언.
"""

from __future__ import annotations

import inspect

import pytest

import src.agent.matching.scoring as scoring_mod
from src.agent.matching.scoring import (
    MatchThresholds,
    ScopeIndex,
    ScopeNode,
    trgm_word_similarity,
)
from src.preprocess.normalize import canonicalize_part_name


def _node(pno: str, name: str, ptype: str | None = None, depth: int = 1) -> ScopeNode:
    return ScopeNode(
        pno=pno,
        part_name=name,
        part_name_canon=canonicalize_part_name(name),
        part_type=ptype,
        depth=depth,
        path=f"/{pno}/",
    )


# ── canonicalize_part_name ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Assmebly 하위", "Assembly 하위"),          # token_canon 오타 흡수
        ("HEATER Assy", "HEATER Assembly"),          # 변이 정규화
        ("Cover Assembly,Rear", "Cover Assembly Rear"),  # 구분문자 → 공백
        ("WSED7667M Door", "Door"),                  # 모델코드 토큰 제거
        ("Bracket 137mm", "Bracket"),                # 치수 토큰 제거
        ("", ""),
    ],
)
def test_canonicalize_part_name(raw, expected):
    assert canonicalize_part_name(raw) == expected


# ── trgm 근사 단독 성질 ─────────────────────────────────────────────────────


def test_trgm_exact_word_is_one():
    assert trgm_word_similarity("heater", "HEATER Assembly") == pytest.approx(1.0)


def test_trgm_empty_inputs_zero():
    assert trgm_word_similarity("", "abc") == 0.0
    assert trgm_word_similarity("abc", "") == 0.0


def test_trgm_unrelated_low():
    assert trgm_word_similarity("브래킷", "ZZZZZZ") < 0.2


# ── 라이브 PG 패리티 (±0.05) ────────────────────────────────────────────────

_PARITY_FIXTURES = [
    ("heater", "heater assembly"),
    ("bracket", "bracket mounting"),
    ("히터 어셈블리", canonicalize_part_name("HEATER Assmebly")),
    ("브래킷", "bracket mounting"),
    ("cover rear", "cover assembly rear"),
    ("door handle", "door handle assembly"),
    ("tray", "tray 법랑"),
    ("assembly", "assembly 구성"),
]


def _live_pg_session_or_skip():
    from sqlalchemy import text as _t

    try:
        from src.ui.agent_client import load_agent_env

        load_agent_env()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.db.engine import make_engine, session_factory

        s = session_factory(make_engine())()
        s.execute(_t("SELECT 1"))
        s.execute(_t("SELECT word_similarity('a', 'a')"))
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"라이브 Postgres/pg_trgm 미가용 — 패리티 테스트 skip: {exc}")


def test_trgm_parity_with_live_pg():
    from sqlalchemy import text as _t

    s = _live_pg_session_or_skip()
    try:
        for needle, hay in _PARITY_FIXTURES:
            pg = float(
                s.execute(
                    _t("SELECT word_similarity(:n, :h)"), {"n": needle, "h": hay}
                ).scalar_one()
            )
            approx = trgm_word_similarity(needle, hay)
            assert abs(approx - pg) <= 0.05, (needle, hay, approx, pg)
    finally:
        s.close()


# ── ScopeIndex.score_line 우선순위 (pno > canon > type) ─────────────────────


@pytest.fixture()
def index() -> ScopeIndex:
    nodes = [
        _node("P100", "HEATER Assembly", "Ass'y"),
        _node("P200", "BRACKET MOUNTING", "PD Part", depth=2),
    ]
    return ScopeIndex(nodes, MatchThresholds())


def test_score_line_pno_exact_wins(index):
    sc = index.score_line(part_name="전혀 다른 이름", base_pno="P100")
    assert sc.kind == "pno_exact" and sc.best == 1.0
    assert sc.best_node is not None and sc.best_node.pno == "P100"


def test_score_line_canon_trgm(index):
    sc = index.score_line(part_name="HEATER Assy")  # canon → HEATER Assembly
    assert sc.kind == "canon_trgm"
    assert sc.best >= 0.75
    assert sc.best_node is not None and sc.best_node.pno == "P100"


def test_score_line_type_match_fallback(index):
    sc = index.score_line(part_name="ZZZZZZ", part_type="PD Part")
    assert sc.kind == "type_match"
    assert sc.best == pytest.approx(MatchThresholds().type_score)
    assert sc.best_node is None


def test_score_line_none(index):
    sc = index.score_line(part_name="ZZZZZZ", part_type="없는 군")
    assert sc.kind == "none"


def test_event_structure_score_coverage(index):
    lines = [
        {"part_name": None, "base_pno": "P100", "new_pno": None, "part_type": None},
        {"part_name": "ZZZZZZ", "base_pno": None, "new_pno": None, "part_type": None},
    ]
    # 1/2 라인이 tau_lo 이상(=1.0) → cov 0.5, max 1.0 → 0.6*0.5 + 0.4*1.0 = 0.7
    assert index.event_structure_score(lines) == pytest.approx(0.7)


def test_event_structure_score_empty_lines(index):
    assert index.event_structure_score([]) == 0.0


# ── MatchThresholds env 게이트 ──────────────────────────────────────────────


def test_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("MATCH_TAU_HI", "0.9")
    monkeypatch.setenv("MATCH_EPS", "0.1")
    th = MatchThresholds.from_env()
    assert th.tau_hi == 0.9 and th.eps == 0.1
    assert th.tau_lo == 0.45  # 미설정은 기본값


def test_thresholds_from_env_bad_value(monkeypatch):
    monkeypatch.setenv("MATCH_TAU_HI", "not-a-float")
    assert MatchThresholds.from_env().tau_hi == 0.75


# ── 결정론 보장 — LLM import 0회 ────────────────────────────────────────────


def test_scoring_module_imports_no_llm():
    src_text = inspect.getsource(scoring_mod).lower()
    assert "agent.llm" not in src_text
    assert "ollama" not in src_text
    assert "import requests" not in src_text
