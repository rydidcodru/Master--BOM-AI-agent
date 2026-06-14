"""Phase 3 — change_event Recall@5 골든셋 테스트 (SQLite + 주입형, 네트워크 0).

검증:
  - golden_cases_from_change_events: 사유 질의(change_log+reason) + 부품 세트 도출,
    min_parts 필터, file_ids 스코프, 빈 부품/빈 질의 제외.
  - evaluate_event_retrieval: 완전/부분/실패 retriever에 대한 event_recall·part_recall·mrr.
  - JSONL dump/load 라운드트립.
"""

from __future__ import annotations

import pytest

from src.agent.eval import (
    GoldenEventCase,
    dump_golden_cases,
    evaluate_event_retrieval,
    golden_cases_from_change_events,
    load_golden_cases,
)
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import ChangeEvent, ChangeLine, SourceFile


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _event(session, *, file_id=None, change_log="cl", change_reason="cr",
           base_model="A", new_model="B", pnos=("P1", "P2")):
    ev = ChangeEvent(
        file_id=file_id, change_log=change_log, change_reason=change_reason,
        base_model=base_model, new_model=new_model,
    )
    session.add(ev)
    session.flush()
    for i, p in enumerate(pnos, 1):
        session.add(ChangeLine(event_id=ev.event_id, seq=i, new_pno=p))
    return ev.event_id


# ── golden 빌더 ────────────────────────────────────────────────


def test_builder_basic(session):
    eid = _event(session, change_log="모터 AC→BLDC", change_reason="BLDC 기능 추가",
                 pnos=("P1", "P2", "P3"))
    session.commit()

    cases = golden_cases_from_change_events(session)
    assert len(cases) == 1
    c = cases[0]
    assert c.event_id == eid
    assert c.golden_pnos == {"P1", "P2", "P3"}
    # 질의 = change_log + change_reason (reason_embedding 내용과 동일 구성)
    assert "모터 AC→BLDC" in c.query and "BLDC 기능 추가" in c.query


def test_builder_min_parts_filter(session):
    _event(session, change_reason="r1", pnos=("P1",))          # 1 part
    _event(session, change_reason="r2", pnos=("P2", "P3"))     # 2 parts
    session.commit()

    assert len(golden_cases_from_change_events(session, min_parts=1)) == 2
    cases2 = golden_cases_from_change_events(session, min_parts=2)
    assert len(cases2) == 1
    assert cases2[0].golden_pnos == {"P2", "P3"}


def test_builder_skips_empty_query_and_parts(session):
    # 질의도 부품도 없음 → 제외.
    ev = ChangeEvent(change_log="", change_reason="", base_model="A")
    session.add(ev)
    session.flush()
    session.add(ChangeLine(event_id=ev.event_id, seq=1, new_pno=""))  # 빈 부품
    session.commit()
    assert golden_cases_from_change_events(session) == []


def test_builder_uses_base_pno_when_no_new(session):
    ev = ChangeEvent(change_reason="삭제 사유", base_model="A")
    session.add(ev)
    session.flush()
    session.add(ChangeLine(event_id=ev.event_id, seq=1, base_pno="OLD1", new_pno=None))
    session.commit()
    cases = golden_cases_from_change_events(session)
    assert cases[0].golden_pnos == {"OLD1"}


def test_builder_file_id_scope(session):
    sf1 = SourceFile(file_name="a.xlsx", file_hash="ha")
    sf2 = SourceFile(file_name="b.xlsx", file_hash="hb")
    session.add_all([sf1, sf2])
    session.commit()
    _event(session, file_id=sf1.file_id, change_reason="r1", pnos=("P1", "P2"))
    _event(session, file_id=sf2.file_id, change_reason="r2", pnos=("P3", "P4"))
    session.commit()

    only1 = golden_cases_from_change_events(session, file_ids=[sf1.file_id])
    assert len(only1) == 1
    assert only1[0].golden_pnos == {"P1", "P2"}


