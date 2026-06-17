# Primer: Semantic Paint as desmata's deferred trust layer

**Status:** forward-looking design note. **Not scheduled.** Like
[verifiable-computation.md](./verifiable-computation.md), its only near-term job
is to constrain *one* future decision — the shape of the provenance projection —
so this future stays reachable. Nothing here needs building now; the obligation is
"don't foreclose it," and the current `Attestation` shape already doesn't.

**Audience:** whoever designs desmata's provenance projection / eventual trust
layer. Read alongside [trustix-interop.md](./trustix-interop.md) and
[phase-2.md](./phase-2.md) (the "Deferred" section names the gap this fills).

**Source:** Semantic Paint protocol design,
`/Users/matt/src/SemanticPaint/protocol_design.md` — esp. §2.8 (verifiable colors)
and §8 (trust scoring via Appleseed/TrustNet). Re-check if it has moved on.

---

## 1. The gap this fills

phase-2.md explicitly defers "the gossip / signed Merkle-log / M-of-N consensus
*trust layer* itself," committing only to capturing records that can later
*become* Trustix log entries. **Semantic Paint (SP) is a concrete candidate for
that deferred layer** — a peer-to-peer, partition-tolerant, ed25519-identified
web-of-trust system that gossips signed typed claims ("brushstrokes") and scores
them with a real trust metric (Appleseed, as adapted by Cobleigh's TrustNet).

The two projects already share a worldview: P2P-first, content-addressed,
offline-capable, ed25519 keys, subjective/chosen trust, and *names stay local
while payloads are addressed by hash* (desmata) ≈ *you trust people you know while
content is content-addressed* (SP). SP's own design lists build provenance among
its motivating examples ("if you compile ____ and sha256 the output, you'll get
____"). This is not a graft; it's the same idea from two ends.

## 2. The mapping

| desmata | Semantic Paint |
|---|---|
| `Attestation(inputs, recipe, runner, determinism, outputs, signer)` | a signed **brushstroke** of a verifiable **color** (SP §2.8) |
| `NarInfo(path → narHash, references, deriver)` | `builds_to(drv, output_hash)` / `references(out, dep)` brushstrokes; args are content refs |
| ed25519 peer key (= Trustix `LogSigner`) | ed25519 master identity; **a desmata peer is an SP user** |
| Trustix **M-of-N** over builders you enumerate | Appleseed + a `consensus`/`topN` ranking strategy (SP §8.5) — **SP generalizes it** to transitive, weighted trust, so you needn't know every builder |
| Reproduced / **Unreproduced** / Unknown buckets | **conflicting attestations** (SP §2.8): same `key` args, different `value` args |
| `determinism: exact-hash \| canonicalize-then-hash \| tolerance \| attest-only` | the same taxonomy, on a color's **verification facet** (SP §2.8) |
| recompute = repeatability **vs** human review = validity | **two palettes** (two trust areas; trust transitive only within one, SP §8.3) |

SP co-evolved to meet desmata: it added the verification facet (procedure +
determinism policy) and conflicting-attestation detection *specifically* so that
re-executable build claims are first-class rather than forced through pure trust.
The determinism taxonomy is shared verbatim with verifiable-computation.md §3.

## 3. SP is the richer layer beside Trustix — not a replacement

Keep the Trustix projection. It is a good external-interop target and a
flat-quorum baseline, and `trustix_key()/trustix_value()` already exist. The point
is that **the same captured record can project to more than one trust layer**:

- **Trustix** answers "do my N chosen builders agree?" — a flat vote.
- **Semantic Paint** answers "should *I* trust this build, given who I trust and
  who they trust?" — transitive, weighted, subjective, and able to keep
  repeatability-trust and software-quality-trust in separate palettes so the cheap
  one never masquerades as the expensive one (the cardinal rule of
  verifiable-computation.md §4, enforced structurally by SP).

A desmata that emits both projections interoperates with the Trustix ecosystem
*and* rides SP's web-of-trust gossip — without a second capture.

## 4. The one near-term obligation

Same shape as verifiable-computation.md's: **no code today.** Just keep
`Attestation` projectable to an SP brushstroke the way it is already projectable to
a Trustix `KeyValuePair`. It already is — `runner/recipe/inputs/outputs/determinism`
map onto an SP verifiable color's `palette/procedure/args/determinism`, and the
peer's ed25519 key is the SP signer. When the trust layer is actually built, add an
`Attestation → brushstroke` projection beside `trustix_key/value`:

- color/palette from `runner` + computation kind (e.g. `reproducibility/v1`,
  `builds_to`);
- `key` args = the input identity (drv / store path); `value` args = the output
  hash; both content refs;
- `determinism` copied through to the color's verification facet;
- signed by the peer key.

That is the whole obligation: one more projection of a record desmata already
captures. Cheap to keep open; a painful retrofit if the `Attestation` shape ever
goes nix-shaped (it must stay general — see verifiable-computation.md §6).

## 5. The honest boundary (unchanged, now enforceable)

verifiable-computation.md §4 insists recompute settles *repeatability*, not
*validity*, and the two must never be conflated. SP makes that a structural
property rather than a discipline: emit reproducibility attestations into a
verifiable palette (`exact-hash`/`tolerance`), and any "this is good, safe
software" claim into a separate `attest-only` palette. Trust does not flow between
palettes, so a perfect reproducibility record can never, by construction, launder
itself into a software-quality endorsement.
