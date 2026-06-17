# Primer: Adding iroh as a second content backend

**Status:** speculative design note. Desmata targets **IPFS first**. Read this when
it's time to add iroh support. Nothing here is implemented yet.

**Audience:** an agent (or human) picking up the "add iroh" task later. It assumes
familiarity with the current code but re-states the relevant seams so you don't have
to re-derive them.

---

## 1. Why iroh, in one paragraph

iroh (by n0/number-zero, 1.0 released 2026-06-15) is a **connectivity layer**, not an
IPFS clone. You dial a *peer* by its 32-byte Ed25519 public key over authenticated,
end-to-end-encrypted QUIC, with NAT hole-punching and relay fallback. Content
addressing is an *optional* protocol on top (`iroh-blobs`), which identifies content
by the **raw 32-byte BLAKE3 root hash** and transfers it with **BLAKE3 verified
streaming (bao)** — chunk-level integrity checking during download. For desmata this
is attractive for the *not-yet-built* `dsm publish`/`dsm clone` transport (it matches
our "dial a peer by their key" model better than the IPFS DHT does), and gives one
hash function for both hashing and verified transfer.

The catch: iroh-blobs is **incompatible** with IPFS CIDs (BLAKE3 vs SHA-256, flat
"Collections" vs UnixFS Merkle-DAGs — switching changes every hash), and as of 1.0
the official Python FFI bindings cover **only core connectivity**; iroh-blobs/docs/
gossip are explicitly *out of scope* of the stable 1.0 binding surface. (Sourced from a
deep-research pass over n0's primary docs — `docs.iroh.computer`, the `iroh-blobs`
DESIGN.md, and the `iroh-ffi` README; re-run it if these facts need refreshing, since
1.0 is brand new.) The two open questions that gate this whole effort:

- **Q1 (transport):** Is there a *supported* way to call iroh-blobs from Python, or do
  we accept maintaining a thin Rust→uniffi shim exposing the blobs API we need?
- **Q2 (hashing):** Can iroh-blobs compute a deterministic directory-tree hash
  **fully offline** (our `ipfs add -r --only-hash` analog) without running a node?

Resolve both before writing production code. Q2 especially — if iroh can't hash
offline and deterministically, it can't serve use case (a) the way IPFS does today.

---

## 2. The end goal (what "done" means)

A desmata cell is identified by a hash. That hash must be **self-describing**: anyone
who encounters it can tell which backend produced it and therefore how to resolve it.
When evaluating a function in a cell, the resolver:

1. parses the hash → learns the backend (ipfs | iroh) + the raw digest;
2. tries to fetch the content via that backend;
3. on failure, falls back to any *other* backend that can serve the same logical
   content (see fault-tolerance, §5);
4. verifies the fetched bytes against the hash before trusting them.

Backend choice on **publish** is user preference (config). Backend choice on
**resolve** is dictated by the hash itself, with fallbacks for fault tolerance.

---

## 3. Where IPFS is wired in today (the seams to generalize)

These are the exact touch-points. Adding iroh = turning each hard-coded `IPFS` into a
dispatch over a backend abstraction.

| Concern | Current symbol | File |
|---|---|---|
| Hash a path → string | `PathHasher.get_hash(dir) -> str` | `lower_protocols.py:50` |
| IPFS hasher impl | `Tools.IPFS.get_hash` (`ipfs add -r --only-hash`) | `builtins/cell.py:42` |
| IPFS as a built dependency | `Deps.IPFS.build_or_get` (`nix.build("ipfs")`, `ipfs init`) | `builtins/cell.py:54` |
| Hasher selection (hard-coded IPFS) | `DefaultCellFactory.get(...)` picks `DesmataBuiltinTools.IPFS` | `cell_factory.py:289-296` |
| Hash type (bare string!) | `PathHash`, `DependencyHash`, `CellHash`, `NucleusHash` = `NewType(str)` | `lower_protocols.py:38-41` |
| Transport (unbuilt) | `Storage.pack_cell` / `unpack_cell` (stubs) | `higher_protocols.py` + `builtins/cell.py:141` |

**The single most important current limitation:** hashes are **bare strings with no
scheme tag**. `PathHash = NewType("PathHash", str)` carries no information about which
backend produced it. The whole multi-backend story depends on fixing this first.

---

## 4. The central design decision: self-describing hashes

Before any iroh code, introduce a hash value that knows its own backend. Options,
roughly in increasing order of "reinventing IPLD":

