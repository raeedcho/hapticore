"""Display-marker test for StatusDashboardProcess lifecycle.

Requires PyQt6 and a display (or xvfb). Skipped unless the 'display'
marker is selected.
"""

from __future__ import annotations

import multiprocessing
import time

import msgpack
import pytest
import zmq

from hapticore.core.config import DashboardConfig, ZMQConfig
from hapticore.core.messages import TOPIC_EVENT
from hapticore.core.messaging import make_ipc_address
from hapticore.tasks.cup_task import CupTask

pytest.importorskip("PyQt6")


@pytest.mark.display
class TestStatusDashboardLifecycle:
    """Tests requiring PyQt6 and a display (or xvfb)."""

    def test_start_and_shutdown(self) -> None:
        """Start StatusDashboardProcess, verify clean shutdown."""
        from hapticore.dashboard.status_dashboard import StatusDashboardProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("sd_evt"),
            haptic_state_address=make_ipc_address("sd_state"),
            display_event_address=make_ipc_address("sd_disp"),
        )
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = StatusDashboardProcess(
            dashboard_config=DashboardConfig(screen=0, resolution=(800, 600)),
            zmq_config=zmq_config,
            task_states=CupTask.STATES,
            task_initial_state=CupTask.INITIAL_STATE,
            block_size=8,
            num_blocks=4,
            num_conditions=2,
            ready_event=ready_event,
        )
        proc.start()

        try:
            assert ready_event.wait(timeout=15.0), "StatusDashboardProcess did not become ready"
            assert proc.is_alive()
        finally:
            proc.request_shutdown()
            proc.join(timeout=5.0)
            assert not proc.is_alive(), f"Process still alive (exit code: {proc.exitcode})"
            assert proc.exitcode == 0

    def test_survives_event_messages(self) -> None:
        """Send StateTransition and TrialEvent; verify process does not crash."""
        from hapticore.dashboard.status_dashboard import StatusDashboardProcess

        zmq_config = ZMQConfig(
            event_pub_address=make_ipc_address("sd_evt2"),
            haptic_state_address=make_ipc_address("sd_state2"),
            display_event_address=make_ipc_address("sd_disp2"),
        )
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = StatusDashboardProcess(
            dashboard_config=DashboardConfig(screen=0, resolution=(800, 600)),
            zmq_config=zmq_config,
            task_states=CupTask.STATES,
            task_initial_state=CupTask.INITIAL_STATE,
            block_size=8,
            num_blocks=4,
            num_conditions=2,
            ready_event=ready_event,
        )
        proc.start()

        ctx = zmq.Context()
        event_pub = ctx.socket(zmq.PUB)
        event_pub.setsockopt(zmq.LINGER, 0)
        event_pub.bind(zmq_config.event_pub_address)

        try:
            assert ready_event.wait(timeout=15.0), "StatusDashboardProcess did not become ready"
            time.sleep(0.2)  # slow-joiner grace

            # Publish a StateTransition
            state_msg = {
                "__msg_type__": "StateTransition",
                "timestamp": time.monotonic(),
                "previous_state": "iti",
                "new_state": "move_to_left",
                "trigger": "trial_begin",
                "trial_number": 0,
                "event_code": 0,
            }
            event_pub.send_multipart([
                TOPIC_EVENT,
                msgpack.packb(state_msg, use_bin_type=True),
            ])

            # Publish a TrialEvent (trial_complete)
            trial_msg = {
                "__msg_type__": "TrialEvent",
                "timestamp": time.monotonic(),
                "event_name": "trial_complete",
                "event_code": 0,
                "trial_number": 0,
                "data": {
                    "outcome": "success",
                    "condition": {"target_id": 0},
                },
            }
            event_pub.send_multipart([
                TOPIC_EVENT,
                msgpack.packb(trial_msg, use_bin_type=True),
            ])

            time.sleep(0.3)
            assert proc.is_alive()
        finally:
            event_pub.close()
            ctx.term()
            proc.request_shutdown()
            proc.join(timeout=5.0)
            assert not proc.is_alive()
            assert proc.exitcode == 0
