# Primer: desmata is an app on a Semantic Paint node

**Status:** orientation note. **Not scheduled.** Records the architectural
relationship between desmata and Semantic Paint (SP) so future work doesn't
re-confuse them. Pairs with
[semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md), which covers the
record *projection*; this one covers the *process boundary*.

**Audience:** anyone reasoning about where desmata ends and SP begins.

**Source:** SP architecture, `/Users/matt/src/SemanticPaint/ARCHITECTURE.md`
(esp. §1 and §3.6); trust mapping in
`/Users/matt/src/SemanticPaint/protocol_design.md` §2.8, §8.

---

## 1. The relationship in one line

SP is a **substrate** — a per-device **node daemon** (`spd`) that holds identity,
stores brushstrokes, computes trust, and gossips with peers. **desmata is an app
that rides on that node**, exactly like SP's own `sp-cli` browser. desmata is *not*
a layer of SP, and SP is *not* a layer of desmata. They meet at the node's local
API.

It is tempting (and was, in early discussion) to picture SP as an umbrella *over*
desmata. Drop that picture. The umbrella is the node; desmata is one of several
apps under it.

## 2. What desmata does as an app

Two interactions with the node, both already implied by the provenance work:

- **Publish.** desmata projects its captured `Attestation`s to SP brushstrokes
  (`NarInfo.to_brushstrokes()` in `provenance.py`) and hands them to the node:
  `builds_to(recipe, narHash)` and `references(path, dep)` in a `reproducibility`
  palette. The node stores, signs, and gossips them like any brushstroke.
- **Ask.** When desmata holds a build output it has *not* re-executed, it asks the
  node a trust question — "do peers I trust attest this `builds_to`?" — via SP's
  `trust` (Appleseed over the per-palette graph). Re-execution still trumps trust
  for verifiable colors (SP §2.8); trust is the recompute-saving fallback.

desmata keeps doing its own job (Nix builds, IPFS content-addressing, closure
capture, Trustix-shaped provenance). The node adds the trust-graph gossip layer
that phase-2.md explicitly defers — without desmata absorbing it.

## 2a. desmata's second role: the reference verification runner

There is one place desmata is more than an app. SP has *verifiable colors* (SP
§2.8): brushstrokes whose claim is `f(x) = y` and which a recipient can confirm by
re-execution rather than by trust. In the general case `f` is a function addressed
by hash — i.e. **a function in a desmata cell**. So a verifiable palette's colors
are defined partly in terms of desmata cells, and to *actively* confirm such a
brushstroke the SP node has to run the cell. That is desmata's job (`from_hash` /
"call any function by its hash").

This is substrate-adjacent, and it does blur the app boundary — honestly. It is
kept from collapsing it by a seam: SP names a **runner** (the engine) and a **ref**
(the content-addressed computation) in the color, and desmata is the *reference
runner*, not a hardwired dependency (SP `ARCHITECTURE.md` §3.8). The SP node depends
on the runner interface; other runners (Wasm, containers, CWL/WDL — the same
runner-plurality [verifiable-computation.md](./verifiable-computation.md) §3 argues
for) can register. A node with no runner falls back to trust.

No trust regress results: the cell holding `f` is itself a build, so it carries its
own `builds_to` attestations, scored by the same trust graph. desmata-the-runner is
verified by desmata-the-producer's records — the system is self-hosting at this
point, not circular.

So: **two hats.** desmata-as-producer/consumer is an app (§2). desmata-as-runner is
the first implementation of a fundamental SP interface (§2a). Only the second is
"part of" SP, and only as an interface it implements.

## 3. Why the boundary sits here

- **Secret isolation.** The node owns the user's keys and friend-shared seeds in
  one process; desmata never handles them. desmata's own ed25519 peer key *is* the
  SP signing key (and a Trustix `LogSigner`) — one identity, held by the node.
- **Shared trust graph.** Other apps on the same node (the CLI browser, a future
  overlay) see the same edges and trust scores desmata's attestations feed. Trust
  built from builds and trust built from anything else live in one graph, cleanly
  partitioned by palette (SP §8.3): a `reproducibility` palette for "this builds
  reproducibly," a separate palette for "this is good software." The cheap machine
  trust never launders into the expensive human trust.
- **desmata stays runnable alone.** The node is an optional consumer of desmata's
  records, not a dependency of its core. No node → desmata still builds, captures,
  and emits Trustix-shaped provenance. Node present → those same records also gossip
  on the web of trust.

## 4. The near-term obligation (unchanged)

Nothing to build now. Keep `Attestation` general and projectable (the constraint
from [verifiable-computation.md](./verifiable-computation.md) §6 and
[semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md) §4). When an SP
node exists, desmata talks to it through the node's local API as a client — no
in-process coupling, no shared database, just publish-and-ask.
