"""D-012 Step 1-6 — 전처리 파이프라인 (dev_part_master 적재용).

6단계로 단순화 (이전 8단계):
  1. classify       (시트 단위 양식 분류, calamine 폴백)
  2. extract        (어댑터 dispatch → dev_part_master_fields)
  3. normalize      (NFC/NFKC 차등)
  4. narrativize    (결정론적 자연어, LLM 0회)
  5. validate       (7 핵심 지표)
  6. db.load        (commit 시 source_files + ingestion_log + dev_part_master)

제거됨:
  - Step 4 resolve (Entity Resolution) — dpm는 단일 테이블, parts/models 없음.
  - 별도 Step embed — db.load 안에서 update_embeddings로 처리.

filesystem lifecycle (dry_run/<run_id> → committed/<run_id> → rolled_back/...)
은 그대로 유지. DB 측 batch handle은 file_id (Phase 5).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from src.preprocess.adapters import ExtractedRow, extract_sheet
from src.preprocess.classify import classify_file
from src.preprocess.column_dict import load_column_dictionary
from src.preprocess.diff import DiffReport, diff_against_golden, load_golden
from src.preprocess.narrativize import build_narrative
from src.preprocess.normalize import normalize_dpm_row
from src.preprocess.quarantine import (
    extract_quarantined,
    list_quarantined,
    save_quarantine,
)
from src.preprocess.report import build_markdown_report
from src.preprocess.validate import ValidationReport, validate_dataframe
from src.utils.excel import read_workbook
from src.utils.logging import get_logger
from src.utils.paths import GOLDEN_DIR, PROCESSED_DIR, REPORTS_DIR

log = get_logger(__name__)

DRY_RUN_ROOT = PROCESSED_DIR / "dry_run"
COMMITTED_ROOT = PROCESSED_DIR / "committed"
ROLLED_BACK_ROOT = PROCESSED_DIR / "rolled_back"

RunMode = Literal["dry-run", "commit"]
RunStatus = Literal["dry_run_complete", "committed", "rolled_back", "rejected"]


# --- Result types --------------------------------------------------------


@dataclass
class FileMeta:
    """source_files 적재용 메타."""

    file_path: str
    file_name: str
    file_hash: str
    file_size: int
    region: str | None = None


@dataclass
class LogEntry:
    """ingestion_log 적재용 항목."""

    file_name: str  # link via file_id_map at load time
    sheet_name: str
    form_id: str
    rows_total: int = 0
    rows_inserted: int = 0
    status: str = "ok"
    error_message: str | None = None


@dataclass
class FileResult:
    """파일 1개 처리 결과 (state.json 용)."""

    file_path: str
    file_name: str
    status: str  # 'ok' | 'empty' | 'error' | 'partial'
    form_ids: list[str] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    quarantine_count: int = 0
    error: str | None = None


@dataclass
class RunResult:
    """run 결과."""

    run_id: str
    status: RunStatus
    mode: RunMode
    files: list[FileMeta] = field(default_factory=list)
    file_results: list[FileResult] = field(default_factory=list)
    aggregate_validation: ValidationReport | None = None
    file_validations: list[tuple[str, ValidationReport, DiffReport | None]] = field(
        default_factory=list
    )
    report_path: Path | None = None
    run_dir: Path | None = None

    @property
    def rows_in(self) -> int:
        return sum(r.rows_in for r in self.file_results)

    @property
    def rows_out(self) -> int:
        return sum(r.rows_out for r in self.file_results)

    @property
    def quarantine_count(self) -> int:
        return sum(r.quarantine_count for r in self.file_results)


def generate_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


# --- File metadata --------------------------------------------------------


_REGION_FROM_NAME = re.compile(
    r"(북유럽|모리셔스|사우디|UAE|중동|남미|러시아|중국|일본|미국|호주|"
    r"Europe|Mauritius|Saudi|North Europe|UAE|Russia|China|Japan|USA|Australia)",
    re.IGNORECASE,
)


def _file_region_hint(file_name: str) -> str | None:
    """파일명에서 region 추측 (best-effort, 없으면 None)."""
    m = _REGION_FROM_NAME.search(file_name)
    return m.group(0) if m else None


def compute_file_meta(path: Path) -> FileMeta:
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return FileMeta(
        file_path=str(path),
        file_name=path.name,
        file_hash=h,
        file_size=path.stat().st_size,
        region=_file_region_hint(path.name),
    )


# --- Per-row processing ---------------------------------------------------


_NUMERIC_DPM_FIELDS = {"qty_new", "qty_base", "bom_depth"}
_NULL_LIKE = frozenset({"-", "–", "—", "", "n/a", "na", "null", "none", "nan"})


def _coerce_numeric(value: Any) -> Any:
    """qty/bom_depth 셀에 문자열 '-' 같은 null-token이나 비-숫자가 섞이면 None.

    parquet의 컬럼 dtype 강제(float / Int64) 위해 행 단위로 미리 처리.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if not s or s.lower() in _NULL_LIKE:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _process_extracted_row(er: ExtractedRow, file_name: str) -> dict[str, Any]:
    """ExtractedRow → DataFrame-ready record (정규화 + narrative 포함).

    환경변수 ``SKIP_VALIDATION=1``이면 axiom post_validate / quarantine을 모두
    우회 — 어댑터가 뽑은 raw value 그대로 DB까지 흘려보냄. 추출 품질을 직접
    검토하기 위한 디버그 모드.
    """
    dpm_norm, fails = normalize_dpm_row(er.dev_part_master_fields)

    # qty/bom_depth 숫자 캐스트 — 실데이터에서 '-', 빈 문자열 등 섞임.
    for k in _NUMERIC_DPM_FIELDS:
        if k in dpm_norm:
            dpm_norm[k] = _coerce_numeric(dpm_norm[k])
    if "bom_depth" in dpm_norm and dpm_norm["bom_depth"] is not None:
        try:
            dpm_norm["bom_depth"] = int(dpm_norm["bom_depth"])
        except (TypeError, ValueError):
            dpm_norm["bom_depth"] = None

    if os.environ.get("SKIP_VALIDATION", "0") == "1":
        quarantine_reason = None
    else:
        quarantine_reason = "; ".join(f"{k}={v}" for k, v in fails.items()) or None

    narrative = build_narrative(dpm_norm, er.extra_fields)

    return {
        **dpm_norm,
        "extra_fields": er.extra_fields,
        "embedding_text": narrative,
        "form_id": er.source_meta.get("form_id", ""),
        "source_file": er.source_meta.get("source_file", file_name),
        "source_sheet": er.source_meta.get("source_sheet", ""),
        "source_row": er.source_meta.get("source_row", 0),
        "_quarantine_reason": quarantine_reason,
    }


