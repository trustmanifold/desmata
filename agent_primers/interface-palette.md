# Primer: the interface palette — desmata's half

**Status:** forward-looking design note, scheduled *after* the
whole-thesis demo scenario ([whats-next.md](./whats-next.md) §1; this is
§3 there). The canonical palette design lives in SemanticPaint,
`docs/design/interface_palette.md` (`~/src/SemanticPaint`) — read that
first; this primer covers only what desmata emits and why.

**Audience:** whoever makes cell interfaces travel the gossip layer.
Read beside [lightweight-cells.md](./lightweight-cells.md) (WAVE
determinism, the invoker seam) and
[semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md) (the
witness → paint pipeline this extends).

---

## 1. The problem, from desmata's side

Using a cell from Python, type hints suggest which outputs pair with
which inputs — but that's a per-language convenience. A consumer holding
only a component hash (which is the whole point of `from_hash`) has no
language-agnostic way to learn how to craft arguments for
`evaluates_to(C, F, X, Y)`-style calls, or whether one cell's output can
feed another's input. Shapes get learned by runtime failure.

## 2. The insight: project, don't invent

Every lightweight cell's component (`artifact.WasmComponent`) already
carries its interface — the WIT world is embedded in the pinned bytes and
mechanically extractable (`wasm-tools component wit`). WIT *is* the
language-agnostic type layer, and WAVE is already its canonical value
syntax (that's why `evaluates_to` verification is a byte comparison —
see `invoke.py`'s raw-result rationale).

So desmata does not need a metadata format. It needs a **witness**: at
the same moments we mint `evaluates_to` (`paint.witnessed_call`) and
`builds_to` (`WasmComponent.build_or_get`), extract the component's WIT
world and mint `interface/v1` strokes:

- `type_def(T, WitText)` — T = sha256 of canonical WIT type text;
- `exports(C, F, ParamsT, ResultT)` — C is the same component sha256 the
  `evaluates_to` claim carries.

These are *verifiable* on the SP side by a `wit-parse` runner (fetch blob
C — already shipped by `ship_blobs` — parse, compare; no execution), so
interface claims get the same confirmation-outranks-trust treatment as
evaluations, and a lying interface collides with an honest one under the
same-key/different-value conflict machinery.

## 3. desmata work items, when this is picked up

1. **Canonical WIT text.** The determinism spec (whitespace, ordering,
   name resolution) is the real design work — pin it the way `wave.py`
   pins WAVE⇄JSON and `invoke.py` pins raw-result capture, with byte
   vectors shared with SP's runner tests. desmata is the reference
   implementation of the contract, per SP's runner philosophy.
2. **Extraction.** A `wit` seam beside the `Invoker` seam — reference
   impl shells `wasm-tools` (pinned in the cell's flake like `wasmtime`
   is); same offline, zero-capability posture.
3. **Witnessing.** `witnessed_call` (and `dsm publish` for cells never
   called locally) mints `type_def`/`exports` strokes into the same
   ledger; `dsm paint` ships them with everything else — no new pipeline.
4. **Typed DataRefs.** When SP extends `DataRef` beyond `{hash, suite}`
   (type ref + size, atproto's blob lesson), `paint.post_put_data` and
   the outbox metadata carry them.
5. **Surface the free win:** confirmed `evaluates_to` strokes are
   verified worked examples of valid inputs for `(C, F)` — the
   memoization cache doubles as usage documentation. Tooling (`dsm`
   query or SP-side view) should present them as such.

## 4. Non-goals

- **No type system in Datalog.** SP rules only ever do hash-equality
  joins over `type_def` refs (`compatible(C1,F1,C2,F2) :-
  exports(C1,F1,_,T), exports(C2,F2,T,_)`). Structural subtyping,
  coercions, generics — WIT tooling's job, on the consumer's machine.
- **No new wire encoding.** Strokes stay bare signed facts; canonical
  bytes stay the pinned JSON array until the deferral in
  [whats-next.md](./whats-next.md) §4 wakes up.
