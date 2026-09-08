# Building the Haptic Server

## Prerequisites

| Tool | Source |
|---|---|
| C++17 compiler | System (Apple Clang on macOS, `g++` on Ubuntu) |
| CMake 3.21+ | pixi (via conda-forge) |
| Ninja | pixi (via conda-forge) |
| libzmq | `brew install zeromq` (macOS) / `sudo apt install libzmq3-dev` (Ubuntu) |
| libusb | `sudo apt install libusb-1.0-0-dev` (Ubuntu only; macOS real-hardware builds use Apple's native IOKit USB stack instead) |

## Quick start (mock hardware — no robot needed)

```bash
pixi shell
cd cpp/haptic_server
cmake --preset dev-mock
cmake --build --preset dev-mock
ctest --preset dev-mock
```

Or via pixi tasks:

```bash
pixi run mock-cpp
```

This builds with mock DHD stubs and runs all unit tests.

## Building with real hardware (rig machine, or macOS desktop with a Falcon)

Requires the Force Dimension SDK. Set the `FD_SDK_DIR` environment variable:

```bash
export FD_SDK_DIR=/opt/forcedimension/sdk-3.17.0
```

Then build inside the pixi environment:

```bash
pixi shell
cd cpp/haptic_server
cmake --preset dev-dhd
cmake --build --preset dev-dhd
```

Or via pixi tasks:

```bash
pixi run dhd-cpp-debug-build
```

For a production build:

```bash
cmake --preset dhd
cmake --build --preset dhd
```

Or via pixi tasks:

```bash
pixi run dhd-cpp-build
```

To build all targets via pixi:

```bash
pixi run haptic-build
```

## Platform notes

### macOS (development)
- Apple Clang is the native compiler.
- `librt` is not needed on macOS (it's part of the system library).
- Install VSCode extensions: **CMake Tools** (`ms-vscode.cmake-tools`) and **C/C++** (`ms-vscode.cpptools`).

### Ubuntu Linux (production rig)
- Use GCC (default on Ubuntu).
- The real hardware build links against `libdhd`, `libdrd`, `librt`, and `libpthread`.
- The Force Dimension SDK must be installed separately.
- `libdhd.a` and `libdrd.a` are located at `$FD_SDK_DIR/lib/release/lin-<arch>-gcc/` (e.g., `lib/release/lin-x86_64-gcc/`), not `$FD_SDK_DIR/lib/`. The CMakeLists.txt resolves this automatically using `CMAKE_SYSTEM_PROCESSOR`.
- `libdhd.a` statically depends on `libusb-1.0`. Since static libraries don't carry transitive dependencies, `libusb-1.0-0-dev` must be installed as a system package and is linked explicitly in `target_link_libraries`.

### macOS with a Novint Falcon (desktop development, verified on macOS 15 / arm64, SDK 3.17.7)

The Falcon uses the same Force Dimension SDK as the delta.3 and can drive the real (non-mock) haptic server, giving actual force feedback during desktop development.

- `libdhd.a` and `libdrd.a` are located at `$FD_SDK_DIR/lib/release/mac-<arch>-clang/` (e.g., `lib/release/mac-arm64-clang/` on Apple Silicon), analogous to the Linux `lin-<arch>-gcc` layout. The CMakeLists.txt resolves this automatically using `CMAKE_SYSTEM_PROCESSOR` and `APPLE`.
- **libusb is not required on macOS.** `libdhd.a`'s USB transport uses Apple's native IOKit USB stack on this platform (no separate driver install needed), not libusb-1.0. The CMakeLists.txt links `IOKit` and `CoreFoundation` frameworks instead.
- The SDK's prebuilt binaries (demos, `libdhd.a`, `libdrd.a`) are quarantined by macOS Gatekeeper on first download. If you see "cannot be opened because the developer cannot be verified" or similar, clear the quarantine attribute recursively:
  ```bash
  sudo xattr -r -d com.apple.quarantine $FD_SDK_DIR
  ```
- The Falcon's SDK does not support DRD auto-calibration (`drdAutoInit()` fails with `DHD_ERROR_NOT_AVAILABLE`) or gravity compensation. Run the server with `--no-calibrate --no-gravity-comp`, or use the `configs/rig/desktop-falcon.yaml` rig config, which sets `auto_calibrate: false` and `gravity_compensation: false` so the Python factory passes those flags automatically:
  ```bash
  export FD_SDK_DIR=/opt/forcedimension/sdk-3.17.7
  pixi run dhd-cpp-build
  hapticore run --rig configs/rig/desktop-falcon.yaml \
                --subject ... \
                --experiment configs/experiments/center_out.yaml \
                --extra-config configs/overrides/falcon-scale.yaml
  ```
