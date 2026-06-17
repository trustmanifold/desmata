# Primer: capturing build provenance in Trustix-compatible shapes

**Status:** **part of Phase 2** (see [phase-2.md](./phase-2.md), thread 4) — a
design note + a small set of "capture this now" actions. Desmata is **not** going
to build the peer-to-peer gossip / Merkle-log / consensus layer itself. The goal
is narrower and time-sensitive: when desmata captures a tool, it should record
**enough provenance, in shapes that line up with Trustix**, that a trust layer can
be added later without re-importing everything — and so that Trustix-compatible
tools can interoperate with desmata. Read this before designing how cells persist
dependency metadata, and before deciding runtime- vs build-closure capture.

**Audience:** an agent/human extending desmata's capture/storage model. Assumes
familiarity with the cell/closure code and the dedup work; re-states the relevant
Trustix shapes so you don't have to read its Go.

**Source:** read against `/Users/matt/src/trustix` (nix-community/trustix). Key
files are cited inline; re-check them if Trustix has moved on.

---

## 1. What Trustix is, in one paragraph

Trustix establishes trust in build outputs by **comparing what independent
builders got for the same build input**. Each builder keeps a signed, append-only
**Merkle log** of `input → output` mappings; a user trusts an output via an
**M-of-N vote** across builders they choose. It is *trust-agility*: no central
authority, you pick your quorum. (`README.md`, `trustix-doc/src/intro.md`.) This
is exactly the "publish verifications to each other for distributed consensus"
layer desmata wants to be ready for.

## 2. Trustix has two layers — only the bottom one is nix-specific

**Layer A — the log/consensus core (`packages/trustix-proto`), protocol-agnostic.**
The log stores opaque key/value byte pairs; it does not know what a "nix build"
is. The shapes worth conforming to:

- `KeyValuePair { bytes Key; bytes Value }` — the unit of submission
  (`api/api.proto`).
- `Log { string LogID; string Protocol; LogSigner Signer; ... }` — a log
  declares a **Protocol** string (the semantics of Key/Value) and a signer.
- `LogSigner { KeyType=ed25519; string Public }` — **ed25519** keys identify
  builders. (Desmata's peer keys in the README — `k51q…` — are already ed25519;
  keep that: a desmata peer == a Trustix log signer.)
- `LogLeaf { Key, ValueDigest, LeafDigest }`, `LogHead { LogRoot, TreeSize,
  MapRoot, …, Signature }` — the Merkle log + sparse-merkle-map heads
  (`schema/*.proto`). **We do not build these now**, but our records must be able
  to *become* `KeyValuePair`s losslessly.
- Consensus: `Decide(Key, Protocol) → DecisionResponse{ LogValueDecision{ LogIDs,
  Digest, Confidence, Value }, Mismatches, Misses }` (`rpc/rpc.proto`). I.e. for a
  Key, which Value do logs agree on, how strongly, and who disagrees.

**Layer B — the nix protocol (`packages/trustix-nix`), the part we mirror.**
`createKVPair(storePath)` in `cmd/lib.go` is the whole interop contract:

- **Key = `[]byte(storePath)`** — the full output path `/nix/store/<hash>-name`.
  In input-addressed nix the `<hash>` *is* the build-input identity, so the Key
  literally is "the build input."
- **Value = JSON of `NarInfo{ path, narHash, narSize, references }`** with
  **references sorted** (`schema/narinfo.go`; `sort.Strings` in `lib.go`).
- Submission walks a closure with `nix-store --query --requisites`
  (`cmd/submit-closure.go`) and submits one KV pair per store path — the *same
  closure walk desmata already does*.

There is also `NarInfo.Fingerprint()` = `1;<path>;<narHash>;<narSize>;<refs,csv>`
(`schema/narinfo.go`) — the classic nix-cache signing string. Emitting that +
signing with a peer key would make desmata outputs consumable as a nix binary
cache too (Trustix has a binary-cache-proxy).

## 3. The verification layer is keyed on the **derivation** (this decides runtime vs build capture)

`trustix-nix-r13y` is the "did builders reproduce this, and where do they differ"
service. Its API (`reprod-api/api.proto`) is keyed on **`DrvPath`**:

```
DerivationReproducibility(DrvPath) ->
  per output: { Output, StorePath, OutputHashes: map<outputHash -> [LogIDs]> }
  buckets: Reproduced / Unreproduced / Unknown / Missing  (per store path)
Diff(OutputHash1, OutputHash2) -> HTMLDiff
```

It **parses `.drv` files** (`internal/derivation/derivation.go`, via `go-nix`).
The lesson for desmata: the "diff the dependency tree to find tampering" and
"others can rebuild and check me" properties live at the **derivation** level, not
the runtime-output level. So **capturing only the runtime closure is not enough**
for the trust story — you need the derivation (`.drv`) graph too. This confirms
the build-closure direction from the meteor-backup discussion: the trust layer and
the offline-rebuild goal want the *same* extra data.

