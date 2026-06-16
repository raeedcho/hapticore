# Teensy 4.1 sync hub — bringup guide

This document walks a technician through wiring, flashing, and validating the Teensy 4.1 sync hub for the Hapticore rig. Each section is self-contained and testable independently. Work through them in order; do not skip ahead.

> **This is the authoritative wiring reference.** The Teensy wiring section in `docs/rig-setup.md` has outdated placeholder pin labels. Use this document, which reflects the actual pin assignments in `firmware/teensy/src/pins.h`.

**Prerequisites:**

- The Teensy 4.1 is soldered to a breadboard with header pins.
- The rig computer (Linux) has the Hapticore repo cloned and `pixi` installed.
- The rig computer is connected to the Ripple Scout via Ethernet and can open Trellis.
- A USB-A to Micro-B cable for the Teensy.
- BNC cables, hookup wire, and a 25-pin D-sub connector or the Ripple Digital BNC Breakout adapter (PN: R01396) are available.

**Equipment on hand:** multimeter, wire strippers, soldering iron (for D-sub connector only).

**Important: Scout connector types.** The Scout's built-in Digital I/O uses **BNC connectors** (not SMA) for the 4 digital input channels, and a **standard 25-pin D-sub** (not Micro-D) for the parallel port. The xipppy documentation calls these "SMA" channels regardless of the physical connector.

---

## Step 0 — Flash the firmware

Connect the Teensy to the rig computer via USB. The Teensy shows up as `/dev/ttyACM0` (or similar — check `ls /dev/ttyACM*`).

```bash
cd hapticore/firmware/teensy
pixi run -- pio run -t upload
```

If the upload stalls, press the small button on the Teensy 4.1 to enter bootloader mode, then retry.

**Pass:** PlatformIO reports "SUCCESS" and the upload completes.

**Fail:** If `pio` is not found, run `pixi install` first from the repo root.  If the upload fails with "no device found," check the USB cable and try a different port.

---

## Step 1 — Verify USB serial

```bash
pixi shell
python tools/teensy_bringup.py --step serial
```

This opens the serial port, sends a sync start/stop command, and closes.  It does not test any output signals — just confirms that the rig computer can talk to the Teensy over USB serial.

**Note:** The Teensy resets when the serial port is opened. The script waits 2 seconds for the firmware to boot before sending commands. This is normal.

**Pass:** Script prints "Serial connection OK."

**Fail:** Check that `/dev/ttyACM*` exists (`ls /dev/ttyACM*`). Try unplugging and re-plugging the USB cable. If the device shows up as a different path (e.g., `/dev/ttyACM1`), pass `--port /dev/ttyACM1` to the script.

---

## Step 2 — Wire and test the sync pulse

### Wiring

| Teensy pin | Signal | Scout connector | Notes |
|---|---|---|---|
| Pin 2 | `SYNC_PULSE` | DIO BNC Input 1 | Short BNC cable, <30 cm |
| GND (any) | Ground | DIO BNC Input 1 shield | Shared ground required |

Use a BNC cable with one end stripped. Solder or clip the center conductor to Teensy pin 2 and the shield to any Teensy GND pin. Connect the BNC end to the leftmost BNC input on the Scout's front-panel Digital I/O section.

### Trellis setup

On the Trellis computer, open the Digital I/O display panel. You should see 4 BNC channels. Channel 1 corresponds to the first BNC input.

### Test

```bash
python tools/teensy_bringup.py --step sync
```

The script sends `S1` (start sync), waits 10 seconds, then sends `S0` (stop).

**What to look for in Trellis:** A 1 Hz blinking event on DIO channel 1 for 10 seconds, then silence.

**Pass:** You see regular 1 Hz blips in Trellis for the duration.

**Fail — no signal at all:** Check BNC connection. Verify center conductor goes to pin 2, shield goes to GND. Use a multimeter in continuity mode to verify the cable.

**Fail — erratic signal:** Check for loose breadboard connections. The Teensy's 3.3V output should register as "high" on the Scout (LVTTL threshold is 2.0V).  Verify with a multimeter that pin 2 measures ~3.3V when high, <0.1V when low.

