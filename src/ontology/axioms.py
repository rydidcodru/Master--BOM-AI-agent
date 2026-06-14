"""v2.0 Step 2 — 결정론적 도메인 axiom (config-driven).

preprocessing_v2.md §4-2 + §11 기반. 검증 규칙은 모두 ``config/axioms.yaml``
에서 로드한다. 코드 변경 없이 튜닝 가능. LLM 호출 없음.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, cast

import yaml

from src.utils.paths import AXIOMS_PATH


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    """Load and cache the axioms config."""
    return cast(dict[str, Any], yaml.safe_load(AXIOMS_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=8)
def _pattern(key: str) -> re.Pattern[str]:
    """Compile and cache a regex pattern from the config."""
    section: dict[str, Any] = _config()[key]
    return re.compile(str(section["pattern"]))


# --- Normalizers ----------------------------------------------------------


def normalize_part_no(value: str) -> str:
    """식별자 정규화: NFC→NFKC → strip → uppercase → 공백/하이픈/언더스코어 제거.

    Args:
        value: Raw part-number string.

    Returns:
        The normalized part number.
    """
    if value is None:
        return value
    nfc = unicodedata.normalize("NFC", str(value))
    nfkc = unicodedata.normalize("NFKC", nfc)
    return re.sub(r"[\s\-_]", "", nfkc.strip()).upper()


def normalize_model_code(value: str) -> str:
    """모델코드 정규화: NFC→NFKC → strip → uppercase → 개행/탭 → 단일 공백.

    실데이터에 ``"WSED7667M / A \\n(EUR)"`` 같이 개행/marker 포함 모델코드 등장.
    개행/탭은 단일 공백으로, 다중 공백은 단일 공백으로 정리.
    """
    if value is None:
        return value
    nfc = unicodedata.normalize("NFC", str(value))
    nfkc = unicodedata.normalize("NFKC", nfc)
    # 개행/탭 → 공백, 다중 공백 → 단일
    cleaned = re.sub(r"[\r\n\t]+", " ", nfkc)
    cleaned = re.sub(r" +", " ", cleaned)
    return cleaned.strip().upper()


def normalize_change_type(value: str) -> str | None:
    """change_type 라벨을 alias dict 통해 canonical form으로.

    Returns:
        canonical change type, or None if unrecognized.
    """
    if value is None:
        return None
    section: dict[str, Any] = _config()["change_type"]
    cleaned = unicodedata.normalize("NFC", str(value)).strip()
    if cleaned in section["allowed"]:
        return cleaned
    return cast(str | None, section.get("aliases", {}).get(cleaned))


def normalize_grade(value: str) -> str | None:
    """Grade 라벨 alias 해소."""
    if value is None:
        return None
    section: dict[str, Any] = _config()["grade"]
    cleaned = unicodedata.normalize("NFC", str(value)).strip()
    if cleaned in section["allowed"]:
        return cleaned
    return cast(str | None, section.get("aliases", {}).get(cleaned))


def normalize_part_type(value: str) -> str | None:
    """part_type 라벨 alias 해소."""
    if value is None:
        return None
    section: dict[str, Any] = _config().get("part_type", {})
    if not section:
        return None
    cleaned = unicodedata.normalize("NFC", str(value)).strip()
    if cleaned in section.get("allowed", []):
        return cleaned
    return cast(str | None, section.get("aliases", {}).get(cleaned))


# --- Validators -----------------------------------------------------------


def validate_part_no(value: str) -> bool:
    """부품번호 패턴 검증 (정규화 후)."""
    if value is None or not str(value).strip():
        return False
    return bool(_pattern("part_no").match(normalize_part_no(value)))


def validate_model_code(value: str) -> bool:
    """모델코드 패턴 검증."""
    if value is None or not str(value).strip():
        return False
    return bool(_pattern("model_code").match(normalize_model_code(value)))


def validate_change_type(value: str) -> bool:
    return normalize_change_type(value) is not None


def validate_grade(value: str) -> bool:
    return normalize_grade(value) is not None


def validate_event_stage(value: str) -> bool:
    if value is None:
        return False
    allowed: list[str] = _config()["event_stage"]["allowed"]
    return unicodedata.normalize("NFC", str(value)).strip().upper() in allowed


def validate_bom_level(value: int) -> bool:
    section: dict[str, Any] = _config()["bom_level"]
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False
    return int(section["min"]) <= v <= int(section["max"])


def validate_region(value: str) -> bool:
    if value is None:
        return False
    allowed: list[str] = _config()["region"]["allowed"]
    return unicodedata.normalize("NFC", str(value)).strip().upper() in allowed


# --- Parsers --------------------------------------------------------------


_GRADE_FROM_SHEET = re.compile(
    r"(Best|Better|Good)\s*-?\s*([12])(?:\s*(BK|STS))?",
    re.IGNORECASE,
)


# BK/STS suffix family 매핑 — 시트명이 "Best STS" 같은 경우도 정확 분리.
# B-6 fix: 단순히 "BK"+"STS" 둘 다 있다고 Good-1 BK로 매핑하던 광범위 로직 제거.
_BK_STS_FAMILY_RE = re.compile(
    r"(Best|Better|Good)\s*-?\s*([12])?\s*(BK|STS|BK\s*STS|STS\s*BK)",
    re.IGNORECASE,
)
# family 없이 "BK STS" 단독인 경우의 historical fallback.
_LONE_BK_STS_RE = re.compile(r"\bBK\s*STS\b|\bSTS\s*BK\b", re.IGNORECASE)


def parse_grade_from_sheet_name(name: str) -> str | None:
    """시트명에서 grade 추출.

    예:
        "변경부품 list_Best1"          → "Best-1"
        "변경부품 list (BK STS)"       → "Good-1 BK" (family 미명시 + BK/STS 둘 다 → historical fallback)
        "Master Best STS"              → "Best-1 STS"
        "변경부품 list (Better-2 BK)"  → "Better-2 BK" → (없는 alias) fallback "Better-2"
        "Master(Best)"                 → "Best-1" (기본 1)
        "Good-1"                       → "Good-1"

    B-6: family + BK/STS 동시 매칭을 우선 처리. family 없는 "BK STS" 단독은
    historical fallback (Good-1 BK)으로만 처리.
    """
    if not name:
        return None
    cleaned = unicodedata.normalize("NFC", str(name))

    # 1) family + rank + (옵셔널 BK/STS) 명시된 경우 — 가장 정확.
    m = _GRADE_FROM_SHEET.search(cleaned)
    if m:
        family = m.group(1).title()  # Best/Better/Good
        rank = m.group(2)
        suffix = m.group(3)
        base = f"{family}-{rank}"
        if suffix:
            base = f"{base} {suffix.upper()}"
        return normalize_grade(base) or base

    # 2) family + suffix만 있고 rank 없음 (예: "Best STS", "Better BK")
    m2 = _BK_STS_FAMILY_RE.search(cleaned)
    if m2:
        family = m2.group(1).title()
        rank = m2.group(2) or "1"  # rank 미명시 → 기본 1
        suffix_raw = m2.group(3).upper().replace(" ", "")
        # "BKSTS" 또는 "STSBK"는 BK 우선 (사용자 합의 시 변경 가능)
        suffix = "BK" if "BK" in suffix_raw else "STS"
        base = f"{family}-{rank} {suffix}"
        return normalize_grade(base) or base

    # 3) family 없이 "BK STS" 단독 — historical fallback.
    if _LONE_BK_STS_RE.search(cleaned):
        return normalize_grade("Good-1 BK")

    # 4) "Master(Best)" 같은 family-only 케이스 — 기본 rank 1로 추정.
    for family in ("Best", "Better", "Good"):
        if family in cleaned and not re.search(rf"{family}\s*-?\s*[12]", cleaned):
            return f"{family}-1"

    return None


def region_from_buyer(buyer_code: str) -> str | None:
    """buyer 코드에서 region 추출 (config buyer_to_region)."""
    if not buyer_code:
        return None
    mapping: dict[str, str] = _config().get("buyer_to_region", {})
    return mapping.get(str(buyer_code).strip().upper())
