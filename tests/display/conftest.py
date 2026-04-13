"""Shared fixtures for display tests requiring PsychoPy."""

from __future__ import annotations

import platform

import pytest


@pytest.fixture(autouse=True)
def _flip_after_test(request: pytest.FixtureRequest) -> object:
    """Pump macOS event loop between tests to prevent window stalls."""
    yield
    if platform.system() != "Darwin":
        return
    if "win" in request.fixturenames:
        win = request.getfixturevalue("win")
        try:
            win.flip()
        except Exception:
            pass
