"""파이프라인 실행 엔드포인트."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

router = APIRouter()

FIXTURE_MAP = {
    "quickzone":    "fixtures/quickzone_bommatch.json",
    "compact_oven": "fixtures/compact_oven_change_points.json",
}

HISTORY_FIXTURE_MAP = {
    "quickzone":    "fixtures/quickzone_history.json",
    "compact_oven": "fixtures/compact_oven_change_points.json",
}

NODES_ROOT = Path(__file__).parent.parent.parent


class BomMatchResult(BaseModel):
    change_points: list[dict]
    bom_rows: list[dict]
    total: int


class HistoryResult(BaseModel):
    change_points: list[dict]
    total: int


# ── STEP 1+2: parse_pptx → bom_match ────────────────────────────────────────

@router.post("/bom_match", response_model=BomMatchResult)
async def run_bom_match(
    pptx:    UploadFile | None = File(None),
    bom:     UploadFile | None = File(None),
    fixture: str | None       = Form(None),  # "quickzone" | "compact_oven"
):
    """
    PPTX + BOM 업로드 또는 fixture 이름으로 parse_pptx → bom_match 실행.
    결과를 사용자에게 보여준 후, /history_search로 넘어간다.
    """
    from nodes.parse_pptx import parse_pptx_node
    from nodes.bom_match import bom_match_node

    # ── 변경점 확보 ──────────────────────────────────────────────────────
    if fixture:
        path = NODES_ROOT / FIXTURE_MAP[fixture]
        change_points = json.loads(path.read_text(encoding="utf-8"))
        # bommatch fixture는 이미 bom_match 완료 상태 → bom_match 스킵
        bom_rows: list[dict] = []
        return BomMatchResult(change_points=change_points, bom_rows=bom_rows, total=len(change_points))

    if not pptx:
        return BomMatchResult(change_points=[], bom_rows=[], total=0)

    content = await pptx.read()
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        f.write(content)
        tmp_pptx = f.name
    state = parse_pptx_node({"pptx_path": tmp_pptx})
    change_points = state.get("change_points", [])
    os.unlink(tmp_pptx)

    # ── Base BOM 매핑 ────────────────────────────────────────────────────
    bom_path = ""
    tmp_bom  = None
    bom_rows = []

    if bom:
        content = await bom.read()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(content)
            bom_path = tmp_bom = f.name

    state2 = bom_match_node({"change_points": change_points, "base_bom_path": bom_path})
    change_points = state2.get("change_points", change_points)

    if bom_path:
        from nodes.bom_match import load_base_bom
        rows, _ = load_base_bom(bom_path)
        bom_rows = [r.to_dict() for r in rows]

    if tmp_bom:
        os.unlink(tmp_bom)

    return BomMatchResult(change_points=change_points, bom_rows=bom_rows, total=len(change_points))


# ── STEP 3: history_search ───────────────────────────────────────────────────

@router.post("/history_search", response_model=HistoryResult)
async def run_history_search(
    body: dict,
):
    """
    bom_match 결과(change_points)를 받아 history_search 실행.
    body: { change_points: [...], fixture?: "quickzone" | "compact_oven" }
    """
    from nodes.history_search import history_search_node

    fixture = body.get("fixture")
    if fixture and fixture in HISTORY_FIXTURE_MAP:
        path = NODES_ROOT / HISTORY_FIXTURE_MAP[fixture]
        change_points = json.loads(path.read_text(encoding="utf-8"))
        return HistoryResult(change_points=change_points, total=len(change_points))

    change_points = body.get("change_points", [])
    state = history_search_node({"change_points": change_points})
    change_points = state.get("change_points", change_points)

    return HistoryResult(change_points=change_points, total=len(change_points))


# ── 레거시: 한방 실행 (하위호환) ────────────────────────────────────────────

@router.post("/run", response_model=BomMatchResult)
async def run_pipeline(
    pptx:    UploadFile | None = File(None),
    bom:     UploadFile | None = File(None),
    fixture: str | None       = Form(None),
):
    """parse_pptx → bom_match → history_search 순서 (레거시)."""
    from nodes.parse_pptx import parse_pptx_node
    from nodes.bom_match import bom_match_node
    from nodes.history_search import history_search_node

    if fixture:
        path = NODES_ROOT / HISTORY_FIXTURE_MAP.get(fixture, FIXTURE_MAP[fixture])
        change_points = json.loads(path.read_text(encoding="utf-8"))
        return BomMatchResult(change_points=change_points, bom_rows=[], total=len(change_points))

    if not pptx:
        return BomMatchResult(change_points=[], bom_rows=[], total=0)

    content = await pptx.read()
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
        f.write(content)
        tmp_pptx = f.name
    state = parse_pptx_node({"pptx_path": tmp_pptx})
    change_points = state.get("change_points", [])
    os.unlink(tmp_pptx)

    bom_path = ""
    tmp_bom  = None
    bom_rows = []

    if bom:
        content = await bom.read()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(content)
            bom_path = tmp_bom = f.name

    state2 = bom_match_node({"change_points": change_points, "base_bom_path": bom_path})
    change_points = state2.get("change_points", change_points)

    if bom_path:
        from nodes.bom_match import load_base_bom
        rows, _ = load_base_bom(bom_path)
        bom_rows = [r.to_dict() for r in rows]

    if tmp_bom:
        os.unlink(tmp_bom)

    state3 = history_search_node({"change_points": change_points})
    change_points = state3.get("change_points", change_points)

    return BomMatchResult(change_points=change_points, bom_rows=bom_rows, total=len(change_points))
