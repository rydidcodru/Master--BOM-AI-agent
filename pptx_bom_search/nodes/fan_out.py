from __future__ import annotations

from langgraph.constants import Send

from state import BOMSearchState


def fan_out_node(state: BOMSearchState) -> list[Send]:
    change_points = state.get("change_points", [])
    if not change_points:
        return []
    print(f"[fan_out] {len(change_points)}개 항목 병렬 검색 시작")
    return [
        Send("item_search", {"item": cp, "retry_count": 0})
        for cp in change_points
    ]
