"""Sync backend factory.

``make_sync_interface()`` is a context manager that yields a
``SyncInterface`` implementation. For ``backend="teensy"``, a
``SyncProcess`` subprocess is started on entry and torn down on
exit. For ``backend="mock"``, a ``MockSync`` is yielded with no
subprocess.
"""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Iterator
from contextlib import contextmanager

from hapticore.core.config import SyncConfig, ZMQConfig
from hapticore.core.interfaces import SyncInterface
from hapticore.core.messaging import EventPublisher

__all__ = ["make_sync_interface"]

logger = logging.getLogger(__name__)

# Time to wait for SyncProcess to open the serial port and subscribe
# to ZMQ before declaring it ready. Generous to cover slow USB
# enumeration on some Linux kernels.
_SYNC_READY_TIMEOUT_S = 5.0

# Shutdown timeouts — same structure as display factory.
_SYNC_SHUTDOWN_TIMEOUT_S = 3.0
_SYNC_TERMINATE_JOIN_TIMEOUT_S = 2.0


@contextmanager
def make_sync_interface(
    cfg: SyncConfig,
    zmq_cfg: ZMQConfig,
    *,
    publisher: EventPublisher,
) -> Iterator[SyncInterface]:
    """Construct a SyncInterface from a resolved SyncConfig.

    Yields a SyncInterface implementation. For ``backend="teensy"``,
    spawns a ``SyncProcess`` on entry and tears it down on exit.
    For ``backend="mock"``, yields a ``MockSync`` with no subprocess.

    Args:
        cfg: Resolved ``SyncConfig``.
        zmq_cfg: ZMQ address configuration.
        publisher: Shared ``EventPublisher`` used by ``TeensySync``
            to publish commands for ``SyncProcess``.

    Raises:
        ValueError: If ``cfg.backend`` is not a supported value.
        RuntimeError: If ``SyncProcess`` fails to become ready within
            the timeout.
    """
    if cfg.backend == "mock":
        from hapticore.sync.mock import MockSync
        yield MockSync()
        return

    if cfg.backend == "teensy":
        from hapticore.sync.sync_process import SyncProcess
        from hapticore.sync.teensy_sync import TeensySync

        ready_event = multiprocessing.Event()
        proc = SyncProcess(
            cfg, zmq_cfg, ready_event=ready_event,
        )
        proc_started = False
        try:
            proc.start()
            proc_started = True

            if not ready_event.wait(timeout=_SYNC_READY_TIMEOUT_S):
                if not proc.is_alive():
                    raise RuntimeError(
                        f"SyncProcess died during startup "
                        f"(exit code: {proc.exitcode}). Check that the "
                        f"Teensy is connected at {cfg.teensy.port}."
                    )
                raise RuntimeError(
                    f"SyncProcess started but did not become ready "
                    f"within {_SYNC_READY_TIMEOUT_S}s. The Teensy "
                    f"may be unresponsive."
                )

            yield TeensySync(publisher)
        finally:
            if proc_started:
                proc.request_shutdown()
                proc.join(timeout=_SYNC_SHUTDOWN_TIMEOUT_S)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=_SYNC_TERMINATE_JOIN_TIMEOUT_S)
                    if proc.is_alive():
                        logger.warning(
                            "SyncProcess (pid=%d) still alive after "
                            "terminate(); may leak.",
                            proc.pid,
                        )
        return

    raise ValueError(f"Unknown sync backend: {cfg.backend!r}")
