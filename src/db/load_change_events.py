"""Phase 1 — dev_part_master 변경행 → change_event / change_line 적재 (구조 B).

CLAUDE.md 검색 인덱스(구조 B)를 실제로 살린다. 과거 master는 이미 dev_part_master에
적재돼 있으므로(D-012 ETL), 여기서는 **DB→DB 변환**으로 변경이력 행을 사유별로 묶어
change_event(검색 대상) + change_line(부품 세트)을 만든다.

그룹핑 규칙 (사용자 확정 2026-05-29): **변경사유별 분할**.
  한 change_event = (file_id, base_model, new_model, change_reason) 한 묶음.
  같은 묶음의 dev_part_master 변경행 각각 = 한 change_line.
  → "사유가 부품들을 묶는 키"(예: 'BLDC 기능 추가'가 여러 행에 걸침).

검색 인덱스 = ``change_event.reason_embedding`` = embed(change_log + change_reason).
부품명/모델은 임베딩에 미포함(검색키=변경내역+변경사유 원칙). 임베딩은
``update_event_embeddings``로 별도 backfill(ENABLE_EMBEDDING=1, Postgres 전용).

idempotent: ``file_ids`` 스코프의 기존 change_event를 지우고 재생성(재실행 안전).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from src.db.models import ChangeEvent, ChangeLine, DevPartMaster, SourceFile
from src.utils.logging import get_logger

log = get_logger(__name__)

# 한 이벤트의 change_log = 멤버 라인의 distinct 변경내역을 합친 것. 너무 길어지지 않게 상한.
_MAX_CHANGE_LOG_PARTS = 20
_CHANGE_LOG_SEP = " / "


@dataclass
class ChangeEventLoadResult:
    """적재 결과 — 생성된 event/line 수 + 처리한 file_id."""

    events_inserted: int = 0
    lines_inserted: int = 0
    file_ids: list[int] = field(default_factory=list)


# 의미 없는 placeholder 셀값 — 사유/모델 등에서 빈값으로 취급(검색 인덱스 노이즈 방지).
_PLACEHOLDERS = {"-", "–", "—", ".", "/", "n/a", "na", "tbd", "nan"}


def _norm(value: object) -> str:
    """None/NaN/공백/placeholder('-','.','n/a'…) → ''. 그 외 → strip된 str.

    '-' 같은 placeholder를 사유로 두면 서로 무관한 부품이 한 event로 과병합되고
    raw_text='- -' 류의 무의미 벡터가 reason_embedding 인덱스에 들어간다(검수 지적).
    """
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in _PLACEHOLDERS else s


def _extra_get(extra: object, needle: str) -> str | None:
    """extra_fields(JSONB dict)에서 키에 ``needle`` 부분문자열이 든 첫 값(best-effort).

    금형/사내/시험 컬럼은 양식마다 헤더명이 달라 정확 키를 추정하지 않고 부분일치로 회수.
    없으면 None(추정값 생성 금지).
    """
    if not isinstance(extra, dict):
        return None
    for key, val in extra.items():
        if needle in str(key):
            v = _norm(val)
            return v or None
    return None


def _change_rows(session: Session, file_ids: list[int]) -> list[DevPartMaster]:
    """대상 file_id의 dev_part_master 중 변경이력(변경내역 또는 변경사유) 보유 행."""
    stmt = select(DevPartMaster).where(DevPartMaster.file_id.in_(file_ids))
    rows = session.execute(stmt).scalars().all()
    return [
        r
        for r in rows
        if _norm(r.change_reason_raw) or _norm(r.change_point_raw)
    ]


def _target_file_ids(session: Session, file_ids: list[int] | None) -> list[int]:
    if file_ids:
        return list(file_ids)
    rows = session.execute(
        select(DevPartMaster.file_id).distinct().where(DevPartMaster.file_id.isnot(None))
    ).all()
    return sorted({int(r[0]) for r in rows if r[0] is not None})


def _delete_existing(session: Session, file_ids: list[int]) -> None:
    """대상 file_id의 기존 change_event + 그 라인을 제거(idempotent 재적재)."""
    ev_ids = session.execute(
        select(ChangeEvent.event_id).where(ChangeEvent.file_id.in_(file_ids))
    ).scalars().all()
    if not ev_ids:
        return
    session.execute(delete(ChangeLine).where(ChangeLine.event_id.in_(ev_ids)))
    session.execute(delete(ChangeEvent).where(ChangeEvent.event_id.in_(ev_ids)))


def load_change_events(
    session: Session,
    file_ids: list[int] | None = None,
) -> ChangeEventLoadResult:
    """dev_part_master 변경행 → change_event/change_line 적재(사유별 분할).

    Args:
        session: 활성 SQLAlchemy session.
        file_ids: 대상 파일 한정. None이면 dev_part_master 전체 file_id.

    Returns:
        :class:`ChangeEventLoadResult` (event/line 수, 처리 file_id).

    임베딩(reason_embedding)은 채우지 않는다 — ``update_event_embeddings`` 별도 호출.
    """
    targets = _target_file_ids(session, file_ids)
    if not targets:
        return ChangeEventLoadResult()

    _delete_existing(session, targets)

    rows = _change_rows(session, targets)

    # 파일명 룩업(source_ref 구성용).
    name_map = {
        fid: name
        for fid, name in session.execute(
            select(SourceFile.file_id, SourceFile.file_name).where(
                SourceFile.file_id.in_(targets)
            )
        ).all()
    }

    # 그룹핑: (file_id, base_model, new_model, group_reason) → 행 리스트.
    # group_reason = 변경사유(있으면) 또는 변경내역(없을 때 fallback) — 비어있지 않음 보장.
    groups: dict[tuple[int, str, str, str], list[DevPartMaster]] = {}
    order: list[tuple[int, str, str, str]] = []
    for r in rows:
        reason = _norm(r.change_reason_raw)
        point = _norm(r.change_point_raw)
        group_reason = reason or point
        key = (
            int(r.file_id) if r.file_id is not None else -1,
            _norm(r.base_model),
            _norm(r.new_model),
            group_reason,
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    events_inserted = 0
    lines_inserted = 0
    for key in order:
        fid, base_model, new_model, _group_reason = key
        members = groups[key]

        # 대표 변경사유(원본) — fallback로 묶였으면 None.
        reasons = [_norm(m.change_reason_raw) for m in members]
        change_reason = next((x for x in reasons if x), "") or None

        # change_log = 멤버 라인의 distinct 변경내역 합침(상한).
        seen_points: list[str] = []
        for m in members:
            p = _norm(m.change_point_raw)
            if p and p not in seen_points:
                seen_points.append(p)
        change_log = _CHANGE_LOG_SEP.join(seen_points[:_MAX_CHANGE_LOG_PARTS]) or None

        # 대표 event 종류(가장 흔한 비어있지 않은 값). 동점은 사전순 — 해시순서 비의존(결정론).
        events = [_norm(m.event) for m in members if _norm(m.event)]
        event_kind = max(sorted(set(events)), key=events.count) if events else None

        raw_text = " ".join(x for x in (change_log, change_reason) if x).strip() or None
        file_name = name_map.get(fid)

        # 출처 프로젝트 전파 — 한 그룹은 같은 file_id이므로 멤버가 동일 source_project('lg'/'ms').
        group_project = next(
            (m.source_project for m in members if m.source_project), "lg"
        )
        ev = ChangeEvent(
            file_id=fid if fid >= 0 else None,
            base_model=base_model or None,
            new_model=new_model or None,
            event=event_kind,
            reason=change_reason,
            change_log=change_log,
            change_reason=change_reason,
            raw_text=raw_text,
            source_file=file_name,
            source_ref=file_name,
            source_project=group_project,
        )
        session.add(ev)
        session.flush()  # event_id 확보
        events_inserted += 1

        for seq, m in enumerate(members, start=1):
            sref = None
            if file_name is not None:
                sref = f"{file_name}/{_norm(m.sheet_name)}/{m.source_row}"
            session.add(
                ChangeLine(
                    event_id=ev.event_id,
                    seq=seq,
                    bom_level=m.bom_depth,
                    part_type=_norm(m.part_type) or None,
                    base_pno=_norm(m.part_no_base) or None,
                    new_pno=_norm(m.part_no_new) or None,
                    changepoint=_norm(m.change_point_raw) or None,
                    part_name=_norm(m.part_name) or None,
                    qty_base=float(m.qty_base) if m.qty_base is not None else None,
                    qty_new=float(m.qty_new) if m.qty_new is not None else None,
                    classification=_norm(m.classification) or None,
                    supplier=_norm(m.supplier) or None,
                    mold_yn=_extra_get(m.extra_fields, "금형"),
                    inhouse_yn=_extra_get(m.extra_fields, "사내"),
                    approval_test_yn=_extra_get(m.extra_fields, "시험"),
                    source_ref=sref,
                )
            )
            lines_inserted += 1

    session.commit()
    result = ChangeEventLoadResult(
        events_inserted=events_inserted,
        lines_inserted=lines_inserted,
        file_ids=targets,
    )
    log.info(
        "db.load_change_events.done",
        events=events_inserted,
        lines=lines_inserted,
        files=len(targets),
    )
    return result


def update_event_embeddings(
    session: Session,
    event_ids: list[int] | None = None,
) -> int:
    """raw_text가 있고 reason_embedding이 NULL인 change_event 일괄 임베딩.

    검색 인덱스 = reason_embedding = embed(change_log + change_reason). Postgres 전용.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    import os

    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError("embedding disabled - set ENABLE_EMBEDDING=1 and run Ollama")
    if session.bind is None or session.bind.dialect.name != "postgresql":
        log.warning("db.event_embed.skip_non_pg", dialect=getattr(session.bind, "dialect", None))
        return 0

    from src.embed.embedder import embed_texts

    stmt = (
        select(ChangeEvent)
        .where(ChangeEvent.raw_text.isnot(None))
        .where(ChangeEvent.reason_embedding.is_(None))
    )
    if event_ids:
        stmt = stmt.where(ChangeEvent.event_id.in_(event_ids))

    events = session.execute(stmt).scalars().all()
    if not events:
        return 0

    vectors = embed_texts([(e.raw_text or "") for e in events])
    for ev, vec in zip(events, vectors, strict=True):
        if vec:
            ev.reason_embedding = vec
    session.commit()
    log.info("db.event_embeddings.updated", events=len(events))
    return len(events)


