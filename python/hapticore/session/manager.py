"""SessionManager — recording orchestration and session lifecycle.

Owns all session infrastructure: ZMQ context, EventPublisher,
session-specific ZMQ addresses, mouse queue, hardware factories
(haptic, display, sync), recording subprocess lifecycle, session
directory, and session receipt.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import json
import logging
import multiprocessing
import multiprocessing.queues
import re
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import zmq

from hapticore.core.config import ExperimentConfig, ZMQConfig
from hapticore.core.interfaces import DisplayInterface, HapticInterface, SyncInterface
from hapticore.core.messages import TOPIC_SESSION, SessionControl, serialize
from hapticore.core.messaging import EventPublisher
from hapticore.dashboard.status_dashboard import StatusDashboardProcess
from hapticore.dashboard.workspace_mirror import WorkspaceMirrorProcess
from hapticore.datalog import DataLoggerProcess
from hapticore.display import make_display_interface
from hapticore.haptic import make_haptic_interface
from hapticore.recording.ripple_process import RippleProcess
from hapticore.sync import make_sync_interface
from hapticore.tasks.trial_manager import TrialManager

logger = logging.getLogger(__name__)

# Time to wait for RippleProcess to open the xipppy connection and subscribe
# to ZMQ before declaring it ready.
_RIPPLE_READY_TIMEOUT_S = 10.0

# Polling interval when waiting for RippleProcess to become ready.
_RIPPLE_READY_POLL_INTERVAL_S = 0.1

# Brief grace period after readiness for ZMQ subscription propagation.
_RIPPLE_SUBSCRIPTION_GRACE_S = 0.05

# Shutdown timeouts.
_RIPPLE_SHUTDOWN_TIMEOUT_S = 5.0
_RIPPLE_TERMINATE_JOIN_TIMEOUT_S = 2.0

# DataLoggerProcess timeouts.
_DATA_LOGGER_READY_TIMEOUT_S = 5.0
_DATA_LOGGER_READY_POLL_INTERVAL_S = 0.1
_DATA_LOGGER_SUBSCRIPTION_GRACE_S = 0.05
_DATA_LOGGER_SHUTDOWN_TIMEOUT_S = 3.0
_DATA_LOGGER_TERMINATE_JOIN_TIMEOUT_S = 2.0

# WorkspaceMirrorProcess timeouts.
_WORKSPACE_MIRROR_READY_TIMEOUT_S = 10.0  # PsychoPy window creation can be slow
_WORKSPACE_MIRROR_READY_POLL_INTERVAL_S = 0.1
_WORKSPACE_MIRROR_SUBSCRIPTION_GRACE_S = 0.05
_WORKSPACE_MIRROR_SHUTDOWN_TIMEOUT_S = 5.0
_WORKSPACE_MIRROR_TERMINATE_JOIN_TIMEOUT_S = 2.0

# StatusDashboardProcess timeouts.
_STATUS_DASHBOARD_READY_TIMEOUT_S = 10.0
_STATUS_DASHBOARD_READY_POLL_INTERVAL_S = 0.1
_STATUS_DASHBOARD_SUBSCRIPTION_GRACE_S = 0.05
_STATUS_DASHBOARD_SHUTDOWN_TIMEOUT_S = 5.0
_STATUS_DASHBOARD_TERMINATE_JOIN_TIMEOUT_S = 2.0


@dataclasses.dataclass
class RecordingSegment:
    """State for an active recording segment.

    Created by ``SessionManager.start_recording()``, read by
    ``stop_recording()`` and ``_write_segment_receipt()``, then
    discarded (set to None).
    """

    label: str
    directory: Path
    active_params: dict[str, Any]
    start_utc: datetime.datetime
    start_trial: int
    trellis_file_name_base: str | None = None

    def to_metadata(self, end_utc: datetime.datetime, end_trial: int) -> dict[str, Any]:
        """Build metadata dict for the session receipt's segments list."""
        return {
            "label": self.label,
            "path": str(self.directory),
            "timing": {
                "start_utc": self.start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "duration_s": (end_utc - self.start_utc).total_seconds(),
            },
            "trial_range": [self.start_trial, end_trial],
            "active_params": self.active_params,
        }


