"""Phase 3/4 — L2 Orchestrator (HITL 경로) 테스트.

retrieval 백엔드는 fake 주입(네트워크/임베딩 없음). walk_subtree + tool_call_log는
실제 SQLite. 검증: propose_candidates(event RRF 융합/dedup/reflection, **트리 전개 없음**)
+ expand_confirmed(확정 닻만 walk, 발번대기 제외).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.agent.confirm.models import ConfirmedAnchor
from src.agent.intent.models import ChangeIntent
from src.agent.orchestrator import expand_confirmed, propose_candidates
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, ChangeLine, SourceFile, ToolCallLog
from src.db.retrieve import EventHit


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class FakeBackend:
    def __init__(
        self,
        change_results=None,
        *,
        hybrid=None,
        lookup=None,
        similar=None,
        events=None,
        lines=None,
    ):
        self.change_results = change_results or {}
        self.hybrid = hybrid or []
        self.lookup = lookup or []
        self.similar = similar or []
        self.events = events or {}   # query -> list[EventHit]
        self.lines = lines or {}     # event_id -> list[ChangeLine]

    def search_changes(self, query, *, top_k=10, region=None):
        return list(self.change_results.get(query, []))[:top_k]

    def hybrid_search(self, query, *, top_k=10, region=None):
        return list(self.hybrid)[:top_k]

    def lookup_by_attribute(self, *, top_k=20, **kw):
        return list(self.lookup)[:top_k]

    def find_similar_changes(self, seed_pno, *, top_k=5, region=None):
        return list(self.similar)[:top_k]

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self.events.get(query, []))[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self.lines.get(event_id, []))


def _ev(event_id: int, reason: str) -> EventHit:
    return EventHit(
        event_id=event_id,
        base_model="A",
        new_model="B",
        event="Change",
        change_log="cp",
        change_reason=reason,
        source_ref="f.xlsx",
        file_id=1,
    )


def _ln(event_id: int, pno: str) -> ChangeLine:
    return ChangeLine(event_id=event_id, new_pno=pno, changepoint="cp", classification="Change")


def _intent(queries: list[str]) -> ChangeIntent:
    return ChangeIntent(
        raw_text="패킹 변경", rewritten_queries=queries, confidence=0.6, source="regex"
    )


# ── HITL: propose_candidates (정지, 트리 전개 없음) ────────────────


def test_propose_returns_candidate_sets_and_no_tree(session):
    backend = FakeBackend(
        events={"q1": [_ev(1, "BLDC 기능 추가"), _ev(2, "원가 절감")]},
        lines={1: [_ln(1, "P1"), _ln(1, "P2")], 2: [_ln(2, "P3")]},
    )
    res = propose_candidates(_intent(["q1"]), session=session, backend=backend, min_candidates=1)

    # event_id RRF: 한 쿼리에 ev1(rank0) > ev2(rank1).
    assert [c.event.event_id for c in res.candidates] == [1, 2]
    # 후보 세트 = 사유별 부품 묶음.
    assert {l.new_pno for c in res.candidates for l in c.lines} == {"P1", "P2", "P3"}
    # 트리 전개 없음 — walk_subtree 호출 부재(절대원칙 #4).
    assert all(r.tool_name != "walk_subtree" for r in res.trace)
    assert {r.tool_name for r in res.trace} == {"search_events", "lookup_lines_by_event"}


def test_propose_logs_tools(session):
    backend = FakeBackend(events={"q1": [_ev(1, "r1")]}, lines={1: [_ln(1, "P1")]})
    res = propose_candidates(_intent(["q1"]), session=session, backend=backend, min_candidates=1)
    logged = session.execute(select(func.count()).select_from(ToolCallLog)).scalar_one()
    assert logged == res.tool_calls
    names = {r.tool_name for r in session.execute(select(ToolCallLog)).scalars().all()}
    assert names == {"search_events", "lookup_lines_by_event"}


def test_propose_write_log_false_skips_logging(session):
    # write_log=False (검수 read-only): trace는 반환하되 tool_call_log에는 기록하지 않음.
    backend = FakeBackend(events={"q1": [_ev(1, "r1")]}, lines={1: [_ln(1, "P1")]})
    res = propose_candidates(
        _intent(["q1"]), session=session, backend=backend, min_candidates=1, write_log=False
    )
    logged = session.execute(select(func.count()).select_from(ToolCallLog)).scalar_one()
    assert logged == 0
    assert res.trace  # 트레이스 자체는 반환됨(화면 표시용)


def test_propose_dedup_by_reason(session):
    # 같은 (change_log+change_reason) 이벤트가 여러 개면 dedup_by_reason=True로 1개만.
    backend = FakeBackend(
        events={"q1": [_ev(1, "외관 변경"), _ev(2, "외관 변경"), _ev(3, "에너지 변경")]},
        lines={1: [_ln(1, "P1")], 2: [_ln(2, "P2")], 3: [_ln(3, "P3")]},
    )
    res = propose_candidates(
        _intent(["q1"]), session=session, backend=backend, min_candidates=1, dedup_by_reason=True
    )
    assert [c.event.change_reason for c in res.candidates] == ["외관 변경", "에너지 변경"]
    # 기본(dedup off)은 중복 유지
    res2 = propose_candidates(_intent(["q1"]), session=session, backend=backend, min_candidates=1)
    assert [c.event.change_reason for c in res2.candidates] == ["외관 변경", "외관 변경", "에너지 변경"]


def test_propose_reflection_when_weak(session):
    # q1은 1건뿐(<min). reflection(raw_text)으로 이벤트 보강.
    backend = FakeBackend(
        events={"q1": [_ev(1, "r1")], "패킹 변경": [_ev(2, "r2"), _ev(3, "r3")]},
        lines={1: [_ln(1, "P1")], 2: [_ln(2, "P2")], 3: [_ln(3, "P3")]},
    )
    res = propose_candidates(
        _intent(["q1"]), session=session, backend=backend, min_candidates=3, max_reflection=1
    )
    assert res.reflections == 1
    assert {c.event.event_id for c in res.candidates} == {1, 2, 3}


# ── HITL: expand_confirmed (확정 닻만 전개) ───────────────────────


def test_expand_confirmed_walks_only_anchors(session):
    sf = SourceFile(file_name="bom.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    for p, c in [("A", "X"), ("A", "Y"), ("B", "Z")]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno=p, child_pno=c))
    session.commit()

    anchors = [ConfirmedAnchor(part_no_new="A", change_point="부품 변경")]
    res = expand_confirmed(anchors, session=session, bom_repo=EdgeBomRepository(session))
    # A의 하위만 — B(미확정)의 Z는 없음.
    assert {n.node.pno for n in res.tree} == {"X", "Y"}
    assert all(n.anchor_pno == "A" for n in res.tree)
    assert any(r.tool_name == "walk_subtree" for r in res.trace)


def test_expand_skips_placeholder_anchor(session):
    anchors = [ConfirmedAnchor(part_no_new="<발번대기>", is_placeholder=True)]
    res = expand_confirmed(anchors, session=session, bom_repo=EdgeBomRepository(session))
    assert res.tree == []
    # 번호 없는 닻은 walk 호출조차 안 함.
    assert all(r.tool_name != "walk_subtree" for r in res.trace)