def update_event_sparse_embeddings(
    session: Session,
    event_ids: list[int] | None = None,
) -> int:
    """raw_text가 있고 reason_sparse가 NULL인 change_event 일괄 sparse(BGE-M3 lexical) 임베딩.

    dense(reason_embedding)와 별개 채널 — search_events가 dense+trgm+sparse RRF로 융합.
    Postgres 전용(sparsevec). ms etl_embed 차용.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    from src.embed.sparse_embedder import embed_sparse, sparse_embedding_enabled

    if not sparse_embedding_enabled():
        raise RuntimeError("embedding disabled - set ENABLE_EMBEDDING=1 for sparse")
    if session.bind is None or session.bind.dialect.name != "postgresql":
        log.warning("db.event_sparse.skip_non_pg", dialect=getattr(session.bind, "dialect", None))
        return 0

    stmt = (
        select(ChangeEvent.event_id, ChangeEvent.raw_text)
        .where(ChangeEvent.raw_text.isnot(None))
        .where(ChangeEvent.reason_sparse.is_(None))
    )
    if event_ids:
        stmt = stmt.where(ChangeEvent.event_id.in_(event_ids))
    rows = session.execute(stmt).all()
    if not rows:
        return 0

    literals = embed_sparse([(r[1] or "") for r in rows])
    upd = text(
        "UPDATE change_event SET reason_sparse = CAST(:lit AS sparsevec) WHERE event_id = :id"
    )
    for (eid, _raw), lit in zip(rows, literals, strict=True):
        session.execute(upd, {"lit": lit, "id": eid})
    session.commit()
    log.info("db.event_sparse_embeddings.updated", events=len(rows))
    return len(rows)
