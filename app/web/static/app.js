/* Strudel Assistant - transport, pattern panels, chat.
 *
 * @strudel/web is loaded as a plain global by index.html. initStrudel()
 * resolves to the repl, whose scheduler.now() reports the current CYCLE
 * position - that is what drives the lane in the rail, so the sweep is the
 * real clock rather than an animation at a guessed tempo.
 */

const $ = (id) => document.getElementById(id);

const feed = $("feed");
const led = $("led");
const lane = $("lane");
const laneTicks = $("laneTicks");
const laneHead = $("laneHead");
const readout = $("readout");
const stopBtn = $("stopAll");
const composer = $("composer");
const input = $("input");
const sendBtn = $("send");

const editorPanel = $("editorPanel");
const editorInput = $("editorInput");
const editorHl = $("editorHl").querySelector("code");
const editorRun = $("editorRun");
const editorStop = $("editorStop");
const editorLamp = $("editorLamp");
const editorHint = $("editorHint");
const editorToggle = $("editorToggle");
const editorClose = $("editorClose");
const editorBackdrop = $("editorBackdrop");
const editorGrip = $("editorGrip");
const editorDisplay = $("editorDisplay");
const drawCanvas = $("test-canvas");

const STEPS = 16; // 4 beats of 4 - the subdivision the lane is marked in
const audioAvailable = typeof window.initStrudel === "function";

/* ============================== transport ============================== */

let repl = null;
let initing = null;
let playing = false;
let pending = false;
let activeBtn = null;
let laneWidth = 0;

for (let i = 0; i < STEPS; i++) {
  const tick = document.createElement("span");
  tick.className = "tick";
  if (i % 4 === 0) tick.dataset.beat = "1";
  laneTicks.appendChild(tick);
}

const measureLane = () => { laneWidth = lane.clientWidth; };
measureLane();
window.addEventListener("resize", measureLane);

/* initStrudel()'s default prebake only registers the synths - the drum banks
   every example pattern uses (bd, sd, hh) come from dirt-samples, so they have
   to be loaded explicitly or playback fails with "sound bd not found". This is
   the download that makes the first Play take a few seconds. */
async function ensureStrudel() {
  if (repl) return repl;
  if (!initing) {
    initing = Promise.resolve(
      window.initStrudel({
        prebake: () => {
          const samples = window.strudel && window.strudel.samples;
          return samples ? samples("github:tidalcycles/dirt-samples") : undefined;
        },
      })
    );
  }
  repl = await initing;
  return repl;
}

function refreshLed() {
  led.dataset.state = playing ? "live" : pending ? "busy" : "idle";
}

function setTransport(live) {
  playing = live;
  lane.dataset.state = live ? "live" : "idle";
  readout.dataset.state = live ? "live" : "idle";
  stopBtn.disabled = !live;
  editorStop.disabled = !live;
  if (!live) readout.textContent = "cps —";
  refreshLed();
}

/* One engine, so only one thing is ever the source. A pattern panel shows that
   by flipping its button to Stop; the editor shows it with its lamp, because
   Run has to stay Run - you re-run the buffer to swap the pattern live. */
function setActive(btn) {
  if (activeBtn && activeBtn !== btn) {
    if (activeBtn === editorRun) editorLamp.dataset.state = "idle";
    else {
      activeBtn.dataset.state = "idle";
      activeBtn.textContent = "Play";
    }
  }
  activeBtn = btn;
  if (!btn) {
    editorLamp.dataset.state = "idle";
    return;
  }
  if (btn === editorRun) {
    editorLamp.dataset.state = "live";
  } else {
    btn.dataset.state = "live";
    btn.textContent = "Stop";
  }
}

function cyclePosition() {
  try {
    if (repl && repl.scheduler && typeof repl.scheduler.now === "function") {
      return repl.scheduler.now();
    }
    if (window.strudel && typeof window.strudel.getTime === "function") {
      return window.strudel.getTime();
    }
  } catch (_) { /* clock not up yet */ }
  return 0;
}

function currentCps() {
  const cps = repl && repl.scheduler ? repl.scheduler.cps : null;
  return typeof cps === "number" && isFinite(cps) ? cps : null;
}

