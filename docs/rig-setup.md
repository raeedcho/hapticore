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
    libusb-1.0-0-dev \
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

## Building the haptic server

Enter the pixi environment, then build:

```bash
pixi shell
cd cpp/haptic_server
cmake --preset dev-real
cmake --build --preset dev-real
```

Verify the build succeeded:

```bash
./build/dev-real/haptic_server --help
```

### Real-time scheduling (SCHED_FIFO)

The haptic loop runs at 4 kHz and needs real-time scheduling priority to avoid jitter. This requires the `CAP_SYS_NICE` capability on the binary. Without it, the server still works but may have occasional timing glitches under load.

Grant the capability after each build:

```bash
sudo setcap cap_sys_nice=eip build/dev-real/haptic_server
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
getcap build/dev-real/haptic_server
# Expected: build/dev-real/haptic_server cap_sys_nice=eip
```

## Running the haptic server

### First run

1. Power on the delta.3 and connect it via USB.

2. Start the server:

```bash
cd cpp/haptic_server
./build/dev-real/haptic_server
```

You should see:

```
Opened device: delta.3
Device already calibrated
Position sanity check: device at nonzero position
Haptic server running.
  PUB: ipc:///tmp/hapticore_haptic_state
  CMD: ipc:///tmp/hapticore_haptic_cmd
  Rate: 200 Hz
  Force limit: 20 N
Press Ctrl+C to stop.
```

For a fresh power-on (uncalibrated device), you will instead see:

```
Opened device: delta.3
Auto-calibrating — device will move, keep hands clear...
Calibration complete
Position sanity check: device at nonzero position
Haptic server running.
...
```

If you see "Error: failed to open haptic device", check that the delta.3 is powered on, the USB cable is connected, and the udev rule is in place.

If you see "Warning: could not set SCHED_FIFO", the `CAP_SYS_NICE` capability is not set. The server will still work but with potentially higher timing jitter.

### Cross-machine operation

To connect from another machine (e.g., your macOS laptop running the Python task controller), use TCP addresses:

```bash
./build/dev-real/haptic_server \
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

## Running hardware tests

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

### What the tests verify

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

## Troubleshooting

**"Error: failed to open haptic device"**
- Is the delta.3 powered on?
- Is the USB cable connected?
- Run `lsusb | grep 1451` to check if the device is visible.
- Check the udev rule: `cat /etc/udev/rules.d/99-forcedimension.rules`
- Try `sudo ./build/dev-real/haptic_server` to rule out permissions.

**"Warning: could not set SCHED_FIFO"**
- Run `getcap build/dev-real/haptic_server` — should show `cap_sys_nice=eip`.
- If empty, run `sudo setcap cap_sys_nice=eip build/dev-real/haptic_server`.
- Capabilities are lost on rebuild — see the passwordless sudo setup above.

**Hardware tests can't connect / time out**
- Is the server running? Check with `ps aux | grep haptic_server`.
- Are addresses matching? The tests default to IPC. If the server uses TCP, set `HAPTICORE_PUB_ADDRESS` and `HAPTICORE_CMD_ADDRESS`.
- For IPC, both the server and tests must run on the same machine.

**Position reads as exactly [0, 0, 0]**
- The mock hardware build reports zero position. Make sure you built with the `dev-real` preset (not `dev-mock`).
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
