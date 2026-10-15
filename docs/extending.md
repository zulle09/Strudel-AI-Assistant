# Extending

Recipes for changing StrudelAssistant. Each one says which file to open, what to
watch out for, and whether you have to re-index.

Read [architecture.md](architecture.md) first if you haven't — several of these
depend on invariants described there.

## Answer quality

Most quality problems are retrieval problems, not model problems. Before
reaching for a bigger model, look at what came back: open the **Sources** panel
on a bad answer. If the right page isn't in there, the model never had a chance
and the fix belongs in retrieval.

### Change the chat model

`app/backend/core.py`:

```python
model = init_chat_model("gpt-5.6-luna", model_provider="openai", reasoning_effort="none")
```

`init_chat_model` is provider-agnostic, so `("claude-sonnet-4-5",
model_provider="anthropic")` works too given the right SDK and API key. No
re-indexing — the chat model is unrelated to the embeddings.

### Tune retrieval

`app/backend/core.py`:

- `RETRIEVAL_K = 10` — how many chunks *each* retriever returns before fusion.
  Raising it costs prompt tokens and can bury the good chunk in noise; lowering
  it risks missing the one page that mattered.
- `weights=[0.5, 0.5]` on the `EnsembleRetriever` — the BM25/vector balance.
  Push toward BM25 if exact function-name lookups are missing; toward the vector
  side if conceptual questions are.

No re-indexing for either.

### Change the system prompt

The prompt lives inline in `run_llm` in `app/backend/core.py`. It is doing
several specific jobs — before editing, know which one you're touching:

- telling the model the corpus has two halves and how to tell them apart
- telling it to trust retrieved reference entries over its own Strudel knowledge
- forcing a `retrieve_context` call before any answer, and a retry with
  different wording if the first pass is thin
- splitting behaviour between *explain* and *write me a pattern*, and requiring
  complete runnable patterns in a single fenced block

That last rule is what makes the UI's pattern modules work: the frontend turns
every fenced code block into a Play/Edit/Copy panel. Loosen it and you get
fragments that can't be run.

### Add a tool to the agent

Define it next to `retrieve_context` and add it to the `tools=[...]` list in
`create_agent`. If the tool produces objects the UI needs (not just text for the
model), use `@tool(response_format="content_and_artifact")` and return a
`(text, object)` tuple — then collect it in the `ToolMessage` loop at the bottom
of `run_llm`. See "How sources survive to the UI" in the architecture doc.

A web-search tool is the obvious candidate; `langchain-tavily` is already a
dependency and `TAVILY_API_KEY` is already in `.env`.

## The corpus

### Re-index after changing what gets ingested

```bash
uv run python app/ingestion.py
```

Long-running and paid. It rewrites `data/chunks.json` and upserts to Pinecone.
**Commit the new `chunks.json`** — it is half the retriever, and leaving it stale
makes BM25 and the vector index disagree.

If you have re-run ingestion several times over changing content, consider
clearing the Pinecone index first: changed chunks get new ids and the vectors
under their old ids are orphaned rather than deleted, so stale content
accumulates on the vector side only.

### Crawl different pages

In `main()` in `app/ingestion.py`, the `tavily_crawl.invoke({...})` call:

- `select_paths` / `exclude_paths` are deterministic path filters — the real
  control. Currently `/workshop/*`, `/learn/*`, `/recipes/*` in; `/de/*` (a
  duplicate German translation), `/blog/*` and the showcase out.
- `instructions` is only a soft semantic hint, not a filter. Keep it short.
- `max_depth: 3` is enough because strudel.cc renders its full nav on every
  page, so everything is 1–2 hops from the entry point.

### Change chunking

`RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=200)` in
`main()`. Smaller chunks make retrieval more precise but more likely to cut an
example away from the prose explaining it; larger chunks cost prompt tokens.
Only the crawled prose goes through the splitter — API reference entries are
deliberately kept whole.

### Change the embedding model

Change it in **both** `app/ingestion.py` and `app/backend/core.py`, recreate the
Pinecone index with the new dimension, and re-ingest everything. Query and
stored vectors must come from the same model; mismatched, retrieval returns
plausible-looking noise rather than failing loudly.

### Extract more from the JSDoc comments

`_parse_jsdoc_block` in `app/ingestion.py` keeps `@name`, `@tags`, `@synonyms`,
`@param` and `@example`, and drops the rest. `_reference_doc_to_document`
decides how that becomes the text the model reads. Blocks without `@name` are
skipped — they are file-level comments, not documented functions.

## The interface

`app/web/` — one HTML file, one stylesheet, one script, no build step. Edit and
reload.

### Restyle

