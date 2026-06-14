"""Phase B/C — 다항목 HITL 통합 어댑터 + New BOM 검증.

검증:
  - propose_from_items: PPT 변경항목 N건 → 항목별 후보(정지, 트리 없음). item_index/intent 보존.
  - confirm_items_and_newbom: 채택 항목만 확정→전개(절대원칙 #4) + 확정 new P/No로 베이스 트리 적용.
  - validate_new_bom: 출처 없는 변경행 / NEW 임의품번 거부.
"""

from __future__ import annotations

import pytest

from src.agent.basebom.apply import NewBom, NewBomRow, validate_new_bom
from src.agent.basebom.parser import BaseBom, BaseBomRow
from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, ChangeLine, DevPartMaster, SourceFile
from src.db.retrieve import EventHit
from src.ui.agent_client import confirm_items_and_newbom, propose_from_items

PA, PX, PY = "AGG74419321", "AGG74419322", "AGG74419323"


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class _EventFake:
    """query 무관 canned 이벤트/라인 반환(헤드리스 propose 경로 검증용)."""

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


def _accept_all(proposals: list[dict]) -> list[dict]:
    """proposals의 모든 후보 라인을 채택하는 선택행(UI _ppt_selections 형태)."""
    sels: list[dict] = []
    for p in proposals:
        for c in p["candidates"]:
            for ln in c["lines"]:
                sels.append(
                    {
                        "accept": True,
                        "part_no_base": ln.get("part_no_base"),
                        "part_no_new_suggested": ln.get("part_no_new_suggested"),
                        "change_point": "부품 변경",
                        "new_pno_input": None,
                        "classification": ln.get("classification"),
                        "source_ref": ln.get("source_ref"),
                        "event_id": ln.get("event_id"),
                        "_item_index": p["item_index"],
                    }
                )
    return sels


def _item(detail: str, *, base_pno: str = PA) -> dict:
    return {
        "change_detail": detail,
        "change_reason": "소음 저감",
        "base_part_no": base_pno,
        "part_name": "MOTOR",
        "module": "M",
        "change_type": "변경",
        "src": {"slide": 1, "table_id": 2},
    }


def test_propose_from_items_aggregates_and_preserves_index(session):
    backend = _EventFake([_ev(1, "r1"), _ev(2, "r2")], {1: [_ln(1, PA)], 2: [_ln(2, PX)]})
    items = [_item("AC 모터를 BLDC로"), _item("패킹 재질 변경"), {"change_detail": "", "change_reason": ""}]
    proposals = propose_from_items(items, session=session, backend=backend, base_model="A", region=None)

    # 빈 텍스트 항목은 건너뛴다 → 2개.
    assert [p["item_index"] for p in proposals] == [0, 1]
    assert all(p["candidates"] for p in proposals)
    # 항목별 intent 유지 + 검색에 부품명/품번 반영(2026-06-10 확정). _item: base_part_no=PA,
    # part_name="MOTOR" → intent.part_nos=[PA], 부품 보강 쿼리에 부품명·품번이 들어간다.
    for p in proposals:
        assert p["intent"]["part_nos"] == [PA] and p["intent"]["models"] == []
        assert p["item"]["base_part_no"] == PA
        joined = " ".join(p["intent"]["rewritten_queries"])
        assert "MOTOR" in joined and PA in joined


def test_confirm_items_and_newbom_end_to_end(session):
    sf = SourceFile(file_name="f.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    for i, pno in enumerate((PA, PX, PY)):
        session.add(
            DevPartMaster(
                file_id=sf.file_id,
                form_id="changing_parts_list_96",
                part_no_new=pno,
                sheet_name="s",
                source_row=i + 1,  # 고유 (file_id, sheet, source_row) — uq_dpm_source
            )
        )
    for parent, child in [(PA, PX), (PA, PY)]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno=parent, child_pno=child))
    session.commit()

    backend = _EventFake([_ev(1, "소음 저감")], {1: [_ln(1, PA)]})
    proposals = propose_from_items([_item("AC 모터를 BLDC로")], session=session, backend=backend)
    assert proposals and proposals[0]["candidates"]

    bom = BaseBom(
        model="A",
        file_name="b.xlsx",
        rows=[
            BaseBomRow(0, "ROOTMODEL000", "ROOT", "", "", 0, "root"),
            BaseBomRow(1, PA, "MOTOR", "ROOTMODEL000", ".1", 1, "root/PA"),
        ],
    )
    res = confirm_items_and_newbom(
        proposals=proposals,
        selections=_accept_all(proposals),
        bom=bom,
        base_model="A",
        session=session,
        bom_repo=EdgeBomRepository(session),
    )

    # 확정 닻 A의 하위만 전개(절대원칙 #4).
    assert len(res["items"]) == 1
    confirmed = res["items"][0]["confirmed"]
    assert {n["pno"] for n in confirmed["tree"]} == {PX, PY}

    # New BOM = 베이스 트리 복제 + PA MODIFY, 검증 통과.
    nb = res["new_bom"]
    assert nb is not None
    statuses = {r["base P/N"]: r["status"] for r in nb["full"]}
    assert statuses[PA] == "MODIFY"
    assert nb["violations"] == []


def test_confirm_items_no_accept_skips_expansion(session):
    backend = _EventFake([_ev(1, "r")], {1: [_ln(1, PA)]})
    proposals = propose_from_items([_item("x")], session=session, backend=backend)
    sels = _accept_all(proposals)
    for s in sels:
        s["accept"] = False  # 전부 거절
    res = confirm_items_and_newbom(
        proposals=proposals, selections=sels, bom=None, session=session,
        bom_repo=EdgeBomRepository(session),
    )
    assert res["items"] == []  # 채택 0 → 전개 없음
    assert res["new_bom"] is None


def test_validate_new_bom_flags_violations():
    rows = [
        NewBomRow(1, "AGG1", "AGG1", "keep", "", 1, "KEEP", src="[SRC base_bom/-/1]"),
        NewBomRow(2, "AGG2", "AGG2X", "mod", "", 1, "MODIFY", src=""),  # 출처 없음
        NewBomRow(None, "", "ARB123", "add", "신규", 1, "ADD", is_new=True, src="[SRC ppt/slide1/2]"),  # NEW 임의품번
    ]
    v = validate_new_bom(NewBom(model="M", rows=rows))
    assert any("출처 없음" in x for x in v)
    assert any("신규 임의품번" in x for x in v)


def test_validate_new_bom_clean():
    rows = [
        NewBomRow(1, "AGG1", "AGG1", "k", "", 1, "KEEP", src="x"),
        NewBomRow(2, "AGG2", "AGG2X", "m", "", 1, "MODIFY", src="[SRC ppt/slide1/2]"),
        NewBomRow(None, "", PNO_PLACEHOLDER, "a", "신규", 1, "ADD", is_new=True, src="[SRC ppt/slide2/3]"),
    ]
    assert validate_new_bom(NewBom(model="M", rows=rows)) == []
