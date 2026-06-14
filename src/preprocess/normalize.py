"""v2.0 Step 3 — 결정론적 값 정규화 (preprocessing_v2.md §3 Step 3).

원칙:
  - 모든 텍스트에 NFC 1차 적용 (macOS 자모분리 복원)
  - 식별자 필드(part_no, model_code, buyer, grade)에 NFKC (전각/반각 통일)
  - 자유텍스트(change_point, change_reason)에 NFC만 (의미 보존)

각 필드는 ``config/normalization.yaml``의 step 시퀀스를 따라 처리. 실패 시
``on_fail`` 정책 적용. null-like 입력은 short-circuit으로 None 반환.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml

from src.ontology import axioms
from src.utils.logging import get_logger
from src.utils.paths import NORMALIZATION_PATH

log = get_logger(__name__)

# Strings (case-insensitive, trimmed) that mean "no value".
_NULL_TOKENS = frozenset({"", "n/a", "na", "null", "none", "-", "—", "nan"})


@dataclass
class NormalizationResult:
    """단일 값 정규화 결과."""

    value: Any
    applied_steps: list[str] = dc_field(default_factory=list)
    success: bool = True
    fail_reason: str | None = None


@dataclass
class NormalizeReport:
    """run/file 단위 요약."""

    rows: int = 0
    failures: list[dict[str, Any]] = dc_field(default_factory=list)


@lru_cache(maxsize=1)
def _rules(path: Path = NORMALIZATION_PATH) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def _is_null_like(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_TOKENS:
        return True
    return False


def _apply_step(value: Any, step: dict[str, Any]) -> Any:
    t = step["type"]
    if t == "null_token_to_none":
        return None if _is_null_like(value) else value
    if value is None:
        return None
    if t == "unicode_normalize":
        return unicodedata.normalize(step["form"], str(value))
    if t == "strip":
        return str(value).strip() if isinstance(value, str) else value
    if t == "upper":
        return str(value).upper() if isinstance(value, str) else value
    if t == "lower":
        return str(value).lower() if isinstance(value, str) else value
    if t == "collapse_whitespace":
        return re.sub(r"\s+", " ", str(value)) if isinstance(value, str) else value
    if t == "regex_remove":
        return re.sub(step["pattern"], "", str(value)) if isinstance(value, str) else value
    if t == "max_length":
        n = int(step["value"])
        if isinstance(value, str) and len(value) > n:
            raise ValueError(f"max_length {n} exceeded")
        return value
    if t == "cast":
        target = step["to"]
        if target == "int":
            return int(float(value))
        if target == "float":
            return float(value)
        if target == "str":
            return str(value)
    if t == "range_check":
        mn = step.get("min")
        mx = step.get("max")
        v = float(value)
        if (mn is not None and v < mn) or (mx is not None and v > mx):
            raise ValueError(f"out of range [{mn}, {mx}]: {v}")
        return value
    if t == "map_alias":
        field = step["field"]
        if field == "change_type":
            canonical = axioms.normalize_change_type(str(value))
        elif field == "grade":
            canonical = axioms.normalize_grade(str(value))
        elif field == "part_type":
            canonical = axioms.normalize_part_type(str(value))
        else:
            canonical = value
        if canonical is None:
            raise ValueError(f"alias not found for {field}: {value!r}")
        return canonical
    return value


_VALIDATORS = {
    "part_no": axioms.validate_part_no,
    "model_code": axioms.validate_model_code,
    "change_type": axioms.validate_change_type,
    "grade": axioms.validate_grade,
    "event_stage": axioms.validate_event_stage,
    "region": axioms.validate_region,
}


def normalize_value(value: Any, field_name: str) -> NormalizationResult:
    """필드 1개 값 정규화. on_fail 정책 따라 결과 처리."""
    rules = _rules()
    rule = rules.get("fields", {}).get(field_name)
    if rule is None:
        return NormalizationResult(value=value)

    applied: list[str] = []
    current = value
    for step in rule.get("steps", []):
        try:
            current = _apply_step(current, step)
            applied.append(step["type"])
        except Exception as exc:  # noqa: BLE001 — 데이터 오류는 캡처
            on_fail = rule.get("on_fail", "quarantine")
            if on_fail == "set_null":
                return NormalizationResult(
                    value=None,
                    applied_steps=applied,
                    success=True,
                    fail_reason=f"{step['type']}: {exc}",
                )
            if on_fail == "truncate" and step["type"] == "max_length":
                current = str(current)[: int(step["value"])]
                applied.append("max_length(truncated)")
                continue
            return NormalizationResult(
                value=current,
                applied_steps=applied,
                success=False,
                fail_reason=f"{step['type']}: {exc}",
            )

    # post_validate
    pv = rule.get("post_validate")
    if pv and current is not None:
        validator = _VALIDATORS.get(pv)
        if validator and not validator(current):
            on_fail = rule.get("on_fail", "quarantine")
            if on_fail == "set_null":
                return NormalizationResult(
                    value=None,
                    applied_steps=applied,
                    success=True,
                    fail_reason=f"post_validate {pv} failed",
                )
            return NormalizationResult(
                value=current,
                applied_steps=applied,
                success=False,
                fail_reason=f"post_validate {pv} failed for {current!r}",
            )

    return NormalizationResult(value=current, applied_steps=applied)


def normalize_core(core: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Core dict 전체 정규화.

    Returns:
        (정규화된 core dict, {field: fail_reason} for 실패한 필드).
    """
    out: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for k, v in core.items():
        res = normalize_value(v, k)
        out[k] = res.value
        if not res.success and res.fail_reason:
            failures[k] = res.fail_reason
    return out, failures


