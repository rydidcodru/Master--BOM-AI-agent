"""v2.0 column_dictionary 로더 + lookup helper.

preprocessing_v2.md §11. 헤더 path → Core 필드 매핑. 운영 중 성장형 자산.

매칭 우선순위:
  1. exact (대소문자 + 공백 정규화 후 일치)
  2. fuzzy (Levenshtein ≥ 0.85) — rapidfuzz로 계산
  3. (TODO v1.5) semantic embedding 매칭

매칭 안 되는 헤더는 ``needs_review_queue``에 등록 후 payload에는 보존.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml
from rapidfuzz import fuzz

from src.utils.paths import COLUMN_DICTIONARY_PATH

_NORMALIZE_WS = re.compile(r"\s+")


def _normalize_header(s: str) -> str:
    """헤더 비교용 정규화: NFC → strip → 다중공백→단일 → lower."""
    if s is None:
        return ""
    nfc = unicodedata.normalize("NFC", str(s))
    return _NORMALIZE_WS.sub(" ", nfc.strip()).lower()


@dataclass
class FieldEntry:
    """한 Core 필드의 매핑 사전 entry (D-011 Phase E 후)."""

    field_name: str
    exact: list[str] = field(default_factory=list)
    fuzzy_keywords: list[str] = field(default_factory=list)
    is_required: bool = False
    sheet_meta_path: dict[str, int] | None = None
    sheet_name_pattern: str | None = None
    cell_value_mapping: dict[str, str] | None = None
    cell_value_pattern: str | None = None
    derived_from_buyer_code: bool = False
    description: str | None = None

    _exact_norm: set[str] = field(default_factory=set, init=False, repr=False)
    _fuzzy_norm: list[str] = field(default_factory=list, init=False, repr=False)

    def _prime(self) -> None:
        self._exact_norm = {_normalize_header(e) for e in self.exact}
        self._fuzzy_norm = [_normalize_header(k) for k in self.fuzzy_keywords]

    def match_exact(self, header_path: str) -> bool:
        return _normalize_header(header_path) in self._exact_norm

    def match_fuzzy(self, header_path: str, threshold: int = 85) -> bool:
        h = _normalize_header(header_path)
        if not h:
            return False
        for kw in self._exact_norm | set(self._fuzzy_norm):
            if not kw:
                continue
            if fuzz.token_set_ratio(h, kw) >= threshold:
                return True
        return False


@dataclass
class ColumnDictionary:
    """Core 필드 ↔ 헤더 매핑 dict + buyer→region 사전."""

    fields: dict[str, FieldEntry]
    buyer_to_region: dict[str, str]
    raw: dict[str, Any]

    def lookup(self, header_path: str) -> str | None:
        """헤더 path → core field 이름. 없으면 None."""
        if not header_path:
            return None
        for name, entry in self.fields.items():
            if entry.match_exact(header_path):
                return name
        for name, entry in self.fields.items():
            if entry.match_fuzzy(header_path):
                return name
        return None

    def required_fields(self) -> list[str]:
        return [name for name, entry in self.fields.items() if entry.is_required]

    def map_cell_value(self, field_name: str, value: object) -> object:
        """field_name의 cell_value_mapping 적용 (없으면 원본 반환)."""
        entry = self.fields.get(field_name)
        if entry is None or entry.cell_value_mapping is None:
            return value
        if value is None:
            return None
        return entry.cell_value_mapping.get(str(value).strip(), value)

    def region_from_buyer(self, buyer_code: str) -> str | None:
        if not buyer_code:
            return None
        return self.buyer_to_region.get(str(buyer_code).strip().upper())


# --- Loader --------------------------------------------------------------


_RESERVED_KEYS = {"buyer_to_region", "test_plan_keys"}


@lru_cache(maxsize=1)
def load_column_dictionary() -> ColumnDictionary:
    """column_dictionary.yaml 로드 (process 단위 캐시)."""
    data = yaml.safe_load(COLUMN_DICTIONARY_PATH.read_text(encoding="utf-8"))
    fields: dict[str, FieldEntry] = {}
    for name, body in data.items():
        if name in _RESERVED_KEYS:
            continue
        if not isinstance(body, dict):
            continue
        entry = FieldEntry(
            field_name=name,
            exact=list(body.get("exact", [])),
            fuzzy_keywords=list(body.get("fuzzy_keywords", [])),
            is_required=bool(body.get("is_required", False)),
            sheet_meta_path=body.get("sheet_meta_path"),
            sheet_name_pattern=body.get("sheet_name_pattern"),
            cell_value_mapping=body.get("cell_value_mapping"),
            cell_value_pattern=body.get("cell_value_pattern"),
            derived_from_buyer_code=bool(body.get("derived_from_buyer_code", False)),
            description=body.get("description"),
        )
        entry._prime()
        fields[name] = entry

    return ColumnDictionary(
        fields=fields,
        buyer_to_region=dict(data.get("buyer_to_region", {})),
        raw=data,
    )
