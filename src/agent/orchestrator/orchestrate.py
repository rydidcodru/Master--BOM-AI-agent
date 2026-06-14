"""L2 orchestrator — HITL 후보 제안(정지) + 확정 닻 전개.

흐름(절대원칙 #4): ``propose_candidates``(검색 → 후보 부품 세트, **트리 전개 없음**) →
``confirm.confirm_candidates``(확정 닻) → ``expand_confirmed``(확정 닻만 walk_subtree).
후보 단위 = change_event(한 사유로 함께 바뀐 부품 세트). 모든 도구 호출 tool_call_log 기록.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy.orm import Session

from src.agent.confirm.models import ConfirmedAnchor
from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.agent.intent.derive import env_flag
from src.agent.intent.models import ChangeIntent
from src.agent.matching.scoring import MatchThresholds, ScopeIndex
from src.agent.orchestrator.backend import RetrievalBackend
from src.agent.orchestrator.structure_scope import build_scope
from src.agent.repository.bom import BomNode, BomRepository, EdgeBomRepository
from src.db.models import ChangeLine, ToolCallLog
from src.db.retrieve import EventHit
from src.utils.logging import get_logger

log = get_logger(__name__)

_RRF_K = 60
_T = TypeVar("_T")


def _struct_weight() -> float:
    """레벨 B 융합에서 structure 랭킹 리스트 가중 (env ``STRUCT_WEIGHT``, 기본 1.0)."""
    try:
        return float(os.environ.get("STRUCT_WEIGHT", "1.0"))
    except ValueError:
        return 1.0


@dataclass
class LogRec:
    tool_name: str
    arguments: dict[str, Any]
    result_count: int | None
    latency_ms: int
    status: str
    error: str | None


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _timed(
    name: str, args: dict[str, Any], fn: Callable[[], list[_T]]
) -> tuple[list[_T], LogRec]:
    """fn() 실행 + 타이밍/상태 기록. 예외는 status=error로 흡수(빈 결과)."""
    t0 = time.monotonic()
    try:
        result = fn()
        return result, LogRec(name, args, len(result), _ms(t0), "ok", None)
    except Exception as exc:  # noqa: BLE001 — 도구 실패는 trace에 남기고 계속
        log.warning("l2.tool_failed", tool=name, error=str(exc)[:160])
        return [], LogRec(name, args, 0, _ms(t0), "error", str(exc)[:500])


def _rrf_fuse(
    result_lists: list[list[Any]],
    *,
    key: Callable[[Any], Any],
    rrf_k: int = _RRF_K,
    weights: list[float] | None = None,
) -> list[Any]:
    """교차쿼리 RRF: key별 Σ weight_i/(k + rank + 1). score_rrf 세팅 후 desc 정렬.

    EventHit(key=event_id) 융합에 쓰인다. ``weights``는 리스트별 가중(①.5 structure
    랭킹 리스트 투입용) — **미지정(기본)이면 전부 1.0으로 기존과 완전 동일 결과**.
    """
    ws = weights if weights is not None else [1.0] * len(result_lists)
    merged: dict[Any, Any] = {}
    score: dict[Any, float] = {}
    for hits, w in zip(result_lists, ws):
        for rank, h in enumerate(hits):
            kk = key(h)
            merged.setdefault(kk, h)
            score[kk] = score.get(kk, 0.0) + w / (rrf_k + rank + 1)
    for kk, h in merged.items():
        h.score_rrf = score[kk]
    return sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)


def _union_lines(backend: RetrievalBackend, event_ids: list[int]) -> list[ChangeLine]:
    """여러 event의 부품 라인을 합집합으로 회수 (P7.3 dedup 병합 보존).

    라인 dedup 키 = (base_pno or part_name_canon, changepoint, classification) — 같은
    부품의 동일 변경이 여러 이벤트에 중복돼도 1회만. 첫 event 순서를 보존한다.
    """
    seen: set[tuple] = set()
    out: list[ChangeLine] = []
    for eid in dict.fromkeys(event_ids):
        for ln in backend.lookup_lines_by_event(eid):
            # 부품 식별 = 첫 비공란(base_pno → new_pno → canon → part_name). 둘 다 None인
            # 행(new_pno만 있는 경우)이 한 키로 뭉개지지 않도록 식별 토큰을 우선순위로 잡는다.
            ident = (
                ln.base_pno or ln.new_pno
                or getattr(ln, "part_name_canon", None) or ln.part_name or ""
            )
            key = (ident.strip().upper(), (ln.changepoint or "").strip(),
                   (ln.classification or "").strip())
            if key in seen:
                continue
            seen.add(key)
            out.append(ln)
    return out


def _write_logs(session: Session, session_id: str, recs: list[LogRec]) -> None:
    for r in recs:
        session.add(
            ToolCallLog(
                session_id=session_id,
                tool_name=r.tool_name,
                arguments=r.arguments,
                result_count=r.result_count,
                latency_ms=r.latency_ms,
                status=r.status,
                error_message=r.error,
            )
        )
    session.commit()


# ════════════════════════════════════════════════════════════════
# HITL 준수 경로 (Phase 4): propose_candidates(정지) → confirm → expand_confirmed.
# 후보 단위 = change_event(한 사유로 함께 바뀐 부품 세트). 트리 전개는 확정 닻에 한해서만.
# ════════════════════════════════════════════════════════════════


@dataclass
class CandidateSet:
    """후보 1건 = 한 사유 묶음(event) + 그 부품 라인들. HITL 게이트가 확정할 단위.

    additive 필드: ``struct_score``(①.5), ``mapping``(②.5, 채택 후 lazy로만 채워짐),
    ``merged_event_ids``(P7.3 dedup 병합된 event_id들), ``rescued``(P7.2 구제 재검색 후보),
    ``manual_added``(P7.2 사람 수동 보강). UI는 데이터만 소비(확정 플로우 무변경).
    """

    event: EventHit
    lines: list[ChangeLine]
    struct_score: float | None = None
    mapping: Any = None
    merged_event_ids: list[int] = field(default_factory=list)
    rescued: bool = False
    manual_added: bool = False


@dataclass
class ProposalResult:
    """propose_candidates 산출 — 후보 세트 목록(트리 없음). 확정 게이트 입력.

    ``retrieval_meta``(P7.2): ``{top_rrf, best_per_channel, queries_used, low_confidence}``
    — "검색이 못 찾았을 수 있음"을 UI 배지로 보이기 위한 신뢰 신호(additive).
    """

    intent: ChangeIntent
    candidates: list[CandidateSet]
    reflections: int
    tool_calls: int
    session_id: str
    trace: list[LogRec] = field(default_factory=list)
    retrieval_meta: dict[str, Any] | None = None


@dataclass
class ExpandedNode:
    anchor_pno: str
    node: BomNode


@dataclass
class ExpansionResult:
    """expand_confirmed 산출 — 확정 닻별 하위 트리(결정론, LLM 0)."""

    anchors: list[ConfirmedAnchor]
    tree: list[ExpandedNode]
    tool_calls: int
    session_id: str
    trace: list[LogRec] = field(default_factory=list)


def propose_candidates(
    intent: ChangeIntent,
    *,
    session: Session,
    backend: RetrievalBackend,
    session_id: str | None = None,
    max_candidates: int = 10,
    min_candidates: int = 3,
    max_reflection: int = 1,
    per_query_top_k: int = 10,
    write_log: bool = True,
    dedup_by_reason: bool = False,
    llm: Any = None,
    rerank: bool = False,
    expand: bool = False,
    scope: ScopeIndex | None = None,
) -> ProposalResult:
    """L1 ChangeIntent → 후보 부품 세트(change_event 단위). **트리 전개 안 함**.

    흐름: rewritten_queries에 대해 search_events → event_id RRF(k=60) 융합 → 약하면
    reflection(raw_text) ≤max_reflection → top max_candidates → 각 event의 라인 회수.
    여기서 멈추고 사용자 확정(confirm_candidates)을 기다린다 — 절대원칙 #4.

    ``write_log=False``이면 tool_call_log에 기록하지 않는다(읽기 전용 검수 UI용 — 매 rerun마다
    트레이스 테이블을 오염시키지 않도록). trace는 반환값(result.trace)으로는 그대로 노출.
    """
    sid = session_id or uuid4().hex[:12]
    recs: list[LogRec] = []

    queries = intent.rewritten_queries or ([intent.raw_text] if intent.raw_text else [])
    # 0단계 LLM 멀티쿼리 확장 (opt-in). 변경사유 표현을 부품군/동의어로 넓혀 RRF recall↑.
    # 식별자 미주입(reason-only) · 실패 시 원본 쿼리 그대로. (lazy import: 결정론 경로 LLM 미접촉)
    if expand and llm is not None:
        from src.agent.orchestrator.expand import expand_queries

        queries = expand_queries(
            intent.raw_text or (queries[0] if queries else ""), llm, base_queries=queries
        )
    event_lists: list[list[EventHit]] = []
    for q in queries:
        hits, rec = _timed(
            "search_events",
            {"query": q},
            lambda q=q: backend.search_events(q, top_k=per_query_top_k),  # type: ignore[misc]
        )
        event_lists.append(hits)
        recs.append(rec)

    # ①.5 구조 스코프 부스트 (SEARCH_INCLUDE_STRUCT 게이트, 기본 off) — 모듈 하위트리(S)와
    # 레벨 A 후보 합집합의 라인 구조 정합으로 structure 랭킹 리스트 1개를 만들어 RRF에
    # "추가 랭킹 리스트"로 투입. S=∅/None이면 리스트 자체를 투입하지 않는다(빈 리스트
    # 투입 금지 — RRF 분모 왜곡). 이 BOM 조회는 검색 보조용 read-only(판정용 전개 아님).
    scope_index = scope
    if scope_index is None and env_flag("SEARCH_INCLUDE_STRUCT", default=False):
        scope_index = build_scope(
            intent.module_names, intent.base_model, session, MatchThresholds.from_env()
        )
    weights: list[float] | None = None
    if scope_index is not None:
        union: dict[int, EventHit] = {}
        for hits in event_lists:
            for h in hits:
                union.setdefault(h.event_id, h)

        def _structure_rank(
            idx: ScopeIndex = scope_index, evs: dict[int, EventHit] = union
        ) -> list[EventHit]:
            scored: list[tuple[float, EventHit]] = []
            for ev in evs.values():
                lines = backend.lookup_lines_by_event(ev.event_id)
                s = idx.event_structure_score(
                    [
                        {
                            "part_name": ln.part_name,
                            "base_pno": ln.base_pno,
                            "new_pno": ln.new_pno,
                            "part_type": ln.part_type,
                        }
                        for ln in lines
                    ]
                ) if lines else 0.0
                ev.struct_score = s
                if s > 0:
                    scored.append((s, ev))
            scored.sort(key=lambda t: -t[0])
            return [ev for _s, ev in scored]

        struct_list, rec = _timed(
            "structure_score", {"events": len(union)}, _structure_rank
        )
        recs.append(rec)
        if struct_list:
            event_lists.append(struct_list)
            weights = [1.0] * (len(event_lists) - 1) + [_struct_weight()]

    fused = _rrf_fuse(event_lists, key=lambda e: e.event_id, weights=weights)

    reflections = 0
    while len(fused) < min_candidates and reflections < max_reflection and intent.raw_text:
        prev = len(fused)
        hits, rec = _timed(
            "search_events",
            {"query": intent.raw_text, "reflect": True},
            lambda: backend.search_events(intent.raw_text, top_k=20),
        )
        event_lists.append(hits)
        if weights is not None:
            weights.append(1.0)
        recs.append(rec)
        fused = _rrf_fuse(event_lists, key=lambda e: e.event_id, weights=weights)
        reflections += 1
        if len(fused) <= prev:  # 개선 없음 → 조기 종료
            break

    # 같은 변경이 모델/파일별로 여러 event로 쪼개져 top-k를 중복으로 채우는 것 방지 —
    # (change_log+change_reason) 텍스트 기준 dedup해 서로 다른 유사 변경만 남긴다.
    # P7.3: dedup 병합 시 대표 event_id만 남기면 다른 이벤트에만 있던 부품 라인이 증발한다.
    # 병합된 event_id를 보존(merged_event_ids)했다가 라인 회수 때 **합집합**으로 살린다.
    merged_ids_of: dict[int, list[int]] = {}
    if dedup_by_reason:
        def _rkey(ev: EventHit) -> str:
            log = " ".join((ev.change_log or "").split()).lower()
            reason = " ".join((ev.change_reason or "").split()).lower()
            return f"{log}|{reason}"

        kept: dict[str, EventHit] = {}
        unique: list[EventHit] = []
        for ev in fused:
            k = _rkey(ev)
            if k in kept:
                prev = kept[k]
                merged_ids_of.setdefault(prev.event_id, []).append(ev.event_id)
                # 병합 시 structure 점수는 MAX 보존(①.5).
                if ev.struct_score is not None and (
                    prev.struct_score is None or ev.struct_score > prev.struct_score
                ):
                    prev.struct_score = ev.struct_score
                continue
            kept[k] = ev
            unique.append(ev)
        fused = unique

    candidates: list[CandidateSet] = []
    for ev in fused[:max_candidates]:
        merged = merged_ids_of.get(ev.event_id, [])
        lines, rec = _timed(
            "lookup_lines_by_event",
            {"event_id": ev.event_id, "merged": merged},
            lambda ev=ev, m=merged: _union_lines(backend, [ev.event_id, *m]),  # type: ignore[misc]
        )
        recs.append(rec)
        # P6: ②.5 매핑은 여기서 하지 않는다(lazy화). 후보 탐색 화면엔 struct_score(①.5
        # event_structure_score 재사용)면 충분하고, 라인 단위 M1~M5 매핑은 사람이 보는
        # 채택 1~2건에만 필요하므로 **채택 후**(build_adopted_mappings)에 계산한다.
        candidates.append(
            CandidateSet(event=ev, lines=lines, struct_score=ev.struct_score,
                         merged_event_ids=merged)
        )

    # P7.2(a): 신뢰 신호 — 전 후보의 dense 최고 유사도가 SEARCH_LOW_CONF 미만이면 low_confidence.
    retrieval_meta = _retrieval_meta(candidates, queries)
    low_conf = bool(retrieval_meta.get("low_confidence"))

    # P7.2(b): 구제 재검색 [RESCUE_SEARCH] — low_confidence일 때 1회 한정 완화 재검색.
    # DERIVE_CP 슬롯 target(없으면 부품명)을 원사유와 합본한 쿼리로 재호출(합본형만 — 슬롯
    # 단독 금지). 구제 후보는 rescued=True로 본 순위와 구분(별도 섹션, RRF 본 순위 미혼입).
    if low_conf and env_flag("RESCUE_SEARCH", default=False):
        rescue_q = _rescue_query(intent)
        if rescue_q:
            seen_ids = {c.event.event_id for c in candidates}
            hits, rec = _timed(
                "search_events", {"query": rescue_q, "rescue": True},
                lambda: backend.search_events(rescue_q, top_k=per_query_top_k),
            )
            recs.append(rec)
            for ev in hits:
                if ev.event_id in seen_ids:
                    continue
                seen_ids.add(ev.event_id)
                lines = _union_lines(backend, [ev.event_id])
                candidates.append(
                    CandidateSet(event=ev, lines=lines, struct_score=ev.struct_score,
                                 rescued=True)
                )

    # 2단계 LLM 재랭킹 (opt-in). 결정론 RRF 결과를 질의 관련도로 재정렬·중복강등 — 후보 재정렬만,
    # 실패 시 원본 순서. LLM_PROVIDER opt-in + rerank=True일 때만. (lazy import: 결정론 경로엔 LLM 미접촉)
    # 구제 후보는 본 순위와 섞지 않으므로 재랭킹 입력에서 분리.
    if rerank and llm is not None:
        main = [c for c in candidates if not c.rescued]
        if len(main) > 1:
            from src.agent.orchestrator.rerank import rerank_candidates

            main = rerank_candidates(
                intent.raw_text or (queries[0] if queries else ""), main, llm
            )
        candidates = main + [c for c in candidates if c.rescued]

    if write_log:
        _write_logs(session, sid, recs)
    return ProposalResult(
        intent=intent,
        candidates=candidates,
        reflections=reflections,
        tool_calls=len(recs),
        session_id=sid,
        trace=recs,
        retrieval_meta=retrieval_meta,
    )


def _low_conf_threshold() -> float:
    """P7.2 '후보 약함' 판정용 dense 최고 유사도 하한 (env SEARCH_LOW_CONF, 기본 0.30)."""
    try:
        return float(os.environ.get("SEARCH_LOW_CONF", "0.30"))
    except ValueError:
        return 0.30


def _retrieval_meta(candidates: list[CandidateSet], queries: list[str]) -> dict[str, Any]:
    """후보들의 채널별 최고 점수 + low_confidence 신호 (P7.2(a), UI 배지용)."""
    def _best(attr: str) -> float | None:
        vals = [getattr(c.event, attr) for c in candidates if getattr(c.event, attr) is not None]
        return round(max(vals), 4) if vals else None

    best = {
        "dense": _best("score_semantic"),
        "lexical": _best("score_lexical"),
        "sparse": _best("score_sparse"),
        "parts": _best("score_parts"),
        "structure": (round(max(c.struct_score for c in candidates if c.struct_score is not None), 4)
                      if any(c.struct_score is not None for c in candidates) else None),
    }
    top_rrf = _best("score_rrf")
    dense = best["dense"]
    # dense가 계산된 경우에만(임베딩 on) 판정 — dense None이면 신호 보류(False).
    low_conf = dense is not None and dense < _low_conf_threshold()
    return {
        "top_rrf": top_rrf,
        "best_per_channel": best,
        "queries_used": list(queries),
        "low_confidence": low_conf,
    }


def _rescue_query(intent: ChangeIntent) -> str | None:
    """구제 재검색 쿼리 — DERIVE_CP 슬롯 target(없으면 부품명)을 원사유와 합본(합본형만)."""
    targets = [s.target for s in intent.derived_change_slots if s.target]
    hint = " ".join(targets) if targets else " ".join(intent.part_nos[:1])
    reason = intent.raw_text or ""
    composed = f"{hint} {reason}".strip()
    return composed or None


def expand_confirmed(
    anchors: list[ConfirmedAnchor],
    *,
    session: Session,
    bom_repo: BomRepository | None = None,
    session_id: str | None = None,
    walk_depth: int = 4,
    file_id: int | None = None,
) -> ExpansionResult:
    """확정 닻 → 하위 BOM 트리(결정론, LLM 0). ``<발번대기>`` 닻은 번호가 없어 제외.

    file_id=None: seed(변경 파일 출처)와 bom_edge(BOM 파일 출처)의 file_id가 다르므로
    전체 BOM에서 닻 품번을 찾아 순회한다(교차 BOM, 기존 orchestrate와 동일).
    """
    repo = bom_repo or EdgeBomRepository(session)
    sid = session_id or uuid4().hex[:12]
    recs: list[LogRec] = []
    tree: list[ExpandedNode] = []

    for a in anchors:
        if a.is_placeholder or not a.part_no_new or a.part_no_new == PNO_PLACEHOLDER:
            continue  # 발번대기 — 번호 없으니 전개 대상 아님
        nodes, rec = _timed(
            "walk_subtree",
            {"seed": a.part_no_new, "depth": walk_depth},
            lambda a=a: repo.walk_subtree(a.part_no_new, "down", walk_depth, file_id),  # type: ignore[misc]
        )
        recs.append(rec)
        tree.extend(ExpandedNode(anchor_pno=a.part_no_new, node=n) for n in nodes)

    _write_logs(session, sid, recs)
    return ExpansionResult(
        anchors=list(anchors),
        tree=tree,
        tool_calls=len(recs),
        session_id=sid,
        trace=recs,
    )
