"""Shared test fixtures for hapticore tests."""

from __future__ import annotations

import os
import platform

import pytest

from hapticore.core.messaging import make_ipc_address


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip display-marked tests on macOS CI (no xvfb, unreliable display context)."""
    if platform.system() == "Darwin" and os.environ.get("CI"):
        skip_display = pytest.mark.skip(reason="Display tests skipped on macOS CI")
        for item in items:
            if "display" in item.keywords:
                item.add_marker(skip_display)


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
