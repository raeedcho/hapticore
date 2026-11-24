"""Unit tests for the Ripple digital-input capture helper.

``tests/hardware/ripple_digin.py`` is the suite's measuring logic: every
hardware assertion about edges, pulses, and event codes depends on it, and CI
is the only place it can be checked without a bench. These tests use synthetic
``SegmentEventPacket`` stand-ins and a scripted ``digin`` function, so they run
without xipppy or hardware.

Reason values are written as literals (1 = parallel, 2/4/8/16 = SMA1-4,
32 = digout marker; docs/xipppy_api_reference.md §10) so the tests also check
the helper's constants against the documented bitmask.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from tests.hardware import ripple_digin
from tests.hardware.ripple_digin import (
    Code,
    DiginOverflowError,
    DiginRecorder,
    Edge,
    parallel_codes,
    sma_edges,
)

# ---------------------------------------------------------------------------
# Synthetic packets and fakes
# ---------------------------------------------------------------------------


def _packet(
    timestamp: int,
    reason: int,
    *,
    sma1: int = 0,
    sma2: int = 0,
    sma3: int = 0,
    sma4: int = 0,
    parallel: int = 0,
) -> SimpleNamespace:
    """A stand-in for ``xipppy.SegmentEventPacket`` (timestamp in 30 kHz ticks)."""
    return SimpleNamespace(
        timestamp=timestamp,
        reason=reason,
        sma1=sma1,
        sma2=sma2,
        sma3=sma3,
        sma4=sma4,
        parallel=parallel,
    )


def _reply(*events: Any) -> tuple[int, list[Any]]:
    """A ``digin()`` reply that reports exactly the events it returns."""
    return len(events), list(events)


class _ScriptedDigin:
    """Stand-in for ``xipppy.digin`` that replays scripted ``(n, events)`` replies."""

    def __init__(self, *replies: tuple[int, list[Any]]) -> None:
        self._replies = list(replies)
        self.calls: list[int] = []

    def __call__(self, max_events: int) -> tuple[int, list[Any]]:
        self.calls.append(max_events)
        if not self._replies:
            raise AssertionError("digin() was called more often than the test scripted")
        return self._replies.pop(0)


class _FakeClock:
    """Stand-in for the ``time`` module: sleeping advances ``now`` instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """Replace the helper's ``time`` module so ``wait()`` runs without real sleeping."""
    fake = _FakeClock()
    monkeypatch.setattr(ripple_digin, "time", fake)
    return fake


# ---------------------------------------------------------------------------
# sma_edges
# ---------------------------------------------------------------------------


class TestSmaEdges:
    def test_returns_only_events_for_the_requested_input(self) -> None:
        events = [
            _packet(15_000, 2, sma1=1),
            _packet(16_000, 4, sma2=1),
            _packet(30_000, 2, sma1=0),
            _packet(31_000, 1, parallel=42),
            _packet(32_000, 8, sma3=1),
            _packet(33_000, 16, sma4=1),
        ]

        assert sma_edges(events, 1) == [Edge(0.5, 1), Edge(1.0, 0)]
        assert sma_edges(events, 2) == [Edge(16_000 / 30_000, 1)]
        assert sma_edges(events, 3) == [Edge(32_000 / 30_000, 1)]
        assert sma_edges(events, 4) == [Edge(33_000 / 30_000, 1)]

    def test_times_are_in_seconds(self) -> None:
        events = [_packet(0, 2, sma1=1), _packet(30_000, 2, sma1=0), _packet(3_000_000, 2, sma1=1)]

        assert [edge.t_s for edge in sma_edges(events, 1)] == [0.0, 1.0, 100.0]

    def test_level_comes_from_the_matching_field(self) -> None:
        # Another input's field is nonzero, and this input's own field decides.
        events = [
            _packet(1_000, 2, sma1=0, sma2=1),
            _packet(2_000, 4, sma1=1, sma2=0),
            _packet(3_000, 4, sma1=0, sma2=5),
        ]

        assert [edge.level for edge in sma_edges(events, 1)] == [0]
        assert [edge.level for edge in sma_edges(events, 2)] == [0, 1]

    def test_event_flagging_two_inputs_appears_in_both_with_its_own_level(self) -> None:
        events = [_packet(3_000, 2 | 4, sma1=1, sma2=0), _packet(6_000, 2 | 4, sma1=0, sma2=1)]

        assert sma_edges(events, 1) == [Edge(0.1, 1), Edge(0.2, 0)]
        assert sma_edges(events, 2) == [Edge(0.1, 0), Edge(0.2, 1)]

    def test_event_flagging_parallel_and_sma_counts_for_the_sma(self) -> None:
        events = [_packet(3_000, 1 | 2, sma1=1, parallel=7)]

        assert sma_edges(events, 1) == [Edge(0.1, 1)]

    def test_other_reason_bits_are_ignored(self) -> None:
        # A digout marker carries nonzero SMA fields but flags none of the inputs.
        events = [_packet(3_000, 32, sma1=1, sma2=1, sma3=1, sma4=1, parallel=9)]

        for sma in (1, 2, 3, 4):
            assert sma_edges(events, sma) == []

    @pytest.mark.parametrize("sma", [0, 5, -1])
    def test_rejects_an_input_that_does_not_exist(self, sma: int) -> None:
        with pytest.raises(ValueError, match="sma must be one of"):
            sma_edges([], sma)

    def test_returns_plain_python_numbers(self) -> None:
        events = [_packet(np.uint32(15_000), np.uint16(2), sma1=np.uint16(1))]

        (edge,) = sma_edges(events, 1)

        assert type(edge.t_s) is float
        assert type(edge.level) is int