1. **desmata-prefixed string** — e.g. `dsm:ipfs:Qm...` / `dsm:iroh:blake3-<hex>`. A
   tiny, explicit, greppable scheme we fully control. Recommended for a first cut —
   matches desmata's "all names are local" ethos and avoids coupling our identifiers
   to multiformats politics.
2. **multibase/multicodec (CIDv1) for both** — encode the BLAKE3 hash as a CIDv1 with
   the BLAKE3 multicodec (0x1e), keep IPFS as its native CID. Pro: interop with the
   wider content-addressing ecosystem; both look like CIDs. Con: iroh's *native* id is
   the raw 32 bytes and its bao chunking differs from kubo's BLAKE3-UnixFS, so a CIDv1
   wrapper is cosmetic — it will **not** be resolvable on the public IPFS network. Risk
   of implying a compatibility we don't have.
3. **a structured `Hash` model** (pydantic) — `Hash(backend: Backend, digest: bytes,
   structure: TreeKind)` with a canonical `str()` for display/storage. Most
   future-proof; lets the type checker enforce "don't hand an iroh hash to the IPFS
   backend." Probably where this ends up.

**Recommendation:** do (3) internally (a `Hash` model) with (1) as its string form.
Reserve (2) as a future-research item if public-IPFS interop ever becomes a goal.

Whatever you pick, `PathHash`/`CellHash`/etc. stop being `NewType(str)` and become this
type, and every `: str` return in the hasher protocol changes with them. Do this change
**while still IPFS-only** — it's the cleanest moment, and it de-risks the iroh add.

---

## 5. Fault tolerance — the part the user cares most about

Goal: a cell stays resolvable even when one backend/peer/network is down. Layers:

- **Self-describing hash picks the primary backend.** No ambiguity about where to look
  first.
- **Content-equivalence across backends.** The hard problem: the *same cell bytes*
  produce a *different* hash under ipfs vs iroh. So a single hash can't natively be
  served by "the other" backend. To get cross-backend fallback you need a **mapping**:
  a small record that says "logical cell X = {ipfs: Qm..., iroh: blake3-...}". Publish
  can populate both and store the mapping (in the local SQLite DB; optionally gossiped
  to peers). Resolve consults it to find an alternate when the primary fails. This is
  the key piece of future research — see §7.
- **Within a backend, multiple sources.** iroh: try direct peer dial → relay fallback
  (built in). IPFS: try local node → gateways → DHT providers. Each backend's adapter
  owns its own internal retry/source list.
- **Verify-then-trust, always.** Whatever bytes come back, recompute the hash with that
  backend's hasher and compare before evaluating any code. This is non-negotiable for a
  package manager and is cheap given both schemes are designed for verified streaming.
- **Degrade loudly.** If only one backend can serve a hash and it's down, surface that
  ("cell X reachable only via iroh peer <key>, currently unreachable") rather than
  failing opaquely.

The most fault-tolerant publish is therefore **publish-to-both** (cost: two hashings +
two stores) recording a cross-backend mapping, so any cell can be fetched whichever way
the network currently permits. Make that a config knob, not a mandate.

---

## 6. Common code vs. special-case code

### Can be common (backend-agnostic)

- **The `Hash` type and its parsing/formatting** (§4). One place that knows all schemes.
- **The resolver / dispatch loop**: parse hash → select adapter → fetch → fallback via
  mapping → verify. Pure orchestration over the backend interface.
- **The backend interface itself.** Generalize today's `PathHasher` into something like:
  ```
  class ContentBackend(Protocol):
      name: Backend
      def hash_path(self, path: Path) -> Hash: ...          # use case (a), offline
      def publish(self, path: Path) -> Hash: ...            # store + return id
      def fetch(self, hash: Hash, into: Path) -> None: ...  # use case (b)
      def can_handle(self, hash: Hash) -> bool: ...
  ```
  `Tools.IPFS` already implements the hashing half of this; it just returns a bare str.
- **The cross-backend mapping store** (SQLite table: logical_id ↔ {backend: hash}).
- **Nix-based dependency provisioning pattern.** `Deps.IPFS.build_or_get` (build the
  tool via Nix, bring it + transitive deps under desmata control) is a *template* an
  `Deps.Iroh` follows almost verbatim — same `nix.build(...)`, `nix.dep_dags(...)`,
  `internalize_ids_hashes(...)` flow, different package name.
- **The `Storage` protocol** (`pack_cell`/`unpack_cell`) — these become thin wrappers
  over `publish`/`fetch` and stay backend-agnostic at the call site.
- **CLI surface** (`dsm publish`, `dsm clone`, `dsm peers`) — backend is an
  implementation detail behind a `--backend`/config flag.

