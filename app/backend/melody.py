"""Hummed audio -> a quantised melody the agent can turn into Strudel.

The model never hears the recording. Pitch and timing are recovered here with
signal processing, and what reaches the agent is a small JSON summary plus a
ready-made mini-notation skeleton. That split matters: language models are
unreliable at hearing pitch and timing, but very good at taking a correct
skeleton and making it musical (choosing a sound, adding effects, a bassline,
drums). So this module's job is to be *right*, not to be creative.

The pipeline is:

    bytes -> mono 22.05k -> pYIN pitch track + onsets -> note segments
          -> tempo (beat_track, octave-folded) -> quantise to 16ths
          -> key (Krumhansl-Schmuckler) -> mini-notation string

Mini-notation syntax used here is confirmed against the project's own corpus
(strudel.cc/workshop/first-notes): `~` is a rest, `@n` elongates an event over
n steps, note names are lowercase with `#`/`b` and an octave digit (`c#4`).
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

# 22.05k is plenty for a voice: pYIN's ceiling here is ~2 kHz, far under
# Nyquist, and halving the rate roughly halves the analysis time.
ANALYSIS_SR = 22_050

# Hop of 256 at 22.05k is ~11.6 ms per frame. Fine enough to place a note
# onset inside a 16th note at any sane tempo.
HOP = 256

# Humming range, generously bracketed. Too wide an interval makes pYIN slower
# and more octave-error prone; too narrow clips the singer.
FMIN_NOTE = "C2"
FMAX_NOTE = "C6"

# Anything shorter than this is a glide, a breath or a cracked note rather
# than something the singer meant as a note.
MIN_NOTE_SEC = 0.08

# Two voiced frames of the same semitone separated by a gap this short are one
# note with a wobble in the middle, not two notes.
MERGE_GAP_SEC = 0.06

# Re-articulation picking, by amplitude rather than by spectral flux.
#
# Humming the same pitch twice means closing and reopening, so the boundary is
# a dip in loudness, not a change in timbre. Measured both ways on hums with
# breath noise, portamento and timing jitter: spectral-flux onset detection
# found 4 of 6 boundaries in one take and put them in the wrong places in
# another, because broadband breath noise dominates the flux. Dips in RMS
# found every boundary in every take, clean and noisy alike, and the result
# does not move across a threefold range of DIP_PROMINENCE - which is what a
# robust signal looks like.
#
# Prominence is measured on RMS normalised to its own peak, so it is relative
# to how loud the take is and a quiet recording is not penalised.
DIP_PROMINENCE = 0.15
DIP_MIN_GAP_SEC = 0.09
RMS_FRAME = 1024

# The quantisation grid. 4 steps per beat (16th notes) in 4/4.
STEPS_PER_BEAT = 4
BEATS_PER_CYCLE = 4
STEPS_PER_CYCLE = STEPS_PER_BEAT * BEATS_PER_CYCLE

# Tempo is folded into this window by doubling or halving. The window is
# wider than one octave, so some tempos have two valid representations - that
# ambiguity is inherent to hearing a pulse and cannot be resolved from the
# audio alone. The bounds matter at the edges: an earlier 70 bpm floor turned
# a melody genuinely hummed at 70 into 138, because measurement noise put it
# a hair under the floor and it was doubled.
BPM_MIN = 60.0
BPM_MAX = 180.0

# A beat-tracked tempo this close to a multiple of 5 is treated as that
# multiple. The tracker's error on a hum is comfortably larger than this, and
# people do not hum at 99.4 bpm on purpose.
BPM_SNAP_TOLERANCE = 0.015

# Krumhansl-Schmuckler key profiles, rotated against a duration-weighted
# pitch-class histogram. Duration weighting (rather than note counts) is what
# keeps a long held tonic from being outvoted by passing sixteenths.
_MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_PITCH_CLASSES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]


class TranscriptionError(ValueError):
    """The recording could not be turned into a melody, with a reason to show."""


@dataclass
class Note:
    """One sung note, before quantisation."""

    start: float      # seconds from the start of the trimmed audio
    end: float
    midi: int         # rounded to the nearest semitone
    confidence: float # mean pYIN voiced probability across the note
    forced: bool = False  # began at a detected onset, so never merge into it


# ------------------------------------------------------------------ loading

def load_audio(data: bytes) -> Tuple[np.ndarray, int]:
    """Decode uploaded bytes to mono float32 at ANALYSIS_SR.

    soundfile (libsndfile) handles WAV/FLAC/OGG but NOT AAC in an .m4a
    container, which is what Safari's MediaRecorder produces. The browser side
    therefore decodes with Web Audio and uploads WAV; this raises a readable
    error rather than a libsndfile stack trace if something else arrives.
    """
    try:
        samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - the cause is in the message
        raise TranscriptionError(
            "Couldn't decode that audio. The app should upload WAV; "
            f"the decoder said: {exc}"
        ) from exc

    mono = samples.mean(axis=1)
    if sr != ANALYSIS_SR:
        mono = librosa.resample(mono, orig_sr=sr, target_sr=ANALYSIS_SR)
    return np.ascontiguousarray(mono), ANALYSIS_SR


# ------------------------------------------------------------------- pitch

def _segment_notes(y: np.ndarray, sr: int) -> List[Note]:
    """pYIN pitch track -> discrete notes.

    pYIN gives a per-frame f0 with a voiced/unvoiced decision. Rounding each
    voiced frame to the nearest semitone and grouping equal runs is crude but
    robust: it needs no onset detector, which is the part that fails on a
    legato hum where there is no attack transient to detect.
    """
    f0, voiced, voiced_prob = librosa.pyin(
        y,
        fmin=float(librosa.note_to_hz(FMIN_NOTE)),
        fmax=float(librosa.note_to_hz(FMAX_NOTE)),
        sr=sr,
        frame_length=2048,
        hop_length=HOP,
    )
    times = librosa.times_like(f0, sr=sr, hop_length=HOP)

    # Pitch runs alone cannot see a repeated note: humming "c c" is one
    # unbroken run of the same semitone. Loudness dips supply the missing
    # boundary, so a run is also cut wherever one falls. On a truly legato
    # hum there are none, and segmentation falls back to pitch alone.
    onset_frames = _rearticulation_frames(y, sr)

    # Round to semitones only where the frame is voiced AND has a real f0;
    # pYIN fills unvoiced frames with NaN, which would poison the rounding.
    usable = voiced & np.isfinite(f0)
    if not usable.any():
        raise TranscriptionError(
            "No pitch found in that recording. Hum or sing a clear note, "
            "close to the microphone, with a little less background noise."
        )
    semitones = np.full(f0.shape, -1, dtype=int)
    semitones[usable] = np.rint(librosa.hz_to_midi(f0[usable])).astype(int)

    notes: List[Note] = []
    run_start: Optional[int] = None
    for i in range(len(semitones) + 1):
        ongoing = i < len(semitones) and semitones[i] >= 0
        same = (
            ongoing
            and run_start is not None
            and semitones[i] == semitones[run_start]
            and i not in onset_frames
        )
        if same:
            continue
        if run_start is not None:
            end_idx = i - 1
            notes.append(
                Note(
                    start=float(times[run_start]),
                    end=float(times[end_idx]) + HOP / sr,
                    midi=int(semitones[run_start]),
                    confidence=float(np.nanmean(voiced_prob[run_start : end_idx + 1])),
                    forced=run_start in onset_frames,
                )
            )
        run_start = i if ongoing else None

    return _clean_notes(notes)


def _rearticulation_frames(y: np.ndarray, sr: int) -> set[int]:
    """Frames where loudness dips enough to mean the singer started a new note.

    Peaks of the negated RMS curve, which is to say its valleys. Prominence -
    how far the curve climbs on both sides of a valley - is the right measure
    here rather than absolute depth: it ignores the slow swell across a phrase
    and responds to the local dip that a re-articulation actually makes.
    """
    rms = librosa.feature.rms(y=y, frame_length=RMS_FRAME, hop_length=HOP)[0]
    peak = float(rms.max())
    if peak <= 0:
        return set()

    valleys, _ = find_peaks(
        -(rms / peak),
        prominence=DIP_PROMINENCE,
        distance=max(1, int(DIP_MIN_GAP_SEC * sr / HOP)),
    )
    return {int(v) for v in valleys}


def _clean_notes(notes: List[Note]) -> List[Note]:
    """Glue same-pitch neighbours across small gaps, then drop the crumbs.

    Order matters. A note whose pitch track flickers for one frame arrives
    here as three fragments; merging first lets the middle fragment be
    absorbed instead of each piece being discarded as too short.
    """
    merged: List[Note] = []
    for note in notes:
        if (
            merged
            and not note.forced
            and note.midi == merged[-1].midi
            and note.start - merged[-1].end <= MERGE_GAP_SEC
        ):
            previous = merged[-1]
            span = (previous.end - previous.start) + (note.end - note.start)
            previous.confidence = (
                previous.confidence * (previous.end - previous.start)
                + note.confidence * (note.end - note.start)
            ) / max(span, 1e-9)
            previous.end = note.end
            continue
        merged.append(note)

    kept = [n for n in merged if (n.end - n.start) >= MIN_NOTE_SEC]
    if not kept:
        raise TranscriptionError(
            "Only found fragments too short to be notes. Try humming more "
            "slowly, holding each note for at least a beat."
        )
    return kept


# ------------------------------------------------------------------- tempo

def _fold(bpm: float) -> float:
    """Double or halve until the tempo sits in the plausible window."""
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm > BPM_MAX:
        bpm /= 2
    return bpm


def _estimate_tempo(y: np.ndarray, sr: int, notes: List[Note]) -> float:
    """Tempo from the median gap between note starts.

    Two earlier approaches were measured and beaten. Fitting onsets to a grid
    at a free bpm is degenerate: the cost is scale-free, so a slower tempo with
    a coarser grid fits just as well and the search drifts downwards.
    beat_track is honest but weak on a voice, because it keys off percussive
    onset strength that a legato hum never produces.

    The median inter-onset interval is a direct measurement of the note rate
    rather than an inference from it, and it became available only once
    loudness dips gave accurate note boundaries. On hums with portamento,
    breath noise and timing jitter it landed within 10% of the true tempo in
    19 of 21 takes, against 9 of 21 for beat_track on the same audio.

    The median, not the mean: a melody that ends on a long note or pauses for
    a rest has a few intervals several times the others, and the mean follows
    them while the median ignores them.

    beat_track remains the fallback for a take with too few notes to have a
    median worth the name. Half- and double-time readings survive either way,
    which is why `setcpm` is the first line of the generated pattern.
    """
    starts = np.array([n.start for n in notes])
    intervals = np.diff(starts)
    # Below ~50 ms is not a rhythm, it is a segmentation artefact.
    intervals = intervals[intervals > 0.05]

    if len(intervals) >= 3:
        bpm = _fold(60.0 / float(np.median(intervals)))
    else:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr, hop_length=HOP, start_bpm=100.0)
        bpm = _fold(float(np.atleast_1d(tempo)[0]) or 100.0)

    # People hum at round tempos. Within the measurement's own margin of error
    # a multiple of 5 is a better guess than the raw figure, and it makes the
    # setcpm line in the result readable.
    nearest_round = round(bpm / 5) * 5
    if abs(bpm - nearest_round) <= nearest_round * BPM_SNAP_TOLERANCE:
        return float(nearest_round)
    return round(bpm, 1)


# --------------------------------------------------------------------- key

def _estimate_key(notes: List[Note]) -> str:
    weights = np.zeros(12)
    for note in notes:
        weights[note.midi % 12] += note.end - note.start
    if weights.sum() <= 0:
        return "unknown"

    weights = weights / weights.sum()
    best = ("c", "major", -math.inf)
    for tonic in range(12):
        rotated = np.roll(weights, -tonic)
        for name, profile in (("major", _MAJOR_PROFILE), ("minor", _MINOR_PROFILE)):
            score = float(np.corrcoef(rotated, profile)[0, 1])
            if score > best[2]:
                best = (_PITCH_CLASSES[tonic], name, score)
    # _PITCH_CLASSES entries already carry their own accidental ("c#"), so
    # upper-casing the letter is the whole of the formatting.
    return f"{best[0].upper()} {best[1]}"


# -------------------------------------------------------------- quantising

def _quantise(notes: List[Note], bpm: float) -> List[Dict[str, int]]:
    """Notes in seconds -> integer step positions on a 16th-note grid.

    Zero-length events are impossible on a grid, so every note claims at least
    one step, and a note that would overlap its successor is truncated. The
    result is a strictly ordered, non-overlapping monophonic line, which is
    what the mini-notation writer below assumes.
    """
    step_sec = 60.0 / bpm / STEPS_PER_BEAT
    origin = notes[0].start

    events: List[Dict[str, int]] = []
    for note in notes:
        start_step = int(round((note.start - origin) / step_sec))
        end_step = int(round((note.end - origin) / step_sec))
        length = max(1, end_step - start_step)
        if events:
            previous = events[-1]
            if start_step <= previous["step"]:
                # Both rounded onto the same step. Keep the longer one, and let
                # it inherit the step so the line cannot drift forward.
                if length <= previous["length"]:
                    continue
                events.pop()
                start_step = previous["step"]
            elif start_step < previous["step"] + previous["length"]:
                # Plain overlap: truncate the predecessor. The writer below
                # assumes a strictly non-overlapping monophonic line.
                previous["length"] = start_step - previous["step"]
        events.append({"step": start_step, "length": length, "midi": note.midi})
    return events


def _note_name(midi: int) -> str:
    """MIDI number -> Strudel note name (`c#4`, `eb3`, lowercase)."""
    return str(librosa.midi_to_note(midi, octave=True, unicode=False)).lower()


def _mini_notation(events: List[Dict[str, int]], total_steps: int) -> str:
    """Grid events -> one flat mini-notation sequence.

    Emitted as a single sequence spanning every cycle, to be stretched with
    `.slow(cycles)` by the caller. The alternative, one bracketed group per
    cycle, nests deeper for no gain and is harder for a person to edit
    afterwards - and being editable afterwards is the point of handing the
    user Strudel source rather than audio.
    """
    tokens: List[str] = []
    cursor = 0
    for event in events:
        if event["step"] > cursor:
            tokens.append(_weighted("~", event["step"] - cursor))
        tokens.append(_weighted(_note_name(event["midi"]), event["length"]))
        cursor = event["step"] + event["length"]
    if total_steps > cursor:
        tokens.append(_weighted("~", total_steps - cursor))
    return " ".join(tokens)


def _weighted(token: str, steps: int) -> str:
    return token if steps <= 1 else f"{token}@{steps}"


# ---------------------------------------------------------------- pipeline

def transcribe(data: bytes) -> Dict[str, Any]:
    """Audio bytes -> the melody summary handed to the agent.

    Raises TranscriptionError with a message meant for the user whenever the
    recording is unusable; every other failure is a bug and propagates.
    """
    y, sr = load_audio(data)
    duration = len(y) / sr
    if duration < 0.5:
        raise TranscriptionError("That recording is too short. Hum for at least a second.")

    # Leading and trailing silence would otherwise become a long rest and drag
    # the quantisation origin away from the first real note.
    y, _ = librosa.effects.trim(y, top_db=30)
    if len(y) < sr // 2:
        raise TranscriptionError("That recording is silent, or too quiet to hear.")

    notes = _segment_notes(y, sr)
    bpm = _estimate_tempo(y, sr, notes)
    events = _quantise(notes, bpm)

    span = events[-1]["step"] + events[-1]["length"]
    cycles = max(1, math.ceil(span / STEPS_PER_CYCLE))
    total_steps = cycles * STEPS_PER_CYCLE

    return {
        "bpm": bpm,
        "cpm": round(bpm / BEATS_PER_CYCLE, 2),
        "key": _estimate_key(notes),
        "cycles": cycles,
        "steps_per_cycle": STEPS_PER_CYCLE,
        "duration_sec": round(float(len(y) / sr), 2),
        "note_count": len(events),
        "lowest": _note_name(min(e["midi"] for e in events)),
        "highest": _note_name(max(e["midi"] for e in events)),
        "confidence": round(float(np.mean([n.confidence for n in notes])), 2),
        "notes": [
            {"note": _note_name(e["midi"]), "step": e["step"], "length": e["length"]}
            for e in events
        ],
        "mini_notation": _mini_notation(events, total_steps),
    }


# The web app's prebake loads dirt-samples and nothing else, so the only
# melodic voices guaranteed to exist at playback time are Strudel's built-in
# synths. `piano` is not among them and fails with "sound piano not found",
# which is a bad first impression for a pattern whose job is to prove the
# transcription worked. `triangle` is always there and is soft enough to hear
# the melody through.
DEFAULT_SOUND = "triangle"

# Same constraint, stated for the agent. Without it the model reaches for the
# General MIDI soundfont names it has seen in the docs (`gm_acoustic_bass`),
# which need @strudel/soundfonts and are not loaded here.
AVAILABLE_SOUNDS = (
    "Strudel's built-in synths (triangle, sawtooth, square, sine, pluck, "
    "supersaw, gm-free FM synths) and the dirt-samples drum banks "
    "(bd, sd, hh, oh, cp, rim, and so on)"
)


def to_pattern(summary: Dict[str, Any]) -> str:
    """The literal, unembellished Strudel transcription. Plays as hummed."""
    return (
        f"setcpm({summary['cpm']})\n"
        f'{melody_expression(summary)}.sound("{DEFAULT_SOUND}")'
    )


# ------------------------------------------------------------------ prompting

def melody_expression(summary: Dict[str, Any]) -> str:
    """The melody alone, with no sound or effects chained onto it yet."""
    expression = f'note("{summary["mini_notation"]}")'
    if summary["cycles"] > 1:
        # The sequence spans several bars, so it has to be stretched over that
        # many cycles or it plays at `cycles` times the tempo it was hummed at.
        expression += f".slow({summary['cycles']})"
    return expression


def build_prompt(summary: Dict[str, Any]) -> str:
    """The melody summary, written out as a request the doc agent can act on.

    Deliberately phrased so the agent's existing "code creation or refinement"
    branch handles it: it gets a complete starting pattern and is asked for a
    complete resulting one. The transcription is stated as fact and marked as
    not-to-be-changed, because the one thing the agent must not do is
    improvise different notes - the user hummed those, and recognising their
    own melody coming back is the entire point of the feature.
    """
    notes = ", ".join(f"{n['note']}@step{n['step']}" for n in summary["notes"][:48])
    return (
        "I hummed a melody and it was transcribed automatically. Here is the "
        "transcription:\n\n"
        f"- Tempo: {summary['bpm']} bpm, i.e. `setcpm({summary['cpm']})`\n"
        f"- Key: {summary['key']}\n"
        f"- Length: {summary['cycles']} bar(s) of 4/4, on a 16th-note grid\n"
        f"- Range: {summary['lowest']} to {summary['highest']}, "
        f"{summary['note_count']} notes, pitch confidence {summary['confidence']}\n"
        f"- Notes (step index on the grid): {notes}\n"
        f"- As mini-notation: `{summary['mini_notation']}`\n"
        f"- Ready to use verbatim: `{melody_expression(summary)}`\n\n"
        "Write a complete Strudel pattern that plays this melody and sounds "
        "good.\n\n"
        "Requirements:\n"
        # Handing over a finished expression rather than a rule to remember:
        # asked to "add .slow(2)" the model simply forgets, and the melody
        # then plays at double speed, which reads as a broken transcription.
        "- Use the melody expression above verbatim, then chain `.sound(...)` "
        "and any effects onto it. Do not substitute different pitches, re-time "
        "them, or drop anything already chained onto it.\n"
        f"- Start with the `setcpm({summary['cpm']})` line.\n"
        f"- Only these sounds are loaded in this app: {AVAILABLE_SOUNDS}. Do "
        "not use a General MIDI soundfont name such as `gm_acoustic_bass`; it "
        "will fail with \"sound not found\".\n"
        "- Choose a sound that suits the melody, and add a little polish: an "
        "effect or two, and if it fits, a simple bassline or drum part in the "
        f"same key ({summary['key']}) that does not fight the melody.\n"
        # Weaker models reach for JavaScript habits here - naming a
        # sub-pattern, then combining the names - and every such answer is
        # dead on arrival in the REPL. Stating the shape of a valid document
        # blocks it more reliably than listing the mistakes one at a time.
        "- Every line must be either the `setcpm(...)` call or a pattern. With "
        "more than one pattern, prefix each with `$:` on its own line. Never "
        "declare a variable or use `=` anywhere: `$:` is a bare prefix, not an "
        "assignment, so `$: note(...)` is right and `$: name = note(...)`, "
        "`name = slow(2)` and `$: a & b` are all wrong.\n"
        "- Keep it short enough to read and edit by hand."
    )


# ------------------------------------------------------------------- repair

# Fenced code blocks in the agent's markdown answer, with the fence language
# tag (```javascript, ```strudel, ```plaintext) skipped.
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def repair_answer(answer: str, summary: Dict[str, Any]) -> Tuple[str, bool]:
    """Put back a `.slow(n)` the model dropped. Returns (answer, repaired).

    The multi-bar case is the one thing the agent gets wrong often enough to
    be worth correcting in code. Told to use `note(...).slow(2)` verbatim it
    complies perhaps half the time, and the half where it drops the `.slow(2)`
    plays the melody at twice the tempo it was hummed at - which reads as a
    broken transcription rather than as a model slip.

    The correct value is known here, so this is a repair and not a guess. It
    is deliberately narrow: it only fires on a code block that contains the
    exact transcribed mini-notation, and only when that block has no `.slow`
    for the right number of cycles anywhere in it. If the model rewrote the
    melody, that is a different failure and not one to paper over - the
    literal pattern beside it is the answer to that.
    """
    cycles = int(summary["cycles"])
    if cycles <= 1:
        return answer, False

    needle = f'note("{summary["mini_notation"]}")'
    slow = f".slow({cycles})"
    repaired = False

    def fix_block(match: "re.Match[str]") -> str:
        nonlocal repaired
        block = match.group(0)
        if needle not in block or slow in block:
            return block
        repaired = True
        return block.replace(needle, needle + slow)

    return _FENCE.sub(fix_block, answer), repaired
