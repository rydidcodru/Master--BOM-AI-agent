"""HITL 게이트 UI 어댑터 테스트 (propose_candidate_sets / confirm_and_expand).

fake 백엔드 + SQLite. 검증:
  - propose: 후보 부품 세트(사유 묶음 + 라인) 반환, 트리 키 없음(정지).
  - confirm+expand: 채택 닻만 전개, agent_feedback 기록, 문서 검증 통과.
  - 신규 P/No 무생성: 미입력 채택 → <발번대기> placeholder, 트리 전개 제외.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import AgentFeedback, BomEdge, ChangeLine, DevPartMaster, SourceFile
from src.db.retrieve import EventHit
from src.ui import agent_client


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class _EventFake:
    def __init__(self, events, lines):
        self._events = events
        self._lines = lines

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self._events)[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self._lines.get(event_id, []))

    def search_changes(self, query, *, top_k=10, region=None):
        return []

    def hybrid_search(self, query, *, top_k=10, region=None):
        return []

    def lookup_by_attribute(self, *, top_k=20, **kw):
        return []

    def find_similar_changes(self, seed_pno, *, top_k=5, region=None):
        return []


def _ev(eid, reason):
    return EventHit(
        event_id=eid, base_model="A", new_model="B", event="Change",
        change_log="모터 AC→BLDC", change_reason=reason, source_ref="f.xlsx", file_id=1,
    )


def _ln(eid, base, new):
    return ChangeLine(
        event_id=eid, base_pno=base, new_pno=new, part_name=f"name-{new}",
        part_type="Assy", changepoint="cp", classification="Change",
        source_ref="f.xlsx/sheet/1",
    )


def test_propose_returns_candidate_sets_no_tree(session):
    backend = _EventFake(
        [_ev(1, "BLDC 기능 추가")],
        {1: [_ln(1, "BASE1", "A"), _ln(1, "BASE2", "B")]},
    )
    out = agent_client.propose_candidate_sets(
        change_detail="AC 모터를 BLDC로 변경",
        change_reason="BLDC 기능 추가",
        base_model="WSED",
        session=session,
        backend=backend,
    )
    assert "tree" not in out  # 정지 — 트리 전개 없음
    assert out["base_model"] == "WSED"
    assert len(out["candidates"]) == 1
    cand = out["candidates"][0]
    assert cand["change_reason"] == "BLDC 기능 추가"
    assert {ln["part_no_new_suggested"] for ln in cand["lines"]} == {"A", "B"}
    # 운반 필드 보존(confirm 복원용).
    assert all(ln["event_id"] == 1 for ln in cand["lines"])
    assert "intent" in out


def test_confirm_expands_only_accepted(session):
    sf = SourceFile(file_name="f.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    for i, pno in enumerate(("A", "X", "Y")):
        session.add(DevPartMaster(
            file_id=sf.file_id, form_id="changing_parts_list_96",
            part_no_new=pno, sheet_name="s", source_row=i + 1,  # uq_dpm_source
        ))
    for p, c in [("A", "X"), ("A", "Y")]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno=p, child_pno=c))
    session.commit()

    intent = {"raw_text": "모터 변경", "change_attribute": "재질", "confidence": 0.6, "source": "regex"}
    selections = [
        {"accept": True, "part_no_base": "BASE1", "part_no_new_suggested": "A",
         "change_point": "부품 변경", "new_pno_input": None, "classification": "Change",
         "source_ref": "f.xlsx/s/1", "event_id": 1},
        {"accept": False, "part_no_base": "BASE2", "part_no_new_suggested": "B",
         "change_point": None, "new_pno_input": None, "classification": "Change",
         "source_ref": "f.xlsx/s/2", "event_id": 1},
    ]
    res = agent_client.confirm_and_expand(
        intent=intent, selections=selections, session=session,
        bom_repo=EdgeBomRepository(session),
    )
    # 채택 A만 닻 → A의 하위(X,Y)만 전개. 거절 B 없음.
    assert {a["part_no_new"] for a in res["anchors"]} == {"A"}
    assert {n["pno"] for n in res["tree"]} == {"X", "Y"}
    assert res["violations"] == []
    # 채택/거절 모두 agent_feedback 기록.
    fbs = session.execute(select(AgentFeedback)).scalars().all()
    assert {f.decision for f in fbs} == {"accept", "reject"}


def test_confirm_blank_new_pno_is_placeholder(session):
    intent = {"raw_text": "신규", "confidence": 0.6, "source": "regex"}
    selections = [
        {"accept": True, "part_no_base": None, "part_no_new_suggested": None,
         "change_point": "부품 추가", "new_pno_input": None, "classification": "New",
         "source_ref": "ppt/slide3", "event_id": 1},
    ]
    res = agent_client.confirm_and_expand(
        intent=intent, selections=selections, session=session,
        bom_repo=EdgeBomRepository(session),
    )
    assert res["anchors"][0]["is_placeholder"] is True
    assert res["anchors"][0]["part_no_new"] == "<발번대기>"
    assert res["tree"] == []  # 발번대기 닻은 번호 없어 전개 제외
    assert res["violations"] == []  # NEW=<발번대기> placeholder, 임의 품번 무생성


def test_confirm_user_pno_input_beats_suggested(session):
    intent = {"raw_text": "변경", "confidence": 0.6, "source": "regex"}
    selections = [
        {"accept": True, "part_no_base": "OLD", "part_no_new_suggested": "SUGG",
         "change_point": "부품 변경", "new_pno_input": "USER999", "classification": "Change",
         "source_ref": "f/x/1", "event_id": 1},
    ]
    res = agent_client.confirm_and_expand(
        intent=intent, selections=selections, session=session,
        bom_repo=EdgeBomRepository(session),
    )
    assert res["anchors"][0]["part_no_new"] == "USER999"
    assert res["anchors"][0]["is_placeholder"] is False
