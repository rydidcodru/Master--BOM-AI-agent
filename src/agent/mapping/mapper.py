"""②.5 — 과거 change_line 세트를 모듈 하위트리 S에 사상 (순수 함수, LLM 0, DB 쓰기 0).

호출 위치: ② 검색 직후 · ③ HITL 직전(``propose_candidates``). ``ScopeIndex``는
①.5(Phase 2)의 인스턴스를 재사용한다(이중 생성 금지). 결과 ``MappedEvent``는
``CandidateSet.mapping``에 additive로 부착 — UI 표시·확정 플로우 변경은 이번 범위
아님(데이터만 흘려보낸다).

status 판별 (M1~M5, 첫 매치 적용):
  M1  base/new pno가 S의 pno와 정확일치(kind=pno_exact)            → matched (conf 1.0)
  M2  best ≥ tau_hi AND (best − second) > eps                      → matched
  M3  best ≥ tau_lo (그 외)                                        → ambiguous (후보 ≤3)
  M4  best < tau_lo AND [classification=New OR changepoint ∈ 추가계]
      AND [part_type ∈ S.types OR coherence ≥ theta]               → add_proposal (<발번대기>)
  M5  그 외                                                        → dropped

coherence는 1패스로 전 라인 score 후 계산 → 2패스에서 M4에 사용. "추가계" 판정은
``changepoint_vocab.yaml``의 actions/action_cues 구동(추가·신설·신규·부착 등).

TODO(후속): ambiguous의 LLM 동점해소(닫힌 택1)는 이번 범위에서 구현하지 않는다.
"""

from __future__ import annotations

from typing import Any

from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.agent.mapping.models import MappedEvent, MappedLine, MatchEvidence, NodeRef
from src.agent.matching.scoring import LineScore, ScopeIndex, ScopeNode
from src.ontology.changepoint_vocab import cue_to_action

# proposed_action 사상 — 과거 line 신호(classification 우선, 없으면 changepoint 행위).
_MODIFY_ACTIONS = {"변경", "교체", "축소", "확대", "보강", "적용", "통합", "분리"}


def _node_ref(n: ScopeNode) -> NodeRef:
    return NodeRef(
        pno=n.pno, part_name=n.part_name, part_type=n.part_type,
        depth=n.depth, path=n.path,
    )


def _line_ref(line: Any) -> dict[str, Any]:
    return {
        "line_id": getattr(line, "line_id", None),
        "part_name": getattr(line, "part_name", None),
        "base_pno": getattr(line, "base_pno", None),
        "new_pno": getattr(line, "new_pno", None),
        "classification": getattr(line, "classification", None),
        "changepoint": getattr(line, "changepoint", None),
        "source_ref": getattr(line, "source_ref", None),
        # cascade 전사(Phase 4)가 δ 레벨 정렬·창 매칭에 사용 (additive).
        "bom_level": getattr(line, "bom_level", None),
        "part_type": getattr(line, "part_type", None),
    }


def _action_signal(classification: str | None, changepoint: str | None) -> str | None:
    """과거 line 신호 → canonical 행위. classification 우선, 없으면 changepoint 행위 탐색."""
    cl = (classification or "").strip().lower()
    if cl == "delete":
        return "삭제"
    if cl == "new":
        return "추가"
    if cl == "change":
        return "변경"
    cp = (changepoint or "").strip()
    if not cp:
        return None
    # 변이형 포함 매칭 — 긴 변이형 먼저(오매칭 방지: '공용화' before '공용').
    for variant in sorted(cue_to_action(), key=len, reverse=True):
        if variant in cp:
            return cue_to_action()[variant]
    return None


def _is_addition(classification: str | None, changepoint: str | None) -> bool:
    """M4 "추가계" 게이트 — classification=New 또는 changepoint 행위가 추가."""
    return _action_signal(classification, changepoint) == "추가"


def _proposed_action(
    status: str, signal: str | None
) -> tuple[str | None, MatchEvidence | None]:
    """(matched/add_proposal/ambiguous) × 과거 신호 → 제안 action (+상충 evidence)."""
    if status == "ambiguous":
        return "CHECK", None  # ambiguous → 항상 CHECK
    if status == "matched":
        if signal == "삭제":
            return "DELETE", None
        if signal in _MODIFY_ACTIONS:
            return "MODIFY", None
        if signal == "추가":  # New인데 matched — 신호 상충
            return "CHECK", MatchEvidence(
                kind="classification", detail="신호 상충: 추가(New) 신호인데 S에 이미 존재", score=0.0
            )
        return "CHECK", MatchEvidence(
            kind="classification", detail="무신호 — classification/changepoint 행위 없음", score=0.0
        )
    if status == "add_proposal":
        if signal == "추가":
            return "ADD", None
        return "CHECK", MatchEvidence(
            kind="classification", detail=f"신호 상충: add_proposal인데 행위={signal or '무신호'}", score=0.0
        )
    return None, None


