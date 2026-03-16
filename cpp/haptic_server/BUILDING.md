# Building the Haptic Server

## Prerequisites

| Tool | macOS | Ubuntu 24.04 |
|---|---|---|
| C++17 compiler | Apple Clang (Xcode CLI tools) | `sudo apt install g++` |
| CMake 3.21+ | `brew install cmake` | `sudo apt install cmake` |
| Ninja | `brew install ninja` | `sudo apt install ninja-build` |

## Quick start (mock hardware — no robot needed)

```bash
cd cpp/haptic_server
cmake --preset dev-mock
cmake --build --preset dev-mock
ctest --preset dev-mock
```

This builds with mock DHD stubs and runs all unit tests.

## Building with real hardware (rig machine only)

Requires the Force Dimension SDK. Set the `FD_SDK_DIR` environment variable:

```bash
export FD_SDK_DIR=/opt/forcedimension/sdk-3.17.0
```

Then:

```bash
cmake --preset dev-real
cmake --build --preset dev-real
```

For a production build:

```bash
cmake --preset release
cmake --build --preset release
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
