"""Unit tests for RippleProcess, XipppyClient, and RippleRecording.

All tests run in CI without xipppy installed. The _FakeXipppy module
is injected in place of the real xipppy package.
"""

from __future__ import annotations

import time
from typing import Any

import msgpack
import pytest

from hapticore.core.interfaces import NeuralRecordingInterface
from hapticore.core.messages import TOPIC_SESSION
from hapticore.core.messaging import EventBus, make_ipc_address
from hapticore.recording.ripple_recording import RippleRecording
from hapticore.recording.ripple_process import RippleProcess
from hapticore.recording.xipppy_client import XipppyClient


# ---------------------------------------------------------------------------
# Fake xipppy module
# ---------------------------------------------------------------------------


class _FakeXipppy:
    """Fake xipppy module for XipppyClient tests.

    Records all calls for assertion. Mirrors the real xipppy API
    surface used by XipppyClient (and nothing more).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._connected = False
        self._time_ticks = 900_000  # 30 seconds at 30 kHz

    def _open(self, use_tcp: bool = False) -> None:
        self.calls.append(("_open", (), {"use_tcp": use_tcp}))
        self._connected = True

    def _close(self) -> None:
        self.calls.append(("_close", (), {}))
        self._connected = False

    def add_operator(self, oper_addr: int, **kwargs: Any) -> None:
        self.calls.append(("add_operator", (oper_addr,), kwargs))

    def trial(self, **kwargs: Any) -> tuple[str, str, int, bool, int]:
        self.calls.append(("trial", (), kwargs))
        status = kwargs.get("status", "stopped")
        fnb = kwargs.get("file_name_base", "")
        return (status, fnb, 0, False, 0)

    def time(self) -> int:
        self.calls.append(("time", (), {}))
        return self._time_ticks


# ---------------------------------------------------------------------------
# TestXipppyClient
# ---------------------------------------------------------------------------


class TestXipppyClient:
    def _make_client(
        self,
        *,
        use_tcp: bool = True,
        operator_id: int = 129,
        fake: _FakeXipppy | None = None,
    ) -> tuple[XipppyClient, _FakeXipppy]:
        if fake is None:
            fake = _FakeXipppy()
        client = XipppyClient(
            use_tcp=use_tcp,
            operator_id=operator_id,
            xipppy_module=fake,  # type: ignore[arg-type]
        )
        return client, fake

    def test_connect_calls_open(self) -> None:
        client, fake = self._make_client()
        client.connect()
        assert any(c[0] == "_open" for c in fake.calls)
        assert client.is_connected()

    def test_connect_tcp_calls_add_operator(self) -> None:
        client, fake = self._make_client(use_tcp=True, operator_id=42)
        client.connect()
        add_calls = [c for c in fake.calls if c[0] == "add_operator"]
        assert len(add_calls) == 1
        assert add_calls[0][1][0] == 42

    def test_connect_udp_skips_add_operator(self) -> None:
        client, fake = self._make_client(use_tcp=False)
        client.connect()
        add_calls = [c for c in fake.calls if c[0] == "add_operator"]
        assert len(add_calls) == 0

    def test_disconnect_calls_close(self) -> None:
        client, fake = self._make_client()
        client.connect()
        client.disconnect()
        assert any(c[0] == "_close" for c in fake.calls)
        assert not client.is_connected()

    def test_context_manager_calls_connect_and_disconnect(self) -> None:
        client, fake = self._make_client()
        with client:
            assert client.is_connected()
            assert any(c[0] == "_open" for c in fake.calls)
        assert not client.is_connected()
        assert any(c[0] == "_close" for c in fake.calls)

    def test_connect_add_operator_failure_closes_and_reraises(self) -> None:
        class _FailingAddOperator(_FakeXipppy):
            def add_operator(self, oper_addr: int, **kwargs: Any) -> None:
                super().add_operator(oper_addr, **kwargs)
                raise RuntimeError("operator registration failed")

        fake = _FailingAddOperator()
        client = XipppyClient(
            use_tcp=True,
            operator_id=129,
            xipppy_module=fake,  # type: ignore[arg-type]
        )
        with pytest.raises(RuntimeError, match="operator registration failed"):
            client.connect()
        # _close must have been called after the failure
        assert any(c[0] == "_close" for c in fake.calls)
        assert not client.is_connected()

    def test_start_recording_passes_correct_args(self) -> None:
        client, fake = self._make_client(operator_id=129)
        client.connect()
        client.start_recording("data/session_001", auto_stop_time_s=3600)
        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 1
        kwargs = trial_calls[0][2]
        assert kwargs["status"] == "recording"
        assert kwargs["file_name_base"] == "data/session_001"
        assert kwargs["auto_stop_time"] == 3600
        assert kwargs["auto_incr"] is False
        assert kwargs["oper"] == 129

    def test_stop_recording_passes_stopped_status(self) -> None:
        client, fake = self._make_client(operator_id=129)
        client.connect()
        client.stop_recording()
        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 1
        kwargs = trial_calls[0][2]
        assert kwargs["status"] == "stopped"
        assert kwargs["oper"] == 129

    def test_get_time_converts_ticks_to_seconds(self) -> None:
        client, fake = self._make_client()
        client.connect()
        # fake._time_ticks = 900_000 => 30.0 seconds
        t = client.get_time()
        assert t == pytest.approx(30.0)

    def test_start_recording_before_connect_raises(self) -> None:
        client, _ = self._make_client()
        with pytest.raises(RuntimeError):
            client.start_recording("some/path")

    def test_stop_recording_before_connect_raises(self) -> None:
        client, _ = self._make_client()
        with pytest.raises(RuntimeError):
            client.stop_recording()

    def test_get_time_before_connect_raises(self) -> None:
        client, _ = self._make_client()
        with pytest.raises(RuntimeError):
            client.get_time()

    def test_double_connect_raises(self) -> None:
        client, _ = self._make_client()
        client.connect()
        with pytest.raises(RuntimeError):
            client.connect()

    def test_not_connected_before_connect(self) -> None:
        client, _ = self._make_client()
        assert not client.is_connected()

    def test_start_recording_returns_trial_response(self) -> None:
        client, fake = self._make_client()
        client.connect()
        result = client.start_recording("myfile", auto_stop_time_s=0)
        assert isinstance(result, tuple)
        assert len(result) == 5
        assert result[0] == "recording"

    def test_stop_recording_returns_trial_response(self) -> None:
        client, fake = self._make_client()
        client.connect()
        result = client.stop_recording()
        assert isinstance(result, tuple)
        assert len(result) == 5
        assert result[0] == "stopped"


# ---------------------------------------------------------------------------
# TestRippleRecordingProtocolCompliance
# ---------------------------------------------------------------------------


class TestRippleRecordingProtocolCompliance:
    def test_isinstance_neural_recording_interface(self) -> None:
        addr = make_ipc_address("test_rr_proto")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            rr = RippleRecording(pub)
            assert isinstance(rr, NeuralRecordingInterface)
        finally:
            pub.close()


# ---------------------------------------------------------------------------
# TestRippleRecordingShim
# ---------------------------------------------------------------------------


class TestRippleRecordingShim:
    def test_start_recording_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_rr_start")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)
        try:
            rr = RippleRecording(pub)
            rr.start_recording("data/session_001")

            result = sub.recv(timeout_ms=500)
            assert result is not None
            _, payload = result
            msg = msgpack.unpackb(payload, raw=False)
            assert msg["action"] == "start_recording"
            assert msg["params"]["file_name_base"] == "data/session_001"
        finally:
            pub.close()
            sub.close()

    def test_stop_recording_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_rr_stop")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)
        try:
            rr = RippleRecording(pub)
            rr.start_recording("data/session_001")
            sub.recv(timeout_ms=200)  # drain start

            rr.stop_recording()

            result = sub.recv(timeout_ms=500)
            assert result is not None
            _, payload = result
            msg = msgpack.unpackb(payload, raw=False)
            assert msg["action"] == "stop_recording"
        finally:
            pub.close()
            sub.close()

    def test_is_recording_tracks_state(self) -> None:
        addr = make_ipc_address("test_rr_state")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            rr = RippleRecording(pub)
            assert not rr.is_recording()
            rr.start_recording("file")
            assert rr.is_recording()
            rr.stop_recording()
            assert not rr.is_recording()
        finally:
            pub.close()

    def test_get_timestamp_returns_elapsed_monotonic(self) -> None:
        addr = make_ipc_address("test_rr_ts")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            rr = RippleRecording(pub)
            assert rr.get_timestamp() == 0.0
            rr.start_recording("file")
            time.sleep(0.05)
            t = rr.get_timestamp()
            assert t > 0.0
            assert t < 5.0  # sanity upper bound
        finally:
            pub.close()

    def test_get_timestamp_before_start_returns_zero(self) -> None:
        addr = make_ipc_address("test_rr_ts0")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            rr = RippleRecording(pub)
            assert rr.get_timestamp() == 0.0
        finally:
            pub.close()


# ---------------------------------------------------------------------------
# TestRippleProcessHandlers
# ---------------------------------------------------------------------------


class TestRippleProcessHandlers:
    """In-process handler tests (no subprocess spawned)."""

    def _make_process(
        self, auto_stop_time_s: int = 0,
    ) -> tuple[RippleProcess, _FakeXipppy]:
        from hapticore.core.config import RippleRecordingConfig, ZMQConfig

        cfg = RippleRecordingConfig(auto_stop_time_s=auto_stop_time_s)
        zmq_cfg = ZMQConfig()
        fake = _FakeXipppy()
        proc = RippleProcess(cfg, zmq_cfg, xipppy_module=fake)  # type: ignore[arg-type]
        return proc, fake

    def test_start_recording_calls_client_with_file_name_base(self) -> None:
        proc, fake = self._make_process(auto_stop_time_s=3600)

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "SessionControl",
            "action": "start_recording",
            "params": {"file_name_base": "data/session_007"},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 1
        kwargs = trial_calls[0][2]
        assert kwargs["file_name_base"] == "data/session_007"
        assert kwargs["auto_stop_time"] == 3600
        assert kwargs["status"] == "recording"

    def test_stop_recording_calls_client_stop(self) -> None:
        proc, fake = self._make_process()

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "SessionControl",
            "action": "stop_recording",
            "params": {},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 1
        assert trial_calls[0][2]["status"] == "stopped"

    def test_start_sync_is_silently_ignored(self) -> None:
        proc, fake = self._make_process()

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "SessionControl",
            "action": "start_sync",
            "params": {},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 0

    def test_stop_sync_is_silently_ignored(self) -> None:
        proc, fake = self._make_process()

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "SessionControl",
            "action": "stop_sync",
            "params": {},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 0

    def test_wrong_msg_type_is_ignored(self) -> None:
        proc, fake = self._make_process()

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "StateTransition",
            "action": "start_recording",
            "params": {"file_name_base": "should_be_ignored"},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 0
    
    def test_start_recording_empty_file_name_base_is_ignored(self) -> None:
        proc, fake = self._make_process()

        client = XipppyClient(xipppy_module=fake)  # type: ignore[arg-type]
        client.connect()

        msg = {
            "__msg_type__": "SessionControl",
            "action": "start_recording",
            "params": {"file_name_base": ""},
            "timestamp": time.monotonic(),
        }
        proc._handle_session_control(client, msg)

        trial_calls = [c for c in fake.calls if c[0] == "trial"]
        assert len(trial_calls) == 0


# ---------------------------------------------------------------------------
# TestRippleProcessConstruction
# ---------------------------------------------------------------------------


class TestRippleProcessConstruction:
    def test_raises_on_none_recording_config(self) -> None:
        from hapticore.core.config import ZMQConfig

        with pytest.raises((ValueError, TypeError)):
            RippleProcess(None, ZMQConfig())  # type: ignore[arg-type]

    def test_request_shutdown_sets_event(self) -> None:
        from hapticore.core.config import RippleRecordingConfig, ZMQConfig

        cfg = RippleRecordingConfig()
        proc = RippleProcess(cfg, ZMQConfig())
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()


# ---------------------------------------------------------------------------
# TestRippleRecordingConfigDefaults
# ---------------------------------------------------------------------------


class TestRippleRecordingConfigDefaults:
    def test_auto_stop_time_s_default(self) -> None:
        from hapticore.core.config import RippleRecordingConfig

        cfg = RippleRecordingConfig()
        assert cfg.auto_stop_time_s == 7200

    def test_trellis_data_dir_default(self) -> None:
        from hapticore.core.config import RippleRecordingConfig

        cfg = RippleRecordingConfig()
        assert cfg.trellis_data_dir == "data"

    def test_auto_increment_field_does_not_exist(self) -> None:
        from hapticore.core.config import RippleRecordingConfig

        cfg = RippleRecordingConfig()
        assert not hasattr(cfg, "auto_increment")

    def test_recording_config_granularity_default(self) -> None:
        from hapticore.core.config import RecordingConfig

        cfg = RecordingConfig()
        assert cfg.granularity == "session"

    def test_recording_config_granularity_values(self) -> None:
        from hapticore.core.config import RecordingConfig

        for val in ("session", "block", "trial"):
            cfg = RecordingConfig(granularity=val)  # type: ignore[arg-type]
            assert cfg.granularity == val
