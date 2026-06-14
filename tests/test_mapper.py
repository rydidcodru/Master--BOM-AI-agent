"""Phase 3 — ②.5 후보-BOM 매핑(mapper) 테스트.

합성 S(노드 7개) + 합성 이벤트 라인으로 M1~M5 각 1건 이상, proposed_action 표 각 행,
오타 흡수("Assmebly"→Assembly canon 매칭), coherence 경계(θ±), ``<발번대기>`` 강제,
LLM import 0 단언.
"""

from __future__ import annotations

import inspect

import pytest

import src.agent.mapping.mapper as mapper_mod
import src.agent.mapping.models as mapping_models_mod
from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.agent.mapping import map_event_to_scope
from src.agent.matching.scoring import MatchThresholds, ScopeIndex, ScopeNode
from src.db.models import ChangeLine
from src.preprocess.normalize import canonicalize_part_name


def _node(pno: str, name: str, ptype: str | None = None, depth: int = 1) -> ScopeNode:
    return ScopeNode(
        pno=pno, part_name=name, part_name_canon=canonicalize_part_name(name),
        part_type=ptype, depth=depth, path=f"/{pno}/",
    )


@pytest.fixture()
def index() -> ScopeIndex:
    nodes = [
        _node("P100", "HEATER Assembly", "Ass'y"),
        _node("P200", "BRACKET MOUNTING", "PD Part"),
        _node("P300", "DOOR HANDLE", "MD Part"),
        _node("P400", "TRAY 법랑", "법랑"),
        _node("P500", "COVER REAR", "PD Part", depth=2),
        _node("P600", "COVER FRONT", "PD Part", depth=2),
        _node("P700", "HARNESS MAIN", "Harness"),
    ]
    return ScopeIndex(nodes, MatchThresholds())


def _line(event_id: int = 7, **kw) -> ChangeLine:
    return ChangeLine(event_id=event_id, seq=1, **kw)


# ── M1~M5 status 판별 ───────────────────────────────────────────────────────


def test_m1_pno_exact_matched(index):
    me = map_event_to_scope([_line(base_pno="P100", classification="Change")], index)
    ln = me.lines[0]
    assert ln.status == "matched" and ln.confidence == 1.0
    assert ln.target_node is not None and ln.target_node.pno == "P100"
    assert any(e.kind == "pno_exact" for e in ln.evidence)
    assert me.event_id == 7


def test_m2_canon_trgm_matched_typo_absorbed(index):
    # 오타 "Assmebly" → token_canon → "Assembly" — canon trgm 1.0, gap 충분.
    me = map_event_to_scope(
        [_line(part_name="HEATER Assmebly", classification="Change")], index
    )
    ln = me.lines[0]
    assert ln.status == "matched"
    assert ln.target_node is not None and ln.target_node.pno == "P100"
    assert ln.confidence >= 0.75
    assert any(e.kind == "canon_trgm" for e in ln.evidence)


def test_m3_ambiguous_with_candidates(index):
    # "COVER"는 COVER REAR/COVER FRONT 둘 다 1.0 — gap 0 ≤ eps → ambiguous.
    me = map_event_to_scope([_line(part_name="COVER", classification="Change")], index)
    ln = me.lines[0]
    assert ln.status == "ambiguous"
    assert ln.proposed_action == "CHECK"  # ambiguous → 항상 CHECK
    pnos = {c.pno for c in ln.candidates}
    assert {"P500", "P600"} <= pnos and len(ln.candidates) <= 3


def test_m4_add_proposal_via_part_type(index):
    # best < tau_lo + classification=New + part_type ∈ S.types → add_proposal.
    me = map_event_to_scope(
        [_line(part_name="ZZ NEW PART", classification="New", part_type="PD Part")],
        index,
    )
    ln = me.lines[0]
    assert ln.status == "add_proposal"
    assert ln.proposed_action == "ADD"
    assert ln.proposed_pno == PNO_PLACEHOLDER  # <발번대기> 강제 — 신규 품번 무생성


