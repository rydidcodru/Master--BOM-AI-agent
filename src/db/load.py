"""D-012 Step 6 — committed run → PostgreSQL 트랜잭션 적재.

committed run 디렉토리(``data/processed/committed/<run_id>/``)에는:
  - rows.parquet            (dev_part_master 행, 정규화 + narrative 포함)
  - files.json              (FileMeta — source_files 적재 입력)
  - ingestion_log.json      (LogEntry — ingestion_log 적재 입력)

세 파일을 모두 읽어 한 트랜잭션으로 적재:
  1. source_files upsert (file_hash 기준 dedup)
  2. ingestion_log insert
  3. dev_part_master insert

임베딩은 ``update_embeddings(session)`` 별도 호출. ENABLE_EMBEDDING=1 게이트.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.load_change_events import load_change_events
from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class LoadResult:
    """적재 결과 — file_id → 테이블별 row 수. ``rows_skipped``는 멱등성으로 스킵된 행 수."""

    rows_inserted: dict[str, int] = field(default_factory=dict)
    file_ids: list[int] = field(default_factory=list)
    rows_skipped: dict[str, int] = field(default_factory=dict)


ROWS_FILE = "rows.parquet"
FILES_JSON = "files.json"
INGEST_JSON = "ingestion_log.json"


# --- IO helpers ----------------------------------------------------------


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _coerce_json(value: object) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _json_safe(value: object) -> Any:
    """JSONB 저장용 스칼라 정규화 — NaN/None→None, numpy/pandas 스칼라→파이썬, 그 외→str."""
    if isinstance(value, (dict, list)):
        return value
    try:
        if pd.isna(value):  # NaN/NaT/None
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, bool, int, float)):
        return value
    item = getattr(value, "item", None)  # numpy 스칼라 → 파이썬 스칼라
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):
            return str(value)
    return str(value)


# raw_json에서 제외할 내부 부기/임베딩 컬럼 (원본 행 데이터가 아님).
_RAW_JSON_DENY = {"embedding_text", "embedding_dense", "reason_embedding", "extra_fields"}


def build_raw_json(row: pd.Series) -> dict[str, Any] | None:
    """원본 행 전체 스냅샷(JSON-safe dict). ms BomLine.rawJson 차용.

    매핑·미매핑 컬럼을 모두 포함(extra_fields는 평탄화해 합침) → extra_fields(미매핑만)보다
    완전. 내부 부기/임베딩 컬럼·``_`` 접두 컬럼은 제외. 빈 dict면 None.
    """
    out: dict[str, Any] = {}
    # 미매핑 잔여(extra_fields)를 먼저 평탄화 — 매핑 컬럼이 같은 키면 아래서 덮어씀(정본 우선).
    extra = _coerce_json(row.get("extra_fields")) if "extra_fields" in row.index else None
    if isinstance(extra, dict):
        for ek, ev in extra.items():
            out[str(ek)] = _json_safe(ev)
    for k in row.index:
        key = str(k)
        if key in _RAW_JSON_DENY or key.startswith("_"):
            continue
        val = _json_safe(row[k])
        if val is not None:
            out[key] = val
    return out or None


def raw_value(dpm: DevPartMaster, key: str) -> Any:
    """원본 값 복구 — raw_json(원본 행 전체) 우선, 없으면 extra_fields(미매핑 잔여)."""
    if dpm.raw_json and key in dpm.raw_json:
        return dpm.raw_json[key]
    if dpm.extra_fields and key in dpm.extra_fields:
        return dpm.extra_fields[key]
    return None


def read_committed_run(
    run_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    """committed run의 (rows_df, files, ingestion_logs) 읽기.

    ``_quarantine_reason``이 채워진 행은 적재 대상이 아니므로 제외.
    """
    rows = pd.DataFrame()
    files: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []

    p_rows = run_dir / ROWS_FILE
    if p_rows.exists():
        rows = pd.read_parquet(p_rows)
        if "_quarantine_reason" in rows.columns:
            before = len(rows)
            rows = rows[rows["_quarantine_reason"].isna()].reset_index(drop=True)
            filtered = before - len(rows)
            if filtered:
                log.info("db.load.quarantine_filtered", rows=filtered)

    p_files = run_dir / FILES_JSON
    if p_files.exists():
        files = json.loads(p_files.read_text(encoding="utf-8"))

    p_logs = run_dir / INGEST_JSON
    if p_logs.exists():
        logs = json.loads(p_logs.read_text(encoding="utf-8"))

    return rows, files, logs


# --- Loader --------------------------------------------------------------


# dev_part_master 컬럼 화이트리스트 (parquet에는 source_file 등 부수 컬럼이 섞여있음).
_DPM_COLUMNS = {
    "region", "base_model", "new_model", "event", "bom_level_raw",
    "bom_depth", "part_type", "part_no_base", "part_no_new", "part_name",
    "qty_base", "qty_new", "change_point_raw", "change_reason_raw",
    "supplier", "classification",
}


def _build_dpm_row(
    row: pd.Series, file_id: int
) -> DevPartMaster:
    """parquet row → DevPartMaster ORM."""
    fields: dict[str, Any] = {}
    for col in _DPM_COLUMNS:
        if col in row.index and _present(row[col]):
            value = row[col]
            if col in {"bom_depth"}:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = None
            elif col in {"qty_new", "qty_base"}:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    value = None
            fields[col] = value

    extra = _coerce_json(row.get("extra_fields")) if "extra_fields" in row.index else None

    return DevPartMaster(
        file_id=file_id,
        form_id=row.get("form_id") if _present(row.get("form_id")) else None,
        sheet_name=row.get("source_sheet") if _present(row.get("source_sheet")) else None,
        source_row=int(row["source_row"]) if _present(row.get("source_row")) else None,
        extra_fields=extra,
        raw_json=build_raw_json(row),  # 원본 행 전체 스냅샷(감사·복구) — ms rawJson 차용
        embedding_text=row.get("embedding_text") if _present(row.get("embedding_text")) else None,
        **fields,
    )


def load_run(
    session: Session,
    run_dir: Path,
) -> LoadResult:
    """committed run 디렉토리 → source_files + ingestion_log + dev_part_master 적재.

    트랜잭션 1회. file_hash가 이미 있으면 source_files를 재사용하고 신규
    ingestion_log + dev_part_master만 추가 (재실행 시 ingestion_log를
    덧붙이는 패턴; 중복 적재가 의심되면 ``rollback_file(file_id)`` 후 재시도).
    """
    rows_df, files, logs = read_committed_run(run_dir)

    if not files:
        log.warning("db.load.empty_run", run_dir=str(run_dir))
        return LoadResult(rows_inserted={"source_files": 0, "ingestion_log": 0, "dev_part_master": 0})

    file_id_map: dict[str, int] = {}
    sf_inserted = 0
    for fm in files:
        existing = session.execute(
            select(SourceFile).where(SourceFile.file_hash == fm["file_hash"])
        ).scalar_one_or_none()
        if existing is None:
            sf = SourceFile(
                file_name=fm["file_name"],
                file_hash=fm["file_hash"],
                file_size=fm.get("file_size"),
                region=fm.get("region"),
            )
            session.add(sf)
            session.flush()
            file_id_map[fm["file_name"]] = sf.file_id
            sf_inserted += 1
        else:
            file_id_map[fm["file_name"]] = existing.file_id

    # 행 단위 멱등성(ms ON CONFLICT 차용) — 이미 적재된 (file_id, sheet_name, source_row)
    # 행은 재적재 시 스킵한다. 실 어댑터는 source_row를 행마다 고유 부여하므로 안전(비-파이프라인
    # master_import 스크래치만 비고유였고 그건 load_run을 안 탐). NULL 키(sheet/row 미설정)는
    # PG NULL-distinct 의미와 맞춰 스킵 대상에서 제외(항상 적재).
    loaded_file_ids = sorted(set(file_id_map.values()))
    seen_keys: set[tuple[int, str, int]] = set()
    if loaded_file_ids:
        for fid_, sheet_, srow_ in session.execute(
            select(DevPartMaster.file_id, DevPartMaster.sheet_name, DevPartMaster.source_row)
            .where(DevPartMaster.file_id.in_(loaded_file_ids))
            .where(DevPartMaster.sheet_name.isnot(None))
            .where(DevPartMaster.source_row.isnot(None))
        ).all():
            seen_keys.add((fid_, sheet_, srow_))

    # dpm 행 add/skip 분류 — ingestion_log에 rows_skipped를 적기 위해 로그 적재보다 먼저.
    dpm_to_add: list[DevPartMaster] = []
    skipped_by_sheet: dict[tuple[int, str | None], int] = {}
    if not rows_df.empty:
        for _, row in rows_df.iterrows():
            fid = file_id_map.get(row.get("source_file"))
            if fid is None:
                log.warning("db.load.row_orphan", source_file=row.get("source_file"))
                continue
            sheet = row.get("source_sheet") if _present(row.get("source_sheet")) else None
            srow = int(row["source_row"]) if _present(row.get("source_row")) else None
            key = (fid, sheet, srow) if sheet is not None and srow is not None else None
            if key is not None and key in seen_keys:
                skipped_by_sheet[(fid, sheet)] = skipped_by_sheet.get((fid, sheet), 0) + 1
                continue
            if key is not None:
                seen_keys.add(key)
            dpm_to_add.append(_build_dpm_row(row, fid))

    log_inserted = 0
    for le in logs:
        fid = file_id_map.get(le["file_name"])
        if fid is None:
            log.warning("db.load.log_orphan", file_name=le["file_name"])
            continue
        sheet_name = le.get("sheet_name", "")
        session.add(
            IngestionLog(
                file_id=fid,
                sheet_name=sheet_name,
                form_id=le.get("form_id", "unknown"),
                rows_total=le.get("rows_total"),
                rows_inserted=le.get("rows_inserted"),
                rows_skipped=skipped_by_sheet.get((fid, sheet_name)),
                status=le.get("status"),
                error_message=le.get("error_message"),
            )
        )
        log_inserted += 1

    for obj in dpm_to_add:
        session.add(obj)
    dpm_inserted = len(dpm_to_add)
    dpm_skipped = sum(skipped_by_sheet.values())

    session.commit()

    # 구조 A — BOM ag-grid 행(form_id='bom_ag_grid_36')의 부모-자식 → bom_edge 적재.
    # walk_subtree(하위 전개)의 대상 테이블. idempotent(기존 엣지 skip)·파괴 변경 0.
    # db→agent 모듈 역의존을 피해 지역 import.
    from src.agent.repository.backfill import backfill_bom_edges

    file_ids = sorted(set(file_id_map.values()))
    edges_inserted = 0
    for fid in file_ids:
        edges_inserted += backfill_bom_edges(session, file_id=fid)

    # 구조 B — dev_part_master 변경행 → change_event/change_line(사유별 묶음). search_events
    # (reason_embedding) 검색 인덱스의 코퍼스다. DB→DB 변환·idempotent. 임베딩(reason_embedding)은
    # ENABLE_EMBEDDING 게이트라 여기서 안 채우고 update_event_embeddings(`db load --embed`)로 분리.
    ev = load_change_events(session, file_ids=file_ids) if file_ids else None

    inserted = {
        "source_files": sf_inserted,
        "ingestion_log": log_inserted,
        "dev_part_master": dpm_inserted,
        "bom_edge": edges_inserted,
        "change_event": ev.events_inserted if ev else 0,
        "change_line": ev.lines_inserted if ev else 0,
    }
    log.info("db.load.done", inserted=inserted, dpm_skipped=dpm_skipped)
    return LoadResult(
        rows_inserted=inserted,
        file_ids=file_ids,
        rows_skipped={"dev_part_master": dpm_skipped},
    )


# --- Embeddings ---------------------------------------------------------


def update_embeddings(
    session: Session,
    file_ids: list[int] | None = None,
) -> int:
    """embedding_text가 있고 embedding_dense가 NULL인 행 일괄 임베딩.

    Args:
        session: 활성 SQLAlchemy session (postgres+pgvector).
        file_ids: 특정 파일만 임베딩. None이면 전체 backfill.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError("embedding disabled - set ENABLE_EMBEDDING=1 and run Ollama")
    if session.bind is None or session.bind.dialect.name != "postgresql":
        log.warning("db.embed.skip_non_pg", dialect=getattr(session.bind, "dialect", None))
        return 0

    from src.embed.embedder import embed_texts

    stmt = (
        select(DevPartMaster)
        .where(DevPartMaster.embedding_text.isnot(None))
        .where(DevPartMaster.embedding_dense.is_(None))
    )
    if file_ids:
        stmt = stmt.where(DevPartMaster.file_id.in_(file_ids))

    rows = session.execute(stmt).scalars().all()
    if not rows:
        return 0

    texts = [(r.embedding_text or "") for r in rows]
    vectors = embed_texts(texts)

    for row, vec in zip(rows, vectors, strict=True):
        if vec:
            row.embedding_dense = vec
    session.commit()
    log.info("db.embeddings.updated", rows=len(rows))
    return len(rows)
