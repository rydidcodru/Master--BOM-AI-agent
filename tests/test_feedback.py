"""P8 — HITL 피드백 축적(confirm_feedback) 테스트.

검증: 스키마 기록 round-trip, FEEDBACK_LOG=0이면 무기록+본 흐름 무영향, 기록 실패
주입 시 확정 흐름 정상 완료, confirm_feedback **읽기 금지** grep 단언(리포트 CLI 외).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.agent.feedback import record_confirm_feedback
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import ConfirmFeedback


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _cands():
    return [
        {"event_id": 1, "score_rrf": 0.04, "struct_score": 0.7, "rescued": False},
        {"event_id": 2, "score_rrf": 0.03, "struct_score": None, "rescued": True},
    ]


def test_round_trip(session, monkeypatch):
    monkeypatch.setenv("FEEDBACK_LOG", "1")
    ok = record_confirm_feedback(
        session, query_text="패킹 변경", intent_json={"raw_text": "패킹 변경"},
        candidates=_cands(), adopted_event_ids=[1], manual_added_event_ids=[],
        low_confidence=True, source_ref="f.xlsx",
    )
    assert ok is True
    row = session.query(ConfirmFeedback).one()
    assert row.query_text == "패킹 변경"
    assert row.adopted_event_ids == [1]
    assert row.low_confidence is True
    assert len(row.candidates) == 2
    assert row.candidates[0]["rank"] == 1  # digest가 rank 부여


def test_feedback_log_off_no_write(session, monkeypatch):
    monkeypatch.setenv("FEEDBACK_LOG", "0")
    ok = record_confirm_feedback(
        session, query_text="x", intent_json=None, candidates=[],
        adopted_event_ids=[], manual_added_event_ids=[], low_confidence=False,
    )
    assert ok is False
    assert session.query(ConfirmFeedback).count() == 0


def test_record_failure_non_blocking(session, monkeypatch):
    monkeypatch.setenv("FEEDBACK_LOG", "1")

    def _boom(*a, **k):
        raise RuntimeError("DB down")

    monkeypatch.setattr(session, "commit", _boom)
    # 기록 실패해도 예외 전파 없이 False 반환(본 확정 흐름 비차단).
    ok = record_confirm_feedback(
        session, query_text="x", intent_json=None, candidates=_cands(),
        adopted_event_ids=[1], manual_added_event_ids=[], low_confidence=False,
    )
    assert ok is False


def test_confirm_and_expand_records(monkeypatch, tmp_path):
    # 확정 흐름이 confirm_feedback을 남기는지(주입형 fake backend 없이 직접 confirm_and_expand).
    from src.agent.repository.bom import EdgeBomRepository
    from src.db.models import BomEdge, DevPartMaster, SourceFile
    from src.ui.agent_client import confirm_and_expand

    monkeypatch.setenv("FEEDBACK_LOG", "1")
    monkeypatch.setenv("SEARCH_INCLUDE_STRUCT", "0")  # 매핑 lazy 경로 비활성(단순화)
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        sf = SourceFile(file_name="f.xlsx", file_hash="h")
        s.add(sf)
        s.commit()
        s.add(DevPartMaster(file_id=sf.file_id, form_id="changing_parts_list_96",
                            part_no_new="A", sheet_name="s", source_row=1))
        s.add(BomEdge(file_id=sf.file_id, parent_pno="A", child_pno="X"))
        s.commit()
        intent = {"raw_text": "모터 변경", "source": "ppt"}
        selections = [{"accept": True, "event_id": 11, "part_no_new_suggested": "A",
                       "source_ref": "f.xlsx"}]
        confirm_and_expand(
            intent=intent, selections=selections, session=s,
            bom_repo=EdgeBomRepository(s),
            candidates=[{"event_id": 11, "score_rrf": 0.04}],
            retrieval_meta={"low_confidence": False},
        )
        fb = s.query(ConfirmFeedback).all()
        assert len(fb) == 1
        assert fb[0].adopted_event_ids == [11]


# ── 읽기 금지: confirm_feedback SELECT는 리포트 CLI 외 어디에도 없다 ────────


def test_confirm_feedback_read_forbidden_outside_cli():
    """src/ 전체에서 confirm_feedback을 SELECT/query하는 코드가 cli.py 외에 없음."""
    src_root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for py in src_root.rglob("*.py"):
        if py.name == "cli.py":  # 리포트 CLI만 읽기 허용
            continue
        text = py.read_text(encoding="utf-8")
        # ConfirmFeedback을 select(...) 하거나 .query(ConfirmFeedback) 하는 읽기 패턴 탐지.
        for marker in ("select(ConfirmFeedback", "query(ConfirmFeedback"):
            if marker in text:
                offenders.append(f"{py.name}:{marker}")
    assert not offenders, f"confirm_feedback 읽기 금지 위반: {offenders}"


def test_feedback_module_no_llm():
    import src.agent.feedback as fb_mod
    src_text = inspect.getsource(fb_mod).lower()
    assert "agent.llm" not in src_text
    assert "ollama" not in src_text
