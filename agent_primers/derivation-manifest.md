# Derivation manifest — a cell advertises which parts are pure functions of others

**Status:** design note. Not scheduled — a deferred row in both roadmaps
(desmata `agent_primers/whats-next.md` §5, SP `docs/ROADMAP.md` §5). Sibling of
[language-neutral-cells.md](./language-neutral-cells.md): both are about *what a
cell advertises about itself*. The verifier half is the deferred `nix` runner;
the trust model is [trustix-interop.md](./trustix-interop.md); the projection
pattern is the interface palette ([interface-palette.md](./interface-palette.md)),
and its general statement is the second design law (SP `docs/design/projection.md`)
— this note projects the flake's derivation graph exactly as §3 projects the WIT.

**Audience:** whoever touches SP's reproducibility palette / `verify/*`,
`cell_archive.py` / `interface.py`, or a `cell.toml`. Read nucleus-membrane.md,
interface-palette.md (the "project a local structure into gossip" move), and
trustix-interop.md (the M-of-N-builders trust model) first.

---

## 1. The idea

A cell's compiled wasm is a pure function of its source (Rust, say) under a
pinned toolchain. So is every *generated* file — SP's `haxe/src/api/` →
generated Gleam/Python/TS is the in-house example, and generated code is a
common feature of codebases generally. A cell should **advertise its internal
derivation DAG** — "output O is a pure function, via tool T, of inputs I" — as
verifiable claims, so that:

> a reviewer audits only the human-readable **sources** plus the **pinned
> tools**, and treats every derived blob (the wasm, the generated code) as
> verifiable-by-re-derivation rather than something to squint at.

**The purpose is trusted-surface reduction.** Today, shipping a wasm blob beside
its Rust source asks a reviewer to either read the blob (infeasible) or *trust*
that it matches the source. A malicious author could ship a blob that does
something the source doesn't. A verifiable derivation edge — confirmed by any
one honest rebuilder — closes that gap: the reviewer no longer has to worry the
bundled wasm is maliciously different from the code that allegedly builds to it.
This is the reproducible-builds / "trusting trust" argument, at cell
granularity, riding the SP trust layer.

## 2. The primitive already exists — coarsely

In SP's `palettes/reproducibility.json`:

```
builds_to(N, A)    "building recipe {N} yields output {A}"    runner: nix, exact-hash
references(O, D)    "output {O} depends on {D} at runtime"     (attest / trust)
```

`builds_to(N, A)` *is* "the artifact A is a pure function of the recipe N" — the
compiled-wasm-from-source edge, one determinism class up from `evaluates_to`.
Its verifier is the deferred **`nix` runner** (rebuild-and-compare, foundry-only,
SP ROADMAP §5). And desmata already captures build provenance in
**Trustix-compatible** shapes (`trustix-interop.md`): the M-of-N-independent-
builders-agree-on-`input → output` model, which is exactly the "reviewers don't
have to trust the blob" property. So SP has the claim + gossip + trust layer,
desmata has the capture, Trustix is the prior art. This note sits on all three.

## 3. What's new

Three things the coarse single edge doesn't give:

1. **Fine-grained DAG, not one edge.** Not just `recipe → artifact`, but the
   cell's whole internal derivation graph — Rust → wasm, `haxe/src/api/` →
   each generated target, and so on. Each edge is an independent
   `builds_to`/`generates_to`-shaped claim.
2. **Advertised, so review shrinks.** The cell *declares* the edges, telling a
   reviewer exactly which derived blobs are re-derivable and how. Without the
   declaration, a reviewer doesn't know what's safe to skip.
3. **Generalized past wasm to all generated code.** SP already has a
   *proto-manifest*: the `_GeneratedFiles.json` files list which files are
   generated. But they only say "these are generated" — no **input edge**, no
   **tool pin** — so they can't say "generated *from what, by what*." That gap
   is the difference between a comment and a verifiable claim.

## 4. The same move as the interface palette

§3 projected the **WIT world** — already embedded in the component bytes — into
gossip as verifiable `type_def`/`exports` strokes. This projects the **Nix
derivation graph** — already embedded in the flake — into gossip as verifiable
`builds_to`/`generates_to` strokes. Both take a structure that is *authoritative
but trapped locally* and make it a crowd-verifiable, gossipable fact. **The flake
is already the "pure function of other parts" declaration; the manifest is its
projection**, exactly as WIT-in-the-bytes was already the contract and §3 just
projected it.

## 5. Shape (sketch, not scheduled)

