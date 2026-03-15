#!/usr/bin/env bash
# Run from the repository root: ./cpp/haptic_server/build.sh [build_dir]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${1:-build}"
MOCK="${MOCK_HARDWARE:-ON}"
cmake -S "$SCRIPT_DIR" -B "$BUILD_DIR" \
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-RelWithDebInfo}" \
      -DMOCK_HARDWARE="$MOCK"
cmake --build "$BUILD_DIR" --parallel "$(nproc 2>/dev/null || sysctl -n hw.ncpu)"
echo "Build complete. Run tests: cd $BUILD_DIR && ctest --output-on-failure"
