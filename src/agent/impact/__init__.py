"""L3 — Impact Analyzer (결정론 룰 엔진). LLM 호출 0회.

이 패키지는 LLM을 절대 import하지 않는다(설계 §5-1, test_impact가 강제).
입력 ImpactInput → 룰 디스패치 + 구조적 cascade → ImpactVerdict(action/tier + 발화 trace).
"""

from src.agent.impact.models import Action, Finding, ImpactInput, ImpactVerdict, Tier
from src.agent.impact.rules import compat_breaks, compat_up_depth, evaluate, evaluate_many

__all__ = [
    "Action",
    "Finding",
    "ImpactInput",
    "ImpactVerdict",
    "Tier",
    "compat_breaks",
    "compat_up_depth",
    "evaluate",
    "evaluate_many",
]