def _score_evidence(sc: LineScore) -> list[MatchEvidence]:
    out: list[MatchEvidence] = []
    if sc.kind in ("pno_exact", "canon_trgm", "type_match"):
        detail = sc.evidence[0] if sc.evidence else sc.kind
        out.append(MatchEvidence(kind=sc.kind, detail=str(detail), score=sc.best))  # type: ignore[arg-type]
    return out


def map_event_to_scope(event_lines: list[Any], index: ScopeIndex) -> MappedEvent:
    """과거 이벤트 라인 세트 → S 사상 결과(MappedEvent). 순수 함수 — LLM 0, DB 쓰기 0."""
    th = index.th
    event_id = 0
    for line in event_lines:
        eid = getattr(line, "event_id", None)
        if eid is not None:
            event_id = int(eid)
            break

    # 1패스 — 전 라인 score + coherence(매칭 라인 비율, 점수 가중).
    scores: list[LineScore] = [
        index.score_line(
            part_name=getattr(line, "part_name", None),
            base_pno=getattr(line, "base_pno", None),
            new_pno=getattr(line, "new_pno", None),
            part_type=getattr(line, "part_type", None),
        )
        for line in event_lines
    ]
    n = len(event_lines)
    coherence = (
        sum(sc.best for sc in scores if sc.best >= th.tau_lo) / n if n else 0.0
    )

    # 2패스 — M1~M5 첫 매치 적용 + proposed_action.
    mapped: list[MappedLine] = []
    for line, sc in zip(event_lines, scores):
        classification = getattr(line, "classification", None)
        changepoint = getattr(line, "changepoint", None)
        signal = _action_signal(classification, changepoint)
        evidence = _score_evidence(sc)
        if signal is not None:
            kind = "classification" if (classification or "").strip() else "cp_label"
            evidence.append(
                MatchEvidence(kind=kind, detail=f"행위 신호: {signal}", score=0.0)  # type: ignore[arg-type]
            )

        status: str
        target: NodeRef | None = None
        candidates: list[NodeRef] = []
        proposed_pno: str | None = None
        conf = 0.0

        if sc.kind == "pno_exact":  # M1
            status, target, conf = "matched", _node_ref(sc.best_node), 1.0  # type: ignore[arg-type]
        elif sc.best >= th.tau_hi and (sc.best - sc.second) > th.eps:  # M2
            status, target, conf = "matched", (_node_ref(sc.best_node) if sc.best_node else None), sc.best
        elif sc.best >= th.tau_lo:  # M3
            status, conf = "ambiguous", sc.best
            candidates = [
                _node_ref(node)
                for s, node in index.top_nodes(getattr(line, "part_name", None), k=3)
                if s >= th.tau_lo
            ]
            # TODO(후속): ambiguous LLM 동점해소(닫힌 택1) — 이번 범위 미구현.
        elif _is_addition(classification, changepoint) and (
            (getattr(line, "part_type", None) in index.types)
            or coherence >= th.theta_coherence
        ):  # M4
            status, conf = "add_proposal", coherence
            proposed_pno = PNO_PLACEHOLDER  # 신규 품번 무생성 — 항상 <발번대기>
            evidence.append(
                MatchEvidence(
                    kind="event_coherence",
                    detail=f"coherence={coherence:.2f} (θ={th.theta_coherence})",
                    score=coherence,
                )
            )
        else:  # M5
            status, conf = "dropped", 0.0

        action, conflict = _proposed_action(status, signal)
        if conflict is not None:
            evidence.append(conflict)

        mapped.append(
            MappedLine(
                line_ref=_line_ref(line),
                status=status,  # type: ignore[arg-type]
                target_node=target,
                candidates=candidates,
                proposed_action=action,  # type: ignore[arg-type]
                proposed_pno=proposed_pno,
                confidence=round(conf, 4),
                evidence=evidence,
            )
        )

    return MappedEvent(event_id=event_id, coherence=round(coherence, 4), lines=mapped)


__all__ = ["map_event_to_scope"]
