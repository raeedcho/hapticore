#!/usr/bin/env bash
set -euo pipefail
BUILD_DIR="${1:-build}"
MOCK="${MOCK_HARDWARE:-ON}"
cmake -S cpp/haptic_server -B "$BUILD_DIR" \
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-RelWithDebInfo}" \
      -DMOCK_HARDWARE="$MOCK"
cmake --build "$BUILD_DIR" --parallel "$(nproc 2>/dev/null || sysctl -n hw.ncpu)"
echo "Build complete. Run tests: cd $BUILD_DIR && ctest --output-on-failure"
