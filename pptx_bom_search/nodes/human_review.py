from __future__ import annotations

from state import BOMSearchState


def human_review_node(state: BOMSearchState) -> dict:
    # 추후: langgraph.types.interrupt(state["search_results"]) 로 교체
    # 사용자가 후보를 선택하고 내용을 수정하는 지점
    print("[STUB] human_review: 사용자 선택 미구현")
    return {"human_selections": []}
