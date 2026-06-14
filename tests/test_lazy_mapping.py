"""P6 — ②.5 매핑 lazy화 + DERIVE_CP_LLM 분리 게이트 테스트.

검증:
  · 후보 검색(propose_candidates)은 scope가 있어도 map_event_to_scope를 **0회** 호출
    (매핑 시점이 채택 후로 이동).
  · build_adopted_mappings는 채택 이벤트 1건당 정확히 1회 매핑(SEARCH_INCLUDE_STRUCT on).
  · SEARCH_INCLUDE_STRUCT off면 매핑 0회(빈 리스트).
  · struct_score는 lazy화 후에도 검색에서 그대로 채워짐.
  · intent_from_change는 DERIVE_CP_LLM off(기본)이면 derive에 LLM을 넘기지 않는다.
"""

from __future__ import annotations

import pytest

import src.agent.mapping.mapper as mapper_mod
import src.agent.pipeline as pipeline_mod
from src.agent.intent.models import ChangeIntent
from src.agent.matching.scoring import MatchThresholds, ScopeIndex, ScopeNode
from src.agent.orchestrator import propose_candidates
from src.agent.pipeline import build_adopted_mappings
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import ChangeLine
from src.db.retrieve import EventHit
from src.preprocess.normalize import canonicalize_part_name


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class FakeBackend:
    def __init__(self, events, lines):
        self.events = events
        self.lines = lines

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self.events.get(query, []))[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self.lines.get(event_id, []))


def _ev(eid):
    return EventHit(event_id=eid, base_model="A", new_model="B", event="Change",
                    change_log="cp", change_reason="r", source_ref="f.xlsx", file_id=1)


def _scope():
    n = ScopeNode("P100", "HEATER Assembly", canonicalize_part_name("HEATER Assembly"),
                  "Ass'y", 1, "/P100/")
    return ScopeIndex([n], MatchThresholds())


def _backend5():
    evs = {"q": [_ev(i) for i in range(1, 6)]}
    lines = {i: [ChangeLine(event_id=i, seq=1, base_pno="P100", part_name="HEATER Assembly",
                            classification="Change")] for i in range(1, 6)}
    return FakeBackend(evs, lines)


def _intent():
    return ChangeIntent(raw_text="x", rewritten_queries=["q"], confidence=1.0,
                        source="ppt", module_names=["Controller"], base_model="M")


# ── propose는 매핑 0회 (scope 있어도) ───────────────────────────────────────


def test_propose_does_not_map_even_with_scope(session, monkeypatch):
    calls = {"n": 0}
    orig = mapper_mod.map_event_to_scope
    monkeypatch.setattr(mapper_mod, "map_event_to_scope",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), orig(*a, **k))[1])
    res = propose_candidates(_intent(), session=session, backend=_backend5(),
                             write_log=False, scope=_scope(), max_candidates=20)
    assert len(res.candidates) == 5
    assert calls["n"] == 0  # 검색 시점 매핑 없음(lazy)
    assert all(c.mapping is None for c in res.candidates)
    # struct_score는 그대로 채워짐(①.5 event_structure_score 재사용)
    assert all(c.struct_score is not None for c in res.candidates)


# ── 채택 후 매핑은 채택 1건당 1회 ───────────────────────────────────────────


def test_build_adopted_mappings_one_call_per_adopted(session, monkeypatch):
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "1")
    monkeypatch.setattr(pipeline_mod, "_unused", None, raising=False)
    # build_scope를 가짜 scope로 대체(BOM 적재 없이 lazy 경로만 검증)
    import src.agent.orchestrator.structure_scope as ss
    monkeypatch.setattr(ss, "build_scope", lambda *a, **k: _scope())
    # lookup_lines_by_event 가짜
    import src.db.retrieve as retr
    monkeypatch.setattr(retr, "lookup_lines_by_event",
                        lambda s, eid: [ChangeLine(event_id=eid, seq=1, base_pno="P100",
                                                   part_name="HEATER Assembly", classification="Change")])
    calls = {"n": 0}
    orig = mapper_mod.map_event_to_scope
    monkeypatch.setattr(mapper_mod, "map_event_to_scope",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), orig(*a, **k))[1])

    out = build_adopted_mappings(_intent(), {3}, session)  # 1건 채택
    assert calls["n"] == 1
    assert len(out) == 1 and out[0].event_id == 3
    assert out[0].lines[0].status == "matched"


def test_build_adopted_mappings_struct_off_is_empty(session, monkeypatch):
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "0")
    assert build_adopted_mappings(_intent(), {1, 2}, session) == []


def test_build_adopted_mappings_no_adopted_is_empty(session, monkeypatch):
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "1")
    assert build_adopted_mappings(_intent(), set(), session) == []


# ── DERIVE_CP_LLM 분리 게이트 ───────────────────────────────────────────────


def test_intent_from_change_no_llm_when_derive_cp_llm_off(monkeypatch):
    from src.agent.intent import structurizer as st

    monkeypatch.delenv("DERIVE_CP_LLM", raising=False)  # 기본 off
    monkeypatch.delenv("DERIVE_CP", raising=False)       # 기본 on
    called = {"maybe_llm": 0}
    monkeypatch.setattr(st, "_maybe_llm",
                        lambda: called.__setitem__("maybe_llm", called["maybe_llm"] + 1) or None)
    captured = {}
    orig = st.derive_change_slots
    monkeypatch.setattr(st, "derive_change_slots",
                        lambda reason, name=None, llm=None: captured.__setitem__("llm", llm) or orig(reason, name, llm=llm))
    st.intent_from_change(change_detail="", change_reason="원가 절감을 위해 재질 변경")
    assert captured.get("llm") is None  # DERIVE_CP_LLM off → derive에 LLM 미전달
    assert called["maybe_llm"] == 0     # LLM 구성 시도조차 안 함


def test_intent_from_change_passes_llm_when_derive_cp_llm_on(monkeypatch):
    from src.agent.intent import structurizer as st

    monkeypatch.setenv("DERIVE_CP_LLM", "1")
    sentinel = object()
    monkeypatch.setattr(st, "_maybe_llm", lambda: sentinel)
    captured = {}
    orig = st.derive_change_slots
    monkeypatch.setattr(st, "derive_change_slots",
                        lambda reason, name=None, llm=None: captured.__setitem__("llm", llm) or orig(reason, name, llm=None))
    st.intent_from_change(change_detail="", change_reason="원가 절감을 위해 재질 변경")
    assert captured.get("llm") is sentinel  # 게이트 on → _maybe_llm() 결과 전달