def preprocess_file(
    path: Path, run_id: str
) -> tuple[FileResult, FileMeta, list[LogEntry], list[dict[str, Any]]]:
    """파일 1개 → (FileResult, FileMeta, ingestion_logs, dev_part_records)."""
    file_meta = compute_file_meta(path)
    records: list[dict[str, Any]] = []
    logs: list[LogEntry] = []
    form_ids: set[str] = set()
    total_in = total_out = total_quar = 0

    try:
        classification, sheet_classes = classify_file(path)
    except Exception as exc:  # noqa: BLE001
        logs.append(
            LogEntry(
                file_name=path.name,
                sheet_name="",
                form_id="unknown",
                status="error",
                error_message=f"classify: {exc}",
            )
        )
        return (
            FileResult(file_path=str(path), file_name=path.name, status="error", error=str(exc)),
            file_meta,
            logs,
            [],
        )

    if classification.error:
        logs.append(
            LogEntry(
                file_name=path.name,
                sheet_name="",
                form_id="unknown",
                status="error",
                error_message=classification.error,
            )
        )
        return (
            FileResult(
                file_path=str(path),
                file_name=path.name,
                status="error",
                error=classification.error,
            ),
            file_meta,
            logs,
            [],
        )

    try:
        sheets = read_workbook(path)
    except Exception as exc:  # noqa: BLE001
        logs.append(
            LogEntry(
                file_name=path.name,
                sheet_name="",
                form_id="unknown",
                status="error",
                error_message=f"read_workbook: {exc}",
            )
        )
        return (
            FileResult(
                file_path=str(path),
                file_name=path.name,
                status="error",
                error=f"read_workbook: {exc}",
            ),
            file_meta,
            logs,
            [],
        )

    sheet_idx = {s.name: s for s in sheets}
    for sc in sheet_classes:
        if sc.form_id == "unknown":
            logs.append(
                LogEntry(
                    file_name=path.name,
                    sheet_name=sc.sheet_name,
                    form_id="unknown",
                    status="skipped",
                    error_message="no matching form signature",
                )
            )
            continue
        sheet = sheet_idx.get(sc.sheet_name)
        if sheet is None:
            continue

        try:
            extracted: list[ExtractedRow] = extract_sheet(
                path, sheet, sc.form_id, {"run_id": run_id}
            )
        except Exception as exc:  # noqa: BLE001
            logs.append(
                LogEntry(
                    file_name=path.name,
                    sheet_name=sc.sheet_name,
                    form_id=sc.form_id,
                    status="error",
                    error_message=f"extract: {exc}",
                )
            )
            continue

        rows_in = len(extracted)
        quar = 0
        for er in extracted:
            record = _process_extracted_row(er, path.name)
            records.append(record)
            if record.get("_quarantine_reason"):
                quar += 1
        rows_out = rows_in - quar

        form_ids.add(sc.form_id)
        total_in += rows_in
        total_out += rows_out
        total_quar += quar
        logs.append(
            LogEntry(
                file_name=path.name,
                sheet_name=sc.sheet_name,
                form_id=sc.form_id,
                rows_total=rows_in,
                rows_inserted=rows_out,
                status="ok" if rows_in else "empty",
            )
        )

    status = "ok" if records else "empty"
    file_result = FileResult(
        file_path=str(path),
        file_name=path.name,
        status=status,
        form_ids=sorted(form_ids),
        rows_in=total_in,
        rows_out=total_out,
        quarantine_count=total_quar,
    )
    log.info(
        "pipeline.file_done",
        file=path.name,
        rows_in=total_in,
        rows_out=total_out,
        quarantine=total_quar,
        forms=sorted(form_ids),
    )
    return file_result, file_meta, logs, records


