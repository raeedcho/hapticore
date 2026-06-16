#!/usr/bin/env python3
"""Teensy 4.1 sync hub — interactive bringup and validation tool.

Walks through each output signal one at a time, with clear
prompts and pass/fail output. Designed to be run from a terminal with
the Teensy connected via USB.

Usage:
    pixi shell
    python tools/teensy_bringup.py                   # run all steps
    python tools/teensy_bringup.py --step sync        # run one step
    python tools/teensy_bringup.py --port /dev/ttyACM1  # non-default port
    python tools/teensy_bringup.py --step verify-xipppy  # automated via Scout

Steps (in order):
    serial    — verify USB serial connection
    sync      — 1 Hz sync pulse on BNC Input 1
    camera    — camera trigger on BNC Input 2
    reward    — reward TTL on BNC Input 3
    events    — 8-bit parallel event codes on D-sub
    combined  — all signals simultaneously
    verify-xipppy — automated readback via xipppy (requires Scout + xipppy)

See docs/teensy-bringup.md for the full wiring guide.
"""

from __future__ import annotations

import argparse
import random
import sys
import time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def open_serial(port: str, baud: int = 115200) -> object:
    """Open the Teensy serial port. Returns a pyserial Serial object."""
    import serial  # noqa: PLC0415

    try:
        s = serial.Serial(port=port, baudrate=baud, timeout=0.5)
        # Teensy 4.1 resets when the serial port is opened (USB CDC DTR).
        # Wait for the firmware to boot before sending commands.
        time.sleep(2.0)
        return s
    except Exception as e:
        print(f"\n  FAIL: Could not open {port}: {e}")
        print("  Check that the Teensy is connected and the port is correct.")
        print("  List ports with: ls /dev/ttyACM*")
        sys.exit(1)


def send(ser: object, cmd: str) -> None:
    """Send an ASCII command to the Teensy (adds newline)."""
    ser.write(f"{cmd}\n".encode("ascii"))  # type: ignore[union-attr]


