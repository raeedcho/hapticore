"""Haptic interface implementations and factory."""

from __future__ import annotations

import multiprocessing.queues
from typing import Any

import zmq

from hapticore.core.config import HapticConfig, ZMQConfig
from hapticore.core.interfaces import HapticInterface
from hapticore.haptic.client import HapticClient
from hapticore.haptic.mock import MockHapticInterface
from hapticore.haptic.mouse import MouseHapticInterface

__all__ = ["HapticClient", "MockHapticInterface", "MouseHapticInterface", "make_haptic_interface"]


def make_haptic_interface(
    cfg: HapticConfig,
    zmq_cfg: ZMQConfig,
    *,
    context: zmq.Context[Any] | None = None,
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None,
) -> HapticInterface:
    """Construct a HapticInterface from a resolved HapticConfig.

    The returned interface satisfies the HapticInterface Protocol. For the
    ``dhd`` backend, the caller is responsible for calling ``connect()`` and
    ``close()`` on the returned HapticClient (or using it as a context
    manager); the factory does NOT connect for you, so the caller owns
    the lifecycle.

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
    """
    if cfg.backend == "mock":
        from hapticore.haptic.mock import MockHapticInterface
        return MockHapticInterface()

    if cfg.backend == "mouse":
        if mouse_queue is None:
            raise ValueError(
                "HapticConfig.backend='mouse' requires mouse_queue to be passed "
                "to make_haptic_interface(). Pass the same queue to "
                "DisplayProcess(mouse_queue=...) so mouse position flows from "
                "the display process to the haptic interface."
            )
        from hapticore.haptic.mouse import MouseHapticInterface
        return MouseHapticInterface(mouse_queue=mouse_queue)

    if cfg.backend == "dhd":
        if cfg.dhd is None:
            # Defensive: the model_validator should have populated this.
            raise ValueError(
                "HapticConfig.backend='dhd' but dhd block is None. "
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
    raise ValueError(f"Unknown haptic backend: {cfg.backend!r}")
