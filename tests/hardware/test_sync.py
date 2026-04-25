"""Hardware smoke test for SyncProcess with a real Teensy.

Prerequisites:
    1. A Teensy 4.1 is connected via USB.
    2. The Teensy firmware from Phase 5A.5 is flashed.
    3. Run with: pytest tests/hardware/ -m hardware -v -k sync

This is a stub for 5A.4. Flesh out in 5A.6 once hardware is on the bench.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.hardware


@pytest.mark.skip(reason="Hardware smoke test — fleshed out in Phase 5A.6")
def test_sync_process_starts_with_real_serial() -> None:
    """SyncProcess starts, connects to real Teensy, shuts down cleanly."""
    import time

    from hapticore.core.config import SyncConfig, ZMQConfig
    from hapticore.core.messaging import make_ipc_address
    from hapticore.sync.sync_process import SyncProcess

    sync_cfg = SyncConfig(backend="teensy")
    zmq_cfg = ZMQConfig(event_pub_address=make_ipc_address("hw_sync"))

    proc = SyncProcess(sync_cfg, zmq_cfg)
    proc.start()
    time.sleep(0.5)
    proc.request_shutdown()
    proc.join(timeout=3)
    assert not proc.is_alive()
