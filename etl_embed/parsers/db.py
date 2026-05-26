"""Postgres + pgvector DB 레이어 (psycopg3)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from .readers.dev_part_master import ParsedRow


# ============================================================================
# .env 자동 로드 — 어떤 진입점(run.py / python -m ...)에서 import 되어도
# PGPORT 등 환경변수가 세팅되도록 모듈 import 시점에 한 번 실행.
# 이미 설정된 환경변수는 덮어쓰지 않음 (setdefault).
# ============================================================================

def _load_dotenv_once() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv_once()


# ============================================================================
# 연결 설정
# ============================================================================

def get_dsn() -> str:
    """환경변수로 DSN 조립. 없으면 로컬 기본값.

    환경변수:
      PGHOST (default: localhost)
      PGPORT (default: 5432)
      PGDATABASE (default: etl_dev)
      PGUSER (default: etl_user)
      PGPASSWORD (default: etl_pass)
    """
    return (
        f"host={os.getenv('PGHOST', 'localhost')} "
        f"port={os.getenv('PGPORT', '5432')} "
        f"dbname={os.getenv('PGDATABASE', 'etl_dev')} "
        f"user={os.getenv('PGUSER', 'etl_user')} "
        f"password={os.getenv('PGPASSWORD', 'etl_pass')}"
    )


def connect() -> psycopg.Connection:
    """기본 연결. autocommit=False (트랜잭션 명시 commit)."""
    return psycopg.connect(get_dsn(), autocommit=False)


# ============================================================================
# 스키마 초기화
# ============================================================================

def apply_schema(con: psycopg.Connection, schema_path: str | Path) -> None:
    """스키마 SQL 실행. IF NOT EXISTS / ON CONFLICT 패턴이라 멱등."""
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    with con.cursor() as cur:
        cur.execute(sql)
    con.commit()


def reset_data(con: psycopg.Connection) -> None:
    """데이터만 비우기 (스키마는 유지). 개발 중 반복 적재용."""
    with con.cursor() as cur:
        cur.execute("TRUNCATE dev_part_master, ingestion_log, source_files CASCADE;")
    con.commit()


# ============================================================================
# source_files
# ============================================================================

def register_source_file(
    con: psycopg.Connection,
    file_name: str,
    file_hash: str,
    file_size: int,
    region: str | None,
) -> str | None:
    """source_files에 INSERT. 이미 존재(file_hash 중복)면 None 반환."""
    with con.cursor() as cur:
        cur.execute(
            "SELECT file_id FROM source_files WHERE file_hash = %s",
            (file_hash,),
        )
        existing = cur.fetchone()
        if existing:
            return None
        cur.execute(
            """
            INSERT INTO source_files (file_name, file_hash, file_size, region)
            VALUES (%s, %s, %s, %s)
            RETURNING file_id
            """,
            (file_name, file_hash, file_size, region),
        )
        return str(cur.fetchone()[0])


# ============================================================================
# dev_part_master 적재
# ============================================================================

DPM_INSERT_SQL = """
INSERT INTO dev_part_master (
    file_id, form_id, sheet_name, source_row,
    region,
    base_model, base_grade, new_model, new_grade,
    event, buyer, brand, set_part_no, production_date,
    bom_level_raw, bom_depth, part_type,
    part_no_base, part_no_new, part_name,
    qty_base, qty_new,
    change_point_raw, change_reason_raw,
    supplier, classification,
    mold_dev, in_house,
    designer, fig_ref, part_grade, remark,
    extra_fields, embedding_text
) VALUES (
    %s, %s, %s, %s,
    %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s,
    %s, %s,
    %s, %s,
    %s, %s, %s, %s,
    %s, %s
)
ON CONFLICT (file_id, sheet_name, source_row) DO NOTHING
"""


def insert_dev_part_master_rows(
    con: psycopg.Connection,
    file_id: str,
    parsed_rows: Iterable[ParsedRow],
    region: str | None = None,
) -> int:
    """파싱된 행들을 dev_part_master에 INSERT.
    psycopg는 dict를 JSONB로 자동 변환하지만 명시적으로 json.dumps 사용.
    같은 (file_id, sheet, row) 조합은 ON CONFLICT로 스킵.
    """
    count = 0
    with con.cursor() as cur:
        for pr in parsed_rows:
            d = pr.data
            m = pr.meta
            embedding_text = _build_embedding_text(d, m, region)
            extra_json = (
                json.dumps(pr.extra_fields, ensure_ascii=False)
                if pr.extra_fields else None
            )
            cur.execute(
                DPM_INSERT_SQL,
                (
                    file_id, pr.form_id, pr.sheet_name, pr.source_row,
                    region,
                    m.base_model, m.base_grade, m.new_model, m.new_grade,
                    m.event, m.buyer, m.brand, m.set_part_no, m.production_date,
                    d.get("bom_level_raw"), d.get("bom_depth"), d.get("part_type"),
                    d.get("part_no_base"), d.get("part_no_new"), d.get("part_name"),
                    d.get("qty_base"), d.get("qty_new"),
                    d.get("change_point_raw"), d.get("change_reason_raw"),
                    d.get("supplier"), d.get("classification"),
                    d.get("mold_dev"), d.get("in_house"),
                    d.get("designer"), d.get("fig_ref"),
                    d.get("part_grade"), d.get("remark"),
                    extra_json, embedding_text,
                ),
            )
            count += cur.rowcount  # ON CONFLICT로 스킵되면 0
    return count


# ============================================================================
# ingestion_log
# ============================================================================

def log_ingestion(
    con: psycopg.Connection,
    file_id: str,
    sheet_name: str | None,
    form_id: str | None,
    target_table: str | None,
    rows_total: int,
    rows_inserted: int,
    rows_skipped: int,
    status: str = "completed",
    error_message: str | None = None,
) -> None:
    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_log (
                file_id, sheet_name, form_id, target_table,
                rows_total, rows_inserted, rows_skipped,
                status, error_message, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::ingest_status, %s, NOW())
            """,
            (file_id, sheet_name, form_id, target_table,
             rows_total, rows_inserted, rows_skipped,
             status, error_message),
        )


# ============================================================================
# 임베딩 텍스트 조립 (실제 벡터 생성은 별도 단계)
# ============================================================================

def _build_embedding_text(data: dict, meta, region: str | None = None) -> str:
    """임베딩 입력 본문. 자기서술형으로 컨텍스트 포함."""
    parts = []
    if region:
        parts.append(f"지역: {region}")
    if meta.base_model and meta.new_model:
        parts.append(f"모델: {meta.base_model} → {meta.new_model}")
    elif meta.new_model:
        parts.append(f"모델: {meta.new_model}")
    if meta.event:
        parts.append(f"이벤트: {meta.event}")
    if data.get("part_no_base") and data.get("part_no_new"):
        parts.append(f"부품번호: {data['part_no_base']} → {data['part_no_new']}")
    elif data.get("part_no_new"):
        parts.append(f"부품번호: {data['part_no_new']}")
    if data.get("part_name"):
        parts.append(f"부품명: {data['part_name']}")
    if data.get("change_point_raw"):
        parts.append(f"변경점: {data['change_point_raw']}")
    if data.get("change_reason_raw"):
        parts.append(f"변경사유: {data['change_reason_raw']}")
    if data.get("classification"):
        parts.append(f"구분: {data['classification']}")
    return " | ".join(parts)
