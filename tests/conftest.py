"""Shared test fixtures for hapticore tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def ipc_address(tmp_path: object) -> str:
    """Generate a unique IPC address for test isolation."""
    import uuid

    return f"ipc:///tmp/hapticore_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def event_address(tmp_path: object) -> str:
    """Generate a unique IPC address for event pub-sub tests."""
    import uuid

    return f"ipc:///tmp/hapticore_event_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def command_address(tmp_path: object) -> str:
    """Generate a unique IPC address for command tests."""
    import uuid

    return f"ipc:///tmp/hapticore_cmd_{uuid.uuid4().hex[:8]}"
