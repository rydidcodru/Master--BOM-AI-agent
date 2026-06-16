import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import refresh_fts, reset_data


MASTER_COLS = [
    "source_file",
    "source_sheet",
    "base_model",
    "base_grade",
    "new_model",
    "new_grade",
    "event",
    "region",
    "project_name",
]


def read_csv(path: str | Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, restkey="_extra_columns")
        rows = list(reader)
    for row in rows:
        if isinstance(row.get("_extra_columns"), list):
            row["_extra_columns"] = json.dumps(row["_extra_columns"], ensure_ascii=False)
    return rows


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def nullable_int(value: Any) -> int | None:
    value = text(value)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def to_bool(value: Any) -> int | None:
    value = text(value).upper()
    if not value:
        return None
    if value in {"O", "Y", "YES", "TRUE", "T", "1", "ON", "○", "필요", "대상"}:
        return 1
    if value in {"X", "N", "NO", "FALSE", "F", "0", "OFF", "×", "불필요", "미대상"}:
        return 0
    return None


def normalize_part_no(value: Any) -> str:
    return re.sub(r"[\s\-_/\.]+", "", text(value).upper())


def normalize_qty(value: Any) -> str:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text(value))
    return match.group(0).replace(",", "") if match else ""


def parse_bom_depth(row: dict[str, Any]) -> int | None:
    explicit = nullable_int(row.get("bom_depth"))
    if explicit is not None:
        return explicit
    level = text(row.get("bom_level"))
    if not level:
        return None
    match = re.search(r"(\d+)\s*$", level)
    return int(match.group(1)) if match else None


def valid_json_text(value: Any, default: str = "{}") -> str:
    value = text(value)
    if not value:
        return default
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return json.dumps({"raw": value}, ensure_ascii=False)
    return value


def build_changing_text(row: dict[str, Any]) -> str:
    if text(row.get("changing_text")):
        return text(row.get("changing_text"))
    parts = []
    if text(row.get("changing_point")):
        parts.append(f"Changing point: {text(row.get('changing_point'))}")
    if text(row.get("changing_reason")):
        parts.append(f"Changing reason: {text(row.get('changing_reason'))}")
    return "\n".join(parts)


def embedding_text(row: dict[str, Any], key: str, fallback_key: str | None = None) -> str:
    value = text(row.get(key))
    if not value and fallback_key:
        value = text(row.get(fallback_key))
    return valid_json_text(value, default="[]")


