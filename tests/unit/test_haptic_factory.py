"""Unit tests for make_haptic_interface."""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import zmq

from hapticore.core.config import DhdConfig, HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.core.messages import TOPIC_STATE
from hapticore.core.messaging import make_ipc_address
from hapticore.haptic import HapticClient, make_haptic_interface
from hapticore.haptic import _haptic_server_alive  # noqa: PLC2701 — testing internal probe directly
from hapticore.haptic.mock import MockHapticInterface
from hapticore.haptic.mouse_bridge import MouseBridge


class TestMakeHapticInterface:
    def test_mock_backend_returns_mock_interface(self) -> None:
        cfg = HapticConfig(backend="mock")
        with make_haptic_interface(cfg, ZMQConfig()) as iface:
            assert isinstance(iface, MockHapticInterface)
            assert isinstance(iface, HapticInterface)

    def test_dhd_backend_returns_connected_haptic_client(self) -> None:
        cfg = HapticConfig(backend="dhd")
        zmq_cfg = ZMQConfig()
        with patch("hapticore.haptic._haptic_server_alive", return_value=True):
            with make_haptic_interface(cfg, zmq_cfg) as iface:
                assert isinstance(iface, HapticClient)
                assert isinstance(iface, HapticInterface)
                assert iface._connected  # noqa: SLF001

    def test_dhd_backend_passes_nested_params_through(self) -> None:
        cfg = HapticConfig(
            backend="dhd",
            dhd=DhdConfig(heartbeat_interval_s=0.1, command_timeout_ms=500),
        )
        with patch("hapticore.haptic._haptic_server_alive", return_value=True):
            with make_haptic_interface(cfg, ZMQConfig()) as iface:
                assert isinstance(iface, HapticClient)
                assert iface._heartbeat_interval_s == 0.1  # noqa: SLF001
                assert iface._command_timeout_ms == 500    # noqa: SLF001

    def test_dhd_backend_uses_zmq_addresses(self) -> None:
        cfg = HapticConfig(backend="dhd")
        zmq_cfg = ZMQConfig(
            haptic_state_address="ipc:///tmp/test_state",
            haptic_command_address="ipc:///tmp/test_cmd",
        )
        with patch("hapticore.haptic._haptic_server_alive", return_value=True):
            with make_haptic_interface(cfg, zmq_cfg) as iface:
                assert isinstance(iface, HapticClient)
                assert iface._state_address == "ipc:///tmp/test_state"       # noqa: SLF001
                assert iface._command_address == "ipc:///tmp/test_cmd"       # noqa: SLF001

    def test_dhd_backend_with_external_context_does_not_own_it(self) -> None:
        cfg = HapticConfig(backend="dhd")
        ctx: zmq.Context[Any] = zmq.Context()
        try:
            with patch("hapticore.haptic._haptic_server_alive", return_value=True):
                with make_haptic_interface(cfg, ZMQConfig(), context=ctx) as iface:
                    assert isinstance(iface, HapticClient)
                    assert not iface._own_context  # noqa: SLF001
                    assert iface._context is ctx   # noqa: SLF001
        finally:
            ctx.term()

    def test_dhd_backend_without_context_owns_its_own(self) -> None:
        cfg = HapticConfig(backend="dhd")
        with patch("hapticore.haptic._haptic_server_alive", return_value=True):
            with make_haptic_interface(cfg, ZMQConfig()) as iface:
                assert isinstance(iface, HapticClient)
                assert iface._own_context  # noqa: SLF001

    def test_dhd_backend_starts_mouse_bridge_when_queue_provided(self) -> None:
        """dhd backend with mouse_input=True and mouse_queue → MouseBridge instantiated and started."""
        cfg = HapticConfig(backend="dhd", dhd=DhdConfig(mouse_input=True))
        zmq_cfg = ZMQConfig()
        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        mock_bridge = MagicMock(spec=MouseBridge)

        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True),
            patch("hapticore.haptic.MouseBridge", return_value=mock_bridge) as mock_cls,
        ):
            with make_haptic_interface(cfg, zmq_cfg, mouse_queue=queue):
                pass

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args
        assert call_kwargs.kwargs["mouse_queue"] is queue
        mock_bridge.start.assert_called_once()
        mock_bridge.request_stop.assert_called_once()

    def test_dhd_backend_no_bridge_when_mouse_input_false(self) -> None:
        """dhd backend with mouse_queue but mouse_input=False → no MouseBridge."""
        cfg = HapticConfig(backend="dhd", dhd=DhdConfig(mouse_input=False))
        zmq_cfg = ZMQConfig()
        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True),
            patch("hapticore.haptic.MouseBridge") as mock_cls,
        ):
            with make_haptic_interface(cfg, zmq_cfg, mouse_queue=queue):
                pass
        mock_cls.assert_not_called()

    def test_dhd_backend_mouse_input_true_without_queue_raises(self) -> None:
        """dhd backend with mouse_input=True but no queue → ValueError."""
        cfg = HapticConfig(backend="dhd", dhd=DhdConfig(mouse_input=True))
        zmq_cfg = ZMQConfig()
        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True),
            pytest.raises(ValueError, match="mouse_queue"),
        ):
            with make_haptic_interface(cfg, zmq_cfg, mouse_queue=None):
                pass

    def test_dhd_backend_no_bridge_without_queue(self) -> None:
        """dhd backend without mouse_queue → MouseBridge is NOT instantiated."""
        cfg = HapticConfig(backend="dhd", dhd=DhdConfig())
        zmq_cfg = ZMQConfig()

        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True),
            patch("hapticore.haptic.MouseBridge") as mock_cls,
        ):
            with make_haptic_interface(cfg, zmq_cfg, mouse_queue=None):
                pass

        mock_cls.assert_not_called()


