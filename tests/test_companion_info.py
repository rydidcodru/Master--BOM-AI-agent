"""P5 — 과거 동반변경 정보행(companion_info) + docgen 체크리스트 섹션 테스트.

검증: matched/ambiguous/add_proposal/dropped 각 1건 문구, Delete 부기, 전 행 [SRC,
정보행이 개발마스터/New BOM(=changed_parts/dev_master_rows/bom_diff) 행으로 안 나감,
generate가 info_rows 유무로 섹션 토글, CASCADE_INFO=0이면 파이프라인 정보 섹션 부재,
cascade 모듈 LLM import 0(T9 승계).
"""

from __future__ import annotations

import inspect

import pytest

import src.agent.impact.cascade as cascade_mod
from src.agent.confirm.models import ConfirmedAnchor
from src.agent.docgen.generator import DocItem, SourceRef, generate
from src.agent.impact.cascade import InfoRow, companion_info
from src.agent.intent.models import ChangeIntent
from src.agent.mapping.models import MappedEvent, MappedLine, NodeRef
from src.agent.pipeline import analyze_confirmed
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, DevPartMaster, SourceFile


def _ml(status, *, lid, part_name, cp="외관 변경", src="유첨3.xlsx",
        target=None, candidates=None, classification="Change") -> MappedLine:
    return MappedLine(
        line_ref={"line_id": lid, "part_name": part_name, "base_pno": f"B{lid}",
                  "new_pno": f"N{lid}", "changepoint": cp, "source_ref": src,
                  "classification": classification},
        status=status,
        target_node=target,
        candidates=candidates or [],
    )


def _event(lines) -> MappedEvent:
    return MappedEvent(event_id=99, coherence=0.8, lines=lines)


# ── companion_info 문구 (status별) ──────────────────────────────────────────


def test_matched_phrase_has_path_and_src():
    me = _event([_ml("matched", lid=1, part_name="Decor,Control Panel",
                      target=NodeRef(pno="P1", part_name="Decor", path="/A/P1/"))])
    rows = companion_info(me, set())
    assert len(rows) == 1
    assert "현재 트리 /A/P1/에 존재" in rows[0].text
    assert "[SRC 유첨3.xlsx]" in rows[0].text


def test_ambiguous_phrase():
    me = _event([_ml("ambiguous", lid=2, part_name="Label,Barcode",
                     candidates=[NodeRef(pno="P2", part_name="Label,Barcode")])])
    rows = companion_info(me, set())
    assert "후보 Label,Barcode (확인 요)" in rows[0].text


def test_add_proposal_phrase():
    me = _event([_ml("add_proposal", lid=3, part_name="신규 부품")])
    rows = companion_info(me, set())
    assert "미존재, 추가 검토 후보 (<발번대기>)" in rows[0].text


def test_dropped_phrase():
    me = _event([_ml("dropped", lid=4, part_name="Compressor")])
    rows = companion_info(me, set())
    assert "현재 트리 미발견" in rows[0].text


def test_delete_classification_suffix():
    me = _event([_ml("matched", lid=5, part_name="Knob", classification="Delete",
                     target=NodeRef(pno="P5", part_name="Knob", path="/A/P5/"))])
    rows = companion_info(me, set())
    assert "(과거엔 삭제됨)" in rows[0].text


def test_adopted_lines_excluded():
    me = _event([
        _ml("matched", lid=1, part_name="A",
            target=NodeRef(pno="P1", part_name="A", path="/x/P1/")),
        _ml("matched", lid=2, part_name="B",
            target=NodeRef(pno="P2", part_name="B", path="/x/P2/")),
    ])
    rows = companion_info(me, {1})  # lid=1은 사람이 채택 → 정보행에서 제외
    assert len(rows) == 1 and "B" in rows[0].text


def test_all_rows_carry_src():
    me = _event([
        _ml("matched", lid=1, part_name="A", target=NodeRef(pno="P", part_name="A", path="/p/")),
        _ml("dropped", lid=2, part_name="B"),
        _ml("add_proposal", lid=3, part_name="C"),
    ])
    for r in companion_info(me, set()):
        assert "[SRC" in r.text


