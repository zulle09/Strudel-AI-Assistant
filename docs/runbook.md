# Runbook

Operational procedures: standing the app up, tearing it down, rebuilding the corpus, and diagnosing
an outage. Not imported into CLAUDE.md — read this when you're operating the app, not writing code
for it.

## Prerequisites (once, per environment)

1. `.env` with `OPENAI_API_KEY` and `PINECONE_API_KEY` (see `.env.example` — but its comments are
   stale, see `docs/architecture.md`'s "known rough edges"; `PINECONE_API_KEY` is required, not
   optional).
2. A Pinecone index named `strudel-doc-index`, dimension 1536 (matching `text-embedding-3-small`),
   created **before** the first run. TODO: the exact console/CLI steps aren't recorded anywhere in
   this repo, and nothing in the code creates the index — `PineconeVectorStore(...)` in both
   `core.py` and `ingestion.py` assumes it already exists.
3. `conda env create -f environment.yml`, then `conda activate StrudelAssistant`
4. `(cd sandbox && npm install)` — optional; the agent degrades to "unverified" pattern answers
   without it, it doesn't fail to start.
5. `data/chunks.json` — gitignored, not created by the conda env. Either copy one from another
   environment or run ingestion (below). Without it, BM25 is skipped and retrieval silently falls
   back to vector-only.

## Start

```bash
python app/server.py            # binds 127.0.0.1:$PORT (default 8000)
```

That entry point cannot bind non-locally — there's no `--host` flag. For another machine
on the network, run uvicorn directly instead:

```bash
uvicorn server:app --app-dir app --host 0.0.0.0 --port 8000
```

Add `STRUDEL_DEBUG=1` in front of either command to also return tracebacks to the browser; leave it
off unless you're the one debugging.

There's no auto-reload. A Python change needs the process restarted; a change under `app/web/` only
needs a browser reload.

## Stop

Foreground process: Ctrl-C. There's no daemon, service file, or process manager checked into the
repo. TODO: write a real stop procedure once this runs as a long-lived service (systemd unit,
launchd plist, or similar).

## Deploy

TODO — there is no deployment target yet. As of this log the app has only run on a developer's own
machine (`docs/next-steps.html` steps 02/03/15 are all still open). Before writing real steps here,
at minimum:

- pick a host for the FastAPI process,
- put a real hostname and TLS certificate in front of it,
- resolve the AGPL question the README's License note raises: `sandbox/` runs Strudel server-side as
  part of a networked service, not just loaded from a CDN into a visitor's browser, which the README
  says needs a lawyer, not a guess.

## Rollback

TODO — no deployment means no rollback procedure exists yet. When one does: the app has no
persistent state of its own to roll back. Everything persistent lives in Pinecone and in
`data/chunks.json`, and both are the Migrations case below, not the app version. Rolling back the
app code should be as simple as redeploying an older commit, *provided* that commit's expected chunk
shape and embedding model haven't changed since (see the embedding-model invariant in
`docs/architecture.md`).

## Migrations (rebuilding the corpus)

There's no database migration in the usual sense. The closest equivalent is re-running ingestion,
which is long-running and paid:

```bash
python app/ingestion.py
```

What it does: crawls strudel.cc with Tavily, fetches and parses JSDoc comments from the Strudel
source on Codeberg, writes `data/chunks.json` (overwriting it), and upserts embeddings to Pinecone
(updating existing ids by content hash, never deleting old ones).

Before running it:

- Confirm `TAVILY_API_KEY` is set. Ingestion needs it even though `.env.example`'s comment implies
  it's only for an optional `search_web` chat tool — that tool doesn't exist (see
  `docs/architecture.md`).
- If you're changing the embedding model, do it in **both** `ingestion.py` and `core.py` first, and
  recreate the Pinecone index at the new model's dimension before running this — mismatched query
  and stored vectors return silent noise, not an error.

After running it:

- Read the terminal output. A partial failure can look like success: batch upsert failures are
  caught and logged per-batch, so the process exits 0 and still prints "Successfully added all
  batches" even when some batches failed. Look for `Failed to add batch N/M` lines instead of
  trusting the exit code.
- **Commit the new `data/chunks.json`** if this environment's copy is meant to be shared — it's
  gitignored, so nothing forces this, and a stale copy makes BM25 disagree with what's actually in
  Pinecone.
- If you've re-run ingestion several times over changing content, consider clearing the Pinecone
  index first. Changed chunks get new content-addressed ids; the vectors under their old ids are
  never deleted, so repeated runs accumulate orphaned, stale vectors on the vector side only. TODO:
  there's no scripted way to do this yet (`docs/next-steps.html` item 17 proposes one) — today it
  means going into the Pinecone console/CLI by hand.

## What to check when it's down

Roughly in order of how often each one turns out to be the cause:

1. **Nothing answers at `:8000` (or `$PORT`).** The process isn't running, or died at startup. Check
   the terminal it was started in — `_prewarm()` logs `[server] retriever not ready: ...` there if
   the retriever failed to build, but the page itself still serves in that case. A process that
   isn't running at all is a different problem from a page that loads but can't answer (next).
2. **The page loads but every question errors.** Read the error text the UI shows —
   `_explain()` in `server.py` already maps the common cases to a plain sentence:
   - *"No valid OpenAI API key"* → `OPENAI_API_KEY` missing or invalid in `.env`. Restart the server
     after fixing it — `.env` is only read once, at the lazy `core.py` import.
   - *"Couldn't reach the Pinecone index"* → check `PINECONE_API_KEY`, and whether
     `strudel-doc-index` actually exists and has vectors in it (run ingestion if it's empty).
   - *"rate limiting"* → wait and ask again; retries with backoff aren't implemented yet
     (`docs/next-steps.html` item 11).
   - Anything else comes back as `ExceptionType: message` verbatim — that's not a mapped case yet.
     Consider adding one to `_explain()` if it recurs (see CLAUDE.md's "fix with ground truth, not
     prompt rules" rule — the equivalent here is: fix with a better error mapping, not a workaround).
3. **Need the full traceback.** Restart with `STRUDEL_DEBUG=1` (see Start) to also get it in the
   browser. The terminal always has it via `traceback.print_exc()` regardless of debug mode or
   uvicorn's own log configuration.
4. **Answers work but patterns come back "unverified".** The Node sandbox is unavailable — either
   `node` isn't on `PATH`, or `sandbox/node_modules` doesn't exist (`cd sandbox && npm install`).
   This degrades silently: no error, no server-side log line, just an agent that says so in its
   answer text.
5. **Retrieval seems to ignore exact function names.** `data/chunks.json` is probably missing
   (gitignored, not restored by the conda env) — BM25 silently falls back to vector-only. Restore the
   file or re-run ingestion.
6. **Humming requests fail specifically, chat is fine.** `POST /api/hum` imports `backend.melody`
   (and therefore `librosa`) lazily, inside the handler. Confirm the conda env actually has
   `librosa`/`soundfile`/`scipy` — a partial install would only surface here, not at server
   startup.

## Tests

```bash
pytest              # ~5s, no network or API keys needed, 62 tests
```

This only exercises `app/backend/melody.py` against synthesized hums. It proves nothing about the
agent, retrieval, the server, the frontend, or the sandbox — those need a manual run with real API
keys. A test failing on an otherwise-unrelated change usually means a shared constant moved
(`STEPS_PER_BEAT`, `BPM_MIN`/`BPM_MAX`, `DIP_PROMINENCE`, ...) — see the test files' own docstrings
and `docs/humming.md` for what each threshold was measured against before changing it.
