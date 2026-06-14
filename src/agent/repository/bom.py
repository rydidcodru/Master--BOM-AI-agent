"""구조 A — BOM 트리 repository (``walk_subtree``).

실데이터 DAG 확정(553품번 중 81개 다중부모)에 따라 엣지 테이블 ``bom_edge``을 정식
백엔드로 한다(:class:`EdgeBomRepository`). 단일 부모 컬럼
(``dev_part_master.extra_fields['bom_parent_part_no']``) 백엔드도 같은 인터페이스를
구현(:class:`ColumnBomRepository`)해, "두 백엔드 모두 동작" 요구를 만족한다.

스코핑 키 = ``file_id`` (한 BOM 파일 = 한 워크 범위). 실데이터 BOM 파일이 multi-root라
단일 루트=model 가정이 안 맞아 file_id로 묶는다. seed는 실제 부품 품번(보통 변경 부품).

``walk_subtree(seed, direction, max_depth, file_id)``는 seed의 하향(자식)/상향(부모)
서브트리를 ``max_depth``(기본 2, 최대 4)까지 순회한다. path 기반 cycle guard 포함.

재귀 CTE는 ``'/a/b/c/'`` string-path를 써서 Postgres와 SQLite(단위 테스트) 모두에서
동작한다(PG의 ARRAY/``= ANY`` 대신). 주의: 품번에 LIKE 와일드카드(``%`` ``_``)가 없다는
가정 — 현 데이터(예: ``WSED7667M.ABMQEUR@CVZ.EKHQ``)는 만족.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.db.models import DevPartMaster

Direction = Literal["down", "up"]

_DEFAULT_MAX_DEPTH = 2
_MAX_MAX_DEPTH = 4


def _clamp_depth(max_depth: int) -> int:
    """max_depth를 [1, 4]로 제한 (기본 2)."""
    return max(1, min(_MAX_MAX_DEPTH, max_depth))


@dataclass(frozen=True)
class BomNode:
    """``walk_subtree`` 결과 노드.

    Attributes:
        pno: 발견된 노드 품번.
        from_pno: seed 방향으로 인접한 노드(엣지의 반대편).
        depth: seed로부터 거리(1부터).
        qty: 엣지 수량.
        bom_level: child의 BOM depth.
        path: ``'/a/b/c/'`` 경유 경로 (cycle guard용).
    """

    pno: str
    from_pno: str
    depth: int
    qty: float | None
    bom_level: int | None
    path: str


class BomRepository(Protocol):
    """BOM 서브트리 순회 인터페이스 (엣지/컬럼 백엔드 공통)."""

    def walk_subtree(
        self,
        seed: str,
        direction: Direction = "down",
        max_depth: int = _DEFAULT_MAX_DEPTH,
        file_id: int | None = None,
    ) -> list[BomNode]: ...


class EdgeBomRepository:
    """``bom_edge`` 재귀 CTE 백엔드 (정식). Postgres + SQLite 모두 동작."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def walk_subtree(
        self,
        seed: str,
        direction: Direction = "down",
        max_depth: int = _DEFAULT_MAX_DEPTH,
        file_id: int | None = None,
    ) -> list[BomNode]:
        depth = _clamp_depth(max_depth)
        params: dict[str, object] = {"seed": seed, "max_depth": depth}
        if file_id is None:
            f_anchor = f_recurse = ""
        else:
            params["file_id"] = file_id
            f_anchor = " AND file_id = :file_id"
            f_recurse = " AND e.file_id = :file_id"

        # direction은 Literal — 고정 SQL 두 개 중 택일(사용자 입력 보간 아님).
        if direction == "down":
            anchor = "child_pno, parent_pno"
            anchor_seed_col = "parent_pno"
            anchor_path = "'/' || parent_pno || '/' || child_pno || '/'"
            step = "e.child_pno, e.parent_pno"
            step_join = "e.parent_pno = s.node_pno"
            step_guard = "e.child_pno"
            step_path = "s.path || e.child_pno || '/'"
        else:
            anchor = "parent_pno, child_pno"
            anchor_seed_col = "child_pno"
            anchor_path = "'/' || child_pno || '/' || parent_pno || '/'"
            step = "e.parent_pno, e.child_pno"
            step_join = "e.child_pno = s.node_pno"
            step_guard = "e.parent_pno"
            step_path = "s.path || e.parent_pno || '/'"

        sql = f"""
        WITH RECURSIVE sub(node_pno, from_pno, depth, qty, bom_level, path) AS (
            SELECT {anchor}, 1, qty, bom_level, {anchor_path}
              FROM bom_edge
             WHERE {anchor_seed_col} = :seed{f_anchor}
            UNION ALL
            SELECT {step}, s.depth + 1, e.qty, e.bom_level, {step_path}
              FROM bom_edge e
              JOIN sub s ON {step_join}
             WHERE s.depth < :max_depth
               AND (s.path NOT LIKE ('%/' || {step_guard} || '/%')){f_recurse}
        )
        SELECT DISTINCT node_pno, from_pno, depth, qty, bom_level, path
          FROM sub
         ORDER BY depth, node_pno
        """
        rows = self._session.execute(text(sql), params).all()
        return [
            BomNode(
                pno=r.node_pno,
                from_pno=r.from_pno,
                depth=int(r.depth),
                qty=float(r.qty) if r.qty is not None else None,
                bom_level=int(r.bom_level) if r.bom_level is not None else None,
                path=r.path,
            )
            for r in rows
        ]


