# Building the Haptic Server

## Prerequisites

| Tool | Source |
|---|---|
| C++17 compiler | System (Apple Clang on macOS, `g++` on Ubuntu) |
| CMake 3.21+ | pixi (via conda-forge) |
| Ninja | pixi (via conda-forge) |
| libzmq | `brew install zeromq` (macOS) / `sudo apt install libzmq3-dev` (Ubuntu) |
| libusb | `brew install libusb` (macOS) / `sudo apt install libusb-1.0-0-dev` (Ubuntu) |

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

## Building with real hardware (rig machine only)

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
