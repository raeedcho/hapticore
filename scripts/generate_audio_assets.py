"""Generate placeholder audio cue files for Hapticore experiments."""

import numpy as np
import soundfile as sf
from pathlib import Path

SAMPLE_RATE = 44100
ASSETS_DIR = Path("assets/audio")


def generate_tone(freq_hz: float, duration_s: float, amplitude: float = 0.5) -> np.ndarray:
    """Generate a sine tone with a short fade-in/out to avoid clicks."""
    t = np.linspace(0, duration_s, int(SAMPLE_RATE * duration_s), endpoint=False)
    tone = amplitude * np.sin(2 * np.pi * freq_hz * t).astype(np.float32)
    # 5 ms fade in/out
    fade_samples = int(0.005 * SAMPLE_RATE)
    tone[:fade_samples] *= np.linspace(0, 1, fade_samples).astype(np.float32)
    tone[-fade_samples:] *= np.linspace(1, 0, fade_samples).astype(np.float32)
    return tone


def generate_click(duration_s: float = 0.03, amplitude: float = 0.4) -> np.ndarray:
    """Generate a short noise burst with exponential decay."""
    n_samples = int(SAMPLE_RATE * duration_s)
    noise = amplitude * np.random.default_rng(42).standard_normal(n_samples).astype(np.float32)
    decay = np.exp(-np.linspace(0, 8, n_samples)).astype(np.float32)
    return noise * decay


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    go_cue = generate_tone(freq_hz=1000.0, duration_s=0.15)
    sf.write(ASSETS_DIR / "go_cue.wav", go_cue, SAMPLE_RATE)
    print(f"Wrote {ASSETS_DIR / 'go_cue.wav'} ({len(go_cue)} samples)")

    click = generate_click()
    sf.write(ASSETS_DIR / "click.wav", click, SAMPLE_RATE)
    print(f"Wrote {ASSETS_DIR / 'click.wav'} ({len(click)} samples)")


if __name__ == "__main__":
    main()
