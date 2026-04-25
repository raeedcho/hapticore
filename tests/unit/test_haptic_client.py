"""Unit tests for HapticClient.

All tests use a Python fake server (no C++ dependency).
Timing-sensitive assertions use event-driven synchronisation where possible.
"""

from __future__ import annotations

import platform
import queue
import time
from typing import Any

import pytest
import zmq

from hapticore.core.interfaces import HapticInterface
from hapticore.core.messages import Command, HapticState, make_haptic_state
from hapticore.core.messaging import make_ipc_address
from hapticore.backends.haptic_client import HapticClient

from ._haptic_server_fixture import fake_haptic_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addresses() -> tuple[str, str]:
    """Return a fresh (state_address, command_address) pair."""
    return make_ipc_address("hcst"), make_ipc_address("hccmd")


# ---------------------------------------------------------------------------
# TestLifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_connect_then_close(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            client = HapticClient(state_addr, cmd_addr)
            client.connect()
            assert client._connected  # noqa: SLF001
            client.close()
            assert not client._connected  # noqa: SLF001

    def test_use_before_connect_raises(self) -> None:
        state_addr, cmd_addr = _addresses()
        client = HapticClient(state_addr, cmd_addr)
        with pytest.raises(RuntimeError, match="not connected"):
            client.get_latest_state()
        with pytest.raises(RuntimeError, match="not connected"):
            client.send_command(Command(command_id="", method="heartbeat", params={}))

    def test_double_connect_raises(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            client = HapticClient(state_addr, cmd_addr)
            client.connect()
            try:
                with pytest.raises(RuntimeError, match="Already connected"):
                    client.connect()
            finally:
                client.close()

    def test_close_is_idempotent(self) -> None:
        state_addr, cmd_addr = _addresses()
        # Never-connected: close is a no-op
        client = HapticClient(state_addr, cmd_addr)
        client.close()  # should not raise

        # Connected then closed twice
        with fake_haptic_server(state_addr, cmd_addr):
            client2 = HapticClient(state_addr, cmd_addr)
            client2.connect()
            client2.close()
            client2.close()  # second close is a no-op

    def test_context_manager(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            with HapticClient(state_addr, cmd_addr) as client:
                assert client._connected  # noqa: SLF001
            assert not client._connected  # noqa: SLF001

    def test_context_manager_on_exception(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            with pytest.raises(ValueError):
                with HapticClient(state_addr, cmd_addr) as client:
                    raise ValueError("test error")
            # Client should still be closed after the exception
            assert not client._connected  # noqa: SLF001

    def test_external_context_not_terminated(self) -> None:
        state_addr, cmd_addr = _addresses()
        ctx: zmq.Context[Any] = zmq.Context()
        try:
            with fake_haptic_server(state_addr, cmd_addr, context=ctx):
                with HapticClient(state_addr, cmd_addr, context=ctx) as _client:
                    pass
            # Context should still be usable after HapticClient.close()
            sock = ctx.socket(zmq.PUSH)
            sock.close()
        finally:
            ctx.term()

    def test_owned_context_terminated(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            client = HapticClient(state_addr, cmd_addr)
            client.connect()
            internal_ctx = client._context  # noqa: SLF001
            client.close()
            # The context should be closed/terminated
            assert internal_ctx.closed


# ---------------------------------------------------------------------------
# TestStateStream
# ---------------------------------------------------------------------------


class TestStateStream:
    def test_latest_state_none_before_any_message(self) -> None:
        state_addr, cmd_addr = _addresses()
        # Don't start a fake server — connect but no messages published
        ctx: zmq.Context[Any] = zmq.Context()
        try:
            # Bind a PUB socket that publishes nothing
            pub: zmq.Socket[Any] = ctx.socket(zmq.PUB)
            pub.bind(state_addr)
            router: zmq.Socket[Any] = ctx.socket(zmq.ROUTER)
            router.bind(cmd_addr)
            try:
                with HapticClient(state_addr, cmd_addr, context=ctx) as client:
                    # Give the thread time to start
                    time.sleep(0.05)
                    assert client.get_latest_state() is None
            finally:
                pub.close()
                router.close()
        finally:
            ctx.term()

    def test_latest_state_reflects_most_recent_publish(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr) as server:
            with HapticClient(state_addr, cmd_addr) as client:
                # Allow slow-joiner
                time.sleep(0.15)

                for seq in range(3):
                    server.publish_state(make_haptic_state(sequence=seq))
                    time.sleep(0.05)

                # Should reflect the last published state
                state = client.get_latest_state()
                assert state is not None
                assert state.sequence == 2

    def test_callback_fires_on_each_state(self) -> None:
        state_addr, cmd_addr = _addresses()
        received: queue.Queue[HapticState] = queue.Queue()

        with fake_haptic_server(state_addr, cmd_addr) as server:
            with HapticClient(state_addr, cmd_addr) as client:
                client.subscribe_state(received.put_nowait)
                # Allow slow-joiner
                time.sleep(0.15)

                n = 5
                for seq in range(n):
                    server.publish_state(make_haptic_state(sequence=seq))

                # Collect all states with a bounded wait
                collected: list[HapticState] = []
                for _ in range(n):
                    try:
                        collected.append(received.get(timeout=2.0))
                    except queue.Empty:
                        break

                assert len(collected) == n
                seqs = [s.sequence for s in collected]
                assert seqs == list(range(n))

    def test_unsubscribe_stops_callback(self) -> None:
        state_addr, cmd_addr = _addresses()
        received: queue.Queue[HapticState] = queue.Queue()

        with fake_haptic_server(state_addr, cmd_addr) as server:
            with HapticClient(state_addr, cmd_addr) as client:
                client.subscribe_state(received.put_nowait)
                # Allow slow-joiner
                time.sleep(0.15)

                server.publish_state(make_haptic_state(sequence=0))
                first = received.get(timeout=2.0)
                assert first.sequence == 0

                client.unsubscribe_state()

                server.publish_state(make_haptic_state(sequence=1))
                time.sleep(0.1)
                assert received.empty()


# ---------------------------------------------------------------------------
# TestCommands
# ---------------------------------------------------------------------------


class TestCommands:
    def test_heartbeat_roundtrip(self) -> None:
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr) as _server:
            with HapticClient(state_addr, cmd_addr) as client:
                resp = client.send_command(
                    Command(command_id="test-hb-001", method="heartbeat", params={})
                )
        assert resp.success is True
        assert resp.result == {"timeout_ms": 500}

    def test_unknown_method_returns_failure(self) -> None:
        state_addr, cmd_addr = _addresses()
        # No handler for "nonexistent_method"
        with fake_haptic_server(state_addr, cmd_addr):
            with HapticClient(state_addr, cmd_addr) as client:
                resp = client.send_command(
                    Command(command_id="test-unk-001", method="nonexistent_method", params={})
                )
        assert resp.success is False
        assert resp.error  # non-empty error message

    def test_command_timeout_returns_failure(self) -> None:
        state_addr, cmd_addr = _addresses()
        # Bind a ROUTER but never read from it
        ctx: zmq.Context[Any] = zmq.Context()
        try:
            pub: zmq.Socket[Any] = ctx.socket(zmq.PUB)
            pub.bind(state_addr)
            router: zmq.Socket[Any] = ctx.socket(zmq.ROUTER)
            router.bind(cmd_addr)
            try:
                with HapticClient(
                    state_addr, cmd_addr, command_timeout_ms=200, context=ctx
                ) as client:
                    resp = client.send_command(
                        Command(command_id="", method="slow_cmd", params={})
                    )
                assert resp.success is False
                assert "timed out" in (resp.error or "")
            finally:
                pub.close()
                router.close()
        finally:
            ctx.term()

    def test_auto_generated_command_id(self) -> None:
        """When command_id is empty, the client fills in a non-empty UUID hex."""
        state_addr, cmd_addr = _addresses()
        with fake_haptic_server(state_addr, cmd_addr):
            with HapticClient(state_addr, cmd_addr) as client:
                # The fake server echoes back command_id in the response.
                resp = client.send_command(
                    Command(command_id="", method="heartbeat", params={})
                )
        # A non-empty command_id was assigned and echoed back
        assert resp.command_id

    def test_stale_response_not_returned_to_next_command(self) -> None:
        """After a timeout, the stale server reply is drained and not mistaken for the next command.

        Scenario:
          1. cmd1 sent with a 200 ms timeout; server delays its response by 300 ms.
          2. Client returns timeout failure for cmd1.
          3. Server's late cmd1 response lands in the DEALER queue.
          4. Client drains the stale reply before sending cmd2.
          5. Server responds promptly to cmd2.
          6. Client returns cmd2's fresh response, not the stale cmd1 response.
        """
        state_addr, cmd_addr = _addresses()

        # Use a fake server with a slow handler for "slow_cmd" and a normal
        # handler for "fast_cmd". The slow handler blocks the dispatch thread
        # for 300 ms, so the response arrives after the client's 200 ms timeout.
        slow_handler_started = queue.Queue[bool]()

        def _slow_handler(_params: dict[str, Any]) -> dict[str, Any]:
            slow_handler_started.put(True)
            time.sleep(0.3)  # response arrives after the 200 ms client timeout
            return {"was_slow": True}

        handlers = {
            "slow_cmd": _slow_handler,
            "fast_cmd": lambda _p: {"was_fast": True},
        }

        with fake_haptic_server(state_addr, cmd_addr, handlers):
            with HapticClient(
                state_addr, cmd_addr,
                command_timeout_ms=200,
                heartbeat_interval_s=0.45,  # avoid heartbeat noise during the test
            ) as client:
                # cmd1: should time out (server takes 300 ms, timeout is 200 ms)
                resp1 = client.send_command(
                    Command(command_id="cmd1", method="slow_cmd", params={})
                )
                assert resp1.success is False
                assert "timed out" in (resp1.error or "")

                # Wait for the stale cmd1 response to land in the DEALER queue
                slow_handler_started.get(timeout=2.0)  # server started the slow handler
                time.sleep(0.15)  # enough for the 300 ms handler to finish and respond

                # cmd2: should get the fresh fast_cmd response, not cmd1's stale result
                resp2 = client.send_command(
                    Command(command_id="cmd2", method="fast_cmd", params={})
                )

        assert resp2.success is True
        assert resp2.result.get("was_fast") is True, (
            f"Got stale/wrong response: {resp2}"
        )


# ---------------------------------------------------------------------------
# TestHeartbeats
# ---------------------------------------------------------------------------


class TestHeartbeats:
    @pytest.mark.skipif(
        platform.system() == "Darwin",
        reason="macOS CI timer jitter makes lower-bound count assertions unreliable",
    )
    def test_heartbeat_thread_sends_periodically(self) -> None:
        state_addr, cmd_addr = _addresses()
        counter: list[int] = [0]

        def _hb_handler(_params: dict[str, Any]) -> dict[str, Any]:
            counter[0] += 1
            return {"timeout_ms": 500}

        with fake_haptic_server(state_addr, cmd_addr, {"heartbeat": _hb_handler}):
            with HapticClient(state_addr, cmd_addr, heartbeat_interval_s=0.2) as _client:
                time.sleep(0.65)

        # At 0.2 s interval over 0.65 s we expect at least 2 heartbeats
        assert counter[0] >= 2

    @pytest.mark.skipif(
        platform.system() == "Darwin",
        reason="macOS CI timer jitter makes lower-bound count assertions unreliable",
    )
    def test_heartbeat_stops_on_close(self) -> None:
        state_addr, cmd_addr = _addresses()
        counter: list[int] = [0]

        def _hb_handler(_params: dict[str, Any]) -> dict[str, Any]:
            counter[0] += 1
            return {"timeout_ms": 500}

        with fake_haptic_server(state_addr, cmd_addr, {"heartbeat": _hb_handler}):
            client = HapticClient(state_addr, cmd_addr, heartbeat_interval_s=0.2)
            client.connect()
            time.sleep(0.45)
            client.close()
            snapshot = counter[0]
            time.sleep(0.5)
            # Counter should not have increased after close
            assert counter[0] == snapshot


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_haptic_interface(self) -> None:
        state_addr, cmd_addr = _addresses()
        client = HapticClient(state_addr, cmd_addr)
        assert isinstance(client, HapticInterface)
