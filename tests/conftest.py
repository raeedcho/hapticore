"""Shared test fixtures for hapticore tests."""

from __future__ import annotations

import pytest

from hapticore.core.messaging import make_ipc_address


@pytest.fixture
def ipc_address() -> str:
    """Generate a unique IPC address safe on all platforms."""
    return make_ipc_address("test")


@pytest.fixture
def event_address() -> str:
    """Generate a unique IPC address for event pub-sub tests."""
    return make_ipc_address("evt")


@pytest.fixture
def command_address() -> str:
    """Generate a unique IPC address for command tests."""
    return make_ipc_address("cmd")
