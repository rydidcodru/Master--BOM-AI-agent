"""실행 스크립트 — Postgres에 모든 엑셀 적재."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from parsers.db import apply_schema, connect, reset_data
from parsers.pipeline import ingest_file


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding real environment vars."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(Path(__file__).parent / ".env")


# 엑셀 파일 위치 - 환경에 맞게 수정
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/mnt/user-data/uploads"))
SCHEMA_PATH = Path(__file__).parent / "schema_postgres.sql"

EXCEL_EXTENSIONS = {".xlsx", ".xlsm"}


def iter_excel_files(upload_dir: Path) -> list[Path]:
    """Return visible Excel files in a stable order."""
    if not upload_dir.exists():
        return []
    return sorted(
        path for path in upload_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in EXCEL_EXTENSIONS
        and not path.name.startswith("~$")
    )


def main() -> None:
    print("=" * 60)
    print("ETL Pipeline - Postgres + pgvector")
    print("=" * 60)

    with connect() as con:
        # 1) 스키마 적용 (멱등)
        apply_schema(con, SCHEMA_PATH)
        print(f"\n[OK] Schema applied: {SCHEMA_PATH.name}")

        # 2) 개발 중에는 데이터 매번 새로 적재 (운영에서는 빼야 함)
        reset_data(con)
        print("[OK] Data tables truncated")

        # 3) 파일 순회 적재
        summaries = []
        for path in iter_excel_files(UPLOAD_DIR):
            try:
                summary = ingest_file(con, path)
                summaries.append(summary)
            except Exception as e:
                print(f"\n[ERROR] {path.name}: {type(e).__name__}: {e}")

        # 4) 요약 + 검증
        print("\n" + "=" * 60)
        print("INGESTION SUMMARY")
        print("=" * 60)
        total_rows = sum(s.get("total_rows_inserted", 0) for s in summaries)
        print(f"Files processed: {len(summaries)}")
        print(f"Total rows inserted: {total_rows}")

        with con.cursor() as cur:
            cur.execute("SELECT count(*) FROM source_files")
            print(f"\nsource_files: {cur.fetchone()[0]}")
            cur.execute("SELECT count(*) FROM dev_part_master")
            print(f"dev_part_master: {cur.fetchone()[0]}")
            cur.execute("SELECT count(*) FROM ingestion_log")
            print(f"ingestion_log: {cur.fetchone()[0]}")

            print("\n--- Rows per file ---")
            cur.execute("""
                SELECT sf.file_name, COUNT(dpm.doc_id) AS rows
                FROM source_files sf
                LEFT JOIN dev_part_master dpm ON dpm.file_id = sf.file_id
                GROUP BY sf.file_name ORDER BY rows DESC
            """)
            for fname, rows in cur.fetchall():
                short = fname[:60] + "..." if len(fname) > 60 else fname
                print(f"  {rows:>4} rows  {short}")

            print("\n--- Sample (first 3) ---")
            cur.execute("""
                SELECT region, base_model, new_model, part_no_new, part_name,
                       substring(change_reason_raw, 1, 50) AS reason
                FROM dev_part_master LIMIT 3
            """)
            for i, row in enumerate(cur.fetchall(), 1):
                print(f"\n[{i}] region={row[0]}")
                print(f"    {row[1]} -> {row[2]}")
                print(f"    {row[3]}: {row[4]}")
                print(f"    이유: {row[5]}")

    print("\n[OK] Done.")


if __name__ == "__main__":
    main()
