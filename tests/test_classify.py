"""v2.0 시트 단위 분류기 회귀 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.preprocess.classify import classify_dir, classify_file


def test_changing_parts_96_classified(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_changing_parts_96.xlsx"
    result, sheet_results = classify_file(file)
    assert result.form_version.startswith("변경부품_list_")
    assert any(s.form_id.startswith("변경부품_list_") for s in sheet_results)


def test_changing_parts_91_classified(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_changing_parts_91.xlsx"
    result, sheet_results = classify_file(file)
    sub = [s.form_id for s in sheet_results if s.form_id.startswith("변경부품_list_")]
    assert sub and any(v == "변경부품_list_91" for v in sub)


def test_new_parts_list_classified(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_new_parts_list_75.xlsx"
    result, _ = classify_file(file)
    assert result.form_version == "신규부품리스트_75"


def test_bom_ag_grid_classified(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_bom_ag_grid_36.xlsx"
    result, _ = classify_file(file)
    assert result.form_version == "BOM_ag_grid_36"


# D-011: test_activity_master_classified 제거 — 어댑터 + form 룰 함께 삭제됨.


def test_classify_dir_distributes(fixture_workbooks: Path):
    results = classify_dir(fixture_workbooks)
    form_versions = {r.form_version for r in results}
    # 적어도 changing_parts와 신규부품리스트와 bom은 식별돼야 함
    assert any(v.startswith("변경부품_list_") for v in form_versions)
    assert "신규부품리스트_75" in form_versions
    assert "BOM_ag_grid_36" in form_versions
