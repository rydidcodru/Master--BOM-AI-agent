"""raw_json 출처보존 (ms BomLine.rawJson 차용).

검증: build_raw_json이 매핑+미매핑(extra_fields 평탄화) 원본 행 전체를 보존하고 내부
부기/임베딩 컬럼은 제외, NaN→None. _build_dpm_row가 raw_json을 채우고 raw_value가 매핑
컬럼까지 복구(extra_fields는 미매핑만이라 복구 불가).
"""

from __future__ import annotations

import math

import pandas as pd

from src.db.load import _build_dpm_row, build_raw_json, raw_value


def _row(**kw) -> pd.Series:
    return pd.Series(kw)


def test_build_raw_json_includes_mapped_and_extra():
    row = _row(
        part_no_new="AGG74419321",
        part_name="Motor, BLDC",
        change_reason_raw="내열 강화",
        extra_fields={"part_grade": "S", "designer": "홍길동"},
        source_row=12,
        embedding_text="부품: Motor / 변경사유: 내열 강화",  # 제외 대상
        _quarantine_reason=float("nan"),  # 내부 부기 — 제외
    )
    rj = build_raw_json(row)
    # 매핑 컬럼 보존
    assert rj["part_no_new"] == "AGG74419321"
    assert rj["part_name"] == "Motor, BLDC"
    # extra_fields 평탄화 보존
    assert rj["part_grade"] == "S" and rj["designer"] == "홍길동"
    # 부기/임베딩 컬럼 제외
    assert "embedding_text" not in rj
    assert "_quarantine_reason" not in rj
    assert "extra_fields" not in rj  # 평탄화했으므로 원본 nested 키는 없음


def test_build_raw_json_nan_dropped_and_empty_none():
    row = _row(part_no_new="X1", qty_new=float("nan"), part_name=None)
    rj = build_raw_json(row)
    assert rj == {"part_no_new": "X1"}  # NaN/None 제거
    assert build_raw_json(_row(a=float("nan"), b=None)) is None  # 전부 비면 None


def test_build_raw_json_mapped_overrides_extra_on_collision():
    # extra_fields에 같은 키가 있어도 매핑 컬럼(정본)이 우선.
    row = _row(part_name="정본", extra_fields={"part_name": "잔여"})
    assert build_raw_json(row)["part_name"] == "정본"


def test_build_dpm_row_populates_raw_json_and_recovery():
    row = _row(
        part_no_new="MFZ67394702",
        part_name="Panel, Control",
        extra_fields={"part_grade": "A"},
        source_sheet="Sheet1",
        source_row=7,
        form_id="changing_parts_list_96",
        embedding_text="...",
    )
    dpm = _build_dpm_row(row, file_id=1)
    assert dpm.raw_json is not None
    assert dpm.raw_json["part_name"] == "Panel, Control"
    assert dpm.raw_json["part_grade"] == "A"
    assert dpm.extra_fields == {"part_grade": "A"}  # 기존 동작 비파괴

    # raw_value: 매핑 컬럼(part_name)은 extra_fields엔 없지만 raw_json에서 복구됨.
    assert raw_value(dpm, "part_name") == "Panel, Control"
    assert raw_value(dpm, "part_grade") == "A"
    assert raw_value(dpm, "없는키") is None


def test_raw_value_falls_back_to_extra_fields_when_no_raw_json():
    from src.db.models import DevPartMaster

    dpm = DevPartMaster(file_id=1, raw_json=None, extra_fields={"designer": "김"})
    assert raw_value(dpm, "designer") == "김"
    assert raw_value(dpm, "part_name") is None