def test_m4_addition_cue_from_changepoint(index):
    # classification 없음 + changepoint "부품 추가"(추가계 cue) → add_proposal.
    me = map_event_to_scope(
        [_line(part_name="ZZ NEW PART", changepoint="부품 추가", part_type="PD Part")],
        index,
    )
    assert me.lines[0].status == "add_proposal"
    assert me.lines[0].proposed_pno == PNO_PLACEHOLDER


def test_m5_dropped(index):
    me = map_event_to_scope(
        [_line(part_name="ZZZZ", classification="Change", part_type="없는 군")], index
    )
    ln = me.lines[0]
    assert ln.status == "dropped"
    assert ln.proposed_action is None and ln.proposed_pno is None


# ── coherence 경계 (θ±) — M4의 두 번째 게이트 ───────────────────────────────


def test_m4_coherence_at_theta_allows_add_proposal(index):
    # 2라인: [P100 정확일치(1.0), 추가 후보] → coherence = 1.0/2 = 0.5 = θ → add_proposal.
    lines = [
        _line(base_pno="P100", classification="Change"),
        _line(part_name="ZZ NEW PART", classification="New", part_type="없는 군"),
    ]
    me = map_event_to_scope(lines, index)
    assert me.coherence == pytest.approx(0.5)
    assert me.lines[1].status == "add_proposal"


def test_m4_coherence_below_theta_drops(index):
    # 단독 라인(매칭 0) → coherence 0 < θ, part_type도 S 밖 → dropped.
    me = map_event_to_scope(
        [_line(part_name="ZZ NEW PART", classification="New", part_type="없는 군")],
        index,
    )
    assert me.coherence == pytest.approx(0.0)
    assert me.lines[0].status == "dropped"


# ── proposed_action 표 ──────────────────────────────────────────────────────


def test_action_delete_for_matched_delete(index):
    me = map_event_to_scope([_line(base_pno="P200", classification="Delete")], index)
    assert me.lines[0].proposed_action == "DELETE"


def test_action_modify_for_matched_change(index):
    me = map_event_to_scope([_line(base_pno="P300", classification="Change")], index)
    assert me.lines[0].proposed_action == "MODIFY"


def test_action_modify_from_changepoint_action(index):
    # classification 없음 → changepoint 행위(교체→교체계) → MODIFY.
    me = map_event_to_scope([_line(base_pno="P400", changepoint="타입 교체")], index)
    assert me.lines[0].proposed_action == "MODIFY"


def test_action_delete_from_changepoint_label(index):
    me = map_event_to_scope([_line(base_pno="P500", changepoint="부품 삭제")], index)
    assert me.lines[0].proposed_action == "DELETE"


def test_action_conflict_new_but_matched_is_check(index):
    # 신호 상충: New(추가)인데 S에 이미 존재(matched) → CHECK + 상충 evidence.
    me = map_event_to_scope([_line(base_pno="P300", classification="New")], index)
    ln = me.lines[0]
    assert ln.status == "matched" and ln.proposed_action == "CHECK"
    assert any("상충" in e.detail for e in ln.evidence)


def test_action_no_signal_is_check(index):
    me = map_event_to_scope([_line(base_pno="P400")], index)
    ln = me.lines[0]
    assert ln.proposed_action == "CHECK"
    assert any("무신호" in e.detail for e in ln.evidence)


# ── line_ref 보존 + 순수성 ──────────────────────────────────────────────────


def test_line_ref_preserves_source(index):
    me = map_event_to_scope(
        [_line(base_pno="P100", part_name="HEATER", source_ref="유첨3.xlsx", changepoint="품번 변경")],
        index,
    )
    ref = me.lines[0].line_ref
    assert ref["source_ref"] == "유첨3.xlsx"
    assert ref["changepoint"] == "품번 변경"
    assert ref["base_pno"] == "P100"


def test_mapper_modules_import_no_llm():
    """②.5는 LLM/DB쓰기/네트워크를 import하지 않는다 (순수 결정론)."""
    for mod in (mapper_mod, mapping_models_mod):
        src_text = inspect.getsource(mod).lower()
        assert "agent.llm" not in src_text
        assert "ollama" not in src_text
        assert "import requests" not in src_text
        assert "session.commit" not in src_text and "session.add" not in src_text
