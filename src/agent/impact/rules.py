"""L3 결정론 룰 엔진 — config/impact_rules.yaml 기반.

LLM import 절대 금지(설계 §5-1). 입력 ImpactInput → 구조적 cascade finding +
YAML 룰 발화 finding → priority 정렬 → 최우선 action/tier. trace엔 발화 전부 표시.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import yaml

from src.agent.impact.models import Action, Finding, ImpactInput, ImpactVerdict, Tier
from src.utils.paths import CONFIG_DIR

IMPACT_RULES_PATH = CONFIG_DIR / "impact_rules.yaml"

# 충돌 tie-break (priority 같을 때 더 영향 큰 action 우선).
_ACT_RANK: dict[str, int] = {"DELETE": 5, "ADD": 4, "MODIFY": 3, "CHECK": 2, "KEEP": 1}
_VALID_TIERS = {"CORE", "CASCADE"}


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(IMPACT_RULES_PATH.read_text(encoding="utf-8"))
    # config 무결성 검증 (프로그래머/설정 오류 → raise).
    for rule in cfg.get("rules", []):
        then = rule.get("then", {})
        if then.get("action") not in _ACT_RANK or then.get("tier") not in _VALID_TIERS:
            raise ValueError(f"impact_rules.yaml: invalid then in {rule.get('id')!r}")
    return cfg


def _attribute_related(inp: ImpactInput) -> bool:
    """하향 통제 어휘로 자식의 변경 관련성 판정. attribute 모르면 보수적(True)."""
    if inp.attribute_related is not None:
        return inp.attribute_related
    if not inp.change_attribute:
        return True
    groups: list[str] = _config().get("attribute_part_groups", {}).get(
        inp.change_attribute, []
    )
    return inp.part_type in groups or inp.classification in groups


def _structural(inp: ImpactInput) -> Finding:
    """relation 기반 구조적 기본 판정 (룰과 별개, 항상 1건).

    우선순위 불변식: ``R09(20) < STRUCT_child_*(40) < STRUCT_seed(50) < R08(55) < … < R06(90)``.
    (P5에서 cascade 전사 finding은 제거됨 — 동반변경은 체크리스트 정보행으로만 표시.)
    """
    if inp.relation == "seed":
        mapping: dict[str | None, tuple[Action, str]] = {
            "New": ("ADD", "신규 부품"),
            "Change": ("MODIFY", "변경 부품"),
            "Carry-over": ("KEEP", "유지 부품"),
        }
        action, why = mapping.get(inp.event, ("CHECK", "event 미상 — 검토"))
        return Finding("STRUCT_seed", action, "CORE", 50, f"seed:{why}", kind="struct")
    if inp.relation == "child":
        if _attribute_related(inp):
            return Finding(
                "STRUCT_child_related", "CHECK", "CASCADE", 40,
                "변경 속성 관련 자식 — 검토", kind="struct",
            )
        return Finding(
            "STRUCT_child_unrelated", "KEEP", "CASCADE", 40,
            "무관 자식 — KEEP(cascade 차단)", kind="struct",
        )
    # parent
    if inp.r_up_break:
        return Finding(
            "STRUCT_parent_chaeban", "ADD", "CASCADE", 40,
            "상향 호환 깨짐 → 부모 채반(NEW)", kind="struct",
        )
    return Finding(
        "STRUCT_parent_keep", "KEEP", "CASCADE", 40,
        "상향 호환 유지 → KEEP", kind="struct",
    )


def compat_breaks(
    change_attribute: str | None,
    change_text: str | None = None,
    part_type: str | None = None,
) -> tuple[bool, str]:
    """상향 호환성(form-fit-function) 깨짐 여부 — 결정론·config 기반.

    "호환성 깨질 때만 상위 채반"(설계 ⑤)의 게이트. ``r_up_break``를 외부에서 받는 대신
    변경 신호로 판정한다. 우선순위 (**break > safe** — 복합 변경 false-negative 방지):

      1) ``break_attributes``/``break_keywords`` 매칭 → ``True`` (치수·조립·인터페이스 등).
         "색상+조립"처럼 break가 섞이면 form-fit-function이 깨지므로 safe보다 우선한다.
      2) (break 신호 없을 때만) ``safe_attributes``/``safe_keywords`` 매칭 → ``False``
         (순수 화장성/소싱 — 색상·인쇄·동등 대체 등).
      3) 둘 다 아니면 ``break_default`` (모호 — 원칙상 기본 False).

    Returns:
        (깨짐 여부, 사유 문자열). 사유는 trace/문서 표기용.
    """
    cfg: dict[str, Any] = _config().get("compatibility", {})
    attr = (change_attribute or "").strip()
    text = change_text or ""
    # 1) break 우선 — form-fit-function 깨짐 신호가 하나라도 있으면 채반.
    if attr and attr in cfg.get("break_attributes", []):
        return True, f"호환 깨짐 속성({attr})"
    break_hit = next((k for k in cfg.get("break_keywords", []) if k in text), None)
    if break_hit:
        return True, f"호환 깨짐 키워드({break_hit})"
    # 2) safe — break 신호가 없을 때만 비-채반(순수 화장성/소싱).
    if attr and attr in cfg.get("safe_attributes", []):
        return False, f"비-채반 속성({attr})"
    safe_hit = next((k for k in cfg.get("safe_keywords", []) if k in text), None)
    if safe_hit:
        return False, f"비-채반 키워드({safe_hit})"
    # 3) 모호 → 기본값(원칙: 깨질 때'만' → 기본 False)
    return bool(cfg.get("break_default", False)), "모호(기본값)"


def compat_up_depth() -> int:
    """상향(부모) 전개 최대 깊이 (config; 기본 2)."""
    return int(_config().get("compatibility", {}).get("up_depth", 2))


def _matches(when: dict[str, Any], inp: ImpactInput) -> bool:
    val = getattr(inp, when["field"], None)
    if "equals" in when:
        return bool(val == when["equals"])
    if "in" in when:
        return val in when["in"]
    if "truthy" in when:
        return bool(val) == bool(when["truthy"])
    return False


def evaluate(
    inp: ImpactInput, extra_findings: list[Finding] | None = None
) -> ImpactVerdict:
    """단일 부품 영향도 판정. findings는 priority desc 정렬, 전부 노출.

    ``extra_findings``(additive, 기본 None → 기존과 동일)는 cascade 전사(kind=
    "cascade_tpl") 등 외부에서 만든 finding을 **주입**하는 자리 — 별도 분기 로직 없이
    기존 충돌해소 ``(priority DESC, action_rank DESC)``가 그대로 최종 action을 결정한다.
    """
    findings: list[Finding] = [_structural(inp)]
    for rule in _config().get("rules", []):
        if _matches(rule["when"], inp):
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    action=cast(Action, rule["then"]["action"]),
                    tier=cast(Tier, rule["then"]["tier"]),
                    priority=int(rule.get("priority", 0)),
                    reason=rule["description"],
                )
            )
    if extra_findings:
        findings.extend(extra_findings)
    findings.sort(key=lambda f: (f.priority, _ACT_RANK[f.action]), reverse=True)
    top = findings[0]
    return ImpactVerdict(part_no=inp.part_no, action=top.action, tier=top.tier, findings=findings)


def evaluate_many(
    inputs: list[ImpactInput],
    extra_findings: dict[str, list[Finding]] | None = None,
) -> list[ImpactVerdict]:
    """배치 판정. ``extra_findings``: part_no → 주입 finding 목록 (additive)."""
    extras = extra_findings or {}
    return [evaluate(i, extras.get(i.part_no)) for i in inputs]
