"""impact_graph — 상위 영향(조상) 거리·관계 등급 (ms IMPACTS_LINE 차용).

검증: 체인 거리, self(distance 0=direct), DAG 최단거리 dedup, max_depth clamp, file_id 스코핑,
impact_type_for 경계.
"""

from __future__ import annotations

import pytest

from src.agent.repository.bom import EdgeBomRepository
from src.agent.repository.impact_graph import (
    ImpactedNode,
    impact_type_for,
    walk_impacted_ancestors,
)
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, SourceFile


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _sf(session, file_hash="h1") -> int:
    sf = SourceFile(file_name="bom.xlsx", file_hash=file_hash)
    session.add(sf)
    session.commit()
    return sf.file_id


def _edge(session, file_id, parent, child):
    session.add(BomEdge(file_id=file_id, parent_pno=parent, child_pno=child))


def test_impact_type_boundaries():
    assert impact_type_for(0) == "direct"
    assert impact_type_for(1) == "parent"
    assert impact_type_for(2) == "ancestor"
    assert impact_type_for(7) == "ancestor"
    assert impact_type_for(-3) == "direct"  # 방어적: 음수도 direct


def test_chain_distances_and_relations(session):
    fid = _sf(session)
    # A → B → C → D (parent → child)
    for p, c in [("A", "B"), ("B", "C"), ("C", "D")]:
        _edge(session, fid, p, c)
    session.commit()
    repo = EdgeBomRepository(session)

    nodes = walk_impacted_ancestors(repo, "D", max_depth=4, file_id=fid)
    got = {n.part_no: (n.distance, n.relation) for n in nodes}
    assert got == {
        "D": (0, "direct"),
        "C": (1, "parent"),
        "B": (2, "ancestor"),
        "A": (3, "ancestor"),
    }
    # 거리 오름차순 정렬
    assert [n.part_no for n in nodes] == ["D", "C", "B", "A"]


def test_include_self_toggle(session):
    fid = _sf(session)
    _edge(session, fid, "P", "X")
    session.commit()
    repo = EdgeBomRepository(session)

    with_self = walk_impacted_ancestors(repo, "X", file_id=fid, include_self=True)
    assert ImpactedNode("X", 0, "direct") in with_self
    without = walk_impacted_ancestors(repo, "X", file_id=fid, include_self=False)
    assert all(n.part_no != "X" for n in without)
    assert {n.part_no for n in without} == {"P"}


def test_dag_keeps_shortest_distance(session):
    fid = _sf(session)
    # X는 P1, P2 두 부모; P1·P2 모두 GP의 자식 → GP는 거리 2(최단)로 한 번만.
    for p, c in [("P1", "X"), ("P2", "X"), ("GP", "P1"), ("GP", "P2")]:
        _edge(session, fid, p, c)
    session.commit()
    repo = EdgeBomRepository(session)

    nodes = walk_impacted_ancestors(repo, "X", max_depth=4, file_id=fid, include_self=False)
    got = {n.part_no: n.distance for n in nodes}
    assert got["P1"] == 1 and got["P2"] == 1
    assert got["GP"] == 2  # 최단거리 dedup (두 경로지만 1건)
    assert sum(1 for n in nodes if n.part_no == "GP") == 1
    assert {n.relation for n in nodes if n.part_no == "GP"} == {"ancestor"}


def test_max_depth_clamped(session):
    fid = _sf(session)
    chain = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
    for i in range(len(chain) - 1):
        _edge(session, fid, chain[i], chain[i + 1])  # L0 부모 … L6 말단
    session.commit()
    repo = EdgeBomRepository(session)

    # 상향 max_depth=10 → repo가 4로 clamp. seed=L6의 조상은 거리 1..4 (L5..L2).
    nodes = walk_impacted_ancestors(repo, "L6", max_depth=10, file_id=fid, include_self=False)
    assert max(n.distance for n in nodes) == 4
    assert {n.part_no for n in nodes} == {"L5", "L4", "L3", "L2"}


def test_file_id_scoping(session):
    f1 = _sf(session, "hf1")
    f2 = _sf(session, "hf2")
    _edge(session, f1, "P1", "C")
    _edge(session, f2, "P2", "C")
    session.commit()
    repo = EdgeBomRepository(session)

    assert {n.part_no for n in walk_impacted_ancestors(repo, "C", file_id=f1, include_self=False)} == {"P1"}
    assert {n.part_no for n in walk_impacted_ancestors(repo, "C", file_id=f2, include_self=False)} == {"P2"}
    assert {n.part_no for n in walk_impacted_ancestors(repo, "C", file_id=None, include_self=False)} == {"P1", "P2"}
