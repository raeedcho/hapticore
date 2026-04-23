"""Unit tests for hapticore.sync protocol encoders, serial adapter, and shim."""

from __future__ import annotations

import time

import msgpack
import pytest

from hapticore.core.interfaces import SyncInterface
from hapticore.core.messages import TOPIC_SESSION, TOPIC_SYNC
from hapticore.core.messaging import EventBus, make_ipc_address
from hapticore.sync import protocol
from hapticore.sync.teensy_serial import TeensySerialClient
from hapticore.sync.teensy_sync import TeensySync


# ---------------------------------------------------------------------------
# Protocol encoder tests
# ---------------------------------------------------------------------------


class TestProtocolEncoders:
    def test_start_stop_sync(self) -> None:
        assert protocol.format_start_sync() == b"S1\n"
        assert protocol.format_stop_sync() == b"S0\n"

    def test_start_stop_camera(self) -> None:
        assert protocol.format_start_camera_trigger() == b"T1\n"
        assert protocol.format_stop_camera_trigger() == b"T0\n"

    def test_camera_rate_rounds(self) -> None:
        assert protocol.format_set_camera_rate(60.0) == b"C60\n"
        assert protocol.format_set_camera_rate(59.7) == b"C60\n"  # rounded

    def test_camera_rate_bounds(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            protocol.format_set_camera_rate(0.5)
        with pytest.raises(ValueError, match="outside"):
            protocol.format_set_camera_rate(501.0)

    def test_camera_rate_boundary_values(self) -> None:
        # Boundary values should be accepted
        assert protocol.format_set_camera_rate(1.0) == b"C1\n"
        assert protocol.format_set_camera_rate(500.0) == b"C500\n"

    def test_event_code_basic(self) -> None:
        assert protocol.format_event_code(0) == b"E0\n"
        assert protocol.format_event_code(42) == b"E42\n"
        assert protocol.format_event_code(255) == b"E255\n"

    def test_event_code_bounds(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            protocol.format_event_code(-1)
        with pytest.raises(ValueError, match="outside"):
            protocol.format_event_code(256)

    def test_reward_ms_basic(self) -> None:
        assert protocol.format_reward_ms(1) == b"R1\n"
        assert protocol.format_reward_ms(100) == b"R100\n"
        assert protocol.format_reward_ms(10_000) == b"R10000\n"

    def test_reward_ms_bounds(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            protocol.format_reward_ms(0)
        with pytest.raises(ValueError, match="outside"):
            protocol.format_reward_ms(10_001)

    def test_all_encoders_end_with_newline(self) -> None:
        commands = [
            protocol.format_start_sync(),
            protocol.format_stop_sync(),
            protocol.format_start_camera_trigger(),
            protocol.format_stop_camera_trigger(),
            protocol.format_set_camera_rate(30.0),
            protocol.format_event_code(1),
            protocol.format_reward_ms(50),
        ]
        for cmd in commands:
            assert cmd.endswith(b"\n"), f"Command {cmd!r} does not end with newline"


# ---------------------------------------------------------------------------
# TeensySerialClient tests
# ---------------------------------------------------------------------------


class _FakeSerial:
    """Fake serial.Serial object for TeensySerialClient tests."""

    def __init__(self, *, port: str, baudrate: int, timeout: float) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def readline(self) -> bytes:
        return b"OK\n"

    def close(self) -> None:
        self.closed = True


class _FakeSerialModule:
    """Fake pyserial module for injection into TeensySerialClient."""

    Serial = _FakeSerial


class TestTeensySerialClient:
    def _make_client(self) -> tuple[TeensySerialClient, _FakeSerial]:
        fake_module = _FakeSerialModule()
        client = TeensySerialClient(
            port="/dev/ttyACM0",
            baud=115200,
            serial_module=fake_module,  # type: ignore[arg-type]
        )
        client.open()
        assert isinstance(client._serial, _FakeSerial)
        return client, client._serial  # type: ignore[return-value]

    def test_open_uses_injected_module(self) -> None:
        client, fake = self._make_client()
        assert fake.port == "/dev/ttyACM0"
        assert fake.baudrate == 115200
        assert client.is_open()

    def test_write_passes_bytes(self) -> None:
        client, fake = self._make_client()
        client.write(b"S1\n")
        client.write(b"E42\n")
        assert fake.writes == [b"S1\n", b"E42\n"]

    def test_readline_returns_bytes(self) -> None:
        client, _ = self._make_client()
        result = client.readline()
        assert result == b"OK\n"

    def test_close_sets_not_open(self) -> None:
        client, fake = self._make_client()
        assert client.is_open()
        client.close()
        assert not client.is_open()
        assert fake.closed

    def test_close_idempotent(self) -> None:
        client, _ = self._make_client()
        client.close()
        client.close()  # should not raise

    def test_not_open_before_open(self) -> None:
        fake_module = _FakeSerialModule()
        client = TeensySerialClient(
            port="/dev/ttyACM0",
            baud=115200,
            serial_module=fake_module,  # type: ignore[arg-type]
        )
        assert not client.is_open()


# ---------------------------------------------------------------------------
# TeensySync shim tests
# ---------------------------------------------------------------------------


def _make_shim() -> tuple[TeensySync, EventBus]:
    addr = make_ipc_address("test_sync")
    bus = EventBus(addr)
    pub = bus.create_publisher()
    shim = TeensySync(pub)
    return shim, bus


class TestTeensySyncProtocolCompliance:
    def test_isinstance_check(self) -> None:
        addr = make_ipc_address("test_sync_isinstance")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            shim = TeensySync(pub)
            assert isinstance(shim, SyncInterface)
        finally:
            pub.close()


class TestTeensySyncEventCode:
    def test_publishes_on_topic_sync(self) -> None:
        addr = make_ipc_address("test_sync_ec")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SYNC])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.send_event_code(42)

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_SYNC
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "send_event_code"
        assert msg["code"] == 42
        assert "timestamp" in msg

        pub.close()
        sub.close()

    def test_invalid_code_raises(self) -> None:
        addr = make_ipc_address("test_sync_ec_inv")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            shim = TeensySync(pub)
            with pytest.raises(ValueError, match="outside"):
                shim.send_event_code(256)
            with pytest.raises(ValueError, match="outside"):
                shim.send_event_code(-1)
        finally:
            pub.close()


class TestTeensySyncSyncPulses:
    def test_start_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_sync_sp")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)

        shim = TeensySync(pub)
        assert not shim.is_sync_running()
        shim.start_sync_pulses()
        assert shim.is_sync_running()

        result = sub.recv(timeout_ms=500)
        assert result is not None
        topic, payload = result
        assert topic == TOPIC_SESSION
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "start_sync"

        pub.close()
        sub.close()

    def test_stop_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_sync_sp2")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.start_sync_pulses()
        # Drain start message
        sub.recv(timeout_ms=200)

        shim.stop_sync_pulses()
        assert not shim.is_sync_running()

        result = sub.recv(timeout_ms=500)
        assert result is not None
        _, payload = result
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "stop_sync"

        pub.close()
        sub.close()


class TestTeensySyncCameraTrigger:
    def test_set_rate_publishes_sync_message(self) -> None:
        addr = make_ipc_address("test_sync_cr")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SYNC])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.set_camera_trigger_rate(30.0)

        result = sub.recv(timeout_ms=500)
        assert result is not None
        _, payload = result
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "set_camera_trigger_rate"
        assert msg["rate_hz"] == 30.0

        pub.close()
        sub.close()

    def test_invalid_rate_raises(self) -> None:
        addr = make_ipc_address("test_sync_cr_inv")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            shim = TeensySync(pub)
            with pytest.raises(ValueError, match="outside"):
                shim.set_camera_trigger_rate(0.5)
        finally:
            pub.close()

    def test_start_stop_tracks_state(self) -> None:
        addr = make_ipc_address("test_sync_ct")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            shim = TeensySync(pub)
            assert not shim.is_camera_trigger_running()
            shim.start_camera_trigger()
            assert shim.is_camera_trigger_running()
            shim.stop_camera_trigger()
            assert not shim.is_camera_trigger_running()
        finally:
            pub.close()

    def test_start_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_sync_ct2")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.start_camera_trigger()

        result = sub.recv(timeout_ms=500)
        assert result is not None
        _, payload = result
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "start_camera_trigger"

        pub.close()
        sub.close()

    def test_stop_publishes_session_message(self) -> None:
        addr = make_ipc_address("test_sync_ct3")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SESSION])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.start_camera_trigger()
        sub.recv(timeout_ms=200)  # drain start

        shim.stop_camera_trigger()

        result = sub.recv(timeout_ms=500)
        assert result is not None
        _, payload = result
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "stop_camera_trigger"

        pub.close()
        sub.close()


