"""DataLoggerProcess — behavioral event and haptic state logging subprocess.

Subscribes to the ZMQ event bus and haptic state stream and writes
behavioral events (TSV) and haptic state (flat binary + JSON sidecar)
to the session's behavior/ directory.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import signal
import time
from pathlib import Path
from typing import Any, BinaryIO, TextIO

import msgpack
import numpy as np
import zmq

from hapticore.core.config import ZMQConfig
from hapticore.core.messages import TOPIC_EVENT, TOPIC_SESSION, TOPIC_STATE

logger = logging.getLogger(__name__)

_JSON_COMPACT_SEPARATORS = (",", ":")


class DataLoggerProcess(multiprocessing.Process):
    """Subprocess writing behavioral events, haptic state, session
    notes, and trial results to disk.

    Subscribes to two ZMQ streams:
    - event_pub_address / TOPIC_EVENT: StateTransition, TrialEvent,
      ParamUpdate, TrialResult, and SessionNote messages.
      StateTransition, TrialEvent, and ParamUpdate are appended to a
      TSV events file. SessionNote messages are routed to a separate
      notes TSV file. TrialResult messages are routed to a separate
      trials TSV file.
    - haptic_state_address / TOPIC_STATE: HapticState messages →
      appended as float64 binary to a .bin file.

    Also subscribes to event_pub_address / TOPIC_SESSION for
    start_recording/stop_recording commands. The process is running
    for the entire session (started in SessionManager.start()), but
    only writes to disk between start_recording and stop_recording.
    This supports test-before-record workflows: the subscriptions
    stay warm during test trials, so no messages are lost when
    recording begins.

    File layout (under session_dir/behavior/):
    - {session_id}_events.tsv: tab-separated behavioral events
    - {session_id}_notes.tsv: tab-separated experimenter notes
    - {session_id}_trials.tsv: tab-separated trial log
    - {session_id}_haptic.bin: flat little-endian float64 binary
    - {session_id}_haptic.json: sidecar describing columns, dtype,
      sample count
    """

    _POLL_TIMEOUT_MS: int = 50
    _ERROR_LOG_INTERVAL_S: float = 5.0

    def __init__(
        self,
        session_dir: Path,
        session_id: str,
        zmq_config: ZMQConfig,
        *,
        ready_event: multiprocessing.Event | None = None,  # type: ignore[type-arg]
    ) -> None:
        super().__init__(name="DataLoggerProcess", daemon=True)
        self._session_dir = session_dir
        self._session_id = session_id
        self._zmq_config = zmq_config
        self._ready_event = ready_event
        self._shutdown: multiprocessing.Event = multiprocessing.Event()  # type: ignore[type-arg]

    def request_shutdown(self) -> None:
        """Signal the process to exit cleanly."""
        self._shutdown.set()

    def run(self) -> None:
        """Entry point in child process."""
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        behavior_dir = self._session_dir / "behavior"
        events_path = behavior_dir / f"{self._session_id}_events.tsv"
        haptic_bin_path = behavior_dir / f"{self._session_id}_haptic.bin"
        haptic_sidecar_path = behavior_dir / f"{self._session_id}_haptic.json"
        notes_path = behavior_dir / f"{self._session_id}_notes.tsv"
        trials_path = behavior_dir / f"{self._session_id}_trials.tsv"

        # ZMQ setup: two SUB sockets
        ctx = zmq.Context()

        # Socket 1: event bus (events + session control)
        event_sub = ctx.socket(zmq.SUB)
        event_sub.setsockopt(zmq.LINGER, 0)
        event_sub.connect(self._zmq_config.event_pub_address)
        event_sub.subscribe(TOPIC_EVENT)
        event_sub.subscribe(TOPIC_SESSION)
        event_sub.setsockopt(zmq.RCVHWM, 1000)

        # Socket 2: haptic state (from C++ server)
        state_sub = ctx.socket(zmq.SUB)
        state_sub.setsockopt(zmq.LINGER, 0)
        state_sub.connect(self._zmq_config.haptic_state_address)
        state_sub.subscribe(TOPIC_STATE)
        state_sub.setsockopt(zmq.RCVHWM, 1000)

        poller = zmq.Poller()
        poller.register(event_sub, zmq.POLLIN)
        poller.register(state_sub, zmq.POLLIN)

        if self._ready_event is not None:
            self._ready_event.set()

        recording = False
        events_file: TextIO | None = None
        haptic_file: BinaryIO | None = None
        notes_file: TextIO | None = None
        trials_file: TextIO | None = None
        haptic_sample_count = 0
        last_error_log_time = 0.0

        try:
            while not self._shutdown.is_set():
                socks = dict(poller.poll(self._POLL_TIMEOUT_MS))

                # Handle event bus messages
                if event_sub in socks:
                    topic, payload = event_sub.recv_multipart(zmq.NOBLOCK)
                    msg = msgpack.unpackb(payload, raw=False)
                    msg_type = msg.get("__msg_type__")

                    if msg_type == "SessionControl":
                        action = msg.get("action")
                        if action == "start_recording" and not recording:
                            events_file, haptic_file = self._open_files(
                                events_path, haptic_bin_path,
                            )
                            notes_file = self._open_notes_file(notes_path)
                            trials_file = self._open_trials_file(trials_path)
                            recording = True
                            haptic_sample_count = 0
                            logger.info("DataLogger: recording started")
                        elif action == "stop_recording" and recording:
                            self._close_files(events_file, haptic_file)
                            if notes_file is not None:
                                notes_file.flush()
                                notes_file.close()
                            if trials_file is not None:
                                trials_file.flush()
                                trials_file.close()
                            events_file = None
                            haptic_file = None
                            notes_file = None
                            trials_file = None
                            self._write_haptic_sidecar(
                                haptic_sidecar_path, haptic_sample_count,
                            )
                            recording = False
                            logger.info(
                                "DataLogger: recording stopped "
                                "(%d haptic samples)", haptic_sample_count,
                            )

                    elif recording and events_file is not None:
                        try:
                            if msg_type == "SessionNote" and notes_file is not None:
                                line = self._format_note(msg)
                                if line is not None:
                                    notes_file.write(line)
                            elif msg_type == "TrialResult" and trials_file is not None:
                                line = self._format_trial_result(msg)
                                if line is not None:
                                    trials_file.write(line)
                            else:
                                line = self._format_event(msg)
                                if line is not None:
                                    events_file.write(line)
                        except Exception:
                            now = time.monotonic()
                            if now - last_error_log_time > self._ERROR_LOG_INTERVAL_S:
                                logger.exception("Error writing event")
                                last_error_log_time = now

                # Handle haptic state messages
                if state_sub in socks and recording and haptic_file is not None:
                    _topic, payload = state_sub.recv_multipart(zmq.NOBLOCK)
                    msg = msgpack.unpackb(payload, raw=False)
                    try:
                        self._write_haptic_sample(haptic_file, msg)
                        haptic_sample_count += 1
                    except Exception:
                        now = time.monotonic()
                        if now - last_error_log_time > self._ERROR_LOG_INTERVAL_S:
                            logger.exception("Error writing haptic sample")
                            last_error_log_time = now

        finally:
            if recording:
                self._close_files(events_file, haptic_file)
                if notes_file is not None:
                    notes_file.flush()
                    notes_file.close()
                if trials_file is not None:
                    trials_file.flush()
                    trials_file.close()
                self._write_haptic_sidecar(
                    haptic_sidecar_path, haptic_sample_count,
                )
            event_sub.close()
            state_sub.close()
            ctx.term()

    @staticmethod
    def _open_files(
        events_path: Path, haptic_bin_path: Path,
    ) -> tuple[TextIO, BinaryIO]:
        """Open event TSV and haptic binary files for writing.

        Writes the TSV header line on open.
        """
        events_file: TextIO = events_path.open("w", newline="")
        events_file.write(
            "timestamp_s\ttrial_number\tmsg_type\tname\tevent_code\n"
        )
        haptic_file: BinaryIO = haptic_bin_path.open("wb")
        return events_file, haptic_file

    @staticmethod
    def _close_files(
        events_file: TextIO | None, haptic_file: BinaryIO | None,
    ) -> None:
        """Flush and close both files."""
        if events_file is not None:
            events_file.flush()
            events_file.close()
        if haptic_file is not None:
            haptic_file.flush()
            haptic_file.close()

    @staticmethod
    def _open_notes_file(notes_path: Path) -> TextIO:
        """Open the notes TSV file for writing.

        Writes the TSV header line on open.
        """
        notes_file: TextIO = notes_path.open("w", newline="")
        notes_file.write("timestamp_s\ttrial_number\ttext\n")
        return notes_file

    @staticmethod
    def _format_note(msg: dict[str, Any]) -> str | None:
        """Format a SessionNote message dict as a TSV line.

        Returns None if required fields are missing.

        Columns: timestamp_s, trial_number, text
        Tab and newline characters in the note text are replaced with
        spaces to preserve TSV structure.
        """
        try:
            text = (
                str(msg["text"])
                .replace("\t", " ")
                .replace("\r", " ")
                .replace("\n", " ")
            )
            return f"{msg['timestamp']}\t{msg['trial_number']}\t{text}\n"
        except KeyError:
            return None

    @staticmethod
    def _open_trials_file(trials_path: Path) -> TextIO:
        """Open the trials TSV file for writing.

        Writes the TSV header line on open.
        """
        trials_file: TextIO = trials_path.open("w", newline="")
        trials_file.write(
            "timestamp_s\ttrial_number\tblock_number\t"
            "outcome\tcondition\textra_data\n"
        )
        return trials_file

    @staticmethod
    def _format_trial_result(msg: dict[str, Any]) -> str | None:
        """Format a TrialResult message dict as a TSV line.

        Returns None if required fields are missing.

        Columns: timestamp_s, trial_number, block_number, outcome,
        condition (JSON), extra_data (JSON)
        """
        try:
            condition = json.dumps(msg["condition"], separators=_JSON_COMPACT_SEPARATORS)
            extra_data = json.dumps(
                msg.get("extra_data", {}), separators=_JSON_COMPACT_SEPARATORS,
            )
            return (
                f"{msg['timestamp']}\t{msg['trial_number']}\t"
                f"{msg['block_number']}\t{msg['outcome']}\t"
                f"{condition}\t{extra_data}\n"
            )
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _format_event(msg: dict[str, Any]) -> str | None:
        """Format a message dict as a TSV line.

        Returns None for unrecognized message types (silently skipped).

        Columns: timestamp_s, trial_number, msg_type, name, event_code
        - StateTransition: msg_type="state", name=new_state, event_code=<int>
        - TrialEvent: msg_type="event", name=event_name, event_code=<int>
        - ParamUpdate: msg_type="param", name=param_name,
          event_code="old_value->new_value"
        """
        msg_type = msg.get("__msg_type__")
        if msg_type == "StateTransition":
            return (
                f"{msg['timestamp']}\t{msg['trial_number']}\t"
                f"state\t{msg['new_state']}\t{msg['event_code']}\n"
            )
        if msg_type == "TrialEvent":
            return (
                f"{msg['timestamp']}\t{msg['trial_number']}\t"
                f"event\t{msg['event_name']}\t{msg['event_code']}\n"
            )
        if msg_type == "ParamUpdate":
            return (
                f"{msg['timestamp']}\t{msg['trial_number']}\t"
                f"param\t{msg['param']}\t{msg['old_value']}->{msg['new_value']}\n"
            )
        return None

    @staticmethod
    def _write_haptic_sample(
        haptic_file: BinaryIO, msg: dict[str, Any],
    ) -> None:
        """Write one haptic state sample as 10 × little-endian float64.

        Column order: timestamp, px, py, pz, vx, vy, vz, fx, fy, fz
        """
        row = np.array(
            [msg["timestamp"]]
            + msg["position"]
            + msg["velocity"]
            + msg["force"],
            dtype="<f8",
        )
        haptic_file.write(row.tobytes())

    @staticmethod
    def _write_haptic_sidecar(
        sidecar_path: Path, sample_count: int,
    ) -> None:
        """Write the JSON sidecar describing the haptic binary file."""
        sidecar = {
            "dtype": "float64",
            "byte_order": "little",
            "columns": [
                "timestamp_s",
                "position_x", "position_y", "position_z",
                "velocity_x", "velocity_y", "velocity_z",
                "force_x", "force_y", "force_z",
            ],
            "n_columns": 10,
            "n_samples": sample_count,
            "bytes_per_sample": 80,
            "description": (
                "Haptic device state logged by DataLoggerProcess. "
                "Read with: np.fromfile(path, dtype=np.float64)"
                ".reshape(-1, 10)"
            ),
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
