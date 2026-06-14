"""Phase 2 — ①.5 구조 스코프 부스트(레벨 B 융합) 테스트.

검증: ① ``SEARCH_INCLUDE_STRUCT=0`` 또는 S=None이면 검색 결과가 기존과 **완전 동일**
(골든 비교), ② 합성 픽스처에서 구조 정합 높은 이벤트의 최종 순위 상승,
③ ``_rrf_fuse`` 기본 호출(가중 미지정) 회귀 없음.
"""

from __future__ import annotations

import pytest

from src.agent.intent.models import ChangeIntent
from src.agent.matching.scoring import MatchThresholds, ScopeIndex, ScopeNode
from src.agent.orchestrator import propose_candidates
from src.agent.orchestrator.orchestrate import _rrf_fuse
from src.agent.orchestrator.structure_scope import build_scope
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
    def __init__(self, events=None, lines=None):
        self.events = events or {}  # query -> list[EventHit]
        self.lines = lines or {}    # event_id -> list[ChangeLine]

    def search_events(self, query, *, top_k=5, new_model=None, base_model=None, event=None):
        return list(self.events.get(query, []))[:top_k]

    def lookup_lines_by_event(self, event_id):
        return list(self.lines.get(event_id, []))


def _ev(event_id: int, reason: str = "사유") -> EventHit:
    return EventHit(
        event_id=event_id, base_model="A", new_model="B", event="Change",
        change_log="cp", change_reason=reason, source_ref="f.xlsx", file_id=1,
    )


def _scope(*pnos: str) -> ScopeIndex:
    nodes = [
        ScopeNode(
            pno=p, part_name=f"PART {p}",
            part_name_canon=canonicalize_part_name(f"PART {p}"),
            part_type=None, depth=1, path=f"/{p}/",
        )
        for p in pnos
    ]
    return ScopeIndex(nodes, MatchThresholds())


def _backend() -> FakeBackend:
    # 두 쿼리 모두 [E1, E2] 순서 — 부스트 없으면 E1이 항상 1위.
    e1, e2 = _ev(1, "사유 하나"), _ev(2, "사유 둘")
    return FakeBackend(
        events={"q1": [e1, e2], "q2": [e1, e2]},
        lines={
            1: [ChangeLine(event_id=1, seq=1)],                      # S 매칭 신호 없음
            2: [ChangeLine(event_id=2, seq=1, base_pno="P900")],     # S의 pno와 정확일치
        },
    )


def _intent() -> ChangeIntent:
    return ChangeIntent(raw_text="변경", rewritten_queries=["q1", "q2"], confidence=1.0, source="ppt")


# ── ① 게이트 off / S=None → 기존과 완전 동일 (골든 비교) ────────────────────

_GOLDEN_ORDER = [1, 2]  # 순수 RRF: E1(rank0×2) > E2(rank1×2)


def test_gate_off_results_identical(monkeypatch, session):
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "0")
    res = propose_candidates(
        _intent(), session=session, backend=_backend(), write_log=False
    )
    assert [c.event.event_id for c in res.candidates] == _GOLDEN_ORDER
    assert all(c.struct_score is None for c in res.candidates)
    # 도구 트레이스에도 structure_score 호출 없음.
    assert all(r.tool_name != "structure_score" for r in res.trace)


def test_gate_on_but_scope_none_identical(monkeypatch, session):
    # 게이트 on이지만 S 구축 실패(빈 bom_edge → build_scope None) → 기존과 완전 동일.
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "1")
    res = propose_candidates(
        _intent(), session=session, backend=_backend(), write_log=False
    )
    assert [c.event.event_id for c in res.candidates] == _GOLDEN_ORDER
    assert all(c.struct_score is None for c in res.candidates)


def test_build_scope_failures_return_none(session):
    th = MatchThresholds()
    assert build_scope([], "WSED7667M", session, th) is None          # 모듈명 없음
    assert build_scope(["Door Assembly"], "NOPE", session, th) is None  # 파일 미존재


# ── ② 구조 정합 높은 이벤트의 최종 순위 상승 ────────────────────────────────


