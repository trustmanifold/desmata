# Primer: Semantic Paint as desmata's deferred trust layer

**Status:** forward-looking design note. **Not scheduled.** Like
[verifiable-computation.md](./verifiable-computation.md), its only near-term job
is to constrain *one* future decision — the shape of the provenance projection —
so this future stays reachable. Nothing here needs building now; the obligation is
"don't foreclose it," and the current `Attestation` shape already doesn't.

For the *process boundary* (desmata is an app on an SP node, not a layer of it),
see [desmata-as-semantic-paint-app.md](./desmata-as-semantic-paint-app.md); this
primer covers the *record projection*.

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

## 4. The one near-term obligation — now met

The `Attestation → brushstroke` projection exists beside `trustix_key/value`:
`provenance.Brushstroke`, whose field set is SP's wire record **verbatim**
(`color, palette, args, placer, created_at, suite, sig, arg_versions` —
SemanticPaint `haxe/src/api/types/Brushstroke.hx`, the schema source behind its
generated Python/Gleam/TS types) and whose `canonical_bytes()` matches SP's
`spd/core/canonical.gleam` byte-for-byte: a compact JSON *array* of the
identity-bearing fields in fixed order, `sig` excluded. A stroke desmata signs
therefore verifies on an SP node unchanged; `test_provenance.py` pins the
canonical-bytes vector.

The mapping, as built:

- color/palette from `runner` + computation kind (`reproducibility/v1`,
  `builds_to` / `references`); `NarInfo.to_brushstrokes()` projects a build,
  and `WasmComponent.build_or_get` mints a `builds_to(nucleus, artifact)`
  witness when a recipe rebuild verifies an artifact pin;
- `key` args = the input identity (drv / store path / nucleus hash); `value`
  args = the output hash. **Which arg is key vs value — and the determinism
  policy — live on the color's verification facet in the palette definition,
  not on the stroke** (SP §2.8 keeps them per-color; the stroke stays a bare
  signed fact);
- signed by the peer key (`Brushstroke.signed(placer_fingerprint, signer)`,
  suite `v1-ed25519-sha256`) — desmata peer key == Trustix LogSigner == SP
  placer key. The peer key is real now (`keys.py`: one Ed25519 keypair per
  userspace, `data/identity/peer.ed25519`; the placer handle is the
  lowercase-hex sha256 of the raw pubkey, byte-for-byte SP's
  `identity.gleam` fingerprint). Witnesses persist *unsigned*; signing
  happens at publish time.

Publish-time is built (`paint.py`, surfaced as `dsm paint <node-url>`):
sign the ledger's witnessed strokes under the peer key and POST them to the
node's `/api/publish`. The node stores a pre-signed stroke **as-is** —
attributed to the desmata placer, not re-signed as the node
(`state.gleam publish`; its gleam tests pin both branches) — and replies
with the content ids it derived from *its* canonical bytes. desmata requires
those ids to equal its own, so every publish round-trips the
canonical-serialization contract; drift raises `PublishMismatch` instead of
storing strokes nobody can verify. Publishing is idempotent (deterministic
Ed25519 + mint-time `created_at` ⇒ byte-identical re-sends ⇒ same set-union
ids).

The verification loop is closed (2026-07): every verified-pin `dsm call`
witnesses an `evaluates_to(C, F, X, Y)` claim (`paint.witnessed_call` — C =
sha256 of the component bytes, F/X the function and WAVE argument list
exactly as the engine received them, Y the engine's canonical WAVE result
verbatim) and stashes the component bytes in a content-addressed outbox;
`dsm paint` ships the outbox to the node's `/api/put_data` (hash
round-tripped, like publish round-trips content ids) before publishing the
strokes. On the SP side the `reproducibility/v1` palette
(SemanticPaint `palettes/reproducibility.json`) declares `evaluates_to`
with a `cell-wasm`/exact-hash facet, and the node's runner
(`spd/verify/verify.gleam` over `spd_wasm_ffi`, wasmtime with zero grants)
re-executes and compares — live e2e: gnize-cell witness → paint →
`/api/verify` returns `confirmed`, with forged results `refuted` and
unshipped components `unavailable` (trust fallback). `builds_to` carries a
`nix`/exact-hash facet no node implements yet, so those claims stay
trust-mediated and their artifacts are deliberately not shipped.

Still open: ingest-time signature verification on the SP side
(`canonical.verify_sig` still has no caller). The `Attestation` shape must
stay general — see verifiable-computation.md §6.

What comes next for the whole integration — the demo scenario wiring
`dsm paint` against a real `spd`, then the interface palette — is
tracked in [whats-next.md](./whats-next.md).

## 5. The honest boundary (unchanged, now enforceable)

verifiable-computation.md §4 insists recompute settles *repeatability*, not
*validity*, and the two must never be conflated. SP makes that a structural
property rather than a discipline: emit reproducibility attestations into a
verifiable palette (`exact-hash`/`tolerance`), and any "this is good, safe
software" claim into a separate `attest-only` palette. Trust does not flow between
palettes, so a perfect reproducibility record can never, by construction, launder
itself into a software-quality endorsement.
