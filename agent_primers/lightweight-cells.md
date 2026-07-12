# Primer: lightweight cells — artifact-pinned, nix-free at runtime

**Status:** built (2026-07). The design below is implemented through the M3
pilot: the invoker seam (`invoke.py`, wasmtime CLI first), the canonical
WAVE⇄JSON value mapping (`wave.py`), artifact-pinned dependencies with
recipe-vs-pin verification and `builds_to` witnessing (`artifact.py`),
`dsm call <dir-or-hash> <fn> <json-args>`, publish-time pin verification
(`verify_artifacts`), the **gnize-cell** pilot (Test A, `pytest -m wasm`), and
the **runner-cell** browser runner (Test B, `pytest -m browser`:
fetch-by-hash → block-level re-hash → pin check → execute on the browser's
wasm engine; a forged pin is refused). In-browser jco transpilation (M4)
remains stretch. §§ below are kept as the rationale record.

**Audience:** whoever adds a non-nix dependency kind, extends `dsm call`, or
any runner that isn't the Python library. Read
[nucleus-membrane.md](./nucleus-membrane.md) (manifest structure) and
[verifiable-computation.md](./verifiable-computation.md) (runner plurality)
first; this primer instantiates both.

---

## 1. The problem

Desmata cells currently require nix at runtime: `Dependency.build_or_get`
builds the cell's tools from its flake. That is the right foundation for cells
with deep dependencies, but it prices out most devices. A phone in a pocket, a
browser tab, an embedded board — none of them will run nix, and many useful
cells don't need it: a small, pure transformation from dataset A to dataset B
in partition-tolerant, memoizable form should be expressible without carrying a
build system to the party.

## 2. The design: one format, two weight classes

A **lightweight cell** is an ordinary cell that additionally pins a prebuilt
**artifact** — a WebAssembly component — in its nucleus:

```
nucleus   = cell.py, flake.nix, flake.lock          (the recipe, as today)
          + component.wit                            (the typed interface)
          + artifact manifest (pins the .wasm hash)  (via the `nucleus` declaration file)
cell      = nucleus + membrane + the .wasm blob itself (a linked file, travels in the CAR)
```

No new manifest structure is needed: the existing `nucleus` declaration
mechanism ([nucleus-membrane.md](./nucleus-membrane.md)) pulls the WIT and the
artifact pin into the nucleus, so **trust in the nucleus structurally extends
to the exact bytes of the component** — the same `verify_has_nucleus` trick,
one level down. The source stays present (the dev experience of cloning a cell
must not degrade), but a light peer never has to use it.

**Realizing a lightweight dependency is offline-first, like everything else:**

1. blob already local under its pinned hash → done (no nix, no python-exec, no network);
2. blob missing but nix available → build from the flake recipe, **verify the
   output hashes to the pin** — and note that this act mints exactly the
   attestation the trust layer wants: `builds_to(nucleus, artifact)`;
3. neither → `CellUnavailable` (fetch by hash from peers, same as any content).

Step 2 is why the two weight classes are a symbiosis, not alternatives: heavy
peers are the ones who *create* the distributed trust that lets light peers
skip the build. "Sufficient distributed trust in the wasm itself" is not an
assumption — it is a product of heavy peers doing step 2 and gossiping about it.

## 3. The membrane contract moves to WIT

For lightweight cells, the canonical callable surface is the **WIT world**, not
`cell.py`:

* A component is **self-describing**: the WIT is embedded in the binary
  (`wasm-tools component wit x.wasm` extracts it). Function identity is
  `(cell_hash, interface.function)` — the addressable, gossipable form a
  verifiable color needs.
* The Component Model's **canonical ABI** supplies the canonical typed I/O
  encoding that verification requires; we do not invent a serialization
  discipline. Concretely (`wave.py`): values cross every runner boundary as
  WAVE, and the JSON projection of a WAVE value renders **record fields in WIT
  declaration order** — pinned so that two independent runners serialize the
  same result byte-identically, which is what lets a gossiped
  `(cell_hash, function, args, result)` claim be compared for
  conflicting-attestation detection (SP §2.8) without re-parsing.
* The README's `interface=` check becomes a **WIT conformance check** —
  language-neutral, machine-checkable, carried in the artifact.

