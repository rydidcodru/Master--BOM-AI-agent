"""D-012 narrativizer 회귀 — 입력 변수명이 팀원 dpm 컬럼명."""

from __future__ import annotations

from src.preprocess.narrativize import build_narrative


def _full_dpm() -> dict:
    return {
        "part_no_new": "AGG74419321",
        "part_name": "Packing Assembly",
        "part_no_base": "AGG74419320",
        "new_model": "WSED7667M.ABMQEUR",
        "region": "EUR",
        "event": "Change",
        "change_point_raw": "도어 힌지 내열 220→240",
        "change_reason_raw": "신규 안전 규제 대응",
        "bom_depth": 1,
        "part_type": "Assy",
    }


def _full_extra() -> dict:
    return {"grade": "Best-1", "event_stage": "DV"}


def test_build_narrative_full_case():
    text = build_narrative(_full_dpm(), _full_extra())
    for token in (
        "AGG74419321",
        "Packing",
        "WSED7667M",
        "EUR",
        "Best-1",
        "AGG74419320",
        "DV",
    ):
        assert token in text, f"missing token: {token}"


def test_build_narrative_no_drbfm_or_hsms():
    """D-011: payload trigger 절 없음."""
    text = build_narrative(_full_dpm(), _full_extra())
    assert "DRBFM" not in text
    assert "HSMS" not in text
    assert "공급사" not in text


def test_build_narrative_missing_optional_fields():
    minimal = {
        "part_no_new": "AGG74419321",
        "part_name": "Packing",
        "new_model": "WSED7667M",
        "event": "New",
    }
    text = build_narrative(minimal, {"grade": "Best-1"})
    assert "AGG74419321" in text
    assert "신규 등록" in text or "New" in text


def test_build_narrative_change_type_carry_over():
    dpm = _full_dpm()
    dpm["event"] = "Carry-over"
    text = build_narrative(dpm, _full_extra())
    assert "유지" in text or "Carry-over" in text