# ── 평가 러너 (주입형 fake) ────────────────────────────────────


def _cases() -> list[GoldenEventCase]:
    return [
        GoldenEventCase(event_id=1, query="q1", golden_pnos={"P1", "P2"}),
        GoldenEventCase(event_id=2, query="q2", golden_pnos={"P3"}),
    ]


def test_eval_perfect_retriever():
    parts = {1: ["P1", "P2"], 2: ["P3"]}
    m = evaluate_event_retrieval(
        _cases(),
        retrieve=lambda q, k: [1] if q == "q1" else [2],
        expand=lambda e: parts[e],
        k=5,
    )
    assert m.n_cases == 2
    assert m.event_recall_at_k == 1.0
    assert m.event_mrr == 1.0
    assert m.part_recall_at_k == 1.0


def test_eval_missing_event():
    # retriever가 다른 이벤트만 반환 → event_recall 0, 부품도 불일치.
    m = evaluate_event_retrieval(
        _cases(),
        retrieve=lambda q, k: [99],
        expand=lambda e: ["ZZ"],
        k=5,
    )
    assert m.event_recall_at_k == 0.0
    assert m.event_mrr == 0.0
    assert m.part_recall_at_k == 0.0


def test_eval_partial_part_recall():
    # 케이스1: 이벤트는 맞지만 부품 한 개만 회수(P1) → part_recall 0.5.
    parts = {1: ["P1"], 2: ["P3"]}
    m = evaluate_event_retrieval(
        [GoldenEventCase(event_id=1, query="q1", golden_pnos={"P1", "P2"})],
        retrieve=lambda q, k: [1],
        expand=lambda e: parts[e],
        k=5,
    )
    assert m.event_recall_at_k == 1.0
    assert m.part_recall_at_k == 0.5


def test_eval_mrr_rank_two():
    # 골든 이벤트가 top의 2번째 → mrr 0.5.
    m = evaluate_event_retrieval(
        [GoldenEventCase(event_id=1, query="q1", golden_pnos={"P1"})],
        retrieve=lambda q, k: [9, 1, 8],
        expand=lambda e: {1: ["P1"]}.get(e, []),
        k=5,
    )
    assert m.event_mrr == 0.5
    assert m.part_recall_at_k == 1.0  # 2번째라도 top-k 안이라 부품 회수됨


def test_eval_k_truncation():
    # k=1이면 top 1개만 → 2번째 골든 이벤트 놓침.
    m = evaluate_event_retrieval(
        [GoldenEventCase(event_id=1, query="q1", golden_pnos={"P1"})],
        retrieve=lambda q, k: [9, 1],
        expand=lambda e: {1: ["P1"]}.get(e, []),
        k=1,
    )
    assert m.event_recall_at_k == 0.0
    assert m.part_recall_at_k == 0.0


def test_eval_empty_cases():
    m = evaluate_event_retrieval([], retrieve=lambda q, k: [], expand=lambda e: [], k=5)
    assert m.n_cases == 0
    assert m.event_recall_at_k == 0.0


# ── JSONL 라운드트립 ───────────────────────────────────────────


def test_golden_jsonl_roundtrip(tmp_path):
    cases = [
        GoldenEventCase(event_id=1, query="모터 변경", golden_pnos={"P2", "P1"},
                        base_model="A", new_model="B"),
        GoldenEventCase(event_id=2, query="재질 변경", golden_pnos={"P3"}),
    ]
    path = tmp_path / "golden.jsonl"
    n = dump_golden_cases(cases, path)
    assert n == 2

    loaded = load_golden_cases(path)
    assert len(loaded) == 2
    assert loaded[0].event_id == 1
    assert loaded[0].golden_pnos == {"P1", "P2"}
    assert loaded[0].base_model == "A"
    assert loaded[1].golden_pnos == {"P3"}
