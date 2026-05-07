"""Unit tests for make_sync_interface."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import zmq

from hapticore.core.config import SyncConfig, ZMQConfig
from hapticore.core.interfaces import SyncInterface
from hapticore.core.messaging import EventPublisher
from hapticore.sync import MockSync, TeensySync, make_sync_interface


@pytest.fixture
def publisher() -> Iterator[EventPublisher]:
    """A real EventPublisher bound to a unique ipc address."""
    from hapticore.core.messaging import make_ipc_address
    ctx = zmq.Context()
    pub = EventPublisher(ctx, make_ipc_address("test_sync_factory"))
    yield pub
    pub.close()
    ctx.term()


class TestMakeSyncInterface:
    def test_mock_backend_yields_mock_sync(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="mock")
        with make_sync_interface(cfg, ZMQConfig(), publisher=publisher) as sync:
            assert isinstance(sync, MockSync)
            assert isinstance(sync, SyncInterface)

    def test_mock_backend_does_not_import_sync_process(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="mock")
        with patch("hapticore.sync.sync_process.SyncProcess") as mock_proc:
            with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                pass
            mock_proc.assert_not_called()

    def test_teensy_backend_starts_and_shuts_down_sync_process(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False

        captured_kwargs: dict[str, Any] = {}

        def capture_and_ready(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            # Simulate the process setting the ready event on start.
            ready = kwargs.get("ready_event")

            def start_and_set_ready() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set_ready
            return fake_proc

        with patch(
            "hapticore.sync.sync_process.SyncProcess",
            side_effect=capture_and_ready,
        ):
            with make_sync_interface(
                cfg, ZMQConfig(), publisher=publisher,
            ) as sync:
                assert isinstance(sync, TeensySync)
                assert isinstance(sync, SyncInterface)
                fake_proc.start.assert_called_once()
                fake_proc.request_shutdown.assert_not_called()

        fake_proc.request_shutdown.assert_called_once()
        fake_proc.join.assert_called()
        fake_proc.terminate.assert_not_called()

    def test_teensy_backend_terminates_if_join_times_out(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()
        # Stays alive after first join, dies after terminate+join.
        fake_proc.is_alive.side_effect = [True, False]

        def capture_and_ready(*args: Any, **kwargs: Any) -> MagicMock:
            ready = kwargs.get("ready_event")

            def start_and_set_ready() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set_ready
            return fake_proc

        with patch(
            "hapticore.sync.sync_process.SyncProcess",
            side_effect=capture_and_ready,
        ):
            with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                pass

        fake_proc.terminate.assert_called_once()
        assert fake_proc.join.call_count == 2

    def test_teensy_backend_raises_if_process_dies_during_startup(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()
        # Process is dead and ready event is never set.
        fake_proc.is_alive.return_value = False
        fake_proc.exitcode = 1

        with patch(
            "hapticore.sync.sync_process.SyncProcess", return_value=fake_proc,
        ):
            with pytest.raises(RuntimeError, match="died during startup"):
                with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                    pass

    def test_teensy_backend_raises_if_ready_timeout_expires(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()
        # Process is alive but ready event is never set.
        fake_proc.is_alive.return_value = True

        with patch(
            "hapticore.sync.sync_process.SyncProcess", return_value=fake_proc,
        ), patch("hapticore.sync.factory._SYNC_READY_TIMEOUT_S", 0.1):
            with pytest.raises(RuntimeError, match="did not become ready"):
                with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                    pass

    def test_teensy_backend_startup_failure_does_not_leak(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()
        fake_proc.start.side_effect = OSError("simulated startup failure")

        with patch(
            "hapticore.sync.sync_process.SyncProcess", return_value=fake_proc,
        ):
            with pytest.raises(OSError, match="simulated startup failure"):
                with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                    pass

        fake_proc.request_shutdown.assert_not_called()
        fake_proc.join.assert_not_called()
        fake_proc.terminate.assert_not_called()

    def test_teensy_backend_passes_ready_event_to_process(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig(backend="teensy")
        fake_proc = MagicMock()

        captured_kwargs: dict[str, Any] = {}

        def capture_and_ready(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            ready = kwargs.get("ready_event")

            def start_and_set_ready() -> None:
                if ready is not None:
                    ready.set()

            fake_proc.start.side_effect = start_and_set_ready
            fake_proc.is_alive.return_value = False
            return fake_proc

        with patch(
            "hapticore.sync.sync_process.SyncProcess",
            side_effect=capture_and_ready,
        ):
            with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                pass

        assert "ready_event" in captured_kwargs
        ready = captured_kwargs["ready_event"]
        assert hasattr(ready, "set") and hasattr(ready, "wait") and hasattr(ready, "is_set")

    def test_invalid_backend_raises(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = SyncConfig.model_construct(backend="weird")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Unknown sync backend"):
            with make_sync_interface(cfg, ZMQConfig(), publisher=publisher):
                pass