`cell.py` demotes to *optional sugar*: a typed Python wrapper for the desktop
dev experience. Crucially, invoking a lightweight cell **never executes fetched
Python** — fetch blob, check WIT, invoke in a wasm sandbox (with timeouts and
resource caps at the seam). "I'll run your sandboxed wasm" is a far lower trust
bar than "I'll exec your `cell.py`", which is what makes stranger-to-stranger
cell exchange plausible at all. Lightweight cells are not just lighter; they
are the *safer* weight class, and the natural substrate for `dsm call
<cell-hash> <function>`.

## 4. Pluggable, not bifurcated

The nix question has the same answer as the ipfs question (one library,
backends behind a protocol), because nix is already behind a seam: the factory
calls `Dependency.build_or_get` and it is the *cell's dependency class* that
chooses nix. A lightweight cell ships a `WasmArtifact`-style dependency whose
`build_or_get` follows §2's resolution order, plus an invoker (wasmtime CLI or
wasmtime-py). **There is no desmata-py / desmata-wasm split** — that would
recreate the two-library problem the `ContentBackend` protocol was built to
avoid.

But pluggability inside the Python library answers only half the question,
because a browser tab will never run the Python library however it is
factored. The portable thing is **the cell format and wire protocol, not the
library**:

> one cell format + one wire protocol, **N runners**, with desmata-py as the
> reference runner and the only *authoring* environment.

The spec to guard jealously: manifest shape, hash scheme (`dsm:<backend>:…`),
nucleus/artifact pinning, WIT-as-contract, canonical invocation. That is the
product; Python is the drill press that makes things for people who don't own
one.

**Runner names name the contract, not the implementation.** Where a runner is
named — an SP verification facet, an `Attestation.runner` field — the name
denotes the *invocation contract*, and any engine satisfying it qualifies:

* `cell-wasm` — canonical component invocation on a cell's nucleus-pinned
  artifact (verify pins, WIT conformance, canonical-ABI/WAVE values, sandboxed
  execution). Three implementations of this one name exist:
  desmata-py-over-wasmtime, the runner-cell browser page, and the spd node's
  verify runner (SemanticPaint `spd/verify/verify.gleam` + `spd_wasm_ffi`,
  which re-executes gossiped `evaluates_to` claims).
* `nix` — rebuild the recipe and hold the output against the pin/hash
  (exact-hash by construction). Foundry-only by nature.

This is what gives per-claim capability requirements: a pocket-tier node
confirms `cell-wasm` claims locally and leaves `nix` claims to trust, and the
facet's runner name *is* the requirement.

**Purity is part of the `cell-wasm` contract — enforced, and statically
checkable, not declared.** Wasm is capability-based: a component can only
reach the world through its *imports*, so a component that imports no WASI
interfaces **cannot** touch the filesystem, network, clock, or entropy — the
engine has nothing to hand it. The contract therefore has two halves:

