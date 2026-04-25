"""Display backend factory.

The display backend is unusual: the production path is a subprocess
(``DisplayProcess``) plus a ZMQ client (``DisplayClient``), while the mock
path is a single in-process class. ``make_display_interface()`` is a
context manager so callers get uniform lifecycle handling regardless of
which backend the config selects.
"""

from __future__ import annotations

import logging
import multiprocessing.queues
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from hapticore.core.config import DisplayConfig, ZMQConfig
from hapticore.core.interfaces import DisplayInterface
from hapticore.core.messaging import EventPublisher

if TYPE_CHECKING:
    from hapticore.display.process import DisplayProcess

__all__ = ["make_display_interface"]

logger = logging.getLogger(__name__)

# Time PsychoPy takes to create the window and bind its ZMQ sockets before
# we can send the first command. Longer on macOS than Linux.
_DISPLAY_STARTUP_DELAY_S = 1.5

# Time we'll wait for DisplayProcess.join() before falling back to terminate().
_DISPLAY_SHUTDOWN_TIMEOUT_S = 5.0
_DISPLAY_TERMINATE_JOIN_TIMEOUT_S = 2.0


@contextmanager
def make_display_interface(
    cfg: DisplayConfig,
    zmq_cfg: ZMQConfig,
    *,
    publisher: EventPublisher,
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None,
) -> Iterator[DisplayInterface]:
    """Construct a DisplayInterface from a resolved DisplayConfig.

    Yields a DisplayInterface implementation. For ``backend="psychopy"``,
    spawns a ``DisplayProcess`` on entry and tears it down on exit. For
    ``backend="mock"``, yields a ``MockDisplay`` with no subprocess.

    Args:
        cfg: Resolved ``DisplayConfig``.
        zmq_cfg: ZMQ address configuration. Only consulted for
            ``backend="psychopy"`` (passed to ``DisplayProcess``).
        publisher: Shared ``EventPublisher`` used by ``DisplayClient`` to
            publish display commands. Required for ``backend="psychopy"``;
            unused for ``backend="mock"`` but the kwarg is required so
            callers don't accidentally forget it when switching backends.
        mouse_queue: Optional queue passed through to ``DisplayProcess``
            for mouse-driven haptic mode. Unused by ``MockDisplay``.

    Raises:
        ValueError: If ``cfg.backend`` is not one of the supported values.
    """
    if cfg.backend == "mock":
        from hapticore.display.mock import MockDisplay
        yield MockDisplay()
        return

    if cfg.backend == "psychopy":
        from hapticore.display.client import DisplayClient
        from hapticore.display.process import DisplayProcess

        proc: DisplayProcess = DisplayProcess(
            cfg, zmq_cfg, headless=False, mouse_queue=mouse_queue,
        )
        proc_started = False
        try:
            proc.start()
            proc_started = True
            # PsychoPy takes ~1s to create the window and bind its ZMQ
            # sockets. Without this sleep, the first command arrives before
            # the subscriber is ready and gets silently dropped.
            time.sleep(_DISPLAY_STARTUP_DELAY_S)
            yield DisplayClient(publisher)
        finally:
            if proc_started:
                proc.request_shutdown()
                proc.join(timeout=_DISPLAY_SHUTDOWN_TIMEOUT_S)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=_DISPLAY_TERMINATE_JOIN_TIMEOUT_S)
                    if proc.is_alive():
                        logger.warning(
                            "DisplayProcess (pid=%d) still alive after terminate(); "
                            "may leak. Run `kill -9 %d` to clean up manually.",
                            proc.pid, proc.pid,
                        )
        return

    raise ValueError(f"Unknown display backend: {cfg.backend!r}")
