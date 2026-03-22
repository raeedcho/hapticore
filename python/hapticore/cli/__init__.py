"""Command-line entry points."""

from __future__ import annotations

import argparse
import importlib
import sys


def _simulate(args: argparse.Namespace) -> None:
    """Run a task in simulation mode with mock hardware."""
    import zmq

    from hapticore.core.config import load_config
    from hapticore.core.messaging import EventPublisher, make_ipc_address
    from hapticore.hardware.mock import MockDisplay, MockHapticInterface, MockSync
    from hapticore.tasks.controller import TaskController
    from hapticore.tasks.trial_manager import TrialManager

    config = load_config(args.config)

    # Import the task class
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

    # Create mock hardware
    haptic = MockHapticInterface()
    display = MockDisplay()
    sync = MockSync()

    # Create event publisher
    ctx = zmq.Context()
    address = make_ipc_address("sim")
    publisher = EventPublisher(ctx, address)

    # Create trial manager
    trial_manager = TrialManager(
        conditions=config.task.conditions,
        block_size=config.task.block_size,
        num_blocks=config.task.num_blocks,
        randomization=config.task.randomization,
    )

    # Create and run controller
    controller = TaskController(
        task=task,
        haptic=haptic,
        display=display,
        sync=sync,
        event_publisher=publisher,
        trial_manager=trial_manager,
        poll_rate_hz=1000.0,  # fast for simulation
    )

    try:
        controller.setup()
        controller.run()
    finally:
        controller.teardown()
        publisher.close()
        ctx.term()

    # Print summary
    summary = trial_manager.get_summary()
    print("\n=== Session Summary ===")
    print(f"Total trials: {summary['total_trials']}")
    print(f"Completed trials: {summary['completed_trials']}")
    print(f"Outcomes: {summary['outcomes']}")
    print(f"Accuracy: {summary['accuracy']:.1%}")


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
            "Error: graphviz support requires the 'graphviz' package.\n"
            "Install with: pip install graphviz\n"
            "Also install the system 'dot' binary:\n"
            "  macOS: brew install graphviz\n"
            "  Ubuntu: apt install graphviz",
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

    output = args.output or f"{class_name}.png"
    try:
        task.get_graph().draw(output, prog="dot")  # type: ignore[attr-defined]
        print(f"State machine diagram saved to: {output}")
    except Exception as e:
        print(f"Error generating diagram: {e}", file=sys.stderr)
        print("Make sure the 'dot' binary is installed on your system.")
        sys.exit(1)


def main() -> None:
    """Hapticore CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="hapticore",
        description="Hapticore experimental control system",
    )
    subparsers = parser.add_subparsers(dest="command")

    # simulate subcommand
    sim_parser = subparsers.add_parser(
        "simulate",
        help="Run a task with mock hardware",
    )
    sim_parser.add_argument(
        "--config", required=True,
        help="Path to experiment config YAML file",
    )
    sim_parser.set_defaults(func=_simulate)

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

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
