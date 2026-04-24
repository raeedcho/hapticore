"""Unit tests for make_haptic_interface."""

from __future__ import annotations

import multiprocessing
import multiprocessing.queues
from typing import Any

import pytest
import zmq

from hapticore.core.config import DhdConfig, HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.hardware import HapticClient, make_haptic_interface
from hapticore.hardware.mock import MockHapticInterface
from hapticore.hardware.mouse_haptic import MouseHapticInterface


class TestMakeHapticInterface:
    def test_mock_kind_returns_mock_interface(self) -> None:
        cfg = HapticConfig(kind="mock")
        iface = make_haptic_interface(cfg, ZMQConfig())
        assert isinstance(iface, MockHapticInterface)
        assert isinstance(iface, HapticInterface)

    def test_mouse_kind_returns_mouse_interface(self) -> None:
        cfg = HapticConfig(kind="mouse")
        queue: multiprocessing.queues.Queue[tuple[float, float]] = (
            multiprocessing.Queue(maxsize=4)
        )
        iface = make_haptic_interface(cfg, ZMQConfig(), mouse_queue=queue)
        assert isinstance(iface, MouseHapticInterface)
        assert isinstance(iface, HapticInterface)

    def test_mouse_kind_without_queue_raises(self) -> None:
        cfg = HapticConfig(kind="mouse")
        with pytest.raises(ValueError, match="mouse_queue"):
            make_haptic_interface(cfg, ZMQConfig())

    def test_dhd_kind_returns_unconnected_haptic_client(self) -> None:
        cfg = HapticConfig(kind="dhd")
        zmq_cfg = ZMQConfig()
        iface = make_haptic_interface(cfg, zmq_cfg)
        assert isinstance(iface, HapticClient)
        assert isinstance(iface, HapticInterface)
        # Factory must NOT connect; caller owns lifecycle.
        assert not iface._connected  # noqa: SLF001

    def test_dhd_kind_passes_nested_params_through(self) -> None:
        cfg = HapticConfig(
            kind="dhd",
            dhd=DhdConfig(heartbeat_interval_s=0.1, command_timeout_ms=500),
        )
        iface = make_haptic_interface(cfg, ZMQConfig())
        assert isinstance(iface, HapticClient)
        assert iface._heartbeat_interval_s == 0.1  # noqa: SLF001
        assert iface._command_timeout_ms == 500    # noqa: SLF001

    def test_dhd_kind_uses_zmq_addresses(self) -> None:
        cfg = HapticConfig(kind="dhd")
        zmq_cfg = ZMQConfig(
            haptic_state_address="ipc:///tmp/test_state",
            haptic_command_address="ipc:///tmp/test_cmd",
        )
        iface = make_haptic_interface(cfg, zmq_cfg)
        assert isinstance(iface, HapticClient)
        assert iface._state_address == "ipc:///tmp/test_state"       # noqa: SLF001
        assert iface._command_address == "ipc:///tmp/test_cmd"       # noqa: SLF001

    def test_dhd_kind_with_external_context_does_not_own_it(self) -> None:
        cfg = HapticConfig(kind="dhd")
        ctx: zmq.Context[Any] = zmq.Context()
        try:
            iface = make_haptic_interface(cfg, ZMQConfig(), context=ctx)
            assert isinstance(iface, HapticClient)
            assert not iface._own_context  # noqa: SLF001
            assert iface._context is ctx   # noqa: SLF001
        finally:
            ctx.term()

    def test_dhd_kind_without_context_owns_its_own(self) -> None:
        cfg = HapticConfig(kind="dhd")
        iface = make_haptic_interface(cfg, ZMQConfig())
        assert isinstance(iface, HapticClient)
        assert iface._own_context  # noqa: SLF001
