"""Phase 1 — change_event/change_line 적재(구조 B) 테스트 (SQLite).

검증:
  - 002 가산 컬럼(change_event.reason_embedding 등 / change_line 보존필드 /
    agent_feedback 게이트 컬럼)이 ORM·SQLite에 존재.
  - load_change_events: **변경사유별 분할** 그룹핑(사유가 부품 묶는 키), change_log
    distinct 변경내역 합침, source_ref, reason_embedding=None(임베딩 분리).
  - 같은 사유 다른 모델 → 이벤트 분리. 변경텍스트 없는 행 제외.
  - idempotent 재적재(카운트 불변), 기존 dev_part_master 비파괴.
  - lookup_lines_by_event: seq 순 부품 라인 세트.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load_change_events import load_change_events
from src.db.models import (
    AgentFeedback,
    ChangeEvent,
    ChangeLine,
    DevPartMaster,
    SourceFile,
)
from src.db.retrieve import lookup_lines_by_event


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _file(session, name="change.xlsx", file_hash="h1") -> int:
    sf = SourceFile(file_name=name, file_hash=file_hash)
    session.add(sf)
    session.commit()
    return sf.file_id


def _dpm(session, file_id, **kw):
    defaults = dict(
        form_id="changing_parts_list_96",
        base_model="WSED7667M",
        new_model="WSED7667M.ABMQEUR",
        event="Change",
        sheet_name="변경부품 list",
        source_row=1,
    )
    defaults.update(kw)
    session.add(DevPartMaster(file_id=file_id, **defaults))


# ── 002 가산 컬럼 존재 ─────────────────────────────────────────


def test_additive_columns_exist(session):
    fid = _file(session)
    ev = ChangeEvent(
        file_id=fid,
        base_model="A",
        new_model="B",
        change_log="내열 220→240",
        change_reason="신규 규제",
        reason_embedding=None,
        source_ref="change.xlsx",
    )
    session.add(ev)
    session.flush()
    session.add(
        ChangeLine(
            event_id=ev.event_id,
            seq=1,
            new_pno="AGG74419321",
            part_name="Packing",
            qty_new=1.0,
            classification="Change",
            supplier="ACME",
            mold_yn="Y",
            inhouse_yn="N",
            approval_test_yn="Y",
            source_ref="change.xlsx/변경부품 list/5",
        )
    )
    session.add(
        AgentFeedback(
            session_id="s1",
            part_no="AGG74419321",
            decision="accept",
            change_point="부품 변경",
            new_pno_input="<발번대기>",
            is_anchor=True,
        )
    )
    session.commit()

    got_ev = session.execute(select(ChangeEvent)).scalar_one()
    assert got_ev.change_reason == "신규 규제"
    assert got_ev.reason_embedding is None
    got_line = session.execute(select(ChangeLine)).scalar_one()
    assert got_line.part_name == "Packing"
    assert got_line.mold_yn == "Y"
    got_fb = session.execute(select(AgentFeedback)).scalar_one()
    assert got_fb.is_anchor is True
    assert got_fb.new_pno_input == "<발번대기>"


# ── 사유별 분할 그룹핑 ─────────────────────────────────────────


def test_load_groups_by_reason(session):
    fid = _file(session)
    # 사유 "BLDC 기능 추가"가 3개 부품에 걸침(변경내역은 제각각).
    _dpm(session, fid, part_no_new="P1", change_point_raw="모터 AC→BLDC",
         change_reason_raw="BLDC 기능 추가", source_row=1)
    _dpm(session, fid, part_no_new="P2", change_point_raw="제어 PCB 추가",
         change_reason_raw="BLDC 기능 추가", source_row=2)
    _dpm(session, fid, part_no_new="P3", change_point_raw="하네스 변경",
         change_reason_raw="BLDC 기능 추가", source_row=3)
    # 다른 사유.
    _dpm(session, fid, part_no_new="P4", change_point_raw="재질 변경",
         change_reason_raw="원가 절감", source_row=4)
    _dpm(session, fid, part_no_new="P5", change_point_raw="공급처 변경",
         change_reason_raw="원가 절감", source_row=5)
    # 사유 없고 변경내역만 → 자체 이벤트(fallback).
    _dpm(session, fid, part_no_new="P6", change_point_raw="인쇄 변경",
         change_reason_raw=None, source_row=6)
    # 변경 텍스트 전무 → 제외.
    _dpm(session, fid, part_no_new="P7", change_point_raw=None,
         change_reason_raw=None, source_row=7)
    session.commit()

    res = load_change_events(session, file_ids=[fid])
    # 이벤트: BLDC / 원가절감 / 인쇄(fallback) = 3. 라인 = 3+2+1 = 6.
    assert res.events_inserted == 3
    assert res.lines_inserted == 6

    bldc = session.execute(
        select(ChangeEvent).where(ChangeEvent.change_reason == "BLDC 기능 추가")
    ).scalar_one()
    lines = lookup_lines_by_event(session, bldc.event_id)
    assert {l.new_pno for l in lines} == {"P1", "P2", "P3"}
    # change_log = distinct 변경내역 합침.
    assert "모터 AC→BLDC" in bldc.change_log
    assert "제어 PCB 추가" in bldc.change_log
    # 임베딩은 적재 단계에서 채우지 않음.
    assert bldc.reason_embedding is None

    # fallback 이벤트: change_reason None, change_log = 변경내역.
    printed = session.execute(
        select(ChangeEvent).where(ChangeEvent.change_log == "인쇄 변경")
    ).scalar_one()
    assert printed.change_reason is None


def test_same_reason_different_model_splits(session):
    fid = _file(session)
    _dpm(session, fid, part_no_new="P1", new_model="MODEL_A",
         change_reason_raw="원가 절감", source_row=1)
    _dpm(session, fid, part_no_new="P2", new_model="MODEL_B",
         change_reason_raw="원가 절감", source_row=2)
    session.commit()

    res = load_change_events(session, file_ids=[fid])
    assert res.events_inserted == 2  # 같은 사유라도 모델 다르면 분리


def test_source_ref_on_lines(session):
    fid = _file(session, name="change.xlsx")
    _dpm(session, fid, part_no_new="P1", change_reason_raw="신규 규제",
         sheet_name="변경부품 list", source_row=5)
    session.commit()

    load_change_events(session, file_ids=[fid])
    line = session.execute(select(ChangeLine)).scalar_one()
    assert line.source_ref == "change.xlsx/변경부품 list/5"


# ── idempotent + 비파괴 ────────────────────────────────────────


def test_load_idempotent(session):
    fid = _file(session)
    _dpm(session, fid, part_no_new="P1", change_reason_raw="신규 규제", source_row=1)
    _dpm(session, fid, part_no_new="P2", change_reason_raw="신규 규제", source_row=2)
    session.commit()

    r1 = load_change_events(session, file_ids=[fid])
    r2 = load_change_events(session, file_ids=[fid])
    assert (r1.events_inserted, r1.lines_inserted) == (1, 2)
    assert (r2.events_inserted, r2.lines_inserted) == (1, 2)
    # 누적되지 않음(재적재는 delete-reinsert).
    assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == 1
    assert session.execute(select(func.count()).select_from(ChangeLine)).scalar_one() == 2


def test_load_preserves_dev_part_master(session):
    fid = _file(session)
    _dpm(session, fid, part_no_new="P1", change_reason_raw="신규 규제", source_row=1)
    _dpm(session, fid, part_no_new="P2", change_reason_raw="신규 규제", source_row=2)
    session.commit()
    before = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()

    load_change_events(session, file_ids=[fid])

    after = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()
    assert before == after == 2  # 구조 B 적재가 dev_part_master를 건드리지 않음


def test_load_all_files_when_none(session):
    f1 = _file(session, name="a.xlsx", file_hash="ha")
    f2 = _file(session, name="b.xlsx", file_hash="hb")
    _dpm(session, f1, part_no_new="P1", change_reason_raw="r1", source_row=1)
    _dpm(session, f2, part_no_new="P2", change_reason_raw="r2", source_row=1)
    session.commit()

    res = load_change_events(session)  # file_ids=None → 전체
    assert res.events_inserted == 2
    assert set(res.file_ids) == {f1, f2}
