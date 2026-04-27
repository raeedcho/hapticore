"""Integration tests for the haptic server probe-then-attach lifecycle.

These tests actually spawn the dev-mock haptic_server binary (if built),
exercise the factory, and verify clean lifecycle management.

Skipped automatically if the dev-mock binary is not built. Build with::

    pixi run cpp          # builds dev-mock preset
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hapticore.core.config import DhdConfig, HapticConfig, ZMQConfig
from hapticore.core.messaging import make_ipc_address
from hapticore.haptic import HapticClient, make_haptic_interface
from hapticore.haptic import _haptic_server_alive  # noqa: PLC2701 — testing internal probe
from hapticore.haptic import _spawn_haptic_server  # noqa: PLC2701 — testing internal spawn
from hapticore.haptic import _wait_for_server_ready  # noqa: PLC2701 — testing internal wait

# Locate the dev-mock binary relative to the repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent
_DEV_MOCK_BINARY = _REPO_ROOT / "cpp" / "haptic_server" / "build" / "dev-mock" / "haptic_server"

pytestmark = pytest.mark.skipif(
    not _DEV_MOCK_BINARY.exists(),
    reason=f"dev-mock haptic_server binary not found at {_DEV_MOCK_BINARY}. "
           "Build with `pixi run cpp`.",
)


def _make_cfg(binary: Path, timeout_s: float = 5.0) -> HapticConfig:
    return HapticConfig(
        backend="dhd",
        dhd=DhdConfig(
            server_binary=binary,
            startup_timeout_s=timeout_s,
        ),
    )


def _make_zmq_cfg() -> ZMQConfig:
    return ZMQConfig(
        haptic_state_address=make_ipc_address("intg_haptic_state"),
        haptic_command_address=make_ipc_address("intg_haptic_cmd"),
    )


class TestHapticServerLifecycle:
    """Factory spawns the server, exercises it, cleans up."""

    def test_factory_spawns_server_and_terminates_on_exit(self) -> None:
        """auto_start=True: factory spawns server, connects, then kills it on exit."""
        cfg = _make_cfg(_DEV_MOCK_BINARY)
        zmq_cfg = _make_zmq_cfg()

        # Capture the spawned Popen object so we can verify it's dead after exit.
        spawned_procs: list[subprocess.Popen[bytes]] = []
        real_spawn = _spawn_haptic_server

        def _recording_spawn(
            dhd_cfg: DhdConfig, z_cfg: ZMQConfig
        ) -> subprocess.Popen[bytes]:
            proc = real_spawn(dhd_cfg, z_cfg)
            spawned_procs.append(proc)
            return proc

        with patch("hapticore.haptic._spawn_haptic_server", side_effect=_recording_spawn):
            with make_haptic_interface(cfg, zmq_cfg) as haptic:
                assert isinstance(haptic, HapticClient)
                assert haptic._connected  # noqa: SLF001

                # Wait for the slow-joiner window to pass.
                deadline = time.monotonic() + 3.0
                state = None
                while time.monotonic() < deadline:
                    state = haptic.get_latest_state()
                    if state is not None:
                        break
                    time.sleep(0.05)
                assert state is not None, "Did not receive a state from the mock server"

        # After the `with` block the spawned process must be dead.
        assert len(spawned_procs) == 1
        proc = spawned_procs[0]
        # Give the OS a moment to reap
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None, "Spawned server process is still alive after factory exit"

    def test_factory_attaches_and_leaves_manual_server_running(self) -> None:
        """If a server is already running, factory attaches and leaves it alive on exit."""
        cfg = _make_cfg(_DEV_MOCK_BINARY)
        zmq_cfg = _make_zmq_cfg()

        # Launch the server manually (outside the factory).
        manual_proc = subprocess.Popen(
            [
                str(_DEV_MOCK_BINARY),
                "--pub-address", zmq_cfg.haptic_state_address,
                "--cmd-address", zmq_cfg.haptic_command_address,
            ]
        )
        try:
            # Wait for it to come up.
            _wait_for_server_ready(zmq_cfg.haptic_state_address, timeout_s=5.0)

            # Factory must attach (not spawn) and leave the server running.
            with make_haptic_interface(cfg, zmq_cfg) as haptic:
                assert isinstance(haptic, HapticClient)
                assert haptic._connected  # noqa: SLF001

            # The manually-launched server must still be alive.
            assert manual_proc.poll() is None, (
                "Factory killed the manually-launched server — it should only "
                "kill what it spawned."
            )
        finally:
            if manual_proc.poll() is None:
                manual_proc.terminate()
                manual_proc.wait(timeout=5.0)
