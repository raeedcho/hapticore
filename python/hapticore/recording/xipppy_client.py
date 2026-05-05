"""Adapter wrapping xipppy for the Ripple Grapevine Scout.

Centralizes all xipppy I/O so tests can inject a fake module and
production code has a single point of import. xipppy is imported
only when connect() is called and no fake has been injected.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Self

logger = logging.getLogger(__name__)


class XipppyClient:
    """Adapter wrapping xipppy for the Ripple Grapevine Scout.

    Centralizes all xipppy I/O so tests can inject a fake module and
    production code has a single point of import. xipppy is imported
    only when connect() is called and no fake has been injected.
    """

    def __init__(
        self,
        *,
        use_tcp: bool = True,
        operator_id: int = 129,
        xipppy_module: ModuleType | None = None,
    ) -> None:
        self._use_tcp = use_tcp
        self._operator_id = operator_id
        self._injected_module = xipppy_module
        self._xp: ModuleType | None = None
        self._connected = False

    def connect(self) -> None:
        """Open xipppy connection and register operator if TCP.

        Calls xp._open() then xp.add_operator() if TCP. Uses _open/_close
        directly (not xipppy_open context manager) because the two-step
        connect sequence needs explicit error handling — if _open succeeds
        but add_operator fails, disconnect() must still call _close().
        """
        if self._connected:
            raise RuntimeError("XipppyClient is already connected")
        if self._injected_module is not None:
            self._xp = self._injected_module
        else:
            try:
                import xipppy as _xp  # noqa: PLC0415 — lazy by design
            except ImportError as exc:
                raise ImportError(
                    "xipppy is required for Ripple recording but is not installed. "
                    "Install the xipppy wheel provided by Ripple "
                    "(see docs/rig-setup.md § Ripple software)."
                ) from exc
            self._xp = _xp

        assert self._xp is not None  # noqa: S101 — unreachable; narrows type
        self._xp._open(use_tcp=self._use_tcp)
        self._connected = True

        if self._use_tcp:
            try:
                self._xp.add_operator(self._operator_id)
            except Exception:
                try:
                    self._xp._close()
                except Exception:
                    logger.exception("Failed to close xipppy after add_operator failure")
                self._connected = False
                raise

    def disconnect(self) -> None:
        """Close xipppy connection."""
        try:
            if self._connected and self._xp is not None:
                self._xp._close()
        finally:
            self._connected = False

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()

    def start_recording(
        self,
        file_name_base: str,
        auto_stop_time_s: int = 0,
    ) -> tuple[str, str, int, bool, int]:
        """Start Trellis recording. Returns the trial() response tuple."""
        if not self._connected or self._xp is None:
            raise RuntimeError("XipppyClient.connect() must be called before start_recording()")
        result: tuple[str, str, int, bool, int] = self._xp.trial(
            oper=self._operator_id,
            status="recording",
            file_name_base=file_name_base,
            auto_stop_time=auto_stop_time_s,
            auto_incr=False,
        )
        return result

    def stop_recording(self) -> tuple[str, str, int, bool, int]:
        """Stop Trellis recording. Returns the trial() response tuple."""
        if not self._connected or self._xp is None:
            raise RuntimeError("XipppyClient.connect() must be called before stop_recording()")
        result: tuple[str, str, int, bool, int] = self._xp.trial(
            oper=self._operator_id,
            status="stopped",
        )
        return result

    def get_time(self) -> float:
        """Return Ripple processor time in seconds (converted from 30 kHz ticks)."""
        if not self._connected or self._xp is None:
            raise RuntimeError("XipppyClient.connect() must be called before get_time()")
        ticks: int = self._xp.time()
        return ticks / 30_000.0

    def is_connected(self) -> bool:
        return self._connected
