"""The same pipeline, against hums that behave like a person.

test_melody.py proves the pipeline works on a clean signal. This proves it
survives the things a microphone actually picks up: portamento, breath noise,
timing jitter, uneven loudness, room rumble and a singer drifting flat.

Both of the numbers below moved because of this file. Segmenting on spectral
flux found 4 of 6 note boundaries on a noisy take and collapsed every repeated
note into one; segmenting on loudness dips finds them all. Tempo from
beat_track landed within 10% in 9 of 21 takes (8 of 21 when re-measured
on 2026-10-15); tempo from the median gap between note starts lands in 21 of 21.

Three seeds per case, and the bar is two of three: these signals are random,
and demanding perfection from every seed would make the suite flaky rather
than strict.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.melody import transcribe  # noqa: E402
from realistic import realistic_hum  # noqa: E402

SEEDS = (1, 2, 3)

# name, notes, bpm, beats per note, key
CASES = [
    ("twinkle", ["C4", "C4", "G4", "G4", "A4", "A4", "G4", "~"], 100, 1.0, "C major"),
    ("scale", ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"], 90, 1.0, "C major"),
    ("eighths", ["E4", "G4", "E4", "G4", "A4", "G4", "E4", "D4"], 120, 0.5, "E minor"),
    ("repeated", ["A3", "A3", "A3", "A3", "C4", "C4", "B3", "~"], 100, 1.0, "A minor"),
    ("slow", ["G3", "B3", "D4", "G4", "~", "~", "~", "~"], 70, 1.0, "G major"),
    ("riff", ["A3", "C4", "E4", "D4", "C4", "A3", "~", "~"], 140, 0.5, "A minor"),
    ("fast", ["C4", "E4", "G4", "C5", "G4", "E4", "C4", "~"], 150, 0.5, "C major"),
]


@pytest.fixture(scope="module")
def results() -> dict:
    return {
        name: [
            transcribe(realistic_hum(notes, bpm=bpm, beat=beat, seed=seed))
            for seed in SEEDS
        ]
        for name, notes, bpm, beat, _ in CASES
    }


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_note_sequence_survives_a_messy_take(results, case):
    """The notes are the content. Losing one loses the melody."""
    name, notes, _, _, _ = case
    expected = [n.lower() for n in notes if n != "~"]
    matches = sum(
        1 for summary in results[name] if [n["note"] for n in summary["notes"]] == expected
    )
    got = [[n["note"] for n in s["notes"]] for s in results[name]]
    assert matches >= 2, f"{name}: wanted {expected}, got {got}"


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_tempo_within_ten_percent(results, case):
    """Looser than the clean suite: jitter is in the signal by design."""
    name, _, bpm, _, _ = case
    found = [s["bpm"] for s in results[name]]
    close = sum(1 for value in found if abs(value - bpm) <= bpm * 0.10)
    assert close >= 2, f"{name}: wanted ~{bpm}, got {found}"


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_key_survives_a_messy_take(results, case):
    """Key was the one thing noise never broke, even before the rework."""
    name, _, _, _, key = case
    found = [s["key"] for s in results[name]]
    assert sum(1 for value in found if value == key) >= 2, f"{name}: wanted {key}, got {found}"


def test_repeated_notes_are_not_collapsed(results):
    """The failure that drove the rework, pinned so it cannot come back.

    Humming "a a a a" is one unbroken run of the same semitone, so pitch
    tracking alone hears a single long note. On a noisy take the spectral-flux
    detector missed the re-articulations too, and four notes became one.
    """
    for summary in results["repeated"]:
        assert len([n for n in summary["notes"] if n["note"] == "a3"]) >= 3, (
            f"repeated a3 collapsed: {[n['note'] for n in summary['notes']]}"
        )


def test_beat_track_fallback_is_logged(capsys):
    """Three notes make two gaps, below the three the median needs."""
    transcribe(realistic_hum(["C4", "E4", "G4"], bpm=100, seed=1))
    assert "fell back to beat_track" in capsys.readouterr().out


def test_normal_takes_do_not_use_the_fallback(capsys):
    name, notes, bpm, beat, _ = CASES[0]
    transcribe(realistic_hum(notes, bpm=bpm, beat=beat, seed=1))
    assert "beat_track" not in capsys.readouterr().out
