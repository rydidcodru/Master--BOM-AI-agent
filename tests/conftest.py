"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.fixtures import make_fixtures


@pytest.fixture(scope="session")
def fixture_workbooks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate synthetic Excel fixtures into an isolated temp directory.

    Returns:
        Path to the directory containing the generated workbooks.
    """
    raw_dir = tmp_path_factory.mktemp("raw")
    for path in make_fixtures.make_all():
        shutil.copy(path, raw_dir / path.name)
        path.unlink()
    return raw_dir
