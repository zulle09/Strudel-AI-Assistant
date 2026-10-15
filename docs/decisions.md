# Decisions

Append-only log, newest first. Add an entry before ending a session in which we settled a choice
that had real alternatives; skip routine work. Entries marked "(inferred — confirm)" were
reconstructed from code, comments and commit messages rather than a recorded decision — verify or
delete them.

## 2026-10-14 — Drop the iOS shell and the CORS allowlist; web app only

Removed `mobile/` (Capacitor shell), `STRUDEL_API_BASE` in `app.js`, and the CORS middleware plus
`STRUDEL_ALLOWED_ORIGINS` in `server.py`. The page and API are always same-origin now, so none of
it has a job. Focus is the web app. The shell is recoverable from git history (added in `2d88161`).
The older iOS entry below is left as written.

Revisit when a backend deployment target exists and a native app is wanted again.

## 2026-10-15 — Don't cache the agent; make a failed ingestion batch exit 1

Two small calls made together. The agent is still rebuilt on every request: `create_agent` measured
1.2 ms median (2.7 ms max, 20 builds) against about 10 s for the model call, so the "build it once"
idea in `docs/next-steps.html` and `docs/extending.md` had nothing to save, and a per-model cache
would be extra state for no gain. Ingestion now treats any failed Pinecone batch as a failed run: it
prints the failed batch numbers and exits 1, because the old code counted `Exception` objects that
`add_batch` never let through and so always reported "all batches" as added.

The ingestion change was checked against a stubbed Pinecone (all ok, two failures, all failing), not
a real failed upload, since a real run is slow and paid. Revisit the agent cache if retrieval or the
model call ever gets fast enough for 1 ms to matter.

## 2026-10-15 — Tempo numbers re-measured: 21 of 21, and the beat_track fallback now logs

Re-ran both tempo methods over the 21 realistic takes (7 melodies x 3 seeds). Median gap between
note starts: 21 of 21 within 10% and 21 of 21 within 5%. `beat_track` alone: 8 of 21 within 10%
(the 2026-09-12 entry recorded 9; the difference is probably library or seed noise, not a
regression). So 21 of 21 is correct and the `_estimate_tempo` docstring's "19 of 21" was wrong; it
and the stale `beat_track` mentions in `melody.py` and `docs/humming.md` are fixed. The
`beat_track` fallback (fewer than 3 usable note gaps) now prints a warning each time it fires, which
answers the earlier "nothing counts how often it's used". It prints a line rather than keeping a
counter.

Revisit once real hums exist: the 21 takes are synthesized, so 21 of 21 says the method is sound on
them, not that a person will get it.

## 2026-10-15 — The app user picks the chat model, from a fixed GPT-5.6 allowlist

A dropdown above the composer sends a model id with each chat and hum request. The server accepts
only ids in `app/backend/models.py` (Luna, Terra, Sol, default Luna) and answers 400 to anything
else, so a client cannot choose an unlisted model. Alternatives were an env var for the developer to
set, or a free-text model name; the first does not put the choice in the user's hands and the second
lets anyone spend on any model. The 5.6 family was chosen because all three tested in the same
session accept tool calling on Chat Completions with `reasoning_effort="none"` and return string
content, so no Responses API work was needed. This also corrects the 2026-09-12 chat-model entry's
claim that `gpt-5.6-sol` needs the Responses API: it only rejected the default reasoning setting.

`gpt-5.4-mini` and `gpt-5.5` were left off the list, and Terra and Sol were not benchmarked on the
melody task (only a live chat answer each, and one hum on Terra). Revisit by running the benchmark
on them before recommending anything but Luna, and if the open server needs a spend guard, since the
picker lets any visitor pick Sol.

## 2026-10-15 — Conda env instead of uv

