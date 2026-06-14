"""①.5 — 모듈 구조 스코프(S) 구축 (검색 부스트·매핑 보조용 read-only 조회).

⚠️ 이 모듈의 BOM 조회는 전부 **검색·매핑 보조용 사전 조회(read-only)**다 — 절대원칙
#4("확정이 전개보다 먼저")의 **판정용 전개(④)가 아니다**. 판정용 전개는 기존 HITL
확정 이후 경로(``expand_confirmed``)가 그대로 담당한다.

흐름: ① base_model → bom_edge 보유 file_id 해석(bom_edge.model / source_files.file_name
매칭) → ② 얕은 레벨(depth≤2) 노드의 part_name_canon vs 모듈명 canon trgm — 최고점 닻
채택("레벨1 고정" 금지: 모듈은 depth 1에도 2에도 있을 수 있다; 복수 매칭 시 모듈당 상위
2개 닻, S는 합집합) → ③ walk_subtree(닻, down, max_depth=4) → ScopeNode[] → ScopeIndex.

실패 조건(파일 미존재/모듈 미매칭/walk 공집합) → None 반환(조용히 스킵 — 게이트 off와
바이트 단위 동일 동작 보존). 순수 결정론 — LLM import 0회.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.agent.matching.scoring import (
    MatchThresholds,
    ScopeIndex,
    ScopeMeta,
    ScopeNode,
    trgm_word_similarity,
)
from src.agent.repository.bom import EdgeBomRepository
from src.preprocess.normalize import canonicalize_part_name
from src.utils.logging import get_logger

log = get_logger(__name__)

_ANCHORS_PER_MODULE = 2
_SHALLOW_DEPTH = 2
_WALK_DEPTH = 4


def _resolve_file_id(session: Session, base_model: str | None) -> int | None:
    """base_model → bom_edge 보유 file_id. 매칭 우선순위:

    1) ``bom_edge.model`` 정확 일치(대소문자 무시)
    2) ``source_files.file_name``에 base_model 포함(bom_edge 보유 파일만)
    3) base_model 미지정/미매칭 시: bom_edge 보유 파일이 정확히 1개면 그 파일, 아니면 None.
    """
    bm = (base_model or "").strip()
    if bm:
        fid = session.execute(
            text(
                "SELECT file_id FROM bom_edge "
                "WHERE upper(model) = upper(:bm) AND file_id IS NOT NULL LIMIT 1"
            ),
            {"bm": bm},
        ).scalar()
        if fid is not None:
            return int(fid)
        fid = session.execute(
            text(
                "SELECT f.file_id FROM source_files f "
                "WHERE lower(f.file_name) LIKE '%' || lower(:bm) || '%' "
                "  AND EXISTS (SELECT 1 FROM bom_edge e WHERE e.file_id = f.file_id) "
                "LIMIT 1"
            ),
            {"bm": bm},
        ).scalar()
        if fid is not None:
            return int(fid)
    fids = [
        int(r[0])
        for r in session.execute(
            text("SELECT DISTINCT file_id FROM bom_edge WHERE file_id IS NOT NULL")
        ).all()
    ]
    return fids[0] if len(fids) == 1 else None


def _part_names(session: Session, file_id: int | None) -> dict[str, tuple[str, str | None]]:
    """pno → (part_name, part_type).

    BOM ag-grid 행(form bom_ag_grid_36)은 ``part_name``에 품번이 그대로 들어 있어
    퍼지 매칭에 못 쓴다 — **이름≠품번인 행(타 양식 포함, 파일 무관)을 우선**하고,
    남는 품번만 file 스코프 행으로 보강한다(품번뿐이어도 pno 정확일치엔 충분).
    """
    out: dict[str, tuple[str, str | None]] = {}
    for scope_sql, params in (
        (
            "SELECT part_no_new, max(part_name), max(part_type) FROM dev_part_master "
            "WHERE part_no_new IS NOT NULL AND part_name IS NOT NULL "
            "  AND part_name <> part_no_new GROUP BY part_no_new",
            {},
        ),
        (
            "SELECT part_no_new, max(part_name), max(part_type) FROM dev_part_master "
            "WHERE file_id = :f AND part_no_new IS NOT NULL GROUP BY part_no_new",
            {"f": file_id},
        ),
    ):
        if ":f" in scope_sql and file_id is None:
            continue
        for pno, name, ptype in session.execute(text(scope_sql), params).all():
            if pno and pno not in out:
                out[str(pno)] = (str(name or ""), (str(ptype) if ptype else None))
    return out


def _shallow_pool(session: Session, file_id: int) -> list[tuple[str, int]]:
    """얕은 레벨 후보 (pno, depth) — bom_level≤2 child + 그 parent(루트 포함)."""
    rows = session.execute(
        text(
            "SELECT DISTINCT child_pno AS pno, COALESCE(bom_level, 1) AS lvl "
            "FROM bom_edge WHERE file_id = :f AND COALESCE(bom_level, 99) <= :d "
            "UNION "
            "SELECT DISTINCT parent_pno AS pno, 0 AS lvl "
            "FROM bom_edge WHERE file_id = :f AND COALESCE(bom_level, 99) <= :d"
        ),
        {"f": file_id, "d": _SHALLOW_DEPTH},
    ).all()
    if rows:
        return [(str(r.pno), int(r.lvl)) for r in rows if r.pno]
    # bom_level 전부 NULL인 파일 — 내부 노드(parent로 등장) 전체를 풀로.
    rows = session.execute(
        text("SELECT DISTINCT parent_pno FROM bom_edge WHERE file_id = :f"),
        {"f": file_id},
    ).all()
    return [(str(r[0]), 0) for r in rows if r[0]]


def build_scope(
    module_names: list[str],
    base_model: str | None,
    session: Session,
    th: MatchThresholds,
) -> ScopeIndex | None:
    """모듈명들 → 모듈 하위트리 S의 ScopeIndex. 실패 시 None (조용히 스킵).

    검색용 사전 조회(read-only) ≠ 판정용 전개 — 모듈 docstring 참조. 실패 사유·닻 점수
    관측은 :func:`scope_status` 로 별도 노출(P7.4) — 본 함수 호출부는 무변경(None 게이트).
    """
    idx, _meta = _build_scope(module_names, base_model, session, th)
    return idx


def scope_status(
    module_names: list[str],
    base_model: str | None,
    session: Session,
    th: MatchThresholds,
) -> ScopeMeta:
    """①.5 스코프 구축 관측 정보만 반환 (env_panel "켰는데 안 도는" 진단용, P7.4)."""
    _idx, meta = _build_scope(module_names, base_model, session, th)
    return meta


def _build_scope(
    module_names: list[str],
    base_model: str | None,
    session: Session,
    th: MatchThresholds,
) -> tuple[ScopeIndex | None, ScopeMeta]:
    """실제 구축 + 관측 meta. 실패 시 (None, meta[fail_reason])."""
    meta = ScopeMeta()
    names = [str(m).strip() for m in module_names or [] if str(m or "").strip()]
    names = [m for m in names if m not in ("정보 없음", "내용 없음")]
    if not names:
        meta.fail_reason = "module_no_match"
        return None, meta
    try:
        file_id = _resolve_file_id(session, base_model)
        if file_id is None:
            meta.fail_reason = "file_not_found"
            return None, meta
        pool = _shallow_pool(session, file_id)
        if not pool:
            meta.fail_reason = "empty_walk"
            return None, meta
        pmeta = _part_names(session, file_id)

        # ② 모듈명 ↔ 얕은 노드 퍼지 매칭 — 모듈당 상위 2개 닻, 합집합.
        anchors: list[str] = []
        best_no_match = 0.0
        for mod in names:
            mod_canon = canonicalize_part_name(mod)
            if not mod_canon:
                continue
            scored = sorted(
                (
                    (trgm_word_similarity(mod_canon, canonicalize_part_name(pmeta.get(p, ("", None))[0] or p)), p)
                    for p, _lvl in pool
                ),
                key=lambda t: -t[0],
            )
            if scored:
                meta.anchors.append((mod, round(scored[0][0], 3)))
                best_no_match = max(best_no_match, scored[0][0])
            # 닻 모호 갭: 단일 모듈명 + top1-top2 < eps → 합집합 유지(S는 부스트지 필터 아님) + 플래그.
            if len(names) == 1 and len(scored) >= 2 and (scored[0][0] - scored[1][0]) < th.eps:
                meta.ambiguous_anchor = True
                log.warning("structure_scope.ambiguous_anchor", module=mod,
                            top=round(scored[0][0], 3), second=round(scored[1][0], 3))
            for s, p in scored[:_ANCHORS_PER_MODULE]:
                if s >= th.tau_lo and p not in anchors:
                    anchors.append(p)
        if not anchors:
            meta.fail_reason = "module_no_match"
            meta.best_no_match_score = round(best_no_match, 3)
            return None, meta

        # ③ 닻 하위 전개(read-only) → ScopeNode 합집합 (닻 자신은 depth 0으로 포함).
        repo = EdgeBomRepository(session)
        nodes: dict[str, ScopeNode] = {}
        for a in anchors:
            a_name, a_type = pmeta.get(a, (a, None))
            nodes.setdefault(
                a,
                ScopeNode(
                    pno=a,
                    part_name=a_name or a,
                    part_name_canon=canonicalize_part_name(a_name or a),
                    part_type=a_type,
                    depth=0,
                    path=f"/{a}/",
                ),
            )
            for n in repo.walk_subtree(a, "down", _WALK_DEPTH, file_id):
                if n.pno in nodes:
                    continue
                p_name, p_type = pmeta.get(n.pno, (n.pno, None))
                nodes[n.pno] = ScopeNode(
                    pno=n.pno,
                    part_name=p_name or n.pno,
                    part_name_canon=canonicalize_part_name(p_name or n.pno),
                    part_type=p_type,
                    depth=n.depth,
                    path=n.path,
                )
        if not nodes:
            meta.fail_reason = "empty_walk"
            return None, meta
        return ScopeIndex(list(nodes.values()), th, meta), meta
    except Exception as exc:  # noqa: BLE001 — 보조 채널 실패는 조용히 스킵(기존 동작 보존)
        log.warning("structure_scope.build_failed", error=str(exc)[:160])
        meta.fail_reason = "empty_walk"
        return None, meta


__all__ = ["build_scope", "scope_status"]