---

## Step 3 — Wire and test the camera trigger

### Wiring

| Teensy pin | Signal | Scout connector |
|---|---|---|
| Pin 3 | `CAMERA_TRIGGER` | DIO BNC Input 2 |
| GND | Ground | BNC shield |

Same cable style as Step 2.

### Test

```bash
python tools/teensy_bringup.py --step camera
```

The script sends `C5` (5 Hz — slow enough to see), then `T1` (start), waits 10 seconds, then `T0` (stop). Then repeats at 30 Hz for 5 seconds.

**What to look for in Trellis:** ~50 events at 5 Hz (one every 200 ms) on DIO channel 2, then ~150 events at 30 Hz (much faster), then silence.

**Pass:** Both rates are visible and look correct by eye.

**Fail:** Same debugging steps as Step 2. Also verify pin 3 is the correct Teensy pin (count from the corner).

---

## Step 4 — Wire and test the reward TTL

### Wiring

| Teensy pin | Signal | Scout connector |
|---|---|---|
| Pin 4 | `REWARD` | DIO BNC Input 3 |
| GND | Ground | BNC shield |

### Test

```bash
python tools/teensy_bringup.py --step reward
```

The script sends `R500` (500 ms pulse), waits 2 seconds, then `R100` (100 ms pulse).

**What to look for in Trellis:** One long blip (~500 ms) on DIO channel 3, then a short blip (~100 ms).

**Pass:** Both pulses are visible and the long one is visibly longer.

**Fail:** Same debugging as above.

---

## Step 5 — Wire and test event codes (parallel port)

This is the most complex wiring step. The Teensy's 8 data lines and 1 strobe line connect to the Scout's 25-pin D-sub parallel input.

### Option A: Using the Ripple Digital BNC Breakout (PN: R01396)

If the lab has this adapter, connect it to the Scout's 25-pin D-sub input connector. Then wire 9 BNC cables:

| Teensy pin | Signal | Breakout BNC |
|---|---|---|
| Pin 14 | `EVENT_CODE_BIT0` | Data 0 (bit 0) |
| Pin 15 | `EVENT_CODE_BIT1` | Data 1 (bit 1) |
| Pin 16 | `EVENT_CODE_BIT2` | Data 2 (bit 2) |
| Pin 17 | `EVENT_CODE_BIT3` | Data 3 (bit 3) |
| Pin 18 | `EVENT_CODE_BIT4` | Data 4 (bit 4) |
| Pin 19 | `EVENT_CODE_BIT5` | Data 5 (bit 5) |
| Pin 20 | `EVENT_CODE_BIT6` | Data 6 (bit 6) |
| Pin 21 | `EVENT_CODE_BIT7` | Data 7 (bit 7) |
| Pin 22 | `EVENT_CODE_STROBE` | Strobe A |
| GND | Ground | Any ground BNC |

### Option B: Custom D-sub cable

Solder a 25-pin D-sub male connector with the following pinout (from Grapevine User Manual Figure A-5):

| D-sub pin | Signal | Teensy pin |
|---|---|---|
| 1 | Data bit 0 | Pin 14 |
| 14 | Data bit 1 | Pin 15 |
| 2 | Data bit 2 | Pin 16 |
| 15 | Data bit 3 | Pin 17 |
| 3 | Data bit 4 | Pin 18 |
| 16 | Data bit 5 | Pin 19 |
| 4 | Data bit 6 | Pin 20 |
| 17 | Data bit 7 | Pin 21 |
| 8 | Strobe A | Pin 22 |
| 13, 25 | Ground | GND |

**Important:** Verify the D-sub pin numbering against Figure A-5 in the Grapevine User Manual before soldering. The numbering above is from that figure but should be double-checked — a single swapped pin will produce garbled event codes.

### Trellis setup

In Trellis, verify that the Digital I/O parallel port display is visible.  The default strobe mode (capture on strobe high) is correct for our firmware.  Do not change it to "bit-change" mode.

### Test

```bash
python tools/teensy_bringup.py --step events
```

The script sends a series of test codes:

| Code | Binary | What to look for |
|---|---|---|
| `E170` | `10101010` | Alternating bits — catches swapped adjacent wires |
| `E85` | `01010101` | Complement of above |
| `E255` | `11111111` | All bits high |
| `E1` | `00000001` | Only bit 0 |
| `E128` | `10000000` | Only bit 7 |
| `E0` | `00000000` | All bits low (verify the event count, not the value — Trellis may display 0 less prominently since it matches the idle bus state) |

**What to look for in Trellis:** The parallel port value display should show the expected decimal value for each code. Each code appears as a separate timestamped event.

**Pass:** All six codes show the correct value in Trellis (check the event count is 6).

**Fail — all codes show 0:** Strobe wire is disconnected or on the wrong D-sub pin.

**Fail — some bits are always 0 or always 1:** That specific data wire is disconnected or shorted. Use the single-bit codes (`E1` through `E128`) to isolate which bit.

**Fail — values are present but wrong:** Wires are swapped. Compare the binary representation of what Trellis shows with what was sent; the mismatch pattern tells you which wires are swapped.

---

## Step 6 — Combined test

All wiring is in place. This test runs all signals simultaneously to verify they don't interfere.

```bash
python tools/teensy_bringup.py --step combined
```

The script runs for 30 seconds with:
- Sync pulse at 1 Hz (running throughout)
- Camera trigger at 60 Hz (running throughout)
- 20 event codes sent at random intervals
- 3 reward pulses at 200 ms each

**What to look for in Trellis:**
- Steady 1 Hz sync on BNC Input 1
- Dense 60 Hz camera triggers on BNC Input 2
- Three blips on BNC Input 3 (reward)
- 20 event codes on the parallel port display, with correct values

**Pass:** All four signal types are visible simultaneously and the sync pulse doesn't glitch when event codes fire.

**Fail — sync or camera drops during event codes:** This would indicate a firmware bug in the interrupt handling. Note the exact symptom and report it.

**Fail — camera trigger count looks low at 60 Hz:** The Scout's digital inputs sample at 10 kS/s with a 67 µs minimum event width. The camera trigger pulse is 100 µs, which just clears this threshold. If events seem to be dropped, try the test again with `--step camera` at 30 Hz to confirm the wiring is correct before suspecting a rate issue.

---

## Step 7 — Automated verification via xipppy (optional)

If `xipppy` is installed on the rig computer, the bringup script can read digital inputs directly from the Scout and verify values automatically, without requiring someone to watch Trellis.

```bash
python tools/teensy_bringup.py --step verify-xipppy
```

This step:
1. Connects to the Scout via xipppy
2. Drains any pending digital input events
3. Sends each command from Steps 2–5
4. Reads `digin()` events and asserts correct values and timing
5. Prints a summary with PASS/FAIL for each signal type

**Pass:** All assertions pass.

**Fail:** The script prints which signal failed and what value was received vs. expected. This is much more specific than Trellis-by-eye; use it to pinpoint wiring errors.

---

## After bringup

Once all steps pass:

1. Secure all wiring. Breadboard connections should be replaced with soldered connections for anything that will stay long-term.
2. Label each cable at both ends (Teensy side and Scout side).
3. Take a photo of the final wiring and save it somewhere
4. Run `python tools/teensy_bringup.py --step all` one final time to confirm everything works end-to-end.

---

## Quick reference: pin map

| Teensy pin | Function | Scout connection |
|---|---|---|
| 2 | Sync pulse (1 Hz) | BNC Input 1 |
| 3 | Camera trigger | BNC Input 2 |
| 4 | Reward TTL | BNC Input 3 |
| 14–21 | Event code bits 0–7 | D-sub data 0–7 |
| 22 | Event code strobe | D-sub Strobe A |
| GND | Common ground | BNC shields + D-sub ground pins |

All pin assignments are defined in `firmware/teensy/src/pins.h`. If any pin needs to change (e.g., due to a physical conflict on the breadboard), edit that file, recompile and reflash (`pixi run -- pio run -t upload` from the `firmware/teensy/` directory), and re-run the tests.