class SessionManager:
    """Orchestrates all session infrastructure and lifecycle.

    Lifecycle methods map to distinct phases:

    - ``start()`` / ``__enter__``: Create ZMQ infrastructure, launch
      hardware interfaces (haptic, display, sync) via ExitStack, launch
      recording subprocesses (RippleProcess if configured), create session
      directory, wait for process readiness.
    - ``start_recording()``: Publish start_sync, start_camera_trigger,
      start_recording to the event bus. SyncProcess and RippleProcess
      act on these.
    - ``stop_recording()``: Publish stops in reverse order.
    - ``stop()`` / ``__exit__``: Shut down recording subprocesses,
      close hardware interfaces, clean up ZMQ infrastructure, write
      session receipt JSON.

    Separating process lifecycle from recording lifecycle supports
    test-before-record workflows: start() launches everything, the
    experimenter runs test trials, then start_recording() begins the
    real session.

    Args:
        config: The full ExperimentConfig (needed for subject info,
            recording config, and config snapshot in the receipt).
        xipppy_module: Optional fake xipppy module for testing.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        *,
        xipppy_module: ModuleType | None = None,
    ) -> None:
        self._config = config
        self._xipppy_module = xipppy_module

        self._trial_manager = TrialManager(
            conditions=config.task.conditions,
            block_size=config.task.block_size,
            num_blocks=config.task.num_blocks,
            randomization=config.task.randomization,
        )

        # Infrastructure — created in start()
        self._zmq_config: ZMQConfig | None = None
        self._ctx: zmq.Context[Any] | None = None
        self._publisher: EventPublisher | None = None
        self._mouse_queue: multiprocessing.queues.Queue[tuple[float, float]] | None = None
        self._exit_stack: contextlib.ExitStack | None = None

        # Interfaces — available after start()
        self._haptic: HapticInterface | None = None
        self._display: DisplayInterface | None = None
        self._sync: SyncInterface | None = None

        self._session_id: str | None = None
        self._session_dir: Path | None = None
        self._start_utc: datetime.datetime | None = None

        self._segments: list[dict[str, Any]] = []
        self._segment_counter: int = 0
        self._current_segment: RecordingSegment | None = None

        self._ripple_proc: RippleProcess | None = None
        self._ripple_proc_started: bool = False

        self._data_logger_proc: DataLoggerProcess | None = None
        self._data_logger_started: bool = False

        self._workspace_mirror_proc: WorkspaceMirrorProcess | None = None
        self._workspace_mirror_started: bool = False

        self._status_dashboard_proc: StatusDashboardProcess | None = None
        self._status_dashboard_started: bool = False

    # -- Process lifecycle --------------------------------------------------

    def start(self) -> None:
        """Start all session infrastructure and recording subprocesses.

        1. Validate backend compatibility.
        2. Generate session_id and create session directory tree.
        3. Create ZMQ context and EventPublisher.
        4. Create mouse queue if dhd.mouse_input is enabled.
        5. Enter hardware factories (haptic, display, sync) via ExitStack.
        6. If config.recording.ripple is not None: start RippleProcess
           with ready_event, wait with polling loop, ZMQ grace period.
        7. If config.recording.data_logging_enabled: start DataLoggerProcess
           with ready_event, wait with polling loop, ZMQ grace period.
        8. Record session start time.
        """
        # Validate backend compatibility before launching anything.
        if (
            self._config.haptic.backend == "dhd"
            and self._config.haptic.dhd is not None
            and self._config.haptic.dhd.mouse_input
            and self._config.display.backend != "psychopy"
        ):
            raise ValueError(
                "haptic.dhd.mouse_input=True requires display.backend='psychopy' "
                "(mouse position comes from the PsychoPy window)."
            )

        self._session_id = self._build_session_id()
        self._session_dir = self._create_session_dirs()

        # ZMQ infrastructure
        self._zmq_config = self._build_zmq_config()
        self._ctx = zmq.Context()
        self._publisher = EventPublisher(
            self._ctx, self._zmq_config.event_pub_address,
        )

        # Mouse queue for dhd.mouse_input
        self._mouse_queue = self._create_mouse_queue()

        # Hardware factories via ExitStack (exits in reverse on stop)
        self._exit_stack = contextlib.ExitStack()
        try:
            self._haptic = self._exit_stack.enter_context(
                make_haptic_interface(
                    self._config.haptic, self._zmq_config,
                    context=self._ctx, mouse_queue=self._mouse_queue,
                )
            )
            self._display = self._exit_stack.enter_context(
                make_display_interface(
                    self._config.display, self._zmq_config,
                    publisher=self._publisher, mouse_queue=self._mouse_queue,
                )
            )

            # Warn if dashboard screen collides with rig display screen.
            if (
                self._config.dashboard is not None
                and self._config.display.backend == "psychopy"
                and self._config.dashboard.screen == self._config.display.screen
            ):
                logger.warning(
                    "Dashboard and display are both on screen %d. "
                    "Both PsychoPy windows will fight for the same X screen.",
                    self._config.dashboard.screen,
                )

            # Start workspace mirror after rig display is up, so both
            # PsychoPy windows don't race to create X11 contexts simultaneously.
            if (
                self._config.dashboard is not None
                and self._config.display.backend == "psychopy"
            ):
                self._start_workspace_mirror()

            # Start status dashboard (Qt only — no PsychoPy, works with mock backend).
            if (
                self._config.dashboard is not None
                and self._config.dashboard.status_enabled
            ):
                self._start_status_dashboard()

            self._sync = self._exit_stack.enter_context(
                make_sync_interface(
                    self._config.sync, self._zmq_config,
                    publisher=self._publisher,
                )
            )

            if self._config.recording.ripple is not None:
                self._start_ripple_process()

        except Exception:
            try:
                if self._exit_stack is not None:
                    self._exit_stack.close()
                    self._exit_stack = None
            finally:
                self._haptic = None
                self._display = None
                self._sync = None
                self._stop_workspace_mirror()
                self._stop_status_dashboard()
                self._cleanup_zmq()
            raise

        self._start_utc = datetime.datetime.now(datetime.UTC)
        logger.info("Session started: %s -> %s", self._session_id, self._session_dir)

    def stop(self) -> None:
        """Shut down hardware interfaces, recording subprocesses, and ZMQ.

        1. If recording is active, call stop_recording() first.
        2. Shut down DataLoggerProcess.
        3. Shut down RippleProcess.
        4. Close hardware interface ExitStack (haptic, display, sync).
        5. Write session receipt JSON.
        6. Clean up interface refs, ZMQ publisher and context.

        Uses try/finally so that interface refs and ZMQ infrastructure are
        always cleared even if ExitStack.close() or receipt writing raises.
        """
        try:
            if self._current_segment is not None:
                self.stop_recording()

            self._stop_workspace_mirror()
            self._stop_status_dashboard()
            self._stop_data_logger()
            self._stop_ripple_process()

            if self._exit_stack is not None:
                self._exit_stack.close()
                self._exit_stack = None

            if self._session_dir is not None:
                self._write_session_receipt()
        finally:
            self._haptic = None
            self._display = None
            self._sync = None
            self._cleanup_zmq()

    def __enter__(self) -> SessionManager:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    # -- Recording lifecycle ------------------------------------------------

    def start_recording(
        self,
        segment_label: str | None = None,
        *,
        active_params: dict[str, Any] | None = None,
    ) -> None:
        """Begin a recording segment.

        Creates a segment directory with behavior/, sync/, and (if Ripple
        is configured) neural/ripple/ subdirectories. Starts
        DataLoggerProcess with segment-specific paths, then publishes
        session control messages to start sync, camera trigger, and
        recording.

        Args:
            segment_label: Label for this segment directory (e.g.,
                "seg-001", "baseline"). If None, auto-generates
                "seg-001", "seg-002", etc.
            active_params: Task parameter values at segment start. Used
                to populate the segment receipt so the segment can be
                analyzed in isolation even when params were changed in a
                prior segment. Defaults to None (written as {} in receipt).

        Raises:
            RuntimeError: If start() has not been called, or if
                recording is already active.
            ValueError: If a segment with this label already exists
                in this session (overwrite protection) or if the label is
                invalid (must be a simple directory name with no path)
            NotImplementedError: If granularity is 'block' or 'trial'.
        """
        if self._session_id is None or self._session_dir is None:
            raise RuntimeError(
                "SessionManager.start() must be called before start_recording()"
            )
        if self._current_segment is not None:
            raise RuntimeError(
                "Recording is already active. Call stop_recording() first."
            )
        granularity = self._config.recording.granularity
        if granularity != "session":
            raise NotImplementedError(
                f"Recording granularity {granularity!r} is not yet supported. "
                "Only 'session' granularity is currently implemented."
            )

        if segment_label is not None:
            if (
                "/" in segment_label
                or "\\" in segment_label
                or segment_label in (".", "..")
                or segment_label.startswith(".")
            ):
                raise ValueError(
                    f"Invalid segment label {segment_label!r}: must be a simple "
                    "directory name with no path separators or leading dots."
                )
        else:
            # Auto-generate segment label
            while True:
                self._segment_counter += 1
                segment_label = f"seg-{self._segment_counter:03d}"
                if not (self._session_dir / segment_label).exists():
                    break

        # Overwrite protection
        segment_dir = self._session_dir / segment_label
        if segment_dir.exists():
            raise ValueError(
                f"Segment directory already exists: {segment_dir}. "
                "Choose a different segment label."
            )

        # Create segment directory tree
        (segment_dir / "behavior").mkdir(parents=True)
        (segment_dir / "sync").mkdir(parents=True)
        if self._config.recording.ripple is not None:
            (segment_dir / "neural" / "ripple").mkdir(parents=True)

        # Start DataLoggerProcess for this segment
        segment_file_prefix = f"{self._session_id}_{segment_label}"
        if self._config.recording.data_logging_enabled:
            self._start_data_logger_for_segment(segment_dir, segment_file_prefix)

        # Build Trellis file name pointing into segment's neural/ripple/
        trellis_file_name_base = self._build_trellis_file_name_base(
            segment_label=segment_label,
        )

        self._current_segment = RecordingSegment(
            label=segment_label,
            directory=segment_dir,
            active_params=dict(active_params) if active_params is not None else {},
            start_utc=datetime.datetime.now(datetime.UTC),
            start_trial=self._trial_manager.current_trial,
            trellis_file_name_base=trellis_file_name_base,
        )

        self._publish_session("start_sync")
        self._publish_session("start_camera_trigger")
        self._publish_session(
            "start_recording",
            {"file_name_base": trellis_file_name_base},
        )

        logger.info(
            "Recording started: segment=%s, file_name_base=%s",
            segment_label, trellis_file_name_base,
        )

    def stop_recording(self) -> None:
        """Stop the current recording segment.

        Publishes session control stop messages, records segment
        metadata, and stops DataLoggerProcess so files are flushed
        and closed.

        No-op if recording is not active (idempotent).
        """
        if self._current_segment is None:
            return

        seg = self._current_segment

        self._publish_session("stop_recording")
        self._publish_session("stop_camera_trigger")
        self._publish_session("stop_sync")

        # Capture end state once — shared by receipt and metadata
        end_utc = datetime.datetime.now(datetime.UTC)
        end_trial = self._trial_manager.current_trial

        # Write segment receipt before stopping DataLoggerProcess
        try:
            self._write_segment_receipt(end_utc, end_trial)
        except Exception:
            logger.exception("Failed to write segment receipt")

        # Record segment metadata for the session receipt
        self._segments.append(seg.to_metadata(end_utc, end_trial))

        # Stop DataLoggerProcess so files are flushed and closed
        self._stop_data_logger()

        logger.info("Recording stopped: segment=%s", seg.label)
        self._current_segment = None

    # -- Properties ---------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """The current session ID, or None if start() hasn't been called."""
        return self._session_id

    @property
    def session_dir(self) -> Path | None:
        """The current session directory, or None if start() hasn't been called."""
        return self._session_dir

    @property
    def is_recording(self) -> bool:
        """Whether recording is currently active."""
        return self._current_segment is not None

    @property
    def segments(self) -> list[dict[str, Any]]:
        """List of completed segment metadata dicts."""
        return list(self._segments)

    @property
    def current_segment_label(self) -> str | None:
        """Label of the active recording segment, or None."""
        return self._current_segment.label if self._current_segment else None

    @property
    def current_segment_dir(self) -> Path | None:
        """Directory of the active recording segment, or None."""
        return self._current_segment.directory if self._current_segment else None

    @property
    def trial_manager(self) -> TrialManager:
        """The session's TrialManager."""
        return self._trial_manager

    @property
    def haptic(self) -> HapticInterface:
        """The haptic interface. Available after start()."""
        if self._haptic is None:
            raise RuntimeError("SessionManager.start() must be called first")
        return self._haptic

    @property
    def display(self) -> DisplayInterface:
        """The display interface. Available after start()."""
        if self._display is None:
            raise RuntimeError("SessionManager.start() must be called first")
        return self._display

    @property
    def sync(self) -> SyncInterface:
        """The sync interface. Available after start()."""
        if self._sync is None:
            raise RuntimeError("SessionManager.start() must be called first")
        return self._sync

    @property
    def publisher(self) -> EventPublisher:
        """The event publisher. Available after start()."""
        if self._publisher is None:
            raise RuntimeError("SessionManager.start() must be called first")
        return self._publisher

    # -- Internal -----------------------------------------------------------

    def _build_session_id(self) -> str:
        """Generate ses-{YYYYMMDD}_{NNN} by scanning existing dirs."""
        today = datetime.date.today().strftime("%Y%m%d")
        subject_dir = (
            self._config.recording.save_dir
            / f"sub-{self._config.subject.subject_id}"
        )
        pattern = re.compile(rf"^ses-{today}_(\d+)$")
        max_num = 0
        if subject_dir.exists():
            for entry in subject_dir.iterdir():
                m = pattern.match(entry.name)
                if m:
                    max_num = max(max_num, int(m.group(1)))
        return f"ses-{today}_{max_num + 1:03d}"

    def _build_zmq_config(self) -> ZMQConfig:
        """Create session-specific ZMQ addresses.

        Generates random IPC addresses so parallel sessions don't
        collide. For backend="dhd", overrides haptic addresses from
        the user's ZMQ config so the client finds an externally
        launched haptic server.
        """
        from hapticore.core.messaging import make_ipc_address
        zmq_cfg = ZMQConfig(
            event_pub_address=make_ipc_address("hc_evt"),
            haptic_state_address=make_ipc_address("hc_state"),
            haptic_command_address=make_ipc_address("hc_cmd"),
            display_event_address=make_ipc_address("hc_disp"),
        )
        if self._config.haptic.backend == "dhd":
            zmq_cfg = zmq_cfg.model_copy(update={
                "haptic_state_address": self._config.zmq.haptic_state_address,
                "haptic_command_address": self._config.zmq.haptic_command_address,
            })
        return zmq_cfg

    def _create_mouse_queue(
        self,
    ) -> multiprocessing.queues.Queue[tuple[float, float]] | None:
        """Create mouse queue if dhd.mouse_input is enabled."""
        if (
            self._config.haptic.backend == "dhd"
            and self._config.haptic.dhd is not None
            and self._config.haptic.dhd.mouse_input
        ):
            from multiprocessing import Queue as MpQueue
            return MpQueue(maxsize=4)
        return None

    def _cleanup_zmq(self) -> None:
        """Close publisher and ZMQ context."""
        if self._publisher is not None:
            self._publisher.close()
            self._publisher = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    def _create_session_dirs(self) -> Path:
        """Create the session directory. Returns the session_dir.

        Subdirectories (behavior/, sync/, neural/) are created per-segment
        in start_recording(), not at session level.
        """
        if self._session_id is None:
            raise RuntimeError("_create_session_dirs called before _build_session_id")
        session_dir = (
            self._config.recording.save_dir
            / f"sub-{self._config.subject.subject_id}"
            / self._session_id
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _build_trellis_file_name_base(
        self, *, segment_label: str | None = None,
    ) -> str:
        """Construct the file_name_base for xipppy.trial().

        Routes through the segment's neural/ripple/ directory so
        Trellis files are colocated with behavioral data in the
        standalone segment folder.
        """
        if self._session_id is None:
            raise RuntimeError("Cannot build Trellis path before start()")
        subject_id = self._config.subject.subject_id

        file_name = self._session_id
        segment_path = ""
        if segment_label is not None:
            file_name = f"{self._session_id}_{segment_label}"
            segment_path = f"{segment_label}/"

        session_relative = (
            f"sub-{subject_id}/{self._session_id}/"
            f"{segment_path}neural/ripple/{file_name}"
        )
        if self._config.recording.ripple is not None:
            trellis_data_dir = self._config.recording.ripple.trellis_data_dir
        else:
            trellis_data_dir = str(self._config.recording.save_dir)
        return f"{trellis_data_dir}/{session_relative}"

    def _start_ripple_process(self) -> None:
        """Start RippleProcess with readiness polling loop."""
        assert self._config.recording.ripple is not None
        assert self._zmq_config is not None
        ready_event = multiprocessing.Event()
        proc = RippleProcess(
            self._config.recording.ripple,
            self._zmq_config,
            xipppy_module=self._xipppy_module,
            ready_event=ready_event,
        )
        self._ripple_proc = proc
        self._ripple_proc_started = False

        proc.start()
        self._ripple_proc_started = True

        try:
            deadline = time.monotonic() + _RIPPLE_READY_TIMEOUT_S
            while not ready_event.is_set():
                if not proc.is_alive():
                    raise RuntimeError(
                        f"RippleProcess died during startup "
                        f"(exit code: {proc.exitcode}). Check that the "
                        f"Ripple Grapevine is reachable."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"RippleProcess started but did not become ready "
                        f"within {_RIPPLE_READY_TIMEOUT_S}s. The Ripple "
                        f"Grapevine may be unresponsive."
                    )
                ready_event.wait(timeout=min(_RIPPLE_READY_POLL_INTERVAL_S, remaining))
        except Exception:
            self._stop_ripple_process()
            raise

        # Brief grace period for ZMQ subscription propagation.
        time.sleep(_RIPPLE_SUBSCRIPTION_GRACE_S)
        logger.info("RippleProcess ready (pid=%d)", proc.pid)

    def _stop_ripple_process(self) -> None:
        """Shut down RippleProcess with the standard shutdown sequence."""
        if self._ripple_proc is None or not self._ripple_proc_started:
            return
        proc = self._ripple_proc
        proc.request_shutdown()
        proc.join(timeout=_RIPPLE_SHUTDOWN_TIMEOUT_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=_RIPPLE_TERMINATE_JOIN_TIMEOUT_S)
            if proc.is_alive():
                logger.warning(
                    "RippleProcess (pid=%d) still alive after "
                    "terminate(); may leak.",
                    proc.pid,
                )

    def _start_data_logger_for_segment(
        self, segment_dir: Path, file_prefix: str,
    ) -> None:
        """Start a DataLoggerProcess for a specific recording segment.

        Passes segment_dir as session_dir and file_prefix as session_id,
        so files land in segment_dir/behavior/{file_prefix}_events.tsv,
        etc.

        This method generalizes to any recording unit directory — manual
        segments, future automatic block/trial sub-units, etc.

        Stops any existing DataLoggerProcess from a previous segment first.
        """
        assert self._zmq_config is not None

        # Stop any leftover DataLoggerProcess from a previous segment
        self._stop_data_logger()

        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = DataLoggerProcess(
            session_dir=segment_dir,
            session_id=file_prefix,
            zmq_config=self._zmq_config,
            ready_event=ready_event,
        )
        self._data_logger_proc = proc
        self._data_logger_started = False

        proc.start()
        self._data_logger_started = True

        try:
            deadline = time.monotonic() + _DATA_LOGGER_READY_TIMEOUT_S
            while not ready_event.is_set():
                if not proc.is_alive():
                    raise RuntimeError(
                        f"DataLoggerProcess died during startup "
                        f"(exit code: {proc.exitcode})."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"DataLoggerProcess started but did not become ready "
                        f"within {_DATA_LOGGER_READY_TIMEOUT_S}s."
                    )
                ready_event.wait(
                    timeout=min(_DATA_LOGGER_READY_POLL_INTERVAL_S, remaining),
                )
        except Exception:
            self._stop_data_logger()
            raise

        time.sleep(_DATA_LOGGER_SUBSCRIPTION_GRACE_S)
        logger.info(
            "DataLoggerProcess ready for segment (pid=%d)", proc.pid,
        )

    def _stop_data_logger(self) -> None:
        """Shut down DataLoggerProcess with the standard shutdown sequence."""
        if self._data_logger_proc is None or not self._data_logger_started:
            return
        proc = self._data_logger_proc
        proc.request_shutdown()
        proc.join(timeout=_DATA_LOGGER_SHUTDOWN_TIMEOUT_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=_DATA_LOGGER_TERMINATE_JOIN_TIMEOUT_S)
            if proc.is_alive():
                logger.warning(
                    "DataLoggerProcess (pid=%d) still alive after "
                    "terminate(); may leak.",
                    proc.pid,
                )
        self._data_logger_started = False

    def _start_workspace_mirror(self) -> None:
        """Start WorkspaceMirrorProcess with readiness polling loop."""
        assert self._config.dashboard is not None
        assert self._zmq_config is not None
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = WorkspaceMirrorProcess(
            dashboard_config=self._config.dashboard,
            display_config=self._config.display,
            zmq_config=self._zmq_config,
            ready_event=ready_event,
        )
        self._workspace_mirror_proc = proc
        self._workspace_mirror_started = False

        proc.start()
        self._workspace_mirror_started = True

        try:
            deadline = time.monotonic() + _WORKSPACE_MIRROR_READY_TIMEOUT_S
            while not ready_event.is_set():
                if not proc.is_alive():
                    raise RuntimeError(
                        f"WorkspaceMirrorProcess died during startup "
                        f"(exit code: {proc.exitcode})."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"WorkspaceMirrorProcess started but did not become ready "
                        f"within {_WORKSPACE_MIRROR_READY_TIMEOUT_S}s."
                    )
                ready_event.wait(
                    timeout=min(_WORKSPACE_MIRROR_READY_POLL_INTERVAL_S, remaining),
                )
        except Exception:
            self._stop_workspace_mirror()
            raise

        time.sleep(_WORKSPACE_MIRROR_SUBSCRIPTION_GRACE_S)
        logger.info("WorkspaceMirrorProcess ready (pid=%d)", proc.pid)

    def _stop_workspace_mirror(self) -> None:
        """Shut down WorkspaceMirrorProcess with the standard shutdown sequence."""
        if self._workspace_mirror_proc is None or not self._workspace_mirror_started:
            return
        proc = self._workspace_mirror_proc
        proc.request_shutdown()
        proc.join(timeout=_WORKSPACE_MIRROR_SHUTDOWN_TIMEOUT_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=_WORKSPACE_MIRROR_TERMINATE_JOIN_TIMEOUT_S)
            if proc.is_alive():
                logger.warning(
                    "WorkspaceMirrorProcess (pid=%d) still alive after "
                    "terminate(); may leak.",
                    proc.pid,
                )

    def _start_status_dashboard(self) -> None:
        """Start StatusDashboardProcess with readiness polling loop."""
        assert self._config.dashboard is not None
        assert self._zmq_config is not None

        import importlib  # noqa: PLC0415

        module_path, class_name = self._config.task.task_class.rsplit(".", 1)
        module = importlib.import_module(module_path)
        task_cls = getattr(module, class_name)

        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
        proc = StatusDashboardProcess(
            dashboard_config=self._config.dashboard,
            zmq_config=self._zmq_config,
            task_states=task_cls.STATES,
            task_initial_state=task_cls.INITIAL_STATE,
            block_size=self._config.task.block_size,
            num_blocks=self._config.task.num_blocks,
            num_conditions=len(self._config.task.conditions),
            ready_event=ready_event,
        )
        self._status_dashboard_proc = proc
        self._status_dashboard_started = False

        proc.start()
        self._status_dashboard_started = True

        try:
            deadline = time.monotonic() + _STATUS_DASHBOARD_READY_TIMEOUT_S
            while not ready_event.is_set():
                if not proc.is_alive():
                    raise RuntimeError(
                        f"StatusDashboardProcess died during startup "
                        f"(exit code: {proc.exitcode})."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"StatusDashboardProcess started but did not become ready "
                        f"within {_STATUS_DASHBOARD_READY_TIMEOUT_S}s."
                    )
                ready_event.wait(
                    timeout=min(_STATUS_DASHBOARD_READY_POLL_INTERVAL_S, remaining),
                )
        except Exception:
            self._stop_status_dashboard()
            raise

        time.sleep(_STATUS_DASHBOARD_SUBSCRIPTION_GRACE_S)
        logger.info("StatusDashboardProcess ready (pid=%d)", proc.pid)

    def _stop_status_dashboard(self) -> None:
        """Shut down StatusDashboardProcess with the standard shutdown sequence."""
        if self._status_dashboard_proc is None or not self._status_dashboard_started:
            return
        proc = self._status_dashboard_proc
        proc.request_shutdown()
        proc.join(timeout=_STATUS_DASHBOARD_SHUTDOWN_TIMEOUT_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=_STATUS_DASHBOARD_TERMINATE_JOIN_TIMEOUT_S)
            if proc.is_alive():
                logger.warning(
                    "StatusDashboardProcess (pid=%d) still alive after "
                    "terminate(); may leak.",
                    proc.pid,
                )

    def _publish_session(self, action: str, params: dict[str, Any] | None = None) -> None:
        """Publish a SessionControl message on TOPIC_SESSION."""
        msg = SessionControl(
            timestamp=time.monotonic(),
            action=action,
            params=params or {},
        )
        # Use the property here to get a clear RuntimeError if called before
        # start() — though in practice start_recording/stop_recording both
        # guard this themselves.
        self.publisher.publish(TOPIC_SESSION, serialize(msg))

    def _build_hardware_info(self) -> dict[str, Any]:
        """Build hardware and recording systems dict for receipts.

        Shared by _write_session_receipt() and _write_segment_receipt().
        """
        recording_systems: list[str] = []
        if self._config.recording.ripple is not None:
            recording_systems.append("ripple")
        if self._config.recording.data_logging_enabled:
            recording_systems.append("data_logger")
        return {
            "haptic_backend": self._config.haptic.backend,
            "display_backend": self._config.display.backend,
            "sync_backend": self._config.sync.backend,
            "recording_systems": recording_systems,
        }

    def _write_segment_receipt(
        self, end_utc: datetime.datetime, end_trial: int,
    ) -> None:
        """Write segment_receipt.json to the current segment directory.

        The segment receipt captures everything needed to understand this
        segment in isolation:
        - Config snapshot (full resolved config at session start)
        - active_params (task parameter values at segment start — critical
          for standalone analysis when params were changed in a prior segment)
        - Trial summary (session-wide count and outcomes)
        - Recording systems and hardware info
        - Segment timing and trial range

        Param changes during the segment are in the events TSV
        (msg_type="param" rows) and are not duplicated here. To
        reconstruct the full parameter history: start with active_params,
        then apply param events chronologically.
        """
        seg = self._current_segment
        if seg is None or self._session_id is None:
            raise RuntimeError("Cannot write segment receipt outside a recording")

        # Start with the segment's own metadata (timing, trial_range, etc.)
        receipt = seg.to_metadata(end_utc, end_trial)

        # Extend with session-level context
        receipt.update({
            "session_id": self._session_id,
            "subject_id": self._config.subject.subject_id,
            "experiment_name": self._config.experiment_name,
            "config_snapshot": self._config.model_dump(mode="json"),
            "trial_summary": self._trial_manager.get_summary(),
            "recording": {
                "data_logging_enabled": self._config.recording.data_logging_enabled,
                "trellis_file_name_base": seg.trellis_file_name_base,
            },
            "hardware": self._build_hardware_info(),
        })

        receipt_path = seg.directory / "segment_receipt.json"
        with receipt_path.open("w") as f:
            json.dump(receipt, f, indent=2)
        logger.info("Segment receipt written: %s", receipt_path)

    def _write_session_receipt(self) -> None:
        """Write session_receipt.json to session_dir."""
        if self._session_dir is None or self._session_id is None or self._start_utc is None:
            raise RuntimeError("Cannot write receipt before start()")

        end_utc = datetime.datetime.now(datetime.UTC)

        ripple_info: dict[str, Any] | None = None
        if self._config.recording.ripple is not None:
            rc = self._config.recording.ripple
            ripple_info = {
                "file_name_base": None,
                "use_tcp": rc.use_tcp,
                "operator_id": rc.operator_id,
                "auto_stop_time_s": rc.auto_stop_time_s,
                "trellis_data_dir": rc.trellis_data_dir,
            }

        receipt: dict[str, Any] = {
            "session_id": self._session_id,
            "subject_id": self._config.subject.subject_id,
            "experiment_name": self._config.experiment_name,
            "timing": {
                "start_utc": self._start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "duration_s": (end_utc - self._start_utc).total_seconds(),
            },
            "config_snapshot": self._config.model_dump(mode="json"),
            "initial_params": (
                dict(self._config.task.params) if self._config.task.params else {}
            ),
            "param_changes": [],
            "segments": self._segments,
            "recording": {
                "session_dir": str(self._session_dir),
                "granularity": self._config.recording.granularity,
                "ripple": ripple_info,
                "data_logging_enabled": self._config.recording.data_logging_enabled,
            },
            "trial_summary": self._trial_manager.get_summary(),
            "hardware": self._build_hardware_info(),
        }

        receipt_path = self._session_dir / "session_receipt.json"
        with receipt_path.open("w") as f:
            json.dump(receipt, f, indent=2)
        logger.info("Session receipt written: %s", receipt_path)
