from __future__ import annotations

"""graph를 UI와 CLI 양쪽에서 공유해서 쓰기 위한 실행 모듈."""

import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from graph import compile_graph  # noqa: E402
from nodes.parse_pptx import parse_pptx_node  # noqa: E402
from nodes.normalize import normalize_node  # noqa: E402
from nodes.fan_out import fan_out_node  # noqa: E402
from nodes.item_search import item_search_node  # noqa: E402
from nodes.collect import collect_node  # noqa: E402
from nodes.rank_candidates import rank_candidates_node  # noqa: E402
from langgraph.constants import Send  # noqa: E402


def run_pipeline(pptx_paths: list[str]) -> list[dict]:
    """
    PPTX 경로 리스트를 받아 search_results를 반환한다.
    각 item: {change_point, candidates, ranked, retry_count}
    """
    graph = compile_graph()
    result = graph.invoke({
        "pptx_paths": pptx_paths,
        "change_points": [],
        "search_results": [],
        "human_selections": [],
        "bom_updates": [],
    })
    return result.get("search_results", [])


def run_pipeline_from_uploads(uploaded_files: list) -> list[dict]:
    """
    Streamlit의 UploadedFile 리스트를 받아 임시 파일로 저장 후 실행.
    """
    tmp_paths = save_uploads(uploaded_files)
    return run_pipeline(tmp_paths)


def save_uploads(uploaded_files: list) -> list[str]:
    """UploadedFile 리스트를 임시 디렉토리에 저장하고 경로 리스트 반환."""
    tmp_dir = tempfile.mkdtemp()
    paths = []
    for f in uploaded_files:
        tmp_path = Path(tmp_dir) / f.name
        tmp_path.write_bytes(f.read())
        paths.append(str(tmp_path))
    return paths


# ── 단계별 실행 함수 (Streamlit 3단계 UI용) ──────────────────────────

def step1_parse(pptx_paths: list[str]) -> list[dict]:
    """1단계: PPTX → change_points 추출."""
    state: dict = {"pptx_paths": pptx_paths, "change_points": []}
    result = parse_pptx_node(state)
    return result.get("change_points", [])


def step2_normalize(change_points: list[dict]) -> list[dict]:
    """2단계: change_points 부품명 정규화."""
    state: dict = {"change_points": change_points}
    result = normalize_node(state)
    return result.get("change_points", change_points)


def step4_build_master(search_results: list[dict],
                       selections: dict,
                       new_model: str = "",
                       base_model: str = "") -> tuple[list[dict], bytes]:
    """
    4단계: 선정된 후보 → Master BOM 행 조립 + 변경사유 LLM 생성 + Excel bytes 반환.
    반환: (rows, excel_bytes)
    """
    from nodes.write_master import build_master_rows, rows_to_excel
    rows = build_master_rows(search_results, selections)
    excel_bytes = rows_to_excel(rows, new_model=new_model, base_model=base_model)
    return rows, excel_bytes


def step3_search_and_rank(change_points: list[dict]) -> list[dict]:
    """3단계: fan_out → item_search(병렬) → collect → rank_candidates."""
    import operator

    # fan_out: Send() 목록 생성
    sends = fan_out_node({"change_points": change_points})

    # item_search 병렬 실행 (단일 스레드로 순차 실행 — 데모용)
    raw_results: list[dict] = []
    for send in sends:
        if isinstance(send, Send):
            res = item_search_node(send.arg)
            raw_results = operator.add(raw_results, res.get("search_results", []))

    # collect
    collect_node({"search_results": raw_results})

    # rank_candidates
    ranked_state = rank_candidates_node({"search_results": raw_results})
    return ranked_state.get("search_results", raw_results)
