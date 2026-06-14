"""agent_client — UI 어댑터 (L1~L4 에이전트).

HITL 단일 경로의 UI 어댑터. PPT 변경항목/수동 입력 → 후보 검색(propose, 정지) →
사용자 확정(confirm) → 확정 닻만 전개 + 베이스 BOM 트리 New BOM(절대원칙 #4).

ENABLE_EMBEDDING을 자동 활성화(검색 필수). LLM(L1 의미 슬롯/L4 설명)은 ENABLE_LLM=1일 때만 —
기본은 결정론 경로.
"""

from __future__ import annotations

import os
from typing import Any

# rag_client와 동일: 검색에 임베딩 필수 → 자동 활성화.
os.environ.setdefault("ENABLE_EMBEDDING", "1")

# 주의: .env(LLM_PROVIDER/ANTHROPIC_API_KEY) 로드는 **여기서 하지 않는다** — 모듈 import만으로
# os.environ을 오염시키면 LLM 비활성 가정의 단위 테스트가 깨진다(import 부작용 금지). .env 로드는
# 실제 UI 진입점(main.py/agent_app.py/inspect_app.py)에서만 한다(`load_agent_env`).

from sqlalchemy.orm import Session  # noqa: E402

from src.agent.confirm import (  # noqa: E402
    CandidateSelection,
    classification_ko_to_en,
    confirm_candidates,
)
from src.agent.feedback import record_confirm_feedback  # noqa: E402
from src.agent.docgen import validate_doc  # noqa: E402
from src.agent.docgen.generator import DocRow  # noqa: E402
from src.agent.docgen.reason_join import ppt_reason_map  # noqa: E402
from src.agent.basebom import (  # noqa: E402
    NewBom, apply_changes, parse_base_bom, validate_new_bom, write_newbom_xlsx,
)
from src.agent.intent.models import ChangeIntent  # noqa: E402
from src.agent.intent.structurizer import intent_from_change  # noqa: E402
from src.agent.orchestrator import ProposalResult  # noqa: E402
from src.agent.orchestrator.backend import DbRetrievalBackend, RetrievalBackend  # noqa: E402
from src.agent.pipeline import (  # noqa: E402
    ConfirmedAnalysis,
    analyze_confirmed,
    build_adopted_mappings,
    propose_analysis,
)
from src.agent.ppt import change_items_from_extraction, extract_change_review_from_pptx_bytes  # noqa: E402
from src.agent.repository.bom import BomRepository, EdgeBomRepository  # noqa: E402
from src.db.engine import make_engine, session_factory  # noqa: E402

_ENGINE: Any = None
_SF: Any = None


def load_agent_env() -> bool:
    """.env(gitignored) 로드 — LLM_PROVIDER/ANTHROPIC_API_KEY 등을 env로(override 안 함).

    **UI 진입점에서만** 호출한다(import 부작용 금지 — 단위 테스트 오염 방지). 키 값은
    로그/출력에 노출하지 않는다(절대원칙). dotenv 미설치/파일 부재면 조용히 False.
    """
    try:
        from pathlib import Path as _Path  # noqa: PLC0415

        from dotenv import load_dotenv as _load_dotenv  # noqa: PLC0415

        return bool(_load_dotenv(_Path(__file__).resolve().parents[2] / ".env"))
    except Exception:  # noqa: BLE001
        return False


def _factory() -> Any:
    """Engine + sessionmaker 캐시 (rag_client와 동일 패턴)."""
    global _ENGINE, _SF
    if _ENGINE is None:
        _ENGINE = make_engine()
        _SF = session_factory(_ENGINE)
    return _SF


def _doc_rows(rows: list[DocRow]) -> list[dict[str, Any]]:
    return [
        {
            "pno": r.pno_display,
            "is_new": r.is_new,
            "action": r.action,
            "tier": r.tier,
            "src": r.src,
            "detail": r.detail,
            "relation": r.relation,
        }
        for r in rows
    ]


# ════════════════════════════════════════════════════════════════
# HITL 확정 게이트 어댑터 (절대원칙 #4 — 확정이 트리 전개보다 먼저).
# Streamlit은 재실행마다 stateless라 2-호출로 나눈다:
#   1) propose_*  : 검색 → 후보 부품 세트 반환(정지, 트리 전개 없음). UI가 session_state에 보관.
#   2) confirm_*  : 사용자 채택/거절·변경점·신규 P/No → 확정 닻 → 하위 전개 + 판정 + 문서.
# 후보/의도는 직렬화 dict로 운반 → confirm 단계에서 재구성(서버 상태 비보존).
# ════════════════════════════════════════════════════════════════


def _serialize_intent(ci: ChangeIntent) -> dict[str, Any]:
    """ChangeIntent → UI 운반/표시용 dict. confirm 단계에서 _intent_from_dict로 복원."""
    return {
        "raw_text": ci.raw_text,
        "part_nos": ci.part_nos,
        "models": ci.models,
        "region": ci.region,
        "base_model": ci.base_model,
        "change_attribute": ci.change_attribute,
        "change_direction": ci.change_direction,
        "intent_summary": ci.intent_summary,
        "rewritten_queries": ci.rewritten_queries,
        "confidence": ci.confidence,
        "source": ci.source,
        "module_names": ci.module_names,
        # 변경점 유도 v3 (DERIVE_CP) — UI '자동요약' 라벨용 (additive).
        "derived_change_slots": [s.model_dump() for s in ci.derived_change_slots],
        "derived_cp_source": ci.derived_cp_source,
    }


