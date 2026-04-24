"""Hardware interface implementations (real and mock)."""

from __future__ import annotations

import multiprocessing.queues
from typing import Any

import zmq

from hapticore.core.config import HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.hardware.haptic_client import HapticClient

__all__ = ["HapticClient", "make_haptic_interface"]


def make_haptic_interface(
    cfg: HapticConfig,
    zmq_cfg: ZMQConfig,
    *,
    context: zmq.Context[Any] | None = None,
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None,
) -> HapticInterface:
    """Construct a HapticInterface from a resolved HapticConfig.

    The returned interface satisfies the HapticInterface Protocol. For the
    ``dhd`` kind, the caller is responsible for calling ``connect()`` and
    ``close()`` on the returned HapticClient (or using it as a context
    manager); the factory does NOT connect for you, so the caller owns
    the lifecycle.

    Args:
        cfg: Resolved ``HapticConfig``. Must have ``kind`` set (and, for
            ``kind="dhd"``, a populated ``dhd`` block — the ``model_validator``
            on ``HapticConfig`` handles this automatically).
        zmq_cfg: ZMQ address configuration. Only consulted for ``kind="dhd"``.
        context: Optional ZMQ context. Only used for ``kind="dhd"``. If
            omitted, ``HapticClient`` creates and owns its own context.
        mouse_queue: Required only when ``kind="mouse"``. The same queue
            must be passed to the display process so the mouse reader and
            the haptic interface share it.

    Raises:
        ValueError: If ``kind="mouse"`` but ``mouse_queue`` was not provided,
            or if ``kind="dhd"`` but the ``dhd`` config block is missing
            (should be impossible after validation; defensive check).
    """
    if cfg.kind == "mock":
        from hapticore.hardware.mock import MockHapticInterface
        return MockHapticInterface()

    if cfg.kind == "mouse":
        if mouse_queue is None:
            raise ValueError(
                "HapticConfig.kind='mouse' requires mouse_queue to be passed "
                "to make_haptic_interface(). Pass the same queue to "
                "DisplayProcess(mouse_queue=...) so mouse position flows from "
                "the display process to the haptic interface."
            )
        from hapticore.hardware.mouse_haptic import MouseHapticInterface
        return MouseHapticInterface(mouse_queue=mouse_queue)

    if cfg.kind == "dhd":
        if cfg.dhd is None:
            # Defensive: the model_validator should have populated this.
            raise ValueError(
                "HapticConfig.kind='dhd' but dhd block is None. "
                "Construct HapticConfig via its normal validation path."
            )
        return HapticClient(
            state_address=zmq_cfg.haptic_state_address,
            command_address=zmq_cfg.haptic_command_address,
            heartbeat_interval_s=cfg.dhd.heartbeat_interval_s,
            command_timeout_ms=cfg.dhd.command_timeout_ms,
            context=context,
        )

    # Unreachable under normal Pydantic validation, but an explicit final
    # branch keeps mypy strict-mode happy and gives a clear error if
    # someone bypasses validation.
    raise ValueError(f"Unknown haptic kind: {cfg.kind!r}")
