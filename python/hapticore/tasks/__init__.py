"""Behavioral task framework: BaseTask and ParamSpec.

Other components (TimerManager, TrialManager, TaskController) are imported
directly from their submodules when needed.
"""

from __future__ import annotations

from hapticore.tasks.base import BaseTask, ParamSpec

__all__ = ["BaseTask", "ParamSpec"]
