"""v2.0 정규화 회귀 (NFC/NFKC 차등 + null token + range)."""

from __future__ import annotations

import unicodedata

from src.preprocess.normalize import normalize_core, normalize_semantic, normalize_value


def test_null_token_drops_to_none():
    res = normalize_value("-", "change_point")
    assert res.value is None


def test_part_no_nfkc_full_width_strip():
    # 전각 영문 → 반각 변환 + strip
    res = normalize_value(" ag-74 19 ", "part_no")
    # 정상화 후 axiom 검증은 실패할 수 있음 → success or set_null
    assert res.value != " ag-74 19 "


def test_change_type_alias_canonicalized():
    res = normalize_value("신규", "change_type")
    assert res.value == "New"


def test_macos_jamo_nfc_recovery():
    # macOS 자모분리 ("ㅎ", "ㅏ" 등이 분리) → NFC로 결합
    decomposed = unicodedata.normalize("NFD", "패킹")
    res = normalize_value(decomposed, "change_point")
    assert res.value == "패킹"


def test_bom_level_range_check_set_null():
    res = normalize_value(99, "bom_level")
    # on_fail set_null → None
    assert res.value is None


def test_normalize_core_returns_failures():
    core = {
        "part_no": "AGG74419321",
        "change_type": "신규",
        "grade": "Best1",
        "change_point": "내열 강화",
    }
    out, fails = normalize_core(core)
    assert out["change_type"] == "New"
    assert out["grade"] == "Best-1"
    assert not fails  # 모두 통과


def test_normalize_semantic_strips_collapses():
    out = normalize_semantic({"DRBFM > 변경점": "  내열   220→240   "})
    assert out["DRBFM > 변경점"] == "내열 220→240"
