"""P7.3 — dedup 라인 합집합 보존 + 신뢰 신호(retrieval_meta) 테스트.

동일 사유 2이벤트(라인 세트 A/B 상이) 병합 후 후보 라인 = A∪B, 동일 키 1회, struct MAX.
"""

from __future__ import annotations

import pytest

from src.agent.intent.models import ChangeIntent
from src.agent.orchestrator import propose_candidates
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import ChangeLine
from src.db.retrieve import EventHit


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class FakeBackend:
    def __init__(self, events, lines):
        self.events, self.lines = events, lines

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self.events.get(query, []))[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self.lines.get(event_id, []))


def _ev(eid, reason, dense=0.5):
    h = EventHit(event_id=eid, base_model="A", new_model="B", event="Change",
                 change_log="외관 변경", change_reason=reason, source_ref="f.xlsx", file_id=1)
    h.score_semantic = dense
    return h


def _ln(eid, pno, cp="외관 변경"):
    return ChangeLine(event_id=eid, base_pno=pno, part_name=pno, changepoint=cp,
                      classification="Change")


def test_dedup_preserves_line_union(session):
    # E1, E2 동일 사유. E1 라인 {P1,P2}, E2 라인 {P2(중복키),P3}. 병합 후 {P1,P2,P3}.
    backend = FakeBackend(
        events={"q": [_ev(1, "같은 사유"), _ev(2, "같은 사유")]},
        lines={1: [_ln(1, "P1"), _ln(1, "P2")], 2: [_ln(2, "P2"), _ln(2, "P3")]},
    )
    intent = ChangeIntent(raw_text="x", rewritten_queries=["q"], confidence=1.0, source="ppt")
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             dedup_by_reason=True, min_candidates=1, max_reflection=0)
    assert len(res.candidates) == 1  # 같은 사유 → 1개 후보로 병합
    cand = res.candidates[0]
    pnos = {ln.base_pno for ln in cand.lines}
    assert pnos == {"P1", "P2", "P3"}  # 합집합 보존
    # 동일 키(P2/외관 변경/Change) 1회만
    assert sum(1 for ln in cand.lines if ln.base_pno == "P2") == 1
    assert 2 in cand.merged_event_ids


def test_no_dedup_keeps_separate(session):
    backend = FakeBackend(
        events={"q": [_ev(1, "사유1"), _ev(2, "사유2")]},
        lines={1: [_ln(1, "P1")], 2: [_ln(2, "P2")]},
    )
    intent = ChangeIntent(raw_text="x", rewritten_queries=["q"], confidence=1.0, source="ppt")
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             dedup_by_reason=False, min_candidates=1, max_reflection=0)
    assert len(res.candidates) == 2


# ── retrieval_meta / low_confidence (P7.2) ──────────────────────────────────


def test_retrieval_meta_low_confidence(session, monkeypatch):
    monkeypatch.setenv("SEARCH_LOW_CONF", "0.30")
    backend = FakeBackend(events={"q": [_ev(1, "r", dense=0.10)]}, lines={1: [_ln(1, "P1")]})
    intent = ChangeIntent(raw_text="x", rewritten_queries=["q"], confidence=1.0, source="ppt")
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             min_candidates=1, max_reflection=0)
    assert res.retrieval_meta["low_confidence"] is True
    assert res.retrieval_meta["best_per_channel"]["dense"] == pytest.approx(0.10)


def test_retrieval_meta_high_confidence(session, monkeypatch):
    monkeypatch.setenv("SEARCH_LOW_CONF", "0.30")
    backend = FakeBackend(events={"q": [_ev(1, "r", dense=0.80)]}, lines={1: [_ln(1, "P1")]})
    intent = ChangeIntent(raw_text="x", rewritten_queries=["q"], confidence=1.0, source="ppt")
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             min_candidates=1, max_reflection=0)
    assert res.retrieval_meta["low_confidence"] is False


def test_rescue_search_gated_off_by_default(session, monkeypatch):
    monkeypatch.delenv("RESCUE_SEARCH", raising=False)
    monkeypatch.setenv("SEARCH_LOW_CONF", "0.30")
    backend = FakeBackend(events={"q": [_ev(1, "r", dense=0.10)]}, lines={1: [_ln(1, "P1")]})
    intent = ChangeIntent(raw_text="패킹 변경", rewritten_queries=["q"], confidence=1.0, source="ppt")
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             min_candidates=1, max_reflection=0)
    assert all(not c.rescued for c in res.candidates)  # 게이트 off → 구제 없음


def test_rescue_search_on_adds_rescued(session, monkeypatch):
    monkeypatch.setenv("RESCUE_SEARCH", "1")
    monkeypatch.setenv("SEARCH_LOW_CONF", "0.30")
    # 본 쿼리 "q"는 저신뢰 E1만. 구제 쿼리(target+raw)는 E9를 추가로 회수.
    rescue_reason = "패킹 변경"
    backend = FakeBackend(
        events={"q": [_ev(1, "r", dense=0.10)], f"패킹 {rescue_reason}": [_ev(9, "r2", dense=0.9)]},
        lines={1: [_ln(1, "P1")], 9: [_ln(9, "P9")]},
    )
    from src.agent.intent.models import ChangeSlot
    intent = ChangeIntent(raw_text=rescue_reason, rewritten_queries=["q"], confidence=1.0,
                          source="ppt", derived_change_slots=[ChangeSlot(target="패킹", action="변경")])
    res = propose_candidates(intent, session=session, backend=backend, write_log=False,
                             min_candidates=1, max_reflection=0)
    rescued = [c for c in res.candidates if c.rescued]
    assert any(c.event.event_id == 9 for c in rescued)  # 구제로 E9 회수
