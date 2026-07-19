# What's next — the desmata ⇄ Semantic Paint thesis

**Status:** living plan, written 2026-07-13. This is the answer to
"what's next here." Its twin is SemanticPaint's `docs/ROADMAP.md`
(`~/src/SemanticPaint`), which carries the same plan from SP's vantage —
when priorities move, move them in both. Items are tagged **[desmata]**,
**[SP]**, or **[both]**.

**Background:** [semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md)
(the record projection, and the verification loop closed 2026-07),
[desmata-as-semantic-paint-app.md](./desmata-as-semantic-paint-app.md)
(the process boundary), [lightweight-cells.md](./lightweight-cells.md)
(artifact-pinned wasm cells, WAVE determinism).

## The thesis

Crowd-sourced memoization over a web of trust: every verified-pin
`dsm call` witnesses `evaluates_to(C, F, X, Y)` (`paint.witnessed_call`),
`dsm paint` signs the ledger under the peer key and ships claim + component
bytes to an SP node, and any node can verify the claim mechanically — fetch
C by hash, re-execute F(X) in a zero-capability wasmtime, byte-compare
against Y. Confirmation outranks trust on the SP side; trust is the
fallback that lets a reader accept the memoized answer because peers they
trust already re-executed it.

**Where it stands:** proven whole, in a repeatable artifact — SP's
`scenarios/memoized_call.yaml` (landed 2026-07-17, §1), in SP's full
pytest tier and replayable in the viewer. Ingest-time signature
verification landed on the SP side the same day — the node now checks our
placer signatures at the publish door; our residue is sending our pubkey
with the paint (§2).

---

## 1. LANDED (2026-07-17): the whole-thesis demo scenario [both]

**`dsm paint` wired against real `spd` nodes** — SP's
`scenarios/memoized_call.yaml`. A `paint` scenario action drives the real
`dsm` CLI non-interactively (no desmata changes were needed — the
`--home <dir>` sandbox was the whole isolation story): fresh userspace,
`dsm call` on gnize-cell witnesses `evaluates_to`, `dsm paint` ships blob
+ signed stroke to a live node, which preserves the pre-signed placer.
Then, all asserted in one run: a wasmtime reader confirms over a
zero-weight edge (confirmation outranks trust), an engineless node
accepts on trust of a confirming-and-regrounding relayer, a forged Y is
refuted wherever an engine runs — and believed where only trust decides —
and far readers hold the claim re-grounded under their friend's key,
never holding a reference to the desmata placer at all.

SP consumes desmata via a `git+file` flake input pinned to this repo's
`interface` branch — after committing here, bump SP with
`nix flake update desmata`.

## 2. Soon

- **LANDED (2026-07-18): Session lifecycle + hermetic home for cells
  [desmata]** — the non-deferred half of session cells
  ([session-cells.md](./session-cells.md) §3.1–3.2): a `Cell.session()`
  seam that generalizes `serve.py`'s `running()` (one bring-up per N
  operations, not per call), plus ephemeral-by-default homes with an
  opt-in overlay of declared inputs / snapshot so runtime state is
  *declared*, never ambient (a missing declared input fails loud —
  `CellUnavailable` — not a silent run off left-over state). Landed with
  it: `tally`, a serverful sample cell (a nix-built python kept alive as a
  counter server), and the ipfs builtin's daemon now runs through the same
  seam (`with builtins.session()` yields a live daemon; the `Inherit` home
  policy serves the peer's identity repo like `dsm serve`, `Ephemeral`
  gives an isolated throwaway node). Carries **no** SP dependency — it is
  what packaging SP itself as a (Category-2, distributable-artifact) cell
  needs. The gossip half (`cell-session` runner + predicate colors) stays
  deferred (§5). Next: SP-as-a-cell (its spd daemon reuses this seam).
- **Send our pubkey with the paint [desmata]** — the SP side of
  ingest-time signature verification LANDED 2026-07-17 (SP ROADMAP §2.1):
  a pre-signed stroke is dropped unless its signature verifies against a
  resolvable placer key, and the Publish request grew an optional
  `placer_pubkeys` field (base64 raw ed25519; self-certifying, since the
  placer id is the key's SHA-256). Until `dsm paint` includes its own key
  there, SP's harness pre-introduces it out of band (it mints the
  userspace identity itself and publishes an empty introduction before
  the paint) — one line in `paint`'s publish body deletes that crutch.
  Our `PublishMismatch` round-trip already fails loudly if a node rejects
  the stroke.
