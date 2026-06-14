"""v2.0 (D-012) — 결정론적 narrativizer.

dev_part_master_fields dict + extra_fields → 자연어 4~6문장. LLM 호출 0회.

D-011 Phase F: payload 조건절 6개 trigger 제거됨.
D-012: 입력 변경 — core(우리) → dev_part_master_fields(팀원 컬럼명).
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, cast

import yaml

from src.utils.logging import get_logger
from src.utils.paths import NARRATIVIZE_TEMPLATES_PATH

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _templates() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.safe_load(NARRATIVIZE_TEMPLATES_PATH.read_text(encoding="utf-8")),
    )


def _value_present(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _fill_clause(template: str, vars_: dict[str, Any]) -> str:
    """{var} 치환. 모든 var가 채워졌고 비어있지 않으면 결과 반환, 아니면 ''."""
    try:
        keys_in_template = re.findall(r"\{(\w+)\}", template)
        for k in keys_in_template:
            if not _value_present(vars_.get(k)):
                return ""
        return template.format(**vars_)
    except (KeyError, IndexError):
        return ""


def build_narrative(
    dpm_fields: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
) -> str:
    """dev_part_master 필드 → 자연어 narrative_text.

    Args:
        dpm_fields: DevPartMaster 컬럼명 dict (part_no_new, event, change_point_raw 등).
        extra_fields: grade / event_stage 등이 들어있는 보조 dict.

    Returns:
        자연어 string. 빈 입력은 짧은 문장만 반환.
    """
    extra = extra_fields or {}
    tmpl = _templates()
    clauses = tmpl.get("clauses", {})
    change_type_korean = tmpl.get("change_type_korean", {})
    stage_korean = tmpl.get("event_stage_korean", {})

    vars_: dict[str, Any] = {
        "part_no": dpm_fields.get("part_no_new", ""),
        "part_name": dpm_fields.get("part_name", ""),
        "bom_level": dpm_fields.get("bom_depth"),
        "part_type": dpm_fields.get("part_type"),
        "new_model_code": dpm_fields.get("new_model", ""),
        "region": dpm_fields.get("region"),
        "grade": extra.get("grade"),
        "base_part_no": dpm_fields.get("part_no_base"),
        "change_point": dpm_fields.get("change_point_raw"),
        "change_reason": dpm_fields.get("change_reason_raw"),
    }

    part_meta = _fill_clause(clauses.get("part_meta_clause", ""), vars_)
    model_meta = _fill_clause(clauses.get("model_meta_clause", ""), vars_)
    base_part = _fill_clause(clauses.get("base_part_clause", ""), vars_)
    change_point_c = _fill_clause(clauses.get("change_point_clause", ""), vars_)
    change_reason_c = _fill_clause(clauses.get("change_reason_clause", ""), vars_)

    ct = str(dpm_fields.get("event", "") or "").strip()
    change_type_phrase = change_type_korean.get(ct, ct)

    es = str(extra.get("event_stage", "") or "").strip()
    stage_phrase = stage_korean.get(es)
    stage_clause = (
        clauses.get("stage_clause", "").format(event_stage_korean=stage_phrase)
        if stage_phrase
        else ""
    )

    sentence_pieces: list[str] = []
    pn = vars_.get("part_no")
    name = vars_.get("part_name")
    if pn:
        head = f"변경부품 {pn}({name}{part_meta})." if name else f"변경부품 {pn}{part_meta}."
        sentence_pieces.append(head)
    if vars_.get("new_model_code"):
        sentence_pieces.append(f"모델 {vars_['new_model_code']}{model_meta}.")
    if change_type_phrase:
        if base_part:
            sentence_pieces.append(f"{change_type_phrase}{base_part}")
        else:
            sentence_pieces.append(change_type_phrase)
    if change_point_c:
        sentence_pieces.append(
            change_point_c if change_point_c.endswith(".") else change_point_c + "."
        )
    if change_reason_c:
        sentence_pieces.append(
            change_reason_c if change_reason_c.endswith(".") else change_reason_c + "."
        )
    if stage_clause:
        sentence_pieces.append(stage_clause)

    narrative = " ".join(s for s in sentence_pieces if s).strip()
    narrative = re.sub(r"\s+", " ", narrative)
    return narrative
