"""Adapter wrapping pyserial for the Teensy sync hub.

Centralizes all serial I/O in one class so tests can inject a fake
module and production code has a single point of import. ``pyserial``
is imported only when ``open()`` is called and no fake has been
injected.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any


class TeensySerialClient:
    """Minimal pyserial adapter for the Teensy sync hub.

    Parameters
    ----------
    port : str
        Serial device path, e.g. ``/dev/ttyACM0``.
    baud : int
        Serial baud rate.
    serial_module : ModuleType | None
        If provided, used directly (tests inject a fake). If None, the
        real ``serial`` module is imported on ``open()``.
    timeout : float
        Read timeout in seconds for ``readline()``. Writes are unbuffered.
    """

    def __init__(
        self,
        *,
        port: str,
        baud: int,
        serial_module: ModuleType | None = None,
        timeout: float = 0.1,
    ) -> None:
        self._port = port
        self._baud = baud
        self._injected_module = serial_module
        self._module: ModuleType | None = None
        self._serial: Any = None
        self._timeout = timeout

    def open(self) -> None:
        """Open the serial connection."""
        if self._serial is not None:
            raise RuntimeError("TeensySerialClient is already open")
        if self._injected_module is not None:
            self._module = self._injected_module
        else:
            import serial as _serial  # noqa: PLC0415 — lazy by design
            self._module = _serial
        assert self._module is not None  # noqa: S101 — unreachable; narrows type
        self._serial = self._module.Serial(
            port=self._port,
            baudrate=self._baud,
            timeout=self._timeout,
        )

    def close(self) -> None:
        """Close the serial connection if open."""
        if self._serial is not None:
            self._serial.close()
        self._serial = None

    def is_open(self) -> bool:
        return self._serial is not None

    def write(self, data: bytes) -> None:
        """Write raw bytes to the serial port."""
        if self._serial is None:
            raise RuntimeError("TeensySerialClient.open() must be called before write()")
        self._serial.write(data)

    def readline(self) -> bytes:
        """Read a line from the serial port (unused in 5A.4; kept for future)."""
        if self._serial is None:
            raise RuntimeError("TeensySerialClient.open() must be called before readline()")
        result: bytes = self._serial.readline()
        return result
