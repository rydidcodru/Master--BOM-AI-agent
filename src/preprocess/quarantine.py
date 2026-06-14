"""Step 4 — quarantine: separate failing rows for human review.

Rows the upstream pipeline could not normalize cleanly (any non-null
``_quarantine_reason``) are pulled out into a per-run, per-file parquet so
they can be inspected, fixed, or reprocessed without losing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from src.utils.logging import get_logger
from src.utils.paths import QUARANTINE_DIR

log = get_logger(__name__)

Severity = Literal["error", "warning"]


@dataclass
class QuarantineRecord:
    """One quarantined row with provenance and a fail-reason."""

    row_index: int
    source_file: str
    source_sheet: str | None
    raw_row: dict[str, object]
    stage_failed: str
    fail_reason: str
    severity: Severity
    run_id: str
    quarantined_at: str


def _severity_for(reason: str) -> Severity:
    """Classify a quarantine reason into ``error`` (required-empty etc.) or
    ``warning`` (post_validate / out-of-range)."""
    if "required empty" in reason or "no source column" in reason:
        return "error"
    return "warning"


def _first_failed_stage(reason: str) -> str:
    """Extract the first ``stage`` label from a semicolon-joined reason."""
    first = reason.split(";", 1)[0].strip()
    return first.split(":", 1)[0] if ":" in first else first


def extract_quarantined(
    df: pd.DataFrame, run_id: str, source_file: str
) -> list[QuarantineRecord]:
    """Return the quarantined rows of ``df`` as records.

    Args:
        df: A processed DataFrame with a ``_quarantine_reason`` column.
        run_id: Batch identifier.
        source_file: Origin file path (recorded on each record).

    Returns:
        One :class:`QuarantineRecord` per failing row. Empty if the column is
        absent or no row failed.
    """
    if "_quarantine_reason" not in df.columns:
        return []
    failed_mask = df["_quarantine_reason"].notna()
    if not failed_mask.any():
        return []

    records: list[QuarantineRecord] = []
    now = datetime.now(timezone.utc).isoformat()
    for index, row in df[failed_mask].iterrows():
        reason = str(row["_quarantine_reason"])
        sheet = row.get("_source_sheet") if "_source_sheet" in df.columns else None
        records.append(
            QuarantineRecord(
                row_index=int(index),
                source_file=source_file,
                source_sheet=str(sheet) if sheet is not None else None,
                raw_row={k: v for k, v in row.to_dict().items() if not k.startswith("_")},
                stage_failed=_first_failed_stage(reason),
                fail_reason=reason,
                severity=_severity_for(reason),
                run_id=run_id,
                quarantined_at=now,
            )
        )
    return records


def save_quarantine(
    records: list[QuarantineRecord], run_id: str, file_stem: str
) -> Path | None:
    """Persist quarantine records to ``data/quarantine/{run_id}/{file}.parquet``.

    Args:
        records: Records to persist.
        run_id: Batch identifier (used in the path).
        file_stem: Source-file basename without suffix.

    Returns:
        The output path, or None when there are no records to save.
    """
    if not records:
        return None
    out_dir = QUARANTINE_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "row_index": r.row_index,
            "source_file": r.source_file,
            "source_sheet": r.source_sheet,
            "raw_row": r.raw_row,
            "stage_failed": r.stage_failed,
            "fail_reason": r.fail_reason,
            "severity": r.severity,
            "run_id": r.run_id,
            "quarantined_at": r.quarantined_at,
        }
        for r in records
    ]
    out_path = out_dir / f"{file_stem}.parquet"
    pd.DataFrame(rows).to_parquet(out_path, index=False)
    log.info("quarantine.saved", path=str(out_path), records=len(records))
    return out_path


def list_quarantined(run_id: str) -> list[dict[str, object]]:
    """Load every quarantine record persisted under a given run.

    Args:
        run_id: Batch identifier.

    Returns:
        A list of dict rows (possibly empty).
    """
    run_dir = QUARANTINE_DIR / run_id
    if not run_dir.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(run_dir.glob("*.parquet")):
        rows.extend(pd.read_parquet(path).to_dict(orient="records"))
    return rows
