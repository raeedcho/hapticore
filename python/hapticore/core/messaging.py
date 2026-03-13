"""ZeroMQ messaging wrappers for inter-process communication.

Provides EventBus (PUB-SUB) for broadcasting events and
CommandClient/CommandServer (DEALER-ROUTER) for request-reply commands.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Self

import msgpack
import zmq

from hapticore.core.messages import (
    Command,
    CommandResponse,
    serialize,
)


def make_ipc_address(label: str = "hc") -> str:
    """Generate a short, unique IPC address safe on macOS (103-char limit).

    Always roots in /tmp to avoid macOS $TMPDIR length explosion.
    The 8-char hex ID provides ~4 billion unique values to avoid collisions
    across parallel test runs.
    """
    short_id = uuid.uuid4().hex[:8]
    return f"ipc:///tmp/{label}-{short_id}"


class EventPublisher:
    """Publishes messages on a ZeroMQ PUB socket."""

    def __init__(self, context: zmq.Context[Any], address: str) -> None:
        self._socket: zmq.Socket[Any] = context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(address)

    def publish(self, topic: bytes, message: bytes) -> None:
        """Send a multipart message: [topic, payload].

        Uses non-blocking send; if the socket's high-water mark is reached,
        the message is dropped rather than raising zmq.Again.
        """
        try:
            self._socket.send_multipart([topic, message], zmq.NOBLOCK)
        except zmq.Again:
            # Drop message when PUB socket cannot accept more data (HWM reached).
            # This preserves non-blocking behavior and prevents publisher crashes.
            return

    def close(self) -> None:
        """Close the socket."""
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class EventSubscriber:
    """Receives messages from a ZeroMQ SUB socket."""

    def __init__(
        self, context: zmq.Context[Any], address: str, topics: list[bytes] | None = None
    ) -> None:
        self._socket: zmq.Socket[Any] = context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(address)
        if topics is None:
            self._socket.setsockopt(zmq.SUBSCRIBE, b"")
        else:
            for topic in topics:
                self._socket.setsockopt(zmq.SUBSCRIBE, topic)
        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

    def recv(self, timeout_ms: int = 0) -> tuple[bytes, bytes] | None:
        """Non-blocking receive. Returns (topic, payload) or None if no message."""
        socks = dict(self._poller.poll(timeout_ms))
        if self._socket in socks:
            parts: list[bytes] = self._socket.recv_multipart()
            return (parts[0], parts[1])
        return None

    def close(self) -> None:
        """Close the socket."""
        self._poller.unregister(self._socket)
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class EventBus:
    """Publish-subscribe event distribution.

    Publisher side: create_publisher() to get an EventPublisher.
    Subscriber side: create_subscriber() to get an EventSubscriber.
    Uses ipc:// transport by default for lowest latency on same machine.
    """

    def __init__(
        self,
        address: str = "ipc:///tmp/hapticore_events",
        context: zmq.Context[Any] | None = None,
    ) -> None:
        self._address = address
        self._context: zmq.Context[Any] = context or zmq.Context.instance()

    @property
    def address(self) -> str:
        """Return the address this EventBus uses."""
        return self._address

    def create_publisher(self) -> EventPublisher:
        """Create a PUB socket bound to the address."""
        return EventPublisher(self._context, self._address)

    def create_subscriber(self, topics: list[bytes] | None = None) -> EventSubscriber:
        """Create a SUB socket connected to the address."""
        return EventSubscriber(self._context, self._address, topics)


class CommandServer:
    """Receives commands, dispatches to handlers, sends responses.

    Uses ROUTER socket so multiple clients can connect.
    """

    def __init__(
        self,
        address: str = "ipc:///tmp/hapticore_commands",
        context: zmq.Context[Any] | None = None,
    ) -> None:
        self._context: zmq.Context[Any] = context or zmq.Context.instance()
        self._socket: zmq.Socket[Any] = self._context.socket(zmq.ROUTER)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(address)
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

    def register_handler(
        self, method: str, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        """Register a handler for a command method name."""
        self._handlers[method] = handler

    def poll_and_dispatch(self, timeout_ms: int = 0) -> bool:
        """Check for incoming command, dispatch to handler, send response.

        Returns True if a command was processed.
        """
        socks = dict(self._poller.poll(timeout_ms))
        if self._socket not in socks:
            return False

        frames: list[bytes] = self._socket.recv_multipart()
        # ROUTER frames: [client_identity, empty_delimiter, payload]
        if len(frames) != 3:
            # Malformed message; ignore and report no command processed.
            return False

        client_identity = frames[0]
        _delimiter = frames[1]
        payload = frames[2]

        unpacked = msgpack.unpackb(payload, raw=False)
        if not isinstance(unpacked, dict):
            # Expect a mapping of command fields; ignore malformed payloads.
            return False

        # Messages serialized via core.messages.serialize() include a
        # "__msg_type__" field that Command.__init__ does not accept.
        unpacked.pop("__msg_type__", None)

        cmd = Command(**unpacked)

        if cmd.method in self._handlers:
            try:
                result = self._handlers[cmd.method](cmd.params)
                response = CommandResponse(
                    command_id=cmd.command_id,
                    success=True,
                    result=result,
                )
            except Exception as e:
                response = CommandResponse(
                    command_id=cmd.command_id,
                    success=False,
                    result={},
                    error=str(e),
                )
        else:
            response = CommandResponse(
                command_id=cmd.command_id,
                success=False,
                result={},
                error=f"Unknown method: {cmd.method}",
            )

        resp_data = serialize(response)
        self._socket.send_multipart([client_identity, b"", resp_data])
        return True

    def close(self) -> None:
        """Close the socket."""
        self._poller.unregister(self._socket)
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class CommandClient:
    """Sends commands and receives responses.

    Uses DEALER socket for async-compatible request-reply.
    """

    def __init__(
        self,
        address: str = "ipc:///tmp/hapticore_commands",
        context: zmq.Context[Any] | None = None,
    ) -> None:
        self._context: zmq.Context[Any] = context or zmq.Context.instance()
        self._socket: zmq.Socket[Any] = self._context.socket(zmq.DEALER)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(address)
        self._poller = zmq.Poller()
        self._poller.register(self._socket, zmq.POLLIN)

    def send_command(self, command: Command, *, timeout_ms: int = 1000) -> CommandResponse:
        """Send a command and wait for response with timeout.

        Raises TimeoutError if no response within timeout.
        """
        cmd_data = msgpack.packb(
            {"command_id": command.command_id, "method": command.method, "params": command.params},
            use_bin_type=True,
        )
        # DEALER sends: [empty_delimiter, payload]
        self._socket.send_multipart([b"", cmd_data])

        socks = dict(self._poller.poll(timeout_ms))
        if self._socket not in socks:
            raise TimeoutError(
                f"No response for command {command.command_id} within {timeout_ms}ms"
            )

        frames: list[bytes] = self._socket.recv_multipart()
        # DEALER receives: [empty_delimiter, payload]
        _delimiter = frames[0]
        payload = frames[1]
        unpacked = msgpack.unpackb(payload, raw=False)
        unpacked.pop("__msg_type__", None)
        return CommandResponse(**unpacked)

    def close(self) -> None:
        """Close the socket."""
        self._poller.unregister(self._socket)
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
