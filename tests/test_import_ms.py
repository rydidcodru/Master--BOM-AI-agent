"""ms tagged import — source_project 태깅·raw_json 보존·change_event 전파·멱등·검색 필터."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.import_ms import import_dpm_rows
from src.db.models import ChangeEvent, ChangeLine, DevPartMaster, SourceFile
from src.db.retrieve import _build_event_filter_sql


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _ms_row(**kw):
    base = {
        "sheet_name": "변경부품", "source_row": 1, "region": "EUR",
        "base_model": "BM1", "new_model": "NM1", "event": "Change",
        "bom_level_raw": "1", "bom_depth": 1, "part_type": "Assy",
        "part_no_base": None, "part_no_new": "AEC74157606", "part_name": "Motor, BLDC",
        "qty_base": None, "qty_new": 1.0, "change_point_raw": "AC→BLDC",
        "change_reason_raw": "BLDC 모터 적용", "supplier": "LGE",
        "classification": "Change", "extra_fields": {"part_grade": "S"},
        "embedding_text": "부품 Motor BLDC",
    }
    base.update(kw)
    return base


def test_import_tags_source_project_and_propagates(session):
    rows = [
        _ms_row(source_row=1, part_no_new="AEC74157606"),
        _ms_row(source_row=2, part_no_new="MFL71927538", part_name="Plate"),
    ]
    res = import_dpm_rows(session, rows, source_project="ms")
    assert res.dpm_inserted == 2
    # 같은 (base,new,reason) → 1 event, 2 lines
    assert res.events_inserted == 1 and res.lines_inserted == 2

    dpms = session.execute(select(DevPartMaster)).scalars().all()
    assert {d.source_project for d in dpms} == {"ms"}
    # raw_json에 원본 행 전체 보존
    d0 = next(d for d in dpms if d.part_no_new == "AEC74157606")
    assert d0.raw_json["part_name"] == "Motor, BLDC"
    assert d0.raw_json["change_reason_raw"] == "BLDC 모터 적용"

    # change_event도 source_project='ms' 전파
    evs = session.execute(select(ChangeEvent)).scalars().all()
    assert {e.source_project for e in evs} == {"ms"}


def test_import_is_idempotent(session):
    rows = [_ms_row(source_row=1), _ms_row(source_row=2, part_no_new="MFL71927538")]
    r1 = import_dpm_rows(session, rows, source_project="ms")
    r2 = import_dpm_rows(session, rows, source_project="ms")  # 재실행 — 기존 삭제 후 재적재
    assert r2.dpm_inserted == 2
    # 누적 안 됨: dpm 2, source_files 1, change_event 1
    assert session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one() == 2
    assert session.execute(select(func.count()).select_from(SourceFile)).scalar_one() == 1
    assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == 1
    assert r2.dpm_inserted == r1.dpm_inserted  # 재적재해도 누적 없이 동일 수


def test_lg_rows_default_to_lg_project(session):
    sf = SourceFile(file_name="lg.xlsx", file_hash="h_lg")
    session.add(sf)
    session.commit()
    session.add(DevPartMaster(file_id=sf.file_id, part_no_new="LG1", sheet_name="s", source_row=1))
    session.commit()
    d = session.execute(select(DevPartMaster).where(DevPartMaster.part_no_new == "LG1")).scalar_one()
    assert d.source_project == "lg"  # 모델 default


def test_event_filter_sql_source_project():
    sql, params = _build_event_filter_sql(source_project="ms")
    assert "source_project = :source_project" in sql
    assert params["source_project"] == "ms"
    # None이면 필터 없음(production = 전체)
    sql2, params2 = _build_event_filter_sql()
    assert "source_project" not in sql2 and "source_project" not in params2
