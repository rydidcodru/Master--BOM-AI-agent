from __future__ import annotations

from state import BOMSearchState


def collect_node(state: BOMSearchState) -> dict:
    results = state.get("search_results", [])
    total_candidates = sum(len(r["candidates"]) for r in results)
    print(f"[collect] {len(results)}개 항목 검색 완료, 총 후보 {total_candidates}건")
    return {}