/* Strudel reports parse and scheduler failures as `strudel.log` events rather
   than by rejecting evaluate(), so a bad pattern would otherwise light the
   transport and play nothing. Show what went wrong instead. */
let errorUntil = 0;

document.addEventListener("strudel.log", (ev) => {
  const detail = ev.detail || {};
  const message = String(detail.message || "");
  if (detail.type === "error" || /\berror\b/i.test(message)) {
    // The rail only has room for a fragment, so the whole thing goes to the
    // console and to the tooltip - a truncated error is not a debuggable one.
    console.error("[strudel]", message, detail.data ?? "");
    readout.dataset.state = "error";
    readout.textContent = message.length > 46 ? message.slice(0, 46) + "…" : message;
    readout.title = message;
    errorUntil = performance.now() + 5000;
    if (activeBtn) note(activeBtn, "Error");
  }
});

function frame() {
  const now = performance.now();
  if (errorUntil && now >= errorUntil) {
    errorUntil = 0;
    readout.dataset.state = playing ? "live" : "idle";
    readout.title = "";
  }
  if (playing && !errorUntil) {
    const cycle = cyclePosition();
    const phase = ((cycle % 1) + 1) % 1;       // 0..1 through the current cycle
    laneHead.style.transform = `translateX(${phase * laneWidth}px)`;

    const cps = currentCps();
    readout.textContent = cps ? `cyc ${Math.floor(cycle)} · cps ${cps.toFixed(2)}` : `cyc ${Math.floor(cycle)}`;
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* Strudel's visualisers are Pattern methods - `.pianoroll()`, not a standalone
   pianoroll(). They capture a drawing context the moment the code is evaluated,
   so the canvas has to be on screen and correctly sized *before* that happens. */
const DRAW_METHODS = ["pianoroll", "punchcard", "wordfall", "spiral", "scope"];

const DRAWS = new RegExp("\\.\\s*_?(" + DRAW_METHODS.join("|") + ")\\s*\\(");

/* On strudel.cc the underscore form - `sound("bd sd")._pianoroll()` - draws the
   roll inline under the line of code. That underscore is not part of the
   pattern API: it is a transpiler plugin plus a CodeMirror widget, and
   @strudel/web ships neither, so `_pianoroll` simply does not exist here.
   Strudel's own documentation recommends the underscore form (and it is in the
   indexed corpus, so the assistant repeats it), so accept it as a synonym and
   draw into the display panel instead of inline. */
function aliasUnderscoreVisualisers() {
  const proto = window.strudel && window.strudel.Pattern && window.strudel.Pattern.prototype;
  if (!proto) return;
  DRAW_METHODS.forEach((name) => {
    if (typeof proto[name] === "function" && typeof proto["_" + name] !== "function") {
      proto["_" + name] = proto[name];
    }
  });
}

if (audioAvailable) aliasUnderscoreVisualisers();

function sizeDrawCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const rect = drawCanvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  // Strudel draws in backing-store pixels and never scales the context, so the
  // backing store carries the ratio and the CSS box stays at layout size.
  const w = Math.round(rect.width * dpr);
  const h = Math.round(rect.height * dpr);
  if (drawCanvas.width !== w) drawCanvas.width = w;
  if (drawCanvas.height !== h) drawCanvas.height = h;
}

function prepareDisplay(code) {
  if (!DRAWS.test(code)) return;
  openEditor();                 // the screen lives on the panel
  editorDisplay.hidden = false;
  sizeDrawCanvas();             // forces layout, so the context is captured sized
}

async function playPattern(code, btn) {
  const isEditor = btn === editorRun;
  const label = isEditor ? "Run" : "Play";
  btn.textContent = "Loading";
  btn.disabled = true;
  try {
    const r = await ensureStrudel();
    prepareDisplay(code);
    await r.evaluate(code, true);   // while playing this hot-swaps the pattern
    btn.disabled = false;
    if (isEditor) btn.textContent = label;
    setActive(btn);
    measureLane();
    setTransport(true);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = label;
    console.error(err);
    note(btn, "Won't play");
  }
}

function stopAll() {
  try {
    if (window.strudel && typeof window.strudel.hush === "function") window.strudel.hush();
    else if (repl) repl.stop();
  } catch (err) { console.error(err); }
  setActive(null);
  setTransport(false);
  editorDisplay.hidden = true;   // give the space back to the code
}

stopBtn.addEventListener("click", stopAll);

/* Transient label on a button, then back to what it said. */
function note(btn, text) {
  const was = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { if (btn.textContent === text) btn.textContent = was; }, 1400);
}

