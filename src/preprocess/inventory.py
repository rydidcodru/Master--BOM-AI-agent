"""Step 0 — raw-file inventory scanner.

Recursively scans a directory of Excel files and records per-file / per-sheet
metadata into a parquet inventory. Model / date / grade hints are extracted
heuristically from file and sheet names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import typer

from src.utils.excel import read_workbook
from src.utils.logging import get_logger
from src.utils.paths import INTERIM_DIR, RAW_DIR

log = get_logger(__name__)

_EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}

# Heuristic patterns for name-based hints.
_DATE_PATTERN = re.compile(r"(20\d{2})[._\-]?(\d{1,2})[._\-]?(\d{1,2})?")
_MODEL_PATTERN = re.compile(r"[A-Z]{2,5}\d{4,5}[A-Z]?")
_GRADE_PATTERN = re.compile(r"(Best|Better|Good)[\-\s]?\d", re.IGNORECASE)
_REGION_PATTERN = re.compile(r"\b(EUR|EUE|EAP|SJ|SA)\b")


@dataclass
class SheetInfo:
    """Metadata for a single worksheet."""

    name: str
    max_row: int
    max_col: int
    model_hint: str | None = None
    grade_hint: str | None = None
    region_hint: str | None = None


@dataclass
class FileInfo:
    """Metadata for a single Excel file."""

    path: str
    name: str
    size_bytes: int
    mtime: str
    sheet_count: int
    date_hint: str | None = None
    model_hint: str | None = None
    sheets: list[SheetInfo] = field(default_factory=list)


def _extract_name_hints(text: str) -> dict[str, str | None]:
    """Extract model / grade / region / date hints from a name string.

    Args:
        text: File or sheet name.

    Returns:
        Mapping with optional ``model``, ``grade``, ``region``, ``date`` keys.
    """
    hints: dict[str, str | None] = {
        "model": None,
        "grade": None,
        "region": None,
        "date": None,
    }
    if (m := _MODEL_PATTERN.search(text)):
        hints["model"] = m.group(0)
    if (g := _GRADE_PATTERN.search(text)):
        hints["grade"] = g.group(0)
    if (r := _REGION_PATTERN.search(text)):
        hints["region"] = r.group(0)
    if (d := _DATE_PATTERN.search(text)):
        year, month, day = d.group(1), d.group(2), d.group(3)
        hints["date"] = f"{year}-{int(month):02d}" + (
            f"-{int(day):02d}" if day else ""
        )
    return hints


def scan_file(path: Path) -> FileInfo:
    """Scan a single Excel file for inventory metadata.

    Args:
        path: Path to the Excel file.

    Returns:
        A populated :class:`FileInfo`.
    """
    stat = path.stat()
    file_hints = _extract_name_hints(path.name)
    info = FileInfo(
        path=str(path),
        name=path.name,
        size_bytes=stat.st_size,
        mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        sheet_count=0,
        date_hint=file_hints["date"],
        model_hint=file_hints["model"],
    )
    for sheet in read_workbook(path):
        sheet_hints = _extract_name_hints(sheet.name)
        info.sheets.append(
            SheetInfo(
                name=sheet.name,
                max_row=sheet.max_row,
                max_col=sheet.max_col,
                model_hint=sheet_hints["model"],
                grade_hint=sheet_hints["grade"],
                region_hint=sheet_hints["region"],
            )
        )
    info.sheet_count = len(info.sheets)
    return info


def guess_form_version(info: FileInfo) -> str:
    """Rough form-version guess from sheet shape (pre-classifier heuristic).

    A precise classifier lives in ``src.extract.form_classifier``; this is only
    a coarse inventory-time hint.

    Args:
        info: Scanned file info.

    Returns:
        One of ``"v1.2"``, ``"96col"``, ``"56col"``, ``"20col"``, ``"unknown"``.
    """
    max_cols = max((s.max_col for s in info.sheets), default=0)
    has_history = any(s.name.strip().lower() == "history" for s in info.sheets)
    if has_history and 45 <= max_cols <= 75:
        return "v1.2"
    if max_cols >= 80:
        return "96col"
    if 45 <= max_cols < 80:
        return "56col"
    if 0 < max_cols < 45:
        return "20col"
    return "unknown"


def build_inventory(raw_dir: Path) -> pd.DataFrame:
    """Scan ``raw_dir`` recursively and build a flat per-sheet inventory.

    Files that fail to open are recorded as a data error and skipped — a single
    broken file never aborts the scan.

    Args:
        raw_dir: Directory to scan.

    Returns:
        A DataFrame with one row per sheet.
    """
    rows: list[dict[str, object]] = []
    errors = 0
    for path in sorted(raw_dir.rglob("*")):
        if path.suffix.lower() not in _EXCEL_SUFFIXES:
            continue
        try:
            info = scan_file(path)
        except Exception as exc:  # noqa: BLE001 — data error, record & continue
            errors += 1
            log.warning("inventory.scan_failed", file=str(path), error=str(exc))
            continue
        form_guess = guess_form_version(info)
        for sheet in info.sheets:
            rows.append(
                {
                    "file_path": info.path,
                    "file_name": info.name,
                    "size_bytes": info.size_bytes,
                    "mtime": info.mtime,
                    "sheet_count": info.sheet_count,
                    "date_hint": info.date_hint,
                    "file_model_hint": info.model_hint,
                    "form_version_guess": form_guess,
                    "sheet_name": sheet.name,
                    "max_row": sheet.max_row,
                    "max_col": sheet.max_col,
                    "sheet_model_hint": sheet.model_hint,
                    "sheet_grade_hint": sheet.grade_hint,
                    "sheet_region_hint": sheet.region_hint,
                }
            )
    log.info("inventory.scanned", sheets=len(rows), errors=errors)
    return pd.DataFrame(rows)


app = typer.Typer(help="Raw-file inventory scanner.")


@app.command()
def run(
    raw_dir: Path = typer.Option(RAW_DIR, help="Directory of raw Excel files."),
    output: Path = typer.Option(
        INTERIM_DIR / "file_inventory.parquet", help="Output parquet path."
    ),
) -> None:
    """Scan raw files and write the inventory parquet, printing a summary."""
    df = build_inventory(raw_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)

    if df.empty:
        typer.echo(f"No Excel files found under {raw_dir}.")
        return

    typer.echo(f"Inventory written to {output} ({len(df)} sheet rows).")
    typer.echo("\nForm-version guess distribution (per file):")
    per_file = df.drop_duplicates("file_path")["form_version_guess"]
    for version, count in per_file.value_counts().items():
        typer.echo(f"  {version}: {count}")
    typer.echo("\nHead:")
    typer.echo(df.head(20).to_string())


if __name__ == "__main__":
    app()
