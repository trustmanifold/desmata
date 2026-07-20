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

## 3. desmata work items — 1–3 LANDED 2026-07-20

1. **Canonical WIT text. LANDED.** The projection landed on the SP side
   first (§3.1, `docs/design/interface_palette.md` §4); `wit.py`'s
   `canonical_signature` is a faithful port and the *reference
   implementation* of the contract in Python, pinned the way `wave.py`
   pins WAVE⇄JSON — with byte vectors **shared** with SP's runner tests
   (`test/fixtures/{gnize,sha256}_wit.json`, run to identical canonical
   texts on both sides; a live `dsm`-path extraction reproduces the
   committed fixture bytes exactly, so hashes agree byte-for-byte).
2. **Extraction. LANDED.** `wit.WitExtractor`, a seam beside `Invoker`;
   the reference impl `WasmToolsCli` shells the cell's pinned `wasm-tools`
   (a new `.#wasm-tools` flake output, sibling to `.#wasmtime`), same
   offline, zero-capability, read-only posture — a parse, never an
   execution. Absent → `WitUnavailable` → abstain (trust fallback).
3. **Witnessing. LANDED** (the `dsm call` path). `witness_interface`
   mints `type_def`/`exports` strokes into the same ledger and stashes the
   component in the outbox; `dsm call` calls it after `witnessed_call`, and
   `dsm paint` ships the strokes with everything else — no new pipeline.
   Residual: a `dsm publish` verb for cells never called locally (the
   function already supports it, no CLI verb yet).
4. **Typed DataRefs. LANDED (2026-07-20).** SP extended `DataRef` beyond
   `{hash, suite}` with `type_ref`/`mime_type`/`size` (atproto's blob
   lesson, SP ROADMAP §3.3). Our share is the consumer's: `post_put_data`
   returns the whole DataRef the node minted (not just its hash), and
   `ship_blobs` cross-checks the node-reported `size` against the bytes we
   shipped — the same round-trip discipline as the hash, and tolerant of an
   older node that omits it (the §2.2 forward-compat posture from the
   reader's side). We do not yet *produce* a typed DataRef: X and Y still
   travel inline as WAVE in the stroke args, and the outbox holds only
   opaque component blobs (`application/wasm`, which the node can't infer
   from bytes anyway). Attaching a `type_ref` to a shipped *value* blob is
   demo-v2 work — it wants a value that travels by content-address rather
   than inline, which is the composition dataflow (§3 close / SP §3.3
   "why after the demo").
5. **Surface the free win:** confirmed `evaluates_to` strokes are
   verified worked examples of valid inputs for `(C, F)` — the
   memoization cache doubles as usage documentation. Tooling (`dsm`
   query or SP-side view) should present them as such. (Unbuilt.)

## 4. Keep the door open: WIT is an interface *kind*, not the interface

When this is picked up, build it so `wit` is one entry in a small closed
vocabulary of **interface kinds**, not the definition of "interface." A
cell should be able to declare a manifest — a list of `(name, kind,
kind-specific spec)` — nucleus-adjacent, so "cell C offers interfaces
I₁..Iₙ" is committed under the cell hash rather than implied by a Python
class. The Python `Cell` class is a *host-side driver*, one binding among
many; the manifest is the contract. Kinds already in sight:

- `wit` — a function surface; spec = the WIT world (this primer's
  subject). Any language reaches it via bindgen; this is the common case.
- `devshell` — the cell's own development environment; spec = a flake
  attr. Already real in practice; declaring it makes it discoverable.
- Session-shaped kinds (`http`, `shell`, `browser`, `chat`) — things you
  bring up and then talk to. These ride `Cell.session()`
  ([session-cells.md](./session-cells.md) §3.1): the session yields a
  handle, the kind says what the handle speaks (an OpenAPI ref, a PTY, a
  URL to open, an agent endpoint). First expected customer: the scenario
  playground (SP `docs/design/scenario_runner.md`), whose runner cell's
  declared interface is `browser`.

The manifest's *at-rest* form (a `cell.toml` at the repo root that replaces
`cell.py` in the nucleus, with per-language bindings demoted to a
`languages/` membrane folder) is worked out in
[language-neutral-cells.md](./language-neutral-cells.md) — the repo-layout
cash-out of this section, deferred until a second language wants first-class
cell access.

Same discipline as runners and arg types: each kind keeps its *native*
spec (WIT stays WIT, OpenAPI stays OpenAPI — no universal IDL), the kind
vocabulary is a closed enumeration per version, and an unknown kind fails
closed (a consumer that doesn't know `browser` doesn't offer that mode).
Projection into gossip is per-kind and *later*: `exports`/`type_def` is
the `wit` kind's projection; other kinds' colors (e.g. an `http` spec
hash, attest-only) can follow the same shape when wanted. Cost now: only
don't hardwire "interface == WIT world" into names, Models, or CLI verbs.

## 5. Non-goals

- **No type system in Datalog.** SP rules only ever do hash-equality
  joins over `type_def` refs (`compatible(C1,F1,C2,F2) :-
  exports(C1,F1,_,T), exports(C2,F2,T,_)`). Structural subtyping,
  coercions, generics — WIT tooling's job, on the consumer's machine.
- **No new wire encoding.** Strokes stay bare signed facts; canonical
  bytes stay the pinned JSON array until the deferral in
  [whats-next.md](./whats-next.md) §5 wakes up.
