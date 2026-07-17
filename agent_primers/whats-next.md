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
- **Forward-compatibility rule [both]** — spec + implement "ignore
  unknown fields" on the brushstroke wire record and palette JSON
  (atproto's reserved-field lesson). Nearly free now, painful to
  retrofit, prerequisite for palette evolution. desmata's share:
  `provenance.Brushstroke.from_dict` tolerates unknown keys; keep
  `canonical_bytes()` closed over the pinned field list.
- **Deterministic fuel budgets for `cell-wasm` [both]** — adopted from
  the homestar review (§6). SP's engine maps its 60s wall-clock timeout
  to `refuted`, so a slow verifier can refute an honest expensive claim.
  Fix: resource exhaustion → `unavailable`, never `refuted`, and the
  verification facet declares a metering ceiling so metered engines give
  up at a reproducible point instead of a wall-clock one. "Fuel" is
  wasmtime's feature, not a wasm concept — accounting is per-engine
  (browser runners have none and stay on wall-clock), which is safe
  because exhaustion only ever abstains. desmata's share: the reference
  invoker (`invoke.py`) defines how the `cell-wasm` contract passes the
  budget to the engine, the way it defined the invocation itself. Budget
  is a generous ceiling, not part of the compared artifact (even
  wasmtime's accounting varies across versions).
- **Palette schema at the Haxe root [SP]** — palette/color/facet/SyncDef
  become `api` Models so parsers, encoders, and docs generate for every
  target, the arg-type vocabulary closes as an enumeration, and the
  drifted §6.1-vs-shipped-JSON shape is reconciled at the root (SP
  ROADMAP §2.4). This deliberately keeps the palette format ready for
  the deferred generate-a-client workflow (§5): typed stroke-constructor
  stubs per color, of which a CLI is just one projection — cells already
  carry their half as the WIT world. desmata's share: none yet, but
  `paint.py`'s hand-built stroke shapes should track the generated
  schema once it exists.

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
