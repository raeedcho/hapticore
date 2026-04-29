"""Haptic interface implementations and factory."""

from __future__ import annotations

import logging
import multiprocessing.queues
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import zmq

from hapticore.core.config import DhdConfig, HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.core.messages import TOPIC_STATE
from hapticore.haptic.client import HapticClient
from hapticore.haptic.mock import MockHapticInterface
from hapticore.haptic.mouse_bridge import MouseBridge

__all__ = ["HapticClient", "MockHapticInterface", "MouseBridge", "make_haptic_interface"]

logger = logging.getLogger(__name__)


def _haptic_server_alive(
    state_address: str,
    *,
    context: zmq.Context[Any] | None = None,
    timeout_s: float = 0.5,
) -> bool:
    """Return True iff a haptic server is publishing state on ``state_address``.

    Opens a SUB socket, subscribes to ``TOPIC_STATE``, and waits up to
    ``timeout_s`` for a single state message. The publisher runs at
    200 Hz, so a 500 ms window has ~100 messages of headroom — comfortably
    detects a healthy server. Returns False on RCVTIMEO; this is the
    "no server" signal, not an error.

    A separate ZMQ context is used by default so this probe is safe to
    call before the long-lived session context is created and so a probe
    timeout never leaves a half-bound socket in the session context.
    """
    own_ctx = context is None
    ctx: zmq.Context[Any] = context if context is not None else zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
    sub.setsockopt(zmq.SUBSCRIBE, TOPIC_STATE)
    try:
        sub.connect(state_address)
        sub.recv_multipart()
        return True
    except zmq.Again:
        return False
    finally:
        sub.close(linger=0)
        if own_ctx:
            ctx.term()


def _resolve_server_binary(cfg: DhdConfig) -> Path:
    """Resolve the server binary path; env var wins, then config field."""
    env = os.environ.get("HAPTICORE_HAPTIC_SERVER_BIN")
    if env:
        return Path(env)
    if cfg.server_binary is not None:
        return cfg.server_binary
    raise RuntimeError(
        "auto_start=True but no haptic_server binary path was provided. "
        "Set the HAPTICORE_HAPTIC_SERVER_BIN environment variable or "
        "haptic.dhd.server_binary in your rig config."
    )


def _spawn_haptic_server(
    cfg: DhdConfig, zmq_cfg: ZMQConfig,
) -> subprocess.Popen[bytes]:
    """Spawn the haptic_server binary with addresses and rig parameters
    matching the resolved config.

    Inherits stdout/stderr so the user sees calibration progress directly.
    Uses start_new_session=True so Ctrl+C in the parent's terminal doesn't
    reach the spawned server. Passes --die-with-parent so the C++ server
    sets PR_SET_PDEATHSIG on Linux (handles the case where Python crashes
    hard without unwinding through the factory's finally block). The
    prctl call happens in the C++ binary after exec(), avoiding the
    multi-thread fork hazard documented at
    https://docs.python.org/3/library/subprocess.html#subprocess.Popen.preexec_fn
    """
    binary = _resolve_server_binary(cfg)
    if not binary.exists():
        raise RuntimeError(
            f"Configured haptic_server binary does not exist: {binary}. "
            "Build with `pixi run mock-cpp` (mock) or "
            "`pixi run dhd-cpp-build` (real hardware)."
        )

    args: list[str] = [
        str(binary),
        "--pub-address", zmq_cfg.haptic_state_address,
        "--cmd-address", zmq_cfg.haptic_command_address,
        "--force-limit", str(cfg.force_limit_n),
        "--pub-rate", str(cfg.publish_rate_hz),
        "--die-with-parent",
    ]

    logger.info("Spawning haptic_server: %s", " ".join(args))
    return subprocess.Popen(args, start_new_session=True)


