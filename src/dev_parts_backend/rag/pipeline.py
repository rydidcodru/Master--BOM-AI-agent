import sqlite3
from typing import Any

from dev_parts_backend.repository import search_details


def answer_stub(conn: sqlite3.Connection, question: str, limit: int = 8) -> dict[str, Any]:
    """RAG extension point.

    Later, this function can:
    1. Embed the question.
    2. Run vector_search.
    3. Pull neighboring BOM tree context.
    4. Send grounded context to an LLM.
    """
    matches = search_details(conn, question, limit=limit)
    return {
        "question": question,
        "answer": "LLM answer generation is not wired yet. Returning retrieved context only.",
        "contexts": matches,
    }
