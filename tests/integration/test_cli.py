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


class TestCLIRun:
    """Integration tests for the CLI run command."""

    def test_fast_run_completes_quickly(self) -> None:
        """End-to-end: _run with --fast finishes in seconds via the factory path."""
        from hapticore.cli import _run

        configs = Path(__file__).parents[2] / "configs"
        args = Namespace(
            rig=str(configs / "rig" / "ci.yaml"),
            subject=str(configs / "subject" / "example_subject.yaml"),
            task=str(configs / "task" / "center_out.yaml"),
            extra_config=[str(configs / "example_experiment.yaml")],
            experiment_name=None, fast=True,
        )

        start = time.monotonic()
        _run(args)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, (
            f"Fast run took {elapsed:.1f}s — timing overrides "
            f"are probably not being applied"
        )

    def test_run_mouse_haptic_with_mock_display_fails(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path,
    ) -> None:
        """haptic.backend='mouse' + display.backend='mock' must exit(1)."""
        from hapticore.cli import _run

        configs = Path(__file__).parents[2] / "configs"
        override = tmp_path / "force_mock_display.yaml"
        override.write_text("display:\n  backend: mock\n")

        args = Namespace(
            rig=str(configs / "rig" / "dev-mouse.yaml"),
            subject=str(configs / "subject" / "example_subject.yaml"),
            task=str(configs / "task" / "center_out.yaml"),
            extra_config=[str(override), str(configs / "example_experiment.yaml")],
            experiment_name=None, fast=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run(args)
        assert exc_info.value.code == 1
        assert "haptic.backend='mouse'" in capsys.readouterr().err

    def test_run_without_rig_layers_fails(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Running without --rig/--subject/--task must exit(1) with a helpful message."""
        from hapticore.cli import _run

        args = Namespace(
            rig=None, subject=None, task=None, extra_config=[],
            experiment_name=None, fast=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            _run(args)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "--rig" in err
        assert "load_config" in err  # points to the Python-side escape hatch