def prompt_confirm(question: str) -> bool:
    """Ask the operator a yes/no question. Returns True for yes."""
    while True:
        try:
            response = input(f"\n  >>> {question} [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(1)
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("  Please type 'y' or 'n'.")


def wait_with_countdown(seconds: int, label: str = "") -> None:
    """Wait with a visible countdown."""
    for i in range(seconds, 0, -1):
        suffix = f" — {label}" if label else ""
        print(f"  {i}s remaining{suffix}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 60, end="\r")  # clear line


def section_header(title: str) -> None:
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def result(passed: bool, label: str) -> bool:
    """Print and return a pass/fail result."""
    tag = "PASS" if passed else "FAIL"
    print(f"\n  [{tag}] {label}")
    return passed


# ---------------------------------------------------------------------------
# Test steps
# ---------------------------------------------------------------------------


def step_serial(port: str) -> bool:
    """Step 0: Verify USB serial connection."""
    section_header("Step 0: USB serial connection")
    print("\n  Opening serial port...")

    ser = open_serial(port)
    send(ser, "S1")
    time.sleep(0.1)
    send(ser, "S0")
    ser.close()  # type: ignore[union-attr]

    return result(True, "Serial connection OK")


def step_sync(port: str) -> bool:
    """Step 2: 1 Hz sync pulse."""
    section_header("Step 2: Sync pulse (1 Hz)")
    print("""
  This test sends a 1 Hz sync pulse for 10 seconds.

  LOOK FOR: Regular 1 Hz blinking on Scout DIO BNC Input 1
  in the Trellis Digital I/O display.
    """)

    input("  Press Enter to start (or Ctrl-C to abort)...")

    ser = open_serial(port)
    send(ser, "S1")
    print("\n  Sync pulse running...")
    wait_with_countdown(10, "sync active")
    send(ser, "S0")
    print("  Sync pulse stopped.")
    ser.close()  # type: ignore[union-attr]

    return result(
        prompt_confirm("Did you see a steady 1 Hz blink on DIO Input 1?"),
        "Sync pulse",
    )


def step_camera(port: str) -> bool:
    """Step 3: Camera trigger at two rates."""
    section_header("Step 3: Camera trigger")
    print("""
  This test sends camera triggers at two rates:
    Phase 1: 5 Hz for 10 seconds (slow — easy to see individual pulses)
    Phase 2: 30 Hz for 5 seconds (fast — looks like a dense raster)

  LOOK FOR: Events on Scout DIO BNC Input 2 in Trellis.
    """)

    input("  Press Enter to start...")

    ser = open_serial(port)

    print("\n  Phase 1: 5 Hz for 10 seconds...")
    send(ser, "C5")
    send(ser, "T1")
    wait_with_countdown(10, "5 Hz camera trigger")
    send(ser, "T0")

    time.sleep(1)

    print("  Phase 2: 30 Hz for 5 seconds...")
    send(ser, "C30")
    send(ser, "T1")
    wait_with_countdown(5, "30 Hz camera trigger")
    send(ser, "T0")

    ser.close()  # type: ignore[union-attr]

    return result(
        prompt_confirm(
            "Did you see slow pulses (5 Hz) then fast pulses (30 Hz) on Input 2?"
        ),
        "Camera trigger",
    )


def step_reward(port: str) -> bool:
    """Step 4: Reward TTL pulse."""
    section_header("Step 4: Reward TTL")
    print("""
  This test sends two reward pulses:
    Pulse 1: 500 ms (long — clearly visible)
    Pulse 2: 100 ms (short)

  LOOK FOR: Two blips on Scout DIO BNC Input 3, the first noticeably
  longer than the second.
    """)

    input("  Press Enter to start...")

    ser = open_serial(port)

    print("\n  Sending 500 ms reward pulse...")
    send(ser, "R500")
    time.sleep(2)

    print("  Sending 100 ms reward pulse...")
    send(ser, "R100")
    time.sleep(1)

    ser.close()  # type: ignore[union-attr]

    return result(
        prompt_confirm("Did you see two blips on Input 3 (long then short)?"),
        "Reward TTL",
    )


def step_events(port: str) -> bool:
    """Step 5: 8-bit parallel event codes."""
    section_header("Step 5: Event codes (parallel port)")
    print("""
  This test sends six event codes through the 8-bit parallel bus.
  Each code tests a different wiring pattern.

  LOOK FOR: The parallel port value in Trellis should match each
  code listed below. Check that the total event count is 6.

  Codes to be sent:
    E170  = 10101010  (alternating bits)
    E85   = 01010101  (complement)
    E255  = 11111111  (all high)
    E1    = 00000001  (bit 0 only)
    E128  = 10000000  (bit 7 only)
    E0    = 00000000  (all low — may look like idle; verify event count)
    """)

    input("  Press Enter to start...")

    ser = open_serial(port)

    test_codes = [
        (170, "10101010 — alternating bits"),
        (85, "01010101 — complement"),
        (255, "11111111 — all high"),
        (1, "00000001 — bit 0 only"),
        (128, "10000000 — bit 7 only"),
        (0, "00000000 — all low"),
    ]

    for code, description in test_codes:
        print(f"\n  Sending E{code}  ({description})")
        send(ser, f"E{code}")
        time.sleep(0.5)

    ser.close()  # type: ignore[union-attr]

    print("\n  All codes sent.")
    return result(
        prompt_confirm("Did all six codes show the correct values in Trellis?"),
        "Event codes",
    )


def step_combined(port: str) -> bool:
    """Step 6: All signals simultaneously."""
    section_header("Step 6: Combined test (30 seconds)")
    print("""
  This test runs all four signal types at once for 30 seconds:
    - Sync pulse at 1 Hz (BNC Input 1)
    - Camera trigger at 60 Hz (BNC Input 2)
    - 20 event codes at random intervals (parallel port)
    - 3 reward pulses at 200 ms each (BNC Input 3)

  LOOK FOR: All four channels active in Trellis simultaneously.
  The sync pulse should NOT glitch when event codes fire.
    """)

    input("  Press Enter to start...")

    ser = open_serial(port)

    # Start continuous signals
    send(ser, "S1")
    send(ser, "C60")
    send(ser, "T1")
    print("\n  Sync + camera running. Sending events and rewards over 30s...")

    # Schedule events and rewards across the 30-second window
    event_times = sorted(random.uniform(1, 28) for _ in range(20))
    reward_times = [5.0, 15.0, 25.0]
    event_codes = [random.randint(1, 255) for _ in range(20)]

    start = time.monotonic()
    event_idx = 0
    reward_idx = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= 30:
            break

        # Send scheduled event codes
        if event_idx < len(event_times) and elapsed >= event_times[event_idx]:
            code = event_codes[event_idx]
            send(ser, f"E{code}")
            print(f"  [{elapsed:5.1f}s] Sent E{code}")
            event_idx += 1

        # Send scheduled rewards
        if reward_idx < len(reward_times) and elapsed >= reward_times[reward_idx]:
            send(ser, "R200")
            print(f"  [{elapsed:5.1f}s] Sent R200")
            reward_idx += 1

        time.sleep(0.05)

    # Stop continuous signals
    send(ser, "T0")
    send(ser, "S0")
    print("\n  All signals stopped.")
    ser.close()  # type: ignore[union-attr]

    return result(
        prompt_confirm(
            "Did all four signal types work simultaneously without glitches?"
        ),
        "Combined test",
    )


def step_verify_xipppy(port: str) -> bool:
    """Step 7: Automated verification via xipppy digin()."""
    section_header("Step 7: Automated verification via xipppy")

    try:
        import xipppy as xp  # noqa: PLC0415
    except ImportError:
        print("\n  xipppy is not installed. Skipping automated verification.")
        print("  This step is optional — manual Trellis verification is sufficient.")
        return result(True, "xipppy verification (skipped — not installed)")

    import numpy as np  # noqa: PLC0415

    all_passed = True

    with xp.xipppy_open():
        ser = open_serial(port)

        # --- Sync pulse ---
        print("\n  Testing sync pulse via digin()...")
        xp.digin(max_events=1024)  # drain buffer
        send(ser, "S1")
        time.sleep(5.5)
        send(ser, "S0")
        time.sleep(0.5)

        n, events = xp.digin(max_events=1024)
        # reason bitmask: 0x02 = SMA1 / BNC Input 1 on Scout
        sma1_events = [e for e in events if e.reason & 0x02]
        if len(sma1_events) >= 4:
            timestamps = np.array([e.timestamp / 30000.0 for e in sma1_events])
            intervals = np.diff(timestamps)
            mean_period = float(np.mean(intervals))
            std_period = float(np.std(intervals))
            print(f"    Sync events: {len(sma1_events)}")
            print(f"    Mean period: {mean_period:.4f} s (expect ~0.5 or ~1.0)")
            print(f"    Std period:  {std_period:.6f} s")
            # Accept ~0.5s (both edges) or ~1.0s (one edge) depending on Scout config
            sync_ok = 0.4 <= mean_period <= 1.1 and std_period < 0.01
        else:
            print(f"    Only {len(sma1_events)} SMA1 events (expected >=4)")
            sync_ok = False
        all_passed &= result(sync_ok, "Sync pulse timing")

        # --- Event codes ---
        print("\n  Testing event codes via digin()...")
        xp.digin(max_events=1024)  # drain
        test_codes = [42, 170, 85, 255, 1, 128]
        for code in test_codes:
            send(ser, f"E{code}")
            time.sleep(0.1)
        time.sleep(0.5)

        n, events = xp.digin(max_events=1024)
        # reason bitmask: 0x01 = parallel port
        parallel_events = [e for e in events if e.reason & 0x01]
        received_codes = [e.parallel & 0xFF for e in parallel_events]
        print(f"    Sent:     {test_codes}")
        print(f"    Received: {received_codes}")
        codes_ok = received_codes == test_codes
        all_passed &= result(codes_ok, "Event code values")

        # --- Reward ---
        print("\n  Testing reward via digin()...")
        xp.digin(max_events=1024)  # drain
        send(ser, "R300")
        time.sleep(1.0)

        n, events = xp.digin(max_events=1024)
        # reason bitmask: 0x08 = SMA3 / BNC Input 3 on Scout
        sma3_events = [e for e in events if e.reason & 0x08]
        print(f"    SMA3 events: {len(sma3_events)}")
        # Expect at least one edge event; exact count depends on Scout sampling
        reward_ok = len(sma3_events) >= 1
        all_passed &= result(reward_ok, "Reward TTL detected")

        # --- Camera trigger ---
        print("\n  Testing camera trigger via digin()...")
        xp.digin(max_events=1024)  # drain
        send(ser, "C10")
        send(ser, "T1")
        time.sleep(3.0)
        send(ser, "T0")
        time.sleep(0.5)

        n, events = xp.digin(max_events=1024)
        # reason bitmask: 0x04 = SMA2 / BNC Input 2 on Scout
        sma2_events = [e for e in events if e.reason & 0x04]
        print(f"    SMA2 events: {len(sma2_events)} (expect ~30 at 10 Hz)")
        camera_ok = 20 <= len(sma2_events) <= 40
        all_passed &= result(camera_ok, "Camera trigger rate")

        ser.close()  # type: ignore[union-attr]

    return all_passed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STEPS = {
    "serial": step_serial,
    "sync": step_sync,
    "camera": step_camera,
    "reward": step_reward,
    "events": step_events,
    "combined": step_combined,
    "verify-xipppy": step_verify_xipppy,
}

STEP_ORDER = ["serial", "sync", "camera", "reward", "events", "combined"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Teensy 4.1 sync hub bringup tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See docs/teensy-bringup.md for the full wiring guide.",
    )
    parser.add_argument(
        "--port",
        default="/dev/ttyACM0",
        help="Teensy serial port (default: /dev/ttyACM0)",
    )
    parser.add_argument(
        "--step",
        choices=list(STEPS.keys()) + ["all"],
        default="all",
        help="Which step to run (default: all)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  Hapticore Teensy 4.1 Sync Hub — Bringup Tool")
    print("=" * 60)
    print(f"\n  Serial port: {args.port}")

    if args.step == "all":
        steps_to_run = STEP_ORDER
    else:
        steps_to_run = [args.step]

    results: dict[str, bool] = {}
    for step_name in steps_to_run:
        fn = STEPS[step_name]
        try:
            results[step_name] = fn(args.port)
        except KeyboardInterrupt:
            print("\n\n  Aborted by user.")
            results[step_name] = False
            break

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        tag = "PASS" if passed else "FAIL"
        print(f"    [{tag}] {name}")

    failed = [n for n, p in results.items() if not p]
    if failed:
        print(f"\n  {len(failed)} step(s) failed: {', '.join(failed)}")
        print("  See docs/teensy-bringup.md for troubleshooting.")
        sys.exit(1)
    else:
        print(f"\n  All {len(results)} step(s) passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
