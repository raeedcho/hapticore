"""Cross-language serialization test: C++ HapticStateData → Python HapticState.

Runs the C++ `write_test_state` binary to produce a packed msgpack file,
then reads it in Python, deserializes, and verifies all fields match.
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path

import msgpack
import pytest

from hapticore.core.messages import HapticState

# Locate the C++ test binary relative to the repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CPP_BUILD_DIRS = [
    _REPO_ROOT / "cpp" / "haptic_server" / "build" / "dev-mock" / "tests",
    _REPO_ROOT / "cpp" / "haptic_server" / "build" / "ci" / "tests",
]


def _find_write_test_state() -> Path | None:
    """Locate the write_test_state binary in known build directories."""
    binary = "write_test_state"
    if platform.system() == "Windows":
        binary += ".exe"
    for build_dir in _CPP_BUILD_DIRS:
        candidate = build_dir / binary
        if candidate.exists():
            return candidate
    return None


@pytest.fixture
def packed_state_file() -> Generator[Path | None, None, None]:
    """Run the C++ write_test_state binary and yield the output file path.

    Skips if the binary is not found (not built yet).
    Fails if the binary is found but exits non-zero.
    Always cleans up the temp file.
    """
    binary = _find_write_test_state()
    if binary is None:
        yield None
        return

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            [str(binary), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.fail(
                f"write_test_state exited with code {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        yield tmp_path
    except subprocess.TimeoutExpired:
        pytest.fail("write_test_state timed out after 10 seconds")
    finally:
        tmp_path.unlink(missing_ok=True)


def test_cpp_packed_state_deserializes_to_python_haptic_state(
    packed_state_file: Path | None,
) -> None:
    """Verify that bytes packed by C++ HapticStateData::pack() can be
    deserialized in Python and used to construct a HapticState dataclass."""
    if packed_state_file is None:
        pytest.skip(
            "C++ write_test_state binary not found — "
            "build with cmake --preset dev-mock first"
        )

    data = packed_state_file.read_bytes()
    unpacked = msgpack.unpackb(data, raw=False)

    # Construct HapticState from the unpacked dict
    state = HapticState(**unpacked)

    # Verify all fields match the known values written by write_test_state.cpp
    assert state.timestamp == pytest.approx(1234.567)
    assert state.sequence == 42
    assert state.position == pytest.approx([0.1, 0.2, 0.3])
    assert state.velocity == pytest.approx([-0.01, 0.0, 0.05])
    assert state.force == pytest.approx([1.0, -2.0, 0.5])
    assert state.active_field == "spring_damper"
    assert isinstance(state.field_state, dict)
    assert state.field_state["stiffness"] == pytest.approx(200.0)
    assert state.field_state["damping"] == pytest.approx(5.0)