# ---------------------------------------------------------------------------
# parallel_codes
# ---------------------------------------------------------------------------


class TestParallelCodes:
    def test_returns_only_parallel_events(self) -> None:
        events = [
            _packet(3_000, 1, parallel=42),
            _packet(6_000, 2, sma1=1, parallel=99),
            _packet(9_000, 1, parallel=0),
            _packet(12_000, 4, sma2=1),
        ]

        assert parallel_codes(events) == [Code(0.1, 42), Code(0.3, 0)]

    def test_keeps_all_16_bits(self) -> None:
        events = [
            _packet(3_000, 1, parallel=0x0001),
            _packet(6_000, 1, parallel=0x0200),
            _packet(9_000, 1, parallel=0x8000),
            _packet(12_000, 1, parallel=0xFFFF),
            _packet(15_000, 1, parallel=0xAB12),
        ]

        assert [code.value for code in parallel_codes(events)] == [
            0x0001,
            0x0200,
            0x8000,
            0xFFFF,
            0xAB12,
        ]

    def test_times_are_in_seconds(self) -> None:
        events = [_packet(15_000, 1, parallel=1), _packet(3_000_000, 1, parallel=2)]

        assert [code.t_s for code in parallel_codes(events)] == [0.5, 100.0]

    def test_event_flagging_parallel_and_sma_appears_as_a_code(self) -> None:
        events = [_packet(3_000, 1 | 2, sma1=1, parallel=7)]

        assert parallel_codes(events) == [Code(0.1, 7)]

    def test_other_reason_bits_are_ignored(self) -> None:
        events = [_packet(3_000, 32, parallel=0x00FF), _packet(6_000, 16, sma4=1, parallel=0x00FF)]

        assert parallel_codes(events) == []

    def test_returns_plain_python_numbers(self) -> None:
        events = [_packet(np.uint32(15_000), np.uint16(1), parallel=np.uint16(0x0200))]

        (code,) = parallel_codes(events)

        assert type(code.t_s) is float
        assert type(code.value) is int


# ---------------------------------------------------------------------------
# DiginRecorder: poll, clear, events
# ---------------------------------------------------------------------------


