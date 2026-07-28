"""Dashboard subpackage — processes that create GUI windows in child processes.

All dashboard processes use the ``"spawn"`` multiprocessing start method
to avoid inheriting Qt/PsychoPy state from the parent (which may already
have a QApplication when launched via ``hapticore gui``).
"""

import multiprocessing
import multiprocessing.synchronize

_spawn_ctx = multiprocessing.get_context("spawn")

# Typed aliases so subclasses don't need per-line type: ignore comments.
SpawnProcess: type[multiprocessing.Process] = _spawn_ctx.Process  # type: ignore[assignment]