Dropped `pyproject.toml`, `uv.lock` and `.python-version` for `environment.yml` (Python 3.13 from
conda-forge, the same pins installed through its `pip:` section, plus `pytest`). All commands in the
docs are now `conda activate StrudelAssistant` followed by plain `python` / `pytest`. The reason is
that the project is now run from the `StrudelAssistant` conda env (the stated reason for the switch
was only "we switched to conda"; the motive beyond that is not recorded).

Cost: no lockfile, so installs are no longer reproducible; `pip` resolves the `>=` pins fresh each
time. Revisit if a fresh `conda env create` ever pulls different versions from the ones in use, by
adding `conda-lock` or an exported `conda env export` file.

## 2026-10-14 — Chat model: `gpt-5.6-luna` with `reasoning_effort="none"`

Supersedes the "stay on `gpt-5.4-mini`" entry below, which was written before credits were added.
Five runs each on a 16-note, 4-cycle hum through the full pipeline: both models 5/5 clean
(`gpt-5.4-mini` mean 11.0s, `gpt-5.6-luna` mean 9.5s). Luna is $0.20 / $1.20 per M tokens against
$0.75 / $4.50 (OpenAI's model docs), so same quality at about a quarter of the cost. Luna rejects
function tools on Chat Completions unless `reasoning_effort` is `"none"`; with that set it returns
string content, so `run_llm` and `server.py` are unchanged. One melody, five runs: a narrow
benchmark, not a quality survey.

Revisit if answer quality on non-melody chat questions looks worse (nothing here measured that), if
`reasoning_effort="none"` turns out to hurt multi-step validate-and-fix loops on hard patterns, or
if the Responses API generalisation lands and a stronger model is wanted.

## 2026-10-14 — Stay on `gpt-5.4-mini`; `gpt-5.6-luna` untested

`gpt-5.6-luna` (reported at about $0.20/$1.20 per M tokens vs $0.75/$4.50 for `gpt-5.4-mini`, from
third-party trackers) was the candidate swap. The OpenAI account had no credits, so neither tool
calling on the Chat Completions path nor the melody benchmark could be run. No model change made.

Revisit once credits are added: check Luna with `init_chat_model`, rerun the benchmark in the
`core.py` comment, and swap only if it scores 5/5 without needing the Responses API.

## 2026-09-12 — Tempo from the median gap between note starts, not `beat_track`

Measured on the realistic-hum suite, `beat_track` (percussive onset strength) landed tempo within
10% in 9 of 21 noisy takes; the median inter-onset interval, made accurate once segmentation moved
to loudness dips, landed within 10% far more often — 21 of 21 per the commit message and
`docs/humming.md`, though `_estimate_tempo`'s own docstring says 19 of 21 (the repo disagrees with
itself; see `docs/architecture.md`). `beat_track` is kept as the fallback when there are fewer than
3 usable gaps. The median, not the mean, because a melody ending on a long note or a rest skews the
mean upward.

Revisit if a real recorded take (not synthesized) lands outside the accuracy figures above, or if
the fallback path fires often enough to matter — nothing currently counts how often it's used.

## 2026-09-12 — Segment notes on loudness dips, not spectral-flux onsets

Spectral-flux onset detection found 4 of 6 repeated-note boundaries on one noisy take and misplaced
them on another; breath noise dominates the flux. Loudness (RMS) dips find every boundary in every
tested take, clean and noisy, and the result doesn't move across a threefold range of
`DIP_PROMINENCE`. This also removed the need for a separate onset detector, since a legato hum has
no attack transient to key off.

Revisit if a real voice produces a loudness profile the synthesized realistic-hum generator doesn't
model (e.g. strong dynamic accents that look like re-articulations).

## 2026-09-12 — Agent loop is ReAct over a sandbox tool, not more system-prompt rules

The agent was producing invalid Strudel (undefined functions, JS variable assignments, unloaded
General MIDI soundfonts) that prompt rules kept failing to prevent — each fix caught one failure
mode and the model found a new variation. `validate_pattern` runs the real Strudel engine and
returns ground truth the agent can react to, which doesn't require anticipating failure modes in
advance. See `[[react-agent-design]]` in memory.

