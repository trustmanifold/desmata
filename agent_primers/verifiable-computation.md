# Primer: builds are a special case of verifiable computation

**Status:** forward-looking vision note. **Not scheduled.** Its only near-term
job is to constrain **one** Phase 2 decision (the shape of the provenance record)
so this future stays reachable. Nothing here needs building now.

**Audience:** whoever designs desmata's provenance/attestation records (Phase 2,
thread 4) — read alongside [trustix-interop.md](./trustix-interop.md) and
[phase-2.md](./phase-2.md).

---

## 1. The generalization

Desmata's near-term goal is to gossip & verify **build** provenance: a nix
derivation's inputs produced these output hashes. The larger goal is to gossip &
verify **any deterministic computation**. Both are the same shape:

> a **recipe** + content-addressed **inputs** → a deterministic, content-addressed
> **output**, attestable as `(input-identity → output-hash)`, signed, verifiable
> by re-execution or trustable via **M-of-N** consensus.

A nix build is this where the runner is `nix` and the recipe is a `.drv`. Running
BLAST on sequence reads to produce a ChIP-seq signal is the *same shape* where the
runner is a container/process and the recipe is a command + declared inputs. The
trust story is identical: a peer with a high-trust relationship — or without the
equipment/compute right now — accepts an output because N peers they trust attest
it, without recomputing it themselves.

**A build is just a computation whose runner happens to be nix.** Design for the
general case; let nix-build be one instance.

## 2. What generalizes for free

- **Consensus is already protocol-agnostic.** Trustix's log layer treats Key/Value
  as opaque bytes tagged by a `Log.Protocol` string. A `desmata.process` protocol
  coexists with `nix` in the *same* M-of-N machinery — no new trust layer needed.
- **IPFS content-addresses arbitrary bytes**, not only store paths; runtime
  inputs/outputs (reads, signals) already fit, and the dedup work applies.
- The ed25519 peer-key / trust-agility model is unchanged.

## 3. What does NOT generalize — design for it

- **The provenance schema.** Make the internal record a general computation
  attestation `(inputs, recipe, runner, determinism-policy, outputs, signer)`. The
  nix narinfo / Trustix `KeyValuePair` is a **projection** of this for the
  nix-build case — *not* the core shape. This is the one thing to get right in
  Phase 2.
- **The runner.** Nix is one runner (builds). Runtime needs others: containers,
  Wasm, or bioinformatics workflow engines (CWL/WDL/Nextflow/Snakemake). Keep the
  runner abstract and named. (The Wasm runner is now designed in its own right —
  [lightweight-cells.md](./lightweight-cells.md) — as the invoker for cells that
  pin a prebuilt component and run without nix.)
- **Determinism (the hard part).** Nix sandboxes builds to *near*-reproducibility;
  arbitrary scientific tools often are not bit-reproducible (FP divergence across
  CPU/GPU, thread races, RNG, locale, tool drift). So each computation declares a
  **verification policy**: `exact-hash` | `canonicalize-then-hash` (normalize away
  irrelevant ordering/timestamps before hashing) | `tolerance` | `attest-only`.
  Capture enough environment to re-execute, and **record disagreement as data**
  (Trustix's "Unreproduced" bucket generalizes — nondeterminism is information).

## 4. The trust economics (the dream, stated plainly)

Volunteers with spare compute re-run computations and publish agreement, so that
**repeatability is settled before the experts arrive** — leaving expert judgment
for what machines can't check. Recompute is the trust currency.

The honest boundary: recompute-verification settles **repeatability** ("this input
deterministically yields this output"), *not* **validity** ("this is good
science"). Desmata must represent **both** kinds of trust and never let the cheap
one (machine recompute) masquerade as the expensive one (human peer review).

## 5. Prior art to align with (as Trustix is for builds)

- **in-toto / SLSA provenance** — the general "a step with these materials produced
  these products, signed" attestation. The natural *format* for a desmata
  computation attestation (Trustix is build-specific *consensus*; in-toto is the
  general *claim*).
- **IPVM (InterPlanetary Virtual Machine)** — deterministic, content-addressed
  computation over IPLD/Wasm; the closest existing effort to gossipable verified
  compute. Watch it.
- **Scientific provenance** — W3C PROV (model), RO-Crate (packaging), BioCompute
  Object / IEEE 2791 (bioinformatics pipeline provenance — the BLAST/ChIP-seq
  world), CWL/WDL.
- **Unison** (already cited in the README) — content-addressed code; the
  computation-identity-by-hash angle.
- **Semantic Paint** — a web-of-trust gossip layer whose trust metric (Appleseed)
  generalizes Trustix's flat M-of-N to transitive weighted trust, and which keeps
  repeatability-trust and validity-trust in separate "palettes" (§4's boundary,
  enforced structurally). A candidate consumer of these attestations; see
  [semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md).

## 6. The one alteration for now

**No code change today** — the provenance record is unbuilt, and nothing committed
forecloses this. When Phase 2 adds provenance capture, make the internal record a
general computation attestation and emit the Trustix nix `KeyValuePair` as a
projection. Cheap now; a painful refactor later. That is the entire near-term
obligation.
