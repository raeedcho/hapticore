"""Serial-wire encoders for the Teensy sync hub.

Each function takes well-typed Python values and returns an ASCII bytes
string terminated by ``\\n``, matching the protocol documented in
``docs/architecture.md`` § Serial protocol. Range validation happens here;
callers can trust that anything emitted by these functions is a
well-formed Teensy command.
"""

from __future__ import annotations

# Wire-protocol bounds. Enforced at the shim boundary so callers that
# provide out-of-range values see a clear ValueError with the bound named.
EVENT_CODE_MIN: int = 0
EVENT_CODE_MAX: int = 255  # 8-bit parallel per ADR-014

REWARD_MS_MIN: int = 1
REWARD_MS_MAX: int = 10_000  # 10 s sanity ceiling; real rewards are ~50-300 ms

CAMERA_RATE_MIN_HZ: float = 1.0
CAMERA_RATE_MAX_HZ: float = 500.0  # Teensy IntervalTimer well above target rates


def format_start_sync() -> bytes:
    return b"S1\n"


def format_stop_sync() -> bytes:
    return b"S0\n"


def format_start_camera_trigger() -> bytes:
    return b"T1\n"


def format_stop_camera_trigger() -> bytes:
    return b"T0\n"


def format_set_camera_rate(rate_hz: float) -> bytes:
    """Encode a camera-trigger rate command. Rate is rounded to the nearest int Hz."""
    if not (CAMERA_RATE_MIN_HZ <= rate_hz <= CAMERA_RATE_MAX_HZ):
        raise ValueError(
            f"camera trigger rate {rate_hz} Hz outside "
            f"[{CAMERA_RATE_MIN_HZ}, {CAMERA_RATE_MAX_HZ}] Hz"
        )
    return f"C{round(rate_hz)}\n".encode("ascii")


def format_event_code(code: int) -> bytes:
    """Encode an 8-bit event code. ADR-014."""
    if not (EVENT_CODE_MIN <= code <= EVENT_CODE_MAX):
        raise ValueError(
            f"event code {code} outside [{EVENT_CODE_MIN}, {EVENT_CODE_MAX}]"
        )
    return f"E{code}\n".encode("ascii")


def format_reward_ms(duration_ms: int) -> bytes:
    """Encode a reward-pulse command. Duration in milliseconds."""
    if not (REWARD_MS_MIN <= duration_ms <= REWARD_MS_MAX):
        raise ValueError(
            f"reward duration {duration_ms} ms outside "
            f"[{REWARD_MS_MIN}, {REWARD_MS_MAX}] ms"
        )
    return f"R{duration_ms}\n".encode("ascii")