class TestTeensySyncReward:
    def test_publishes_sync_message(self) -> None:
        addr = make_ipc_address("test_sync_rw")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        sub = bus.create_subscriber(topics=[TOPIC_SYNC])
        time.sleep(0.05)

        shim = TeensySync(pub)
        shim.deliver_reward(100)

        result = sub.recv(timeout_ms=500)
        assert result is not None
        _, payload = result
        msg = msgpack.unpackb(payload, raw=False)
        assert msg["action"] == "deliver_reward"
        assert msg["duration_ms"] == 100

        pub.close()
        sub.close()

    def test_invalid_duration_raises(self) -> None:
        addr = make_ipc_address("test_sync_rw_inv")
        bus = EventBus(addr)
        pub = bus.create_publisher()
        try:
            shim = TeensySync(pub)
            with pytest.raises(ValueError, match="outside"):
                shim.deliver_reward(0)
            with pytest.raises(ValueError, match="outside"):
                shim.deliver_reward(10_001)
        finally:
            pub.close()


# ---------------------------------------------------------------------------
# SyncProcess handler logic tests (in-process, no subprocess)
# ---------------------------------------------------------------------------


class _FakeSerialModuleForProcess:
    """Fake serial module that records written bytes."""

    class Serial:
        def __init__(self, *, port: str, baudrate: int, timeout: float) -> None:
            self.writes: list[bytes] = []
            self.closed = False

        def write(self, data: bytes) -> None:
            self.writes.append(data)

        def close(self) -> None:
            self.closed = True


