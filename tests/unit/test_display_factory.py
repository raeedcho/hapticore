"""Unit tests for make_display_interface."""

from __future__ import annotations

import multiprocessing
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import zmq

from hapticore.backends import make_display_interface
from hapticore.backends.mock import MockDisplay
from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.interfaces import DisplayInterface
from hapticore.core.messaging import EventPublisher


@pytest.fixture
def publisher() -> Iterator[EventPublisher]:
    """A real EventPublisher bound to a unique ipc address."""
    from hapticore.core.messaging import make_ipc_address
    ctx = zmq.Context()
    pub = EventPublisher(ctx, make_ipc_address("test_disp"))
    yield pub
    pub.close()
    ctx.term()


class TestMakeDisplayInterface:
    def test_mock_backend_yields_mock_display(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = DisplayConfig(backend="mock")
        with make_display_interface(
            cfg, ZMQConfig(), publisher=publisher,
        ) as display:
            assert isinstance(display, MockDisplay)
            assert isinstance(display, DisplayInterface)

    def test_mock_backend_does_not_spawn_subprocess(
        self, publisher: EventPublisher,
    ) -> None:
        """Mock backend must not import or instantiate DisplayProcess."""
        cfg = DisplayConfig(backend="mock")
        with patch("hapticore.display.process.DisplayProcess") as mock_proc:
            with make_display_interface(
                cfg, ZMQConfig(), publisher=publisher,
            ):
                pass
            mock_proc.assert_not_called()

    def test_psychopy_backend_spawns_and_shuts_down_display_process(
        self, publisher: EventPublisher,
    ) -> None:
        """psychopy backend must start, yield, then shut down DisplayProcess."""
        cfg = DisplayConfig(backend="psychopy")
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False  # clean shutdown after join

        with patch(
            "hapticore.display.process.DisplayProcess", return_value=fake_proc,
        ), patch("hapticore.backends.display.time.sleep"):  # skip startup wait
            with make_display_interface(
                cfg, ZMQConfig(), publisher=publisher,
            ) as display:
                from hapticore.display.display_client import DisplayClient
                assert isinstance(display, DisplayClient)
                assert isinstance(display, DisplayInterface)
                fake_proc.start.assert_called_once()
                fake_proc.request_shutdown.assert_not_called()  # not yet

        # After context exit, shutdown ran.
        fake_proc.request_shutdown.assert_called_once()
        fake_proc.join.assert_called()
        fake_proc.terminate.assert_not_called()  # is_alive() returned False

    def test_psychopy_backend_terminates_if_join_times_out(
        self, publisher: EventPublisher,
    ) -> None:
        """If DisplayProcess doesn't exit cleanly, factory calls terminate()."""
        cfg = DisplayConfig(backend="psychopy")
        fake_proc = MagicMock()
        # Stays alive after first join, dies after terminate+join.
        fake_proc.is_alive.side_effect = [True, False]

        with patch(
            "hapticore.display.process.DisplayProcess", return_value=fake_proc,
        ), patch("hapticore.backends.display.time.sleep"):
            with make_display_interface(
                cfg, ZMQConfig(), publisher=publisher,
            ):
                pass

        fake_proc.terminate.assert_called_once()
        assert fake_proc.join.call_count == 2  # once after request_shutdown, once after terminate

    def test_psychopy_backend_passes_mouse_queue_through(
        self, publisher: EventPublisher,
    ) -> None:
        cfg = DisplayConfig(backend="psychopy")
        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        fake_proc = MagicMock()
        fake_proc.is_alive.return_value = False
        captured_kwargs: dict[str, Any] = {}

        def capture(*args: Any, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return fake_proc

        with patch(
            "hapticore.display.process.DisplayProcess", side_effect=capture,
        ), patch("hapticore.backends.display.time.sleep"):
            with make_display_interface(
                cfg, ZMQConfig(), publisher=publisher, mouse_queue=queue,
            ):
                pass

        assert captured_kwargs.get("mouse_queue") is queue
        assert captured_kwargs.get("headless") is False

    def test_invalid_backend_raises(
        self, publisher: EventPublisher,
    ) -> None:
        # Bypass Pydantic validation by constructing with model_construct.
        cfg = DisplayConfig.model_construct(backend="weird")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Unknown display backend"):
            with make_display_interface(
                cfg, ZMQConfig(), publisher=publisher,
            ):
                pass

    def test_psychopy_backend_startup_failure_does_not_leak(
        self, publisher: EventPublisher,
    ) -> None:
        """If proc.start() raises, no terminate is attempted on the unstarted process."""
        cfg = DisplayConfig(backend="psychopy")
        fake_proc = MagicMock()
        fake_proc.start.side_effect = OSError("simulated startup failure")

        with patch(
            "hapticore.display.process.DisplayProcess", return_value=fake_proc,
        ):
            with pytest.raises(OSError, match="simulated startup failure"):
                with make_display_interface(
                    cfg, ZMQConfig(), publisher=publisher,
                ):
                    pass

        # Cleanup branch must NOT have called join/terminate on the
        # unstarted process — those would raise AssertionError and mask
        # the original OSError.
        fake_proc.request_shutdown.assert_not_called()
        fake_proc.join.assert_not_called()
        fake_proc.terminate.assert_not_called()
