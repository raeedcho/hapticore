"""Capture and analysis of Ripple Scout digital-input events.

The Teensy hardware suite uses the Scout as its measuring instrument: the
Teensy's outputs are wired to the Scout's digital inputs, and ``xipppy.digin()``
returns time-stamped events for them. This module turns those events into SMA
edges and parallel-port event codes, and accumulates them across polls.

It is pure Python and never imports xipppy. ``DiginRecorder`` is handed the
``digin`` function, so this measurement logic is unit-tested in CI
(``tests/unit/test_ripple_digin.py``) without a bench.

Polling is synchronous; there is no background thread. Ripple's event buffer
holds 1,024 events and is flushed on every read, so tests wait with
``DiginRecorder.wait()`` rather than ``time.sleep()``. A poll that may have
lost events raises ``DiginOverflowError``, so a test never measures incomplete
data.

Events are ``SegmentEventPacket`` objects (docs/xipppy_api_reference.md §10).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

TICKS_PER_S = 30_000  # NIP clock ticks per second (docs/xipppy_api_reference.md, xipppy.time())
DIGIN_SAMPLE_PERIOD_S = 1e-4  # Scout digital inputs are sampled at 10 kS/s (Grapevine manual)
REASON_PARALLEL = 0x01  # SegmentEventPacket.reason bits (docs/xipppy_api_reference.md §10)
REASON_SMA = {1: 0x02, 2: 0x04, 3: 0x08, 4: 0x10}

_POLL_INTERVAL_S = 0.25  # wait() polls at least this often to stay clear of the buffer limit


@dataclass(frozen=True)
class Edge:
    """One transition of an SMA input."""

    t_s: float
    level: int  # 1 = the input went high, 0 = it went low


@dataclass(frozen=True)
class Code:
    """One parallel-port event code, latched on the strobe."""

    t_s: float
    value: int  # the full 16-bit parallel word


def sma_edges(events: Sequence[Any], sma: int) -> list[Edge]:
    """Return the edges on SMA input ``sma`` (1-4), in the order they arrived.

    ``reason`` is a bitmask, so one event can flag several inputs and counts
    for each of them. An edge's level comes from the matching ``.smaN`` field.
    """
    if sma not in REASON_SMA:
        raise ValueError(f"sma must be one of {sorted(REASON_SMA)}, got {sma!r}")
    mask = REASON_SMA[sma]
    field = f"sma{sma}"
    return [
        Edge(t_s=float(e.timestamp) / TICKS_PER_S, level=1 if getattr(e, field) else 0)
        for e in events
        if e.reason & mask
    ]


def parallel_codes(events: Sequence[Any]) -> list[Code]:
    """Return the parallel-port codes, in the order they arrived.

    The value is the full 16-bit ``.parallel`` word, never masked to 8 bits,
    so a code that lands on the wrong data lines stays visible.
    """
    return [
        Code(t_s=float(e.timestamp) / TICKS_PER_S, value=int(e.parallel))
        for e in events
        if e.reason & REASON_PARALLEL
    ]


class DiginOverflowError(RuntimeError):
    """Ripple's event buffer overflowed or may have, so events may have been lost."""


class DiginRecorder:
    """Accumulates ``digin()`` events across polls.

    Parameters
    ----------
    digin : Callable[[int], tuple[int, list[Any]]]
        ``xipppy.digin`` or a stand-in. Called with ``max_events``; returns
        ``(n, events)``, where ``n`` counts the events since the last call.
    max_events : int
        Passed to every ``digin()`` call. Ripple's buffer holds 1,024 events.
    """

    def __init__(
        self,
        digin: Callable[[int], tuple[int, list[Any]]],
        *,
        max_events: int = 1024,
    ) -> None:
        self._digin = digin
        self._max_events = max_events
        self._events: list[Any] = []

    def clear(self) -> None:
        """Discard Ripple's buffer and everything accumulated, without an overflow check."""
        self._digin(self._max_events)
        self._events.clear()

    def poll(self) -> None:
        """Read Ripple's buffer once and add its events to the accumulator.

        Raises
        ------
        DiginOverflowError
            If ``digin()`` reports more events than it returned, or returns
            exactly ``max_events``, meaning the buffer may have filled.
        """
        count, events = self._digin(self._max_events)
        self._events.extend(events)
        if count > len(events):
            raise DiginOverflowError(
                f"digin() reported {count} events but returned {len(events)}: "
                "Ripple's event buffer overflowed and events were lost. "
                "Poll more often or reduce the event rate."
            )
        if len(events) >= self._max_events:
            raise DiginOverflowError(
                f"digin() returned {len(events)} events, its limit of {self._max_events}: "
                "Ripple's event buffer may have filled and events may have been lost. "
                "Poll more often or reduce the event rate."
            )

    def wait(self, seconds: float) -> None:
        """Sleep for ``seconds``, polling at least every 0.25 s and once at the end."""
        deadline = time.monotonic() + seconds
        while True:
            time.sleep(min(max(deadline - time.monotonic(), 0.0), _POLL_INTERVAL_S))
            self.poll()
            if time.monotonic() >= deadline:
                return

    def events(self) -> list[Any]:
        """Poll once more, then return every event since the last ``clear()``."""
        self.poll()
        return list(self._events)
