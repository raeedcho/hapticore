"""Lifecycle tests for DisplayProcess requiring PsychoPy.

These tests are guarded by ``@pytest.mark.display`` and live in their own
directory so ``pixi run test-unit`` never collects them.
"""

from __future__ import annotations

import pytest
pytest.importorskip("psychopy")

import time

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import make_ipc_address


@pytest.mark.display
class TestDisplayProcessLifecycle:
    """Tests requiring PsychoPy and a display (or xvfb).

    These tests are skipped unless the 'display' marker is selected
    and PsychoPy is available.
    """

    def test_start_and_shutdown(self) -> None:
        """Start DisplayProcess(headless=True), verify it shuts down within 2s."""
        from hapticore.display.process import DisplayProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("dp_evt"),
            haptic_state_address=make_ipc_address("dp_state"),
        )
        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()
        time.sleep(0.5)
        assert proc.is_alive()

        proc.request_shutdown()
        proc.join(timeout=2.0)
        assert not proc.is_alive()

    def test_survives_display_commands(self) -> None:
        """Send 5 display commands; verify process does not crash."""
        from hapticore.display.process import DisplayProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("dp_evt"),
            haptic_state_address=make_ipc_address("dp_state"),
        )

        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(zmq_config.event_pub_address)
        time.sleep(0.5)

        for i in range(5):
            payload = msgpack.packb(
                {"action": "show", "stim_id": f"s{i}", "params": {}},
                use_bin_type=True,
            )
            pub.send_multipart([TOPIC_DISPLAY, payload])

        time.sleep(0.5)
        assert proc.is_alive()

        proc.request_shutdown()
        proc.join(timeout=2.0)
        assert not proc.is_alive()

        pub.close()
        ctx.term()
