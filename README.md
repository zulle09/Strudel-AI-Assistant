# StrudelAssistant

A documentation assistant for [Strudel](https://strudel.cc), the JavaScript live-coding environment for algorithmic music. Ask what a function does or describe a pattern you want; answers are grounded in the Strudel documentation, cite the pages they came from, and come back **playable in the browser**.

Or **hum a melody** and get it back as a pattern.

## How it works

- **Retrieval** is hybrid: BM25 (lexical) over a local chunk dump plus Pinecone (semantic) vector search, merged by LangChain's `EnsembleRetriever`. BM25 catches exact function names (`lpf`, `euclid`); the vector side catches paraphrases. Without `data/chunks.json` it falls back to vector-only.
- **The agent** (`gpt-5.4-mini` via LangChain `create_agent`) decides when to call the `retrieve_context` tool, and may search more than once before answering. The raw `Document` objects ride back to the UI on the tool message's `artifact`, which is how sources are cited without re-parsing the prompt.
- **The corpus** is built from two sources: tutorial/concept pages (`strudel.cc/workshop`, `/learn`, `/recipes`) and API reference entries extracted from each function's JSDoc in the Strudel source (`codeberg.org/uzu/strudel`).
- **Playback** uses `@strudel/web` loaded from a CDN — the same engine as the official REPL. Every fenced code block in an answer becomes a pattern module with Play, Edit and Copy, and the lane in the top rail sweeps once per cycle, driven by the repl's own scheduler clock.
- **The agent loop is ReAct**: it searches the docs, writes a pattern, runs it in a Node sandbox with the real Strudel engine, reads the error, and fixes it before answering. `validate_pattern` catches undefined functions, mini-notation parse errors, silent patterns and soundfonts this app never loads, which no amount of prompt-writing reliably prevented.
- **Humming** is transcribed with signal processing, not by the model: `librosa` recovers pitch, tempo and key, and the agent is handed a mini-notation skeleton to turn into something musical. The literal transcription comes back alongside the agent's version, so you always hear what you actually hummed. See [docs/humming.md](docs/humming.md).
- **The editor** is a buffer of your own, docked to the left on a wide screen and a slide-over below that. Edit on any answer sends that pattern into it; `⌘/Ctrl+Enter` re-runs the buffer, swapping the pattern live without stopping the transport. Drag its right edge to resize — the conversation reflows to match. Contents and width persist in `localStorage`.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

The pattern validator runs in Node, so install it too. It is optional: without
it the agent answers from the documentation alone and says its pattern is
unverified, rather than failing.

```bash
cd sandbox && npm install
```

Create a `.env` like the `.env.example` with:

| Variable | Required | Used for |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | chat model + embeddings |
| `PINECONE_API_KEY` | yes | vector index (`strudel-doc-index`) |
| `TAVILY_API_KEY` | ingestion only | crawling the docs site |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | no | tracing on [smith.langchain.com](https://smith.langchain.com) |

Then run the app:

```bash
uv run python app/server.py
```

Open http://localhost:8000. On the first Play, `@strudel/web` downloads the drum samples from GitHub, so the first playback takes a few seconds.

## Building the index

The repo ships with `data/chunks.json`, so retrieval works out of the box as long as the Pinecone index is populated. To rebuild both from scratch:

```bash
uv run python app/ingestion.py
```

This crawls the documentation with Tavily, splits it into chunks, writes `data/chunks.json`, and upserts the embeddings to Pinecone. It is long-running and paid (crawling + embedding thousands of chunks) — run it once.

## Project structure

```
app/
  server.py            # FastAPI: "/" (app shell), "/api/chat", "/api/hum"
  backend/core.py      # hybrid retriever, retrieve_context tool, run_llm agent loop
  backend/melody.py    # audio -> pitch/tempo/key -> mini-notation -> agent prompt
  ingestion.py         # crawl -> chunk -> data/chunks.json + Pinecone upsert
  logger.py            # coloured console output for the ingestion pipeline
  web/
    index.html         # app shell
    static/style.css   # the interface
    static/app.js      # transport, pattern panels, chat
data/chunks.json       # chunk dump that backs BM25
sandbox/               # Node + real Strudel: evaluates a pattern so the agent can check it
tests/                 # transcription accuracy on clean and realistic synthesised hums
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — how the pieces fit, the request
  lifecycle, the ingestion pipeline, and the invariants that will bite you.
- [docs/extending.md](docs/extending.md) — recipes for changing things: model,
  retrieval, prompt, corpus, interface.
- [docs/humming.md](docs/humming.md) — the audio-to-pattern pipeline, how
  accurate it is.

## Stack

FastAPI + uvicorn serving a vanilla HTML/CSS/JS frontend (no bundler, no build step), managed with [uv](https://docs.astral.sh/uv/). `librosa` for pitch, onset, tempo and key analysis. LangChain for the agent and hybrid retrieval, OpenAI for chat and embeddings, Pinecone for the vector index, Tavily for crawling during ingestion, `markdown-it-py` to render answers server-side, and `@strudel/web` via CDN for audio in the browser.

## License note

Strudel is distributed under the **AGPL-3.0** license. By embedding the `@strudel/*` packages in a web app, the AGPL may require that the source code of the entire application be made available under a compatible license — worth verifying (ideally with a lawyer) before commercial production use.

Note that `sandbox/` installs `@strudel/*` on the **server**, not just in the browser. If you deploy this, AGPL-licensed code is running as part of a service you offer over a network, which is exactly the situation the AGPL's network clause is written for. That is a stronger obligation than loading the same library from a CDN into a visitor's browser, and worth raising with the same lawyer.
