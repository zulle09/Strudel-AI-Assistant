/* Stub for @kabelsalat/web.
 *
 * The published @kabelsalat/web@0.4.1 that @strudel/core@1.2.6 depends on no
 * longer exports SalatRepl, so importing @strudel/core throws before any of
 * its own code runs. It is a broken published pair, not something we are
 * working around for convenience.
 *
 * SalatRepl is used only by @strudel/core/repl.mjs, which drives live audio.
 * This sandbox queries patterns and never plays anything, so a class that
 * throws if anyone actually tries to use it is the honest stub: it unblocks
 * the import and fails loudly rather than silently if the assumption breaks.
 */
export class SalatRepl {
  constructor() {
    throw new Error("SalatRepl is stubbed: this sandbox evaluates patterns, it does not play audio.");
  }
}
export default { SalatRepl };
