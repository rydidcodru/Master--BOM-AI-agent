"""``bom_edge`` backfill — dev_part_master의 BOM ag-grid 행에서 엣지를 생성.

- 스코핑 키 = ``file_id`` (한 BOM 파일 = 한 워크 범위). 실데이터 BOM이 multi-root라
  단일 루트=model 가정 불가 → file_id로 스코핑.
- ``model`` = best-effort 라벨(파일의 최소 bom_depth 품번; 여러 개면 알파벳순 첫 번째).
  순회 스코핑엔 쓰지 않음.
- parent = ``extra_fields['bom_parent_part_no']`` (어댑터 bom_ag_grid.py가 저장).
- idempotent: 기존 (file_id, parent, child) 엣지는 건너뜀 → 재실행 안전. 파괴적 변경 0.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import BomEdge, DevPartMaster
from src.utils.logging import get_logger

log = get_logger(__name__)

_BOM_FORM_ID = "bom_ag_grid_36"
_PARENT_KEY = "bom_parent_part_no"


def _as_dict(extra: Any) -> dict[str, Any]:
    """extra_fields를 dict로 정규화 (parquet round-trip이 str로 만든 경우 방어)."""
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, str) and extra:
        try:
            parsed = json.loads(extra)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def backfill_bom_edges(session: Session, file_id: int | None = None) -> int:
    """BOM ag-grid 행 → bom_edge 적재. 삽입한 엣지 수 반환.

    Args:
        session: 활성 세션.
        file_id: 특정 파일만 처리(None이면 전체 BOM 행).
    """
    stmt = select(
        DevPartMaster.doc_id,
        DevPartMaster.file_id,
        DevPartMaster.part_no_new,
        DevPartMaster.bom_depth,
        DevPartMaster.qty_new,
        DevPartMaster.extra_fields,
    ).where(DevPartMaster.form_id == _BOM_FORM_ID)
    if file_id is not None:
        stmt = stmt.where(DevPartMaster.file_id == file_id)
    rows = session.execute(stmt).all()

    by_file: dict[Any, list[Any]] = defaultdict(list)
    for r in rows:
        by_file[r.file_id].append(r)

    # (file_id, model_label, parent, child, bom_level, qty, source_doc_id)
    desired: list[tuple[Any, ...]] = []
    for fid, frows in by_file.items():
        with_depth = [x for x in frows if x.part_no_new and x.bom_depth is not None]
        roots = sorted(
            x.part_no_new
            for x in with_depth
            if x.bom_depth == min(y.bom_depth for y in with_depth)
        ) if with_depth else []
        model_label = roots[0] if roots else None
        for x in frows:
            parent = _as_dict(x.extra_fields).get(_PARENT_KEY)
            if parent and x.part_no_new and parent != x.part_no_new:
                desired.append(
                    (fid, model_label, parent, x.part_no_new, x.bom_depth, x.qty_new, x.doc_id)
                )

    if not desired:
        return 0

    file_ids = {d[0] for d in desired}
    existing: set[tuple[Any, Any, Any]] = {
        (e.file_id, e.parent_pno, e.child_pno)
        for e in session.execute(
            select(BomEdge.file_id, BomEdge.parent_pno, BomEdge.child_pno).where(
                BomEdge.file_id.in_(file_ids)
            )
        ).all()
    }

    inserted = 0
    seen_new: set[tuple[Any, Any, Any]] = set()
    for fid, model_label, parent, child, level, qty, doc_id in desired:
        key = (fid, parent, child)
        if key in existing or key in seen_new:
            continue
        seen_new.add(key)
        session.add(
            BomEdge(
                file_id=fid,
                model=model_label,
                parent_pno=parent,
                child_pno=child,
                bom_level=level,
                qty=qty,
                source_doc_id=doc_id,
            )
        )
        inserted += 1

    session.commit()
    log.info("backfill.bom_edges.done", inserted=inserted, files=len(file_ids))
    return inserted
