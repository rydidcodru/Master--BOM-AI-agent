from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from nodes.bom_match import bom_match_node
from nodes.parse_pptx import parse_pptx_node
from state import BOMSearchState


def build_graph() -> StateGraph:
    g = StateGraph(BOMSearchState)

    g.add_node("parse_pptx", parse_pptx_node)
    g.add_node("bom_match",  bom_match_node)

    g.add_edge(START,        "parse_pptx")
    g.add_edge("parse_pptx", "bom_match")
    g.add_edge("bom_match",  END)

    return g


def compile_graph():
    return build_graph().compile()
