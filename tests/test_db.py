"""D-012 DB ORM smoke test — SQLite로 dev_part_master 적재 + 롤백 검증."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run
from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.db.rollback import rollback_file


@pytest.fixture()
def session(tmp_path):
    db_path = tmp_path / "test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _write_committed_artifacts(
    run_dir: Path,
    row_records: list[dict] | None = None,
    files: list[dict] | None = None,
    logs: list[dict] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    files = files or [
        {
            "file_path": "/abs/x.xlsx",
            "file_name": "x.xlsx",
            "file_hash": "h_xlsx_1",
            "file_size": 100,
            "region": "EUR",
        }
    ]
    logs = logs or [
        {
            "file_name": "x.xlsx",
            "sheet_name": "변경부품 list",
            "form_id": "changing_parts_list_96",
            "rows_total": len(row_records or []),
            "rows_inserted": len(row_records or []),
            "status": "ok",
            "error_message": None,
        }
    ]
    (run_dir / "files.json").write_text(
        json.dumps(files, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "ingestion_log.json").write_text(
        json.dumps(logs, ensure_ascii=False), encoding="utf-8"
    )
    if row_records:
        df = pd.DataFrame(row_records)
        # extra_fields를 JSON 문자열로 직렬화 (pipeline.py와 동일 처리)
        if "extra_fields" in df.columns:
            df["extra_fields"] = df["extra_fields"].apply(
                lambda v: json.dumps(v, ensure_ascii=False) if v is not None else None
            )
        df.to_parquet(run_dir / "rows.parquet", index=False)


def _base_row(**overrides):
    base = {
        "part_no_new": "AGG74419321",
        "part_name": "Packing",
        "part_no_base": "AGG74419320",
        "base_model": "WSED7667M",
        "new_model": "WSED7667M.ABMQEUR",
        "region": "EUR",
        "event": "Change",
        "change_point_raw": "내열 220→240",
        "change_reason_raw": "신규 규제",
        "bom_depth": 1,
        "bom_level_raw": "1",
        "part_type": "Assy",
        "qty_new": 1.0,
        "extra_fields": {"grade": "Best-1", "event_stage": "DV"},
        "embedding_text": "변경부품 AGG74419321...",
        "form_id": "changing_parts_list_96",
        "source_file": "x.xlsx",
        "source_sheet": "변경부품 list",
        "source_row": 5,
        "_quarantine_reason": None,
    }
    base.update(overrides)
    return base


def test_load_inserts_all_three_tables(session, tmp_path):
    run_dir = tmp_path / "committed" / f"run_{uuid.uuid4().hex[:8]}"
    _write_committed_artifacts(run_dir, row_records=[_base_row()])

    result = load_run(session, run_dir)
    # load_run은 적재 후 bom_edge 백필(구조 A, 비-BOM 행이라 0) + change_event/change_line
    # 백필(구조 B, search_events 검색 인덱스 코퍼스)도 수행한다. 변경행 1건 → event/line 각 1.
    assert result.rows_inserted == {
        "source_files": 1,
        "ingestion_log": 1,
        "dev_part_master": 1,
        "bom_edge": 0,
        "change_event": 1,
        "change_line": 1,
    }
    sf = session.execute(select(func.count()).select_from(SourceFile)).scalar_one()
    log = session.execute(select(func.count()).select_from(IngestionLog)).scalar_one()
    dpm = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()
    assert (sf, log, dpm) == (1, 1, 1)

    sample = session.execute(select(DevPartMaster)).scalar_one()
    assert sample.part_no_new == "AGG74419321"
    assert sample.extra_fields == {"grade": "Best-1", "event_stage": "DV"}
    assert sample.embedding_text.startswith("변경부품")


def test_quarantine_rows_excluded(session, tmp_path):
    """_quarantine_reason 있는 행은 dev_part_master에 들어가지 않는다."""
    run_dir = tmp_path / "committed" / f"run_{uuid.uuid4().hex[:8]}"
    _write_committed_artifacts(
        run_dir,
        row_records=[
            _base_row(),
            _base_row(part_no_new="INVALID", _quarantine_reason="part_no=axiom failed"),
        ],
    )
    result = load_run(session, run_dir)
    assert result.rows_inserted["dev_part_master"] == 1


def test_file_hash_dedup_avoids_duplicate_source_files(session, tmp_path):
    """동일 file_hash 두 번 적재해도 source_files는 1행만."""
    run1 = tmp_path / "committed" / "run1"
    run2 = tmp_path / "committed" / "run2"
    _write_committed_artifacts(run1, row_records=[_base_row()])
    _write_committed_artifacts(run2, row_records=[_base_row(source_row=7)])

    load_run(session, run1)
    load_run(session, run2)

    sf = session.execute(select(func.count()).select_from(SourceFile)).scalar_one()
    log = session.execute(select(func.count()).select_from(IngestionLog)).scalar_one()
    dpm = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()
    assert sf == 1  # dedup
    assert log == 2  # 매 run마다 추가
    assert dpm == 2


def test_reload_same_run_is_idempotent(session, tmp_path):
    """동일 committed run 재적재 시 (file_id, sheet, source_row) 중복 행은 스킵된다 (ms 차용)."""
    run_dir = tmp_path / "committed" / "ridem"
    _write_committed_artifacts(
        run_dir, row_records=[_base_row(source_row=5), _base_row(part_no_new="MFZ1", source_row=6)]
    )

    r1 = load_run(session, run_dir)
    assert r1.rows_inserted["dev_part_master"] == 2
    assert r1.rows_skipped.get("dev_part_master", 0) == 0

    r2 = load_run(session, run_dir)  # 같은 run 재적재
    assert r2.rows_inserted["dev_part_master"] == 0   # 신규 0
    assert r2.rows_skipped["dev_part_master"] == 2     # 둘 다 스킵

    # dpm 총행수는 2로 유지(중복 미적재). source_files도 1(file_hash dedup).
    assert session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one() == 2
    assert session.execute(select(func.count()).select_from(SourceFile)).scalar_one() == 1

    # 2번째 ingestion_log에 rows_skipped 기록.
    skipped = [
        l.rows_skipped
        for l in session.execute(select(IngestionLog).order_by(IngestionLog.log_id)).scalars().all()
    ]
    assert skipped == [None, 2]  # 1차 적재 None, 2차 재적재 2건 스킵


def test_distinct_source_row_not_skipped(session, tmp_path):
    """source_row가 다르면(고유키 다름) 스킵하지 않고 정상 적재 — 멱등성이 과적 스킵 안 함."""
    run1 = tmp_path / "committed" / "d1"
    run2 = tmp_path / "committed" / "d2"
    _write_committed_artifacts(run1, row_records=[_base_row(source_row=5)])
    _write_committed_artifacts(run2, row_records=[_base_row(source_row=9)])  # 다른 행
    load_run(session, run1)
    r2 = load_run(session, run2)
    assert r2.rows_inserted["dev_part_master"] == 1
    assert r2.rows_skipped.get("dev_part_master", 0) == 0
    assert session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one() == 2


def test_rollback_file_cascades_to_log_and_dpm(session, tmp_path):
    run_dir = tmp_path / "committed" / "r1"
    _write_committed_artifacts(
        run_dir, row_records=[_base_row(), _base_row(source_row=6)]
    )
    result = load_run(session, run_dir)
    file_id = result.file_ids[0]

    rb = rollback_file(session, file_id)
    assert rb.rows_deleted == {
        "source_files": 1,
        "ingestion_log": 1,
        "dev_part_master": 2,
    }
    # 전체 0
    assert session.execute(select(func.count()).select_from(SourceFile)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(IngestionLog)).scalar_one() == 0
    assert session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one() == 0


def test_rollback_unknown_file_id_raises(session):
    with pytest.raises(ValueError, match="unknown file_id"):
        rollback_file(session, 99999)


def test_legacy_rollback_run_raises_not_implemented(session):
    """D-012: run_id 기반 롤백은 사라짐. 호출 시 NotImplementedError."""
    from src.db.rollback import rollback_run

    with pytest.raises(NotImplementedError):
        rollback_run(session, "some_run_id")