- **Colors.** Keep `builds_to(N, A)`. Add a `generates_to`-style edge — or fold
  into one general `derives(Output, Tool, InputsClosure)` — where `Output` and
  `InputsClosure` are `content_ref`s and `Tool` is a pinned tool reference. Same
  `nix` runner (or a `codegen` sibling for cheaper generators). Determinism is
  `exact-hash` *over a reproducible derivation*.
- **The input is the whole build closure, not just the source.** "Pure function
  of other parts" must capture the toolchain, flags, and deps, or the claim is
  uncheckable. `flake.lock` already pins that — so an edge's input is *source +
  pinned flake inputs*, and the `nix` runner re-realizes the derivation.
- **Manifest home.** A `[derivations]` section in `cell.toml` (the
  language-neutral-cells manifest), committed under the cell hash. A **Merkle
  closure hash** over the edges (§4's discipline) lets a recipient verify the
  advertised DAG arrived intact, the same structural-commitment trick as the
  cell hash embedding the nucleus manifest.
- **Determinism class.** Reproducible-build, **not** bit-deterministic-sandbox.
  A rebuild mismatch might be malice *or* an embedded timestamp/path — so
  **abstain-not-refute** governs (§2.3, "giving up is not failing"): a foundry
  that can't reproduce **abstains** (`unavailable`); only a *deterministically*
  reproducible build that mismatches **refutes**. Nix is what buys the
  reproducibility, which is why cells being flake-built is the enabler.
  Cross-*architecture* reproducibility is harder still and need not be solved:
  [cross-arch-provisioning.md](./cross-arch-provisioning.md) §4 keys `builds_to`
  by `system`, so per-system reproducibility (the tractable kind) is the
  refutable invariant and a different-arch build is a distinct honest fact, not
  a conflict.

## 6. Verification economics — why it leans on trust, and why that's the point

Runner cost tiers: `wit-parse` (parse) < `evaluates_to` (one sandboxed call) <
`builds_to` (full rebuild). This color family lives in the most expensive tier,
so it will be mostly **trust-mediated with rare foundry confirmation** — and
that is the feature, not a weakness. A pocket node can't rebuild Rust → wasm;
but **one honest foundry rebuild, gossiped and confirmed, protects the whole
network**: everyone else accepts it via trust of confirming-and-re-grounding
peers, and any single honest rebuilder's confirmation outranks the placer's mere
assertion. The entire reason the trust layer exists is to amortize a rare,
expensive confirmation across many cheap readers — and build provenance is the
case where that economics matters most. It is Trustix's M-of-N, riding SP's
gossip.

## 7. How it composes

- **The `nix` runner deferred row is the *verifier* half; this is the *claim +
  manifest* half.** Two halves of one feature — worth uniting when either wakes.
- **§4 stroke dependencies.** The derivation manifest is a closure of these
  edges with a Merkle closure hash committed under the cell hash — §4's exact
  discipline, applied to build/codegen edges instead of data edges.
- **language-neutral-cells.md.** `cell.toml` is the manifest's home, and this
  makes that note's hand-wave concrete: a generated language binding *is* a
  `generates_to` edge (`bindgen(component.wit) → languages/haskell/`), which is
  precisely the "verifiable-by-regeneration honest-bindings check" it promised.
  Same insight from the review angle instead of the trust-stacking angle.
- **trustix-interop.md / shared-nuclei.md.** The M-of-N builder model, and the
  audit/vuln dedup that shares this note's trust-leaning economics.

## 8. The discipline (and how it breaks)

- **Only reproducible derivations are refutable.** A non-reproducible build can
  only ever be trust-mediated — a mismatch is unattributable, so it never
  refutes. Name this: the color's determinism class is "reproducible-build," and
  cells that want strong claims must be reproducibly built.
- **The advertised inputs must be the full closure**, or the claim is a
  decoration nobody can check. The `flake.lock` pin is load-bearing.
- **Don't over-claim the payoff.** This does not make review free; it *moves*
  review from "audit an opaque blob" to "audit human-readable sources and trust
  a pinned, deterministic tool that others re-run." The win is that the opaque,
  un-auditable artifact drops out of the trusted set — not that trust vanishes.

## 9. Wake-up condition

A foundry-class node exists to confirm builds (shares the `nix` runner's
wake-up); **or** a cell ships generated code a reviewer wants to skip auditing —
**SP-as-a-cell**, with its Haxe → Gleam/Python/TS codegen, is the first in-house
case; **or** a supply-chain / security review wants the reduced trusted surface
the derivation manifest buys.
