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
    randomization strategy, generates a full trial sequence and tracks progress.
    """

    def __init__(
        self,
        conditions: list[dict[str, Any]],
        block_size: int,
        num_blocks: int,
        randomization: str = "pseudorandom",
        seed: int | None = None,
    ) -> None:
        """
        Args:
            conditions: list of condition dicts, e.g.
                ``[{"target_id": 0, "position": [0.08, 0]}, ...]``
            block_size: number of trials per block (typically == len(conditions)
                for balanced blocks)
            num_blocks: total number of blocks
            randomization: ``"pseudorandom"`` (shuffle within blocks),
                ``"sequential"`` (no shuffle), or ``"latin_square"``
                (balanced ordering across blocks)
            seed: optional random seed for reproducibility
        """
        if not conditions:
            raise ValueError("conditions must be a non-empty list")
        if block_size < 1:
            raise ValueError("block_size must be >= 1")
        if num_blocks < 1:
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

        self._sequence = self.generate_sequence()

    def generate_sequence(self) -> list[dict[str, Any]]:
        """Generate the full trial sequence.

        For ``"pseudorandom"``: each block contains ``block_size`` trials drawn
        from ``conditions`` (cycling if block_size > len(conditions)), shuffled
        within the block.

        For ``"sequential"``: conditions repeat in order without shuffling.

        For ``"latin_square"``: balanced Latin square ordering across blocks.
        Each condition appears in each ordinal position across blocks.
        (If block_size != len(conditions), fall back to pseudorandom with a warning.)

        Returns the full list of condition dicts in trial order.
        """
        sequence: list[dict[str, Any]] = []

        if self._randomization == "sequential":
            for _block in range(self._num_blocks):
                block = self._make_block()
                sequence.extend(block)

        elif self._randomization == "latin_square":
            if self._block_size != len(self._conditions):
                logger.warning(
                    "latin_square requires block_size == len(conditions) "
                    "(%d != %d); falling back to pseudorandom",
                    self._block_size,
                    len(self._conditions),
                )
                return self._generate_pseudorandom()
            sequence = self._generate_latin_square()

        else:  # pseudorandom (default)
            sequence = self._generate_pseudorandom()

        return sequence

    def _make_block(self) -> list[dict[str, Any]]:
        """Create one block of trials by cycling through conditions."""
        n = len(self._conditions)
        block: list[dict[str, Any]] = []
        for i in range(self._block_size):
            block.append(dict(self._conditions[i % n]))
        return block

    def _generate_pseudorandom(self) -> list[dict[str, Any]]:
        """Generate pseudorandom sequence: shuffle within each block."""
        sequence: list[dict[str, Any]] = []
        for _block in range(self._num_blocks):
            block = self._make_block()
            self._rng.shuffle(block)
            sequence.extend(block)
        return sequence

    def _generate_latin_square(self) -> list[dict[str, Any]]:
        """Generate a balanced Latin square ordering across blocks."""
        n = len(self._conditions)
        sequence: list[dict[str, Any]] = []
        # Create a basic Latin square: each row is a cyclic shift
        for block_idx in range(self._num_blocks):
            shift = block_idx % n
            block = [dict(self._conditions[(shift + i) % n]) for i in range(n)]
            sequence.extend(block)
        return sequence

    @property
    def total_trials(self) -> int:
        """Total number of trials in the session."""
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
        """True if all trials have been advanced through AND the last trial has been logged."""
        return (
            self._trial_index >= len(self._sequence) - 1
            and len(self._trial_log) >= len(self._sequence)
        )

    def advance(self) -> dict[str, Any] | None:
        """Advance to the next trial.

        Returns the next condition dict, or None if all trials are complete.
        """
        next_index = self._trial_index + 1
        if next_index >= len(self._sequence):
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
        outcomes (count by type), accuracy (fraction of 'success' outcomes).
        """
        outcomes: dict[str, int] = {}
        for entry in self._trial_log:
            o = entry["outcome"]
            outcomes[o] = outcomes.get(o, 0) + 1

        completed = len(self._trial_log)
        success_count = outcomes.get("success", 0)
        accuracy = success_count / completed if completed > 0 else 0.0

        return {
            "total_trials": self.total_trials,
            "completed_trials": completed,
            "outcomes": outcomes,
            "accuracy": accuracy,
        }
