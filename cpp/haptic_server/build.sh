#!/usr/bin/env bash
# Run from the repository root: ./cpp/haptic_server/build.sh [preset]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRESET="${1:-dev-mock}"
(
  cd "$SCRIPT_DIR"
  cmake --preset "$PRESET"
  cmake --build --preset "$PRESET" --parallel "$(nproc 2>/dev/null || sysctl -n hw.ncpu)"
)
echo "Build complete. Run tests: cd $SCRIPT_DIR && ctest --preset $PRESET"