class TestDiginRecorder:
    def test_accumulates_events_across_polls(self) -> None:
        first, second, third = (_packet(t, 2, sma1=1) for t in (3_000, 6_000, 9_000))
        recorder = DiginRecorder(_ScriptedDigin(_reply(first), _reply(second), _reply(third)))

        recorder.poll()
        recorder.poll()

        # events() polls once more before returning everything.
        assert recorder.events() == [first, second, third]

    def test_events_polls_once_more_and_keeps_accumulating(self) -> None:
        first, second = _packet(3_000, 2, sma1=1), _packet(6_000, 2, sma1=0)
        digin = _ScriptedDigin(_reply(first), _reply(second))
        recorder = DiginRecorder(digin)

        assert recorder.events() == [first]
        assert recorder.events() == [first, second]
        assert len(digin.calls) == 2

    def test_events_returns_a_copy(self) -> None:
        event = _packet(3_000, 2, sma1=1)
        recorder = DiginRecorder(_ScriptedDigin(_reply(event), _reply()))

        recorder.events().clear()

        assert recorder.events() == [event]

    def test_every_digin_call_passes_max_events(self) -> None:
        digin = _ScriptedDigin(_reply(), _reply(), _reply())
        recorder = DiginRecorder(digin, max_events=64)

        recorder.clear()
        recorder.poll()
        recorder.events()

        assert digin.calls == [64, 64, 64]

    def test_max_events_defaults_to_the_ripple_buffer_size(self) -> None:
        digin = _ScriptedDigin(_reply())

        DiginRecorder(digin).poll()

        assert digin.calls == [1024]

    def test_clear_discards_accumulated_events_and_the_ripple_buffer(self) -> None:
        old, still_buffered, new = (_packet(t, 2, sma1=1) for t in (3_000, 6_000, 9_000))
        recorder = DiginRecorder(_ScriptedDigin(_reply(old), _reply(still_buffered), _reply(new)))
        recorder.poll()

        recorder.clear()  # reads and drops `still_buffered` from Ripple's buffer

        assert recorder.events() == [new]

    def test_clear_does_not_check_for_overflow(self) -> None:
        full_buffer = [_packet(t, 2, sma1=t % 2) for t in range(1024)]
        recorder = DiginRecorder(_ScriptedDigin((5_000, full_buffer), _reply()))

        recorder.clear()

        assert recorder.events() == []

    def test_poll_raises_when_digin_reports_more_events_than_it_returned(self) -> None:
        returned = [_packet(t, 2, sma1=t % 2) for t in range(100)]
        recorder = DiginRecorder(_ScriptedDigin((1500, returned)))

        with pytest.raises(DiginOverflowError, match="reported 1500 events but returned 100"):
            recorder.poll()

    def test_poll_raises_when_digin_returns_exactly_max_events(self) -> None:
        # Ripple's buffer may have filled and silently dropped the rest.
        full_buffer = [_packet(t, 2, sma1=t % 2) for t in range(1024)]
        recorder = DiginRecorder(_ScriptedDigin((1024, full_buffer)))

        with pytest.raises(DiginOverflowError, match="may have filled"):
            recorder.poll()

    def test_poll_accepts_one_event_fewer_than_max_events(self) -> None:
        events = [_packet(t, 2, sma1=t % 2) for t in range(3)]
        recorder = DiginRecorder(_ScriptedDigin(_reply(*events), _reply()), max_events=4)

        recorder.poll()

        assert recorder.events() == events

    def test_poll_overflow_threshold_follows_max_events(self) -> None:
        events = [_packet(t, 2, sma1=t % 2) for t in range(4)]
        recorder = DiginRecorder(_ScriptedDigin(_reply(*events)), max_events=4)

        with pytest.raises(DiginOverflowError):
            recorder.poll()

    def test_events_surfaces_overflow_from_its_final_poll(self) -> None:
        returned = [_packet(t, 2, sma1=t % 2) for t in range(10)]
        recorder = DiginRecorder(_ScriptedDigin((11, returned)))

        with pytest.raises(DiginOverflowError):
            recorder.events()


# ---------------------------------------------------------------------------
# DiginRecorder.wait
# ---------------------------------------------------------------------------


class TestDiginRecorderWait:
    @staticmethod
    def _poll_times(clock: _FakeClock, seconds: float) -> list[float]:
        """Run ``wait(seconds)`` against an empty Ripple buffer; return each poll's time."""
        times: list[float] = []

        def digin(max_events: int) -> tuple[int, list[Any]]:
            times.append(clock.now)
            return 0, []

        DiginRecorder(digin).wait(seconds)
        return times

    @pytest.mark.parametrize("seconds", [0.1, 0.25, 1.0, 2.5])
    def test_polls_at_least_every_quarter_second_and_once_at_the_end(
        self, clock: _FakeClock, seconds: float
    ) -> None:
        times = self._poll_times(clock, seconds)

        assert times[-1] == pytest.approx(seconds)
        previous = 0.0
        for polled_at in times:
            assert polled_at - previous <= 0.25 + 1e-9
            previous = polled_at

    def test_waiting_zero_seconds_still_polls_once(self, clock: _FakeClock) -> None:
        assert self._poll_times(clock, 0.0) == [0.0]

    def test_accumulates_events_from_every_poll(self, clock: _FakeClock) -> None:
        first, second = _packet(3_000, 2, sma1=1), _packet(6_000, 2, sma1=0)
        recorder = DiginRecorder(_ScriptedDigin(_reply(first), _reply(second), _reply()))

        recorder.wait(0.5)  # two polls, a quarter second apart

        assert recorder.events() == [first, second]

    def test_stops_waiting_as_soon_as_a_poll_overflows(self, clock: _FakeClock) -> None:
        returned = [_packet(t, 2, sma1=t % 2) for t in range(10)]
        recorder = DiginRecorder(_ScriptedDigin((11, returned)))

        with pytest.raises(DiginOverflowError):
            recorder.wait(2.5)

        assert clock.now == pytest.approx(0.25)
