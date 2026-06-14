"""Phase 6 — 평가 메트릭 수식 테스트 (recall/precision/MRR + 룰 FP)."""

from __future__ import annotations

import pytest

from src.agent.eval import (
    evaluate_retrieval,
    mrr,
    precision_at_k,
    recall_at_k,
    rule_fp_rate,
)
from src.agent.impact.models import ImpactVerdict


def test_recall_at_k():
    assert recall_at_k(["A", "B", "C"], {"B", "D"}, 3) == 0.5
    assert recall_at_k(["A"], set(), 3) == 0.0


def test_precision_at_k():
    assert precision_at_k(["A", "B", "C"], {"B", "D"}, 3) == pytest.approx(1 / 3)
    assert precision_at_k(["A"], {"A"}, 0) == 0.0


def test_mrr():
    assert mrr(["A", "B", "C"], {"B"}) == 0.5  # 2번째 → 1/2
    assert mrr(["A", "B"], {"Z"}) == 0.0


def test_rule_fp_rate():
    verdicts = [
        ImpactVerdict("A", "KEEP", "CASCADE"),
        ImpactVerdict("B", "CHECK", "CASCADE"),  # 유지여야 하는데 발화 → FP
    ]
    assert rule_fp_rate(verdicts, {"A", "B"}) == 0.5
    assert rule_fp_rate(verdicts, {"A"}) == 0.0
    assert rule_fp_rate(verdicts, set()) == 0.0


def test_evaluate_retrieval():
    m = evaluate_retrieval(["A", "B"], {"A"}, k=5)
    assert m.recall_at_k == 1.0
    assert m.mrr == 1.0
    assert m.k == 5
