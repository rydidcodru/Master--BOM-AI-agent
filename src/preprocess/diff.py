"""Step 4 — golden diff: auto pipeline output vs. user hand-work.

The user's hand-made preprocessing result is the ground truth (see
``data/golden/README.md``). For every raw file the auto pipeline processed,
``diff_against_golden`` joins on a primary key (default ``base_part_no``) and
counts row-level matches / mismatches plus per-column mismatch frequencies.
A handful of mismatching rows are surfaced as samples for the report.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

_SAMPLE_LIMIT = 10


class DiffReport(BaseModel):
    """Row-level comparison of auto output against golden ground truth."""

    rows_match: int = 0
    rows_mismatch: int = 0
    rows_only_in_auto: int = 0
    rows_only_in_golden: int = 0
    column_mismatches: dict[str, int] = Field(default_factory=dict)
    sample_mismatches: list[dict[str, object]] = Field(default_factory=list)
    key: str = "base_part_no"

    @property
    def match_rate(self) -> float:
        """Fraction of compared rows (key matched both sides) that match exactly."""
        total = self.rows_match + self.rows_mismatch
        return self.rows_match / total if total else 0.0


def _values_equal(a: object, b: object) -> bool:
    """Return True if two cell values are equal, with NaN/None tolerated.

    Numeric values are compared as floats when both sides cast cleanly (so
    ``1`` matches ``1.0`` and ``"2"`` matches ``2``). Otherwise comparison is
    over stripped string forms, so trailing-whitespace differences do not flip
    the result. NaN equals NaN here.
    """
    a_null = a is None or (isinstance(a, float) and math.isnan(a))
    b_null = b is None or (isinstance(b, float) and math.isnan(b))
    if a_null and b_null:
        return True
    if a_null or b_null:
        return False
    try:
        return float(a) == float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pass
    return str(a).strip() == str(b).strip()


def diff_against_golden(
    auto_df: pd.DataFrame,
    golden_df: pd.DataFrame,
    key: str = "base_part_no",
) -> DiffReport:
    """Compare automatic output against golden ground truth.

    Args:
        auto_df: The automatic pipeline output.
        golden_df: The user's hand-made result (ground truth).
        key: Join key for row alignment.

    Returns:
        A populated :class:`DiffReport`.
    """
    if key not in auto_df.columns or key not in golden_df.columns:
        raise KeyError(f"diff key {key!r} not in both DataFrames")

    merged = auto_df.merge(
        golden_df,
        on=key,
        how="outer",
        suffixes=("_auto", "_golden"),
        indicator=True,
    )

    rows_only_in_auto = int((merged["_merge"] == "left_only").sum())
    rows_only_in_golden = int((merged["_merge"] == "right_only").sum())

    both = merged[merged["_merge"] == "both"]
    compare_columns = [
        c for c in golden_df.columns if c != key and c in auto_df.columns
    ]
    column_mismatches: dict[str, int] = {c: 0 for c in compare_columns}
    samples: list[dict[str, object]] = []
    rows_match = 0
    rows_mismatch = 0

    for _, row in both.iterrows():
        diffs: dict[str, object] = {}
        for column in compare_columns:
            auto_value = row[f"{column}_auto"]
            golden_value = row[f"{column}_golden"]
            if not _values_equal(auto_value, golden_value):
                column_mismatches[column] += 1
                diffs[column] = {"auto": auto_value, "golden": golden_value}
        if diffs:
            rows_mismatch += 1
            if len(samples) < _SAMPLE_LIMIT:
                samples.append({key: row[key], **diffs})
        else:
            rows_match += 1

    return DiffReport(
        rows_match=rows_match,
        rows_mismatch=rows_mismatch,
        rows_only_in_auto=rows_only_in_auto,
        rows_only_in_golden=rows_only_in_golden,
        column_mismatches={c: n for c, n in column_mismatches.items() if n},
        sample_mismatches=samples,
        key=key,
    )


def load_golden(golden_dir: Path, source_file: Path) -> pd.DataFrame | None:
    """Return the golden DataFrame matching a raw source file, or None.

    Lookup tries ``<stem>.parquet`` then ``<stem>.xlsx`` under ``golden_dir``.

    Args:
        golden_dir: Path to ``data/golden/``.
        source_file: Raw file whose golden counterpart we want.

    Returns:
        A DataFrame or None when no golden file exists.
    """
    parquet = golden_dir / f"{source_file.stem}.parquet"
    if parquet.exists():
        return pd.read_parquet(parquet)
    excel = golden_dir / f"{source_file.stem}.xlsx"
    if excel.exists():
        return pd.read_excel(excel)
    return None