- **LANDED (2026-07-17): Forward-compatibility rule [both]** — "ignore
  unknown fields" on the brushstroke wire record and palette JSON is now
  normative (SP `protocol_design.md` §6) and pinned by tests on all
  sides. Every consumer already behaved this way; the work was locking
  it in: SP's pylib codegen now emits an explicit `extra="ignore"`, SP
  tests pin all three targets, and our share landed as
  `test_brushstroke_from_dict_ignores_unknown_fields`
  (`from_dict` pulls named keys only; `canonical_bytes()` stays closed
  over the pinned field list, so extras never perturb content ids or
  signatures). Spec'd corollary: unknown fields are *unsigned* — never
  put anything load-bearing outside the schema. Unknown suite IDs stay
  rejected (crypto agility, not shape).
- **LANDED (2026-07-18): Deterministic fuel budgets for `cell-wasm`
  [both]** — adopted from the homestar review (§6). SP side: resource
  exhaustion (wall-clock or fuel) is now `unavailable`, never `refuted` —
  only a completed mismatch or a trap refutes (SP protocol_design.md
  §2.8, "giving up is not failing") — and the verification facet grew an
  optional `budget` the wasmtime engine renders as `-W fuel=N`. Our
  share: the reference `Invoker` protocol (`invoke.py`) now defines
  `budget: int | None` on `invoke`/`invoke_raw` — the contract's
  metering ceiling, engine-defined accounting, exhaustion is abstention
  — and the wasmtime CLI impl passes it as fuel. Budget is a generous
  ceiling, not part of the compared artifact; a witness minted under one
  engine's accounting stays valid under every other. Whether exhaustion
  should ever harden to `refuted` stays a contract-spec question,
  deferred.
- **LANDED (2026-07-18): Palette schema at the Haxe root [SP]** — the
  palette format's single source of truth is now `haxe/src/api/types/`
  (SP ROADMAP §2.4): the shipped-but-untyped `retention`/`requires`
  keys became Models, the arg-type vocabulary closed as an enumeration
  (`preimage`/`digest` — the sha256 pilot's types — included, so the
  out-of-tree palette kept validating untouched), and the drifted
  §6.1-vs-shipped-JSON docs were reconciled to the root. Notable
  doctrine now spec'd: SP's harness fails closed on unknown arg types
  at load; SP's node stays shape-permissive and abstains at use; and a
  rule carrying an unknown side-condition kind never fires (was
  fail-open). The palette format stays ready for the deferred
  generate-a-client workflow (§5): typed stroke-constructor stubs per
  color, of which a CLI is just one projection — cells already carry
  their half as the WIT world. desmata's share: none required —
  `paint.py`'s hand-built stroke shapes still match; adopting the
  generated schema stays a §5 wake-up.

## 3. Next: the interface palette [both]