Everything visual comes from the custom properties at the top of
`app/web/static/style.css`: `--ink`/`--panel` surfaces, `--cream*` text,
`--amber` (the single accent — power lamp, transport, strings), `--mint`
(numbers and source links only), `--peak` (errors only), plus the three type
roles. Change the tokens rather than individual rules and the whole interface
moves together.

The design is committed to a single dark treatment; there is no light theme to
keep in sync.

### Add a control to a pattern module

`buildPatch()` in `app/web/static/app.js` builds the panel markup and wires the
buttons. `decoratePatches()` is what calls it, on every `pre > code` in a
rendered answer. `note(btn, text)` gives a button a transient label ("Copied",
"Error") and restores it.

### Touch the editor

The editor is a transparent `<textarea>` layered over a highlighted `<pre>`.
**Every metric that affects text layout must stay identical between the two** —
font, size, line-height, letter-spacing, padding, `white-space`,
`overflow-wrap`, `tab-size`. They are set together in one rule
(`.editor__hl, .editor textarea`) for exactly that reason. Change one, change
both, or the caret drifts away from the glyphs.

`syncHighlight()` repaints the layer under it; call it after any programmatic
change to the buffer. `setEditorCode()` does that and persists for you.

### Visualisers (pianoroll, punchcard, scope)

Strudel's visualisers are **Pattern methods**, not standalone functions — it is
`note("c e g").pianoroll()`, chained onto a pattern. A bare `pianoroll()` fails
with "not defined". The methods `@strudel/web` ships are `pianoroll`,
`punchcard`, `wordfall`, `spiral`, `scope`, plus the lower-level `draw` and
`onPaint`.

**The underscore form needs explaining.** On strudel.cc you can write
`sound("bd sd")._pianoroll()` and the roll appears inline, right under that line
of code. That underscore is not part of the pattern API: `plugin-widgets.mjs` in
`@strudel/transpiler` rewrites any call whose method name is a registered widget
type, and `@strudel/codemirror` renders a DOM widget at the recorded source
position. `@strudel/web` ships neither package, so `_pianoroll` does not exist
there at all.

It matters here because Strudel's own JSDoc recommends the underscore form, that
comment is in `data/chunks.json`, and the assistant therefore repeats the advice
— correctly citing the docs — in code that would then fail. So
`aliasUnderscoreVisualisers()` in `app/web/static/app.js` defines `_name` as a
synonym for each visualiser at startup. Pasted REPL code runs; the roll just
appears in the display panel rather than inline under the line.

Each of them takes a canvas context, defaulting to Strudel's own
`getDrawContext()`, which looks for an element with `id="test-canvas"` and, not
finding one, **creates a full-viewport fixed canvas and prepends it to the
body**. In this app that canvas ends up underneath the interface, so patterns
drew correctly and were invisible. `app/web/index.html` therefore supplies its
own `#test-canvas` inside the editor panel, and Strudel draws into that.

Two constraints follow, both handled in `app/web/static/app.js`:

- The context is captured **when the code is evaluated**, so the canvas has to
  be visible and correctly sized before `evaluate()` runs. `prepareDisplay()`
  does that, revealing the panel only when the code actually matches the `DRAWS`
  regex — otherwise the screen would be dead space on every run.
- Strudel draws in backing-store pixels and never scales the context, so the
  canvas needs `width`/`height` set to its CSS size times `devicePixelRatio`.
  `sizeDrawCanvas()` handles it, and is called again whenever the panel is
  resized — a canvas keeps its backing store until you change it, and resizing
  it clears whatever was drawn.

Adding another visualiser to the list is a matter of extending the `DRAWS`
regex; the canvas plumbing is already generic.

### Change what the highlighter colours

`highlight()` in `app/web/static/app.js` is a small tokenizer over a single
alternation regex: comment, string, number, callable identifier, punctuation —
in that order, first match wins. `highlightString()` runs inside string tokens
and dims mini-notation operators (`~ * < > [ ] , / ! @ ?`). Classes are
`.t-str`, `.t-mini`, `.t-num`, `.t-fn`, `.t-punc`, `.t-com` in the stylesheet.

It escapes as it goes rather than escaping first, which is why it can colour
`<` and `>` inside a pattern at all. Keep that property if you rewrite it.

### Panel docking behaviour

The width-based default lives in **CSS**, not JavaScript: with no explicit
choice stored, `body` has no `data-editor` attribute and
`@media (min-width: 1024px)` docks the panel. JavaScript only sets the attribute
when the reader explicitly opens or closes it. This is deliberate — reading
`window.innerWidth` at startup is unreliable when the window is still settling,
and it stops responding to later resizes.

Only the **Editor** and **Close** controls persist a preference. Opening the
panel to receive a pattern, or dismissing the slide-over with Escape or the
backdrop, does not.

