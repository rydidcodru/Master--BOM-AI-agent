from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from nodes.collect import collect_node
from nodes.fan_out import fan_out_node
from nodes.human_review import human_review_node
from nodes.item_search import item_search_node
from nodes.merge_selections import merge_selections_node
from nodes.normalize import normalize_node
from nodes.output import output_node
from nodes.parse_pptx import parse_pptx_node
from nodes.rank_candidates import rank_candidates_node
from nodes.write_bom import write_bom_node
from state import BOMSearchState


def build_graph() -> StateGraph:
    g = StateGraph(BOMSearchState)

    # ── 노드 등록 ──────────────────────────────────────────
    g.add_node("parse_pptx",       parse_pptx_node)
    g.add_node("normalize",        normalize_node)
    g.add_node("fan_out",          fan_out_node)
    g.add_node("item_search",      item_search_node)    # Send() 수신 노드
    g.add_node("collect",          collect_node)
    g.add_node("rank_candidates",  rank_candidates_node) # LLM 의미 유사도 판단
    g.add_node("output",           output_node)          # ★ 1차 목표 END
    g.add_node("human_review",     human_review_node)    # STUB
    g.add_node("merge_selections", merge_selections_node) # STUB
    g.add_node("write_bom",        write_bom_node)       # STUB

    # ── 엣지 ───────────────────────────────────────────────
    g.add_edge(START,               "parse_pptx")
    g.add_edge("parse_pptx",        "normalize")
    g.add_edge("normalize",         "fan_out")
    g.add_conditional_edges("fan_out", fan_out_node, ["item_search"])
    g.add_edge("item_search",       "collect")
    g.add_edge("collect",           "rank_candidates")   # collect 후 LLM 랭킹
    g.add_edge("rank_candidates",   "output")
    g.add_edge("output",            "human_review")
    g.add_edge("human_review",      "merge_selections")
    g.add_edge("merge_selections",  "write_bom")
    g.add_edge("write_bom",         END)

    return g


def compile_graph():
    return build_graph().compile()
