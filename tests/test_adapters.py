"""D-012 양식 어댑터 회귀 — ExtractedRow 신규 shape 검증."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.adapters import (
    extract_bom_ag_grid,
    extract_changing_parts_list_family,
    extract_new_parts_list_75,
    extract_sheet,
)
from src.utils.excel import read_workbook


def test_changing_parts_extracts_dpm_fields(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_changing_parts_96.xlsx"
    sheets = read_workbook(file)
    rows = list(extract_changing_parts_list_family(file, sheets[0]))
    assert len(rows) >= 1
    first = rows[0]
    # 신규 dpm 컬럼명 (D-012)
    assert first.dev_part_master_fields.get("part_no_new") == "AGG74419321"
    assert first.dev_part_master_fields.get("part_name") == "Packing Assembly"
    assert first.dev_part_master_fields.get("change_point_raw")
    # extra_fields: Core 13 매핑 안 된 헤더 + grade 잔여
    assert "grade" in first.extra_fields
    # source_meta
    assert first.source_meta["form_id"].startswith("changing_parts_list_")


def test_new_parts_list_extracts_rows(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_new_parts_list_75.xlsx"
    sheets = read_workbook(file)
    sheet = next(s for s in sheets if s.name == "신규부품리스트")
    rows = list(extract_new_parts_list_75(file, sheet))
    assert rows, "신규부품리스트에서 최소 1행 추출"
    assert rows[0].dev_part_master_fields.get("part_no_new")


def test_bom_ag_grid_emits_dpm_rows_with_bom_depth(fixture_workbooks: Path):
    """D-012: BOM 어댑터도 ExtractedRow 스트림. bom_depth + bom_level_raw 채움."""
    file = fixture_workbooks / "fixture_bom_ag_grid_36.xlsx"
    sheets = read_workbook(file)
    rows = list(extract_bom_ag_grid(file, sheets[0], {"run_id": "test_run"}))
    assert len(rows) > 0
    # 최소 한 행은 bom_depth 정수 + bom_level_raw 문자
    assert any(r.dev_part_master_fields.get("bom_depth") is not None for r in rows)
    # parent 정보는 extra_fields에 보존
    assert any("bom_parent_part_no" in r.extra_fields for r in rows)
    # event 컬럼은 BOM 부품에 대해 비어야 함 (변경 이벤트가 아님)
    assert all(r.dev_part_master_fields.get("event") is None for r in rows)


def test_dispatcher_routes_legacy_and_new_form_ids(fixture_workbooks: Path):
    """form_signatures.yaml의 한국어 이름과 신규 영어 이름 모두 인식."""
    file = fixture_workbooks / "fixture_changing_parts_96.xlsx"
    sheets = read_workbook(file)
    out_legacy = extract_sheet(file, sheets[0], "변경부품_list_96", {"run_id": "r1"})
    out_new = extract_sheet(file, sheets[0], "changing_parts_list_96", {"run_id": "r1"})
    assert out_legacy and out_legacy[0].dev_part_master_fields.get("part_no_new")
    assert out_new and out_new[0].dev_part_master_fields.get("part_no_new")


def test_bom_ag_grid_handles_short_rows(tmp_path):
    """parent col 위치보다 짧은 행도 IndexError 안 남 (D-011 B-1 회귀 유지)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ag-grid"
    headers = ["P/No", "Desc.", "Lvl", "Qty"] + [f"c{i}" for i in range(4, 35)] + ["부모 P/No"]
    ws.append(headers)
    ws.append(["AGP0000001", "Part 1", 1, 1.0] + [None] * 31 + ["AGP0000000"])
    ws.append(["AGP0000002", "Part 2", 2, 1.0])
    for i in range(100):
        ws.append([f"AGP{i:07d}", f"P {i}", 1, 1.0])
    path = tmp_path / "short_bom.xlsx"
    wb.save(path)

    sheets = read_workbook(path)
    rows = list(extract_bom_ag_grid(path, sheets[0], {"run_id": "test"}))
    assert len(rows) > 0
