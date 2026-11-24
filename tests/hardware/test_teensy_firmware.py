"""Teensy firmware tests, with the Ripple Scout as the measuring instrument.

The Teensy's outputs are wired to the Scout's digital inputs, and the tests read
what the Scout captured through ``xipppy.digin()`` (see ``ripple_digin.py``).

Prerequisites:
    1. The bench is wired per docs/teensy-bringup.md: sync pin 2 to Scout DIO BNC
       Input 1; event-code data pins 14-21 and strobe pin 22 to the Scout's
       parallel port (data bits 0-7 and Strobe A); grounds shared.
    2. Trellis is running, with the parallel port in its default
       capture-on-strobe mode (ADR-014).
    3. xipppy is installed (``pixi run check-xipppy``).
    4. Nothing else has the Teensy's serial port open.

Run (takes about 10 s):

    pixi run test-hardware -k teensy

Add ``--teensy-port /dev/ttyACM1`` if the Teensy is not on the default port.
The ``bench`` fixture runs first. If the Scout cannot see the Teensy's sync
output, it fails every test in this module at once, with the likely cause.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from itertools import pairwise

import pytest

from hapticore.sync import protocol
from hapticore.sync.teensy_serial import TeensySerialClient

from .ripple_digin import DiginRecorder, parallel_codes

pytestmark = [pytest.mark.hardware, pytest.mark.usefixtures("bench")]

N_BITS = 8
N_CODES = 1 << N_BITS

# Each code takes 2 ms (ADR-014: 0.5 ms settle, 1 ms strobe, 0.5 ms clear). The Scout
# samples at 10 kS/s, so measured spacings can be off by one 0.1 ms sample.
MIN_CODE_SPACING_S = 1.8e-3
MEDIAN_CODE_SPACING_S = (1.9e-3, 2.2e-3)


def _hex(values: Sequence[int], limit: int = 16) -> str:
    """Format 16-bit words as hex, or "none" if there are none; long lists are cut off."""
    if not values:
        return "none"
    text = ", ".join(f"0x{value:04X}" for value in values[:limit])
    return text if len(values) <= limit else f"{text}, … ({len(values) - limit} more)"


# Keep the event-code tests first in this module: later tests use event codes as
# timing markers.


def test_event_code_walking_bits(teensy: TeensySerialClient, ripple: DiginRecorder) -> None:
    """Each data line arrives as exactly one code, with only its own bit set.

    The failure message lists all eight bits, so a single run shows the whole
    wiring map of the D-sub cable.
    """
    received: dict[int, list[int]] = {}
    for bit in range(N_BITS):
        sent = 1 << bit
        ripple.clear()
        teensy.write(protocol.format_event_code(sent))
        ripple.wait(0.1)
        received[sent] = [code.value for code in parallel_codes(ripple.events())]

    if any(values != [sent] for sent, values in received.items()):
        rows = "\n".join(
            f"  bit {sent.bit_length() - 1}: 0x{sent:04X} → {_hex(values)}"
            + ("" if values == [sent] else "   ← wrong")
            for sent, values in received.items()
        )
        pytest.fail(
            "Each walking-bit event code must arrive as exactly one code whose 16-bit "
            f"value equals what was sent (sent → received):\n{rows}\n"
            "A code in the high byte (0x0100 and up) means the data wires reach the Scout's "
            "parallel bits 8-15 instead of 0-7. Another wrong value means a swapped, open, "
            "or shorted data wire. 'none' means no code arrived at all: check the strobe wire "
            "(Teensy pin 22 → Strobe A) and that Trellis captures on the strobe. "
            "D-sub pinout: docs/teensy-bringup.md, Step 5.",
            pytrace=False,
        )


def test_event_code_all_values(teensy: TeensySerialClient, ripple: DiginRecorder) -> None:
    """Codes 0-255 sent in one write arrive once each, in order, 2 ms apart (ADR-014)."""
    ripple.clear()
    teensy.write(b"".join(protocol.format_event_code(value) for value in range(N_CODES)))
    ripple.wait(1.0)
    codes = parallel_codes(ripple.events())
    values = [code.value for code in codes]
    expected = list(range(N_CODES))

    if len(codes) > N_CODES:
        pytest.fail(
            f"{len(codes)} codes arrived for the {N_CODES} sent. The Scout is probably "
            "capturing on data-line changes rather than on the strobe; set Trellis back to "
            "capture on the strobe (ADR-014).",
            pytrace=False,
        )
    if values != expected:
        missing = sorted(set(expected) - set(values))
        unexpected = sorted(set(values) - set(expected))
        first = next(
            (i for i, (got, want) in enumerate(zip(values, expected, strict=False)) if got != want),
            len(values),
        )
        got = f"0x{values[first]:04X}" if first < len(values) else "nothing"
        pytest.fail(
            f"{len(codes)} codes arrived for the {N_CODES} sent, and they are not 0-255 in "
            f"order. First difference at position {first}: expected 0x{expected[first]:04X}, "
            f"got {got}. Missing: {_hex(missing)}. Unexpected: {_hex(unexpected)}.",
            pytrace=False,
        )

    spacings = [later.t_s - earlier.t_s for earlier, later in pairwise(codes)]
    median = statistics.median(spacings)
    low, high = MEDIAN_CODE_SPACING_S
    if min(spacings) < MIN_CODE_SPACING_S or not low <= median <= high:
        pytest.fail(
            "Event codes are not spaced as the 2 ms strobe sequence should leave them: "
            f"smallest spacing {min(spacings) * 1e3:.2f} ms (at least "
            f"{MIN_CODE_SPACING_S * 1e3:.1f} ms), median {median * 1e3:.2f} ms (between "
            f"{low * 1e3:.1f} and {high * 1e3:.1f} ms).",
            pytrace=False,
        )