Revisit if a fix to a recurring agent mistake gets made by adding a prompt rule instead of improving
what a tool reports — CLAUDE.md's Rules section already asks future sessions to catch this.

## 2026-09-12 — Node sandbox runs patterns with `--permission --allow-fs-read=sandbox/`, nothing else

Measured directly: without `--permission`, a pattern containing `writeFileSync` wrote to `/tmp` and
a pattern containing `fetch` reached the network. With the flag and read-only access scoped to
`sandbox/`, filesystem writes, network and subprocess spawns are all denied while ordinary patterns
still evaluate. The code being validated is untrusted model output.

Revisit only after re-measuring against a new Node version — don't assume `--permission`'s coverage
carries forward across an upgrade without testing it again.

## 2026-09-12 — Sandbox queries patterns instead of playing them

`validate_pattern` needs to know what a pattern does, not to make sound. Querying
(`pattern.queryArc(0, 4)`) is Strudel's own mechanism for asking what events fall in a range, and
it's pure computation — no audio device, scheduler or real time needed, which is what makes running
the sandbox inside a headless server process workable at all.

Revisit if the agent ever needs to check something only observable at render time. Sound-name
existence already can't be checked this way — it's why the sandbox only flags the `gm_` prefix
instead of validating sample names against the real list (see `docs/next-steps.html` item 09).

## 2026-09-12 — `sandbox/stubs/kabelsalat-web` stubs the missing `SalatRepl` export

`@strudel/core@1.2.6` depends on a published `@kabelsalat/web` that doesn't export `SalatRepl`, so
importing core throws before any of its own code runs. Every published version from 0.3.0 to 0.4.1
was tested and fails the same way, and core has no release past 1.2.6. The stub exports a class that
throws if actually constructed, since only live audio — which this sandbox never runs — would
construct one.

Revisit when `@strudel/core` publishes a release newer than 1.2.6, or a `@kabelsalat/web` release
restores the export — check both before assuming the stub is still needed.

## 2026-09-12 — Chat model: `gpt-5.4-mini`

Benchmarked five runs each on this app's own melody task (transcribed melody survives verbatim,
`.slow(n)` kept, no invalid variable assignment, no unloaded soundfont): `gpt-5.4-mini` 5/5 in 4.5s,
`gpt-5.5` 5/5 in 12.6s, `gpt-6-astra` 5/5 in 10.1s, `gpt-5.6-sol` 5/5 in 24.3s, `gpt-4o-mini` 1/5 in
5.2s (dropped `.slow(2)` in four runs). The larger models scored no better, so they buy nothing here;
`gpt-4o-mini` was faster but unreliable. Pricing itself was not compared. `gpt-6-astra` and
`gpt-5.6-sol` also need the Responses API, which returns content blocks instead of a string — a
further change `run_llm` and the server don't currently make.

Revisit if the string-shaped answer handling in `run_llm`/`server.py` is generalized to content
blocks (`docs/next-steps.html` item 14), which would remove the reason the newest two models were
set aside, or if this benchmark is re-run on real (not synthesized) hums.

## 2026-09-12 — Two patterns always come back from `/api/hum`, literal pattern shown first

`pattern` (the direct transcription) is returned even when the agent's model call fails outright,
and the UI renders it above the agent's elaboration. Recognising your own melody played back is the
point of the feature, so it can't depend on the model behaving. The same reasoning is why
`melody_expression` hands the agent a finished `note(...).slow(n)` expression to chain onto, rather
than a rule to remember: asked to add `.slow(2)` the model forgets often enough that
`repair_answer()` exists to put it back afterward.

Revisit if `repair_answer`'s narrow trigger (exact mini-notation match, no existing `.slow`) starts
missing real cases — that would argue for fixing the prompt instead of patching the output.

## 2026-09-12 — The model never hears hummed audio; signal processing transcribes, the agent elaborates