class TestSyncProcessHandlers:
    """Test the handler methods of SyncProcess in-process."""

    def _make_process(self) -> object:
        """Create a SyncProcess with teensy transport for handler testing."""
        from hapticore.core.config import SyncConfig, ZMQConfig
        from hapticore.sync.sync_process import SyncProcess

        sync_cfg = SyncConfig(transport="teensy")
        zmq_cfg = ZMQConfig()
        return SyncProcess(sync_cfg, zmq_cfg)

    def _make_client_and_protocol(
        self,
    ) -> tuple[object, object]:
        from hapticore.sync import protocol as proto
        from hapticore.sync.teensy_serial import TeensySerialClient

        fake_module = _FakeSerialModuleForProcess()
        client = TeensySerialClient(
            port="/dev/ttyACM0",
            baud=115200,
            serial_module=fake_module,  # type: ignore[arg-type]
        )
        client.open()
        return client, proto

    def test_handle_sync_command_event_code(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()
        msg = {"action": "send_event_code", "code": 77}
        proc._handle_sync_command(client, proto, msg)
        assert client._serial.writes == [b"E77\n"]  # type: ignore[union-attr]

    def test_handle_sync_command_reward(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()
        msg = {"action": "deliver_reward", "duration_ms": 150}
        proc._handle_sync_command(client, proto, msg)
        assert client._serial.writes == [b"R150\n"]  # type: ignore[union-attr]

    def test_handle_sync_command_camera_rate(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()
        msg = {"action": "set_camera_trigger_rate", "rate_hz": 60.0}
        proc._handle_sync_command(client, proto, msg)
        assert client._serial.writes == [b"C60\n"]  # type: ignore[union-attr]

    def test_handle_event_auto_emits_code(self) -> None:
        from hapticore.core.config import EventCodeMap, SyncConfig, ZMQConfig
        from hapticore.sync.sync_process import SyncProcess

        code_map = EventCodeMap(state_codes={"reach": 10, "hold": 20})
        sync_cfg = SyncConfig(transport="teensy", code_map=code_map)
        zmq_cfg = ZMQConfig()
        proc = SyncProcess(sync_cfg, zmq_cfg)
        client, proto = self._make_client_and_protocol()

        msg = {
            "__msg_type__": "StateTransition",
            "new_state": "reach",
            "previous_state": "iti",
        }
        proc._handle_event(client, proto, msg)
        assert client._serial.writes == [b"E10\n"]  # type: ignore[union-attr]

    def test_handle_event_no_code_for_unmapped_state(self) -> None:
        from hapticore.core.config import EventCodeMap, SyncConfig, ZMQConfig
        from hapticore.sync.sync_process import SyncProcess

        code_map = EventCodeMap(state_codes={"reach": 10})
        sync_cfg = SyncConfig(transport="teensy", code_map=code_map)
        zmq_cfg = ZMQConfig()
        proc = SyncProcess(sync_cfg, zmq_cfg)
        client, proto = self._make_client_and_protocol()

        msg = {
            "__msg_type__": "StateTransition",
            "new_state": "iti",  # not in code_map
        }
        proc._handle_event(client, proto, msg)
        assert client._serial.writes == []  # type: ignore[union-attr]

    def test_handle_event_ignores_non_state_transition(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "TrialEvent", "new_state": "reach"}
        proc._handle_event(client, proto, msg)
        assert client._serial.writes == []  # type: ignore[union-attr]

    def test_handle_session_control_start_sync(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "SessionControl", "action": "start_sync"}
        proc._handle_session_control(client, proto, msg)
        assert client._serial.writes == [b"S1\n"]  # type: ignore[union-attr]

    def test_handle_session_control_stop_sync(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "SessionControl", "action": "stop_sync"}
        proc._handle_session_control(client, proto, msg)
        assert client._serial.writes == [b"S0\n"]  # type: ignore[union-attr]

    def test_handle_session_control_start_camera_trigger(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "SessionControl", "action": "start_camera_trigger"}
        proc._handle_session_control(client, proto, msg)
        assert client._serial.writes == [b"T1\n"]  # type: ignore[union-attr]

    def test_handle_session_control_stop_camera_trigger(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "SessionControl", "action": "stop_camera_trigger"}
        proc._handle_session_control(client, proto, msg)
        assert client._serial.writes == [b"T0\n"]  # type: ignore[union-attr]

    def test_handle_session_control_ignores_recording_actions(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        for action in ("start_recording", "stop_recording"):
            msg = {"__msg_type__": "SessionControl", "action": action}
            proc._handle_session_control(client, proto, msg)

        assert client._serial.writes == []  # type: ignore[union-attr]

    def test_handle_session_control_ignores_non_session_control(self) -> None:
        from hapticore.sync.sync_process import SyncProcess

        proc = self._make_process()
        assert isinstance(proc, SyncProcess)
        client, proto = self._make_client_and_protocol()

        msg = {"__msg_type__": "HapticState", "action": "start_sync"}
        proc._handle_session_control(client, proto, msg)
        assert client._serial.writes == []  # type: ignore[union-attr]


class TestSyncProcessConstruction:
    def test_raises_on_wrong_transport(self) -> None:
        from hapticore.core.config import SyncConfig, ZMQConfig
        from hapticore.sync.sync_process import SyncProcess

        sync_cfg = SyncConfig(transport="mock")
        zmq_cfg = ZMQConfig()
        with pytest.raises(ValueError, match="transport='teensy'"):
            SyncProcess(sync_cfg, zmq_cfg)

    def test_request_shutdown_sets_event(self) -> None:
        from hapticore.core.config import SyncConfig, ZMQConfig
        from hapticore.sync.sync_process import SyncProcess

        sync_cfg = SyncConfig(transport="teensy")
        zmq_cfg = ZMQConfig()
        proc = SyncProcess(sync_cfg, zmq_cfg)
        assert not proc._shutdown.is_set()
        proc.request_shutdown()
        assert proc._shutdown.is_set()
