# Phase 2 — Peer-based dependency sharing with verifiable provenance

**Status:** scope note (not a detailed implementation plan yet — write that when we
start, the way Phase 1 had one). This records what Phase 2 *is*, because its scope
converged across several design discussions and now spans four threads that turn
out to want the same underlying change.

**Audience:** whoever picks up Phase 2. Read the cited primers/files first.

---

## Where Phase 1 left us

Phase 1 proved the crux: a nix closure moves peer→peer by hash, offline, and
reconstructs (CAR round-trip, `test_partition_spike.py`, `transport.py`). It also
surfaced the gaps Phase 2 must close, and the inspect commands + dedup tests
showed *why* the storage model matters.

## The four threads (they converge on one change)

1. **Real peer bootstrap.** Wire `dsm bootstrap --source peer`
   (`bootstrap.py`, the `BootstrapSource.peer` → `NotImplementedError` seam) to
   `transport.py`, so a peer with no internet assembles a cell from another peer.

   **Transport is two-tier** (resolves the chicken-and-egg: you can't receive ipfs
   over ipfs). The *first* managed dependency (ipfs) travels over the **trusted
   tools** — nix's own closure transport, over **ssh** between machines
   (`nix copy --from ssh://…`) or a plain file copy on one
   (`transport.acquire_closure_*`). ssh is therefore a **trusted bootstrap tool**
   (added to `dsm check` alongside nix/git). Everything *after* ipfs rides the
   content-addressed **ipfs** path (CAR/manifest, and — since `dsm serve` and
   `from_hash`'s online fallback landed — live bitswap with DHT discovery for
   *cells*; dependency closures still travel as CAR/manifest) with dedup.
   Provenance is identical either way — same NARs/store paths — so verify-by-rebuild
   is unaffected. The single-machine acquire is tested in `test_bootstrap_peer.py`;
   the ssh/LAN form is the container phase.

2. **Per-store-path IPLD manifest transport (fixes dedup).** Phase 1 ships a
   closure as one opaque NAR blob, which defeats cross-tool dedup (see the KNOWN
   GAP note in `transport.py`). Replace it with per-store-path adds + a small IPLD
   manifest whose links are each path's CID, so a shared dependency is a shared
   sub-DAG. The dedup work (`test_dedup.py`, `dsm inspect … ipfs`) is the proof
   harness for this.

3. **Capture the derivation/build closure, not just runtime.** For tamper-evidence
   (others rebuild & diff), cross-architecture rebuild, and the meteor-grade
   offline goal, capture the `.drv` graph + sources — not only the 4-path runtime
   closure. (Three-closures discussion; the residual trust root is the bootstrap
   seed.)

4. **Trustix-compatible provenance.** Persist, per store path, a canonical
   `{path, narHash, narSize, references(sorted)}` record (= a Trustix nix
   `KeyValuePair`) plus the `deriver`/drv linkage, keyed by store path, signed by
   the peer's ed25519 key. See **[trustix-interop.md](./trustix-interop.md)** —
   that primer is **part of Phase 2's scope**: it specifies the shapes to conform
   to and the capture-now checklist.

**Why they're one change:** all four want desmata to *capture the derivation
closure and address everything by content, recording `(storePath → narHash,
references, deriver)` per path*. Build that capture + the per-path manifest once,
and peer transport, dedup, offline rebuild, and Trustix provenance are all served.
Dedup (already built) is what makes capturing the large build closures tractable.

**Design constraint on thread 4 (don't foreclose the future).** Make the
provenance record a *general computation attestation*
`(inputs, recipe, runner, determinism-policy, outputs, signer)`; emit the Trustix
nix `KeyValuePair` as a **projection** of it, rather than making the core schema
nix-shaped. A nix build is one instance of a verifiable computation; desmata aims
to eventually attest runtime computations (e.g. running BLAST on reads) the same
way. Cheap to get right now, painful to retrofit. See
[verifiable-computation.md](./verifiable-computation.md).

## Deferred (explicitly NOT Phase 2)

The gossip / signed Merkle-log / M-of-N consensus *trust layer* itself
(`trustix-proto`'s log + `Decide`). Phase 2 only ensures our captured records can
*become* valid Trustix log entries and our peer keys can act as Trustix signers —
so that layer (ours or an external Trustix) drops in later without re-importing.
A concrete candidate for that deferred layer — richer than Trustix's flat M-of-N,
because it scores transitive weighted trust — is **Semantic Paint**; see
[semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md). It changes
nothing in Phase 2 except reinforcing the "keep `Attestation` general/projectable"
constraint already imposed by [verifiable-computation.md](./verifiable-computation.md).

## Likely ordering (refine in the real plan)

1. Provenance capture first (cheap, unblocks everything): persist canonical
   narinfo + deriver per internalized path (§5 of trustix-interop.md). Desmata
   already computes these in `NixPathInfo`.
2. Derivation-closure capture (the `.drv` graph + sources).
3. Per-store-path IPLD manifest + transport rework (retire the NAR blob).
4. Wire `--source peer` end-to-end; extend the partition spike to two stores that
   share a dependency and confirm the shared sub-DAG transfers once.
5. (Maybe) the sample non-builtin cell, as the first user of all this.
