# Primer: lightweight cells — artifact-pinned, nix-free at runtime

**Status:** design note. **Not scheduled.** Records the design for a second
*weight class* of cell and the distribution tiers it implies, so nothing built
in the meantime forecloses it. Nothing here needs building now.

**Audience:** whoever adds a non-nix dependency kind, a `dsm call` command, or
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
  discipline.
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

## 7. The pilot

**gnize-cell**: a sibling of nushell-cell whose nucleus is gnize's rust source
+ WIT + flake recipe + artifact pin, and whose published blob is
`gnize_wasm.wasm` (SemanticPaint already builds it, with a real typed
interface — records and lists, not just strings). It would be the first cell
fetchable by hash and callable on a machine with neither nix nor python, and
the first whose pin-verification a heavy peer can gossip. Prerequisite worth
doing anyway: give gnize-wasm the hermetic `wasmtime --invoke` flake check that
hello-wasm has — that invocation path is what every runner will depend on.