* **Enforcement (the runner's half):** instantiate with *zero* ambient
  capabilities — no fs, no net, no env, no preopens. Both existing
  implementations already comply (`invoke.py`'s wasmtime gets no grants; the
  browser page instantiates bare), plus resource caps at the seam (timeouts
  today; fuel/epoch + memory limits are the same knob). Engines must also pin
  the determinism corners: canonicalize NaNs, no relaxed-simd, so a
  zero-import call is bit-deterministic — which is exactly what `exact-hash`
  verification of a gossiped `(cell_hash, function, args, result)` claim
  assumes.
* **Verification (anyone's half):** the import surface is *in the pinned
  bytes*. `wasm-tools component wit` on the blob — whose hash the nucleus
  pins — shows every import, so "this function is pure" is not a trusted
  assertion but a cheap static check over content-addressed bytes, available
  even to a pocket node that never executes the function. Purity claims
  gossiped as brushstrokes are thus *verifiable colors* with a static (no-
  execution) verification procedure.

A future weight class that legitimately needs I/O (a WASI-http cell, say)
must take a **different contract name** — it is a different trust bar, a
different determinism story, and never a valid runner for a verifiable
color. `cell-wasm` itself stays pure by definition.

## 5. Capability tiers (per device, not per person)

* **Foundry** — desktop/laptop: desmata + nix + git + python. Authors, builds
  from recipe, verifies pins (minting `builds_to` attestations), publishes,
  serves (`dsm serve`), and acts as LAN rendezvous + gateway. Install this
  *now* so it works when the internet doesn't.
* **Runner** — any device with a browser: a JS runner (helia for fetch-by-hash
  — same CIDs and bitswap as kubo — plus `jco` to run components on the
  browser's own wasm engine). Distributed **as a cell**, served by any
  foundry's kubo HTTP gateway: joining the network is typing
  `http://<foundry-ip>:8080/ipfs/<runner-cid>` into a browser. The user-tier
  artifact is a bookmark; a PWA cache upgrades it to "carried with you."
* **Ember** — embedded (esp32-class), someday: a flashed image with a C wasm
  interpreter (WAMR-class). Also fabricated and flashed *from* a foundry. Not
  designed for now; the only obligation is that the invocation spec never
  assumes a big machine.

**Runners are cells.** The JS runner (and any future ember image) is a build
output of a nix cell — hash-addressed, pinned, attestable, fetchable from any
peer, improvable through the same gossip. The foundry fabricates the hand
tools it gives to people without a drill press; no npm, no app store, no
release infrastructure in the disaster path.

**The honest constraint:** browser tabs can't listen, can't mDNS, and WebRTC
needs signaling — so the runner tier is structurally a *client* tier. Every
functioning LAN cluster needs **at least one native peer** (normally a
foundry) as rendezvous, provider, and gateway. Light peers cluster around
heavy peers; a LAN with zero foundries has spectators, not a network. This is
a design fact, not a bug: it is the same "some prepared person anchors the
network" story desmata's partition-tolerance pitch always told.

**What can't be fabricated after the fact:** software can always be re-fetched
from a peer, but **identity and trust state cannot** — keys, peer
fingerprints, first trust edges. The pre-positioning advice for a non-foundry
user is therefore less "install this package" and more "establish your keys
and first few trust edges while it's easy" — which is Semantic Paint's
department (see
[semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md)), and part of
why SP is the first app worth exchanging on a fledgling network.

## 6. Relationship to the other primers

* [verifiable-computation.md](./verifiable-computation.md): the wasm invoker is
  that primer's "runner plurality" made concrete; a lightweight cell's
  `builds_to(nucleus, artifact)` pin-verification is a computation attestation
  of exactly the general shape it mandates.
* [nucleus-membrane.md](./nucleus-membrane.md): the artifact pin rides the
  nucleus-declaration mechanism unchanged; the WIT becomes the "named and
  durable" membrane surface that primer's closing note asks for.
* [desmata-as-semantic-paint-app.md](./desmata-as-semantic-paint-app.md) §2a:
  a wasm-component runner makes `sp-verify` feasible on nodes that have neither
  nix nor python — the browser/phone tier of SP nodes verifies lightweight
  colors locally instead of always falling back to trust.

## 7. The pilot — done

**gnize-cell** (committed): nucleus = gnize's flake recipe + extracted
`component.wit` (with a wit-drift check) + artifact pin; the blob is committed
with a `./repin` script. Test A (`pytest -m wasm`): publish → `from_hash` →
fingerprints through the invoker → piped as JSON into nushell-cell's `math
max` → equal to the Python max and to native `gn --json` built from the exact
SemanticPaint rev the cell's lock pins. Its variant deletes the fetched blob,
re-realizes via the recipe, verifies the pin, and checks the `builds_to`
witness landed in provenance. The prerequisite hermetic `wasmtime --invoke`
flake check lives in SemanticPaint (`gnize-wasm-invoke`) and guards the
invocation path every runner depends on.

**runner-cell** (committed) is the §5 runner tier made real: a heavy cell
whose build output is the light runner (static bundle, servable from any
foundry's kubo gateway). Test B (`pytest -m browser`) covers
fetch-verify-execute and forged-pin refusal. One deliberate deviation from the
§5 sketch: the page verifies blocks *explicitly* (multiformats + dag-cbor/
dag-pb re-hashing against the requested CID) rather than through helia's
verified-fetch — smaller, and the verification is visible rather than
delegated.
