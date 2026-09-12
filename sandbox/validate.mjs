/* Evaluate a candidate Strudel pattern and report what it does.
 *
 * This is the observation half of the agent's ReAct loop. The agent writes a
 * pattern, this runs it, and the result tells it whether the thing it wrote
 * is real: does it parse, does it produce events, which sounds does it ask
 * for. Errors come back as text the agent can act on, which is the point -
 * "lpf is not defined" sends it back to the documentation, and a prompt rule
 * never had to anticipate that particular mistake.
 *
 * Nothing here plays audio. Patterns are queried, which is Strudel's own way
 * of asking "what events happen between cycle A and cycle B" and is pure
 * computation.
 *
 * Code arrives on stdin, never argv: it is model output, and argv would put
 * it through shell quoting on the way here.
 */

import { readFileSync } from "node:fs";

/* @strudel/core greets stdout on import ("cannot use window: not in
 * browser?", a banner) and any pattern the agent writes may log too. stdout
 * is this script's result channel, so anything printed into it corrupts the
 * JSON. Everything written while the flag is up is diverted to stderr, where
 * it stays visible for debugging without being parsed. */
const realWrite = process.stdout.write.bind(process.stdout);
let quiet = true;
process.stdout.write = (chunk, ...rest) => {
  if (quiet) return process.stderr.write(chunk, ...rest);
  return realWrite(chunk, ...rest);
};
const emit = (payload) => {
  quiet = false;
  realWrite(JSON.stringify(payload));
};

const CYCLES = 4;          // enough to see a `<a b>` alternation come round
const SAFE_SYNTHS = [
  "triangle", "square", "sawtooth", "sine", "pulse", "supersaw",
  "pluck", "zzfx", "white", "pink", "brown", "crackle",
];

async function main() {
  const code = readFileSync(0, "utf8");
  if (!code.trim()) return { ok: false, error: "No code given." };

  const core = await import("@strudel/core");
  const mini = await import("@strudel/mini");
  const tonal = await import("@strudel/tonal");
  const { evaluate } = await import("@strudel/transpiler");

  // Tempo and sample-loading live in the REPL, not in core, so the names the
  // agent legitimately writes are undefined here. Stubbing them (rather than
  // stripping them from the code) means a typo like `setcmp` still surfaces
  // as an error instead of being silently accepted.
  // `$:` compiles to `.p('$')`, which in the real REPL files each pattern
  // into a bank that the scheduler later stacks. repl.mjs owns that, and
  // repl.mjs constructs the audio engine, so it is reimplemented here: collect
  // the patterns, stack them afterwards. Without this every `$:` in the
  // agent's answer dies on ".p is not a function", and `$:` is precisely what
  // the prompt tells it to write for more than one pattern.
  const collected = [];
  core.Pattern.prototype.p = function () {
    collected.push(this);
    return this;
  };
  core.Pattern.prototype.q = function () {
    return core.silence;
  };

  let cpm = null;
  const replStubs = {
    setcpm: (v) => { cpm = Number(v); },
    setcps: (v) => { cpm = Number(v) * 60; },
    setbpm: (v) => { cpm = Number(v) / 4; },
    samples: async () => {},
    hush: () => {},
    initStrudel: () => {},
    all: () => {},
    each: () => {},
  };
  await core.evalScope(core, mini, tonal, replStubs);

  let result;
  try {
    result = await evaluate(code);
  } catch (err) {
    return { ok: false, stage: "evaluate", error: String(err && err.message || err) };
  }

  // The transpiler returns only the last statement, so a multi-pattern answer
  // would be judged on its drums alone. The collected bank is the whole thing.
  const pattern = collected.length ? core.stack(...collected) : result?.pattern;
  if (!pattern || typeof pattern.queryArc !== "function") {
    return { ok: false, stage: "evaluate", error: "The code ran but produced no pattern." };
  }

  let haps;
  try {
    haps = pattern.queryArc(0, CYCLES);
  } catch (err) {
    return { ok: false, stage: "query", error: String(err && err.message || err) };
  }

  const sounds = new Set();
  const notes = [];
  for (const hap of haps) {
    const v = hap.value || {};
    const sound = v.s ?? v.sound;
    if (sound != null) sounds.add(String(sound));
    if (v.note != null) notes.push(String(v.note));
  }

  // Sound names resolve at audio time, not query time, so an unloaded one
  // cannot fail here - it fails silently in the browser with "sound not
  // found". Naming the suspects is what lets the agent fix it itself.
  const soundList = [...sounds];
  const soundfonts = soundList.filter((s) => s.startsWith("gm_"));

  return {
    ok: true,
    cycles_queried: CYCLES,
    events: haps.length,
    cpm,
    sounds: soundList,
    notes: notes.slice(0, 32),
    distinct_notes: [...new Set(notes)].length,
    warnings: [
      ...(haps.length === 0 ? ["The pattern parses but produces no events: it is silent."] : []),
      ...(soundfonts.length
        ? [`General MIDI soundfonts are not loaded in this app and will fail at playback: ${soundfonts.join(", ")}. Use one of ${SAFE_SYNTHS.slice(0, 6).join(", ")} or a dirt-samples drum name.`]
        : []),
    ],
  };
}

main()
  .then(emit)
  .catch((err) => emit({ ok: false, stage: "sandbox", error: String(err && err.message || err) }))
  .finally(() => { process.exit(0); });