/* ========================== pattern highlighting ======================== */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => s.replace(/[&<>"]/g, (c) => ESCAPES[c]);

const MINI = "~*<>[],/!@?"; // Strudel mini-notation operators

/* Strings hold the mini-notation, so they carry the accent colour; the
   operators inside them are dimmed so the sound names stay legible. */
function highlightString(raw) {
  let out = "";
  let buf = "";
  for (const ch of raw) {
    if (MINI.includes(ch)) {
      if (buf) { out += esc(buf); buf = ""; }
      out += `<span class="t-mini">${esc(ch)}</span>`;
    } else {
      buf += ch;
    }
  }
  return out + esc(buf);
}

const TOKEN = new RegExp(
  [
    "(\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)",                             // comment
    "(\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`(?:[^`\\\\]|\\\\.)*`)", // string
    "(\\b\\d+(?:\\.\\d+)?\\b)",                                           // number
    "([A-Za-z_$][\\w$]*)(?=\\s*\\()",                                     // callable
    "([(){}\\[\\].,;:+\\-*/=<>|&!?])",                                    // punctuation
  ].join("|"),
  "g"
);

function highlight(code) {
  let out = "";
  let last = 0;
  let m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(code)) !== null) {
    out += esc(code.slice(last, m.index));
    if (m[1]) out += `<span class="t-com">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="t-str">${highlightString(m[2])}</span>`;
    else if (m[3]) out += `<span class="t-num">${esc(m[3])}</span>`;
    else if (m[4]) out += `<span class="t-fn">${esc(m[4])}</span>`;
    else if (m[5]) out += `<span class="t-punc">${esc(m[5])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}

/* Build the playable module around a block of pattern code. */
function buildPatch(code) {
  const patch = document.createElement("div");
  patch.className = "patch";
  patch.innerHTML =
    '<div class="patch__head">' +
      '<span class="micro patch__label">Pattern</span>' +
      '<button class="btn btn--play" type="button" data-state="idle">Play</button>' +
      '<button class="btn btn--edit" type="button">Edit</button>' +
      '<button class="btn btn--copy" type="button">Copy</button>' +
    "</div>" +
    '<pre class="patch__code"><code></code></pre>';

  patch.querySelector("code").innerHTML = highlight(code);

  const play = patch.querySelector(".btn--play");
  if (!audioAvailable) {
    play.remove();
  } else {
    play.addEventListener("click", () => {
      if (play.dataset.state === "live") stopAll();
      else playPattern(code, play);
    });
  }

  // Hand the pattern to the buffer so it can be changed and re-run.
  patch.querySelector(".btn--edit").addEventListener("click", () => {
    setEditorCode(code);
    openEditor();
    editorInput.focus();
  });

  patch.querySelector(".btn--copy").addEventListener("click", async (ev) => {
    try {
      await navigator.clipboard.writeText(code);
      note(ev.currentTarget, "Copied");
    } catch (_) {
      note(ev.currentTarget, "Blocked");
    }
  });

  return patch;
}

/* Every fenced block the model returns becomes a pattern module. */
function decoratePatches(container) {
  container.querySelectorAll("pre > code").forEach((codeEl) => {
    const code = codeEl.textContent.replace(/\s+$/, "");
    codeEl.parentElement.replaceWith(buildPatch(code));
  });
}

/* ============================== rendering ============================== */

/* codeberg.org/uzu/strudel/src/branch/main/packages/core/controls.mjs is not a
   readable link label; the tail of the path is. */
function sourceLabel(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.replace(/\/+$/, "");
    const tail = path.split("/").filter(Boolean).slice(-2).join("/");
    return tail ? `${u.hostname}/${tail}` : u.hostname;
  } catch (_) {
    return url;
  }
}

function buildSources(sources) {
  const details = document.createElement("details");
  details.className = "sources";
  details.innerHTML =
    '<summary class="sources__summary">' +
      '<span class="sources__chev">&rsaquo;</span>' +
      `<span class="micro">Sources ~ ${sources.length}</span>` +
    "</summary>" +
    '<ul class="sources__list"></ul>';

  const list = details.querySelector(".sources__list");
  sources.forEach((src) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = src;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = sourceLabel(src);
    a.title = src;
    li.appendChild(a);
    list.appendChild(li);
  });

  return details;
}

function scrollDown() {
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg msg--user";
  el.textContent = text;
  feed.appendChild(el);
  scrollDown();
}

function addThinking() {
  const el = document.createElement("div");
  el.className = "msg msg--bot thinking";
  el.innerHTML = '<span class="micro">Retrieving</span><span class="thinking__bar"></span>';
  feed.appendChild(el);
  scrollDown();
  return el;
}

function addAnswer(html, sources) {
  const el = document.createElement("div");
  el.className = "msg msg--bot";

  const prose = document.createElement("div");
  prose.className = "prose";
  prose.innerHTML = html;          // server-rendered markdown, raw HTML escaped
  decoratePatches(prose);
  el.appendChild(prose);

  if (sources && sources.length) el.appendChild(buildSources(sources));

  feed.appendChild(el);
  scrollDown();
}

function addError(message, trace) {
  const el = document.createElement("div");
  el.className = "msg msg--bot msg--error";
  el.innerHTML = '<span class="micro">Failed</span>';

  const p = document.createElement("p");
  p.className = "error__message";
  p.textContent = message;
  el.appendChild(p);

  // Only present when the server runs with STRUDEL_DEBUG=1; the terminal has
  // the same trace either way.
  if (trace) {
    const details = document.createElement("details");
    details.className = "trace";
    details.innerHTML = '<summary class="micro trace__summary">Traceback</summary>';
    const pre = document.createElement("pre");
    pre.className = "trace__body";
    pre.textContent = trace;
    details.appendChild(pre);
    el.appendChild(details);
  }

  feed.appendChild(el);
  scrollDown();
}

/* ================================ chat ================================= */

const history = [];   // prior turns only; the pending question is sent separately
let inFlight = false;

async function ask(text) {
  if (inFlight || !text.trim()) return;
  inFlight = true;
  pending = true;
  refreshLed();
  sendBtn.disabled = true;

  // The starter panel is an invitation, not history: it goes as soon as there
  // is a conversation, however the question was asked.
  const start = feed.querySelector(".start");
  if (start) start.remove();

  addUser(text);
  const thinking = addThinking();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history }),
    });
    const data = await res.json().catch(() => ({}));
    thinking.remove();

    if (!res.ok || data.error) {
      addError(
        data.error || `The server returned ${res.status}. Check the terminal running the app.`,
        data.trace
      );
    } else {
      addAnswer(data.answer_html, data.sources);
      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: data.answer_md });
    }
  } catch (err) {
    thinking.remove();
    addError("Couldn't reach the server. Is it still running in your terminal?");
    console.error(err);
  } finally {
    inFlight = false;
    pending = false;
    refreshLed();
    sendBtn.disabled = false;
  }
}

composer.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  ask(text);
});

input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    composer.requestSubmit();
  }
});

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 168) + "px";
}
input.addEventListener("input", autoGrow);

/* The full prompt wraps to a clipped second line on a phone, so the narrow
   layout gets a placeholder that fits on one. */
const PLACEHOLDER_FULL = "Ask about a function, or describe a pattern to build.";
const PLACEHOLDER_SHORT = "Ask about Strudel…";

function fitPlaceholder() {
  input.placeholder = window.innerWidth < 560 ? PLACEHOLDER_SHORT : PLACEHOLDER_FULL;
}
fitPlaceholder();
window.addEventListener("resize", fitPlaceholder);

/* =============================== editor ================================ */

const DEMO = 's("bd sd [~ bd] sd, hh*8")\n  .lpf(sine.range(400, 3000).slow(4))\n  .room(0.3)';
const BUFFER_KEY = "strudel-assistant:buffer";
const OPEN_KEY = "strudel-assistant:editor-open";

const isApple = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");

function saveBuffer() {
  try { localStorage.setItem(BUFFER_KEY, editorInput.value); } catch (_) { /* private mode */ }
}

/* Repaint the highlighted layer under the transparent textarea. A buffer that
   ends in a newline needs a spare character, or `pre` drops that last row and
   the two layers drift apart while you type at the end. */
function syncHighlight() {
  const text = editorInput.value;
  editorHl.innerHTML = highlight(text.endsWith("\n") ? text + " " : text);
  editorHl.parentElement.scrollTop = editorInput.scrollTop;
}

function setEditorCode(code) {
  editorInput.value = code;
  syncHighlight();
  saveBuffer();
}

const wideQuery = window.matchMedia("(min-width: 1024px)");

/* No `data-editor` attribute means "no explicit choice", and the stylesheet
   docks or hides the panel by width on its own. Only a deliberate open/close
   sets the attribute and stores a preference. */
function isEditorOpen() {
  const explicit = document.body.dataset.editor;
  return explicit ? explicit === "open" : wideQuery.matches;
}

function refreshToggle() {
  const open = isEditorOpen();
  editorToggle.dataset.state = open ? "open" : "closed";
  editorToggle.setAttribute("aria-expanded", String(open));
}

function setEditorOpen(open, remember) {
  document.body.dataset.editor = open ? "open" : "closed";
  if (remember) {
    try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (_) { /* private mode */ }
  }
  refreshToggle();
}

/* Opening the panel to receive a pattern, or flicking the slide-over shut, is
   incidental - only the Editor and Close controls state a lasting preference. */
function openEditor() { setEditorOpen(true, false); }
function closeEditor() { setEditorOpen(false, false); }

/* ---- resizing -----------------------------------------------------------
   Everything on both sides of the split reads --editor-w: the panel's own
   width, the body's padding-left that shifts the conversation, and the
   composer's left edge. Moving that one custom property resizes both columns
   in the same frame, so there is nothing to keep in sync by hand. */

const WIDTH_KEY = "strudel-assistant:editor-width";
const MIN_EDITOR_W = 280;   // below this the code wraps into soup
const CHAT_FLOOR = 420;     // the conversation never gets squeezed past this

// What the reader last asked for, which is not always what fits: a narrow
// window clamps the applied width, and widening it again restores the ask.
let preferredWidth = 400;

const maxEditorW = () =>
  Math.max(MIN_EDITOR_W, Math.min(680, window.innerWidth - CHAT_FLOOR));

const clampEditorWidth = (px) =>
  Math.round(Math.min(Math.max(px, MIN_EDITOR_W), maxEditorW()));

function applyEditorWidth(px, remember) {
  preferredWidth = px;
  const width = clampEditorWidth(px);
  document.documentElement.style.setProperty("--editor-w", width + "px");

  editorGrip.setAttribute("aria-valuenow", String(width));
  editorGrip.setAttribute("aria-valuemin", String(MIN_EDITOR_W));
  editorGrip.setAttribute("aria-valuemax", String(maxEditorW()));

  // The visualiser draws into a fixed backing store, so a narrower panel has to
  // re-size it or the picture stretches.
  sizeDrawCanvas();

  // Store the width that was actually applied, never the raw pointer position -
  // dragging past the edge should not persist a number the reader never saw.
  if (remember) {
    try { localStorage.setItem(WIDTH_KEY, String(width)); } catch (_) { /* private mode */ }
  }
}

let resizing = false;

editorGrip.addEventListener("pointerdown", (ev) => {
  if (!wideQuery.matches) return;
  resizing = true;
  // Capture so the drag survives the pointer outrunning the 7px grip.
  editorGrip.setPointerCapture(ev.pointerId);
  document.body.classList.add("is-resizing");
  ev.preventDefault();
});

// The panel is anchored at left: 0, so its width is just the pointer's x.
editorGrip.addEventListener("pointermove", (ev) => {
  if (resizing) applyEditorWidth(ev.clientX, false);
});

function endResize(ev) {
  if (!resizing) return;
  resizing = false;
  document.body.classList.remove("is-resizing");
  try { editorGrip.releasePointerCapture(ev.pointerId); } catch (_) { /* already gone */ }
  // Settle on the achievable width, so an overshoot doesn't linger as the ask.
  applyEditorWidth(clampEditorWidth(preferredWidth), true);
}

editorGrip.addEventListener("pointerup", endResize);
editorGrip.addEventListener("pointercancel", endResize);

editorGrip.addEventListener("keydown", (ev) => {
  if (!wideQuery.matches) return;
  const step = ev.shiftKey ? 64 : 16;
  const keys = {
    ArrowLeft: () => preferredWidth - step,
    ArrowRight: () => preferredWidth + step,
    Home: () => MIN_EDITOR_W,
    End: () => maxEditorW(),
  };
  if (!keys[ev.key]) return;
  ev.preventDefault();
  applyEditorWidth(keys[ev.key](), true);
});

// A narrower window may no longer fit the chosen width; re-clamp the ask
// rather than the applied value, so widening restores what was asked for.
window.addEventListener("resize", () => applyEditorWidth(preferredWidth, false));

function runEditor() {
  if (!editorInput.value.trim()) {
    note(editorRun, "Empty");
    return;
  }
  playPattern(editorInput.value, editorRun);
}

editorInput.addEventListener("input", () => { syncHighlight(); saveBuffer(); });
editorInput.addEventListener("scroll", () => {
  editorHl.parentElement.scrollTop = editorInput.scrollTop;
});

editorInput.addEventListener("keydown", (ev) => {
  // The live-coding keystroke: re-run the buffer, swapping the pattern without
  // stopping the transport.
  if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
    ev.preventDefault();
    runEditor();
    return;
  }
  if (ev.key === "Tab") {
    ev.preventDefault();
    const start = editorInput.selectionStart;
    const end = editorInput.selectionEnd;
    editorInput.value = editorInput.value.slice(0, start) + "  " + editorInput.value.slice(end);
    editorInput.selectionStart = editorInput.selectionEnd = start + 2;
    syncHighlight();
    saveBuffer();
  }
});

editorRun.addEventListener("click", runEditor);
editorStop.addEventListener("click", stopAll);
editorClose.addEventListener("click", () => setEditorOpen(false, true));
editorBackdrop.addEventListener("click", closeEditor);
editorToggle.addEventListener("click", () => setEditorOpen(!isEditorOpen(), true));

document.addEventListener("keydown", (ev) => {
  // Escape only dismisses the slide-over; docked it has nowhere to go.
  if (ev.key === "Escape" && isEditorOpen() && !wideQuery.matches) closeEditor();
});

function initEditor() {
  let saved = null;
  try { saved = localStorage.getItem(BUFFER_KEY); } catch (_) { /* private mode */ }
  editorInput.value = saved || DEMO;
  syncHighlight();

  let storedWidth = null;
  try { storedWidth = parseFloat(localStorage.getItem(WIDTH_KEY)); } catch (_) { /* private mode */ }
  applyEditorWidth(Number.isFinite(storedWidth) ? storedWidth : preferredWidth, false);

  if (!audioAvailable) {
    editorRun.disabled = true;
    editorHint.textContent = "audio unavailable";
  } else {
    editorHint.textContent = (isApple ? "⌘" : "Ctrl+") + "⏎ runs";
  }

  // Docked by default where there is room for both; a slide-over stays shut
  // until asked for, so it never lands on top of the conversation. Absent a
  // stored choice the attribute stays off and the stylesheet decides.
  let pref = null;
  try { pref = localStorage.getItem(OPEN_KEY); } catch (_) { /* private mode */ }
  // A stored "open" only applies where the panel docks - a slide-over must
  // never start on top of the conversation on a phone.
  if (pref === "1" && wideQuery.matches) setEditorOpen(true, false);
  else if (pref === "0") setEditorOpen(false, false);
  else refreshToggle();

  wideQuery.addEventListener("change", refreshToggle);
}

/* ============================= empty state ============================= */

const STARTERS = [
  "What does lpf do?",
  "How does mini-notation work?",
  "Build me a rolling techno groove",
  "Add reverb and a filter sweep to a bassline",
];

function renderStart() {
  const start = document.createElement("div");
  start.className = "start";
  start.innerHTML =
    '<span class="micro">Retrieval-augmented · Strudel docs</span>' +
    '<h1 class="start__title">Ask the docs.<br />Then <em>hear</em> the answer.</h1>' +
    '<p class="start__lede">Every reply is grounded in the Strudel documentation and cites the pages it used. ' +
    "Patterns come back playable, and any of them can go to the editor to be reworked and re-run.</p>" +
    '<div class="chips"></div>';

  const chips = start.querySelector(".chips");
  STARTERS.forEach((text) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = text;
    chip.addEventListener("click", () => ask(text));
    chips.appendChild(chip);
  });

  feed.appendChild(start);
}

/* ================================= hum ================================= */

/* Record a melody, ship it to /api/hum, get a pattern back.
 *
 * The recording is converted to 16 kHz mono WAV in the browser rather than
 * uploaded as-is. MediaRecorder gives you whatever the engine prefers - webm
 * on Chrome, AAC in an .m4a on Safari - and
 * libsndfile on the server reads neither. Web Audio decodes all of them, so
 * decoding here means the server only ever sees one format. It also shrinks
 * the upload by an order of magnitude.
 */

const recBtn = $("rec");
const recLabel = $("recLabel");
const composerHint = $("composerHint");

// The hint line is borrowed to explain the recording while it runs, so the
// keyboard hint has to be captured before it is overwritten.
const HINT_DEFAULT = composerHint.textContent;

const UPLOAD_SR = 16000;   // pYIN's ceiling is ~1 kHz; 16k is ample and small
const MAX_REC_SEC = 30;    // past this the grid fit has more drift than signal

let recorder = null;
let recStream = null;
let recChunks = [];
let recTimer = null;
let recStarted = 0;

const recordingSupported =
  typeof MediaRecorder !== "undefined" &&
  !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);

if (!recordingSupported) recBtn.remove();

function setRecState(state, label) {
  recBtn.dataset.state = state;
  recLabel.textContent = label;
  recBtn.disabled = state === "working";
  sendBtn.disabled = state !== "idle";
}

/* Float samples -> a 16-bit PCM WAV file. Forty-four bytes of header and a
   straight quantisation; no library is worth pulling in for this. */
function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVEfmt ");
  view.setUint32(16, 16, true);          // PCM header size
  view.setUint16(20, 1, true);           // format: PCM
  view.setUint16(22, 1, true);           // channels: mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    // Asymmetric scaling: int16 reaches -32768 but only +32767.
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function blobToWav(blob) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  try {
    const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());

    // OfflineAudioContext does the downmix and the resample in one pass, and
    // its resampler is the engine's own - better than anything hand-rolled.
    const frames = Math.ceil(decoded.duration * UPLOAD_SR);
    const offline = new OfflineAudioContext(1, frames, UPLOAD_SR);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    return encodeWav(rendered.getChannelData(0), UPLOAD_SR);
  } finally {
    ctx.close();
  }
}

async function startRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      // Voice processing is tuned for speech intelligibility and chews up the
      // steady tone a hum is made of, which is exactly what pitch tracking
      // needs. Ask for the raw signal.
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
  } catch (err) {
    addError(
      err && err.name === "NotAllowedError"
        ? "Microphone access was declined. Allow it for this site, then try again."
        : "No microphone available. Check that one is connected and not in use elsewhere."
    );
    return;
  }

  recStream = stream;
  recChunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (ev) => { if (ev.data.size) recChunks.push(ev.data); };
  recorder.onstop = handleRecordingStopped;
  recorder.start();

  recStarted = Date.now();
  setRecState("recording", "0:00");
  composerHint.textContent = "Hum a melody · click again to stop";

  recTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - recStarted) / 1000);
    if (elapsed >= MAX_REC_SEC) { stopRecording(); return; }
    recLabel.textContent = `0:${String(elapsed).padStart(2, "0")}`;
  }, 200);
}

function stopRecording() {
  clearInterval(recTimer);
  recTimer = null;
  if (recorder && recorder.state !== "inactive") recorder.stop();
  // Releasing the tracks is what turns off the browser's recording indicator
  // and, on a phone, hands the microphone back to the OS.
  if (recStream) recStream.getTracks().forEach((track) => track.stop());
  recStream = null;
}

async function handleRecordingStopped() {
  const seconds = (Date.now() - recStarted) / 1000;
  const blob = new Blob(recChunks, { type: recorder.mimeType || "audio/webm" });
  recorder = null;
  recChunks = [];
  composerHint.textContent = HINT_DEFAULT;

  if (seconds < 1 || !blob.size) {
    setRecState("idle", "Hum");
    addError("That was too short to read. Hold the button through a few notes.");
    return;
  }

  setRecState("working", "Reading");
  const start = feed.querySelector(".start");
  if (start) start.remove();
  addUser(`Hummed a melody, ${seconds.toFixed(1)}s`);
  const thinking = addThinking();

  try {
    const wav = await blobToWav(blob);
    const body = new FormData();
    body.append("audio", wav, "hum.wav");

    const res = await fetch("/api/hum", { method: "POST", body });
    const data = await res.json().catch(() => ({}));
    thinking.remove();

    if (!res.ok || data.error) {
      addError(data.error || `The server returned ${res.status}.`, data.trace);
    } else {
      addHumAnswer(data);
      // Only the model's own reply joins the history. The transcription is a
      // measurement, not something the user said, and putting its numbers in
      // the transcript would have follow-ups arguing with the tempo.
      history.push({ role: "user", content: "I hummed a melody for you to turn into a pattern." });
      if (data.answer_md) history.push({ role: "assistant", content: data.answer_md });
    }
  } catch (err) {
    thinking.remove();
    addError("Couldn't send the recording. Is the server still running?");
    console.error(err);
  } finally {
    setRecState("idle", "Hum");
  }
}

function addHumAnswer(data) {
  const el = document.createElement("div");
  el.className = "msg msg--bot";

  const t = data.transcription || {};
  const stats = document.createElement("p");
  stats.className = "micro hum__stats";
  stats.textContent =
    `Transcribed · ${t.bpm} bpm · ${t.key} · ` +
    `${t.note_count} notes · confidence ${t.confidence}`;
  el.appendChild(stats);

  // The literal transcription goes first and always. If the model's version
  // is wrong or the model leg failed outright, this one still plays back what
  // was actually hummed, which is the result the user came for.
  const literal = buildPatch(data.pattern);
  literal.querySelector(".patch__label").textContent = "As hummed";
  el.appendChild(literal);

  if (data.warning) {
    const warn = document.createElement("p");
    warn.className = "error__message";
    warn.textContent = `Transcribed, but the assistant couldn't elaborate: ${data.warning}`;
    el.appendChild(warn);
  }

  if (data.answer_html) {
    const prose = document.createElement("div");
    prose.className = "prose";
    prose.innerHTML = data.answer_html;
    decoratePatches(prose);
    el.appendChild(prose);
  }

  // Said out loud rather than fixed quietly: the pattern below is not quite
  // what the model wrote, and a person editing it should know that.
  if (data.repaired) {
    const fixed = document.createElement("p");
    fixed.className = "micro hum__stats";
    fixed.textContent =
      `Put back the .slow(${(data.transcription || {}).cycles}) the assistant left out, ` +
      "which would have played the melody too fast.";
    el.appendChild(fixed);
  }

  if (data.sources && data.sources.length) el.appendChild(buildSources(data.sources));

  feed.appendChild(el);
  scrollDown();
}

if (recordingSupported) {
  recBtn.addEventListener("click", () => {
    if (recBtn.dataset.state === "recording") stopRecording();
    else if (!inFlight) startRecording();
  });
}

initEditor();
renderStart();
input.focus();

// Arm the layout transitions only once the first frame has been painted with
// the panel already in place, so nothing slides on load. Two frames: one for
// the styles above to land, one for the browser to paint them.
requestAnimationFrame(() => {
  requestAnimationFrame(() => document.body.classList.add("ready"));
});
