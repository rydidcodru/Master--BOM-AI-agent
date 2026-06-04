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


def step3_explore_bom(change_points: list[dict],
                      bom_bytes: bytes) -> list[dict]:
    """
    3단계: change_points × base_bom → 변경 부품 후보 목록 반환.

    각 change_point의 canonical_part/aliases로 base_bom을 매칭하고
    하위 계층을 전개해서 반환한다.

    반환: [
      {
        "change_point": cp,
        "bom_parts": [
          {"part_no", "lvl", "description", "type", "maker", "qty"}, ...
        ]
      }, ...
    ]
    """
    from nodes.bom_hierarchy import (load_bom, find_matching_parts,
                                      expand_hierarchy, build_bom_dict,
                                      resolve_to_bom_description)

    bom_rows = load_bom(bom_bytes)
    bom_dict = build_bom_dict(bom_rows)
    print(f"[step3] base_bom 로드: {len(bom_rows)}행, 사전: {len(bom_dict)}개 부품명")

    # ── 중복 제거: canonical_part 기준으로 유니크 change_point만 처리 ──
    seen_parts: set[str] = set()
    unique_cps: list[dict] = []
    for cp in change_points:
        # base_bom 사전으로 canonical_part 보정
        resolved = resolve_to_bom_description(
            cp.get("canonical_part") or cp.get("part", ""),
            cp.get("aliases") or [],
            bom_dict,
        )
        cp = {**cp, "canonical_part": resolved}

        # 보정된 canonical_part 기준 중복 제거
        key = resolved or cp.get("part", "")
        if key not in seen_parts:
            seen_parts.add(key)
            unique_cps.append(cp)

    print(f"[step3] 중복 제거: {len(change_points)}개 → {len(unique_cps)}개 유니크 부품")

    results = []
    for cp in unique_cps:
        keywords = [cp.get("canonical_part") or ""] + (cp.get("aliases") or [])
        keywords = [k for k in keywords if k] or [cp.get("part", "")]

        matched = find_matching_parts(bom_rows, keywords)
        if matched:
            bom_parts = expand_hierarchy(bom_rows, matched[0]["part_no"])
            print(f"  '{cp.get('part')}' → {matched[0]['description']} ({len(bom_parts)}행)")
        else:
            bom_parts = []
            print(f"  '{cp.get('part')}' → base_bom 매칭 실패")

        results.append({"change_point": cp, "bom_parts": bom_parts})

    return results


def step4_search_history(confirmed_parts: list[dict]) -> list[dict]:
    """
    4단계: 확정된 부품들로 Neo4j 과거 이력 조회.

    confirmed_parts: [
      {
        "part_no", "lvl", "description", "type", "maker",
        "change_point_ref": cp,   # 원본 change_point 참조
      }, ...
    ]

    반환: [
      {
        "confirmed_part": part_dict,
        "histories": [
          {
            "changeId", "changingPoint", "changingReason",
            "basePartNoRaw", "newPartNoRaw", "modelName",
            "lineId", "documentId",
            "related_parts": [partNameRaw, ...]   # 같은 케이스 관련 부품
          }, ...
        ]
      }, ...
    ]
    """
    from nodes.history_search import search_history_by_part

    results = []
    for part in confirmed_parts:
        desc = part.get("description", "")
        print(f"  [step4] '{desc}' 이력 조회 중...")
        histories = search_history_by_part(desc)
        print(f"    → {len(histories)}건")
        results.append({"confirmed_part": part, "histories": histories})

    return results


def step5_build_master(confirmed_selections: list[dict],
                       new_model: str = "",
                       base_model: str = "") -> tuple[list[dict], bytes]:
    """
    5단계: 확정 부품 + 선택 이력 → Master BOM 행 조립 + Excel 반환.

    confirmed_selections: [
      {
        "confirmed_part": part_dict,
        "selected_history": history_dict or None,
        "custom_reason": str   # 이력 없을 때 직접 입력한 변경사유
      }, ...
    ]
    반환: (rows, excel_bytes)
    """
    from nodes.write_master import build_master_rows_from_confirmed, rows_to_excel

    rows = build_master_rows_from_confirmed(confirmed_selections)
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
