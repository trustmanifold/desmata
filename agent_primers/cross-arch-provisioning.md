# Cross-architecture provisioning — a cell that survives the partition on any machine

**Status:** design note. Not scheduled — a deferred row in both roadmaps
(desmata `agent_primers/whats-next.md` §5, SP `docs/ROADMAP.md` §5). Extends
[derivation-manifest.md](./derivation-manifest.md) — it walks the same DAG —
through the partition-tolerance / disaster-preparedness lens. Read
[lightweight-cells.md](./lightweight-cells.md) (wasm portability + WAVE
determinism), derivation-manifest.md, and [interface-palette.md](./interface-palette.md)
(the projection pattern) first.

**Audience:** whoever touches SP's reproducibility palette / `verify/*`, the
cell build/pack tooling (`dsm`), or `cell.toml`. Assumes the cell/closure model
and the FOD vendoring.

---

## 1. The scenario, and the reframe

Build a cell on an ARM laptop while online; gossip it to a user who is now
offline; they use it — on an **x86** machine. Partition tolerance is resilience;
hermeticity is how you get it. This is what makes desmata a
disaster-preparedness tool, so the arch mismatch has to be answerable.

The reframe that shrinks the problem: **for a wasm cell it is already answered.**
A compiled wasm component is architecture-neutral, and its execution is
**bit-deterministic across architectures** — not by luck but because
`evaluates_to` *requires* it (a verifier on any box must re-execute and
byte-compare, so the zero-capability contract pins canonical NaNs, no
relaxed-simd, etc.). So the offline x86 user runs the *same* `gnize_wasm.wasm`
bytes with an x86 engine and gets identical results. Nothing is rebuilt,
cross-compiled, or fetched. The two existing cells split exactly on this line:
**gnize-cell** (wasm artifact) crosses ARM→x86 for free; **nushell-cell** (its
artifact is "a nix closure this cell's sibling builds") is where architecture
actually bites.

## 2. Where architecture bites

1. **The wasm engine.** wasmtime is a native, arch-specific binary. The x86 user
   needs an x86 wasmtime to *run* any wasm cell. Small, usually already present.
2. **Category-2 cells** whose artifact *is* a native nix closure (nushell-cell).
   No portable form exists; it's x86-or-ARM.
3. **Rebuild-to-verify across arch** — the derivation-manifest trust story,
   performed on a different architecture than the original builder.

Only (2) and (3) are the substance; (1) is a provisioning detail.

## 2a. The floor is nix — and it's a standard, universal substrate

Hermeticity has a bottom: you cannot build from nothing. Desmata **depends on
nix for exactly this reason** — nix is the floor, a standard environment every
node can be assumed to carry. From a nix base you can *realize any derivation for
your own system, given its inputs* — so a user with nix can **bootstrap their way
to wasm-freedom**: build the engine and any native leaves offline, no internet.
Some users run in lightweight mode and never need it; but those who have nix can
bootstrap the rest, and disaster-prep users are precisely the ones who will.
The floor is **nix + the kernel it runs on**: pre-stage that, and the cell is
hermetic above it. (Below it — a bare machine with no nix — is out of scope; that
is stocking the bunker, not shipping a cell.)

## 3. The DAG partitions the cell at the arch frontier

`system` (`x86_64-linux`, `aarch64-darwin`, …) is a coordinate on every nix
derivation; the flake already carries it. Walk the derivation manifest and mark
each node's system-neutrality:

- **Arch-neutral nodes** — the wasm output, generated code, and crucially the
  **fetch layer**: sources, crates, FODs. FODs are arch-neutral *by definition*
  (fixed output hash regardless of builder), so the entire fetch closure vendors
  offline **once** and never needs the network again. These ship as-is.
- **Arch-specific frontier** — native tools, nix-closure artifacts. The DAG names
  exactly these, so a policy (§5) applies only to the native leaves, not the
  whole cell.

Without the DAG you fatten everything or ship nothing and hope; with it the
frontier is machine-readable and provisioning is a graph walk.

## 4. Cross-arch reproducibility is hard — so split brushstrokes per `system`

Don't claim architecture-*universal* reproducibility (the reproducible-builds
frontier). Instead, make `system` part of the build claim's **key**:

```
builds_to(N, System, A)      key: (N, System)   value: A
```

- **Same `(N, System)`, different `A`** → a genuine conflict: within one
  architecture, a recipe is supposed to reproduce, so divergence is malice or
  nondeterminism. This is the *existing* conflicting-attestation machinery
  (same key, different value), refutable wherever the build is same-system
  reproducible — **the tractable kind of reproducibility.**
