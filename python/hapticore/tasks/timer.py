"""Non-blocking timer manager using time.monotonic().

The TimerManager provides soft-real-time timer scheduling. It does NOT use
time.sleep(), threading.Timer, or any blocking mechanism. Instead, the task
controller's main loop calls timer.check() on each iteration, and the
TimerManager returns any timer names whose deadlines have passed.
"""

from __future__ import annotations

import time


class TimerManager:
    """Non-blocking timer manager using time.monotonic().

    Usage in the main loop::

        timer = TimerManager()
        timer.set("hold_complete", delay=0.5)  # fires trigger in 0.5s
        ...
        # In the main loop:
        expired = timer.check()
        for trigger_name in expired:
            task.trigger(trigger_name)
    """

    def __init__(self) -> None:
        self._timers: dict[str, float] = {}  # name → deadline (monotonic time)

    def set(self, name: str, delay: float) -> None:
        """Schedule a named timer to expire after *delay* seconds from now.

        If a timer with the same name already exists, it is replaced.
        """
        self._timers[name] = time.monotonic() + delay

    def cancel(self, name: str) -> bool:
        """Cancel a named timer. Returns True if the timer existed."""
        return self._timers.pop(name, None) is not None

    def cancel_all(self) -> None:
        """Cancel all active timers."""
        self._timers.clear()

    def is_active(self, name: str) -> bool:
        """Check if a named timer is currently active (not yet expired)."""
        return name in self._timers

    def check(self) -> list[str]:
        """Check all timers against the current time.

        Returns a list of expired timer names (in no guaranteed order).
        Expired timers are automatically removed.
        """
        now = time.monotonic()
        expired = [name for name, deadline in self._timers.items() if now >= deadline]
        for name in expired:
            del self._timers[name]
        return expired

    @property
    def active_count(self) -> int:
        """Number of currently active (pending) timers."""
        return len(self._timers)