## 4. What desmata already has

`desmata/messages.py::NixPathInfo` already parses, per store path:
`path, narHash, narSize, references, deriver, registrationTime, signatures, valid`.

That is a **superset** of Trustix's narinfo Value (`path, narHash, narSize,
references`) **plus the `deriver`** (the link output → recipe). Desmata also
walks the closure (`nix.closure_info` / `closure_paths`) just like
`submit-closure.go`. So the per-path provenance is *already computed* — it is
simply not persisted in a canonical, Trustix-shaped, signed-attestation-ready
form, and the `.drv` graph behind `deriver` is not captured at all.

## 5. Capture-now checklist (cheap, and unblocks the trust layer later)

Do these while building/internalizing a tool; none require the gossip layer:

1. **Persist a canonical narinfo record per store path**, keyed by the store path:
   `{ path, narHash, narSize, references(SORTED) }`. This *is* a Trustix nix
   `KeyValuePair` (Key=path, Value=narinfo). Keep desmata's richer fields
   (`deriver`, etc.) in a separate/extended record — the **trust Value must be
   exactly those four fields** to stay digest-compatible.
2. **Keep nix identities as first-class, alongside IPFS CIDs.** Two addressing
   systems, two jobs: nix `storePath` (input-addressed identity = trust **Key**)
   and `narHash` (content identity = trust **Value** core) are for verification /
   Trustix; IPFS CIDs are for block storage/transfer/dedup. Record both; don't
   collapse one into the other.
3. **Record the `deriver` (drvPath) for every output**, and **capture the
   derivation closure** (the `.drv` files + their input sources), not just the
   runtime closure. This is the bridge to rebuild + input-diff + cross-arch, and
   what `trustix-nix-r13y` is keyed on. (It's also large — but see §7.)
4. **Use ed25519 for peer/signing keys** (desmata already does). A desmata peer's
   future attestation = a Trustix log entry signed by that key.
5. **Canonicalize deterministically.** Sort references; pin field order/encoding
   of the persisted Value. Trustix digests the *marshaled bytes*, so to be
   proof-compatible the bytes must match (or be re-derivable). Don't lose a field
   to a non-deterministic encoder.

## 6. What we deliberately defer (but leave room for)

- The signed append-only **Merkle log** + sparse-merkle **map** + log heads.
- The **Decide / M-of-N consensus** RPC and gossip/replication.
- The r13y **reproducibility index** and HTML diff service.

We are not building these. The only obligation now is that a desmata provenance
record can be emitted as a Trustix `KeyValuePair` (Protocol `"nix"`), and a
desmata peer key can act as a Trustix `LogSigner` — so the day we add a trust
layer, our historical captures are already valid log entries, and external
Trustix logs/builders are already consumable.

## 7. Why this converges with the dedup work

Capturing build/derivation closures is expensive (hundreds of paths, GBs once
realized) — but they overlap enormously across tools (shared go/gcc/glibc/stdenv/
bootstrap). The content-addressed dedup already built is precisely what makes
build-closure capture tractable: the second go-based tool's toolchain is already
stored once. So the three threads — **dedup**, **offline/meteor rebuild**, and
**Trustix-shaped provenance** — all pull toward the same design: capture the
derivation closure, address everything by content, record `(storePath → narHash,
references, deriver)` per path. Build that capture once and all three are served.

## 8. Open questions to resolve when the trust layer starts

- **Input-addressed vs CA-derivations.** Trustix keys on input-addressed
  `storePath`. CA-derivations would give cleaner tamper *localization* (early
  cutoff) but change the Key. Capturing both `storePath` and `narHash` now keeps
  either path open.
- **Exact byte-encoding of the Value.** Decide whether to reproduce Trustix's Go
  `json.Marshal(NarInfo)` byte-for-byte (for shared digests/proofs) or to define
  desmata's canonical encoding and convert at the boundary.
- **Granularity of attestation.** Per store path (Trustix's unit) vs per cell vs
  per derivation-output. Per-store-path matches Trustix and the dedup model; cell-
  level attestations can be a composite over those.
- **deriver ≠ local instantiation.** The `deriver` in narinfo (recorded when a
  path is substituted from a cache, e.g. Hydra's drv) can differ from the .drv
  you get by instantiating the same attr locally — two different drvs yield the
  same output path because an output path depends on its inputs' *resolved
  outputs*, not their drv identities. Observed: kubo's narinfo deriver
  `0whnvyr…` vs local `nix derivation show .#ipfs` root `v6xw5z…`, both →
  `bilkygay…-kubo`. Trustix keys on the **output store path**, which is robust to
  this; but the *derivation graph* we capture is whoever instantiated it, so
  record which (cache vs local) and don't assume the deriver equals the captured
  root drv.