def test_struct_aligned_event_rises(monkeypatch, session):
    monkeypatch.delenv("SEARCH_INCLUDE_STRUCT", raising=False)
    res = propose_candidates(
        _intent(), session=session, backend=_backend(), write_log=False,
        scope=_scope("P900"),  # E2의 base_pno=P900 정확일치 → structure 리스트 1위
    )
    assert [c.event.event_id for c in res.candidates] == [2, 1]  # E2가 역전
    by_id = {c.event.event_id: c for c in res.candidates}
    assert by_id[2].struct_score == pytest.approx(1.0)  # cov 1.0, max 1.0
    assert by_id[2].event.struct_score == pytest.approx(1.0)
    # S 신호 없는 E1은 structure 리스트 미포함(score 0) — struct_score는 0.0 기록.
    assert by_id[1].struct_score == pytest.approx(0.0)
    assert any(r.tool_name == "structure_score" for r in res.trace)
    # P6: ②.5 매핑은 lazy화 — 검색 시점엔 채우지 않는다(채택 후 build_adopted_mappings).
    assert all(c.mapping is None for c in res.candidates)


def test_propose_never_maps_lazy(monkeypatch, session):
    # 구조 부스트 on이어도 후보 매핑은 채택 후로 미룬다(struct_score만 채움).
    res = propose_candidates(
        _intent(), session=session, backend=_backend(), write_log=False,
        scope=_scope("P900"),
    )
    assert all(c.mapping is None for c in res.candidates)
    assert all(c.struct_score is not None for c in res.candidates)


def test_struct_weight_env_scales_boost(monkeypatch, session):
    # STRUCT_WEIGHT=0 → structure 리스트가 점수에 기여하지 않아 골든 순서 유지.
    monkeypatch.setenv("STRUCT_WEIGHT", "0.0")
    res = propose_candidates(
        _intent(), session=session, backend=_backend(), write_log=False,
        scope=_scope("P900"),
    )
    assert [c.event.event_id for c in res.candidates] == _GOLDEN_ORDER


def test_dedup_by_reason_keeps_max_struct_score(monkeypatch, session):
    # 같은 사유의 두 이벤트 — dedup 병합 시 struct_score MAX 보존.
    e1, e1dup = _ev(1, "같은 사유"), _ev(3, "같은 사유")
    backend = FakeBackend(
        events={"q1": [e1, e1dup]},
        lines={1: [ChangeLine(event_id=1, seq=1)], 3: [ChangeLine(event_id=3, seq=1, base_pno="P900")]},
    )
    intent = ChangeIntent(raw_text="변경", rewritten_queries=["q1"], confidence=1.0, source="ppt")
    res = propose_candidates(
        intent, session=session, backend=backend, write_log=False,
        dedup_by_reason=True, scope=_scope("P900"),
    )
    assert len(res.candidates) == 1
    assert res.candidates[0].struct_score == pytest.approx(1.0)  # dup(E3)의 MAX 보존


# ── ③ _rrf_fuse 기본 호출 회귀 없음 ─────────────────────────────────────────


def test_rrf_fuse_default_equals_unit_weights():
    a, b, c = _ev(1), _ev(2), _ev(3)
    lists = [[a, b], [b, c]]
    default = _rrf_fuse([list(x) for x in lists], key=lambda e: e.event_id)
    default_scores = {e.event_id: e.score_rrf for e in default}
    unit = _rrf_fuse([list(x) for x in lists], key=lambda e: e.event_id, weights=[1.0, 1.0])
    unit_scores = {e.event_id: e.score_rrf for e in unit}
    assert [e.event_id for e in default] == [e.event_id for e in unit]
    assert default_scores == unit_scores
    # 수기 계산 핀: b = 1/62 + 1/61, a = 1/61, c = 1/62.
    assert default_scores[2] == pytest.approx(1 / 62 + 1 / 61)
    assert default_scores[1] == pytest.approx(1 / 61)
    assert default_scores[3] == pytest.approx(1 / 62)