def _intent_from_dict(d: dict[str, Any]) -> ChangeIntent:
    """_serialize_intent 역변환 (Pydantic extra=ignore라 여분 키는 무시)."""
    return ChangeIntent(**d)


def _serialize_candidates(prop: ProposalResult) -> list[dict[str, Any]]:
    """ProposalResult → 후보 세트 dict 목록 (event=한 사유 묶음, lines=부품 세트).

    각 line은 confirm 입력(CandidateSelection)을 복원하는 데 필요한 필드를 모두 보존:
    part_no_base / part_no_new_suggested(회수된 기존값) / classification / source_ref / event_id.
    신규 P/No는 여기서 만들지 않는다(무생성) — 사용자가 confirm에서 입력.
    """
    out: list[dict[str, Any]] = []
    for c in prop.candidates:
        ev = c.event
        # ②.5 매핑(MappedEvent) — 라인 순서는 c.lines와 동일(mapper가 순서 보존).
        mapping = getattr(c, "mapping", None)
        mlines = list(mapping.lines) if mapping is not None else []
        lines = []
        for i, ln in enumerate(c.lines):
            d: dict[str, Any] = {
                "event_id": ev.event_id,
                "part_no_base": ln.base_pno,
                "part_no_new_suggested": ln.new_pno,
                "part_name": ln.part_name,
                "part_type": ln.part_type,
                "bom_level": ln.bom_level,  # 카드 뷰 'Lv.N' 배지용(참고 이미지). 다른 소비자는 무시(additive).
                "changepoint": ln.changepoint,
                "classification": ln.classification,
                "source_ref": ln.source_ref or ev.source_ref,
            }
            if i < len(mlines):  # 구조 스코프 게이트 on일 때만 존재 (additive)
                ml = mlines[i]
                d["mapping_status"] = ml.status
                d["mapping_action"] = ml.proposed_action
                d["mapping_target"] = (
                    ml.target_node.pno if ml.target_node is not None else (ml.proposed_pno or "")
                )
            lines.append(d)
        out.append(
            {
                "event_id": ev.event_id,
                "change_log": ev.change_log,
                "change_reason": ev.change_reason,
                "base_model": ev.base_model,
                "new_model": ev.new_model,
                "score_rrf": round(ev.score_rrf, 5) if ev.score_rrf is not None else None,
                # ①.5 구조 정합 점수 (SEARCH_INCLUDE_STRUCT on일 때만 값 존재, additive).
                "struct_score": round(c.struct_score, 4) if c.struct_score is not None else None,
                "mapping_coherence": (
                    round(mapping.coherence, 4) if mapping is not None else None
                ),
                # P7.2/7.3 — 구제/수동 보강/병합 이벤트 표시(additive).
                "rescued": bool(getattr(c, "rescued", False)),
                "manual_added": bool(getattr(c, "manual_added", False)),
                "merged_event_ids": list(getattr(c, "merged_event_ids", []) or []),
                "source_ref": ev.source_ref,
                "lines": lines,
            }
        )
    return out


def propose_candidate_sets(
    *,
    change_detail: str,
    change_reason: str,
    base_model: str | None = None,
    region: str | None = None,
    part_name: str | None = None,
    part_nos: list[str] | None = None,
    models: list[str] | None = None,
    module_names: list[str] | None = None,
    session: Session,
    backend: RetrievalBackend,
    llm: Any = None,
    rerank: bool = True,
    expand: bool = True,
) -> dict[str, Any]:
    """변경내용+사유(+부품명·식별자) → 후보 부품 세트(정지). 주입형(테스트는 fake). 트리 전개 없음.

    검색 쿼리는 변경내용/변경사유 + (주어지면)부품명/품번으로 구성한다(2026-06-10 확정:
    검색에 부품명+식별자 포함). base_model은 표기/필터용으로만 함께 싣는다.

    LLM이 설정(opt-in: LLM_PROVIDER/ENABLE_LLM)됐으면 ``expand=True``(기본)에서 검색 전
    멀티쿼리 확장(부품군/동의어), ``rerank=True``(기본)에서 회수 후보를 LLM 관련도로 2단계
    재정렬한다(RankGPT-style). **LLM 미설정이면 자동으로 결정론 RRF만**(opt-in 보존).
    """
    if (rerank or expand) and llm is None:
        from src.agent.llm.client import default_llm, llm_enabled  # noqa: PLC0415
        if llm_enabled():
            try:
                llm = default_llm()
            except Exception:  # noqa: BLE001 — LLM 미가용 시 결정론으로
                llm = None
    intent = intent_from_change(
        change_detail=change_detail,
        change_reason=change_reason,
        base_model=base_model,
        region=region,
        part_name=part_name,
        part_nos=part_nos,
        models=models,
        module_names=module_names,
    )
    prop = propose_analysis(
        intent.raw_text, session=session, backend=backend, intent=intent,
        llm=llm, rerank=bool(rerank and llm), expand=bool(expand and llm),
        # 같은 사유가 모델 변형마다 쪼개진 중복 event를 병합(라인은 합집합 보존 — P7.3).
        # 화면에 동일 변경이 여러 번 보이던 문제 해소.
        dedup_by_reason=True,
    )
    return {
        "intent": _serialize_intent(intent),
        "base_model": base_model or "",
        "candidates": _serialize_candidates(prop),
        "reflections": prop.reflections,
        "tool_calls": prop.tool_calls,
        "session_id": prop.session_id,
        "retrieval_meta": prop.retrieval_meta,  # P7.2 신뢰 신호(low_confidence 등)
    }


