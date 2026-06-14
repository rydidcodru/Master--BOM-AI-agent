"""Canonical project paths (v2.0)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Data layers ----------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
GOLDEN_DIR = DATA_DIR / "golden"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
QUARANTINE_DIR = DATA_DIR / "quarantine"
REPORTS_DIR = DATA_DIR / "reports"
EVAL_DIR = DATA_DIR / "eval"

# --- Config ---------------------------------------------------------------
CONFIG_DIR = PROJECT_ROOT / "config"
FORM_SIGNATURES_PATH = CONFIG_DIR / "form_signatures.yaml"
COLUMN_DICTIONARY_PATH = CONFIG_DIR / "column_dictionary.yaml"
NORMALIZATION_PATH = CONFIG_DIR / "normalization.yaml"
AXIOMS_PATH = CONFIG_DIR / "axioms.yaml"
NARRATIVIZE_TEMPLATES_PATH = CONFIG_DIR / "narrativize_templates.yaml"

# --- Ontology -------------------------------------------------------------
ONTOLOGY_DIR = PROJECT_ROOT / "src" / "ontology"