Design: [interface-palette.md](./interface-palette.md) (desmata's half);
canonical doc in SP `docs/design/interface_palette.md`. The problem:
cross-cell wiring leans on Python type hints — nothing language-agnostic
tells a consumer how to craft X or whether C₁'s output feeds C₂'s input,
so shapes are learned by runtime failure. The fix is projection, not
invention: every component already carries its WIT world; emit it as
verifiable `type_def`/`exports` brushstrokes (`interface/v1` palette,
`wit-parse` runner on the SP side), and wiring compatibility becomes a
derived Datalog fact over content-addressed type hashes. Typed DataRefs
(add type ref + size to `{hash, suite}`) ride along.

**Why after the demo:** it rides exactly the pipeline §1 proves, and its
acceptance demo — a node discovering *how to call a cell it has never
seen* from gossiped interface facts — is demo v2. That demo's
composition dataflow should crib homestar/IPVM's `await/ok` promise
shape (§6): instructions referencing prior results by content address,
scheduled as a DAG that resumes from the first unmemoized step. Spec
reference, not code.

## 4. Then: stroke dependencies — data moves the way software moves [both]

Design: SP `docs/design/stroke_dependencies.md` (canonical). The cell is
desmata's answer for software dependencies — they travel as one closure;
this extends the same discipline to the data layer. A color declares
which colors it depends on with explicit arg mappings, foreign-key style
("my arg 3 binds that color's arg 5"), so a synapse shipping a stroke
ships the strokes it presupposes; transmission-time rewrites must
commute with the FK maps; a Merkle closure hash over stroke content ids
lets a recipient verify the shipment in one comparison — the same
structural-commitment trick as the cell hash embedding the nucleus
manifest. Mostly [SP] work; desmata's share is the vantage: `dsm paint`'s
`put_data` blob preload is the hand-rolled special case (stroke → blob
edge) that the general closure eventually replaces, and the closure-hash
spec should be defined the way `invoke.py`/`wave.py` defined the
invocation contract. Standing note: SP keeps its store behind an
interface these closure/FK queries fit, in-memory now, database later.

## 5. Deferred, deliberately

| Item | Why deferred | Wake-up condition |
|---|---|---|
| Content-addressed palettes (reference by hash, not name+version) | name+version unambiguous at current scale | palettes evolve incompatibly / second palette author |
| Spec'd deterministic canonical encoding (DAG-CBOR/DRISL-style) replacing the hand-rolled JSON array | twin byte-pinned test vectors (`test_provenance.py` ↔ `canonical.gleam`) do the job; migration breaks every sig and content id | a second independent wire implementation |
| `nix` runner (rebuild-and-compare) | no foundry-class node exists; `builds_to` staying trust-mediated is fine — we deliberately don't ship those artifacts (`paint.py`) | someone stands up a foundry node |
| `cell-session` runner + predicate colors — gossipable claims about *serverful* cells ([session-cells.md](./session-cells.md) §3.3, §6; SP ROADMAP §5) | `cell-session` is `nix`'s sibling: heavy, foundry-only, trust-mediated on pocket nodes. Wants SP §3's runner-name-in-gossip pattern first; the bug-repro travel story wants SP §4's blob co-shipping so an injected home overlay reaches peers by hash. **Distinct from** the session lifecycle + hermetic home ([session-cells.md](./session-cells.md) §3.1–3.2), which is desmata-side, carries no SP dependency, and LANDED 2026-07-18 (`Cell.session()` + `HomePolicy`, the `tally` sample, ipfs daemon over the seam) | SP §3 lands **and** a serverful cell wants a non-trivial claim gossiped (first crowd-verified bug report, or SP-as-a-cell reproducibility) |
| Scenario-playground cells (SP `docs/design/scenario_runner.md`) — SP-as-a-cell (Category-2 session cell, [session-cells.md](./session-cells.md) §4: `spd` in the tally/ipfs shape), palette cells (palette JSON + its verifier-cell closure, by CID — the vehicle; the palette id stays the wire identity), behavior cells (scenario+trace+paths bundle); the runner cell's declared interface kind is `browser` ([interface-palette.md](./interface-palette.md) §4) | the SP-side simulation layer already exists (mobility harness, client-side viewer); desmata's share is packaging, which rides the landed session/hermetic-home seams | SP-as-a-cell begins, or a first outside collaborator wants the playground's fork-and-retry loop |
| Blob co-shipping along dependency closures (replaces `put_data` preload) | `put_data` is the declared MVP stand-in, fine at demo scale | SP interest-driven sync matures |
| iroh content backend (`content.py`) | IPFS covers the thesis path | phase-2 transport work resumes |
| Key rotation (`keys.py`) | single peer key per userspace suffices pre-demo | multi-device / revocation needs |
| Palette-derived clients (typed stroke-constructors per color; CLI/client-lib/OpenAPI-style projections of the SP §2.4 schema) | PoC phase: apps embed stroke construction in author-written functions; the schema work keeps the format generation-ready | an app author outside these repos, or observed palette-authoring friction |
| Version lenses — palette *and cell* compatibility declarations (SP `docs/design/version_lenses.md`) | nothing has two versions yet; palettes/cells are immutable by hash, so evolution means publishing "v2 relates to v1 modulo Δ" as hash-pinned data — the cell half ("this cell is like that one, but different in this way") is desmata's vantage | a palette or cell evolves and both populations must interoperate |
| Agent bench — scored cold-start episodes (SP `docs/design/agent_bench.md`) | usability measurement is post-PoC by decision (2026-07-15) | demo v2 lands, or a first outside user (human or agent) onboards cold |

## 6. Considered and not adopted

**AT Protocol (data model, 2026-07).** Reviewed atproto's data model for
the wire layer. Adopted: typed blob refs, forward-compat rule, and two
deferred rows above. Rejected: the **no-floats rule** (their fix for
float-hash nondeterminism; ours is the engine's canonical WAVE rendering
captured verbatim — already closed) and **multiformat CIDs**
(`dsm:<backend>:<digest>` and `DataRef.suite` are already the
self-describing-hash idea; the cells-as-CIDs vs
components-as-bare-sha256 spelling split can be absorbed by `suite` if
it ever bites).

**homestar / IPVM (2026-07).** Fission's IPVM runtime is the closest
prior art to the whole thesis — content-addressed wasm, receipts keyed
by instruction CID, gossiped over libp2p — and it is the thesis minus
the load-bearing property: receipts are unsigned in practice and stored
on the transport signature alone, never verified by re-execution. Its
executor also breaks the `cell-wasm` contract (WASI linked, so guests
reach clock/entropy and purity stops being statically checkable; silent
int→float coercion; IPLD/DAG-CBOR values instead of canonical WAVE — its
outputs can never byte-compare against a desmata witness), and it is
unmaintained (last real commit 2024-05, wasmtime 18, Fission wound
down). Disposition: conceptual alternative; adopted fuel budgets (§2)
and the `await/ok` dataflow reference (§3); no code. Their
memoization key `{resource, op, input, nonce}` is independently the
`evaluates_to(C, F, X)` shape — convergent evolution in our favor.

Full rationale for both: SP `docs/ROADMAP.md` §6.
