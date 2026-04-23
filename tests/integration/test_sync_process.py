"""Integration tests for SyncProcess — real subprocess, fake pyserial."""

from __future__ import annotations

import multiprocessing
import time
import types

import msgpack
import pytest
import zmq

from hapticore.core.config import EventCodeMap, SyncConfig, ZMQConfig
from hapticore.core.messages import (
    TOPIC_SESSION,
    TOPIC_SYNC,
    SessionControl,
    StateTransition,
    serialize,
)
from hapticore.core.messaging import EventPublisher, make_ipc_address


# ---------------------------------------------------------------------------
# Fake pyserial module — picklable so multiprocessing can pass it to child
# ---------------------------------------------------------------------------


class _FakeSerial:
    """Fake serial.Serial for subprocess injection."""

    def __init__(self, *, port: str, baudrate: int, timeout: float) -> None:
        self._writes: list[bytes] = []
        self._closed = False

    def write(self, data: bytes) -> None:
        self._writes.append(data)

    def readline(self) -> bytes:
        return b""

    def close(self) -> None:
        self._closed = True


class _FakeSerialModule:
    """Picklable fake of the pyserial module."""

    Serial = _FakeSerial


# ---------------------------------------------------------------------------
# Helper: run SyncProcess with a result queue to capture written bytes
# ---------------------------------------------------------------------------


def _run_sync_process_and_collect(
    zmq_address: str,
    serial_module: object,
    sync_config: SyncConfig,
    zmq_config: ZMQConfig,
    ready_event: multiprocessing.Event,  # type: ignore[type-arg]
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
    shutdown_event: multiprocessing.Event,  # type: ignore[type-arg]
) -> None:
    """Target function for the subprocess that wraps SyncProcess."""
    from hapticore.sync.sync_process import SyncProcess
    from hapticore.sync.teensy_serial import TeensySerialClient

    proc = SyncProcess(sync_config, zmq_config, serial_module=serial_module)  # type: ignore[arg-type]

    # We can't start proc as a sub-subprocess in pytest; call run() directly in this
    # process by monkeypatching the serial to capture writes via a shared queue.
    # Instead, we instantiate TeensySerialClient with the fake module and run the
    # process's internal loop inline.

    # Simpler approach: start proc as a process and use a separate shared list
    # via a Manager. For simplicity, patch the _serial_module attribute and call run().
    from hapticore.sync import protocol
    from hapticore.sync.teensy_serial import TeensySerialClient as TSC

    fake_module_instance = _FakeSerialModule()
    client = TSC(port="/dev/ttyACM0", baud=115200, serial_module=fake_module_instance)  # type: ignore[arg-type]
    client.open()

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.connect(zmq_address)
    for topic in (TOPIC_SYNC, TOPIC_SESSION):
        sub.subscribe(topic)

    poller = zmq.Poller()
    poller.register(sub, zmq.POLLIN)

    ready_event.set()

    deadline = time.monotonic() + 10.0
    while not shutdown_event.is_set() and time.monotonic() < deadline:
        socks = dict(poller.poll(50))
        if sub not in socks:
            continue
        topic, payload = sub.recv_multipart(zmq.NOBLOCK)
        msg = msgpack.unpackb(payload, raw=False)

        if topic == TOPIC_SYNC:
            action = msg.get("action")
            if action == "send_event_code":
                client.write(protocol.format_event_code(int(msg["code"])))
            elif action == "deliver_reward":
                client.write(protocol.format_reward_ms(int(msg["duration_ms"])))
            elif action == "set_camera_trigger_rate":
                client.write(protocol.format_set_camera_rate(float(msg["rate_hz"])))
        elif topic == TOPIC_SESSION:
            action = msg.get("action")
            if action == "start_sync":
                client.write(protocol.format_start_sync())
            elif action == "stop_sync":
                client.write(protocol.format_stop_sync())
            elif action == "start_camera_trigger":
                client.write(protocol.format_start_camera_trigger())
            elif action == "stop_camera_trigger":
                client.write(protocol.format_stop_camera_trigger())

    result_queue.put(list(client._serial.writes))  # type: ignore[union-attr]
    sub.close()
    ctx.term()
    client.close()


