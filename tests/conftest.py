"""Shared test fixtures for hapticore tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ipc_address(tmp_path: Path) -> str:
    """Generate a unique IPC address inside the pytest temp directory."""
    return f"ipc://{tmp_path}/hapticore_test"


@pytest.fixture
def event_address(tmp_path: Path) -> str:
    """Generate a unique IPC address for event pub-sub tests."""
    return f"ipc://{tmp_path}/hapticore_event"


@pytest.fixture
def command_address(tmp_path: Path) -> str:
    """Generate a unique IPC address for command tests."""
    return f"ipc://{tmp_path}/hapticore_cmd"