def load_csvs(
    conn: sqlite3.Connection,
    master_csv: str | Path,
    detail_csv: str | Path,
    *,
    reset: bool = False,
) -> dict[str, int]:
    if reset:
        reset_data(conn)

    master_rows = read_csv(master_csv)
    detail_rows = read_csv(detail_csv)
    master_key_to_id: dict[str, int] = {}

    for row in master_rows:
        input_key = text(row.get("input_master_key"))
        if not input_key:
            raise ValueError("master.csv has a row without input_master_key")
        cur = conn.execute(
            """
            INSERT INTO dev_part_master (
                source_file, source_sheet, base_model, base_grade, new_model,
                new_grade, event, region, project_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [text(row.get(col)) for col in MASTER_COLS],
        )
        master_key_to_id[input_key] = int(cur.lastrowid)

    pending_parent_rows: list[tuple[int, str, int | None, int | None]] = []
    line_to_detail_id: dict[tuple[int, int], int] = {}
    last_line_by_depth: dict[int, dict[int, int]] = {}

    for row in detail_rows:
        input_key = text(row.get("input_master_key"))
        master_id = master_key_to_id.get(input_key)
        if master_id is None:
            raise ValueError(f"detail.csv references unknown input_master_key: {input_key}")

        line_order = nullable_int(row.get("line_order"))
        if line_order is None:
            raise ValueError(f"detail.csv row has invalid line_order: {row}")

        bom_depth = parse_bom_depth(row)
        parent_line_order = nullable_int(row.get("parent_line_order"))
        new_part_no = text(row.get("new_part_no"))

        cur = conn.execute(
            """
            INSERT INTO dev_part_detail (
                master_id, line_order, row_no, bom_level, bom_depth, parent_detail_id,
                part_type, base_part_no, new_part_no, normalized_new_part_no,
                part_name, base_qty, new_qty, normalized_new_qty,
                changing_point, changing_reason, supplier, classification,
                mold_dev_modify, inhouse_prod, part_approval_test,
                raw_json, changing_text, changing_embedding,
                changing_reason_embedding, changing_point_embedding,
                part_name_embedding, combined_embedding
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                master_id,
                line_order,
                nullable_int(row.get("row_no")),
                text(row.get("bom_level")),
                bom_depth,
                text(row.get("part_type")),
                text(row.get("base_part_no")),
                new_part_no,
                normalize_part_no(new_part_no),
                text(row.get("part_name")),
                text(row.get("base_qty")),
                text(row.get("new_qty")),
                normalize_qty(row.get("new_qty")),
                text(row.get("changing_point")),
                text(row.get("changing_reason")),
                text(row.get("supplier")),
                text(row.get("classification")),
                to_bool(row.get("mold_dev_modify")),
                to_bool(row.get("inhouse_prod")),
                to_bool(row.get("part_approval_test")),
                valid_json_text(row.get("raw_json")),
                build_changing_text(row),
                embedding_text(row, "changing_embedding", "combined_embedding"),
                embedding_text(row, "changing_reason_embedding"),
                embedding_text(row, "changing_point_embedding"),
                embedding_text(row, "part_name_embedding"),
                embedding_text(row, "combined_embedding", "changing_embedding"),
            ],
        )
        detail_id = int(cur.lastrowid)
        line_to_detail_id[(master_id, line_order)] = detail_id
        pending_parent_rows.append((detail_id, input_key, line_order, parent_line_order))

        if bom_depth is not None:
            by_depth = last_line_by_depth.setdefault(master_id, {})
            by_depth[bom_depth] = line_order
            for depth in list(by_depth):
                if depth > bom_depth:
                    by_depth.pop(depth, None)

    for detail_id, input_key, line_order, parent_line_order in pending_parent_rows:
        master_id = master_key_to_id[input_key]
        parent_detail_id = None
        if parent_line_order is not None:
            parent_detail_id = line_to_detail_id.get((master_id, parent_line_order))
        else:
            row = conn.execute(
                "SELECT bom_depth FROM dev_part_detail WHERE detail_id = ?",
                (detail_id,),
            ).fetchone()
            bom_depth = row["bom_depth"] if row else None
            if bom_depth and bom_depth > 1:
                parent = conn.execute(
                    """
                    SELECT detail_id
                    FROM dev_part_detail
                    WHERE master_id = ?
                      AND line_order < ?
                      AND bom_depth = ?
                    ORDER BY line_order DESC
                    LIMIT 1
                    """,
                    (master_id, line_order, bom_depth - 1),
                ).fetchone()
                parent_detail_id = parent["detail_id"] if parent else None

        if parent_detail_id:
            conn.execute(
                "UPDATE dev_part_detail SET parent_detail_id = ? WHERE detail_id = ?",
                (parent_detail_id, detail_id),
            )

    refresh_fts(conn)
    conn.commit()

    return {
        "masters": len(master_rows),
        "details": len(detail_rows),
        "embedded": conn.execute(
            """
            SELECT COUNT(*)
            FROM dev_part_detail
            WHERE (combined_embedding IS NOT NULL AND combined_embedding <> '[]')
               OR (changing_embedding IS NOT NULL AND changing_embedding <> '[]')
            """
        ).fetchone()[0],
        "linked_parents": conn.execute(
            "SELECT COUNT(*) FROM dev_part_detail WHERE parent_detail_id IS NOT NULL"
        ).fetchone()[0],
    }
