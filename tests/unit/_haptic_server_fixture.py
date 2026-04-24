"""Fake haptic server fixture for HapticClient unit tests.

Runs a Python ``CommandServer`` + ``EventPublisher`` in threads; does NOT link
against the C++ server. Used only to exercise the HapticClient's wire protocol
behaviour.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import zmq

from hapticore.core.messages import TOPIC_STATE, HapticState, make_haptic_state, serialize
from hapticore.core.messaging import CommandServer, EventPublisher


@dataclass
class _Handle:
    publisher: EventPublisher
    cmd_server: CommandServer
    context: zmq.Context[Any]

    def publish_state(self, state: HapticState) -> None:
        """Publish a HapticState on the PUB socket."""
        self.publisher.publish(TOPIC_STATE, serialize(state))

    def publish_default_state(self, sequence: int = 0) -> None:
        """Publish a default-valued HapticState."""
        self.publish_state(make_haptic_state(sequence=sequence))

    def dispatch_once(self, timeout_ms: int = 200) -> bool:
        """Process one pending command, if any. Returns True if dispatched."""
        return self.cmd_server.poll_and_dispatch(timeout_ms)

    def dispatch_until_shutdown(self, stop: threading.Event) -> None:
        """Dispatch commands until *stop* is set."""
        while not stop.is_set():
            self.cmd_server.poll_and_dispatch(timeout_ms=50)


@contextmanager
def fake_haptic_server(
    state_address: str,
    command_address: str,
    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    *,
    context: zmq.Context[Any] | None = None,
) -> Iterator[_Handle]:
    """Run a Python fake haptic server in threads for unit tests.

    The server registers a default ``heartbeat`` handler returning
    ``{"timeout_ms": 500}``. Additional handlers are passed via *handlers*.

    The caller receives a :class:`_Handle` that exposes
    ``publish_state()`` / ``publish_default_state()`` for injecting state
    messages and ``dispatch_once()`` for processing a single command from
    within the test body. A background thread continuously dispatches
    commands so heartbeats are processed without test code needing to call
    ``dispatch_once()`` in a loop.
    """
    own_ctx = context is None
    ctx: zmq.Context[Any] = context if context is not None else zmq.Context()

    publisher = EventPublisher(ctx, state_address)
    cmd_server = CommandServer(command_address, context=ctx)

    # Default heartbeat handler
    cmd_server.register_handler("heartbeat", lambda _p: {"timeout_ms": 500})

    for name, handler in (handlers or {}).items():
        cmd_server.register_handler(name, handler)

    stop_event = threading.Event()
    handle = _Handle(publisher=publisher, cmd_server=cmd_server, context=ctx)

    dispatch_thread = threading.Thread(
        target=handle.dispatch_until_shutdown,
        args=(stop_event,),
        daemon=True,
        name="FakeServer-dispatch",
    )
    dispatch_thread.start()

    try:
        yield handle
    finally:
        stop_event.set()
        dispatch_thread.join(timeout=2.0)
        publisher.close()
        cmd_server.close()
        if own_ctx:
            ctx.term()
