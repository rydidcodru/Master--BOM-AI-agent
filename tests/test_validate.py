"""D-012 검증 모듈 회귀 — dpm 컬럼명 (part_no_new 등) 기준."""

from __future__ import annotations

import json

import pandas as pd

from src.preprocess.validate import THRESHOLDS, validate_dataframe


def test_validate_minimal_acceptable():
    """팀원 dpm 컬럼 채워진 깨끗한 1행이 모든 지표 통과."""
    df = pd.DataFrame(
        [
            {
                "part_no_new": "AGG74419321",
                "part_name": "Packing",
                "new_model": "WSED7667M.ABMQEUR",
                "event": "Change",
                "part_no_base": "AGG74419320",
                "base_model": "WSED7667M",
                "region": "EUR",
                "change_point_raw": "내열 220→240",
                "change_reason_raw": "신규 규제",
                "bom_depth": 1,
                "part_type": "Assy",
                "extra_fields": json.dumps({"grade": "Best-1"}),
            }
        ]
    )
    df["bom_depth"] = df["bom_depth"].astype("Int64")
    report = validate_dataframe(df, run_id="test_run", rows_in=1)
    assert not report.critical_failures(), (
        f"unexpected failures: {report.critical_failures()}"
    )


def test_quarantine_in_axiom_rate():
    df = pd.DataFrame(
        [
            {
                "part_no_new": "OK1234567",
                "part_name": "x",
                "_quarantine_reason": None,
            },
            {
                "part_no_new": "BAD",
                "part_name": "y",
                "_quarantine_reason": "part_no_new=axiom failed",
            },
        ]
    )
    report = validate_dataframe(df, run_id="x", rows_in=2)
    assert report.axiom_violation_rate == 0.5


def test_thresholds_keys():
    expected = {
        "column_match",
        "type_match",
        "value_format_match",
        "row_preservation",
        "null_rate_required_max",
        "axiom_violation_rate_max",
    }
    assert set(THRESHOLDS.keys()) == expected