def _wait_for_server_ready(
    state_address: str, *, timeout_s: float,
) -> None:
    """Poll the probe until it succeeds or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        probe_timeout = min(0.5, remaining)
        if _haptic_server_alive(state_address, timeout_s=probe_timeout):
            return
        # Probe blocks for up to probe_timeout; loop immediately and re-probe
        # rather than adding another sleep.
    raise RuntimeError(
        f"Haptic server did not become ready within {timeout_s} s. "
        "Possible causes: calibration is taking longer than expected, "
        "the binary crashed at startup (check stderr), or a stale IPC "
        "socket file is preventing bind. Check the server's stdout."
    )


def _terminate_server(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM, wait 5 s, SIGKILL if still alive."""
    if proc.poll() is not None:
        return  # already exited
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        logger.warning(
            "haptic_server (pid=%d) did not exit within 5 s of SIGTERM; "
            "sending SIGKILL.",
            proc.pid,
        )
        proc.kill()
        proc.wait(timeout=2.0)


@contextmanager
def make_haptic_interface(
    cfg: HapticConfig,
    zmq_cfg: ZMQConfig,
    *,
    context: zmq.Context[Any] | None = None,
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None,
) -> Iterator[HapticInterface]:
    """Construct a HapticInterface from a resolved HapticConfig.

    Now a context manager so the dhd backend can own the haptic_server
    subprocess lifecycle when it spawns one. Mock backend yields
    immediately and owns no resources; the dhd backend yields a
    connected HapticClient (probing the configured state address first
    and either attaching, spawning, or raising depending on auto_start).

    Args:
        cfg: Resolved ``HapticConfig``. Must have ``backend`` set (and, for
            ``backend="dhd"``, a populated ``dhd`` block — the ``model_validator``
            on ``HapticConfig`` handles this automatically).
        zmq_cfg: ZMQ address configuration. Only consulted for ``backend="dhd"``.
        context: Optional ZMQ context. Only used for ``backend="dhd"``. If
            omitted, ``HapticClient`` creates and owns its own context.
        mouse_queue: Optional queue of ``(x_m, y_m)`` tuples. When provided
            alongside ``backend="dhd"`` and ``dhd.mouse_input=True``, a
            ``MouseBridge`` thread is started to forward positions to the
            server. Ignored for ``backend="mock"``.

    Raises:
        ValueError: If ``backend="dhd"`` but the ``dhd`` config block is missing
            (should be impossible after validation; defensive check).
        RuntimeError: If ``backend="dhd"``, ``auto_start=False``, and no server
            is detected at the configured ZMQ state address.
    """
    if cfg.backend == "mock":
        yield MockHapticInterface()
        return

    if cfg.backend == "dhd":
        if cfg.dhd is None:
            raise ValueError(
                "HapticConfig.backend='dhd' but dhd block is None. "
                "Construct HapticConfig via its normal validation path."
            )

        pre_existing = _haptic_server_alive(zmq_cfg.haptic_state_address)
        spawned: subprocess.Popen[bytes] | None = None

        if not pre_existing:
            if not cfg.dhd.auto_start:
                raise RuntimeError(
                    f"No haptic server detected at "
                    f"{zmq_cfg.haptic_state_address}, and "
                    f"haptic.dhd.auto_start is False. Either start the "
                    f"server manually (see docs/rig-setup.md) or remove "
                    f"the auto_start: false override from your rig config."
                )
            spawned = _spawn_haptic_server(cfg.dhd, zmq_cfg)
            try:
                _wait_for_server_ready(
                    zmq_cfg.haptic_state_address,
                    timeout_s=cfg.dhd.startup_timeout_s,
                )
            except BaseException:
                _terminate_server(spawned)
                raise

        client = HapticClient(
            state_address=zmq_cfg.haptic_state_address,
            command_address=zmq_cfg.haptic_command_address,
            heartbeat_interval_s=cfg.dhd.heartbeat_interval_s,
            command_timeout_ms=cfg.dhd.command_timeout_ms,
            context=context,
        )
        bridge: MouseBridge | None = None
        try:
            with client:
                if mouse_queue is not None:
                    bridge = MouseBridge(
                        mouse_queue=mouse_queue,
                        command_address=zmq_cfg.haptic_command_address,
                        context=context,
                    )
                    bridge.start()
                yield client
        finally:
            if bridge is not None:
                bridge.request_stop()
                bridge.join(timeout=2.0)
            if spawned is not None:
                _terminate_server(spawned)
        return

    raise ValueError(f"Unknown haptic backend: {cfg.backend!r}")
