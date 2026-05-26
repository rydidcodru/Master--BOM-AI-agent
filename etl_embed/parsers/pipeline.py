"""파일 → Postgres 적재 파이프라인."""
from __future__ import annotations

from pathlib import Path

import psycopg

from .db import (
    insert_dev_part_master_rows, log_ingestion, register_source_file,
)
from .readers.dev_part_master import read_file as read_dev_part_master_file
from .utils import file_sha256


REGION_HINTS = [
    ("동유럽", "동유럽"),
    ("싱가포르", "싱가포르"),
    ("모리셔스", "모리셔스"),
    ("사우디", "사우디"),
    ("이집트", "이집트"),
    ("북유럽", "북유럽"),
    ("북미", "북미"),
    ("포르투갈", "포르투갈/그리스"),
    ("그리스", "포르투갈/그리스"),
    ("호주", "호주"),
    ("UAE", "UAE"),
    ("이라크", "이라크"),
    ("CIS", "CIS"),
    ("유럽", "유럽"),
]


def infer_region(file_name: str) -> str | None:
    for needle, region in REGION_HINTS:
        if needle in file_name:
            return region
    return None


def ingest_file(con: psycopg.Connection, file_path: str | Path, verbose: bool = True) -> dict:
    """단일 파일 적재.

    트랜잭션 단위: 파일 1개 = 1 transaction.
    파일 중간에 실패하면 그 파일의 모든 적재가 롤백됨.
    """
    file_path = Path(file_path)
    file_name = file_path.name
    file_hash = file_sha256(file_path)
    file_size = file_path.stat().st_size
    region = infer_region(file_name)

    if verbose:
        print(f"\n[ingest] {file_name}")
        print(f"  region={region}, size={file_size:,}B, sha256={file_hash[:12]}...")

    try:
        file_id = register_source_file(con, file_name, file_hash, file_size, region)
        if file_id is None:
            con.rollback()
            if verbose:
                print("  -> SKIPPED (already ingested)")
            return {"file_name": file_name, "status": "skipped_duplicate"}

        parsed_rows, skipped_sheets = read_dev_part_master_file(file_path)

        rows_by_sheet: dict[str, list] = {}
        for pr in parsed_rows:
            rows_by_sheet.setdefault(pr.sheet_name, []).append(pr)

        total_inserted = 0
        for sheet_name, rows in rows_by_sheet.items():
            form_id = rows[0].form_id
            inserted = insert_dev_part_master_rows(con, file_id, rows, region=region)
            total_inserted += inserted
            log_ingestion(
                con, file_id, sheet_name, form_id, "dev_part_master",
                rows_total=len(rows), rows_inserted=inserted, rows_skipped=0,
                status="completed",
            )
            if verbose:
                print(f"  [OK] sheet '{sheet_name}' [{form_id}]: {inserted} rows")

        for sheet_name in skipped_sheets:
            log_ingestion(
                con, file_id, sheet_name, None, None,
                rows_total=0, rows_inserted=0, rows_skipped=0,
                status="skipped", error_message="No matching form",
            )
            if verbose:
                print(f"  - sheet '{sheet_name}': skipped (no matching form)")

        con.commit()
        return {
            "file_name": file_name,
            "status": "completed",
            "file_id": file_id,
            "sheets_ingested": len(rows_by_sheet),
            "sheets_skipped": len(skipped_sheets),
            "total_rows_inserted": total_inserted,
        }
    except Exception as e:
        con.rollback()
        if verbose:
            print(f"  [ERROR] {type(e).__name__}: {e}")
        raise
