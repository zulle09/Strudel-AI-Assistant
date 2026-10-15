"""Transcription accuracy against synthesised hums at known tempos.

There is no ground truth for a real recording of a person humming, so the
reference signals are built here: a vibrato sine with two harmonics, an
attack/release envelope and a little noise, which is close enough to a hum
that pYIN and the onset detector behave the way they do on a real take.

These are regression tests, not a proof. They exist so that tuning a constant
in melody.py - ONSET_DELTA, MIN_NOTE_SEC, the tempo window - shows up as a
number rather than as a vague feeling that it sounds worse. The tempo and
note-sequence thresholds are set at the level measured when the pipeline was
written; if a change moves them, that is the change's cost, made visible.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import librosa  # noqa: E402
from backend.melody import TranscriptionError, to_pattern, transcribe  # noqa: E402

SR = 22_050


def hum(notes: list[str], bpm: float = 100, beat: float = 1.0, seed: int = 0) -> bytes:
    """Synthesise a hum of `notes`, each lasting `beat` beats at `bpm`."""
    rng = np.random.default_rng(seed)
    step = 60.0 / bpm * beat
    pieces = []
    for name in notes:
        length = int(step * SR)
        t = np.arange(length) / SR
        if name == "~":
            pieces.append(np.zeros(length))
            continue

        freq = float(librosa.note_to_hz(name))
        # Vibrato plus a per-note offset: a perfectly flat tone is easier to
        # track than anything a person produces, and would flatter the result.
        vibrato = 1 + 0.006 * np.sin(2 * np.pi * 5.2 * t) + 0.002 * rng.standard_normal()
        phase = 2 * np.pi * np.cumsum(freq * vibrato) / SR
        signal = np.sin(phase) + 0.35 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)

        # The release is what separates consecutive notes of the same pitch.
        envelope = np.ones(length)
        attack, release = int(0.02 * SR), int(0.10 * SR)
        envelope[:attack] = np.linspace(0, 1, attack)
        envelope[-release:] = np.linspace(1, 0, release)

        pieces.append(signal * envelope * 0.5 + 0.002 * rng.standard_normal(length))

    audio = np.concatenate(pieces).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, audio, SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


# name, notes, bpm, beats per note
CASES = [
    ("twinkle", ["C4", "C4", "G4", "G4", "A4", "A4", "G4", "~"], 100, 1.0),
    ("scale", ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"], 90, 1.0),
    ("eighths", ["E4", "G4", "E4", "G4", "A4", "G4", "E4", "D4"], 120, 0.5),
    ("repeated", ["A3", "A3", "A3", "A3", "C4", "C4", "B3", "~"], 100, 1.0),
    ("riff", ["A3", "C4", "E4", "D4", "C4", "A3", "~", "~"], 140, 0.5),
    ("slow", ["G3", "B3", "D4", "G4", "~", "~", "~", "~"], 70, 1.0),
    ("fast", ["C4", "E4", "G4", "C5", "G4", "E4", "C4", "~"], 150, 0.5),
]

KEYS = {
    "twinkle": "C major",
    "scale": "C major",
    "eighths": "E minor",
    "repeated": "A minor",
    "riff": "A minor",
    "slow": "G major",
    "fast": "C major",
}


@pytest.fixture(scope="module")
def results() -> dict:
    return {name: transcribe(hum(notes, bpm, beat)) for name, notes, bpm, beat in CASES}


@pytest.mark.parametrize("name", [case[0] for case in CASES])
def test_key_is_exact(results, name):
    """Key estimation was right on every case when this was written."""
    assert results[name]["key"] == KEYS[name]


def test_note_sequences(results):
    """At most one case may get the note sequence wrong.

    Not all seven: the repeated-note case splits a held note one extra time,
    and tightening the onset threshold enough to fix it costs a genuine
    re-articulation elsewhere. One is the measured baseline.
    """
    wrong = []
    for name, notes, _, _ in CASES:
        expected = [n.lower() for n in notes if n != "~"]
        actual = [n["note"] for n in results[name]["notes"]]
        if expected != actual:
            wrong.append(f"{name}: expected {expected}, got {actual}")
    assert len(wrong) <= 1, "\n".join(wrong)


def test_tempos(results):
    """Every case within 5% on clean audio.

    Tempo comes from the median gap between note starts. That is a direct
    measurement of the note rate, so it is accurate everywhere but no longer
    exact anywhere: two cases land 2-4% out where the old beat-tracker hit
    them precisely and missed others entirely. The trade was deliberate and is
    worth far more on real recordings - see test_realistic.py.

    5% is musically indistinguishable, and the `setcpm` line is one number to
    edit if a take reads half- or double-time.
    """
    wrong = []
    for name, _, bpm, _ in CASES:
        if abs(results[name]["bpm"] - bpm) > bpm * 0.05:
            wrong.append(f"{name}: expected {bpm}, got {results[name]['bpm']}")
    assert not wrong, "\n".join(wrong)


def test_pattern_is_wellformed(results):
    """Every generated pattern is a setcpm line and one playable note line."""
    for name, _, _, _ in CASES:
        summary = results[name]
        lines = to_pattern(summary).splitlines()
        assert len(lines) == 2, f"{name}: {lines}"
        assert lines[0].startswith("setcpm("), f"{name}: {lines[0]}"
        assert lines[1].startswith('note("'), f"{name}: {lines[1]}"
        # A melody spanning several bars must be stretched over them, or it
        # plays at `cycles` times the tempo it was hummed at.
        if summary["cycles"] > 1:
            assert f".slow({summary['cycles']})" in lines[1], f"{name}: {lines[1]}"


def test_grid_is_monophonic_and_ordered(results):
    """The mini-notation writer assumes non-overlapping, ordered events."""
    for name, _, _, _ in CASES:
        notes = results[name]["notes"]
        for previous, following in zip(notes, notes[1:]):
            assert following["step"] >= previous["step"] + previous["length"], (
                f"{name}: {previous} overlaps {following}"
            )


def test_silence_is_rejected():
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(SR * 2, dtype="float32"), SR, format="WAV")
    with pytest.raises(TranscriptionError):
        transcribe(buffer.getvalue())


def test_too_short_is_rejected():
    with pytest.raises(TranscriptionError):
        transcribe(hum(["C4"], bpm=600))


def test_undecodable_is_rejected():
    with pytest.raises(TranscriptionError):
        transcribe(b"\x00\x00\x00\x20ftypM4A ")
