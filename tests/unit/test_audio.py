"""Tests for MockAudio and make_audio_interface factory."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from hapticore.audio import MockAudio, make_audio_interface
from hapticore.core.config import AudioConfig
from hapticore.core.interfaces import AudioInterface


def test_mock_audio_play_cue_records_names() -> None:
    """play_cue() calls are recorded in _play_log."""
    audio = MockAudio()
    audio.play_cue("click")
    audio.play_cue("click")
    assert audio._play_log == ["click", "click"]


def test_mock_audio_unknown_cue_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown cue names log a warning but still append to _play_log."""
    audio = MockAudio(known_cues={"click"})
    with caplog.at_level(logging.WARNING, logger="hapticore.audio.mock"):
        audio.play_cue("beep")
    assert "beep" in audio._play_log
    assert any("beep" in record.message for record in caplog.records)


def test_mock_audio_known_cue_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Known cue names do not produce a warning."""
    audio = MockAudio(known_cues={"click"})
    with caplog.at_level(logging.WARNING, logger="hapticore.audio.mock"):
        audio.play_cue("click")
    assert audio._play_log == ["click"]
    assert not caplog.records


def test_mock_audio_no_known_cues_accepts_all(caplog: pytest.LogCaptureFixture) -> None:
    """With no known_cues, all names are accepted without warning."""
    audio = MockAudio()
    assert audio._known_cues is None
    with caplog.at_level(logging.WARNING, logger="hapticore.audio.mock"):
        audio.play_cue("anything")
    assert audio._play_log == ["anything"]
    assert not caplog.records


def test_factory_mock_backend() -> None:
    """make_audio_interface with backend='mock' yields a MockAudio."""
    cfg = AudioConfig(backend="mock")
    with make_audio_interface(cfg) as audio:
        assert isinstance(audio, MockAudio)


def test_factory_mock_backend_satisfies_protocol() -> None:
    """The yielded MockAudio satisfies the AudioInterface protocol."""
    cfg = AudioConfig(backend="mock")
    with make_audio_interface(cfg) as audio:
        assert isinstance(audio, AudioInterface)


def test_factory_mock_with_cues() -> None:
    """make_audio_interface passes cue names to MockAudio as known_cues."""
    cfg = AudioConfig(backend="mock", cues={"click": "click.wav", "go": "go.wav"})
    with make_audio_interface(cfg) as audio:
        assert isinstance(audio, MockAudio)
        # Known cues are set from config
        assert audio._known_cues == {"click", "go"}


def test_factory_sounddevice_backend(tmp_path: Path) -> None:
    """make_audio_interface with backend='sounddevice' yields an AudioPlayer."""
    import soundfile as sf
    wav_path = tmp_path / "test.wav"
    sf.write(wav_path, np.zeros(100, dtype=np.float32), 44100)

    cfg = AudioConfig(backend="sounddevice", cues={"test": str(wav_path)})
    with make_audio_interface(cfg) as audio:
        from hapticore.audio.player import AudioPlayer
        assert isinstance(audio, AudioPlayer)


def test_audio_player_loads_cues(tmp_path: Path) -> None:
    """AudioPlayer pre-loads cue files at construction."""
    import soundfile as sf
    from hapticore.audio.player import AudioPlayer

    wav = tmp_path / "beep.wav"
    sf.write(wav, np.zeros(441, dtype=np.float32), 44100)

    player = AudioPlayer(cues={"beep": wav})
    assert "beep" in player._buffers
    data, sr = player._buffers["beep"]
    assert sr == 44100
    assert len(data) == 441


def test_audio_player_missing_file(tmp_path: Path) -> None:
    """AudioPlayer raises FileNotFoundError for missing cue files."""
    from hapticore.audio.player import AudioPlayer

    with pytest.raises(FileNotFoundError, match="no_such_file"):
        AudioPlayer(cues={"bad": tmp_path / "no_such_file.wav"})


def test_audio_player_unknown_cue_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """AudioPlayer.play_cue() warns on unknown cue names."""
    import soundfile as sf
    from hapticore.audio.player import AudioPlayer

    wav = tmp_path / "beep.wav"
    sf.write(wav, np.zeros(100, dtype=np.float32), 44100)
    player = AudioPlayer(cues={"beep": wav})

    with caplog.at_level(logging.WARNING, logger="hapticore.audio.player"):
        player.play_cue("nonexistent")
    assert any("nonexistent" in r.message for r in caplog.records)


def test_audio_player_play_cue_calls_sd_play(tmp_path: Path) -> None:
    """AudioPlayer.play_cue() calls sd.play() with correct arguments."""
    import soundfile as sf
    from unittest.mock import patch
    from hapticore.audio.player import AudioPlayer

    wav = tmp_path / "tone.wav"
    tone = np.ones(441, dtype=np.float32) * 0.5
    sf.write(wav, tone, 44100)
    player = AudioPlayer(cues={"tone": wav}, device=None)

    with patch("hapticore.audio.player.sd.play") as mock_play:
        player.play_cue("tone")
        mock_play.assert_called_once()
        call_args = mock_play.call_args
        np.testing.assert_array_equal(call_args[0][0], tone)
        assert call_args[0][1] == 44100
        assert call_args[1]["device"] is None
        assert call_args[1]["latency"] == "low"


def test_audio_player_portaudio_error_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """PortAudioError during playback is caught and logged, not raised."""
    import sounddevice as sd_module
    import soundfile as sf
    from unittest.mock import patch
    from hapticore.audio.player import AudioPlayer

    wav = tmp_path / "tone.wav"
    sf.write(wav, np.zeros(100, dtype=np.float32), 44100)
    player = AudioPlayer(cues={"tone": wav})

    with patch("hapticore.audio.player.sd.play", side_effect=sd_module.PortAudioError(-9999)):
        with caplog.at_level(logging.WARNING, logger="hapticore.audio.player"):
            player.play_cue("tone")  # must NOT raise
    assert any("PortAudio" in r.message for r in caplog.records)


def test_factory_unknown_backend() -> None:
    """make_audio_interface with an unknown backend raises ValueError."""
    # Bypass Pydantic validation by constructing config normally then patching
    cfg = AudioConfig(backend="mock")
    cfg.backend = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown audio backend"):
        with make_audio_interface(cfg):
            pass


def test_audio_config_rejects_invalid_backend() -> None:
    """AudioConfig raises a Pydantic ValidationError for an unknown backend."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AudioConfig(backend="bogus")  # type: ignore[arg-type]
