import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "masters": conn.execute("SELECT COUNT(*) FROM dev_part_master").fetchone()[0],
        "details": conn.execute("SELECT COUNT(*) FROM dev_part_detail").fetchone()[0],
        "embedded": conn.execute(
            """
            SELECT COUNT(*)
            FROM dev_part_detail
            WHERE (combined_embedding IS NOT NULL AND combined_embedding <> '[]')
               OR (changing_embedding IS NOT NULL AND changing_embedding <> '[]')
            """
        ).fetchone()[0],
        "embedded_reason": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE changing_reason_embedding IS NOT NULL AND changing_reason_embedding <> '[]'"
        ).fetchone()[0],
        "embedded_point": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE changing_point_embedding IS NOT NULL AND changing_point_embedding <> '[]'"
        ).fetchone()[0],
        "embedded_part_name": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE part_name_embedding IS NOT NULL AND part_name_embedding <> '[]'"
        ).fetchone()[0],
        "embedded_combined": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE combined_embedding IS NOT NULL AND combined_embedding <> '[]'"
        ).fetchone()[0],
        "linked_parents": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE parent_detail_id IS NOT NULL"
        ).fetchone()[0],
    }


def list_masters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT m.master_id, m.source_file, m.source_sheet, m.project_name,
               m.base_model, m.new_model, m.region, COUNT(d.detail_id) AS details
        FROM dev_part_master m
        LEFT JOIN dev_part_detail d ON d.master_id = m.master_id
        GROUP BY m.master_id
        ORDER BY m.master_id
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def search_details(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    if not query.strip():
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            ORDER BY d.master_id, d.line_order
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    try:
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region
            FROM dev_part_detail_fts f
            JOIN dev_part_detail d ON d.detail_id = f.detail_id
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE dev_part_detail_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query}%"
        rows = conn.execute(
            """
            SELECT d.*, m.source_file, m.source_sheet, m.project_name, m.region
            FROM dev_part_detail d
            JOIN dev_part_master m ON m.master_id = d.master_id
            WHERE COALESCE(d.part_name, '') LIKE ?
               OR COALESCE(d.base_part_no, '') LIKE ?
               OR COALESCE(d.new_part_no, '') LIKE ?
               OR COALESCE(d.changing_point, '') LIKE ?
               OR COALESCE(d.changing_reason, '') LIKE ?
            ORDER BY d.master_id, d.line_order
            LIMIT ?
            """,
            (like, like, like, like, like, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_tree(conn: sqlite3.Connection, master_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT detail_id, parent_detail_id, line_order, bom_level, bom_depth,
               part_type, base_part_no, new_part_no, part_name
        FROM dev_part_detail
        WHERE master_id = ?
        ORDER BY line_order
        """,
        (master_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]
