"""Generate per-step visualizations for ``docs/walkthrough.md``.

Runs inventory, classify, and the full Step 4 pipeline against ``data/raw/``
and writes PNG charts into ``docs/images/`` for the walkthrough document.
Requires the optional ``viz`` extra::

    uv sync --extra viz
    uv run python scripts/build_walkthrough.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless render
import matplotlib.pyplot as plt
import pandas as pd

from src.preprocess.classify import classify_dir
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    DRY_RUN_ROOT,
    discover_raw_files,
    run_pipeline,
)
from src.preprocess.validate import THRESHOLDS
from src.utils.paths import QUARANTINE_DIR, RAW_DIR

IMAGE_DIR = Path("docs/images")
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# Short ASCII handles for each real file — used in chart labels because the
# matplotlib environment in this container has no CJK font.
FILE_LABELS = {
    "240430_BDO30_SKS_Transitional_MasterList.xlsx": "240430-Transitional",
    "BO24_B700_nonpyro_241120.xlsx": "BO24-B700-nonpyro",
    "BO24_Better_250424.xlsx": "BO24-Better",
    "LSIU6339XE.ARSLLGACVZ.EKHQ_1.0.xlsx": "LSIU6339XE",
    "WDEK9429S.ATTLSNACVZ.EKHQ_1.0.xlsx": "WDEK9429S",
}


def _label(name: str) -> str:
    return FILE_LABELS.get(name, name)


# --- Step 0: inventory ---------------------------------------------------


def viz_inventory(inv_df: pd.DataFrame) -> Path:
    """Per-sheet rows × max_col scatter, coloured by file."""
    fig, ax = plt.subplots(figsize=(9, 5))
    files = inv_df["file_name"].unique()
    cmap = plt.get_cmap("tab10")
    for index, file_name in enumerate(files):
        subset = inv_df[inv_df["file_name"] == file_name]
        ax.scatter(
            subset["max_col"],
            subset["max_row"],
            s=120,
            color=cmap(index % 10),
            label=_label(file_name),
            edgecolors="black",
            alpha=0.85,
        )
    ax.set_xscale("symlog")
    ax.set_yscale("symlog")
    ax.set_xlabel("max columns (log)")
    ax.set_ylabel("max rows (log)")
    ax.set_title("Step 0 — sheet inventory: rows vs columns per sheet")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()
    out = IMAGE_DIR / "step0_inventory.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# --- Step 1: classification ----------------------------------------------


def viz_classification(results: list) -> Path:
    """Heat-map of per-version score per file."""
    versions = ["20col", "56col", "96col", "v1_2", "bom_tree"]
    files = [_label(Path(r.file_path).name) for r in results]
    scores = [
        [r.evidence.get(v, 0.0) for v in versions] for r in results
    ]
    fig, ax = plt.subplots(figsize=(8, 4))
    im = ax.imshow(scores, cmap="YlGnBu", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(versions)))
    ax.set_xticklabels(versions, rotation=0)
    ax.set_yticks(range(len(files)))
    ax.set_yticklabels(files)
    ax.set_xlabel("form version")
    ax.set_title("Step 1 — classifier signal score per file (1.0 = full match)")
    for i in range(len(files)):
        for j in range(len(versions)):
            value = scores[i][j]
            label = f"{value:.2f}"
            color = "white" if value > 0.5 else "black"
            ax.text(j, i, label, ha="center", va="center", color=color, fontsize=9)
    # Annotate the winner with a star.
    for i, result in enumerate(results):
        if result.form_version in versions:
            j = versions.index(result.form_version)
            ax.add_patch(
                plt.Rectangle(  # type: ignore[attr-defined]
                    (j - 0.5, i - 0.5),
                    1,
                    1,
                    fill=False,
                    edgecolor="red",
                    lw=2,
                )
            )
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    out = IMAGE_DIR / "step1_classification.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# --- Step 3: field coverage + quarantine reasons -------------------------


REQUIRED_FIELDS = [
    "base_part_no",
    "new_part_no",
    "part_name",
    "bom_level",
    "part_type",
    "change_type",
    "change_point",
    "model_code",
]


def _load_processed(run_dir: Path) -> dict[str, pd.DataFrame]:
    """Return {original_file_name: processed_df} for every parquet in the run."""
    files_dir = run_dir / "files"
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(files_dir.glob("*.parquet")):
        df = pd.read_parquet(path)
        if "source_file" in df.columns and not df.empty:
            file_name = Path(str(df["source_file"].iloc[0])).name
            out[file_name] = df
        else:
            out[path.stem + ".xlsx"] = df
    return out


def viz_field_coverage(processed: dict[str, pd.DataFrame]) -> Path:
    """Per-file × per-field heatmap of non-null rate after mapping/normalize."""
    files = list(processed)
    fields = REQUIRED_FIELDS
    matrix: list[list[float]] = []
    for file_name in files:
        df = processed[file_name]
        if df.empty:
            matrix.append([0.0] * len(fields))
            continue
        row = []
        for field in fields:
            if field in df.columns and len(df):
                row.append(float(df[field].notna().mean()))
            else:
                row.append(0.0)
        matrix.append(row)
    fig, ax = plt.subplots(figsize=(9, 3.8))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(fields)))
    ax.set_xticklabels(fields, rotation=30, ha="right")
    ax.set_yticks(range(len(files)))
    ax.set_yticklabels([_label(f) for f in files])
    ax.set_title("Step 3 — field non-null coverage per file (after map + normalize)")
    for i in range(len(files)):
        for j in range(len(fields)):
            value = matrix[i][j]
            label = f"{int(value * 100)}%"
            color = "black" if value > 0.5 else "white"
            ax.text(j, i, label, ha="center", va="center", color=color, fontsize=8)
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    out = IMAGE_DIR / "step3_field_coverage.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def viz_quarantine_reasons(quarantine_dir: Path) -> Path:
    """Stacked bar of top quarantine stages per file."""
    files: list[str] = []
    stage_counts: dict[str, list[int]] = {}
    raw: dict[str, Counter] = {}
    for path in sorted(quarantine_dir.glob("*.parquet")):
        df = pd.read_parquet(path)
        counter: Counter = Counter()
        for reason in df["fail_reason"].dropna():
            for part in str(reason).split(";"):
                key = part.strip().split(":")[0]
                if key:
                    counter[key] += 1
        raw[path.stem + ".xlsx"] = counter

    top_stages = sorted(
        {stage for counter in raw.values() for stage in counter},
        key=lambda s: sum(counter[s] for counter in raw.values()),
        reverse=True,
    )[:8]
    for file_name, counter in raw.items():
        files.append(file_name)
        for stage in top_stages:
            stage_counts.setdefault(stage, []).append(counter.get(stage, 0))

    fig, ax = plt.subplots(figsize=(9, 4.5))
    bottom = [0] * len(files)
    cmap = plt.get_cmap("tab20")
    for index, stage in enumerate(top_stages):
        ax.bar(
            range(len(files)),
            stage_counts[stage],
            bottom=bottom,
            label=stage,
            color=cmap(index % 20),
        )
        bottom = [b + n for b, n in zip(bottom, stage_counts[stage])]
    ax.set_xticks(range(len(files)))
    ax.set_xticklabels([_label(f) for f in files], rotation=15, ha="right")
    ax.set_ylabel("quarantined rows (failures per stage)")
    ax.set_title("Step 4 — quarantine reasons per file (top stages)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    out = IMAGE_DIR / "step3_quarantine.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# --- Step 4: validation metrics ------------------------------------------


VALIDATION_METRICS = [
    ("column_match", "max", THRESHOLDS["column_match"]),
    ("type_match", "max", THRESHOLDS["type_match"]),
    ("value_format_match", "max", THRESHOLDS["value_format_match"]),
    ("referential_integrity", "max", THRESHOLDS["referential_integrity"]),
    ("row_preservation", "max", THRESHOLDS["row_preservation"]),
    ("null_rate_required", "min", THRESHOLDS["null_rate_required_max"]),
    ("axiom_violation_rate", "min", THRESHOLDS["axiom_violation_rate_max"]),
]


def viz_validation_metrics(state: dict[str, Any], processed: dict[str, pd.DataFrame]) -> Path:
    """Re-derive validate metrics per file from the processed parquets."""
    from src.preprocess.validate import validate_dataframe

    files = list(processed)
    reports = []
    for file_name in files:
        df = processed[file_name]
        report = validate_dataframe(df, run_id=state["run_id"], file_path=file_name)
        reports.append(report)

    # Add an aggregate column from state.
    metric_names = [name for name, _, _ in VALIDATION_METRICS]
    matrix: list[list[float]] = []
    for report in reports:
        matrix.append([getattr(report, name) for name in metric_names])

    fig, ax = plt.subplots(figsize=(9, 3.8))
    im = ax.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)
    ax.set_xticks(range(len(metric_names)))
    ax.set_xticklabels(metric_names, rotation=30, ha="right")
    ax.set_yticks(range(len(files)))
    ax.set_yticklabels([_label(f) for f in files])
    ax.set_title("Step 4 — validation metrics per file (green = passing)")
    for i in range(len(files)):
        for j, (name, direction, threshold) in enumerate(VALIDATION_METRICS):
            value = matrix[i][j]
            label = f"{value:.2f}"
            if direction == "max":
                passed = value >= threshold
            else:
                passed = value <= threshold
            marker = "OK" if passed else "X"
            ax.text(
                j,
                i,
                f"{label}\n{marker}",
                ha="center",
                va="center",
                color="black",
                fontsize=7,
            )
    fig.colorbar(im, ax=ax, fraction=0.04)
    fig.tight_layout()
    out = IMAGE_DIR / "step4_validation.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


# --- Main ----------------------------------------------------------------


def main() -> dict[str, Path]:
    artifacts: dict[str, Path] = {}

    # Step 0
    inv = build_inventory(RAW_DIR)
    inv.to_parquet(Path("data/interim/file_inventory.parquet"), index=False)
    artifacts["inventory"] = viz_inventory(inv)

    # Step 1
    classifications = classify_dir(RAW_DIR)
    artifacts["classification"] = viz_classification(classifications)

    # Step 3 / 4 — full pipeline
    files = discover_raw_files(RAW_DIR)
    result = run_pipeline(files, mode="dry-run")
    run_dir = DRY_RUN_ROOT / result.run_id
    processed = _load_processed(run_dir)
    artifacts["field_coverage"] = viz_field_coverage(processed)
    artifacts["quarantine"] = viz_quarantine_reasons(QUARANTINE_DIR / result.run_id)
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    artifacts["validation"] = viz_validation_metrics(state, processed)

    print(f"\nRun: {result.run_id}")
    print(f"Report: {result.report_path}")
    for name, path in artifacts.items():
        print(f"  {name}: {path}")
    return artifacts


if __name__ == "__main__":
    main()
