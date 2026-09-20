"""Test fixtures: isolate every test in a throwaway data directory."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_DATA_DIR = Path(tempfile.mkdtemp(prefix="paul-tests-"))
# Must be set before ``app.config`` is imported, since DATA_DIR is read there.
os.environ["PAUL_DATA_DIR"] = str(_DATA_DIR)


@pytest.fixture(autouse=True)
def clean_data_dir():
    """Start each test from an empty data directory and no running job."""
    from app import background
    from app.ranking import jobs
    from app.writer import jobs as writer_jobs

    jobs.reset()
    writer_jobs.reset()
    background.reset()
    for child in _DATA_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    yield
    jobs.reset()
    writer_jobs.reset()
    background.reset()


@pytest.fixture(scope="session", autouse=True)
def _remove_tmp_data_dir():
    yield
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
