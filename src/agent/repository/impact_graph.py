"""구조 A 영향 그래프 — 변경 부품의 상위 영향(조상) 거리·관계 집합.

차용 출처: Master--BOM-AI-agent(ms) Neo4j ``IMPACTS_LINE{distance, impactType}`` —
'하위 변경 → Root까지 모든 조상이 영향'을 거리(0=자신/직접, 1=부모, 2+=조상)로 등급화한
1급 구조(비교 기준 #4에서 ms 우위였던 부분). ms는 **적재 시점에 물질화(materialize)** 하지만,
lg는 ``bom_edge`` 재귀 CTE(:meth:`BomRepository.walk_subtree` up)로 **온디맨드** 계산한다
(엣지 ~2.6k 규모라 물질화 불필요). 결정론·LLM 0회. 트리 구조만 다루므로 검색키/신규번호
원칙과 무관.

기존 ``walk_subtree('up', ...)``는 ``BomNode.depth``(거리)를 이미 주지만 관계가 'parent'로
뭉개진다. 이 모듈은 거리별 관계 라벨(direct/parent/ancestor)을 붙이고 DAG에서 같은 조상이
여러 경로로 닿을 때 **최단 거리**로 dedup해, ms IMPACTS_LINE와 동등한 1급 결과를 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.repository.bom import _DEFAULT_MAX_DEPTH, BomRepository


def impact_type_for(distance: int) -> str:
    """거리 → 영향 유형 (ms ``impactType`` 등가). 0=직접, 1=부모, 2+=조상."""
    if distance <= 0:
        return "direct"
    if distance == 1:
        return "parent"
    return "ancestor"


@dataclass(frozen=True)
class ImpactedNode:
    """상위 영향 노드 1건 (ms ``IMPACTS_LINE`` 한 간선에 대응).

    Attributes:
        part_no: 영향받는 상위 품번.
        distance: seed로부터 상향 거리 (0=자신, 1=부모, 2+=조상).
        relation: ``impact_type_for(distance)`` — direct / parent / ancestor.
    """

    part_no: str
    distance: int
    relation: str


def walk_impacted_ancestors(
    repo: BomRepository,
    seed: str,
    *,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    file_id: int | None = None,
    include_self: bool = True,
) -> list[ImpactedNode]:
    """seed 변경이 영향 주는 상위 조상 집합(거리·관계 등급). 결정론, LLM 0회.

    ``repo.walk_subtree(seed, 'up', ...)`` 결과에 거리별 관계 라벨을 붙이고, DAG에서 같은
    조상이 여러 경로로 닿으면 최단 거리로 dedup한다(ms IMPACTS_LINE와 동등).

    Args:
        repo: BOM 트리 repository(:class:`EdgeBomRepository` 등).
        seed: 변경 부품 품번.
        max_depth: 상향 순회 깊이(repo가 [1,4]로 clamp).
        file_id: BOM 파일 스코핑(None이면 교차 BOM).
        include_self: True면 seed 자신을 distance=0(direct)으로 포함.

    Returns:
        :class:`ImpactedNode` 리스트, (distance, part_no) 오름차순.
    """
    nearest: dict[str, int] = {}
    if include_self:
        nearest[seed] = 0
    for n in repo.walk_subtree(seed, "up", max_depth, file_id):
        if n.pno == seed:  # 사이클/자기참조 방어 (include_self의 0을 보존)
            continue
        d = int(n.depth)
        if n.pno not in nearest or d < nearest[n.pno]:  # DAG: 최단 거리 유지
            nearest[n.pno] = d
    return [
        ImpactedNode(pno, dist, impact_type_for(dist))
        for pno, dist in sorted(nearest.items(), key=lambda kv: (kv[1], kv[0]))
    ]


__all__ = ["ImpactedNode", "impact_type_for", "walk_impacted_ancestors"]
