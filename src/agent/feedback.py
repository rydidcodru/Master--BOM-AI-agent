"""P8 — HITL 확정 피드백 기록 (운영 = 평가 데이터).

확정 1회당 ``confirm_feedback`` 1행을 남긴다. **기록 전용** — 이 모듈도, 다른 어떤
검색·매핑·판정 경로도 ``confirm_feedback``을 읽지 않는다(읽기는 ``db feedback-report``
CLI만; 읽기금지 grep 단언 테스트로 강제). 기록 실패는 본 확정 흐름을 막지 않는다
(try/except + 경고 로그). ``FEEDBACK_LOG`` env로 게이트(기본 on).

LLM import 0 (결정론 보조 경로).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.agent.intent.derive import env_flag
from src.db.models import ConfirmFeedback
from src.utils.logging import get_logger

log = get_logger(__name__)


def _candidate_digest(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """후보 dict 목록 → 기록용 축약(event_id/rank/rrf/struct/low_conf/rescued/manual)."""
    out: list[dict[str, Any]] = []
    for rank, c in enumerate(candidates, 1):
        out.append({
            "event_id": c.get("event_id"),
            "rank": rank,
            "rrf": c.get("score_rrf"),
            "struct": c.get("struct_score"),
            "rescued": bool(c.get("rescued")),
            "manual_added": bool(c.get("manual_added")),
        })
    return out


def record_confirm_feedback(
    session: Session,
    *,
    query_text: str | None,
    intent_json: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    adopted_event_ids: list[int],
    manual_added_event_ids: list[int],
    low_confidence: bool,
    source_ref: str | None = None,
) -> bool:
    """확정 스냅샷 1행 기록 (``FEEDBACK_LOG`` 게이트). 실패해도 예외를 전파하지 않는다.

    Returns:
        기록되면 True, 게이트 off/실패면 False (본 흐름은 어느 경우든 계속).
    """
    if not env_flag("FEEDBACK_LOG", default=True):
        return False
    try:
        session.add(
            ConfirmFeedback(
                query_text=query_text,
                intent_json=intent_json,
                candidates=_candidate_digest(candidates),
                adopted_event_ids=[int(e) for e in adopted_event_ids if e is not None] or None,
                manual_added_event_ids=[int(e) for e in manual_added_event_ids if e is not None] or None,
                low_confidence=bool(low_confidence),
                source_ref=source_ref,
            )
        )
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — 기록 실패는 확정 흐름 비차단
        log.warning("feedback.record_failed", error=str(exc)[:160])
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


__all__ = ["record_confirm_feedback"]
