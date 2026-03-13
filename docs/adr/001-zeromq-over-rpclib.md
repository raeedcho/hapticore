# ADR-001: ZeroMQ + msgpack for inter-process communication

**Status:** Accepted  
**Date:** 2026-03-11  
**Context:** The system needs IPC between 4–6 processes (haptic server, task controller, display, sync, recording wrappers) on the same machine, with latency < 1 ms for state streaming at 200+ Hz.

## Decision

Use ZeroMQ with msgpack serialization for all inter-process communication. PUB-SUB for event/state distribution, DEALER-ROUTER for command/response.

## Alternatives considered

**rpclib (msgpack-RPC):** Used in the predecessor hapticEnvironment codebase (mfliu/hapticEnvironment). Provides elegant typed function binding in C++, but both rpclib (last release 2021, seeking maintainers) and its Python client msgpack-rpc-python (abandoned, Python 3 compatibility issues) are effectively unmaintained. Point-to-point only — no pub-sub for multi-subscriber state streaming. Latency ~50–120 µs per call.

**gRPC:** Strong schema enforcement via protobuf, excellent Python/C++ codegen. But ~120–170 µs latency per unary call (5–10× slower than ZeroMQ), HTTP/2 framing overhead is unnecessary for same-machine IPC, and head-of-line blocking on bidirectional streams can delay state updates.

**Raw UDP:** Lowest latency (~2–5 µs) but requires manual message framing, serialization, command routing, and subscriber management. Used by the original djoshea/haptic-control system with an ad-hoc binary format that was fragile and undocumented.

## Rationale

ZeroMQ provides ~20–50 µs latency on ipc:// transport (well within the 1 ms budget), native PUB-SUB for multi-subscriber state distribution (critical for the display, sync, and recording processes all needing haptic state), DEALER-ROUTER for reliable command/response, and excellent C++ (cppzmq) and Python (pyzmq, ~40M monthly downloads) bindings. Pairing with msgpack preserves the same serialization format as rpclib, easing migration of the haptic server.

## Consequences

- No automatic type-safe function binding (unlike rpclib). Commands are dispatched by string method names, requiring a thin dispatcher on the server side.
- ZeroMQ PUB-SUB has a slow-joiner problem: subscribers may miss early messages after connecting. Must handle gracefully.
- libzmq core library has been dormant since May 2024, but pyzmq vendors it in wheels and is actively maintained. Monitor for successor activity.
- macOS limits Unix domain socket paths to 103 characters (`sun_path` is 104 bytes in XNU). IPC socket paths must be rooted in `/tmp` with short names — never use `tempfile.gettempdir()` or pytest's `tmp_path` as a base directory on macOS. Use `hapticore.core.messaging.make_ipc_address()` to generate safe paths.
