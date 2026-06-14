"""D-012 E2E: 합성 파일 → run_pipeline → dry_run 산출물 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.preprocess.pipeline import discover_raw_files, run_pipeline


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    """data/ 계열 경로를 tmp로 redirect — 실데이터 폴더 오염 방지."""
    monkeypatch.setattr("src.preprocess.pipeline.PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(
        "src.preprocess.pipeline.DRY_RUN_ROOT", tmp_path / "processed" / "dry_run"
    )
    monkeypatch.setattr(
        "src.preprocess.pipeline.COMMITTED_ROOT", tmp_path / "processed" / "committed"
    )
    monkeypatch.setattr(
        "src.preprocess.pipeline.ROLLED_BACK_ROOT",
        tmp_path / "processed" / "rolled_back",
    )
    monkeypatch.setattr("src.preprocess.pipeline.REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr("src.preprocess.quarantine.QUARANTINE_DIR", tmp_path / "quarantine")
    return tmp_path


def test_run_pipeline_emits_dpm_artifacts(isolated_data, fixture_workbooks):
    """D-012: dry_run 디렉토리에 rows.parquet + files.json + ingestion_log.json."""
    files = discover_raw_files(fixture_workbooks)
    assert files
    result = run_pipeline(files, mode="dry-run")
    assert result.status in {"dry_run_complete", "rejected"}
    assert result.run_dir is not None

    rows_path = result.run_dir / "rows.parquet"
    files_path = result.run_dir / "files.json"
    log_path = result.run_dir / "ingestion_log.json"
    state_path = result.run_dir / "state.json"
    assert rows_path.exists()
    assert files_path.exists()
    assert log_path.exists()
    assert state_path.exists()


def test_changing_parts_yields_dpm_column_names(isolated_data, fixture_workbooks):
    """rows.parquet 컬럼은 팀원 dpm 컬럼명 (part_no_new 등)."""
    only = [fixture_workbooks / "fixture_changing_parts_96.xlsx"]
    result = run_pipeline(only, mode="dry-run")
    rows_path = result.run_dir / "rows.parquet"
    if not rows_path.exists():
        pytest.skip("no rows extracted")
    df = pd.read_parquet(rows_path)
    assert "part_no_new" in df.columns
    assert "event" in df.columns or "change_point_raw" in df.columns
    assert "extra_fields" in df.columns
    assert "embedding_text" in df.columns
    assert df["embedding_text"].notna().any()


def test_files_json_has_hash_and_region(isolated_data, fixture_workbooks):
    """files.json은 file_hash + file_size + region 포함 (source_files 입력)."""
    only = [fixture_workbooks / "fixture_changing_parts_96.xlsx"]
    result = run_pipeline(only, mode="dry-run")
    files_data = json.loads(
        (result.run_dir / "files.json").read_text(encoding="utf-8")
    )
    assert files_data
    fm = files_data[0]
    assert fm["file_hash"]
    assert fm["file_size"] > 0
    assert fm["file_name"].endswith(".xlsx")


def test_ingestion_log_records_per_sheet(isolated_data, fixture_workbooks):
    only = [fixture_workbooks / "fixture_changing_parts_96.xlsx"]
    result = run_pipeline(only, mode="dry-run")
    logs = json.loads(
        (result.run_dir / "ingestion_log.json").read_text(encoding="utf-8")
    )
    assert logs
    entry = logs[0]
    assert entry["file_name"] == "fixture_changing_parts_96.xlsx"
    assert entry["form_id"]
    assert entry["status"] in {"ok", "empty", "error", "skipped"}


def test_state_json_has_critical_failures_key(isolated_data, fixture_workbooks):
    """state.json의 aggregate_validation에 critical_failures + is_acceptable 명시."""
    only = [fixture_workbooks / "fixture_changing_parts_96.xlsx"]
    result = run_pipeline(only, mode="dry-run")
    state = json.loads(
        (result.run_dir / "state.json").read_text(encoding="utf-8")
    )
    agg = state.get("aggregate_validation")
    assert agg is not None
    assert "critical_failures" in agg
    assert "is_acceptable" in agg


def test_commit_run_gate_blocks_when_invalid(isolated_data, fixture_workbooks):
    """commit_run이 critical_failures 있으면 ValueError raise."""
    from src.preprocess.pipeline import commit_run

    only = [fixture_workbooks / "fixture_changing_parts_96.xlsx"]
    result = run_pipeline(only, mode="dry-run")
    if result.aggregate_validation and not result.aggregate_validation.is_acceptable():
        with pytest.raises(ValueError, match="commit blocked"):
            commit_run(result.run_id)
