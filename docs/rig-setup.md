# Rig Machine Setup

First-time setup for running Hapticore with real hardware on the Ubuntu rig machine. This covers the haptic server (C++ with the delta.3) and the hardware test suite. For development with mock hardware on macOS, see `cpp/haptic_server/BUILDING.md`.

**Why pixi?** Hapticore uses [pixi](https://pixi.sh) for environment management. Pixi manages both the Python environment and C++ build tools (cmake, ninja) from a single lockfile (`pixi.lock`), while keeping system libraries (libzmq, libusb) outside the environment to avoid linking conflicts with the proprietary Force Dimension SDK. This means a new developer only needs a few `apt install` packages and `pixi install` to get a fully working environment.

## Prerequisites

### System packages

Only a few system packages are needed — everything else (Python, cmake, ninja, pytest, ruff, mypy) comes from pixi:

```bash
sudo apt update
sudo apt install -y \
    build-essential g++ \
    libzmq3-dev \
    libusb-1.0-0-dev
```

### Install pixi

```bash
curl -fsSL https://pixi.sh/install.sh | bash
```

Restart your shell (or `source ~/.bashrc`) after installation.

### Force Dimension SDK

The delta.3 requires the Force Dimension SDK (proprietary, not redistributable). Download from [forcedimension.com](https://www.forcedimension.com/software) and extract it:

```bash
sudo mkdir -p /opt/forcedimension
sudo tar xf sdk-3.17.x-linux-x86_64.tar.gz -C /opt/forcedimension
```

Key details about the SDK layout (discovered during hardware bring-up):

- **Headers** are named `dhdc.h` and `drdc.h` (not `dhd.h`/`drd.h`). The `c` suffix is only in the header filenames — function names like `dhdGetPosition` do not have it.
- **Libraries** (`libdhd.a`, `libdrd.a`) are located at `$FD_SDK_DIR/lib/release/lin-<arch>-gcc/` (e.g., `lib/release/lin-x86_64-gcc/`), NOT at `$FD_SDK_DIR/lib/`. The CMakeLists.txt resolves this path automatically using `CMAKE_SYSTEM_PROCESSOR`.
- **`libusb-1.0`** is a transitive dependency of `libdhd.a`. Since static libraries don't carry transitive dependencies, it must be installed as a system package (handled by the `apt install` above) and is linked explicitly in `target_link_libraries`.

Add to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export FD_SDK_DIR=/opt/forcedimension/sdk-3.17.0
```

Reload:

```bash
source ~/.bashrc
```

### USB permissions for the delta.3

By default, USB devices require root access. Create a udev rule so your user account can access the delta.3 without sudo:

```bash
sudo tee /etc/udev/rules.d/99-forcedimension.rules << 'EOF'
# Force Dimension delta.3 haptic device
SUBSYSTEM=="usb", ATTR{idVendor}=="1451", MODE="0666", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Make sure your user is in the `plugdev` group:

```bash
sudo usermod -aG plugdev $USER
```

Log out and back in for the group change to take effect.

## Clone and install

```bash
git clone https://github.com/raeedcho/hapticore.git
cd hapticore
pixi install
```

That's it for the Python environment, build tools, and all dev dependencies. Pixi reads `pixi.toml` (for cmake, ninja, and dev tools) and `pyproject.toml` (for Python package dependencies) and installs everything into an isolated environment.

To verify:

```bash
pixi run test-unit
```

## Teensy 4.1 sync hub
 
The Teensy 4.1 serves as the centralized timing hub for the rig, generating camera frame triggers, cross-system sync pulses, behavioral event codes, and reward TTL signals. See ADR-013 for the reasoning behind this choice.
 
### Voltage levels
 
Teensy 4.1 outputs **3.3V logic** and is **not 5V tolerant** on any pin.
 
- **Blackfly S cameras:** Line 3 non-isolated input threshold is 2.6V. Direct 3.3V connection works.
- **Ripple Scout DIO:** SMA inputs accept LVTTL (high threshold 2.0V). Direct 3.3V works.
- **NI-DAQ digital inputs:** TTL-compatible (high threshold 2.0V). Direct 3.3V works.
- **Solenoid driver circuit:** Check input threshold. If the existing circuit expects 5V TTL, add a 74AHCT125 buffer (~$1, <10 ns propagation delay) between the Teensy output and the driver input.

### Firmware build
 
Firmware source: `hapticore/firmware/teensy/`
 
Option 1 — PlatformIO (preferred for CI):
```bash
cd firmware/teensy
pio run              # build
pio run -t upload    # flash
```
 
Option 2 — Arduino IDE:
1. Install Teensyduino addon
2. Select board: Teensy 4.1
3. Open `firmware/teensy/teensy_sync.ino`
4. Upload

### Wiring
 
```
Teensy 4.1 pin assignments (defined in firmware header):
 
Pin A  → Camera frame trigger (30-120 Hz)
         Wire to all 5 Blackfly S cameras' Line 3 (Pin 1, green wire)
         on 6-pin Hirose connector. Parallel wire — all cameras share
         the same physical signal. Share ground via Pin 6 (brown wire).
 
Pin B  → 1 Hz cross-system sync (50% duty cycle)
         Wire to BNC, T-split to:
           - Ripple Scout SMA digital input 1
           - SpikeGLX NI-DAQ digital input (for Neuropixels sync)
 
Pin C  → Event strobe
         Wire to BNC, T-split to:
           - Ripple Scout SMA digital input 2
           - SpikeGLX NI-DAQ digital input
 
Pin D  → Reward TTL
         Wire to solenoid driver circuit TTL input
 
GND    → Common ground shared with all connected devices
```
 
> Pin assignments are placeholders — update with actual pin numbers once firmware is finalized.

## Multi-monitor and mirror setup

Rig 2 uses two monitors: one that the subject views through a canted mirror, and one in the control room for the experimenter. The display process renders to one monitor at a time. Use the following fields in `configs/rig/rig2.yaml` (under `display:`) to configure which monitor is used and whether the image should be mirrored.

### Identifying monitor indices

Run `hapticore list-screens` (requires the `display` pixi environment) to list available monitors with their indices and resolutions:

```bash
pixi run -e display hapticore list-screens
```

Example output:
```
Index  Resolution      Position
0      1920x1080       (0, 0)
1      1920x1080       (1920, 0)
```

Set `screen: 1` in the rig config to render on the second monitor. On Linux/X11, the screen index follows `xrandr` output order and is stable across reboots unless the physical cable connection order changes.

### Horizontal mirror for a canted mirror

The canted mirror reflects the image left-right. Set `mirror_horizontal: true` to compensate — the rendered frame is flipped so stimuli appear at the correct positions from the subject's perspective.

Use `mirror_vertical: true` if the optical path also inverts the image vertically (uncommon for the standard single-mirror setup).

### Optical-path-length note

`monitor_distance_cm` should be set to the **total optical path length**: monitor-to-mirror distance plus mirror-to-eye distance, not the straight-line distance from monitor to subject. This matters for visual-angle calculations even though tasks currently render in `units="cm"`. Measuring the wrong distance will make stimuli appear smaller or larger than intended if tasks ever switch to `units="deg"`.

### Photodiode corner

`photodiode_corner` refers to the **physical location** of the sensor taped to the monitor (e.g., `"bottom_left"` if the sensor is at the bottom-left corner as seen from the front). When a mirror flag is set, the display process automatically remaps the render-frame corner to match the physical sensor location — no manual adjustment needed.

## Running the haptic server

By default, hapticore manages the C++ haptic server's lifecycle for you. When you run `hapticore run` (or hardware tests via the same factory), the Python factory probes the configured ZMQ state address:

- **If a server is already running on those addresses,** hapticore attaches to it as a client and leaves it running on exit.
- **If no server is detected,** hapticore spawns one from the binary path in `haptic.dhd.server_binary`, waits up to `startup_timeout_s` (default 20 s) for it to come up, attaches, and on exit cleanly terminates the server it spawned.

This means most workflows are one command: `hapticore run --rig configs/rig/rig2.yaml --subject ... --task ...`. The factory only kills what it spawned, so launching the server manually in a separate terminal is the supported way to keep it alive across multiple `hapticore run` invocations (see "Long-lived server" below).

The factory passes `force_limit_n` from the rig config through as `--force-limit` and `publish_rate_hz` as `--pub-rate`, so spawned-server parameters can never drift from the YAML. (For manually-launched servers, you're responsible for matching them yourself.)

### First-time setup

1. Power on the delta.3 and connect it via USB.
2. Build the server (once per checkout; rebuild after C++ changes):

   ```bash
   pixi run haptic-build           # build and test mock, and build dev-dhd and dhd release targets
   # use setcap for real-time scheduling
   # see below for automatic setcap
   sudo setcap cap_sys_nice=eip cpp/haptic_server/build/dev-dhd/haptic_server
   sudo setcap cap_sys_nice=eip cpp/haptic_server/build/dhd/haptic_server
   ```

3. Make sure your rig config (e.g. `configs/rig/rig2.yaml`) has `haptic.dhd.server_binary` pointing to the binary you built, or set the `HAPTICORE_HAPTIC_SERVER_BIN` environment variable (which takes precedence over the config). The env var is useful when the path is rig-specific and you don't want it baked into a shared config file.

4. Run a session. The first run after powering on the device will trigger auto-calibration:

   ```
   Spawning haptic_server: .../haptic_server --pub-address ...
   Opened device: delta.3
   Auto-calibrating — device will move, keep hands clear...
   Calibration complete
   Position sanity check: device at nonzero position
   Haptic server running.
     PUB: ipc:///tmp/hapticore_haptic_state
     CMD: ipc:///tmp/hapticore_haptic_cmd
     Rate: 200 Hz
     Force limit: 20 N
   Press Ctrl+C to stop.
   ```

   Subsequent runs within the same power cycle skip calibration.

If you see "No haptic server detected at ..." with `auto_start: false` set in your config, either remove the override or start the server manually (see below). If you see `"Configured haptic_server binary does not exist"`, build the server first.

### Long-lived server (across multiple `hapticore run` invocations)

If you want the server to outlive any individual `hapticore run` — for example, iterating on task code without re-paying calibration on each run, or keeping the server up across debugging sessions — launch the binary once manually (replace with `dev-dhd` build if you need the debugger):

```bash
cd cpp/haptic_server
./build/dhd/haptic_server
```

Subsequent `hapticore run` invocations will probe the address, find the running server, and attach. Because the factory only kills what it spawned, your manually-launched server stays alive across multiple `hapticore run` invocations.

### Real-time scheduling (SCHED_FIFO)

The haptic loop runs at 4 kHz and needs real-time scheduling priority to avoid jitter. This requires the `CAP_SYS_NICE` capability on the binary. Without it, the server still works but may have occasional timing glitches under load.

Grant the capability after each build:

```bash
sudo setcap cap_sys_nice=eip build/dev-dhd/haptic_server
sudo setcap cap_sys_nice=eip build/dhd/haptic_server
```

To avoid running this manually after every rebuild, set up passwordless sudo for just the `setcap` command. Create a sudoers rule (replace `yourusername` with your actual username):

```bash
sudo visudo -f /etc/sudoers.d/hapticore-setcap
```

Add this line:

```
yourusername ALL=(root) NOPASSWD: /usr/sbin/setcap cap_sys_nice=eip /home/yourusername/hapticore/cpp/haptic_server/build/*/haptic_server
```

With this in place, the CMake post-build hook (if configured in `CMakeLists.txt`) will set the capability automatically, or you can run the `sudo setcap` command without a password prompt.

You can verify the capability is set:

```bash
getcap build/dhd/haptic_server
# Expected: build/dhd/haptic_server cap_sys_nice=eip
```

### Cross-machine operation

To connect from another machine (e.g., your macOS laptop running the Python task controller), use TCP addresses:

```bash
./build/dhd/haptic_server \
    --pub-address tcp://*:5555 \
    --cmd-address tcp://*:5556
```

Then on the client machine, set environment variables before running tests or the task controller:

```bash
export HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555
export HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556
```

### Command-line options

| Flag | Default | Description |
|---|---|---|
| `--pub-address` | `ipc:///tmp/hapticore_haptic_state` | ZMQ PUB socket address |
| `--cmd-address` | `ipc:///tmp/hapticore_haptic_cmd` | ZMQ ROUTER socket address |
| `--pub-rate` | `200` | State publish rate in Hz |
| `--force-limit` | `20` | Maximum force in Newtons |
| `--cpu-core` | `1` | CPU core to pin the haptic thread to |
| `--no-calibrate` | off | Skip auto-calibration on startup |

### Running hardware tests

Hardware tests connect to a running haptic server and exercise the real device. They are tagged with `@pytest.mark.hardware` and excluded from CI.

1. Start the haptic server (see above).

2. Make sure the device handle is not at the exact workspace center — just leave it wherever it naturally rests.

3. Run the tests:

```bash
pixi run test-hardware
```

To use TCP addresses (e.g., server on a different machine):

```bash
HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555 \
HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556 \
pixi run test-hardware
```

#### What the tests verify

**Stage 2 — State stream:**
- State messages arrive and deserialize to `HapticState`
- Sequence numbers are monotonically increasing
- Device reports a non-origin position (handle not exactly at [0,0,0])
- Publish rate is approximately 200 Hz

**Stage 3 — Force fields and safety:**
- Command round-trip (heartbeat, unknown method error)
- Spring-damper field activates and state stream reflects the change
- Restoring force points back toward center (opposite sign of displacement)
- Force magnitude is clamped to 20 N even with high stiffness
- Heartbeat timeout causes forces to drop to near-zero
- Server recovers after heartbeat timeout and accepts new commands

The final test always reverts to NullField so the handle is free-moving when tests complete.

### Running interactive (feel) tests

Interactive tests keep force fields alive and prompt you to evaluate how
they feel. They support single-person remote operation — the robot can be
in a different room from the terminal.

1. Start the haptic server.
2. Run:

   ```bash
   pixi run test-interactive
   ```

3. For each test, the flow is:
   - Read the instructions on screen.
   - Press **Enter** to start the countdown.
   - Walk to the device and grab the handle during the countdown.
   - Evaluate the feel during the timed window (default 10 s).
   - Walk back to the terminal and answer `y` or `n`.

Adjust timing for your environment:

```bash
pixi run test-interactive --countdown=8 --duration=15
```

### Troubleshooting

**"Error: failed to open haptic device"**
- Is the delta.3 powered on?
- Is the USB cable connected?
- Run `lsusb | grep 1451` to check if the device is visible.
- Check the udev rule: `cat /etc/udev/rules.d/99-forcedimension.rules`
- Try `sudo ./build/dev-dhd/haptic_server` to rule out permissions.

**"Warning: could not set SCHED_FIFO"**
- Run `getcap build/dev-dhd/haptic_server` — should show `cap_sys_nice=eip`.
- If empty, run `sudo setcap cap_sys_nice=eip build/dev-dhd/haptic_server`.
- Capabilities are lost on rebuild — see the passwordless sudo setup above.

**Hardware tests can't connect / time out**
- Is the server running? Check with `ps aux | grep haptic_server`.
- Are addresses matching? The tests default to IPC. If the server uses TCP, set `HAPTICORE_PUB_ADDRESS` and `HAPTICORE_CMD_ADDRESS`.
- For IPC, both the server and tests must run on the same machine.

**Position reads as exactly [0, 0, 0]**
- The mock hardware build reports zero position. Make sure you built with the `dev-dhd` preset (not `dev-mock`).
- Physically move the handle slightly and re-run.

**Handle drops under gravity when server is running**
- This means `dhdEnableForce(DHD_ON)` was not called, or the SDK's gravity compensation pipeline is not initialized. This was a known bug fixed in Phase 2B. If you see this on an older build, rebuild from the latest code.
- If using a custom build that skips `enable_force`, pressing the physical FORCE button on the DHC will enable the amplifiers but *not* the SDK's host-side gravity compensation — the handle will still drop.

**"Error: calibration failed"**
- The DRD auto-calibration could not complete and the server exits.
- Try running the SDK's `autocenter` example manually to diagnose.
- Make sure the device arms can move freely — obstructions prevent calibration.
- If the device was calibrated externally this power cycle, use `--no-calibrate` to skip.
- Power-cycle the device and restart the server to retry auto-calibration.

## FTDI FT232H beam break sensor
 
The beam break sensor (Adafruit 3mm IR through-beam) connects to the C++ haptic server via an FTDI FT232H USB-to-GPIO adapter. This is the safety-critical read path — the C++ server reads beam break state directly at sub-0.5 ms latency, independent of the Python layer.
 
The FTDI FT232H is a separate device from the Teensy. The Teensy handles timing/sync; the FTDI handles safety-critical GPIO into the C++ process.
 
### Why separate from Teensy
 
The beam break must be read by the C++ haptic server process, which runs a 4 kHz SCHED_FIFO loop. The Teensy communicates via USB serial to the Python `SyncProcess` — routing beam break data through Python would add unacceptable latency. The FTDI FT232H provides direct GPIO access from C++ via libftdi or libusb, staying in the real-time path.
 
## Camera PC
 
A dedicated computer runs camera acquisition, separate from the haptic control rig. See ADR-012 for rationale.
 
### Recommended specs
 
| Component | Recommendation | Rationale |
|---|---|---|
| CPU | Intel i7/i9 or AMD Ryzen 7/9 | PySpin acquisition threads + FFMPEG encoding |
| RAM | 32 GB+ | Frame buffering for 5 cameras |
| GPU | NVIDIA RTX 3060+ | NVENC hardware video encoding |
| USB3 | 2× PCIe quad-channel USB3 controller cards | Independent bandwidth per camera; split cameras 3+2 across controllers |
| Storage | 2–4 TB NVMe SSD | Raw recording buffer; ~35 GB/hour compressed at 5 cameras × 1280×1024 × 30 fps |
| Network | Gigabit Ethernet | Post-session transfer to NAS |
 
### USB3 controller cards
 
Verified chipsets for Blackfly S cameras: Fresco Logic FL1100, Renesas µPD720202. Teledyne sells the ACC-01-1203 quad-channel card. Each camera port must have independent bandwidth — hubs that share a single upstream link will cause frame drops.
 
Rule of thumb: each Blackfly S at 1280×1024 @ 30 fps Mono8 uses ~39 MB/s. Five cameras total ~197 MB/s, within a single controller's ~450 MB/s effective bandwidth, but two controllers provide headroom for higher frame rates and prevent contention.
 
### Software
 
Camera acquisition software lives in a separate repository. Install Spinnaker SDK (includes PySpin) from Teledyne's download portal. The acquisition pipeline uses PySpin for camera control and FFMPEG with NVENC for real-time H.264 compression.
 
### Camera strobe feedback wiring
 
Only one camera strobe is wired back to the neural recording system. The five cameras are hardware-triggered in lockstep by the Teensy (see `architecture.md` § Camera subsystem), so every camera exposes on the same physical edge — additional strobes would carry no extra alignment information. Per-camera drop detection is handled by Spinnaker's hardware frame counter on the camera PC, not by neural-side pulse counting.

```
Camera 1 Line 1 (or Line 2) strobe output → BNC cable → Ripple Scout SMA input 2
```

Configure camera 1's strobe output in Spinnaker (`LineSelector = Line 1` or `Line 2`, `LineMode = Output`, `LineSource = ExposureActive`). Leave the other four cameras' strobes unwired on the neural side — they still need to be configured if downstream analysis wants per-camera exposure timing in the camera PC's own log.

The Scout's remaining SMA inputs under this plan:

| SMA input | Signal |
|---|---|
| 1 | Teensy 1 Hz cross-system sync |
| 2 | Camera 1 exposure strobe |
| 3 | Unused (reserved for future analog/event signals) |
| 4 | Unused (reserved) |

Event codes are carried on the 25-pin D-sub parallel input port, not on an SMA, per ADR-014.