Language models are unreliable at hearing pitch and timing and good at making a correct skeleton
musical. `melody.py` does pitch/tempo/key extraction with `librosa` and hands the agent a text
summary plus a ready-made mini-notation expression; no second agent or system prompt — the existing
"code creation" branch of the chat prompt handles it.

Revisit if a multimodal path is ever benchmarked against this one on the same melody suite — nothing
in the repo suggests that's been tried.

## 2026-09-12 — iOS app is a Capacitor shell around the same web app; the backend stays remote

"There is no second interface": `sync-www.mjs` copies `app/web` into `mobile/www` verbatim rather
than building a native UI. The Python backend needs an OpenAI key and a Pinecone index, so it isn't
bundled into the app; the phone talks to it over the network, which is why CORS and
`STRUDEL_API_BASE` exist at all.

Revisit once the backend has a real deployment target — today the app can only reach a Mac on the
same LAN (`docs/next-steps.html` steps 03/04/15, none done as of this log).

## 2026-09-12 — The browser converts recordings to 16 kHz mono WAV before upload

`MediaRecorder` produces WebM/Opus (Chrome) or AAC-in-MP4 (Safari/iOS), and `libsndfile` (under
`soundfile`) reads neither. `OfflineAudioContext` decodes, downmixes and resamples client-side using
the browser's own resampler, so the server only ever handles one format, and the upload shrinks by
roughly an order of magnitude — worth doing before `server.py`'s `MAX_UPLOAD_BYTES` cap applies
rather than after.

Revisit if a format `libsndfile` can read natively becomes available from `MediaRecorder` on every
target browser.

## 2026-09-12 (inferred — confirm) — Hybrid retrieval: BM25 + Pinecone via `EnsembleRetriever`, 50/50

No commit message weighs this against a vector-only design, but the code and its comments are
explicit about the reasoning: BM25 catches exact function names dense embeddings sometimes rank low
(`lpf`), Pinecone catches paraphrases BM25 can't see ("how do I make it sound muffled"). The 50/50
weight and `k=10` per retriever aren't otherwise justified anywhere in the repo.

Revisit by tuning `weights=` in `_build_retriever()` against real query logs once there are any —
right now the split is a reasonable default, not a measured one.

## 2026-08-27 (inferred — confirm) — FastAPI instead of Flask

`app/server.py` (FastAPI) and the `fastapi`/`uvicorn` dependencies first appear together on
2026-08-27; `flask` was declared as a dependency the day before and was never removed, and nothing
imports it. No commit explains the switch.

Revisit by deleting the unused `flask` dependency (already flagged in `docs/architecture.md`'s known
rough edges and `docs/next-steps.html` item 18) — there's no reason to keep it once this entry
confirms it's dead.

## 2026-08-26 (inferred — confirm) — Pinecone instead of Chroma for the vector index

`ingestion.py` imports `Chroma` but never uses it, next to a comment contrasting it with Pinecone:
"Chroma is a local vector store... Pinecone is a managed vector database service... scalable and
efficient... in the cloud." That reads like leftover reasoning for choosing Pinecone, not a note
about a live option — but no commit confirms Chroma was ever actually wired up and torn back out.

Revisit by deleting the unused `Chroma` import and the `langchain-chroma` dependency it justifies
(already flagged in `docs/architecture.md`'s known rough edges) once this entry confirms the
comparison is settled and not a TODO.

## 2026-08-26 (inferred — confirm) — Hand-built vanilla JS frontend instead of Gradio

The `gradio-UI` branch (one commit, "gradio ui", 2026-08-26) diverged from the same point as the
current frontend and was never merged; the custom HTML/CSS/JS frontend was built starting the next
day and is what shipped. Nothing in either branch's history explains why Gradio was set aside.

Revisit — or delete this entry — if you remember the actual reason. Control over the live-coding UI
(the transport rail, the pattern editor overlay) is the obvious guess, but it's only a guess.