def normalize_dpm_row(
    dpm_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """D-012 — dev_part_master 필드 dict 정규화.

    팀원 컬럼명(part_no_new 등)을 normalization.yaml의 rule key(part_no 등)로
    역매핑한 뒤 normalize_value를 호출하고, 결과를 다시 dpm 컬럼명으로 기록.

    Returns:
        (정규화된 dpm dict, {dpm_column: fail_reason}).
    """
    from src.db._mapping import CORE_TO_DEV_PART_MASTER

    # dpm 컬럼 → Core 13 rule key
    dpm_to_rule = {v: k for k, v in CORE_TO_DEV_PART_MASTER.items()}

    out: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for dpm_key, value in dpm_fields.items():
        rule_key = dpm_to_rule.get(dpm_key, dpm_key)
        res = normalize_value(value, rule_key)
        out[dpm_key] = res.value
        if not res.success and res.fail_reason:
            failures[dpm_key] = res.fail_reason
    return out, failures


def normalize_semantic(semantic: dict[str, str]) -> dict[str, str]:
    """자유텍스트 dict: NFC + strip + collapse_whitespace만 적용."""
    out: dict[str, str] = {}
    for k, v in semantic.items():
        if v is None:
            continue
        nfc = unicodedata.normalize("NFC", str(v))
        cleaned = re.sub(r"\s+", " ", nfc.strip())
        if cleaned:
            out[k] = cleaned
    return out


# ── 부품명 canon (변경점 어휘 v3 token_canon 공유) ──────────────────────────
# 모델코드 토큰: extractor의 모델코드 규약(영문 2~5 + 숫자 3~5 [+ 영숫자 ≤2])과
# 정합 + 풀 품번 꼴(WSED7667M.ABMQEUR@CVZ.EKHQ)도 제거.
_MODEL_CODE_TOKEN_RE = re.compile(
    r"^[A-Z]{2,5}\d{3,5}[A-Z0-9]{0,2}(?:[.@][A-Z0-9.@]+)?$", re.IGNORECASE
)
# 치수/수치 토큰: 137mm, 6.8", 1,170, 230V, 50Hz, 2ea, 24인치 등.
_DIM_TOKEN_RE = re.compile(
    r"^\d+(?:[.,]\d+)*(?:mm|cm|m|ea|인치|inch|\"|°|w|v|hz|l|kg|g)?$",
    re.IGNORECASE,
)


def canonicalize_part_name(name: str) -> str:
    """부품명 → 매칭용 canonical 형태 (결정론, ①.5/②.5 매칭 + parts 채널 공유).

    규칙: NFC → 구분문자(,/괄호)를 공백으로 → (a) 다단어 별칭(part_aliases) 최장 일치 →
    토큰별로 (b) 모델코드/풀품번·치수 토큰 제거, (c) ``token_canon``(Assmebly→Assembly 등)
    + ``part_aliases``(한↔영: 도어→Door 등) 단어 치환. 공백 압축해 반환.

    P7.1: part_aliases는 trgm 0점인 한↔영 쌍을 메워 ①.5 닻 탐색·②.5 매칭을 살린다.
    """
    from src.ontology.changepoint_vocab import token_canon  # 순환 import 방지 lazy
    from src.ontology.part_aliases import apply_aliases_phrase, apply_aliases_token

    t = unicodedata.normalize("NFC", str(name or ""))
    t = re.sub(r"[,/()\[\]{}]+", " ", t)
    t = apply_aliases_phrase(t)  # 다단어 별칭 먼저(최장 일치)
    canon_map = token_canon()
    out: list[str] = []
    for tok in t.split():
        tok = tok.strip(".-_·")
        if not tok:
            continue
        if _MODEL_CODE_TOKEN_RE.match(tok) or _DIM_TOKEN_RE.match(tok):
            continue
        tok = canon_map.get(tok.casefold(), tok)  # token_canon(오타/변이)
        tok = apply_aliases_token(tok)             # part_aliases(한↔영 단어)
        out.append(tok)
    return " ".join(out).strip()