### Panel width

The split is driven entirely by one custom property, `--editor-w`. Three rules
read it — the panel's own `width`, the `padding-left` that shifts the
conversation, and the composer's `left` — so dragging the grip writes that one
value on `<html>` and both columns resize in the same frame. Nothing is kept in
sync by hand; if you add another element that must respect the split, have it
read `--editor-w` too rather than measuring the panel.

- `MIN_EDITOR_W` (280) and `CHAT_FLOOR` (420) in `app/web/static/app.js` are the
  bounds — the second one is what stops the conversation from being crushed, so
  the maximum width depends on the window.
- The grip captures the pointer (`setPointerCapture`), so a fast drag doesn't
  fall out of a 7px target. Arrow keys move it too — 16px, or 64 with Shift.
- `body.is-resizing` kills transitions on everything on that axis while
  dragging. Without it the layout eases along behind the pointer.
- `preferredWidth` is what was last asked for; the applied width is that value
  clamped. They differ when the window is too narrow, and separating them is
  what lets the chosen width come back when the window grows again. Only the
  applied width is persisted.

Layout transitions are armed by a `ready` class added after the first two
animation frames. Without it the conversation visibly slides into place on every
load, because JavaScript sets `--editor-w` after the stylesheet has already laid
the page out at zero offset.

## Operations

### Run it

```bash
uv run python app/server.py
```

Serves on `:8000`, or `PORT` if set. Launching by file path matters: it puts
`app/` on `sys.path`, which is how `backend.core` and its `from logger import`
resolve.

There is no auto-reload. Static files (`app/web/*`) just need a browser reload;
Python changes need a server restart.

### Add an endpoint

`app/server.py`. Use a plain `def` for anything that blocks — FastAPI runs those
in a worker thread. Reserve `async def` for genuinely async work, or you will
stall the event loop for every other request.

### Startup and failure behaviour

`core.py` is imported lazily behind a lock and pre-warmed by a background
thread, so the page serves immediately while the BM25 index builds. Configuration
problems surface as a readable message in the chat rather than a crash at boot.
`_explain()` maps common exceptions to sentences that say what to fix — extend
it when you hit a new failure worth naming.

### Seeing the full error

The chat deliberately shows a short, actionable sentence rather than a wall of
Python. The detail is never thrown away — it just lives somewhere else.

**Python tracebacks** are printed to the terminal running the server on every
failed request, unconditionally. `traceback.print_exc()` is used rather than the
`logging` module so the trace lands on stderr no matter how uvicorn's log config
is set up.

To get the same trace in the browser, start the server in debug mode:

```bash
STRUDEL_DEBUG=1 uv run python app/server.py
```

The failed message then carries a collapsible **Traceback** block. Leave it off
by default: the response body is not the place for internals unless you are the
one debugging.

**Strudel pattern errors** (a bad `lpf` argument, a missing sample bank) arrive
as `strudel.log` events, not as rejected promises. The rail readout only has
room for a fragment, so the full message is also written to the browser console
with a `[strudel]` prefix and set as the readout's tooltip.

**Everything else in the browser** — JavaScript exceptions, failed requests,
the exact `/api/chat` payload — is in the browser devtools, console and network
tabs. The app adds no layer on top of them.

**The agent's own reasoning** is the one thing none of the above shows: which
queries it sent to `retrieve_context`, what came back, how many times it looped.
For that, set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` and read
the run on [smith.langchain.com](https://smith.langchain.com). LangChain picks
those up on its own — nothing in the code imports `langsmith` explicitly. When
an answer is wrong and the sources look fine, this is where the reason is.

## Things worth doing

Roughly in order of value:

1. **Stream the answer.** The single biggest UX win. Today the UI shows a
   sweeping bar for the ten-odd seconds a question takes. `run_llm` would return
   an async generator over `agent.astream(...)`, the endpoint would become an
   SSE or chunked response, and the client would append tokens as they arrive.
   The Sources panel can still be attached at the end.
2. **Build the agent once.** `run_llm` calls `create_agent(...)` per request.
   Hoist it to module level next to `model` and `retriever`.
3. **Clean up the leftovers** listed at the end of the architecture doc — the
   stale ingestion docstring, the unused `Chroma` import, `flask` and
   `tavily-python` in `pyproject.toml`.
4. **Delete orphaned vectors on re-ingest**, so BM25 and the vector index stop
   drifting apart. Ingestion already knows every current id; anything else in
   the index is stale.
5. **Evaluate retrieval.** A fixed set of questions with known-correct source
   pages, scored on whether the right page appears in the retrieved set, turns
   the tuning knobs above from guesswork into measurement.
