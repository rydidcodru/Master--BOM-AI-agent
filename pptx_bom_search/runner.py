from __future__ import annotations

"""graph를 UI와 CLI 양쪽에서 공유해서 쓰기 위한 실행 모듈."""

import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from nodes.parse_pptx import parse_pptx_node          # noqa: E402
from nodes.bom_match import bom_match_node            # noqa: E402
from nodes.history_search import history_search_node  # noqa: E402
from nodes.export_excel import generate_excel         # noqa: E402


def save_uploads(uploaded_files: list) -> list[str]:
    """UploadedFile 리스트를 임시 디렉토리에 저장하고 경로 리스트 반환."""
    tmp_dir = tempfile.mkdtemp()
    paths = []
    for f in uploaded_files:
        tmp_path = Path(tmp_dir) / f.name
        tmp_path.write_bytes(f.read())
        paths.append(str(tmp_path))
    return paths


def save_upload(uploaded_file) -> str:
    """UploadedFile 단건을 임시 파일로 저장하고 경로 반환."""
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir) / uploaded_file.name
    tmp_path.write_bytes(uploaded_file.read())
    return str(tmp_path)


# ── 단계별 실행 함수 ──────────────────────────────────────────────────

def step1_parse(pptx_paths: list[str]) -> list[dict]:
    """STEP 1: PPTX → change_points 추출."""
    state: dict = {"pptx_paths": pptx_paths, "change_points": []}
    result = parse_pptx_node(state)
    return result.get("change_points", [])


def step3_bom_match(change_points: list[dict], base_bom_path: str) -> list[dict]:
    """STEP 3: change_points + Base BOM → LLM 매칭 + 하위 트리 전개."""
    state: dict = {
        "change_points": change_points,
        "base_bom_path": base_bom_path,
    }
    result = bom_match_node(state)
    return result.get("change_points", change_points)


def step4_history_search(change_points: list[dict]) -> list[dict]:
    """STEP 4: base_part_no로 과거 이력 조회 + Top 5 후보 + 연동 부품 추출."""
    state: dict = {"change_points": change_points}
    result = history_search_node(state)
    return result.get("change_points", change_points)


def step5_export(
    change_points: list[dict],
    selected_linked: list[dict],
    base_model: str = "",
    new_model: str  = "TBD",
    event: str      = "",
) -> bytes:
    """STEP 5: 변경부품리스트.xlsx bytes 반환."""
    return generate_excel(change_points, selected_linked, base_model, new_model, event)
