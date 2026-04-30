"""Command-line entry points."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys


def _run(args: argparse.Namespace) -> None:
    """Run a task against the hardware specified in the rig config."""
    import multiprocessing
    import multiprocessing.queues

    import zmq

    from hapticore.core.config import ZMQConfig, load_session_config
    from hapticore.core.messaging import EventPublisher, make_ipc_address
    from hapticore.display import make_display_interface
    from hapticore.haptic import make_haptic_interface
    from hapticore.sync import MockSync
    from hapticore.tasks.controller import TaskController
    from hapticore.tasks.trial_manager import TrialManager

    # --rig, --subject, --task are effectively required for `run`. Keep the
    # manual check so we can give a helpful error, rather than relying on
    # argparse's `required=True` which produces a less friendly message.
    if not (args.rig and args.subject and args.task):
        print(
            "Error: hapticore run requires --rig, --subject, and --task. "
            "For single-file configs, call load_config() directly in Python "
            "scripts (not supported on the CLI).",
            file=sys.stderr,
        )
        sys.exit(1)

    session_overrides: dict[str, object] = {}
    if args.experiment_name:
        session_overrides["experiment_name"] = args.experiment_name

    config = load_session_config(
        rig=args.rig, subject=args.subject, task=args.task,
        extra=args.extra_config or [],
        overrides=session_overrides or None,
    )

    # Import the task class (unchanged logic).
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

    # Validate backend+display compatibility before launching anything.
    if (
        config.haptic.backend == "dhd"
        and config.haptic.dhd is not None
        and config.haptic.dhd.mouse_input
        and config.display.backend != "psychopy"
    ):
        print(
            "Error: haptic.dhd.mouse_input=True requires display.backend='psychopy' "
            "(mouse position comes from the PsychoPy window).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Session-specific ZMQConfig with random IPC addresses so parallel
    # sessions don't collide, and so EventPublisher and DisplayProcess share
    # the same addresses.
    session_zmq = ZMQConfig(
        event_pub_address=make_ipc_address("hc_evt"),
        haptic_state_address=make_ipc_address("hc_state"),
        haptic_command_address=make_ipc_address("hc_cmd"),
        display_event_address=make_ipc_address("hc_disp"),
    )
    # For backend="dhd", override haptic addresses from the user-provided ZMQConfig
    # so the client finds the server the user launched separately.
    if config.haptic.backend == "dhd":
        session_zmq = session_zmq.model_copy(update={
            "haptic_state_address": config.zmq.haptic_state_address,
            "haptic_command_address": config.zmq.haptic_command_address,
        })

    # Mouse queue for dhd.mouse_input. None otherwise.
    mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None
    if (
        config.haptic.backend == "dhd"
        and config.haptic.dhd is not None
        and config.haptic.dhd.mouse_input
    ):
        from multiprocessing import Queue as MpQueue
        mouse_queue = MpQueue(maxsize=4)

    # ZMQ context shared between event publisher, HapticClient, and DisplayProcess.
    ctx = zmq.Context()
    publisher = EventPublisher(ctx, session_zmq.event_pub_address)

    try:
        with make_haptic_interface(
            config.haptic, session_zmq,
            context=ctx, mouse_queue=mouse_queue,
        ) as haptic, make_display_interface(
            config.display, session_zmq,
            publisher=publisher, mouse_queue=mouse_queue,
        ) as display:
            sync = MockSync()  # until Phase 5C wires SyncConfig.backend properly.

            trial_manager = TrialManager(
                conditions=config.task.conditions,
                block_size=config.task.block_size,
                num_blocks=config.task.num_blocks,
                randomization=config.task.randomization,
            )

            # --fast: override timing parameters to 1ms for smoke testing.
            param_overrides = dict(config.task.params) if config.task.params else {}
            if args.fast:
                for name, spec in task.PARAMS.items():
                    if spec.unit == "s" and spec.type is float:
                        param_overrides[name] = 0.001

            controller = TaskController(
                task=task, haptic=haptic, display=display, sync=sync,
                event_publisher=publisher, trial_manager=trial_manager,
                params=param_overrides or None,
                poll_rate_hz=1000.0,
            )
            try:
                controller.setup()
                controller.run()
            finally:
                controller.teardown()
    finally:
        publisher.close()
        ctx.term()

    summary = trial_manager.get_summary()
    print("\n=== Session Summary ===")
    print(f"Total trials: {summary['total_trials']}")
    print(f"Completed trials: {summary['completed_trials']}")
    print(f"Outcomes: {summary['outcomes']}")
    print(f"Accuracy: {summary['accuracy']:.1%}")


def _list_screens(args: argparse.Namespace) -> None:
    """List available screens with their indices and resolutions."""
    try:
        import pyglet
    except ImportError:
        print(
            "Error: list-screens requires the 'display' pixi environment.\n"
            "Run with: pixi run -e display hapticore list-screens",
            file=sys.stderr,
        )
        sys.exit(1)

    screens = pyglet.canvas.get_display().get_screens()
    print(f"{'Index':<6} {'Resolution':<15} {'Position':<15}")
    for i, s in enumerate(screens):
        res = f"{s.width}x{s.height}"
        print(f"{i:<6} {res:<15} ({s.x}, {s.y})")


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
        "--task",
        help="Path to task config YAML (task_class, params, conditions)",
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

    # list-screens subcommand
    list_parser = subparsers.add_parser(
        "list-screens",
        help="List available monitors (requires the display pixi environment)",
    )
    list_parser.set_defaults(func=_list_screens)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
