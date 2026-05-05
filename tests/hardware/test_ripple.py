"""Hardware smoke test for RippleProcess with a real Ripple Scout.

Run with: pytest tests/hardware/ -m hardware -v -k ripple
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.hardware


@pytest.mark.skip(reason="Hardware smoke test — flesh out during rig bringup")
def test_ripple_process_connects_to_real_scout() -> None:
    """RippleProcess starts, connects to real Scout, shuts down cleanly."""
    ...