# ── docgen 섹션: 정보행은 체크리스트에만 ────────────────────────────────────


def _doc_item(pno="P", action="MODIFY"):
    return DocItem(part_no=pno, is_new=False, action=action, tier="CORE",
                   source=SourceRef(file_name="f.xlsx", sheet_name="s", source_row=1))


def test_generate_info_section_present_and_isolated():
    info = [InfoRow(text="과거 동반변경: X (외관 변경) — 현재 트리 미발견 [SRC f.xlsx]",
                    status="dropped", source_ref="f.xlsx")]
    doc = generate([_doc_item()], info_rows=info)
    # 체크리스트 말미에 섹션 + 정보행
    assert any("참고: 과거 동반 변경" in c for c in doc.checklist)
    assert any("과거 동반변경: X" in c for c in doc.checklist)
    # 정보행은 changed_parts/dev_master_rows/bom_diff 어디에도 없음
    for rows in (doc.changed_parts, doc.dev_master_rows, doc.bom_diff):
        assert all("과거 동반변경" not in r.detail and "과거 동반변경" not in r.pno_display
                   for r in rows)


def test_generate_no_info_rows_no_section():
    doc = generate([_doc_item(action="CHECK")])
    assert all("참고: 과거 동반 변경" not in c for c in doc.checklist)


# ── 파이프라인 게이트: CASCADE_INFO=0이면 섹션 부재 ─────────────────────────


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _seed_bom(s):
    sf = SourceFile(file_name="f.xlsx", file_hash="h1")
    s.add(sf)
    s.commit()
    for i, pno in enumerate(("A", "X")):
        s.add(DevPartMaster(file_id=sf.file_id, form_id="changing_parts_list_96",
                            part_no_new=pno, sheet_name="s", source_row=i + 1))
    s.add(BomEdge(file_id=sf.file_id, parent_pno="A", child_pno="X"))
    s.commit()


def _mapping_for_companion():
    # 채택 닻 A(line 1) + 동반변경 다른 부품(line 2, 채택 안 됨)
    return _event([
        _ml("matched", lid=1, part_name="A", target=NodeRef(pno="A", part_name="A", path="/A/")),
        _ml("dropped", lid=2, part_name="동반부품"),
    ])


def test_pipeline_cascade_info_gate(session, monkeypatch):
    _seed_bom(session)
    intent = ChangeIntent(raw_text="변경", confidence=0.6, source="regex")
    anchors = [ConfirmedAnchor(part_no_new="A", part_no_base="B1")]
    me = _mapping_for_companion()

    monkeypatch.setenv("CASCADE_INFO", "1")
    res_on = analyze_confirmed(intent, anchors, session=session,
                               bom_repo=EdgeBomRepository(session), adopted_mappings=[me])
    assert any("참고: 과거 동반 변경" in c for c in res_on.doc.checklist)
    assert any("동반부품" in c for c in res_on.doc.checklist)

    monkeypatch.setenv("CASCADE_INFO", "0")
    res_off = analyze_confirmed(intent, anchors, session=session,
                                bom_repo=EdgeBomRepository(session), adopted_mappings=[me])
    assert all("참고: 과거 동반 변경" not in c for c in res_off.doc.checklist)


def test_no_adopted_mappings_no_section(session):
    _seed_bom(session)
    intent = ChangeIntent(raw_text="변경", confidence=0.6, source="regex")
    res = analyze_confirmed(intent, [ConfirmedAnchor(part_no_new="A")], session=session,
                            bom_repo=EdgeBomRepository(session))
    assert all("참고: 과거 동반 변경" not in c for c in res.doc.checklist)


# ── T9 승계: cascade 모듈 LLM import 0 ──────────────────────────────────────


def test_cascade_module_no_llm():
    src_text = inspect.getsource(cascade_mod).lower()
    assert "agent.llm" not in src_text
    assert "ollama" not in src_text
    assert "import requests" not in src_text
