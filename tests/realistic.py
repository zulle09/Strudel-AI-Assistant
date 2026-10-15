"""A hum that behaves like a person rather than an oscillator.

The clean generator in test_melody.py is a vibrato sine with an envelope: it
proves the pipeline works, not that it survives a real microphone. This one
adds what an actual recording has and that one lacks.

- **Portamento.** A voice slides between notes instead of stepping, so the f0
  track is continuous and smoothed across boundaries rather than piecewise
  constant.
- **Breath noise**, an order of magnitude above the clean generator's dither.
  This is what defeated spectral-flux onset detection.
- **Timing jitter.** Nobody hums on a grid.
- **Uneven loudness** between notes.
- **Room rumble**, low-passed noise under everything.
- **Pitch drift.** Singers go flat over a phrase.

Used by the realistic half of the test suite. Keeping it beside the tests
rather than in app/ is deliberate: it is a measuring instrument, not part of
the product.
"""

from __future__ import annotations

import io

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, lfilter

SR = 22_050


def realistic_hum(
    notes: list[str],
    bpm: float = 100,
    beat: float = 1.0,
    seed: int = 0,
    glide: float = 0.06,
    noise: float = 0.02,
    jitter: float = 0.03,
    drift_cents: float = -25,
    rumble: float = 0.01,
) -> bytes:
    rng = np.random.default_rng(seed)
    step = 60.0 / bpm * beat

    events, cursor = [], 0.0
    for name in notes:
        duration = step * (1 + rng.normal(0, jitter))
        events.append((name, cursor, duration))
        cursor += duration

    total = int(np.ceil(cursor * SR)) + SR // 2
    f0 = np.zeros(total)
    amplitude = np.zeros(total)

    for name, start, duration in events:
        a, b = int(start * SR), int((start + duration) * SR)
        if name == "~" or b <= a:
            continue
        f0[a:b] = float(librosa.note_to_hz(name))
        loudness = float(np.clip(0.5 * (1 + rng.normal(0, 0.18)), 0.15, 0.9))
        envelope = np.ones(b - a)
        attack, release = int(0.03 * SR), int(0.08 * SR)
        envelope[:attack] = np.linspace(0, 1, attack)
        envelope[-release:] = np.linspace(1, 0, release)
        amplitude[a:b] = loudness * envelope

    voiced = f0 > 0
    if not voiced.any():
        raise ValueError("every note was a rest")

    # One continuous f0 for the whole take, so note changes slide rather than
    # jump. Rests are filled by interpolation and then silenced by the
    # amplitude envelope, which is what a voice does between phrases anyway.
    filled = np.interp(np.arange(total), np.flatnonzero(voiced), f0[voiced])
    window = np.hanning(max(3, int(glide * SR)))
    f0_smooth = np.convolve(filled, window / window.sum(), mode="same")

    # Singers go flat. Cents are logarithmic, hence the power of two.
    f0_smooth *= 2 ** (np.linspace(0, drift_cents, total) / 1200.0)

    vibrato = 1 + 0.008 * np.sin(2 * np.pi * 5.0 * np.arange(total) / SR)
    phase = 2 * np.pi * np.cumsum(f0_smooth * vibrato) / SR
    tone = np.sin(phase) + 0.4 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)

    signal = tone * amplitude
    signal += noise * rng.standard_normal(total)
    b_coef, a_coef = butter(2, 80 / (SR / 2), "low")
    signal += rumble * lfilter(b_coef, a_coef, rng.standard_normal(total))
    signal = signal / max(float(np.abs(signal).max()), 1e-9) * 0.7

    buffer = io.BytesIO()
    sf.write(buffer, signal.astype(np.float32), SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()
