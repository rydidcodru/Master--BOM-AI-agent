"""E2E HITL 경로 — propose(정지) → [사용자 확정] → analyze_confirmed(전개+판정+문서).

자유텍스트/PPT 변경항목 → ChangeIntent(L1) → 후보 부품 세트(L2 propose, **정지**) →
확정 닻(confirm) → 하위 전개(L2 expand) → 영향 판정(L3) → 문서(L4). 확정이 트리 전개보다
먼저(절대원칙 #4). retrieval 백엔드/LLM은 주입 가능(테스트 fake, 운영 DbRetrievalBackend+Ollama).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.confirm.models import ConfirmedAnchor
from src.agent.docgen.generator import (
    DocItem,
    GeneratedDoc,
    SourceRef,
    generate,
    source_ref_for_pno,
)
from src.agent.impact.cascade import companion_info
from src.agent.impact.models import ImpactInput, ImpactVerdict
from src.agent.impact.rules import compat_breaks, compat_up_depth, evaluate_many
from src.agent.intent.derive import env_flag
from src.agent.mapping.models import MappedEvent
from src.agent.intent.models import ChangeIntent
from src.agent.intent.structurizer import structurize
from src.agent.llm.client import LlmClient, default_llm, llm_enabled
from src.agent.orchestrator.backend import RetrievalBackend
from src.agent.orchestrator.orchestrate import (
    ExpansionResult,
    ProposalResult,
    expand_confirmed,
    propose_candidates,
)
from src.agent.repository.bom import BomRepository
from src.agent.repository.impact_graph import walk_impacted_ancestors
from src.db.models import BomEdge, DevPartMaster


def _escalate_parents(
    intent: ChangeIntent,
    seed_pnos: Sequence[str | None],
    session: Session,
    bom_repo: BomRepository | None,
    seen: set[str],
) -> list[ImpactInput]:
    """"호환성 깨질 때만 상위 채반"(설계 ⑤). 깨질 때만 seed들의 부모를 walk_subtree(up)로
    회수해 ``r_up_break=True`` parent 입력 생성 → R06/STRUCT_parent_chaeban(ADD CASCADE).

    BOM 루트(모델코드 등 dev_part_master에 행이 없는 = 출처 없는) 노드는 제외한다
    (절대원칙: 출처 없는 부품은 출력하지 않는다). 안 깨지거나 bom_repo 없으면 빈 리스트.
    """
    broke, _why = compat_breaks(intent.change_attribute, intent.raw_text)
    if not broke or bom_repo is None:
        return []
    up_depth = compat_up_depth()
    # ms IMPACTS_LINE 차용 — 상위 영향을 거리(distance)·관계(direct/parent/ancestor)로 등급화한
    # 1급 구조로 회수(DAG 최단거리 dedup). seed 자신은 relation='seed'로 이미 처리 → include_self=False.
    cand: list[tuple[str, int]] = []
    for pno in seed_pnos:
        if not pno:
            continue
        fid = session.execute(
            select(BomEdge.file_id).where(BomEdge.child_pno == pno).limit(1)
        ).scalar()
        cand.extend(
            (n.part_no, n.distance)
            for n in walk_impacted_ancestors(
                bom_repo, pno, max_depth=up_depth, file_id=fid, include_self=False
            )
        )
    cand_pnos = {p for p, _ in cand if p not in seen}
    real: set[str] = set()
    if cand_pnos:
        real = {
            r[0]
            for r in session.execute(
                select(DevPartMaster.part_no_new).where(
                    DevPartMaster.part_no_new.in_(cand_pnos)
                )
            ).all()
        }
    out: list[ImpactInput] = []
    for pno, depth in cand:
        if pno in seen or pno not in real:  # 미존재(출처 없음) BOM 루트 제외
            continue
        seen.add(pno)
        out.append(
            ImpactInput(
                part_no=pno,
                relation="parent",
                depth=depth,
                change_attribute=intent.change_attribute,
                r_up_break=True,
            )
        )
    return out


# ════════════════════════════════════════════════════════════════
# HITL 준수 2단계 (Phase 4): propose_analysis(정지) → [사용자 확정] → analyze_confirmed.
# 헤드리스(CLI/배치)는 propose_analysis까지만. 확정·전개는 UI에서 트리거(절대원칙 #4).
# ════════════════════════════════════════════════════════════════


@dataclass
class ConfirmedAnalysis:
    """analyze_confirmed 산출 — 확정 닻 기준 전개 + 판정 + 문서(L3·L4).

    P5: cascade 레벨창 전사는 제거됐다. 채택 이벤트의 동반변경은 ``doc.checklist`` 말미
    "참고: 과거 동반 변경" 정보행으로만 표시된다(BOM/개발마스터 행으로는 안 나감).
    """

    intent: ChangeIntent
    anchors: list[ConfirmedAnchor]
    expansion: ExpansionResult
    verdicts: list[ImpactVerdict]
    doc: GeneratedDoc


def build_adopted_mappings(
    intent: ChangeIntent,
    adopted_event_ids: set[int] | list[int],
    session: Session,
) -> list[MappedEvent]:
    """채택 이벤트에 한해 ②.5 매핑을 **lazy 계산** (P6 — 확정 후·전개 직전 1회).

    후보 탐색 시점엔 매핑을 만들지 않고(struct_score면 충분), 사람이 채택한 이벤트에
    대해서만 ``map_event_to_scope``를 호출한다. ``SEARCH_INCLUDE_STRUCT`` off이거나
    모듈 스코프(S) 구축 실패면 빈 리스트(= companion 정보행도 안 나옴). ``ScopeIndex``는
    1회 구축해 채택 이벤트들이 공유(이중 생성 금지).
    """
    ids = [int(e) for e in adopted_event_ids if e is not None]
    if not ids or not env_flag("SEARCH_INCLUDE_STRUCT", default=False):
        return []
    from src.agent.mapping.mapper import map_event_to_scope
    from src.agent.matching.scoring import MatchThresholds
    from src.agent.orchestrator.structure_scope import build_scope
    from src.db.retrieve import lookup_lines_by_event

    scope = build_scope(
        intent.module_names, intent.base_model, session, MatchThresholds.from_env()
    )
    if scope is None:
        return []
    out: list[MappedEvent] = []
    for eid in dict.fromkeys(ids):  # 순서 보존 dedup
        lines = lookup_lines_by_event(session, eid)
        if lines:
            out.append(map_event_to_scope(lines, scope))
    return out


def _adopted_line_ids(anchors: list[ConfirmedAnchor], mapping: MappedEvent) -> set:
    """매핑 라인 중 사람이 채택한 것(닻 품번과 일치)의 line_id 집합."""
    anchor_pnos = {
        a.part_no_new for a in anchors if a.part_no_new and not a.is_placeholder
    } | {a.part_no_base for a in anchors if a.part_no_base}
    ids: set = set()
    for ml in mapping.lines:
        ref = ml.line_ref or {}
        if ref.get("base_pno") in anchor_pnos or ref.get("new_pno") in anchor_pnos:
            lid = ref.get("line_id")
            if lid is not None:
                ids.add(lid)
    return ids


def propose_analysis(
    text: str,
    *,
    session: Session,
    backend: RetrievalBackend,
    intent: ChangeIntent | None = None,
    llm: LlmClient | None = None,
    rerank: bool | None = None,
    expand: bool = False,
    **propose_kw: Any,
) -> ProposalResult:
    """헤드리스 1단계 — 텍스트 → 후보 부품 세트(**정지**). 트리 전개·문서 생성 안 함.

    L1 structurize(또는 주입 intent) → L2 propose_candidates. ``expand=True``면 검색 전 LLM
    멀티쿼리 확장(부품군/동의어), ``rerank=True``면 회수 후보를 LLM 관련도로 2단계 재정렬
    (RankGPT-style). 둘 다 reason-only·결정론 fallback. 사용자 확정 후 ``analyze_confirmed``.

    ``rerank`` 미지정(None)이면 env ``RERANK_LLM`` 게이트(기본 off)를 따른다 — on이면 게이트된
    ``default_llm()``(LLM_PROVIDER: anthropic=Claude / openai=GPT / ollama)로 재랭킹. LLM이
    꺼져 있으면(키/게이트 없음) 결정론 RRF 순서 그대로(무영향).
    """
    if rerank is None:
        rerank = env_flag("RERANK_LLM", default=False)
    if (rerank or expand) and llm is None and llm_enabled():
        llm = default_llm()
    if intent is None:
        intent = structurize(text, llm=llm)
    return propose_candidates(
        intent, session=session, backend=backend, llm=llm,
        rerank=rerank, expand=expand, **propose_kw,
    )


def _anchor_impact_inputs(
    intent: ChangeIntent,
    anchors: list[ConfirmedAnchor],
    expansion: ExpansionResult,
    session: Session,
    bom_repo: BomRepository | None = None,
) -> list[ImpactInput]:
    """확정 닻(seed) + 전개 노드(child) → ImpactInput. _build_impact_inputs의 닻 버전."""
    inputs: list[ImpactInput] = []
    seen: set[str] = set()
    for a in anchors:
        if not a.part_no_new or a.is_placeholder:
            continue
        seen.add(a.part_no_new)
        inputs.append(
            ImpactInput(
                part_no=a.part_no_new,
                relation="seed",
                classification=a.classification,
                change_attribute=intent.change_attribute,
            )
        )
    child_pnos = {n.node.pno for n in expansion.tree if n.node.pno not in seen}
    attr: dict[str, tuple[str | None, str | None]] = {}
    if child_pnos:
        for pno, ptype, classif in session.execute(
            select(
                DevPartMaster.part_no_new,
                DevPartMaster.part_type,
                DevPartMaster.classification,
            ).where(DevPartMaster.part_no_new.in_(child_pnos))
        ).all():
            attr.setdefault(pno, (ptype, classif))
    for n in expansion.tree:
        pno = n.node.pno
        if pno in seen:
            continue
        seen.add(pno)
        ptype, classif = attr.get(pno, (None, None))
        inputs.append(
            ImpactInput(
                part_no=pno,
                relation="child",
                depth=n.node.depth,
                change_attribute=intent.change_attribute,
                part_type=ptype,
                classification=classif,
            )
        )
    # 상향(parent) 채반 — 호환성 깨질 때만(설계 ⑤). 출처 있는 실부품만.
    seed_pnos = [a.part_no_new for a in anchors if not a.is_placeholder]
    inputs.extend(_escalate_parents(intent, seed_pnos, session, bom_repo, seen))
    return inputs


def _anchor_doc_items(
    session: Session,
    anchors: list[ConfirmedAnchor],
    expansion: ExpansionResult,
    verdicts: list[ImpactVerdict],
) -> list[DocItem]:
    """확정 닻 + 전개 노드 → DocItem. <발번대기> 닻은 is_new=True(docgen이 placeholder 강제)."""
    by_pno: dict[str, ImpactVerdict] = {v.part_no: v for v in verdicts}
    items: list[DocItem] = []
    seen: set[str] = set()
    for a in anchors:
        pno = a.part_no_new
        if a.is_placeholder:
            # 발번대기 닻 — 번호 없음. 출처는 확정 시 source_ref(없으면 invalid).
            v = by_pno.get(pno)
            items.append(
                DocItem(
                    part_no=None,
                    is_new=True,
                    action=v.action if v else "ADD",
                    tier=v.tier if v else "CORE",
                    source=SourceRef(file_name=a.source_ref),
                    relation="seed",
                    reason="; ".join(f.reason for f in v.findings[:2]) if v else "",
                )
            )
            continue
        if not pno or pno not in by_pno:
            continue
        seen.add(pno)
        v = by_pno[pno]
        items.append(
            DocItem(
                part_no=pno,
                is_new=False,
                action=v.action,
                tier=v.tier,
                source=source_ref_for_pno(session, pno),
                relation="seed",
                reason="; ".join(f.reason for f in v.findings[:2]),
            )
        )
    for n in expansion.tree:
        pno = n.node.pno
        if pno in seen or pno not in by_pno:
            continue
        seen.add(pno)
        v = by_pno[pno]
        items.append(
            DocItem(
                part_no=pno,
                is_new=False,
                action=v.action,
                tier=v.tier,
                source=source_ref_for_pno(session, pno),
                relation="child",
                reason="; ".join(f.reason for f in v.findings[:2]),
            )
        )
    # 상향 채반 부모(relation=parent) — verdict엔 있으나 닻/전개에 없는 행. 출처 보유.
    for v in verdicts:
        if v.part_no in seen:
            continue
        seen.add(v.part_no)
        items.append(
            DocItem(
                part_no=v.part_no,
                is_new=False,
                action=v.action,
                tier=v.tier,
                source=source_ref_for_pno(session, v.part_no),
                relation="parent",
                reason="; ".join(f.reason for f in v.findings[:2]),
            )
        )
    return items


def analyze_confirmed(
    intent: ChangeIntent,
    anchors: list[ConfirmedAnchor],
    *,
    session: Session,
    bom_repo: BomRepository | None = None,
    llm: LlmClient | None = None,
    walk_depth: int = 4,
    adopted_mappings: list[MappedEvent] | None = None,
) -> ConfirmedAnalysis:
    """헤드리스 2단계(확정 후) — 확정 닻 → 하위 전개 + 영향 판정 + 문서(L3·L4).

    expand_confirmed(결정론, ``<발번대기>`` 제외) → 닻+자식 verdict(룰) → generate.

    ``adopted_mappings``(P5/P6, additive, 기본 None → 기존과 동일)는 채택된 이벤트의
    ②.5 ``MappedEvent``다. ``CASCADE_INFO``가 켜져 있고 매핑이 주어지면, 채택 이벤트의
    **동반변경(사람이 채택하지 않은 나머지 라인)**을 ``companion_info``로 만들어 문서
    체크리스트 말미 "참고: 과거 동반 변경" 정보행으로 표시한다(룰 판정과 무간섭 —
    BOM/개발마스터 행으로는 안 나감). 매핑은 채택 후 시점에 1회 계산됨(P6 lazy).
    """
    expansion = expand_confirmed(
        anchors, session=session, bom_repo=bom_repo, walk_depth=walk_depth
    )
    verdicts = evaluate_many(
        _anchor_impact_inputs(intent, anchors, expansion, session, bom_repo)
    )
    info_rows: list[object] = []
    if adopted_mappings and env_flag("CASCADE_INFO", default=True):
        for me in adopted_mappings:
            info_rows.extend(companion_info(me, _adopted_line_ids(anchors, me)))
    doc = generate(
        _anchor_doc_items(session, anchors, expansion, verdicts),
        llm=llm,
        info_rows=info_rows or None,
    )
    return ConfirmedAnalysis(
        intent=intent,
        anchors=list(anchors),
        expansion=expansion,
        verdicts=verdicts,
        doc=doc,
    )
