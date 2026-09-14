# CLAUDE.md

@docs/architecture.md
@docs/decisions.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

StrudelAssistant answers questions about [Strudel](https://strudel.cc), a JavaScript live-coding
music environment. It combines RAG with a ReAct agent, returns patterns you can play, and turns a
hummed melody into a pattern. The parts are a Python backend (FastAPI + LangChain), a vanilla JS
frontend, a Node sandbox that runs the real Strudel engine, and a Capacitor iOS shell.

The docs are detailed. Read the relevant one before a non-trivial change:
- `docs/architecture.md` (imported above): request lifecycle, ingestion, frontend, invariants and traps
- `docs/decisions.md` (imported above): append-only log of choices that had real alternatives, newest first
- `docs/runbook.md` (not imported — read it when operating the app, not when coding): deploy,
  rollback, migrations, what to check when it's down
- `docs/extending.md`: step-by-step guides for changing the model, retrieval, prompt, corpus, UI,
  visualisers and debugging
- `docs/humming.md`: the audio-to-pattern pipeline, accuracy numbers, and the iOS build

## Commands

```bash
uv sync                                        # Python 3.13 deps (uv.lock)
(cd sandbox && npm install)                    # pattern validator; the agent degrades without it
uv run python app/server.py                    # http://localhost:8000, PORT overrides; no auto-reload
STRUDEL_DEBUG=1 uv run python app/server.py    # also return tracebacks to the browser

uv run pytest                                  # whole suite, ~5s, no network or API keys
uv run pytest tests/test_melody.py::test_tempos
uv run pytest tests/test_realistic.py -k repeated

# Run the validator by hand: pattern on stdin, JSON report on stdout
cd sandbox && echo 's("bd sd")' | node --permission --allow-fs-read="$PWD" validate.mjs

uv run python app/ingestion.py                 # rebuilds chunks.json + upserts Pinecone: slow and PAID
```

No linter or formatter is configured. The frontend has no build step: edit `app/web/*` and reload
the browser. Python changes need a server restart.

The tests only cover `app/backend/melody.py`, using synthesised hums. The agent, retrieval, server
and frontend have no tests. Checking them means a manual run with `OPENAI_API_KEY` and
`PINECONE_API_KEY` in `.env`.

## Decisions log

Before ending a session in which we settled on an approach that had a real alternative — not
routine work, not a one-way call with nothing else sane to do — append an entry to
`docs/decisions.md`. Newest first, format:

```
## YYYY-MM-DD — <short title>
<what we chose and why>
<what would make us revisit>
```

Mark anything inferred rather than confirmed with "(inferred — confirm)" so it can be verified or
deleted later.

## Architecture

Two pipelines that only meet through stored data: `app/ingestion.py` (run by hand, writes
`data/chunks.json` and the Pinecone index) and `app/server.py` (every request, only reads them). The
request lifecycle, the ReAct/sandbox agent loop, hybrid retrieval, the humming pipeline and the
frontend are all in `docs/architecture.md`, imported above — extend that doc rather than restating
its content here.

## Rules

Things that have already gone wrong once, or are easy to break while "helpfully" refactoring. The
reasoning behind each is in `docs/architecture.md`; this is the checklist.

- **Launch by file path** (`app/server.py`, `uv run pytest`, ...) — moving files breaks the
  `sys.path` setup the imports depend on.
- Don't import `backend.core` at module level anywhere outside `server.py`'s lazy loader — it does
  real work (builds BM25, opens Pinecone, needs API keys) at import time.
- New blocking endpoints in `server.py` are plain `def`, not `async def`, so FastAPI runs them in a
  worker thread. Reserve `async def` for genuinely async work, or you stall the event loop for every
  other request.
- **Never drop the sandbox's `SANDBOX_FLAGS`** (`--permission --allow-fs-read=<sandbox>`) — they are
  a security control against model-written code, not a tidiness preference. Code goes to the sandbox
  over stdin, never argv. `validate.mjs` must print only its JSON report to stdout; everything else
  goes to stderr.
- Don't delete `sandbox/stubs/kabelsalat-web` — `@strudel/core@1.2.6` fails to import without it.
- Keep `DEFAULT_SOUND`/`AVAILABLE_SOUNDS` (`melody.py`) in sync with what's actually loaded in the
  browser (`ensureStrudel` in `app.js`) — only dirt-samples and the built-in synths exist there.
- The embedding model is set in two places (`ingestion.py`, `core.py`) — change both together, and
  re-index from scratch, including recreating the Pinecone index at the new dimension.
- Keep `to_pattern` and `build_prompt` going through `melody_expression` (`melody.py`) — that's what
  stops the literal transcription and the agent's pattern from being able to disagree on the notes.
- Keep answers rendered with `MarkdownIt(..., {"html": False})` — the text is model output derived
  from crawled third-party content.
- Keep `.editor__hl, .editor textarea` (`style.css`) sharing every text-layout property — the
  editor's caret depends on the highlighted copy underneath it having identical metrics.
- **Fix agent mistakes with ground truth, not prompt rules.** Improve a tool, or what it reports,
  instead of adding another rule to the system prompt. The one prompt rule that does stay: a pattern
  comes back complete, in a single fenced code block — the frontend's Play/Edit/Copy panels depend
  on that.
- The melody test thresholds (`tests/`) are measured baselines, not arbitrary numbers — see their
  docstrings and `docs/humming.md`. If a tuning change moves a result, report it instead of loosening
  the threshold.
