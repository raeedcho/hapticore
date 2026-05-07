"""SessionManager — recording orchestration and session lifecycle.

Owns the recording subprocess lifecycle, publishes session-level commands,
creates the data directory, and writes the session receipt JSON.
"""

from __future__ import annotations

import datetime
import json
import logging
import multiprocessing
import re
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from hapticore.core.config import ExperimentConfig, ZMQConfig
from hapticore.core.messages import TOPIC_SESSION, SessionControl, serialize
from hapticore.core.messaging import EventPublisher
from hapticore.recording.ripple_process import RippleProcess

if TYPE_CHECKING:
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


class SessionManager:
    """Orchestrates recording processes and session lifecycle.

    Lifecycle methods map to distinct phases:

    - ``start()`` / ``__enter__``: Launch recording subprocesses
      (RippleProcess if configured), create session directory, wait
      for process readiness.
    - ``start_recording()``: Publish start_sync, start_camera_trigger,
      start_recording to the event bus. SyncProcess and RippleProcess
      act on these.
    - ``stop_recording()``: Publish stops in reverse order.
    - ``stop()`` / ``__exit__``: Shut down recording subprocesses,
      write session receipt JSON.

    Separating process lifecycle from recording lifecycle supports
    test-before-record workflows: start() launches everything, the
    experimenter runs test trials, then start_recording() begins the
    real session.

    Args:
        config: The full ExperimentConfig (needed for subject info,
            recording config, and config snapshot in the receipt).
        zmq_config: Session-specific ZMQ addresses.
        publisher: Shared EventPublisher for publishing SessionControl.
        trial_manager: Reference to the TrialManager for the session
            summary in the receipt. Optional — may be None if the
            SessionManager is started before the TrialManager exists.
        xipppy_module: Optional fake xipppy module for testing.
    """

    def __init__(
        self,
        config: ExperimentConfig,
        zmq_config: ZMQConfig,
        publisher: EventPublisher,
        *,
        trial_manager: TrialManager | None = None,
        xipppy_module: ModuleType | None = None,
    ) -> None:
        self._config = config
        self._zmq_config = zmq_config
        self._publisher = publisher
        self._trial_manager = trial_manager
        self._xipppy_module = xipppy_module

        self._session_id: str | None = None
        self._session_dir: Path | None = None
        self._start_utc: datetime.datetime | None = None
        self._is_recording: bool = False
        self._trellis_file_name_base: str | None = None

        self._ripple_proc: Any | None = None  # RippleProcess | None
        self._ripple_proc_started: bool = False

    # -- Process lifecycle --------------------------------------------------

    def start(self) -> None:
        """Start recording subprocesses and create session directory.

        1. Generate session_id: ses-{YYYYMMDD}_{NNN} where NNN is
           auto-incremented by scanning existing directories under
           {save_dir}/sub-{subject_id}/.
        2. Create session directory tree.
        3. If config.recording.ripple is not None: start RippleProcess
           with ready_event, wait with polling loop, ZMQ grace period.
        4. Record session start time.
        """
        self._session_id = self._build_session_id()
        self._session_dir = self._create_session_dirs()

        if self._config.recording.ripple is not None:
            self._start_ripple_process()

        self._start_utc = datetime.datetime.now(datetime.UTC)
        logger.info("Session started: %s -> %s", self._session_id, self._session_dir)

    def stop(self) -> None:
        """Shut down recording subprocesses and write session receipt.

        1. If recording is active, call stop_recording() first.
        2. Shut down RippleProcess.
        3. Write session receipt JSON.
        """
        if self._is_recording:
            self.stop_recording()

        self._stop_ripple_process()

        if self._session_dir is not None:
            self._write_session_receipt()

    def __enter__(self) -> SessionManager:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    # -- Recording lifecycle ------------------------------------------------

    def start_recording(self) -> None:
        """Begin recording and sync.

        Publishes SessionControl messages in order:
        1. start_sync
        2. start_camera_trigger
        3. start_recording (with file_name_base in params)

        Raises:
            RuntimeError: If start() has not been called.
            NotImplementedError: If granularity is 'block' or 'trial'.
        """
        if self._session_id is None:
            raise RuntimeError(
                "SessionManager.start() must be called before start_recording()"
            )
        granularity = self._config.recording.granularity
        if granularity != "session":
            raise NotImplementedError(
                f"Recording granularity {granularity!r} is not yet supported. "
                "Only 'session' granularity is currently implemented."
            )

        self._trellis_file_name_base = self._build_trellis_file_name_base()

        self._publish_session("start_sync")
        self._publish_session("start_camera_trigger")
        self._publish_session(
            "start_recording",
            {"file_name_base": self._trellis_file_name_base},
        )
        self._is_recording = True
        logger.info("Recording started: file_name_base=%s", self._trellis_file_name_base)

    def stop_recording(self) -> None:
        """Stop recording and sync.

        Publishes SessionControl messages in reverse order:
        1. stop_recording
        2. stop_camera_trigger
        3. stop_sync
        """
        self._publish_session("stop_recording")
        self._publish_session("stop_camera_trigger")
        self._publish_session("stop_sync")
        self._is_recording = False
        logger.info("Recording stopped")

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
        return self._is_recording

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

    def _create_session_dirs(self) -> Path:
        """Create the session directory tree. Returns the session_dir."""
        assert self._session_id is not None
        session_dir = (
            self._config.recording.save_dir
            / f"sub-{self._config.subject.subject_id}"
            / self._session_id
        )
        # Always create these subdirectories.
        for subdir in ("behavior", "sync", "lsl"):
            (session_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Only create neural/ripple when ripple is configured.
        if self._config.recording.ripple is not None:
            (session_dir / "neural" / "ripple").mkdir(parents=True, exist_ok=True)

        return session_dir

    def _build_trellis_file_name_base(self) -> str:
        """Construct the file_name_base for xipppy.trial().

        Combines config.recording.ripple.trellis_data_dir with the
        session-relative path. For co-located Trellis (trellis_data_dir
        == save_dir), this produces a path on the local filesystem.
        For remote Trellis, this produces a path on the Trellis machine.
        """
        assert self._session_id is not None
        subject_id = self._config.subject.subject_id
        session_relative = (
            f"sub-{subject_id}/{self._session_id}/neural/ripple/{self._session_id}"
        )
        if self._config.recording.ripple is not None:
            trellis_data_dir = self._config.recording.ripple.trellis_data_dir
        else:
            trellis_data_dir = str(self._config.recording.save_dir)
        # Always join with forward slash — works on Linux and Windows Trellis.
        return f"{trellis_data_dir}/{session_relative}"

    def _start_ripple_process(self) -> None:
        """Start RippleProcess with readiness polling loop."""
        assert self._config.recording.ripple is not None
        ready_event: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]
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

    def _publish_session(self, action: str, params: dict[str, Any] | None = None) -> None:
        """Publish a SessionControl message on TOPIC_SESSION."""
        msg = SessionControl(
            timestamp=time.monotonic(),
            action=action,
            params=params or {},
        )
        self._publisher.publish(TOPIC_SESSION, serialize(msg))

    def _write_session_receipt(self) -> None:
        """Write session_receipt.json to session_dir."""
        assert self._session_dir is not None
        assert self._session_id is not None
        assert self._start_utc is not None

        end_utc = datetime.datetime.now(datetime.UTC)

        recording_systems: list[str] = []
        if self._config.recording.ripple is not None:
            recording_systems.append("ripple")
        if self._config.recording.lsl_enabled:
            recording_systems.append("lsl")

        ripple_info: dict[str, Any] | None = None
        if self._config.recording.ripple is not None:
            rc = self._config.recording.ripple
            ripple_info = {
                "file_name_base": self._trellis_file_name_base,
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
            "recording": {
                "session_dir": str(self._session_dir),
                "granularity": self._config.recording.granularity,
                "ripple": ripple_info,
                "lsl_enabled": self._config.recording.lsl_enabled,
            },
            "trial_summary": (
                self._trial_manager.get_summary() if self._trial_manager else None
            ),
            "hardware": {
                "haptic_backend": self._config.haptic.backend,
                "display_backend": self._config.display.backend,
                "sync_backend": self._config.sync.backend,
                "recording_systems": recording_systems,
            },
        }

        receipt_path = self._session_dir / "session_receipt.json"
        with receipt_path.open("w") as f:
            json.dump(receipt, f, indent=2)
        logger.info("Session receipt written: %s", receipt_path)

    def set_trial_manager(self, trial_manager: TrialManager) -> None:
        """Attach the TrialManager after construction.

        The CLI creates SessionManager before TrialManager (SessionManager
        creates the session directory, then TrialManager is created with
        task config). This method wires the TrialManager in so the session
        receipt can include the trial summary.
        """
        self._trial_manager = trial_manager
