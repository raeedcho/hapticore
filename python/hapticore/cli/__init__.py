"""Command-line entry points."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys


def _run(args: argparse.Namespace) -> None:
    """Run a task against the hardware specified in the rig config."""
    from hapticore.core.config import load_session_config
    from hapticore.session import SessionManager
    from hapticore.tasks.controller import TaskController

    # --rig, --subject, --experiment are effectively required for `run`. Keep the
    # manual check so we can give a helpful error, rather than relying on
    # argparse's `required=True` which produces a less friendly message.
    if not (args.rig and args.subject and args.experiment):
        print(
            "Error: hapticore run requires --rig, --subject, and --experiment. "
            "For single-file configs, call load_config() directly in Python "
            "scripts (not supported on the CLI).",
            file=sys.stderr,
        )
        sys.exit(1)

    session_overrides: dict[str, object] = {}
    if args.experiment_name:
        session_overrides["experiment_name"] = args.experiment_name

    config = load_session_config(
        rig=args.rig, subject=args.subject, experiment=args.experiment,
        extra=args.extra_config or [],
        overrides=session_overrides or None,
    )

    # Import the task class.
    task_class_path = config.task.task_class
    if "." not in task_class_path:
        print(
            f"Error: task_class must be a dotted path "
            f"(e.g. 'hapticore.tasks.center_out.CenterOutTask'), "
            f"got '{task_class_path}'",
            file=sys.stderr,
        )
        sys.exit(1)
    module_path, class_name = task_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    task_cls = getattr(module, class_name)
    task = task_cls()

    try:
        with SessionManager(config) as session:
            # --fast: override timing parameters to 1ms for smoke testing.
            param_overrides = dict(config.task.params) if config.task.params else {}
            if args.fast:
                for name, spec in task.PARAMS.items():
                    if spec.unit == "s" and spec.type is float:
                        param_overrides[name] = 0.001

            controller = TaskController(
                task=task,
                haptic=session.haptic,
                display=session.display,
                sync=session.sync,
                event_publisher=session.publisher,
                trial_manager=session.trial_manager,
                params=param_overrides or None,
                poll_rate_hz=1000.0,
            )
            try:
                controller.setup()
                session.start_recording(active_params=dict(task.params))
                controller.run()
            finally:
                session.stop_recording()
                controller.teardown()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSession receipt: {session.session_dir}/session_receipt.json")


def _graph_task(args: argparse.Namespace) -> None:
    """Generate a state machine diagram for a task class."""
    # Import the task class
    task_class_path = args.task_class
    if "." not in task_class_path:
        print(
            f"Error: task_class must be a dotted path "
            f"(e.g. 'hapticore.tasks.center_out.CenterOutTask'), "
            f"got '{task_class_path}'",
            file=sys.stderr,
        )
        sys.exit(1)
    module_path, class_name = task_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    task_cls = getattr(module, class_name)
    task = task_cls()

    try:
        from transitions.extensions import GraphMachine
    except ImportError:
        print(
            "Error: diagram support requires 'pygraphviz'.\n"
            "Install with: pip install 'hapticore[diagrams]'\n"
            "Also install the system graphviz development libraries:\n"
            "  macOS: brew install graphviz\n"
            "  Ubuntu: apt install graphviz libgraphviz-dev",
            file=sys.stderr,
        )
        sys.exit(1)

    GraphMachine(
        model=task,
        states=task_cls.STATES,
        transitions=task_cls.TRANSITIONS,
        initial=task_cls.INITIAL_STATE,
        show_conditions=True,
    )

    output = args.output or f"{class_name}.svg"
    try:
        # Ensure graphviz plugins are registered (needed for conda/pixi installs)
        subprocess.run(["dot", "-c"], capture_output=True)
        task.get_graph().draw(output, prog="dot")  # type: ignore[attr-defined]
        print(f"State machine diagram saved to: {output}")
    except Exception as e:
        print(f"Error generating diagram: {e}", file=sys.stderr)
        print("Make sure the 'dot' binary is installed on your system.")
        sys.exit(1)


def _gui(args: argparse.Namespace) -> None:
    """Launch the Hapticore Control Center GUI."""
    from hapticore.control.app import run_control_center
    sys.exit(run_control_center())


def main() -> None:
    """Hapticore CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="hapticore",
        description="Hapticore experimental control system",
    )
    subparsers = parser.add_subparsers(dest="command")

    # run subcommand
    run_parser = subparsers.add_parser(
        "run",
        help="Run a task against the hardware specified in the rig config",
    )
    run_parser.add_argument(
        "--experiment-name",
        help="Name for this experiment session (overrides YAML value)",
    )
    run_parser.add_argument(
        "--rig",
        help="Path to rig config YAML (haptic, display, sync, ZMQ settings)",
    )
    run_parser.add_argument(
        "--subject",
        help="Path to subject config YAML (subject_id, species, implant_info)",
    )
    run_parser.add_argument(
        "--experiment",
        help="Path to experiment config YAML (experiment_name + task)",
    )
    run_parser.add_argument(
        "--extra-config", nargs="*", default=[],
        help="Additional YAML files merged on top (later files win)",
    )
    run_parser.add_argument(
        "--fast", action="store_true",
        help="Override all timing parameters to 1ms for quick smoke-testing",
    )
    run_parser.set_defaults(func=_run)

    # graph-task subcommand
    graph_parser = subparsers.add_parser(
        "graph-task",
        help="Generate a state machine diagram",
    )
    graph_parser.add_argument(
        "task_class",
        help="Dotted path to task class (e.g. hapticore.tasks.center_out.CenterOutTask)",
    )
    graph_parser.add_argument(
        "--output", "-o",
        help="Output file path (default: <ClassName>.png)",
    )
    graph_parser.set_defaults(func=_graph_task)

    # gui subcommand
    gui_parser = subparsers.add_parser(
        "gui",
        help="Launch the Hapticore Control Center GUI",
    )
    gui_parser.set_defaults(func=_gui)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
