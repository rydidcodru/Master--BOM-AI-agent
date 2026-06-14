"""Step 4 — markdown report generation.

A single ``build_markdown_report`` renders every per-file validation report,
golden-diff result, and quarantine summary into one human-reviewable
``data/reports/{run_id}.md``. The report ends with an explicit accept/reject
verdict that drives the dry-run -> commit gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.preprocess.diff import DiffReport
from src.preprocess.validate import THRESHOLDS, ValidationReport
from src.utils.paths import REPORTS_DIR


def _checkmark(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _format_metrics_table(report: ValidationReport) -> str:
    rows = [
        # D-011: referential_integrity 등 제거된 지표 빠짐.
        ("column_match", report.column_match, THRESHOLDS["column_match"], ">="),
        ("type_match", report.type_match, THRESHOLDS["type_match"], ">="),
        ("value_format_match", report.value_format_match, THRESHOLDS["value_format_match"], ">="),
        ("row_preservation", report.row_preservation, THRESHOLDS["row_preservation"], ">="),
        ("null_rate_required", report.null_rate_required, THRESHOLDS["null_rate_required_max"], "<="),
        ("axiom_violation_rate", report.axiom_violation_rate, THRESHOLDS["axiom_violation_rate_max"], "<="),
    ]
    lines = [
        "| Metric | Value | Threshold | Status |",
        "|---|---:|---:|:---:|",
    ]
    for name, value, threshold, op in rows:
        passed = value >= threshold if op == ">=" else value <= threshold
        lines.append(
            f"| {name} | {value:.3f} | {op} {threshold} | {_checkmark(passed)} |"
        )
    return "\n".join(lines)


def _format_file_section(
    source_path: str,
    validation: ValidationReport,
    diff: DiffReport | None,
) -> str:
    lines = [f"### {Path(source_path).name}"]
    lines.append(
        f"- form_version: `{validation.form_version}`  "
        f"rows_in={validation.rows_in}  rows_out={validation.rows_out}  "
        f"quarantined={validation.rows_quarantined}"
    )
    lines.append("")
    lines.append(_format_metrics_table(validation))
    if validation.drop_reasons:
        lines.append("")
        lines.append("**Drop reasons:**")
        for reason, count in sorted(
            validation.drop_reasons.items(), key=lambda x: -x[1]
        ):
            lines.append(f"- `{reason}`: {count}")
    if diff is not None:
        lines.append("")
        lines.append(
            f"**Golden diff:** match_rate={diff.match_rate:.3f}  "
            f"match={diff.rows_match}  mismatch={diff.rows_mismatch}  "
            f"only_auto={diff.rows_only_in_auto}  "
            f"only_golden={diff.rows_only_in_golden}"
        )
        if diff.column_mismatches:
            lines.append("")
            lines.append("Top column mismatches:")
            top = sorted(
                diff.column_mismatches.items(), key=lambda x: -x[1]
            )[:5]
            for column, count in top:
                lines.append(f"- `{column}`: {count}")
    return "\n".join(lines)


def build_markdown_report(
    run_id: str,
    file_reports: list[tuple[str, ValidationReport, DiffReport | None]],
    aggregate: ValidationReport,
    output_dir: Path = REPORTS_DIR,
) -> Path:
    """Render and save the markdown report for one run.

    Args:
        run_id: Batch identifier.
        file_reports: Triples of (source_file_path, validation, optional diff).
        aggregate: Aggregate validation across all files.
        output_dir: Directory to write into. Created if missing.

    Returns:
        The path of the written report.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    decision = "ACCEPTABLE" if aggregate.is_acceptable() else "NOT ACCEPTABLE"
    failures = aggregate.critical_failures()

    lines: list[str] = []
    lines.append(f"# Preprocessing Report — {run_id}")
    lines.append(f"Generated: {timestamp}")
    lines.append("")
    lines.append("## Summary")
    lines.append(
        f"- files: {len(file_reports)}  rows_in: {aggregate.rows_in}  "
        f"rows_out: {aggregate.rows_out}  quarantined: {aggregate.rows_quarantined}"
    )
    lines.append("")
    lines.append("## Aggregate Validation")
    lines.append(_format_metrics_table(aggregate))
    lines.append("")
    lines.append("## Per-file")
    for source_path, validation, diff in file_reports:
        lines.append(_format_file_section(source_path, validation, diff))
        lines.append("")
    lines.append("## Decision")
    lines.append(f"**{decision}** - gate based on the 6 acceptance thresholds (D-011 후).")
    if failures:
        lines.append("")
        lines.append("Failing metrics:")
        for name in failures:
            lines.append(f"- `{name}`")
    if aggregate.is_acceptable():
        lines.append("")
        lines.append(f"Next: `python -m src.cli pipeline commit --run-id {run_id}`")
    else:
        lines.append("")
        lines.append(
            "Next: review the failing metrics above and fix the rules / data "
            "before commit."
        )

    output_path = output_dir / f"{run_id}.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
