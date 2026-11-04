"""Shared pytest configuration for the AMCA backend test suite."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_default_project_base() -> None:
    """Keep auto-created project folders out of the real user's Documents.

    A new Studio Project gets a folder automatically when the request does not
    name one. That default is ``Documents/<Project Name>`` in production, so
    without this the test suite would litter the developer's Documents with
    directories for throwaway project names.
    """
    if os.environ.get("AMCA_DEFAULT_PROJECT_BASE"):
        return
    with tempfile.TemporaryDirectory(prefix="amca-project-base-") as directory:
        os.environ["AMCA_DEFAULT_PROJECT_BASE"] = str(Path(directory) / "Documents")
        yield