class TestHapticServerProbe:
    """Tests for the _haptic_server_alive probe."""

    def test_probe_returns_true_when_server_publishing(self) -> None:
        """Bind a real PUB socket and publish TOPIC_STATE; probe should return True."""
        address = make_ipc_address("probe_test_alive")
        ctx: zmq.Context[Any] = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.bind(address)

        stop_event = threading.Event()

        def _publish() -> None:
            while not stop_event.is_set():
                try:
                    pub.send_multipart([TOPIC_STATE, b"x"], zmq.NOBLOCK)
                except zmq.Again:
                    pass
                time.sleep(0.02)

        t = threading.Thread(target=_publish, daemon=True)
        t.start()
        try:
            # Give the publisher a moment to bind and start
            time.sleep(0.1)
            result = _haptic_server_alive(address, timeout_s=1.0)
            assert result is True
        finally:
            stop_event.set()
            t.join(timeout=2.0)
            pub.close(linger=0)
            ctx.term()

    def test_probe_returns_false_when_socket_bound_but_no_publish(self) -> None:
        """Bind a PUB socket but never publish; probe should return False."""
        address = make_ipc_address("probe_test_silent")
        ctx: zmq.Context[Any] = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.bind(address)
        try:
            result = _haptic_server_alive(address, timeout_s=0.2)
            assert result is False
        finally:
            pub.close(linger=0)
            ctx.term()

    def test_probe_returns_false_when_no_socket(self) -> None:
        """No socket at the address; probe should return False."""
        address = make_ipc_address("probe_test_nosocket")
        result = _haptic_server_alive(address, timeout_s=0.2)
        assert result is False


