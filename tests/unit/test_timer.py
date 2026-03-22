"""Tests for TimerManager."""

from __future__ import annotations

import time

from hapticore.tasks.timer import TimerManager


class TestTimerManager:
    def test_zero_delay_expires_immediately(self) -> None:
        timer = TimerManager()
        timer.set("x", 0.0)
        expired = timer.check()
        assert "x" in expired

    def test_delayed_timer(self) -> None:
        timer = TimerManager()
        timer.set("x", 0.05)
        assert timer.check() == []
        time.sleep(0.08)
        expired = timer.check()
        assert "x" in expired

    def test_cancel_removes_timer(self) -> None:
        timer = TimerManager()
        timer.set("x", 0.05)
        assert timer.cancel("x") is True
        time.sleep(0.08)
        assert timer.check() == []

    def test_cancel_nonexistent(self) -> None:
        timer = TimerManager()
        assert timer.cancel("nonexistent") is False

    def test_cancel_all(self) -> None:
        timer = TimerManager()
        timer.set("a", 0.05)
        timer.set("b", 0.05)
        timer.set("c", 0.05)
        timer.cancel_all()
        time.sleep(0.08)
        assert timer.check() == []

    def test_is_active_pending(self) -> None:
        timer = TimerManager()
        timer.set("x", 1.0)
        assert timer.is_active("x") is True

    def test_is_active_after_cancel(self) -> None:
        timer = TimerManager()
        timer.set("x", 1.0)
        timer.cancel("x")
        assert timer.is_active("x") is False

    def test_is_active_after_expiry(self) -> None:
        timer = TimerManager()
        timer.set("x", 0.0)
        timer.check()
        assert timer.is_active("x") is False

    def test_is_active_nonexistent(self) -> None:
        timer = TimerManager()
        assert timer.is_active("x") is False

    def test_replace_timer(self) -> None:
        timer = TimerManager()
        timer.set("x", 1.0)
        timer.set("x", 0.0)  # replace with immediate
        expired = timer.check()
        assert "x" in expired

    def test_multiple_independent_timers(self) -> None:
        timer = TimerManager()
        timer.set("fast", 0.0)
        timer.set("slow", 1.0)
        expired = timer.check()
        assert "fast" in expired
        assert "slow" not in expired
        assert timer.is_active("slow") is True

    def test_active_count(self) -> None:
        timer = TimerManager()
        assert timer.active_count == 0
        timer.set("a", 1.0)
        assert timer.active_count == 1
        timer.set("b", 1.0)
        assert timer.active_count == 2
        timer.cancel("a")
        assert timer.active_count == 1

    def test_timer_accuracy(self) -> None:
        """Timer fires within 5ms of target on a non-real-time OS."""
        timer = TimerManager()
        delay = 0.010  # 10 ms
        timer.set("t", delay)
        start = time.monotonic()
        while True:
            expired = timer.check()
            if expired:
                actual = time.monotonic() - start
                # Timer should not fire early and should be within 5ms of target
                assert actual >= delay - 0.001  # allow 1ms clock jitter
                assert actual - delay < 0.005   # within 5ms of target
                break
            if time.monotonic() - start > 0.1:
                raise AssertionError("Timer did not expire within 100ms")