# --- Run lifecycle -------------------------------------------------------


def _write_state(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_dir / "state.json"
    path.write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    return path


def read_state(run_id: str) -> dict[str, Any] | None:
    for root in (DRY_RUN_ROOT, COMMITTED_ROOT, ROLLED_BACK_ROOT):
        path = root / run_id / "state.json"
        if path.exists():
            return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    return None


def discover_raw_files(directory: Path) -> list[Path]:
    suffixes = {".xlsx", ".xlsm", ".xls"}
    return sorted(p for p in directory.rglob("*") if p.suffix.lower() in suffixes)


def run_pipeline(
    files: list[Path],
    mode: RunMode = "dry-run",
    run_id: str | None = None,
) -> RunResult:
    """전체 파이프라인 1회 run.

    files를 처리해 dry_run/<run_id>/에 저장. mode='commit'이면 committed/로
    승격 (validation gate 통과 시).
    """
    run = run_id or generate_run_id()
    run_dir = DRY_RUN_ROOT / run
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("pipeline.run.start", run_id=run, mode=mode, files=len(files))

    file_metas: list[FileMeta] = []
    file_results: list[FileResult] = []
    all_logs: list[LogEntry] = []
    all_records: list[dict[str, Any]] = []

    for path in files:
        fr, fm, logs, records = preprocess_file(path, run)
        file_results.append(fr)
        file_metas.append(fm)
        all_logs.extend(logs)
        all_records.extend(records)

    # persist — extra_fields를 JSON 직렬화 (parquet 호환).
    rows_df = pd.DataFrame(all_records) if all_records else pd.DataFrame()
    if not rows_df.empty:
        # type_match 측정용: bom_depth nullable Int64로 캐스트 (mixed None+int 회피).
        if "bom_depth" in rows_df.columns:
            rows_df["bom_depth"] = rows_df["bom_depth"].astype("Int64")
        if "extra_fields" in rows_df.columns:
            rows_df["extra_fields"] = rows_df["extra_fields"].apply(
                lambda v: json.dumps(v, ensure_ascii=False, default=str)
                if v is not None
                else None
            )
        rows_df.to_parquet(run_dir / "rows.parquet", index=False)

        if "_quarantine_reason" in rows_df.columns:
            quarantined = rows_df[rows_df["_quarantine_reason"].notna()]
            if not quarantined.empty:
                for source_file, group in quarantined.groupby("source_file"):
                    q_records = extract_quarantined(group, run, str(source_file))
                    save_quarantine(q_records, run, Path(str(source_file)).stem)

    # files.json — Phase 5 load.py 가 source_files 적재에 활용.
    (run_dir / "files.json").write_text(
        json.dumps([fm.__dict__ for fm in file_metas], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # ingestion_log.json
    (run_dir / "ingestion_log.json").write_text(
        json.dumps([le.__dict__ for le in all_logs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # validate
    aggregate = validate_dataframe(
        rows_df,
        run_id=run,
        rows_in=sum(r.rows_in for r in file_results) or len(rows_df),
    )

    # golden diff (per file, opt-in)
    file_validations: list[tuple[str, ValidationReport, DiffReport | None]] = []
    for fr in file_results:
        file_df = (
            rows_df[rows_df["source_file"] == Path(fr.file_path).name]
            if not rows_df.empty
            else pd.DataFrame()
        )
        v = validate_dataframe(
            file_df,
            run_id=run,
            file_path=fr.file_path,
            form_version=",".join(fr.form_ids),
            rows_in=fr.rows_in,
        )
        diff_report: DiffReport | None = None
        golden = load_golden(GOLDEN_DIR, Path(fr.file_path))
        if golden is not None and not file_df.empty:
            diff_report = diff_against_golden(file_df, golden)
        file_validations.append((fr.file_path, v, diff_report))

    report_path = build_markdown_report(
        run_id=run,
        file_reports=file_validations,
        aggregate=aggregate,
        output_dir=REPORTS_DIR,
    )
    shutil.copy(report_path, run_dir / "report.md")

    acceptable = aggregate.is_acceptable() if aggregate else False
    status: RunStatus = "dry_run_complete"
    if mode == "commit":
        if acceptable:
            status = "committed"
            COMMITTED_ROOT.mkdir(parents=True, exist_ok=True)
            target = COMMITTED_ROOT / run
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(run_dir, target)
        else:
            status = "rejected"

    aggregate_dict: dict[str, Any] | None = None
    if aggregate:
        aggregate_dict = aggregate.model_dump()
        aggregate_dict["critical_failures"] = aggregate.critical_failures()
        aggregate_dict["is_acceptable"] = aggregate.is_acceptable()

    _write_state(
        run_dir,
        {
            "run_id": run,
            "status": status,
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "files": [fr.__dict__ for fr in file_results],
            "rows_in": sum(r.rows_in for r in file_results),
            "rows_out": sum(r.rows_out for r in file_results),
            "quarantine_count": sum(r.quarantine_count for r in file_results),
            "aggregate_validation": aggregate_dict,
            "report_path": str(report_path),
        },
    )

    log.info(
        "pipeline.run.done",
        run_id=run,
        status=status,
        records=len(all_records),
        files=len(file_metas),
    )

    return RunResult(
        run_id=run,
        status=status,
        mode=mode,
        files=file_metas,
        file_results=file_results,
        aggregate_validation=aggregate,
        file_validations=file_validations,
        report_path=report_path,
        run_dir=run_dir,
    )


# --- commit / rollback (filesystem lifecycle) ---------------------------


def _resolve_critical_failures(agg: dict[str, Any] | None) -> list[str]:
    if not agg or not isinstance(agg, dict):
        return []
    if "critical_failures" in agg:
        return list(agg.get("critical_failures") or [])
    try:
        report = ValidationReport(
            **{k: v for k, v in agg.items() if k in ValidationReport.model_fields}
        )
        return report.critical_failures()
    except Exception:  # noqa: BLE001
        return []


def commit_run(run_id: str) -> Path:
    """dry_run/<run_id> → committed/<run_id> (validation gate 통과 시)."""
    src = DRY_RUN_ROOT / run_id
    if not src.exists():
        raise FileNotFoundError(f"no dry_run for {run_id}")
    state = read_state(run_id) or {}
    failing = _resolve_critical_failures(state.get("aggregate_validation"))
    if failing:
        raise ValueError(f"commit blocked - failing metrics: {failing}")
    target = COMMITTED_ROOT / run_id
    COMMITTED_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    state["status"] = "committed"
    state["committed_at"] = datetime.now(timezone.utc).isoformat()
    (target / "state.json").write_text(
        json.dumps(state, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    return target


def rollback_run(run_id: str) -> Path:
    """committed/<run_id> → rolled_back/<run_id> (filesystem-level)."""
    src = COMMITTED_ROOT / run_id
    if not src.exists():
        raise FileNotFoundError(f"no committed run for {run_id}")
    target = ROLLED_BACK_ROOT / run_id
    ROLLED_BACK_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    state = read_state(run_id) or {}
    state["status"] = "rolled_back"
    state["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    (target / "state.json").write_text(
        json.dumps(state, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    return target


def reprocess_quarantine(run_id: str) -> dict[str, Any]:
    records = list_quarantined(run_id)
    if not records:
        return {"records": 0, "new_run_id": None, "now_pass": 0, "still_fail": 0}
    return {
        "records": len(records),
        "new_run_id": None,
        "now_pass": 0,
        "still_fail": len(records),
    }