### Must be special-cased (per backend)

- **Hashing internals.** IPFS: `ipfs add -r --only-hash` (SHA-256 UnixFS DAG). iroh:
  BLAKE3 root hash over a Collection/HashSeq — and *only if Q2 resolves* that this can
  be done offline+deterministically. Directory-tree representation differs fundamentally
  (Merkle-DAG vs flat hash-sequence), so "hash a directory" is genuinely different code.
- **Transport.** IPFS: daemon/gateway/DHT, `ipfs init`, bitswap, IPNS keys for naming.
  iroh: QUIC endpoint, NodeId (Ed25519) identity, tickets, relays, iroh-blobs
  request-response. The *peer identity* concepts don't line up: IPNS `k51q...` keys vs
  iroh NodeIds. Peer management (`dsm peers add --pubkey`) needs a backend-tagged key.
- **Integration mechanism.** IPFS: shell out to the `ipfs` CLI (current approach). iroh:
  **no stable CLI guaranteed for blobs**, and blobs isn't in the 1.0 Python FFI. Likely
  a native binding (uniffi `iroh` PyPI wheel for core) + a custom Rust shim for blobs,
  or a third-party crate. This is a different *category* of integration (in-process FFI,
  not subprocess) and will shape error handling, logging (`Loggers.proc` assumes
  subprocess output), and Nix packaging.
- **Lifecycle.** IPFS hashing is stateless-ish (`--only-hash` needs no running node).
  iroh transfer needs a live endpoint/node object held open; hashing may or may not.
  The factory's "get a hasher" step (`cell_factory.py:289`) may need a "get a
  node/endpoint" step with different lifetime.

---

## 7. Avenues for future research

1. **Resolve Q1 and Q2 first** (offline deterministic iroh hashing; supported Python
   path to iroh-blobs). Everything else is moot until these are answered. Prototype:
   one cell, hashed and transferred both ways, hashes stable across machines.
2. **Cross-backend logical identity.** What *is* the stable "logical cell id" that maps
   to per-backend hashes? Candidates: (a) one backend's hash designated canonical with
   others as mirrors; (b) a content-independent random/key-derived id with a signed
   mapping; (c) derive both from a shared lower-level manifest desmata controls. This
   choice drives the whole fault-tolerance story in §5 and interacts with the
   nucleus/membrane split (do nucleus and membrane hash independently per backend?).
3. **Signed mappings & trust.** If a peer claims "ipfs:Qm... == iroh:blake3...", who
   signs that, and how does a resolver decide to believe it? Ties into desmata's
   social-graph/peer-key model. Don't let a malicious mapping redirect a fetch to
   attacker content — the verify-then-trust step (§5) is the backstop, but mapping
   poisoning could still cause denial-of-service.
4. **iroh-docs / iroh-gossip for the peer layer.** iroh-gossip (pub/sub) could carry
   "I published cell X" announcements; iroh-docs (range-based set reconciliation) could
   sync the cross-backend mapping table between peers. Evaluate vs. keeping that purely
   in local SQLite + manual key exchange (the README's current model).
5. **Nix packaging of iroh.** `nix.build("iroh")` for the CLI is easy; building/vendoring
   a custom uniffi Rust shim through poetry2nix/the flake is the real work. Scope it.
6. **Multiformats interop (deferred).** Whether to ever expose iroh content as CIDv1 for
   the broader ecosystem (§4 option 2). Only if a concrete need appears.
7. **Migration / dual-publish ergonomics.** Cost and UX of publish-to-both; whether to
   lazily backfill the second backend on first fault.

---

## 8. Suggested order of work (when the time comes)

1. **(IPFS-only refactor, do early)** Replace bare-string hash `NewType`s with a
   `Hash` model + string form (§4). Generalize `PathHasher` → `ContentBackend` (§6),
   keeping IPFS as the sole impl. Introduce a `Backend` enum and a registry/dispatch
   even with one entry. This is all doable without iroh and de-risks it.
2. Add the cross-backend mapping table to the SQLite schema (unused until backend #2).
3. Resolve Q1/Q2 via a throwaway prototype (§7.1).
4. Implement `Deps.Iroh` (Nix-provisioned) + an iroh `ContentBackend` adapter, hashing
   first (if Q2 allows), transport second.
5. Wire fault-tolerant resolve (parse → dispatch → fallback via mapping → verify).
6. CLI/config: `--backend`, default-backend preference, publish-to-both knob.

Keep IPFS the default until iroh's blobs-from-Python story is proven in-tree.
