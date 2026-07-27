"""Dashboard subpackage — processes that create GUI windows in child processes.

All dashboard processes use the ``"spawn"`` multiprocessing start method
to avoid inheriting Qt/PsychoPy state from the parent (which may already
have a QApplication when launched via ``hapticore gui``).
"""

import multiprocessing

_spawn_ctx = multiprocessing.get_context("spawn")
