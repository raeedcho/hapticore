"""AudioPlayer: sounddevice-based AudioInterface implementation."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Audio cue player using sounddevice (PortAudio).

    Pre-loads all cue files into memory at construction. ``play_cue()``
    submits the buffer to PortAudio and returns immediately — actual
    audio output happens on PortAudio's callback thread.

    If a cue is already playing when another ``play_cue()`` fires,
    ``sd.play()`` interrupts the previous sound. This is the desired
    behavior for short training cues.
    """

    def __init__(
        self,
        cues: dict[str, Path],
        device: str | int | None = None,
    ) -> None:
        self._device = device
        self._buffers: dict[str, tuple[np.ndarray, int]] = {}

        for name, path in cues.items():
            if not path.exists():
                raise FileNotFoundError(
                    f"Audio cue {name!r}: file not found: {path}"
                )
            data, samplerate = sf.read(path, dtype="float32")
            self._buffers[name] = (data, samplerate)
            logger.info(
                "Loaded audio cue %r: %s (%.1f s, %d Hz)",
                name, path, len(data) / samplerate, samplerate,
            )

    def play_cue(self, name: str) -> None:
        """Play a named cue. Non-blocking; returns immediately.

        Unknown names log a warning and return. PortAudio errors
        (e.g. no audio device) are caught and logged — ``play_cue``
        never raises during a trial.
        """
        buf = self._buffers.get(name)
        if buf is None:
            logger.warning("Unknown audio cue %r — skipping", name)
            return
        data, samplerate = buf
        try:
            sd.play(data, samplerate, device=self._device, latency="low")
        except sd.PortAudioError:
            logger.warning(
                "PortAudio error playing cue %r — audio device may be unavailable",
                name,
                exc_info=True,
            )
