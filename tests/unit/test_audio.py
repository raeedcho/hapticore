"""Tests for MockAudio and make_audio_interface factory."""

from __future__ import annotations

import logging

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


def test_factory_sounddevice_not_implemented() -> None:
    """make_audio_interface with backend='sounddevice' raises NotImplementedError."""
    cfg = AudioConfig(backend="sounddevice")
    with pytest.raises(NotImplementedError):
        with make_audio_interface(cfg):
            pass


def test_factory_unknown_backend() -> None:
    """make_audio_interface with an unknown backend raises ValueError."""
    # Bypass Pydantic validation by constructing config normally then patching
    cfg = AudioConfig(backend="mock")
    cfg.backend = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown audio backend"):
        with make_audio_interface(cfg):
            pass
