"""Integration tests for Hapticore CLI commands."""

from __future__ import annotations

import os
import tempfile
import time
from argparse import Namespace
from pathlib import Path

import pytest


class TestCLIGraphTask:
    """Tests for the graph-task subcommand."""

    def test_graph_task_produces_valid_png(self) -> None:
        """Verify graph-task outputs a real PNG, not DOT source text."""
        from hapticore.cli import _graph_task

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            output_path = f.name

        try:
            args = Namespace(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                output=output_path,
            )
            _graph_task(args)

            with open(output_path, "rb") as f:
                header = f.read(8)
            assert header[:4] == b"\x89PNG", "Output is not a valid PNG file"
        finally:
            os.unlink(output_path)

    def test_graph_task_produces_svg(self) -> None:
        """Verify graph-task can output SVG format."""
        from hapticore.cli import _graph_task

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            output_path = f.name

        try:
            args = Namespace(
                task_class="hapticore.tasks.center_out.CenterOutTask",
                output=output_path,
            )
            _graph_task(args)

            with open(output_path, "r") as f:
                content = f.read()
            assert "<svg" in content, "Output is not a valid SVG file"
        finally:
            os.unlink(output_path)


class TestCLISimulate:
    """Integration tests for the CLI simulate command."""

    def test_fast_simulation_completes_quickly(self) -> None:
        """End-to-end: _simulate with --fast and --config finishes in seconds."""
        from hapticore.cli import _simulate

        config_path = Path(__file__).parents[2] / "configs" / "example_config.yaml"
        args = Namespace(
            config=str(config_path),
            rig=None, subject=None, task=None, extra_config=[],
            experiment_name=None, fast=True, display=False, input="mock",
        )

        start = time.monotonic()
        _simulate(args)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, (
            f"Fast simulation took {elapsed:.1f}s — timing overrides "
            f"are probably not being applied"
        )

    def test_fast_simulation_layered_mode(self) -> None:
        """End-to-end: _simulate with --rig/--subject/--task layered configs."""
        from hapticore.cli import _simulate

        configs = Path(__file__).parents[2] / "configs"
        args = Namespace(
            config=None,
            rig=str(configs / "rig" / "default.yaml"),
            subject=str(configs / "subject" / "example_subject.yaml"),
            task=str(configs / "task" / "center_out.yaml"),
            extra_config=[str(configs / "example_experiment.yaml")],
            experiment_name=None, fast=True, display=False, input="mock",
        )

        start = time.monotonic()
        _simulate(args)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, (
            f"Fast simulation took {elapsed:.1f}s — timing overrides "
            f"are probably not being applied"
        )

    def test_simulate_mouse_without_display_fails(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--input mouse requires --display; running without it must exit(1)."""
        from hapticore.cli import _simulate

        config_path = Path(__file__).parents[2] / "configs" / "example_config.yaml"
        args = Namespace(
            config=str(config_path),
            rig=None, subject=None, task=None, extra_config=[],
            experiment_name=None, fast=False, display=False, input="mouse",
        )
        with pytest.raises(SystemExit) as exc_info:
            _simulate(args)
        assert exc_info.value.code == 1
        assert "requires --display" in capsys.readouterr().err