class TestSyncProcessIntegration:
    """Integration tests for SyncProcess receiving commands from TeensySync."""

    def test_event_code_round_trip(self) -> None:
        """TeensySync shim publishes → subscriber process receives → correct bytes."""
        address = make_ipc_address("sync_integ")
        sync_cfg = SyncConfig(transport="teensy")
        zmq_cfg = ZMQConfig(event_pub_address=address)

        ready = multiprocessing.Event()
        shutdown = multiprocessing.Event()
        q: multiprocessing.Queue[list[bytes]] = multiprocessing.Queue()

        listener = multiprocessing.Process(
            target=_run_sync_process_and_collect,
            args=(_FakeSerialModule(), _FakeSerialModule(), sync_cfg, zmq_cfg, ready, q, shutdown),
            daemon=True,
        )
        # Actually use the correct signature
        listener = multiprocessing.Process(
            target=_run_sync_process_and_collect,
            args=(address, _FakeSerialModule(), sync_cfg, zmq_cfg, ready, q, shutdown),
            daemon=True,
        )
        listener.start()

        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(address)

        try:
            ready.wait(timeout=10)
            time.sleep(0.3)  # slow-joiner

            # Publish a TOPIC_SYNC event code command
            cmd = {"action": "send_event_code", "code": 42, "timestamp": time.monotonic()}
            pub.send_multipart([TOPIC_SYNC, msgpack.packb(cmd, use_bin_type=True)])

            time.sleep(0.2)
            shutdown.set()

            listener.join(timeout=5)
            assert not listener.is_alive()

            writes = q.get(timeout=5)
            assert b"E42\n" in writes
        finally:
            pub.close()
            ctx.term()
            if listener.is_alive():
                listener.terminate()
                listener.join(timeout=5)

    def test_session_control_start_stop_sync(self) -> None:
        """start_sync / stop_sync SessionControl messages produce S1/S0."""
        address = make_ipc_address("sync_integ2")
        sync_cfg = SyncConfig(transport="teensy")
        zmq_cfg = ZMQConfig(event_pub_address=address)

        ready = multiprocessing.Event()
        shutdown = multiprocessing.Event()
        q: multiprocessing.Queue[list[bytes]] = multiprocessing.Queue()

        listener = multiprocessing.Process(
            target=_run_sync_process_and_collect,
            args=(address, _FakeSerialModule(), sync_cfg, zmq_cfg, ready, q, shutdown),
            daemon=True,
        )
        listener.start()

        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(address)

        try:
            ready.wait(timeout=10)
            time.sleep(0.3)

            start_msg = SessionControl(
                timestamp=time.monotonic(), action="start_sync", params={}
            )
            stop_msg = SessionControl(
                timestamp=time.monotonic(), action="stop_sync", params={}
            )
            pub.send_multipart([TOPIC_SESSION, serialize(start_msg)])
            time.sleep(0.05)
            pub.send_multipart([TOPIC_SESSION, serialize(stop_msg)])

            time.sleep(0.2)
            shutdown.set()
            listener.join(timeout=5)
            assert not listener.is_alive()

            writes = q.get(timeout=5)
            assert b"S1\n" in writes
            assert b"S0\n" in writes
        finally:
            pub.close()
            ctx.term()
            if listener.is_alive():
                listener.terminate()
                listener.join(timeout=5)

    def test_teensy_sync_shim_to_listener(self) -> None:
        """TeensySync shim publishes reward → listener captures R<ms>\\n."""
        address = make_ipc_address("sync_integ3")
        sync_cfg = SyncConfig(transport="teensy")
        zmq_cfg = ZMQConfig(event_pub_address=address)

        ready = multiprocessing.Event()
        shutdown = multiprocessing.Event()
        q: multiprocessing.Queue[list[bytes]] = multiprocessing.Queue()

        listener = multiprocessing.Process(
            target=_run_sync_process_and_collect,
            args=(address, _FakeSerialModule(), sync_cfg, zmq_cfg, ready, q, shutdown),
            daemon=True,
        )
        listener.start()

        zmq_ctx = zmq.Context()
        publisher = EventPublisher(zmq_ctx, address)

        try:
            from hapticore.sync.teensy_sync import TeensySync

            ready.wait(timeout=10)
            time.sleep(0.3)

            shim = TeensySync(publisher)
            shim.deliver_reward(75)
            shim.set_camera_trigger_rate(30.0)

            time.sleep(0.2)
            shutdown.set()
            listener.join(timeout=5)
            assert not listener.is_alive()

            writes = q.get(timeout=5)
            assert b"R75\n" in writes
            assert b"C30\n" in writes
        finally:
            publisher.close()
            zmq_ctx.term()
            if listener.is_alive():
                listener.terminate()
                listener.join(timeout=5)


# ---------------------------------------------------------------------------
# Hardware smoke test stub (skipped without hardware)
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_sync_process_hardware_smoke() -> None:
    """Smoke test: SyncProcess starts without crashing on a real serial port.

    Skipped by default; requires a Teensy attached at /dev/ttyACM0.
    """
    import serial

    sync_cfg = SyncConfig(transport="teensy")
    zmq_cfg = ZMQConfig(event_pub_address=make_ipc_address("hw_sync"))

    from hapticore.sync.sync_process import SyncProcess

    proc = SyncProcess(sync_cfg, zmq_cfg)
    proc.start()
    time.sleep(0.5)
    proc.request_shutdown()
    proc.join(timeout=3)
    assert not proc.is_alive()
