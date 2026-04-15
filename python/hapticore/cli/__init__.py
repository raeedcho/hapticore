"""Command-line entry points."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys


def _simulate(args: argparse.Namespace) -> None:
    """Run a task in simulation mode with mock hardware."""
    import multiprocessing
    import multiprocessing.queues
    import time

    import zmq

    from hapticore.core.config import load_config, load_session_config
    from hapticore.core.messaging import EventPublisher, make_ipc_address
    from hapticore.hardware.mock import MockDisplay, MockHapticInterface, MockSync
    from hapticore.tasks.controller import TaskController
    from hapticore.tasks.trial_manager import TrialManager

    # Build overrides dict
    session_overrides: dict[str, object] = {}
    if args.experiment_name:
        session_overrides["experiment_name"] = args.experiment_name

    if args.config:
        # Backward-compatible single flat file mode
        config = load_config(
            args.config,
            overrides=session_overrides or None,
        )
    elif args.rig and args.subject and args.task:
        # Layered mode with required rig/subject/task arguments
        config = load_session_config(
            rig=args.rig,
            subject=args.subject,
            task=args.task,
            extra=args.extra_config or [],
            overrides=session_overrides or None,
        )
    else:
        print(
            "Error: provide either --config for a flat YAML file, or "
            "all three of --rig, --subject, and --task for layered configs.",
            file=sys.stderr,
        )
        sys.exit(1)

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

    # Create hardware for simulation
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None
    if args.input == "mouse":
        if not args.display:
            print(
                "Error: --input mouse requires --display (mouse position "
                "comes from the PsychoPy window)",
                file=sys.stderr,
            )
            sys.exit(1)

        from multiprocessing import Queue as MpQueue

        from hapticore.hardware.mouse_haptic import MouseHapticInterface

        # Buffer a few frames of mouse positions (~4 frames at 60 Hz).
        # Consumer drains the queue and keeps only the latest value.
        mouse_queue = MpQueue(maxsize=4)
        haptic = MouseHapticInterface(mouse_queue=mouse_queue)
    else:
        haptic = MockHapticInterface()
    sync = MockSync()

    display_proc = None
    if args.display:
        # Build a session-specific ZMQConfig with random IPC addresses so
        # that parallel sessions don't collide and, critically, so the
        # EventPublisher and DisplayProcess share the *same* addresses.
        from hapticore.core.config import ZMQConfig
        from hapticore.display.display_client import DisplayClient
        from hapticore.display.process import DisplayProcess

        session_zmq = ZMQConfig(
            event_pub_address=make_ipc_address("sim_evt"),
            haptic_state_address=make_ipc_address("sim_state"),
            display_event_address=make_ipc_address("sim_disp"),
        )
        display_proc = DisplayProcess(
            config.display, session_zmq, headless=False, mouse_queue=mouse_queue,
        )
        display_proc.start()
        time.sleep(1.5)  # let PsychoPy create the window (~1s on macOS)

    # Create event publisher — use the session ZMQ config so commands
    # reach the DisplayProcess subscriber (when --display is active).
    ctx = zmq.Context()
    address = (
        session_zmq.event_pub_address if args.display else make_ipc_address("sim")
    )
    publisher = EventPublisher(ctx, address)

    if args.display:
        display: MockDisplay | DisplayClient = DisplayClient(publisher)
    else:
        display = MockDisplay()

    # Create trial manager
    trial_manager = TrialManager(
        conditions=config.task.conditions,
        block_size=config.task.block_size,
        num_blocks=config.task.num_blocks,
        randomization=config.task.randomization,
    )

    # Create and run controller
    # In --fast mode, override all timing parameters to 1ms for quick smoke-testing
    param_overrides = dict(config.task.params) if config.task.params else {}
    if args.fast:
        for name, spec in task.PARAMS.items():
            if spec.unit == "s" and spec.type is float:
                param_overrides[name] = 0.001

    controller = TaskController(
        task=task,
        haptic=haptic,
        display=display,
        sync=sync,
        event_publisher=publisher,
        trial_manager=trial_manager,
        params=param_overrides or None,
        poll_rate_hz=1000.0,  # fast for simulation
    )

    try:
        controller.setup()
        controller.run()
    finally:
        controller.teardown()
        if display_proc is not None:
            display_proc.request_shutdown()
            display_proc.join(timeout=5.0)
            if display_proc.is_alive():
                display_proc.terminate()
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
        "--experiment-name",
        help="Name for this experiment session (overrides YAML value)",
    )
    # Layered config mode (preferred)
    sim_parser.add_argument(
        "--rig",
        help="Path to rig config YAML (haptic, display, sync, ZMQ settings)",
    )
    sim_parser.add_argument(
        "--subject",
        help="Path to subject config YAML (subject_id, species, implant_info)",
    )
    sim_parser.add_argument(
        "--task",
        help="Path to task config YAML (task_class, params, conditions)",
    )
    sim_parser.add_argument(
        "--extra-config", nargs="*", default=[],
        help="Additional YAML files merged on top (e.g., overrides)",
    )
    # Backward-compatible flat config mode
    sim_parser.add_argument(
        "--config",
        help="Path to a single flat experiment config YAML (skips layer validation)",
    )
    sim_parser.add_argument(
        "--fast", action="store_true",
        help="Override all timing parameters to 1ms for quick smoke-testing",
    )
    sim_parser.add_argument(
        "--display", action="store_true",
        help="Launch a real PsychoPy display process (requires display environment)",
    )
    sim_parser.add_argument(
        "--input",
        choices=["mock", "mouse"],
        default="mock",
        help="Position source for simulation. 'mock' = stationary origin, "
             "'mouse' = live mouse cursor (requires --display)",
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