class TestMakeHapticInterfaceDhdLifecycle:
    """Tests for the auto-start branch of the dhd backend."""

    def _make_dhd_cfg(self, **kwargs: Any) -> HapticConfig:
        return HapticConfig(backend="dhd", dhd=DhdConfig(**kwargs))

    def test_auto_start_true_probe_passes_no_spawn(self) -> None:
        """auto_start=True, probe returns True → no spawn, client connected."""
        cfg = self._make_dhd_cfg()
        zmq_cfg = ZMQConfig()
        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True) as mock_probe,
            patch("hapticore.haptic._spawn_haptic_server") as mock_spawn,
            patch("hapticore.haptic._terminate_server") as mock_terminate,
        ):
            with make_haptic_interface(cfg, zmq_cfg) as haptic:
                assert isinstance(haptic, HapticClient)
                assert haptic._connected  # noqa: SLF001
            mock_probe.assert_called_once()
            mock_spawn.assert_not_called()
            mock_terminate.assert_not_called()

    def test_auto_start_true_probe_fails_spawns_server(self) -> None:
        """auto_start=True, probe returns False → server spawned with correct args."""
        cfg = self._make_dhd_cfg(force_limit_n=15.0, publish_rate_hz=100.0)
        zmq_cfg = ZMQConfig(
            haptic_state_address="ipc:///tmp/test_spawn_state",
            haptic_command_address="ipc:///tmp/test_spawn_cmd",
        )
        fake_proc: MagicMock = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=False) as mock_probe,
            patch("hapticore.haptic._spawn_haptic_server", return_value=fake_proc) as mock_spawn,
            patch("hapticore.haptic._wait_for_server_ready") as mock_wait,
            patch("hapticore.haptic._terminate_server") as mock_terminate,
        ):
            with make_haptic_interface(cfg, zmq_cfg) as haptic:
                assert isinstance(haptic, HapticClient)
            mock_probe.assert_called_once()
            mock_spawn.assert_called_once()
            # Verify the spawn was called with the right config objects
            spawn_call_args = mock_spawn.call_args
            assert spawn_call_args[0][0] is cfg.dhd
            assert spawn_call_args[0][1] is zmq_cfg
            mock_wait.assert_called_once()
            mock_terminate.assert_called_once_with(fake_proc)

    def test_auto_start_true_spawn_then_probe_times_out(self) -> None:
        """auto_start=True, _wait_for_server_ready raises → terminate spawned proc, re-raise."""
        cfg = self._make_dhd_cfg()
        zmq_cfg = ZMQConfig()
        fake_proc: MagicMock = MagicMock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None

        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=False),
            patch("hapticore.haptic._spawn_haptic_server", return_value=fake_proc),
            patch(
                "hapticore.haptic._wait_for_server_ready",
                side_effect=RuntimeError("timed out"),
            ),
            patch("hapticore.haptic._terminate_server") as mock_terminate,
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                with make_haptic_interface(cfg, zmq_cfg):
                    pass
            mock_terminate.assert_called_once_with(fake_proc)

    def test_auto_start_false_probe_passes_no_spawn(self) -> None:
        """auto_start=False, probe returns True → attach, no spawn."""
        cfg = self._make_dhd_cfg(auto_start=False)
        zmq_cfg = ZMQConfig()
        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=True),
            patch("hapticore.haptic._spawn_haptic_server") as mock_spawn,
            patch("hapticore.haptic._terminate_server") as mock_terminate,
        ):
            with make_haptic_interface(cfg, zmq_cfg) as haptic:
                assert isinstance(haptic, HapticClient)
            mock_spawn.assert_not_called()
            mock_terminate.assert_not_called()

    def test_auto_start_false_probe_fails_raises_runtime_error(self) -> None:
        """auto_start=False, probe returns False → RuntimeError, no spawn."""
        cfg = self._make_dhd_cfg(auto_start=False)
        zmq_cfg = ZMQConfig()
        with (
            patch("hapticore.haptic._haptic_server_alive", return_value=False),
            patch("hapticore.haptic._spawn_haptic_server") as mock_spawn,
        ):
            with pytest.raises(RuntimeError, match="No haptic server detected"):
                with make_haptic_interface(cfg, zmq_cfg):
                    pass
            mock_spawn.assert_not_called()


class TestSpawnHapticServer:
    """Tests for _spawn_haptic_server helper."""

    @patch("hapticore.haptic.subprocess.Popen")
    def test_spawn_uses_new_session(self, mock_popen: MagicMock) -> None:
        """Spawned server must be in its own session so Ctrl+C doesn't reach it."""
        from hapticore.haptic import _spawn_haptic_server  # noqa: PLC2701
        cfg = DhdConfig(server_binary=Path("/some/existing/path"))
        with patch.object(Path, "exists", return_value=True):
            _spawn_haptic_server(cfg, ZMQConfig())
        assert mock_popen.call_args.kwargs["start_new_session"] is True

    @patch("hapticore.haptic.subprocess.Popen")
    def test_spawn_passes_die_with_parent(self, mock_popen: MagicMock) -> None:
        """Factory spawns must pass --die-with-parent so the C++ server sets
        PR_SET_PDEATHSIG; this is the hard-crash safety net when Python can't
        run _terminate_server. Manual launches omit the flag intentionally."""
        from hapticore.haptic import _spawn_haptic_server  # noqa: PLC2701
        cfg = DhdConfig(server_binary=Path("/some/existing/path"))
        with patch.object(Path, "exists", return_value=True):
            _spawn_haptic_server(cfg, ZMQConfig())
        spawned_args: list[str] = mock_popen.call_args.args[0]
        assert "--die-with-parent" in spawned_args