class ColumnBomRepository:
    """단일 부모(``extra_fields['bom_parent_part_no']``) 백엔드 — 호환/대안.

    DAG 표현 불가(부모 1개 가정)라 정식 백엔드는 :class:`EdgeBomRepository`.
    같은 인터페이스를 만족시키기 위한 Python BFS 구현. ``form_id='bom_ag_grid_36'``
    행만 사용하며 ``file_id``로 스코핑(주어지면).
    """

    _BOM_FORM_ID = "bom_ag_grid_36"
    _PARENT_KEY = "bom_parent_part_no"

    def __init__(self, session: Session) -> None:
        self._session = session

    def walk_subtree(
        self,
        seed: str,
        direction: Direction = "down",
        max_depth: int = _DEFAULT_MAX_DEPTH,
        file_id: int | None = None,
    ) -> list[BomNode]:
        depth_cap = _clamp_depth(max_depth)
        stmt = select(
            DevPartMaster.part_no_new,
            DevPartMaster.bom_depth,
            DevPartMaster.qty_new,
            DevPartMaster.extra_fields,
        ).where(DevPartMaster.form_id == self._BOM_FORM_ID)
        if file_id is not None:
            stmt = stmt.where(DevPartMaster.file_id == file_id)
        rows = self._session.execute(stmt).all()

        parent_of: dict[str, str] = {}
        children_of: dict[str, list[tuple[str, int | None, float | None]]] = {}
        depth_of: dict[str, int | None] = {}
        for pno, bom_depth, qty, extra in rows:
            if not pno:
                continue
            depth_of[pno] = bom_depth
            parent = (extra or {}).get(self._PARENT_KEY)
            if parent:
                parent_of[pno] = parent
                children_of.setdefault(parent, []).append(
                    (pno, bom_depth, float(qty) if qty is not None else None)
                )

        result: list[BomNode] = []
        if direction == "down":
            queue: deque[tuple[str, int, str]] = deque([(seed, 0, f"/{seed}/")])
            while queue:
                node, d, path = queue.popleft()
                if d >= depth_cap:
                    continue
                for child, child_depth, qty in children_of.get(node, []):
                    if f"/{child}/" in path:  # cycle guard
                        continue
                    child_path = path + f"{child}/"
                    result.append(
                        BomNode(
                            pno=child,
                            from_pno=node,
                            depth=d + 1,
                            qty=qty,
                            bom_level=child_depth,
                            path=child_path,
                        )
                    )
                    queue.append((child, d + 1, child_path))
        else:  # up — 단일 부모 체인
            node = seed
            path = f"/{seed}/"
            for d in range(depth_cap):
                parent = parent_of.get(node)
                if not parent or f"/{parent}/" in path:
                    break
                path = path + f"{parent}/"
                result.append(
                    BomNode(
                        pno=parent,
                        from_pno=node,
                        depth=d + 1,
                        qty=None,
                        bom_level=depth_of.get(parent),
                        path=path,
                    )
                )
                node = parent
        return result
