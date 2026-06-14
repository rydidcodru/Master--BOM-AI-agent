"""Phase 1 — Agentic RAG 가산 데이터 모델 테스트 (SQLite).

검증:
  - bom_edge 재귀 CTE walk_subtree: 하향/상향, **DAG 다중부모**, cycle guard, max_depth
    clamp(≤4), file_id 스코핑.
  - backfill_bom_edges: dev_part_master BOM 행 → 엣지(file_id 스코핑), idempotent 재실행.
  - ColumnBomRepository(단일부모 백엔드) 동작.
  - dev_part_master.change_intent 가산 컬럼 + 기존 적재 비파괴.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.agent.repository.backfill import backfill_bom_edges
from src.agent.repository.bom import ColumnBomRepository, EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import (
    AgentFeedback,
    BomEdge,
    ChangeEvent,
    ChangeLine,
    DevPartMaster,
    SourceFile,
    ToolCallLog,
)


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _source_file(session, file_hash="h1") -> int:
    sf = SourceFile(file_name="bom.xlsx", file_hash=file_hash)
    session.add(sf)
    session.commit()
    return sf.file_id


def _edge(session, file_id, parent, child, level=None, qty=None):
    session.add(
        BomEdge(
            file_id=file_id,
            parent_pno=parent,
            child_pno=child,
            bom_level=level,
            qty=qty,
        )
    )


def _bom_row(session, file_id, pno, depth, parent=None, qty=None):
    session.add(
        DevPartMaster(
            file_id=file_id,
            form_id="bom_ag_grid_36",
            part_no_new=pno,
            bom_depth=depth,
            qty_new=qty,
            extra_fields={"bom_parent_part_no": parent} if parent else {},
        )
    )


# ── 가산 테이블 존재 (init_db / migration) ─────────────────────


def test_additive_tables_created(session):
    for model in (BomEdge, ChangeEvent, ChangeLine, ToolCallLog, AgentFeedback):
        assert session.execute(select(func.count()).select_from(model)).scalar_one() == 0


# ── walk_subtree (엣지 백엔드) ─────────────────────────────────


def test_walk_down_basic(session):
    fid = _source_file(session)
    for p, c in [("A", "B"), ("A", "C"), ("B", "D"), ("B", "E")]:
        _edge(session, fid, p, c, level=1)
    session.commit()
    repo = EdgeBomRepository(session)

    nodes = repo.walk_subtree("A", direction="down", max_depth=2, file_id=fid)
    by_depth: dict[int, set[str]] = {1: set(), 2: set()}
    for n in nodes:
        by_depth[n.depth].add(n.pno)
    assert by_depth[1] == {"B", "C"}
    assert by_depth[2] == {"D", "E"}


def test_walk_dag_child_has_multiple_parents(session):
    fid = _source_file(session)
    # D는 B와 C 두 부모 아래 (실데이터 DAG 패턴).
    for p, c in [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]:
        _edge(session, fid, p, c)
    session.commit()
    repo = EdgeBomRepository(session)

    down = repo.walk_subtree("A", direction="down", max_depth=2, file_id=fid)
    d_at2 = [n for n in down if n.depth == 2 and n.pno == "D"]
    assert {n.from_pno for n in d_at2} == {"B", "C"}

    # 상향: D의 부모는 B,C 둘 다 (단일컬럼이면 표현 불가 — 엣지라 가능).
    up = repo.walk_subtree("D", direction="up", max_depth=1, file_id=fid)
    assert {n.pno for n in up} == {"B", "C"}


def test_walk_cycle_guard(session):
    fid = _source_file(session)
    for p, c in [("X", "Y"), ("Y", "Z"), ("Z", "X")]:
        _edge(session, fid, p, c)
    session.commit()
    repo = EdgeBomRepository(session)

    nodes = repo.walk_subtree("X", direction="down", max_depth=4, file_id=fid)
    assert {n.pno for n in nodes} == {"Y", "Z"}
    assert all(n.pno != "X" for n in nodes)


def test_walk_max_depth_clamped_to_4(session):
    fid = _source_file(session)
    chain = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
    for i in range(len(chain) - 1):
        _edge(session, fid, chain[i], chain[i + 1])
    session.commit()
    repo = EdgeBomRepository(session)

    nodes = repo.walk_subtree("L0", direction="down", max_depth=10, file_id=fid)
    assert max(n.depth for n in nodes) == 4
    assert {n.pno for n in nodes} == {"L1", "L2", "L3", "L4"}


def test_walk_file_id_scoping(session):
    f1 = _source_file(session, "hf1")
    f2 = _source_file(session, "hf2")
    _edge(session, f1, "A", "B")
    _edge(session, f2, "A", "C")
    session.commit()
    repo = EdgeBomRepository(session)

    assert {n.pno for n in repo.walk_subtree("A", file_id=f1)} == {"B"}
    assert {n.pno for n in repo.walk_subtree("A", file_id=f2)} == {"C"}
    assert {n.pno for n in repo.walk_subtree("A", file_id=None)} == {"B", "C"}


# ── backfill ───────────────────────────────────────────────────


def test_backfill_bom_edges_and_idempotent(session):
    fid = _source_file(session)
    _bom_row(session, fid, "ROOT", 0)
    _bom_row(session, fid, "C1", 1, parent="ROOT", qty=1.0)
    _bom_row(session, fid, "C2", 2, parent="C1", qty=2.0)
    session.commit()

    inserted = backfill_bom_edges(session)
    assert inserted == 2
    assert session.execute(select(func.count()).select_from(BomEdge)).scalar_one() == 2

    # 재실행 → 0 (idempotent)
    assert backfill_bom_edges(session) == 0
    assert session.execute(select(func.count()).select_from(BomEdge)).scalar_one() == 2

    repo = EdgeBomRepository(session)
    down = repo.walk_subtree("ROOT", direction="down", max_depth=2, file_id=fid)
    assert {n.pno for n in down} == {"C1", "C2"}
    # model 라벨 = 루트(최소 depth) 품번 best-effort
    edge = session.execute(select(BomEdge).where(BomEdge.child_pno == "C1")).scalar_one()
    assert edge.model == "ROOT"


# ── ColumnBomRepository (단일부모 백엔드) ──────────────────────


def test_column_backend_walk(session):
    fid = _source_file(session)
    _bom_row(session, fid, "ROOT", 0)
    _bom_row(session, fid, "C1", 1, parent="ROOT")
    _bom_row(session, fid, "C2", 2, parent="C1")
    session.commit()

    repo = ColumnBomRepository(session)
    down = repo.walk_subtree("ROOT", direction="down", max_depth=2, file_id=fid)
    assert {(n.pno, n.depth) for n in down} == {("C1", 1), ("C2", 2)}

    up = repo.walk_subtree("C2", direction="up", max_depth=2, file_id=fid)
    assert [n.pno for n in up] == ["C1", "ROOT"]


# ── change_intent 가산 컬럼 + 비파괴 ───────────────────────────


def test_change_intent_column_additive(session):
    fid = _source_file(session)
    session.add(
        DevPartMaster(
            file_id=fid,
            form_id="changing_parts_list_96",
            part_no_new="AGG74419321",
            change_point_raw="내열 220→240",
            change_intent={"change_attribute": "heat", "confidence": 0.9},
        )
    )
    session.add(
        DevPartMaster(
            file_id=fid, form_id="changing_parts_list_96", part_no_new="MFZ67394702"
        )
    )
    session.commit()

    rows = (
        session.execute(select(DevPartMaster).order_by(DevPartMaster.part_no_new))
        .scalars()
        .all()
    )
    assert rows[0].change_intent == {"change_attribute": "heat", "confidence": 0.9}
    assert rows[1].change_intent is None


# ── expand_bom_db (검수 UI용 코퍼스 BOM 전개 래퍼) ─────────────


def test_expand_bom_db_down_and_missing(tmp_path):
    from src.agent.inspect import expand_bom_db

    engine = make_engine(f"sqlite:///{tmp_path / 'e.db'}")
    init_db(engine)
    sf = session_factory(engine)
    with sf() as s:
        fid = _source_file(s)
        for p, c, q in [("A", "B", 1.0), ("A", "C", 2.0), ("B", "D", None), ("B", "E", None)]:
            _edge(s, fid, p, c, level=1, qty=q)
        for pno in ("A", "B", "C", "D", "E"):
            s.add(DevPartMaster(file_id=fid, form_id="bom_ag_grid_36",
                                part_no_new=pno, part_name=f"name-{pno}"))
        s.commit()

    view = expand_bom_db("A", direction="down", max_depth=2, session_factory=sf)
    assert view.file_id == fid and view.file_name == "bom.xlsx"
    assert {n.pno for n in view.nodes} == {"B", "C", "D", "E"}
    b = next(n for n in view.nodes if n.pno == "B")
    assert b.part_name == "name-B" and b.qty == 1.0 and b.depth == 1

    # auto file scoping(파일 안 줘도 anchor로 파일 찾음) + 미존재 부품 → 빈 결과 + 사유
    miss = expand_bom_db("ZZZ", session_factory=sf)
    assert miss.nodes == [] and miss.detail
    # backend(session_factory) 없으면 안전 폴백
    assert expand_bom_db("A", session_factory=None).nodes == []
