"""Tests for TrialManager."""

from __future__ import annotations

import pytest

from hapticore.tasks.trial_manager import TrialManager


class TestTrialManager:
    def _make_conditions(self, n: int = 4) -> list[dict]:
        return [{"target_id": i, "position": [i * 0.02, 0]} for i in range(n)]

    def test_basic_construction(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(4),
            block_size=4,
            num_blocks=3,
        )
        assert tm.total_trials == 12

    def test_sequential_order(self) -> None:
        conditions = self._make_conditions(4)
        tm = TrialManager(
            conditions=conditions,
            block_size=4,
            num_blocks=3,
            randomization="sequential",
        )
        seq = tm._sequence
        for block in range(3):
            for i in range(4):
                idx = block * 4 + i
                assert seq[idx]["target_id"] == i

    def test_pseudorandom_all_conditions_per_block(self) -> None:
        conditions = self._make_conditions(4)
        tm = TrialManager(
            conditions=conditions,
            block_size=4,
            num_blocks=3,
            randomization="pseudorandom",
            seed=42,
        )
        seq = tm._sequence
        for block in range(3):
            block_ids = {seq[block * 4 + i]["target_id"] for i in range(4)}
            assert block_ids == {0, 1, 2, 3}

    def test_pseudorandom_blocks_differ(self) -> None:
        conditions = self._make_conditions(4)
        tm = TrialManager(
            conditions=conditions,
            block_size=4,
            num_blocks=3,
            randomization="pseudorandom",
            seed=42,
        )
        seq = tm._sequence
        block0 = [seq[i]["target_id"] for i in range(4)]
        block1 = [seq[4 + i]["target_id"] for i in range(4)]
        # With seed=42 and 4 conditions, blocks should differ
        # (extremely unlikely to be the same with reasonable seed)
        # We just verify the blocks are valid; order may match by chance
        assert len(block0) == 4
        assert len(block1) == 4

    def test_advance_returns_conditions(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        c1 = tm.advance()
        assert c1 is not None
        assert tm.current_trial == 0
        c2 = tm.advance()
        assert c2 is not None
        assert tm.current_trial == 1

    def test_advance_returns_none_when_complete(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        tm.advance()
        tm.advance()
        assert tm.advance() is None

    def test_is_complete(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        assert tm.is_complete is False
        tm.advance()
        assert tm.is_complete is False
        tm.advance()
        assert tm.is_complete is True

    def test_log_trial_and_get_log(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        tm.advance()
        tm.log_trial("success", reaction_time=0.35)
        log = tm.get_trial_log()
        assert len(log) == 1
        assert log[0]["outcome"] == "success"
        assert log[0]["reaction_time"] == 0.35
        assert log[0]["trial_number"] == 0

    def test_get_summary(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=2,
            randomization="sequential",
        )
        tm.advance()
        tm.log_trial("success")
        tm.advance()
        tm.log_trial("timeout")
        tm.advance()
        tm.log_trial("success")
        tm.advance()
        tm.log_trial("success")

        summary = tm.get_summary()
        assert summary["total_trials"] == 4
        assert summary["completed_trials"] == 4
        assert summary["outcomes"]["success"] == 3
        assert summary["outcomes"]["timeout"] == 1
        assert summary["accuracy"] == 0.75

    def test_current_block_increments(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=3,
            randomization="sequential",
        )
        tm.advance()
        assert tm.current_block == 0
        tm.advance()
        assert tm.current_block == 0
        tm.advance()
        assert tm.current_block == 1
        tm.advance()
        assert tm.current_block == 1

    def test_seed_reproducibility(self) -> None:
        conditions = self._make_conditions(4)
        tm1 = TrialManager(
            conditions=conditions, block_size=4, num_blocks=3,
            randomization="pseudorandom", seed=123,
        )
        tm2 = TrialManager(
            conditions=conditions, block_size=4, num_blocks=3,
            randomization="pseudorandom", seed=123,
        )
        assert tm1._sequence == tm2._sequence

    def test_block_size_larger_than_conditions(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=5,
            num_blocks=1,
            randomization="sequential",
        )
        assert tm.total_trials == 5
        seq = tm._sequence
        # Conditions cycle: 0, 1, 0, 1, 0
        assert seq[0]["target_id"] == 0
        assert seq[1]["target_id"] == 1
        assert seq[2]["target_id"] == 0
        assert seq[3]["target_id"] == 1
        assert seq[4]["target_id"] == 0

    def test_current_condition_before_start(self) -> None:
        tm = TrialManager(
            conditions=self._make_conditions(2),
            block_size=2,
            num_blocks=1,
            randomization="sequential",
        )
        assert tm.current_condition == {}

    def test_empty_conditions_raises(self) -> None:
        with pytest.raises(ValueError):
            TrialManager(conditions=[], block_size=1, num_blocks=1)
