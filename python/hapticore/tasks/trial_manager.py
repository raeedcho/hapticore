"""Trial sequencing, block structure, and condition randomization.

The TrialManager takes a list of conditions and randomization settings from
the experiment config, generates a sequence of trials organized in blocks,
and provides the current condition to the task at each trial start.
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


class TrialManager:
    """Manages trial sequencing, block structure, and condition randomization.

    Given a list of conditions (dicts), a block size, number of blocks, and
    randomization strategy, generates a trial sequence and tracks progress.

    When ``num_blocks`` is ``None`` the session is open-ended: blocks are
    generated lazily in :meth:`advance` until :meth:`request_stop` is called.
    When ``num_blocks`` is a positive integer the full sequence is generated
    upfront (existing behaviour).
    """

    def __init__(
        self,
        conditions: list[dict[str, Any]],
        block_size: int,
        num_blocks: int | None,
        randomization: str = "pseudorandom",
        seed: int | None = None,
    ) -> None:
        """
        Args:
            conditions: list of condition dicts, e.g.
                ``[{"target_id": 0, "position": [0.08, 0]}, ...]``
            block_size: number of trials per block (typically == len(conditions)
                for balanced blocks)
            num_blocks: total number of blocks, or ``None`` for an open-ended
                session that runs until :meth:`request_stop` is called
            randomization: ``"pseudorandom"`` (shuffle within blocks),
                ``"sequential"`` (no shuffle), or ``"latin_square"``
                (balanced ordering across blocks)
            seed: optional random seed for reproducibility
        """
        if not conditions:
            raise ValueError("conditions must be a non-empty list")
        if block_size < 1:
            raise ValueError("block_size must be >= 1")
        if num_blocks is not None and num_blocks < 1:
            raise ValueError("num_blocks must be >= 1")

        self._conditions = list(conditions)
        self._block_size = block_size
        self._num_blocks = num_blocks
        self._randomization = randomization
        self._seed = seed
        self._rng = random.Random(seed)

        self._sequence: list[dict[str, Any]] = []
        self._trial_index: int = -1  # -1 means not started
        self._trial_log: list[dict[str, Any]] = []
        self._blocks_generated: int = 0
        self._stop_after_trial: bool = False
        self._stop_after_block: bool = False

        if self._num_blocks is not None:
            # Finite session: generate the full sequence upfront
            for _ in range(self._num_blocks):
                self._append_next_block()
        else:
            # Infinite session: generate the first block eagerly
            self._append_next_block()

    # ------------------------------------------------------------------
    # Block generation
    # ------------------------------------------------------------------

    def _append_next_block(self) -> None:
        """Generate one block and append it to the internal sequence.

        For finite sessions this is called upfront.  For infinite sessions it
        is called lazily from :meth:`advance`.  No-op when the finite limit has
        already been reached.
        """
        if self._num_blocks is not None and self._blocks_generated >= self._num_blocks:
            return

        block = self._make_block()
        block_idx = self._blocks_generated

        if self._randomization == "sequential":
            pass  # no shuffle
        elif self._randomization == "latin_square":
            if self._block_size != len(self._conditions):
                logger.warning(
                    "latin_square requires block_size == len(conditions) "
                    "(%d != %d); falling back to pseudorandom",
                    self._block_size,
                    len(self._conditions),
                )
                self._rng.shuffle(block)
            else:
                n = len(self._conditions)
                shift = block_idx % n
                block = [dict(self._conditions[(shift + i) % n]) for i in range(n)]
        else:  # pseudorandom (default)
            self._rng.shuffle(block)

        self._sequence.extend(block)
        self._blocks_generated += 1

    def _make_block(self) -> list[dict[str, Any]]:
        """Create one block of trials by cycling through conditions."""
        n = len(self._conditions)
        block: list[dict[str, Any]] = []
        for i in range(self._block_size):
            block.append(dict(self._conditions[i % n]))
        return block

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_trials(self) -> int:
        """Number of trials generated so far (grows for infinite sessions)."""
        return len(self._sequence)

    @property
    def current_trial(self) -> int:
        """Current trial index (0-based). -1 if not started."""
        return self._trial_index

    @property
    def current_block(self) -> int:
        """Current block index (0-based)."""
        if self._trial_index < 0:
            return 0
        return self._trial_index // self._block_size

    @property
    def current_condition(self) -> dict[str, Any]:
        """Condition dict for the current trial."""
        if self._trial_index < 0 or self._trial_index >= len(self._sequence):
            return {}
        return self._sequence[self._trial_index]

    @property
    def is_complete(self) -> bool:
        """True when the session is done and the current trial has been logged.

        Returns ``True`` when:

        * A stop-after-trial was requested and the current trial is logged, OR
        * A stop-after-block was requested, we are at a block boundary, and the
          current trial is logged, OR
        * ``num_blocks`` is finite and all planned trials have been run and logged.

        Returns ``False`` for open-ended sessions (``num_blocks=None``) with no
        stop requested — those sessions require an explicit :meth:`request_stop`.
        """
        # Current trial is "logged" when the trial_log is ahead of the index.
        current_trial_logged = (
            self._trial_index >= 0
            and len(self._trial_log) > self._trial_index
        )

        if self._stop_after_trial:
            return current_trial_logged

        # A block boundary occurs when the *next* trial would start a new block.
        next_index = self._trial_index + 1
        at_block_boundary = next_index > 0 and next_index % self._block_size == 0

        if self._stop_after_block and at_block_boundary:
            return current_trial_logged

        # Finite session: complete when all planned trials are run and logged.
        if self._num_blocks is not None:
            total = self._num_blocks * self._block_size
            return self._trial_index >= total - 1 and len(self._trial_log) >= total

        # Infinite session with no stop requested: never complete on its own.
        return False

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def request_stop(self, after: str = "block") -> None:
        """Request a graceful stop.

        Args:
            after: ``"block"`` — finish the current block, then stop (preserves
                balanced conditions).  ``"trial"`` — finish the current trial,
                then stop immediately (may leave an incomplete block).
        """
        if after == "trial":
            self._stop_after_trial = True
            self._stop_after_block = True  # also stop at block boundary
            logger.info("Stop requested: will stop after the current trial")
        elif after == "block":
            self._stop_after_block = True
            logger.info("Stop requested: will stop at the next block boundary")
        else:
            raise ValueError(f"after must be 'block' or 'trial', got {after!r}")

    def advance(self) -> dict[str, Any] | None:
        """Advance to the next trial.

        Returns the next condition dict, or ``None`` when the session should end
        (all trials complete, or a stop has been requested and the stopping
        condition is met).
        """
        next_index = self._trial_index + 1

        # Stop immediately after the current trial.
        if self._stop_after_trial:
            return None

        # Stop at a block boundary (next trial would start a new block).
        at_block_boundary = next_index > 0 and next_index % self._block_size == 0
        if self._stop_after_block and at_block_boundary:
            return None

        # Lazy block generation for infinite sessions.
        if next_index >= len(self._sequence) and self._num_blocks is None:
            self._append_next_block()

        if next_index >= len(self._sequence):
            # Finite limit reached (or generation failed).
            return None

        self._trial_index = next_index
        return dict(self._sequence[self._trial_index])

    def log_trial(self, outcome: str, **extra_data: Any) -> None:
        """Record the outcome and any extra data for the current trial."""
        entry: dict[str, Any] = {
            "trial_number": self._trial_index,
            "block_number": self.current_block,
            "condition": dict(self.current_condition),
            "outcome": outcome,
        }
        entry.update(extra_data)
        self._trial_log.append(entry)

    def get_trial_log(self) -> list[dict[str, Any]]:
        """Return the complete trial log as a list of dicts."""
        return list(self._trial_log)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of trial outcomes.

        Returns a dict with keys: total_trials, completed_trials,
        outcomes (count by type), accuracy (fraction of 'success' outcomes),
        stop_type ('completed', 'stopped_at_block', or 'stopped_mid_block').
        """
        outcomes: dict[str, int] = {}
        for entry in self._trial_log:
            o = entry["outcome"]
            outcomes[o] = outcomes.get(o, 0) + 1

        completed = len(self._trial_log)
        success_count = outcomes.get("success", 0)
        accuracy = success_count / completed if completed > 0 else 0.0

        if self._stop_after_trial:
            stop_type = "stopped_mid_block"
        elif self._stop_after_block:
            stop_type = "stopped_at_block"
        else:
            stop_type = "completed"

        return {
            "total_trials": self.total_trials,
            "completed_trials": completed,
            "outcomes": outcomes,
            "accuracy": accuracy,
            "stop_type": stop_type,
        }
