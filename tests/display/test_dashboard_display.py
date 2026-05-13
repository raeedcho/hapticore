"""Display-marker test for WorkspaceMirrorProcess lifecycle.

Requires PsychoPy and a display (or xvfb). Skipped unless the 'display'
marker is selected.
"""

from __future__ import annotations

import pytest

import multiprocessing
import time

import msgpack
import zmq

from hapticore.core.config import DashboardConfig, DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY, TOPIC_STATE
from hapticore.core.messaging import make_ipc_address

pytest.importorskip("psychopy")

@pytest.mark.display
class TestWorkspaceMirrorLifecycle:
    """Tests requiring PsychoPy and a display (or xvfb)."""

    def test_start_and_shutdown(self) -> None:
        """Start WorkspaceMirrorProcess(headless=True), verify clean shutdown."""
        from hapticore.dashboard.workspace_mirror import WorkspaceMirrorProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("wm_evt"),
            haptic_state_address=make_ipc_address("wm_state"),
            display_event_address=make_ipc_address("wm_disp"),
        )
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = WorkspaceMirrorProcess(
            dashboard_config=DashboardConfig(screen=0, resolution=(800, 600)),
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            ready_event=ready_event,
            headless=True,
        )
        proc.start()

        try:
            # Wait for process to signal readiness (ZMQ sockets created)
            assert ready_event.wait(timeout=10.0), "WorkspaceMirrorProcess did not become ready"
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=5.0)
            assert not proc.is_alive(), f"Process still alive (exit code: {proc.exitcode})"
            assert proc.exitcode == 0

    def test_survives_display_and_state_messages(self) -> None:
        """Send a display command and a haptic state; verify process does not crash."""
        from hapticore.dashboard.workspace_mirror import WorkspaceMirrorProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("wm_evt2"),
            haptic_state_address=make_ipc_address("wm_state2"),
            display_event_address=make_ipc_address("wm_disp2"),
        )
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = WorkspaceMirrorProcess(
            dashboard_config=DashboardConfig(screen=0, resolution=(800, 600)),
            display_config=DisplayConfig(),
            zmq_config=zmq_config,
            ready_event=ready_event,
            headless=True,
        )
        proc.start()

        ctx = zmq.Context()
        display_pub = ctx.socket(zmq.PUB)
        display_pub.setsockopt(zmq.LINGER, 0)
        display_pub.bind(zmq_config.event_pub_address)

        state_pub = ctx.socket(zmq.PUB)
        state_pub.setsockopt(zmq.LINGER, 0)
        state_pub.bind(zmq_config.haptic_state_address)

        try:
            assert ready_event.wait(timeout=10.0), "WorkspaceMirrorProcess did not become ready"
            time.sleep(0.2)  # slow-joiner grace

            # Send a display clear command
            display_pub.send_multipart([
                TOPIC_DISPLAY,
                msgpack.packb({"action": "clear"}, use_bin_type=True),
            ])

            # Send a haptic state message
            state_pub.send_multipart([
                TOPIC_STATE,
                msgpack.packb({
                    "timestamp": time.monotonic(),
                    "sequence": 1,
                    "position": [0.01, 0.02, 0.0],
                    "velocity": [0.0, 0.0, 0.0],
                    "force": [2.0, 1.0, 0.0],
                    "active_field": "null",
                    "field_state": {},
                }, use_bin_type=True),
            ])

            time.sleep(0.3)
            assert proc.is_alive()
        finally:
            display_pub.close()
            state_pub.close()
            ctx.term()
            proc.request_shutdown()
            proc.join(timeout=5.0)
            assert not proc.is_alive()
            assert proc.exitcode == 0