def manual_search_candidates(keyword: str, *, base_model: str | None = None,
                             region: str | None = None) -> list[dict[str, Any]]:
    """P7.2(c) — HITL 수동 보강 검색. 키워드로 동일 search_events 파이프 재호출 →
    후보 dict 목록(manual_added=True)을 반환. 자동 검색이 못 가져온 정답을 사람이 회수하는
    유일한 경로(P8 기록과 결합해 미회수율 측정의 원천). 확정 플로우 자체는 무변경."""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    factory = _factory()
    backend = DbRetrievalBackend(factory)
    with factory() as s:
        intent = intent_from_change(change_detail=keyword, change_reason=keyword,
                                    base_model=base_model, region=region)
        prop = propose_analysis(intent.raw_text, session=s, backend=backend, intent=intent,
                                max_reflection=0, write_log=False)
        rows = _serialize_candidates(prop)
        for r in rows:
            r["manual_added"] = True
        return rows


def propose_change_item(
    change_detail: str,
    change_reason: str,
    *,
    base_model: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """UI 진입점 — 변경항목 → 후보 부품 세트(정지). 실 Postgres + 설정 LLM(.env — Claude/Ollama opt-in)."""
    factory = _factory()
    backend = DbRetrievalBackend(factory)
    with factory() as s:
        return propose_candidate_sets(
            change_detail=change_detail,
            change_reason=change_reason,
            base_model=base_model,
            region=region,
            session=s,
            backend=backend,
        )


def _selections_from_ui(rows: list[dict[str, Any]]) -> list[CandidateSelection]:
    """UI 행 dict 목록 → CandidateSelection. change_point 집합 밖 값은 Pydantic이 거부."""
    sels: list[CandidateSelection] = []
    for r in rows:
        sels.append(
            CandidateSelection(
                part_no_base=r.get("part_no_base"),
                part_no_new_suggested=r.get("part_no_new_suggested"),
                decision="accept" if r.get("accept") else "reject",
                change_point=(r.get("change_point") or None),
                new_pno_input=(r.get("new_pno_input") or None),
                classification=r.get("classification"),
                source_ref=r.get("source_ref"),
                event_id=r.get("event_id"),
            )
        )
    return sels


def _serialize_confirmed(res: ConfirmedAnalysis, session: Session) -> dict[str, Any]:
    """ConfirmedAnalysis → UI 렌더 dict (확정 닻 / 하위 트리 / 판정 / 문서 3종 + 위반)."""
    doc = res.doc
    return {
        "anchors": [
            {
                "part_no_new": a.part_no_new,
                "part_no_base": a.part_no_base,
                "change_point": a.change_point,
                "classification": a.classification,
                "is_placeholder": a.is_placeholder,
                "src": a.source_ref,
            }
            for a in res.anchors
        ],
        "tree": [
            {"anchor": n.anchor_pno, "pno": n.node.pno, "depth": n.node.depth}
            for n in res.expansion.tree
        ],
        "verdicts": [
            {
                "part_no": v.part_no,
                "action": v.action,
                "tier": v.tier,
                "rules": [f.rule_id for f in v.findings],
            }
            for v in res.verdicts
        ],
        "doc": {
            "changed_parts": _doc_rows(doc.changed_parts),
            "dev_master_rows": _doc_rows(doc.dev_master_rows),
            "bom_diff": _doc_rows(doc.bom_diff),
            "checklist": doc.checklist,
        },
        "violations": validate_doc(doc),
    }


def confirm_and_expand(
    *,
    intent: dict[str, Any],
    selections: list[dict[str, Any]],
    session: Session,
    session_id: str = "ui",
    bom_repo: BomRepository | None = None,
    llm: Any = None,
    candidates: list[dict[str, Any]] | None = None,
    retrieval_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """확정 게이트 → 하위 전개. 주입형(테스트는 fake). 채택/거절은 agent_feedback 기록.

    intent: propose 단계의 _serialize_intent 결과. selections: UI 행(accept/change_point/
    new_pno_input 등). 채택 닻에 한해서만 트리 전개(절대원칙 #4). ``candidates``/
    ``retrieval_meta``(propose 결과)는 P8 confirm_feedback 기록용(선택).
    """
    ci = _intent_from_dict(intent)
    anchors = confirm_candidates(
        session,
        session_id=session_id,
        selections=_selections_from_ui(selections),
    )
    # P6: 채택 이벤트에 한해 ②.5 매핑을 lazy 계산(확정 후·전개 직전). companion 정보행 소비.
    adopted_ids = [
        r.get("event_id") for r in selections if r.get("accept") and r.get("event_id")
    ]
    manual_ids = [
        r.get("event_id") for r in selections
        if r.get("accept") and r.get("event_id") and r.get("manual_added")
    ]
    adopted_mappings = build_adopted_mappings(ci, set(adopted_ids), session)
    res = analyze_confirmed(
        ci, anchors, session=session, bom_repo=bom_repo, llm=llm,
        adopted_mappings=adopted_mappings or None,
    )
    # P8: 확정 = 평가 데이터. 기록 전용·비차단(FEEDBACK_LOG 게이트).
    record_confirm_feedback(
        session,
        query_text=ci.raw_text or None,
        intent_json=intent,
        candidates=candidates or [],
        adopted_event_ids=adopted_ids,
        manual_added_event_ids=manual_ids,
        low_confidence=bool((retrieval_meta or {}).get("low_confidence")),
        source_ref=(anchors[0].source_ref if anchors else None),
    )
    return _serialize_confirmed(res, session)


def confirm_change_item(
    intent: dict[str, Any],
    selections: list[dict[str, Any]],
    *,
    session_id: str = "ui",
    candidates: list[dict[str, Any]] | None = None,
    retrieval_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """UI 진입점 — 사용자 확정 → 하위 전개 + 판정 + 문서. 실 Postgres + 설정 LLM(.env — Claude/Ollama opt-in).

    ``candidates``/``retrieval_meta``(propose 결과)는 P8 confirm_feedback 기록용(선택).
    """
    factory = _factory()
    with factory() as s:
        return confirm_and_expand(
            intent=intent,
            selections=selections,
            session=s,
            session_id=session_id,
            bom_repo=EdgeBomRepository(s),
            candidates=candidates,
            retrieval_meta=retrieval_meta,
        )


# ════════════════════════════════════════════════════════════════
# 다항목 HITL — PPT 변경항목 N건을 하나의 확정 게이트로 집계(절대원칙 #4).
#   propose_from_items : 항목별 정지 후보(트리 없음).
#   confirm_items_*    : 채택 항목만 confirm_and_expand + 확정 new P/No로 베이스 BOM 트리 적용.
# 항목별 intent를 유지해 change_attribute가 섞이지 않는다(스키마 변경 불필요).
# ════════════════════════════════════════════════════════════════


def propose_from_items(
    items: list[dict[str, Any]],
    *,
    base_model: str | None = None,
    region: str | None = None,
    session: Session,
    backend: RetrievalBackend,
    llm: Any = None,
    select_indices: set[int] | None = None,
) -> list[dict[str, Any]]:
    """PPT 변경항목 목록 → **선택한 항목만** 후보 부품 세트(정지). 주입형. 트리 전개 없음.

    각 항목의 (변경내용+변경사유 + 부품명/품번)으로 search_events 검색(2026-06-10 확정:
    검색에 부품명+식별자 포함). ``select_indices``가 주어지면 그 **원본 인덱스** 항목만
    검색하고 ``item_index``는 원본 위치를 보존한다(전 항목 일괄 검색 금지 — 사람이 고른 것만).
    반환: 항목별 ``{item_index, item, intent, base_model, ...}``.
    """
    out: list[dict[str, Any]] = []
    for idx, it in enumerate(items):
        if select_indices is not None and idx not in select_indices:
            continue
        detail = str(it.get("change_detail") or "")
        reason = str(it.get("change_reason") or "")
        if not (detail.strip() or reason.strip()):
            continue
        pname = str(it.get("part_name") or "")
        pnos = [p for p in (str(it.get("base_part_no") or ""), str(it.get("new_part_no") or "")) if p.strip()]
        # ①.5 구조 스코프용 모듈명 — PPT 추출(module_details 매칭) 항목의 module 플러밍.
        mods = [m for m in [str(it.get("module") or "").strip()] if m]
        prop = propose_candidate_sets(
            change_detail=detail,
            change_reason=reason,
            base_model=base_model,
            region=region,
            part_name=pname,
            part_nos=pnos,
            module_names=mods,
            session=session,
            backend=backend,
            llm=llm,
        )
        out.append({"item_index": idx, "item": it, **prop})
    return out


def propose_items(
    items: list[dict[str, Any]],
    *,
    base_model: str | None = None,
    region: str | None = None,
    max_items: int = 20,
    select_indices: set[int] | None = None,
) -> list[dict[str, Any]]:
    """UI 진입점 — PPT 변경항목 → **선택 항목만** 후보 부품 세트(정지). 실 Postgres + 설정 LLM.

    ``select_indices``(원본 인덱스 집합)가 주어지면 그 항목만 검색한다(전 항목 일괄 금지).
    """
    factory = _factory()
    backend = DbRetrievalBackend(factory)
    with factory() as s:
        return propose_from_items(
            items[:max_items], base_model=base_model, region=region, session=s,
            backend=backend, select_indices=select_indices,
        )


def _newbom_change_items(
    anchor_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """(PPT item, 채택 선택행) → ``apply_changes``용 change_item. 확정 new P/No로 덮어쓴다.

    new P/No 우선순위: 사용자 입력 > 회수 제안 > 빈값(apply가 ``<발번대기>`` 강제). 신규 무생성.
    """
    out: list[dict[str, Any]] = []
    for item, accepted in anchor_rows:
        new_pno = ""
        for r in accepted:
            cand = str(r.get("new_pno_input") or "").strip() or str(
                r.get("part_no_new_suggested") or ""
            ).strip()
            if cand:
                new_pno = cand
                break
        ci = dict(item)
        ci["new_part_no"] = new_pno  # 빈값이면 apply가 is_new + <발번대기>
        out.append(ci)
    return out


def confirm_items_and_newbom(
    *,
    proposals: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    bom: Any = None,
    base_model: str | None = None,
    session: Session,
    bom_repo: BomRepository | None = None,
    llm: Any = None,
    session_id: str = "ui",
) -> dict[str, Any]:
    """다항목 확정 → 항목별 하위 전개 + (베이스 BOM 있으면) New BOM. 주입형.

    ``selections``: 집계 확정 에디터 행(각 행에 ``_item_index``). 채택된 항목만
    ``confirm_and_expand``(절대원칙 #4) + 확정 new P/No로 베이스 BOM 트리 적용. New BOM은
    단일 소스(``apply_changes``) + ``validate_new_bom`` 게이트.
    """
    intent_by_idx = {p["item_index"]: p["intent"] for p in proposals}
    item_by_idx = {p["item_index"]: p.get("item", {}) for p in proposals}
    cands_by_idx = {p["item_index"]: p.get("candidates") for p in proposals}
    rmeta_by_idx = {p["item_index"]: p.get("retrieval_meta") for p in proposals}

    groups: dict[int, list[dict[str, Any]]] = {}
    for r in selections:
        groups.setdefault(int(r.get("_item_index", 0)), []).append(r)

    item_results: list[dict[str, Any]] = []
    anchor_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for idx in sorted(groups):
        sels = groups[idx]
        accepted = [s for s in sels if s.get("accept")]
        if not accepted or idx not in intent_by_idx:
            continue
        confirmed = confirm_and_expand(
            intent=intent_by_idx[idx],
            selections=sels,
            session=session,
            session_id=session_id,
            bom_repo=bom_repo,
            llm=llm,
            candidates=cands_by_idx.get(idx),
            retrieval_meta=rmeta_by_idx.get(idx),
        )
        item = item_by_idx.get(idx, {})
        item_results.append({"item_index": idx, "item": item, "confirmed": confirmed})
        anchor_rows.append((item, accepted))

    new_bom: dict[str, Any] | None = None
    if bom is not None and anchor_rows:
        nb = apply_changes(bom, _newbom_change_items(anchor_rows))
        new_bom = _serialize_newbom(nb)
        new_bom["violations"] = validate_new_bom(nb)
        if not new_bom["violations"]:
            # 서식보존 xlsx — 원본 base BOM 리로드 후 주석 컬럼 가산. openpyxl 실패 시 CSV로 graceful.
            import base64
            try:
                new_bom["xlsx_b64"] = base64.b64encode(write_newbom_xlsx(bom, nb)).decode()
            except Exception as exc:  # noqa: BLE001
                new_bom["xlsx_error"] = str(exc)[:200]

    return {"items": item_results, "new_bom": new_bom, "base_model": base_model or ""}


def confirm_items(
    *,
    proposals: list[dict[str, Any]],
    selections: list[dict[str, Any]],
    base_bom_bytes: bytes | None = None,
    base_bom_name: str = "base_bom.xlsx",
    base_model: str | None = None,
    session_id: str = "ui",
) -> dict[str, Any]:
    """UI 진입점 — 다항목 확정 → 전개 + New BOM. 실 Postgres + 설정 LLM(.env — Claude/Ollama opt-in)."""
    factory = _factory()
    bom = parse_base_bom(base_bom_bytes, file_name=base_bom_name) if base_bom_bytes else None
    with factory() as s:
        return confirm_items_and_newbom(
            proposals=proposals,
            selections=selections,
            bom=bom,
            base_model=base_model,
            session=s,
            bom_repo=EdgeBomRepository(s),
            session_id=session_id,
        )


# ════════════════════════════════════════════════════════════════
# 통합 개발부품 Master(Compact v1.1) 내보내기 + 정확도 (검수 UI; additive).
#   change_list_from_result : 확정 결과(new_bom) → 검수/편집용 평면 변경 리스트.
#   build_master_xlsx       : (편집된) UI 행 → MasterRow → v1.1 서식 xlsx bytes.
#   score_master_xlsx       : 내보낸 xlsx vs 정답지 채점(선택 패널).
# 모두 LLM/DB/네트워크 0회. 분류 한글→영어(신규/변경/기존/삭제 → New/Change/Common/Delete)는
# confirm.classification_ko_to_en 단일 출처. 신규 무번호 → <발번대기>(master_writer 강제).
# ════════════════════════════════════════════════════════════════

# 정답지(통합 개발부품Master Compact v1.1.xlsx) — 내보내기 템플릿 겸 정확도 oracle.
# 레포 루트 바깥(LG_Data_pipeline/) 상위 두 단계 위에 위치. env로 override 가능.
import os as _os  # noqa: E402

_DEFAULT_MASTER_TEMPLATE = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    "통합 개발부품Master Compact v1.1.xlsx",
)


def master_template_path() -> str:
    """v1.1 템플릿/정답지 경로 — MASTER_TEMPLATE_PATH env 우선, 없으면 기본(레포 상위)."""
    return _os.environ.get("MASTER_TEMPLATE_PATH", _DEFAULT_MASTER_TEMPLATE)


# new_bom status(KEEP/MODIFY/ADD/DELETE) → 분류 한글 기본값(사용자가 셀렉트로 덮어쓸 수 있음).
_STATUS_TO_KO_CLS = {"KEEP": "기존", "MODIFY": "변경", "ADD": "신규", "DELETE": "삭제"}


def change_list_from_result(result: dict[str, Any], *, changed_only: bool = True) -> list[dict[str, Any]]:
    """확정 결과(confirm_items 반환)의 new_bom → 검수/편집용 변경 리스트 행.

    각 행에 식별(부품명/Base·New P/No, 읽기전용)과 자유편집(변경내용/변경사유/분류) 컬럼을
    담는다. 분류 기본값은 new_bom status에서 유도(사용자가 셀렉트로 수정). new_bom이 없으면
    빈 리스트(베이스 BOM 미업로드 등). LLM/DB 0회.

    Args:
        result: ``confirm_items``/``confirm_items_and_newbom`` 반환 dict.
        changed_only: True면 변경행(MODIFY/ADD/DELETE)만, False면 전체 트리.
    """
    nb = (result or {}).get("new_bom") or {}
    src_rows = nb.get("changed" if changed_only else "full") or []
    out: list[dict[str, Any]] = []
    for r in src_rows:
        status = str(r.get("status") or "")
        out.append(
            {
                "BOM Level": str(r.get("depth", "") or ""),
                "Part Type": str(r.get("type", "") or ""),
                "부품명": str(r.get("part_name", "") or ""),
                "Base P/No": str(r.get("base P/N", "") or ""),
                "New P/No": str(r.get("new P/N", "") or ""),
                "Q'ty": str(r.get("qty", "") or ""),
                "변경내용": str(r.get("bom_path", "") or ""),
                "변경사유": str(r.get("사유", "") or ""),
                "분류": _STATUS_TO_KO_CLS.get(status, "변경"),
                "_status": status,
                "_src": str(r.get("출처", "") or ""),
                "_is_new": bool(r.get("is_new")),
            }
        )
    return out


def _master_row_from_edit(r: dict[str, Any]) -> Any:
    """편집된 UI 변경리스트 행 → MasterRow (분류 한글→영어, 신규 무번호→<발번대기>).

    new_pno 우선순위는 master_writer 규약과 동일: 분류가 Common이면 '←'(미변경),
    Delete면 'X'(또는 교체품번), 그 외엔 New P/No(없으면 <발번대기>). master_writer가
    DiffRow 경유로 강제하던 셀 규약을 여기서 직접 재현한다(편집행은 DiffRow가 아니므로).
    """
    from src.agent.docgen.master_writer import MasterRow  # noqa: PLC0415

    cls_en = classification_ko_to_en(r.get("분류"))
    base_pno = str(r.get("Base P/No", "") or "").strip()
    new_pno_raw = str(r.get("New P/No", "") or "").strip()
    qty = str(r.get("Q'ty", "") or "").strip()

    if cls_en == "Common":
        new_pno_cell, qty_new, jp, kr = "←", "←", "-", "-"
    elif cls_en == "Delete":
        new_pno_cell = new_pno_raw or "X"
        qty_new, jp = "X", "부품 삭제"
        kr = str(r.get("변경사유", "") or "").strip() or "-"
    else:  # New / Change — base→new 번호 교체 또는 신규
        # 신규인데 번호가 없거나 placeholder류면 <발번대기>(절대원칙 — 시스템 무생성).
        new_pno_cell = new_pno_raw if new_pno_raw and new_pno_raw != "<발번대기>" else "<발번대기>"
        qty_new = qty
        jp = "부품 추가" if cls_en == "New" else "부품 변경"
        kr = str(r.get("변경사유", "") or "").strip() or "-"

    return MasterRow(
        bom_level=str(r.get("BOM Level", "") or ""),
        part_type=str(r.get("Part Type", "") or ""),
        base_pno=base_pno,
        new_pno=new_pno_cell,
        part_name=str(r.get("부품명", "") or ""),
        qty_base=qty,
        qty_new=qty_new,
        changing_point=jp,
        changing_reason=kr,
        supplier="-",
        classification=cls_en,
    )


def build_master_xlsx(
    rows: list[dict[str, Any]], *, template_path: str | None = None
) -> bytes:
    """편집된 변경리스트 행 목록 → 통합 master(v1.1 서식) xlsx bytes.

    각 행을 MasterRow로 매핑(분류 한글→영어, 신규 무번호→<발번대기>) 후
    ``write_master_xlsx``로 정답지 서식 보존 출력. st.download_button에 그대로 넣는다.
    """
    from src.agent.docgen.master_writer import write_master_xlsx  # noqa: PLC0415

    master_rows = [_master_row_from_edit(r) for r in rows]
    return write_master_xlsx(master_rows, template_path or master_template_path())


def export_rows_preview(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """내보낼 MasterRow 미리보기(st.dataframe용 dict). build_master_xlsx와 동일 매핑."""
    out: list[dict[str, Any]] = []
    for r in rows:
        mr = _master_row_from_edit(r)
        out.append(
            {
                "BOM Level": mr.bom_level,
                "Part Type": mr.part_type,
                "Base P/No": mr.base_pno,
                "New P/No": mr.new_pno,
                "부품명": mr.part_name,
                "Q'ty Base": mr.qty_base,
                "Q'ty New": mr.qty_new,
                "변경점": mr.changing_point,
                "변경사유": mr.changing_reason,
                "양산처": mr.supplier,
                "분류": mr.classification,
            }
        )
    return out


def score_master_xlsx(produced_bytes: bytes, *, oracle_path: str | None = None) -> dict[str, Any]:
    """내보낸 통합 master xlsx(bytes) vs 정답지 채점. 게이팅/리콜 dict 반환.

    produced bytes를 임시파일로 떨군 뒤 ``accuracy.score``로 비교한다. 정답지 경로
    기본값은 v1.1 템플릿(=oracle). 호출부는 try/except로 감싼다(절대 크래시 금지).
    """
    import tempfile  # noqa: PLC0415

    from src.agent.accuracy import score  # noqa: PLC0415

    oracle = oracle_path or master_template_path()
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        tf.write(produced_bytes)
        produced_path = tf.name
    try:
        return score(produced_path, oracle)
    finally:
        try:
            _os.unlink(produced_path)
        except OSError:
            pass


def extract_ppt(file_bytes: bytes, *, base_model_hint: str = "") -> dict[str, Any]:
    """심의회 PPT bytes → project_meta(base_model 포함) + 변경항목 리스트 (검색 없음).

    UI가 base_model/변경항목을 먼저 보여주고 확인/수정한 뒤 분석을 돌릴 수 있도록
    추출만 분리한 진입점. LLM/DB/네트워크 호출 0회.
    """
    ctx = {"source_model": base_model_hint} if base_model_hint else {}
    extraction = extract_change_review_from_pptx_bytes(file_bytes, ctx=ctx)
    pm = extraction.get("project_meta") or {}
    items = change_items_from_extraction(extraction)
    return {
        "project_meta": pm,
        "base_model": str(pm.get("base_model") or "").strip(),
        "items": [
            {
                "change_detail": it.change_detail,
                "change_reason": it.change_reason,
                "part_name": it.part_name,
                "module": it.module,
                "change_type": it.change_type,
                "base_part_no": it.base_part_no,
                "new_part_no": it.new_part_no,
                "concerns": it.concerns,
                "search_text": it.search_text,
                "src": it.src,
            }
            for it in items
        ],
    }


def _newbom_row(r: Any) -> dict[str, Any]:
    return {
        "status": r.status,
        "depth": r.depth,
        "bom_path": r.bom_path,
        "part_name": r.part_name,
        "base P/N": r.part_no_base,
        "new P/N": r.part_no_new,
        "is_new": r.is_new,
        "qty": r.qty,
        "type": r.type,
        "사유": r.reason,
        "출처": r.src,
    }


def _serialize_newbom(nb: NewBom) -> dict[str, Any]:
    return {
        "model": nb.model,
        "total_rows": len(nb.rows),
        "unmatched": nb.unmatched,
        "changed": [_newbom_row(r) for r in nb.changed_rows],
        "dev_master": [_newbom_row(r) for r in nb.dev_master_rows],
        "full": [_newbom_row(r) for r in nb.rows],
    }


def extract_base_bom(file_bytes: bytes, *, file_name: str = "base_bom.xlsx") -> dict[str, Any]:
    """베이스 BOM bytes → 모델/행수 요약 (UI 미리보기용). 변경 적용 전 단계."""
    bom = parse_base_bom(file_bytes, file_name=file_name)
    return {"model": bom.model, "rows": len(bom.rows), "columns": bom.columns}


# ════════════════════════════════════════════════════════════════
# 통합 Master — base BOM ↔ new BOM diff 기반 (결정론 가지치기 + 사유 보강; additive).
#   bom_diff_change_list      : base+new(+PPT) → 변경행 평면 리스트(confidence·사유초안) + 요약.
#   build_master_from_diff_rows : 편집된 변경행 → 풀 diff에 사유/분류 덮어쓰기 → v1.1 xlsx bytes.
# 후보-확정 경로(change_list_from_result/build_master_xlsx)와 **별개**다 — 이쪽은 PPT가 품번을
# 거의 안 주는 상황에서 base↔new BOM 집합차로 *구조화된* 변경(어느 품번이 무엇으로)을 만든다.
# LLM/DB/네트워크 0회. 신규 무번호 → <발번대기>(master_writer 강제). 분류 한글→영어는
# classification_ko_to_en 단일 출처.
# ════════════════════════════════════════════════════════════════


def _diff_reason_key(base_pno: str, new_pno: str) -> str:
    """rows_from_diff/ppt_reason_map 공용 사유 키 — base_pno 우선, 없으면 new_pno (대문자·trim).

    rows_from_diff는 ``key = d.base_pno or d.new_pno``로 reasons를 찾고, ppt_reason_map은
    ``(base_pno or new_pno).upper()``로 키를 만든다. 둘을 일치시키려고 여기서 동일 정규화한다.
    """
    return (str(base_pno or "") or str(new_pno or "")).strip().upper()


def _diff_full_rows(
    base_bytes: bytes, base_name: str, new_bytes: bytes, new_name: str,
):
    """base+new bytes → (base BaseBom, new BaseBom, refined DiffRow 전체). 내부 공용."""
    from src.agent.basebom.diff import diff_boms  # noqa: PLC0415
    from src.agent.basebom.refine import refine_diff  # noqa: PLC0415

    base = parse_base_bom(base_bytes, file_name=base_name)
    new = parse_base_bom(new_bytes, file_name=new_name)
    refined = refine_diff(diff_boms(base, new), base, new)
    return base, new, refined


def bom_diff_change_list(
    base_bytes: bytes,
    base_name: str,
    new_bytes: bytes,
    new_name: str,
    ppt_bytes: bytes | None = None,
) -> dict[str, Any]:
    """base BOM ↔ new BOM diff(가지치기·신뢰도) + PPT 사유보강 → 검수/편집용 변경 리스트.

    base+new를 파싱해 ``diff_boms``→``refine_diff``로 변경 집합을 만들고(가짜삭제→Common
    재분류, confidence high/medium/low), ``ppt_bytes``가 있으면 ``ppt_reason_map``으로 변경사유
    초안을 매핑한다(없으면 빈 맵). ``rows``는 **변경행**(classification != "Common")만 담은
    편집용 dict 리스트(base_pno/new_pno/part_name/bom_level/part_type/qty/classification/
    confidence/changing_reason/changing_point). ``summary``=``refine_summary``, ``common_count``도
    함께 반환. LLM/DB/네트워크 0회. 절대원칙: 신규 무번호 회수만, 임의 생성 금지.
    """
    from src.agent.basebom.refine import refine_summary  # noqa: PLC0415

    _base, _new, refined = _diff_full_rows(base_bytes, base_name, new_bytes, new_name)

    reasons: dict[str, str] = {}
    if ppt_bytes:
        try:
            extraction = extract_change_review_from_pptx_bytes(ppt_bytes)
            ppt_changes = extraction.get("detailed_changes") or []
            reasons = ppt_reason_map(refined, ppt_changes)
        except Exception:  # noqa: BLE001 — PPT 파싱 실패해도 사유 없이 진행
            reasons = {}

    # 변경점(J) 거친 매핑 — master_writer._COARSE_POINT와 동일 어휘.
    _point = {"New": "부품 추가", "Change": "부품 변경", "Delete": "부품 삭제"}
    rows: list[dict[str, Any]] = []
    common_count = 0
    for d in refined:
        if d.classification == "Common":
            common_count += 1
            continue
        key = _diff_reason_key(d.base_pno, d.new_pno)
        rows.append(
            {
                "base_pno": d.base_pno,
                "new_pno": d.new_pno,
                "part_name": d.part_name,
                "bom_level": d.lvl,
                "part_type": d.part_type,
                "qty": d.qty,
                "classification": d.classification,   # English (New/Change/Delete)
                "confidence": d.confidence,            # high/medium/low (읽기전용)
                "changing_reason": reasons.get(key, ""),
                "changing_point": _point.get(d.classification, ""),
            }
        )
    return {
        "rows": rows,
        "summary": refine_summary(refined),
        "common_count": common_count,
    }


def build_master_from_diff_rows(
    edited_rows: list[dict[str, Any]],
    base_bytes: bytes,
    base_name: str,
    new_bytes: bytes,
    new_name: str,
    *,
    include_common: bool = True,
    confidence_keep: set[str] | None = None,
) -> bytes:
    """편집된 변경행 → 통합 master(v1.1 서식) xlsx bytes (base↔new diff 기반).

    풀 diff를 재실행해 **Common 이월행까지 포함한 전체 행**으로 v1.1 형태를 만들고, 사용자가
    편집한 변경행의 ``changing_reason``/``classification``을 base_pno+new_pno+part_name 키로
    덮어쓴다. ``confidence_keep``(예: {"high","medium"})가 주어지면 confidence가 그 집합에
    없는 **변경행만** 드롭한다(Common은 항상 유지 — 단, include_common=False면 Common 제외).
    ``rows_from_diff``로 MasterRow 매핑 후 ``write_master_xlsx``. 신규 무번호→<발번대기>는
    master_writer가 강제. LLM/DB/네트워크 0회.

    Args:
        edited_rows: ``bom_diff_change_list``의 ``rows``를 사용자가 편집한 결과(분류는 한글 또는
            영어 라벨 허용 — classification_ko_to_en로 정규화).
        confidence_keep: 유지할 confidence 집합. None이면 신뢰도 필터 없음.
        include_common: False면 Common 이월행 제외(변경행만 내보냄).
    """
    _base, _new, refined = _diff_full_rows(base_bytes, base_name, new_bytes, new_name)

    # 편집 결과 인덱스 — (base_pno, new_pno, part_name) 정규화 키. diff가 같은 키를 중복 생성할
    # 수 있어 dict 마지막 값이 이긴다(UI는 동일 키 행을 거의 만들지 않음).
    def _ekey(bp: Any, np_: Any, nm: Any) -> tuple[str, str, str]:
        return (
            str(bp or "").strip().upper(),
            str(np_ or "").strip().upper(),
            str(nm or "").strip().upper(),
        )

    edits: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in edited_rows or []:
        edits[_ekey(r.get("base_pno"), r.get("new_pno"), r.get("part_name"))] = r

    keep_rows = []
    overridden_reasons: dict[str, str] = {}
    for d in refined:
        if d.classification == "Common":
            if include_common:
                keep_rows.append(d)
            continue
        ek = _ekey(d.base_pno, d.new_pno, d.part_name)
        ed = edits.get(ek)
        # 신뢰도 필터 — 변경행만. 편집행에 confidence가 있으면 그 값을, 없으면 diff 값을 본다.
        conf = str((ed or {}).get("confidence") or d.confidence or "")
        if confidence_keep is not None and conf and conf not in confidence_keep:
            continue
        # 분류 덮어쓰기 — 편집값(한글/영어) → 영어. 미편집이면 diff 분류 유지.
        if ed is not None and str(ed.get("classification") or "").strip():
            new_cls = classification_ko_to_en(ed.get("classification"))
            d = _replace_diffrow(d, classification=new_cls)
        # 사유 덮어쓰기 — rows_from_diff의 reasons 맵에 base_pno|new_pno 키로 주입.
        if ed is not None:
            rr = str(ed.get("changing_reason") or "").strip()
            if rr:
                overridden_reasons[_diff_reason_key(d.base_pno, d.new_pno)] = rr
        keep_rows.append(d)

    from src.agent.docgen.master_writer import rows_from_diff, write_master_xlsx  # noqa: PLC0415

    master_rows = rows_from_diff(keep_rows, reasons=overridden_reasons)
    return write_master_xlsx(master_rows, master_template_path())


def _replace_diffrow(d: Any, **changes: Any) -> Any:
    """DiffRow(dataclass) 얕은 치환 — dataclasses.replace 래퍼(가독성용)."""
    from dataclasses import replace as _dc_replace  # noqa: PLC0415

    return _dc_replace(d, **changes)


__all__ = [
    "bom_diff_change_list",
    "build_master_from_diff_rows",
    "build_master_xlsx",
    "change_list_from_result",
    "confirm_and_expand",
    "confirm_change_item",
    "confirm_items",
    "confirm_items_and_newbom",
    "export_rows_preview",
    "extract_base_bom",
    "extract_ppt",
    "master_template_path",
    "propose_candidate_sets",
    "propose_change_item",
    "propose_from_items",
    "propose_items",
    "score_master_xlsx",
]
