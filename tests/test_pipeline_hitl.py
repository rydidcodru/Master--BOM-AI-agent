"""Phase 4 — HITL 2단계 파이프라인 (헤드리스 propose-only → 확정 후 분석) 테스트.

검증:
  - propose_analysis: structurize → 후보 세트 반환, **트리 전개 없음**(헤드리스 정지).
  - analyze_confirmed: 확정 닻 → 하위 전개(walk_subtree) + 판정(L3) + 문서(L4).
  - <발번대기> 닻은 트리 전개 제외.
"""

from __future__ import annotations

import pytest

from src.agent.confirm.models import ConfirmedAnchor
from src.agent.docgen import validate_doc
from src.agent.intent.models import ChangeIntent
from src.agent.orchestrator import ProposalResult
from src.agent.pipeline import (
    ConfirmedAnalysis,
    analyze_confirmed,
    propose_analysis,
)
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, ChangeLine, DevPartMaster, SourceFile
from src.db.retrieve import EventHit


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class _EventFake:
    """query 무관하게 canned 이벤트/라인 반환(헤드리스 propose 경로 검증용)."""

    def __init__(self, events, lines):
        self._events = events
        self._lines = lines

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self._events)[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self._lines.get(event_id, []))

    # Protocol 나머지(헤드리스 propose 경로에선 미사용).
    def search_changes(self, query, *, top_k=10, region=None):
        return []

    def hybrid_search(self, query, *, top_k=10, region=None):
        return []

    def lookup_by_attribute(self, *, top_k=20, **kw):
        return []

    def find_similar_changes(self, seed_pno, *, top_k=5, region=None):
        return []


def _ev(eid: int, reason: str) -> EventHit:
    return EventHit(
        event_id=eid,
        base_model="A",
        new_model="B",
        event="Change",
        change_log="cp",
        change_reason=reason,
        source_ref="f.xlsx",
        file_id=1,
    )


def _ln(eid: int, pno: str) -> ChangeLine:
    return ChangeLine(event_id=eid, new_pno=pno, changepoint="cp", classification="Change")


def test_propose_analysis_stops_at_candidates(session):
    backend = _EventFake(
        [_ev(1, "r1"), _ev(2, "r2")], {1: [_ln(1, "P1")], 2: [_ln(2, "P2")]}
    )
    res = propose_analysis(
        "AC 모터를 BLDC로 변경", session=session, backend=backend, min_candidates=1
    )
    assert isinstance(res, ProposalResult)
    assert {c.event.event_id for c in res.candidates} == {1, 2}
    # 헤드리스 propose-only: 트리 전개 없음(절대원칙 #4).
    assert all(r.tool_name != "walk_subtree" for r in res.trace)


def test_analyze_confirmed_expands_and_documents(session):
    sf = SourceFile(file_name="f.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    # 출처용 dev_part_master 행(A 닻 + X,Y 자식) — generate_documents source 룩업.
    for i, pno in enumerate(("A", "X", "Y")):
        session.add(
            DevPartMaster(
                file_id=sf.file_id,
                form_id="changing_parts_list_96",
                part_no_new=pno,
                sheet_name="s",
                source_row=i + 1,  # 고유 (file_id, sheet, source_row) — uq_dpm_source
            )
        )
    for p, c in [("A", "X"), ("A", "Y")]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno=p, child_pno=c))
    session.commit()

    intent = ChangeIntent(
        raw_text="모터 변경", change_attribute="재질", confidence=0.6, source="regex"
    )
    anchors = [ConfirmedAnchor(part_no_new="A", change_point="부품 변경")]
    res = analyze_confirmed(
        intent, anchors, session=session, bom_repo=EdgeBomRepository(session)
    )
    assert isinstance(res, ConfirmedAnalysis)
    assert {n.node.pno for n in res.expansion.tree} == {"X", "Y"}  # 확정 닻 A의 하위만
    assert any(v.part_no == "A" for v in res.verdicts)
    assert validate_doc(res.doc) == []  # 모든 행 출처 있음, NEW 임의품번 없음


def test_analyze_confirmed_skips_placeholder_tree(session):
    intent = ChangeIntent(raw_text="신규 부품", confidence=0.6, source="regex")
    # 확정된 발번대기 닻은 후보(event)의 source_ref를 물고 온다.
    anchors = [
        ConfirmedAnchor(
            part_no_new="<발번대기>", is_placeholder=True, source_ref="ppt:slide3/표1"
        )
    ]
    res = analyze_confirmed(
        intent, anchors, session=session, bom_repo=EdgeBomRepository(session)
    )
    assert res.expansion.tree == []  # 발번대기 닻은 번호 없어 전개 제외
    # 발번대기 닻은 문서에서 NEW=<발번대기>로 표기(임의 품번 무생성), 출처 보존.
    assert validate_doc(res.doc) == []
    assert any(r.is_new and r.pno_display == "<발번대기>" for r in res.doc.changed_parts)
