"""Haptic interface implementations and factory."""

from __future__ import annotations

import ctypes
import logging
import multiprocessing.queues
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import zmq

from hapticore.core.config import DhdConfig, HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.core.messages import TOPIC_STATE
from hapticore.haptic.client import HapticClient
from hapticore.haptic.mock import MockHapticInterface
from hapticore.haptic.mouse import MouseHapticInterface

__all__ = ["HapticClient", "MockHapticInterface", "MouseHapticInterface", "make_haptic_interface"]

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
    On Linux, sets PR_SET_PDEATHSIG=SIGTERM in the child so a Python crash
    doesn't leak a server. macOS has no equivalent; documented as a gap.
    """
    binary = _resolve_server_binary(cfg)
    if not binary.exists():
        raise RuntimeError(
            f"Configured haptic_server binary does not exist: {binary}. "
            "Build with `pixi run cpp` (mock) or "
            "`cmake --build --preset dev-real` (real hardware)."
        )

    args: list[str] = [
        str(binary),
        "--pub-address", zmq_cfg.haptic_state_address,
        "--cmd-address", zmq_cfg.haptic_command_address,
        "--force-limit", str(cfg.force_limit_n),
        "--pub-rate", str(cfg.publish_rate_hz),
    ]

    preexec_fn: Callable[[], None] | None = None
    if sys.platform == "linux":
        def _set_pdeathsig() -> None:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1  # noqa: N806 — Linux constant
            ret = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
            if ret != 0:
                errno = ctypes.get_errno()
                logger.warning(
                    "prctl(PR_SET_PDEATHSIG) failed (errno=%d); spawned server "
                    "may not be cleaned up if Python crashes hard.",
                    errno,
                )
        preexec_fn = _set_pdeathsig

    logger.info("Spawning haptic_server: %s", " ".join(args))
    return subprocess.Popen(args, preexec_fn=preexec_fn)


def _wait_for_server_ready(
    state_address: str, *, timeout_s: float,
) -> None:
    """Poll the probe until it succeeds or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _haptic_server_alive(state_address, timeout_s=0.5):
            return
        # Probe already includes its own 0.5 s of waiting; loop
        # immediately and re-probe rather than adding another sleep.
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
    subprocess lifecycle when it spawns one. Mock and mouse backends
    yield immediately and own no resources; the dhd backend yields a
    connected HapticClient (probing the configured state address first
    and either attaching, spawning, or raising depending on auto_start).

    Args:
        cfg: Resolved ``HapticConfig``. Must have ``backend`` set (and, for
            ``backend="dhd"``, a populated ``dhd`` block — the ``model_validator``
            on ``HapticConfig`` handles this automatically).
        zmq_cfg: ZMQ address configuration. Only consulted for ``backend="dhd"``.
        context: Optional ZMQ context. Only used for ``backend="dhd"``. If
            omitted, ``HapticClient`` creates and owns its own context.
        mouse_queue: Required only when ``backend="mouse"``. The same queue
            must be passed to the display process so the mouse reader and
            the haptic interface share it.

    Raises:
        ValueError: If ``backend="mouse"`` but ``mouse_queue`` was not provided,
            or if ``backend="dhd"`` but the ``dhd`` config block is missing
            (should be impossible after validation; defensive check).
        RuntimeError: If ``backend="dhd"``, ``auto_start=False``, and no server
            is detected at the configured ZMQ state address.
    """
    if cfg.backend == "mock":
        yield MockHapticInterface()
        return

    if cfg.backend == "mouse":
        if mouse_queue is None:
            raise ValueError(
                "HapticConfig.backend='mouse' requires mouse_queue to be passed "
                "to make_haptic_interface(). Pass the same queue to "
                "DisplayProcess(mouse_queue=...) so mouse position flows from "
                "the display process to the haptic interface."
            )
        yield MouseHapticInterface(mouse_queue=mouse_queue)
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
            except Exception:
                _terminate_server(spawned)
                raise

        client = HapticClient(
            state_address=zmq_cfg.haptic_state_address,
            command_address=zmq_cfg.haptic_command_address,
            heartbeat_interval_s=cfg.dhd.heartbeat_interval_s,
            command_timeout_ms=cfg.dhd.command_timeout_ms,
            context=context,
        )
        try:
            with client:
                yield client
        finally:
            if spawned is not None:
                _terminate_server(spawned)
        return

    raise ValueError(f"Unknown haptic backend: {cfg.backend!r}")
