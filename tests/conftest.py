"""Shared test fixtures for hapticore tests."""

from __future__ import annotations

import os

import pytest

from hapticore.core.messaging import make_ipc_address


@pytest.fixture(autouse=True)
def _clean_hapticore_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent stale HAPTICORE_ env vars from leaking into tests."""
    for key in list(os.environ):
        if key.startswith("HAPTICORE_"):
            monkeypatch.delenv(key)


@pytest.fixture
def ipc_address() -> str:
    """Generate a unique IPC address suitable for Unix-like platforms (macOS/Linux)."""
    return make_ipc_address("test")


@pytest.fixture
def event_address() -> str:
    """Generate a unique IPC address for event pub-sub tests."""
    return make_ipc_address("evt")


@pytest.fixture
def command_address() -> str:
    """Generate a unique IPC address for command tests."""
    return make_ipc_address("cmd")
