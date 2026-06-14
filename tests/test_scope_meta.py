"""P7.4 — ①.5 관측성(ScopeMeta) + 닻 갭 + trgm 캐싱 동일성 테스트."""

from __future__ import annotations

import pytest

from src.agent.matching.scoring import (
    MatchThresholds,
    ScopeIndex,
    ScopeNode,
    trgm_word_similarity,
)
from src.agent.orchestrator.structure_scope import scope_status
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, DevPartMaster, SourceFile
from src.preprocess.normalize import canonicalize_part_name


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


TH = MatchThresholds()


# ── 실패 사유 3종 ───────────────────────────────────────────────────────────


def test_meta_module_no_match_when_empty_names(session):
    meta = scope_status([], "M", session, TH)
    assert meta.fail_reason == "module_no_match"


def test_meta_file_not_found(session):
    # bom_edge 비어 있음 → file 해석 실패.
    meta = scope_status(["Door"], "NOPE", session, TH)
    assert meta.fail_reason == "file_not_found"


def test_meta_module_no_match_records_best(session):
    sf = SourceFile(file_name="WSED7613S.xlsx", file_hash="h")
    session.add(sf)
    session.commit()
    session.add(DevPartMaster(file_id=sf.file_id, form_id="bom_ag_grid_36",
                              part_no_new="P1", part_name="Heater Assembly",
                              sheet_name="s", source_row=1))
    session.add(BomEdge(file_id=sf.file_id, parent_pno="ROOT", child_pno="P1", bom_level=1))
    session.commit()
    # 전혀 안 닮은 모듈명 → 닻 미매칭, best_no_match_score 기록.
    meta = scope_status(["ZZZQWERTY"], "WSED7613S", session, TH)
    assert meta.fail_reason == "module_no_match"
    assert meta.best_no_match_score is not None


# ── 닻 모호 갭 플래그 ───────────────────────────────────────────────────────


def test_ambiguous_anchor_flag(session):
    sf = SourceFile(file_name="M.xlsx", file_hash="h")
    session.add(sf)
    session.commit()
    # 동일명 노드 2개 → 단일 모듈명 닻의 top1-top2 갭 0 < eps → ambiguous.
    for i, p in enumerate(("P1", "P2")):
        session.add(DevPartMaster(file_id=sf.file_id, form_id="bom_ag_grid_36",
                                  part_no_new=p, part_name="Cover Assembly",
                                  sheet_name="s", source_row=i + 1))
        session.add(BomEdge(file_id=sf.file_id, parent_pno=p, child_pno=f"C{i}", bom_level=1))
    session.commit()
    meta = scope_status(["Cover Assembly"], "M", session, TH)
    assert meta.ambiguous_anchor is True


# ── trgm 캐싱 전후 동일성 (score_line / top_nodes) ──────────────────────────


def _node(pno, name):
    return ScopeNode(pno, name, canonicalize_part_name(name), None, 1, f"/{pno}/")


def test_cached_trigrams_score_identical():
    nodes = [_node("P1", "HEATER Assembly"), _node("P2", "BRACKET MOUNTING"),
             _node("P3", "DOOR HANDLE")]
    idx = ScopeIndex(nodes, TH)
    # 캐시 경로(score_line) vs 직접 trgm 계산이 동일해야 함.
    for q in ("HEATER Assmebly", "브래킷", "Door"):
        canon = canonicalize_part_name(q)
        cached = idx.score_line(part_name=q).best
        direct = max(trgm_word_similarity(canon, n.part_name_canon) for n in nodes)
        assert cached == pytest.approx(direct)


def test_meta_attached_on_success():
    nodes = [_node("P1", "HEATER Assembly")]
    idx = ScopeIndex(nodes, TH)
    assert idx.meta is not None  # 기본 ScopeMeta
