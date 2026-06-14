"""Phase 4 — HITL 확정 게이트 테스트 (SQLite).

검증:
  - 채택 → 닻(회수 기존값/사용자입력/<발번대기> 규칙), 거절 → 제외.
  - 시스템 무생성: 입력·제안 모두 없으면 반드시 <발번대기>.
  - 사용자 입력 > 검색 회수 우선순위.
  - 채택/거절 모두 agent_feedback에 기록(is_anchor 구분).
  - change_point 도메인 검증(집합 밖이면 ValidationError).
  - infer_change_point 결정론 매핑.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from src.agent.confirm import (
    CandidateSelection,
    confirm_candidates,
    infer_change_point,
    resolve_new_pno,
)
from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import AgentFeedback


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


# ── resolve_new_pno (시스템 무생성) ───────────────────────────


def test_resolve_blank_falls_to_placeholder():
    sel = CandidateSelection(decision="accept", change_point="부품 추가")
    pno, is_ph = resolve_new_pno(sel)
    assert pno == PNO_PLACEHOLDER
    assert is_ph is True


def test_resolve_uses_suggested_when_no_input():
    sel = CandidateSelection(part_no_new_suggested="AGG74419321", decision="accept")
    pno, is_ph = resolve_new_pno(sel)
    assert pno == "AGG74419321"
    assert is_ph is False


def test_resolve_user_input_beats_suggested():
    sel = CandidateSelection(
        part_no_new_suggested="OLD123", new_pno_input="NEW999", decision="accept"
    )
    pno, is_ph = resolve_new_pno(sel)
    assert pno == "NEW999"
    assert is_ph is False


# ── confirm_candidates ────────────────────────────────────────


def test_accept_builds_anchor(session):
    sel = CandidateSelection(
        part_no_new_suggested="AGG74419321", decision="accept", change_point="부품 변경"
    )
    anchors = confirm_candidates(session, session_id="s1", selections=[sel])
    assert len(anchors) == 1
    assert anchors[0].part_no_new == "AGG74419321"
    assert anchors[0].change_point == "부품 변경"
    assert anchors[0].is_placeholder is False


def test_blank_accept_falls_to_placeholder_anchor(session):
    sel = CandidateSelection(decision="accept", change_point="부품 추가")
    anchors = confirm_candidates(session, session_id="s1", selections=[sel])
    assert anchors[0].part_no_new == PNO_PLACEHOLDER
    assert anchors[0].is_placeholder is True


def test_reject_excluded_but_recorded(session):
    sels = [
        CandidateSelection(part_no_new_suggested="A", decision="accept", change_point="부품 변경"),
        CandidateSelection(part_no_new_suggested="B", decision="reject"),
    ]
    anchors = confirm_candidates(session, session_id="s1", selections=sels)
    assert {a.part_no_new for a in anchors} == {"A"}  # 거절 B 제외

    fbs = session.execute(select(AgentFeedback)).scalars().all()
    assert len(fbs) == 2  # 채택/거절 모두 기록
    by_part = {f.part_no: f for f in fbs}
    assert by_part["A"].decision == "accept" and by_part["A"].is_anchor is True
    assert by_part["B"].decision == "reject" and by_part["B"].is_anchor is False


def test_feedback_count_matches(session):
    sels = [
        CandidateSelection(part_no_new_suggested=f"P{i}", decision="accept", change_point="부품 변경")
        for i in range(3)
    ]
    confirm_candidates(session, session_id="s1", selections=sels)
    n = session.execute(select(func.count()).select_from(AgentFeedback)).scalar_one()
    assert n == 3


def test_record_feedback_can_be_disabled(session):
    sel = CandidateSelection(part_no_new_suggested="A", decision="accept")
    confirm_candidates(session, session_id="s1", selections=[sel], record_feedback=False)
    n = session.execute(select(func.count()).select_from(AgentFeedback)).scalar_one()
    assert n == 0


# ── 도메인 검증 ───────────────────────────────────────────────


def test_change_point_must_be_in_set():
    with pytest.raises(ValidationError):
        CandidateSelection(decision="accept", change_point="아무거나")


def test_change_point_none_allowed():
    sel = CandidateSelection(decision="accept", change_point=None)
    assert sel.change_point is None


@pytest.mark.parametrize(
    "classification,expected",
    [
        ("New", "부품 추가"),
        ("신규", "부품 추가"),
        ("Change", "부품 변경"),
        ("Delete", "부품 삭제"),
        ("unknown", None),
        (None, None),
    ],
)
def test_infer_change_point(classification, expected):
    assert infer_change_point(classification) == expected