- **Different `System`, different `A`** → *different keys*, so two honest facts,
  no conflict. An artifact built on atypical hardware is **expected, not
  suspicious.**

So `system` is exactly the metadata that tells a "malicious or accidental build
variant" (same system, diverged) apart from "a build that's just on atypical
hardware" (different system). **Per-system reproducibility becomes the refutable
invariant; cross-system difference is benign by construction** — and we never
have to solve cross-arch bit-reproducibility to keep the trust model sharp.

*Palette implication (SP):* `builds_to` grows from arity 2 to 3 — `(N, System,
A)`, roles `[key, key, value]`. `System` is a short platform string; it rides as
a `preimage` (exact bytes matter) or a new closed `platform` arg type added at
the Haxe root (§2.4 doctrine: a closed vocabulary, unknown value fails closed).
`generates_to` edges carry `system` the same way (arch-neutral codegen tags a
universal/neutral system or omits it). A verifier rebuilds **for its own
system** and holds the result against the `builds_to` claim tagged with that
system.

## 5. Two provisioning modes — the user chooses and supplies the resources

The size/portability dial is a real, user-facing decision, and both ends are
first-class:

- **Game night.** Spotty internet at a friend's house; you want the thing to
  *play*. Ship the **wasm only** — smallest, runs on any machine with an engine,
  no rebuild, no reverify. The common case.
- **End of the world.** You need to bootstrap a technology-enabled civilization
  afterward. Ship the **full multi-arch build closure** — sources + cross-arch
  build-time deps — so any surviving machine, from its nix floor, rebuilds any
  node offline for its own architecture. Heaviest.

The format and tooling support the **spectrum**, not one point: `dsm build
--systems x86_64-linux,aarch64-linux,…` pre-provisions named architectures (fat
membrane, chosen while online); a `pack --closure` mode bundles the open-ended
rebuildable closure; the choice is recorded as a **provisioning policy in
`cell.toml`**. The user brings the disk, bandwidth, and pre-staging their choice
demands — the cell format doesn't force the dial, it exposes it.

## 6. Offline rebuild produces trust — per architecture

When the offline x86 user rebuilds an arch-specific node from the shipped closure
and it matches, they've **confirmed the x86-tagged `builds_to`**. The *first* x86
rebuilder mints the x86 fact that rescues every future x86 user — the same
"whoever first clears the per-observer step publishes the link that rescues
everyone" pattern as fingerprint→hash discovery (app_design_provenance §4). They
can't gossip it while partitioned; it hardens the cell's provenance across
architectures on reconnect. **Resilience and trust-accrual are the same act**,
and the per-system split (§4) means each architecture accrues its own
corroboration independently.

## 7. Honest limits

- **The floor is nix + kernel.** Hermetic above it, pre-stage below it. A machine
  with no nix is outside the cell's promise.
- **Per-system reproducibility still needs same-system determinism** to be
  refutable. Where even that fails, the arch's `builds_to` is trust-only
  (abstain-not-refute, §2.3) — the split contains the damage to one arch, it
  doesn't abolish nondeterminism.
- **The dial has real costs at both ends.** End-of-world mode is heavy and the
  user pays for it in disk and pre-staging; game-night mode can neither reverify
  nor rebuild native leaves. Naming the trade is the honesty; hiding it would
  overpromise "no internet, ever, for anything."

## 8. What it would take (sketch, not scheduled)

- **Palette (SP):** `builds_to` grows a `System` key arg (and `generates_to`
  likewise); a verifier rebuilds for its own system and compares against the
  matching-system claim.
- **`cell.toml`:** a provisioning-policy section — which systems to pre-build
  (fat) vs leave to offline rebuild — beside the derivation manifest.
- **`dsm`:** `build --systems=…` (pre-provision named arches), `pack --closure`
  (end-of-world bundle), and an offline `rebuild-local` that walks the DAG, finds
  the arch frontier for the host system, and realizes it from the shipped closure
  atop the nix floor.
- **Already offline-able:** the FOD/vendoring layer is the arch-neutral fetch
  closure — it travels with the cell and needs no network.

## 9. Wake-up condition

A cross-arch offline deployment is actually attempted (a non-wasm cell reaching a
different-architecture partitioned peer); **or** disaster-prep "end-of-world"
packaging is requested; **or** SP-as-a-cell must run across a foundry of mixed
architecture.
