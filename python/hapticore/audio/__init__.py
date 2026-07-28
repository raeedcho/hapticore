"""Audio cue playback interface.

Contains ``MockAudio`` and the ``make_audio_interface`` factory (ADR-015).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hapticore.audio.mock import MockAudio
from hapticore.core.config import AudioConfig
from hapticore.core.interfaces import AudioInterface

__all__ = ["MockAudio", "make_audio_interface"]


@contextmanager
def make_audio_interface(cfg: AudioConfig) -> Iterator[AudioInterface]:
    """Construct an AudioInterface from a resolved AudioConfig.

    For ``backend="mock"``, yields a ``MockAudio`` with no system
    dependencies.  For ``backend="sounddevice"``, yields an
    ``AudioPlayer`` that pre-loads all cue files at construction
    and plays them non-blocking via PortAudio.

    Args:
        cfg: Resolved ``AudioConfig``.

    Raises:
        ValueError: If ``cfg.backend`` is not a supported value.
        FileNotFoundError: If any cue file path does not exist
            (sounddevice backend only).
    """
    if cfg.backend == "mock":
        yield MockAudio(known_cues=set(cfg.cues.keys()) if cfg.cues else None)
        return

    if cfg.backend == "sounddevice":
        from hapticore.audio.player import AudioPlayer

        cue_paths = {name: Path(p) for name, p in cfg.cues.items()}
        yield AudioPlayer(cue_paths, device=cfg.device)
        return

    raise ValueError(f"Unknown audio backend: {cfg.backend!r}")
