"""Tests for DisplayProcess import safety, subclass checks, and drain logic."""

from __future__ import annotations

import multiprocessing
import time
from unittest.mock import MagicMock

import msgpack
import zmq

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import make_ipc_address


class TestImportSafety:
    """Verify that importing the display package does not require PsychoPy."""

    def test_import_display_package(self) -> None:
        """import hapticore.display succeeds without PsychoPy installed."""
        import hapticore.display  # noqa: F401

    def test_import_display_client(self) -> None:
        import hapticore.display.display_client  # noqa: F401

    def test_import_display_process(self) -> None:
        import hapticore.display.process  # noqa: F401


class TestDisplayProcessSubclass:
    """Verify DisplayProcess is a multiprocessing.Process subclass."""

    def test_is_process_subclass(self) -> None:
        from hapticore.display.process import DisplayProcess

        assert issubclass(DisplayProcess, multiprocessing.Process)

    def test_instantiation(self) -> None:
        from hapticore.display.process import DisplayProcess

        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
            headless=True,
        )
        assert proc.name == "DisplayProcess"
        assert proc.daemon is True


class TestRequestShutdown:
    """Verify request_shutdown sets the internal event."""

    def test_sets_shutdown_event(self) -> None:
        from hapticore.display.process import DisplayProcess

        proc = DisplayProcess(
            display_config=DisplayConfig(),
            zmq_config=ZMQConfig(),
        )
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()


class TestDrainMessages:
    """Verify _drain_messages returns all pending messages without blocking."""

    def test_empty_socket_returns_empty_list(self) -> None:
        from hapticore.display.process import DisplayProcess

        addr = make_ipc_address("drain")
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(addr)

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(addr)
        sub.subscribe(TOPIC_DISPLAY)
        time.sleep(0.1)

        result = DisplayProcess._drain_messages(sub)
        assert result == []

        sub.close()
        pub.close()
        ctx.term()

    def test_drains_all_pending_messages(self) -> None:
        from hapticore.display.process import DisplayProcess

        addr = make_ipc_address("drain")
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(addr)

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(addr)
        sub.subscribe(TOPIC_DISPLAY)
        time.sleep(0.1)

        # Publish 3 messages
        for i in range(3):
            payload = msgpack.packb({"action": "clear", "index": i}, use_bin_type=True)
            pub.send_multipart([TOPIC_DISPLAY, payload])

        time.sleep(0.05)

        result = DisplayProcess._drain_messages(sub)
        assert len(result) == 3
        assert result[0]["index"] == 0
        assert result[1]["index"] == 1
        assert result[2]["index"] == 2

        # Socket should be empty now
        result2 = DisplayProcess._drain_messages(sub)
        assert result2 == []

        sub.close()
        pub.close()
        ctx.term()


class TestPhotodiodeInFrameLoop:
    """Verify _handle_display_command returns stim_id on show, None otherwise.

    The frame loop uses the return value to decide whether to trigger
    the photodiode: ``if shown_stim_ids and photodiode is not None``.
    """

    def test_show_returns_stim_id(self) -> None:
        """'show' command returns stim_id, which causes photodiode.trigger()."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "show", "stim_id": "target", "params": {"type": "circle"}}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result == "target"

    def test_hide_returns_none(self) -> None:
        """'hide' command returns None — photodiode should not trigger."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "hide", "stim_id": "target"}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result is None

    def test_clear_returns_none(self) -> None:
        """'clear' command returns None — photodiode should not trigger."""
        from hapticore.display.process import DisplayProcess

        scene = MagicMock()
        cmd = {"action": "clear"}
        result = DisplayProcess._handle_display_command(scene, cmd)
        assert result is None
