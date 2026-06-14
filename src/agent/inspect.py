"""정형화·검색 검수(inspection) — UI/CLI 공용 순수 로직.

입력(변경내역+변경사유) → ① 정형화(ChangeIntent; 결정론 reason-only + 선택적 Claude 보강)
→ ② change_event 검색(propose_candidates; 백엔드 자동 감지·우아한 폴백)을 검수용 구조체로
반환한다. Streamlit 등 표현 계층은 이 결과만 렌더한다(표현/로직 분리 → 테스트 용이).

정책(2026-06-10 개정, [[feedback-search-by-reason-not-id]]):
- 의미 인덱스는 **변경내역+변경사유**(reason_embedding)가 정답. 부품명/품번/모델이 주어지면
  결정론 ``intent_from_change`` 가 *부품 보강 쿼리*를 더해 검색의 parts 채널이 함께 매칭한다.
  미지정 시 reason-only 동작 유지. Claude 보강 쿼리도 검색에 쓰되 환각 식별자는 sanitize.
- 트리 전개는 하지 않는다(``propose_candidates``에서 멈춤 — HITL 게이트 이전 단계).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.agent.intent.models import ChangeIntent
from src.agent.intent.structurizer import intent_from_change, structurize
from src.agent.llm.client import LlmClient
from src.ontology import axioms
from src.agent.orchestrator.backend import DbRetrievalBackend
from src.agent.orchestrator.orchestrate import CandidateSet, propose_candidates
from src.agent.repository.bom import EdgeBomRepository
from src.utils.logging import get_logger

log = get_logger(__name__)


# ── 검수 결과 구조체 ────────────────────────────────────────────────────────


@dataclass
class FormalizationView:
    """① 정형화 결과 (reason-only 결정론 + Claude 보강 비교용)."""

    reason_intent: ChangeIntent          # 결정론 intent (사유 기준)
    reason_queries: list[str]            # reason_intent의 검색 쿼리(변경내역+변경사유)
    llm_intent: ChangeIntent | None      # Claude 보강 intent (속성/방향/요약/재작성)
    llm_queries: list[str]               # Claude 재작성 쿼리(원본)
    llm_provider: str                    # "anthropic" | "ollama" | "none" | "custom"
    llm_model: str | None
    llm_error: str | None = None
    # **실제 검색에 투입되는 쿼리** = reason_queries + sanitize(llm_queries) (중복 제거).
    # CLAUDE.md ②(정형화 agent의 multi-query 재작성)대로 Claude 재작성을 검색에 사용.
    search_queries: list[str] = field(default_factory=list)


@dataclass
class CandidateView:
    """② 검색 후보 1건 = 한 사유(change_event) + 함께 바뀐 부품 라인."""

    rank: int
    event_id: int
    base_model: str | None
    new_model: str | None
    change_log: str | None
    change_reason: str | None
    source_ref: str | None
    score_rrf: float | None
    score_semantic: float | None
    score_lexical: float | None
    score_sparse: float | None = None
    score_parts: float | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SearchView:
    """② 검색 결과 + 백엔드 상태 + 도구 트레이스."""

    status: str                          # ok | no_backend | no_data | error | skipped
    detail: str
    mode: str                            # hybrid | lexical-only | -
    candidates: list[CandidateView] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    reflections: int = 0


@dataclass
class BackendStatus:
    status: str                          # ok | no_backend | no_data
    detail: str
    mode: str
    factory: sessionmaker[Session] | None = None


@dataclass
class InspectionResult:
    formalization: FormalizationView
    search: SearchView
    queries_used: list[str]


@dataclass
class BomExpandNode:
    """③(전개) bom_edge walk_subtree 결과 노드 + 부품명."""

    pno: str
    from_pno: str
    depth: int
    qty: float | None
    bom_level: int | None
    part_name: str | None


@dataclass
class BomExpandView:
    """③ 코퍼스 BOM(bom_edge) 하위/상위 전개 결과."""

    anchor: str
    direction: str                       # down | up
    file_id: int | None
    file_name: str | None
    nodes: list[BomExpandNode] = field(default_factory=list)
    detail: str = ""


# ── ① 정형화 ────────────────────────────────────────────────────────────────


def formalize(
    *,
    change_log: str,
    change_reason: str,
    base_model: str | None = None,
    region: str | None = None,
    part_name: str | None = None,
    part_nos: list[str] | None = None,
    models: list[str] | None = None,
    llm: LlmClient | None = None,
) -> FormalizationView:
    """변경내역+변경사유(+부품명·식별자) → 정형화 뷰. ``llm`` 주어지면 Claude 보강 intent도 함께.

    ``part_name``/``part_nos``/``models``가 주어지면 reason 쿼리에 더해 부품 보강 쿼리를 만들고
    검색의 parts 채널이 부품명/품번/모델을 매칭한다(2026-06-10 확정). 미지정 시 reason-only 유지.
    """
    reason_intent = intent_from_change(
        change_detail=change_log,
        change_reason=change_reason,
        base_model=base_model,
        region=region,
        part_name=part_name,
        part_nos=part_nos,
        models=models,
    )

    llm_intent: ChangeIntent | None = None
    llm_queries: list[str] = []
    llm_error: str | None = None
    provider = "none"
    model: str | None = None

    if llm is not None:
        provider = getattr(llm, "provider", "custom")
        model = getattr(llm, "model", None)
        seed = reason_intent.raw_text or " / ".join(
            p for p in (change_log, change_reason) if p
        )
        try:
            llm_intent = structurize(seed, llm=llm)
            llm_queries = list(llm_intent.rewritten_queries)
        except Exception as exc:  # noqa: BLE001 — 네트워크/스키마 실패는 표시만, fallback 유지
            llm_error = f"{type(exc).__name__}: {exc}"
            log.warning("inspect.llm_failed", error=str(exc)[:200])

    reason_queries = list(reason_intent.rewritten_queries)
    # 검색 쿼리 = reason + Claude 재작성(sanitize 후) 합집합(중복 제거). 입력(변경내역+사유+
    # 부품명+식별자)에 없던 식별자만 Claude가 끌어들였으면 제거 — 환각 식별자 방어(사용자가
    # 준 부품명/품번은 allowed 토큰에 포함하므로 유지된다). 토큰 **정확 일치**로 비교
    # (부분문자열 매칭 금지 — 예: 'MCR6'가 'MCR68450803'에 묻혀 통과하는 것 방지).
    allowed_tokens = {
        t.strip(".,()/")
        for src in (change_log, change_reason, part_name or "", *(part_nos or []), *(models or []))
        for t in str(src or "").split()
    }
    allowed_tokens.discard("")
    search_queries = list(reason_queries)
    for q in llm_queries:
        sq = _sanitize_llm_query(q, allowed_tokens)
        if sq and sq not in search_queries:
            search_queries.append(sq)

    return FormalizationView(
        reason_intent=reason_intent,
        reason_queries=reason_queries,
        llm_intent=llm_intent,
        llm_queries=llm_queries,
        llm_provider=provider,
        llm_model=model,
        llm_error=llm_error,
        search_queries=search_queries,
    )


def _sanitize_llm_query(q: str, allowed_tokens: set[str]) -> str:
    """Claude 재작성에서 **입력에 없던** part_no/model 식별자 토큰만 제거.

    입력(변경내역/사유/부품명/사용자 식별자)에 원래 든 식별자는 허용(CLAUDE.md 카브아웃);
    Claude가 새로 끌어들인 식별자만 검색 쿼리에서 떼어낸다. ``allowed_tokens``와 **정확 일치**로
    판정한다(부분문자열 매칭 금지).
    """
    kept: list[str] = []
    for tok in q.split():
        bare = tok.strip(".,()/")
        is_id = axioms.validate_part_no(bare) or axioms.validate_model_code(bare)
        if is_id and bare not in allowed_tokens:
            continue
        kept.append(tok)
    return " ".join(kept).strip()


# ── ② 검색 (백엔드 자동 감지 → propose_candidates) ───────────────────────────


def detect_backend(session_factory: sessionmaker[Session] | None = None) -> BackendStatus:
    """검색 백엔드 가용성 자동 감지. (Postgres 연결 + change_event 적재 + 임베딩 모드)"""
    sf = session_factory
    if sf is None:
        try:
            from src.db.engine import make_engine, session_factory as _mk

            sf = _mk(make_engine())
        except Exception as exc:  # noqa: BLE001
            return BackendStatus("no_backend", f"엔진 생성 실패: {exc}", "-", None)

    try:
        with sf() as s:
            s.execute(text("SELECT 1"))
            count = int(s.execute(text("SELECT count(*) FROM change_event")).scalar() or 0)
    except Exception as exc:  # noqa: BLE001 — DB 미기동/스키마 없음 등
        return BackendStatus("no_backend", f"DB 연결/스키마 확인 실패: {exc}", "-", None)

    if count == 0:
        return BackendStatus(
            "no_data",
            "change_event 테이블이 비어 있음 — `db load`(신규 적재) 또는 기존 적재분은 "
            "`db change-events --embed`로 change_event/reason_embedding을 채우세요.",
            "-",
            sf,
        )

    from src.embed.embedder import embedding_enabled

    mode = "hybrid (semantic+lexical)" if embedding_enabled() else "lexical-only (ENABLE_EMBEDDING=0)"
    return BackendStatus("ok", f"change_event {count}건 적재됨", mode, sf)


def _to_candidate_view(rank: int, cs: CandidateSet) -> CandidateView:
    ev = cs.event
    lines = [
        {
            "seq": getattr(ln, "seq", None),
            "bom_level": getattr(ln, "bom_level", None),
            "part_type": getattr(ln, "part_type", None),
            "base_pno": getattr(ln, "base_pno", None),
            "new_pno": getattr(ln, "new_pno", None),
            "part_name": getattr(ln, "part_name", None),
            "changepoint": getattr(ln, "changepoint", None),
            "classification": getattr(ln, "classification", None),
            "qty_base": getattr(ln, "qty_base", None),
            "qty_new": getattr(ln, "qty_new", None),
            "supplier": getattr(ln, "supplier", None),
            "source_ref": getattr(ln, "source_ref", None),
        }
        for ln in cs.lines
    ]
    return CandidateView(
        rank=rank + 1,
        event_id=ev.event_id,
        base_model=ev.base_model,
        new_model=ev.new_model,
        change_log=ev.change_log,
        change_reason=ev.change_reason,
        source_ref=ev.source_ref,
        score_rrf=ev.score_rrf,
        score_semantic=ev.score_semantic,
        score_lexical=ev.score_lexical,
        score_sparse=getattr(ev, "score_sparse", None),
        score_parts=getattr(ev, "score_parts", None),
        lines=lines,
    )


def run_search(
    reason_intent: ChangeIntent,
    queries: list[str],
    backend: BackendStatus,
    *,
    top_k: int = 5,
    write_log: bool = False,
) -> SearchView:
    """전달된 ``queries``로 change_event 검색(propose_candidates). 백엔드 미가용 시 폴백.

    주의: 이 함수는 **건네받은 queries를 그대로** 검색한다 — 절대원칙(검색 키=변경내역+변경사유,
    식별자 미포함)의 보장은 **호출부 책임**이다. inspect_query 기본값과 UI는 reason-only
    쿼리(``intent_from_change`` 산출)만 넘긴다. ``write_log=False``(기본)는 검수용 — tool_call_log
    미기록(매 rerun 오염 방지).
    """
    if backend.status != "ok" or backend.factory is None:
        return SearchView(status=backend.status, detail=backend.detail, mode=backend.mode)
    if not queries:
        return SearchView(status="skipped", detail="검색 쿼리가 비어 있음", mode=backend.mode)

    intent = reason_intent.model_copy(update={"rewritten_queries": list(queries)})
    sf = backend.factory
    impl = DbRetrievalBackend(sf)
    try:
        with sf() as s:
            result = propose_candidates(
                intent,
                session=s,
                backend=impl,
                per_query_top_k=top_k,
                max_candidates=top_k * 2,
                write_log=write_log,
                dedup_by_reason=True,  # 같은 변경 중복 제거 → 다양한 유사 변경 노출
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("inspect.search_failed", error=str(exc)[:200])
        return SearchView(status="error", detail=f"{type(exc).__name__}: {exc}", mode=backend.mode)

    candidates = [_to_candidate_view(i, c) for i, c in enumerate(result.candidates)]
    trace = [
        {
            "tool": r.tool_name,
            "status": r.status,
            "count": r.result_count,
            "ms": r.latency_ms,
            "error": r.error,
        }
        for r in result.trace
    ]
    return SearchView(
        status="ok",
        detail=backend.detail,
        mode=backend.mode,
        candidates=candidates,
        trace=trace,
        reflections=result.reflections,
    )


# ── 통합 진입점 ──────────────────────────────────────────────────────────────


def inspect_query(
    *,
    change_log: str,
    change_reason: str,
    base_model: str | None = None,
    region: str | None = None,
    llm: LlmClient | None = None,
    queries: list[str] | None = None,
    session_factory: sessionmaker[Session] | None = None,
    top_k: int = 5,
) -> InspectionResult:
    """정형화 + 검색을 한 번에. ``queries=None``이면 reason 기준 쿼리로 검색(절대원칙)."""
    fv = formalize(
        change_log=change_log,
        change_reason=change_reason,
        base_model=base_model,
        region=region,
        llm=llm,
    )
    backend = detect_backend(session_factory)
    chosen = list(queries) if queries else fv.search_queries
    sv = run_search(fv.reason_intent, chosen, backend, top_k=top_k)
    return InspectionResult(formalization=fv, search=sv, queries_used=chosen)


# ── ③ BOM 하위 전개 (코퍼스 bom_edge, 결정론 walk_subtree) ────────────────────


def expand_bom_db(
    anchor: str,
    *,
    direction: str = "down",
    max_depth: int = 2,
    session_factory: sessionmaker[Session] | None = None,
) -> BomExpandView:
    """``bom_edge``(적재된 코퍼스 BOM) 하위/상위 전개. LLM 호출 0(결정론).

    ``file_id``는 anchor가 등장하는 BOM 파일로 자동 선택(하위 전개면 parent로 나오는 파일을
    우선, 없으면 child). HITL 확정 이후 ⑤ 단계의 ``walk_subtree``를 검수 UI에서 바로 보기 위한
    얇은 래퍼다. 업로드 BOM 스냅샷(``walk_base_bom_subtree``)과 달리 적재된 DAG를 순회한다.
    """
    anchor = (anchor or "").strip()
    if not anchor or session_factory is None:
        return BomExpandView(anchor, direction, None, None, detail="anchor/backend 없음")
    pref_col = "parent_pno" if direction == "down" else "child_pno"
    with session_factory() as s:
        fid = s.execute(
            text(f"SELECT file_id FROM bom_edge WHERE {pref_col} = :p LIMIT 1"), {"p": anchor}
        ).scalar()
        if fid is None:  # 반대 방향 컬럼에라도 있으면 그 파일로
            fid = s.execute(
                text("SELECT file_id FROM bom_edge WHERE parent_pno = :p OR child_pno = :p LIMIT 1"),
                {"p": anchor},
            ).scalar()
        if fid is None:
            return BomExpandView(anchor, direction, None, None, detail="bom_edge에 없는 부품")
        fname = s.execute(
            text("SELECT file_name FROM source_files WHERE file_id = :f"), {"f": fid}
        ).scalar()
        raw = EdgeBomRepository(s).walk_subtree(
            anchor, direction=direction, max_depth=max_depth, file_id=fid  # type: ignore[arg-type]
        )
        names = dict(
            s.execute(
                text(
                    "SELECT part_no_new, max(part_name) FROM dev_part_master "
                    "WHERE file_id = :f AND part_no_new IS NOT NULL GROUP BY part_no_new"
                ),
                {"f": fid},
            ).all()
        )
        nodes = [
            BomExpandNode(n.pno, n.from_pno, n.depth, n.qty, n.bom_level, names.get(n.pno))
            for n in raw
        ]
    return BomExpandView(anchor, direction, int(fid), fname, nodes=nodes)


def build_llm_from_env() -> tuple[LlmClient | None, str | None]:
    """env 설정으로 LLM 클라이언트 생성. 실패 시 (None, 사유 메시지)."""
    from src.agent.llm.client import current_provider, default_llm, llm_enabled

    if not llm_enabled():
        if current_provider() == "anthropic":
            return None, "ANTHROPIC_API_KEY 미설정 — .env에 키를 넣고 LLM_PROVIDER=anthropic 설정."
        return (
            None,
            "LLM 비활성 — LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY, 또는 ENABLE_LLM=1(Ollama).",
        )
    try:
        return default_llm(), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
