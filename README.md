If you're a human, you deserve human written text.  The rest of this README was
written by a LLM.  If you want to discuss this with a human, drop me a line in the
issues, I'd love to chat with you about it.

# Desmata

Desmata is an experimental package manager that addresses code and its
dependencies by **cryptographic hash** instead of by name. When it grows up it
wants to keep [CALM](https://arxiv.org/abs/1901.01930) and replace git.

> ### Status: early / pre-alpha
>
> The core loop works end to end: desmata builds a managed dependency (IPFS
> itself) with Nix, brings its whole dependency closure under hash-addressed
> control, **moves a cell to a peer that has no internet**, and **resolves a cell
> by its hash and runs it**. A containerized test proves the partition-tolerant
> case (peer B, with the internet cut, reconstructs the builtin cell from peer A
> and reproduces its hash).
>
> What's *not* built is mostly the friendly surface and the trust layer: the
> high-level `dsm publish`/`clone`/`peers` workflow, single-argument
> `from_hash("dsm:ipfs:Qm…")` with automatic discovery, and gossiped build/run
> attestations. This README says which is which — see
> [What works today](#what-works-today) and
> [What doesn't work yet](#what-doesnt-work-yet).

## Why

Names are convenient but fragile. We've trusted domain names like `github.com`
and package names like `leftpad` for decades, and when we lean on them hard we
tend to get software that is:

- **insecure** — a heavily-relied-on name is a high-value target for corruption;
- **unreliable** — turning a name into bits can give you *different* bits at
  different times (a server is down, an upstream moved, a release was re-tagged);
- **expensive** — because a name doesn't *consistently* map to the same bits,
  you re-fetch and re-verify things you already had.

Desmata's bet is that we can get much of the way without *globally meaningful*
names: address payloads by hash, and keep all human-readable names **local**.
People are good at trusting other people; they're worse at trusting global
namespaces. Hash-addressing leans on the former.

## Core concepts

### Trusted vs. managed dependencies

Desmata draws a hard line between two kinds of dependency:

- **Trusted (you install them):** `nix`, `git`, `ssh`, and desmata itself (plus
  its Python deps). Desmata does **not** distribute these. It assumes you
  installed conforming versions and only *verifies their interfaces* (`dsm
  check`). `ssh` is here because it's how the *first* managed dependency (IPFS)
  reaches a peer that doesn't have it yet — over `nix copy --from ssh://…` — the
  chicken-and-egg breaker.
- **Managed (desmata handles them):** everything else — starting with IPFS, and
  eventually the dependencies of the cells you use. These are built with Nix,
  content-addressed, and (eventually) moved between peers.

This split is what should make offline/partition-tolerant use possible: the
trusted tools are already on every machine, so only hash-addressed managed
dependencies ever need to travel.

### Cells

A **cell** is desmata's unit of packaging. The builtin cell wraps IPFS; user
cells will wrap whatever code and non-Python dependencies they need. When a cell
is built, desmata resolves its Nix dependency closure and **internalizes** it —
copying/hard-linking each store path into a desmata-controlled directory keyed
both by id and by content hash.

### Nucleus and membrane

The files in a cell split into a **nucleus** (stable, widely-trusted, defining —
the core `flake.nix`/`flake.lock`/`cell.py`, plus anything the author declares
via an optional `nucleus` file) and a **membrane** (the part you're encouraged
to fork and republish: config, glue, small auditable code — everything else in
the cell directory). The idea is that you find a nucleus you trust, pick a
membrane that resembles your use case, and start from something that already
runs rather than a blank slate.

This split is **enforced**: a cell's `nucleus_hash` is computed over the nucleus
files only (so forking the membrane doesn't change it — that's how you find
sibling cells), while `cell_hash` covers nucleus + membrane (so siblings are
distinguishable), and a directory missing nucleus files is rejected as not a
cell. The cell manifest embeds the nucleus manifest as an IPLD link, so a cell
hash **structurally commits** to its nucleus hash — "this cell contains that
nucleus, unchanged" is checkable with one `dag get` (`verify_has_nucleus`),
which is what lets trust in many sibling cells stack onto their shared nucleus.
See `desmata/cell_archive.py` and `agent_primers/nucleus-membrane.md`.

## What works today

You need `nix` (with flakes), `git`, and `ssh` installed. Enter the dev
environment:

```
nix develop      # or: direnv allow
```

**Check the trusted tools.** Verifies your nix/git/ssh satisfy desmata's
expectations:

```
$ dsm check
  [ok  ] nix  version 2.31.2  — builds and pins desmata's managed dependencies
  [ok  ] git  version 2.51.2  — local repository operations
  [ok  ] ssh  version 9.6.0   — moves managed dependencies between peers during bootstrap
```

**Bootstrap over the internet.** Builds the builtin cell (IPFS/kubo) with Nix,
internalizes its whole dependency closure under content-addressed control, and
uses IPFS to content-address a probe — end-to-end proof the managed-dependency
path works:

```
$ dsm bootstrap
Bootstrapped 'builtins' via internet.
  ipfs dependency   : bilkygayml...-kubo-0.28.0
  builtin cell hash : dsm:ipfs:Qm…     ← its content address (self-describing: backend + digest)
  probe "desmata"   → Qm…              ← produced by the managed ipfs
```

Idempotent; first run downloads via Nix, then it's cached. `--verbose` to watch.

**Bootstrap from a peer (no internet).** A node with no internet acquires the
builtin cell from a peer over the trusted tools (`nix copy --from ssh://…`),
constructs it without rebuilding, and reproduces the same probe hash:

```
$ dsm bootstrap --source peer --from ssh://peer@host --ipfs-path /nix/store/…-kubo
```

This is proven end to end in a containerized partition test (`e2e/`): two podman
peers on a network where B can reach A but **not** the internet; B's normal
bootstrap fails offline, then `dsm bootstrap --source peer` reconstructs the cell
from A and `CID_B == CID_A`. Run it with `pytest e2e` (needs podman).

**Inspect a cell's structure.** Four lenses on any managed tool in a cell:

```
$ dsm inspect builtins ipfs nix          # runtime store-path graph
$ dsm inspect builtins ipfs ipfs         # IPFS merkle DAG of blocks (--depth N)
$ dsm inspect builtins ipfs provenance   # Trustix-shaped narinfo per store path
$ dsm inspect builtins ipfs drv          # build-recipe (derivation) graph
```

**A sample user cell.** `greeter` wraps `cowsay` — the first non-builtin cell,
showing what a user cell looks like:

```
$ dsm inspect greeter cowsay nix         # cowsay → bash + perl → … (a real closure)
```

**Resolve a cell by its hash, and run it.** Content-address a whole cell
(nucleus + membrane), fetch it (locally or from a peer that has it),
reconstruct it, and run its tool — the foundation of the "call code by hash"
idea:

```python
from desmata.get import publish_cell, from_hash

hashes = publish_cell(my_ipfs, cell_dir)   # peer A: store + pin the whole cell (`dsm publish`)
                                           # peer A keeps `dsm serve` running; then, given nothing but the hash:
cell = from_hash(my_ipfs, factory, "dsm:ipfs:…", into=…)   # peer B: local content resolves offline;
cell.greet("hello")                                        # anything else is fetched from whichever peer has it
```

Resolution is offline-first: a cell that is already local never needs a daemon.
On a miss, the fetch needs `dsm serve` running (here to fetch, at the publisher
to provide) — discovery goes through the DHT, so B never has to know who A is.
The sneakernet path still works: `from_hash(…, car=bundle)` imports a CAR made
by `pack_cell`, and `from_peer` pulls from an explicitly-referenced peer.

Hashes are **self-describing**: `str(hash)` is `dsm:<backend>:<digest>` (today
always `dsm:ipfs:<cid>`), so anyone who encounters one can tell which backend
resolves it — the seam a second content backend (e.g. iroh) plugs into. See
`desmata/content.py` and `agent_primers/iroh.md`.

**Provenance capture.** Every build records, per store path, a canonical
`{path, narHash, narSize, references, deriver}` — projectable to a Trustix
`KeyValuePair` (and a Semantic Paint brushstroke) for a future trust layer.

**Publish and serve cells to peers:**

```
$ dsm publish path/to/cell   # content-address + pin the cell; prints its dsm:ipfs:… hashes
$ dsm serve                  # run the ipfs daemon: published cells become fetchable by hash,
                             # and non-local hashes become resolvable from peers
```

**Inspect and reset local state:**

```
$ dsm cells                # cells with local state, by size
$ dsm clean builtins       # clear one cell's home (any cell type)
$ dsm clean --all
```

There are **74 fast tests** (`pytest`), an opt-in three-node peer-discovery
test (`pytest -m peernet`, uses the pinned nushell-cell fixture from the dev
shell), plus the containerized partition e2e (`pytest e2e`).

## What doesn't work yet

Real goals with partial or no implementation:

- **The friendly collaboration CLI, completed.** `dsm publish` and `dsm serve`
  exist; `dsm clone` / `dsm peers` don't yet (`dsm ls` is a placeholder). The
  Alice/Bob workflow below is doable with the building blocks but isn't fully
  wrapped into commands.
- **`from_hash("dsm:ipfs:Qm…", interface=…)`'s `interface=` check.** Resolving a
  cell by hash alone — string form, automatic peer discovery via the DHT —
  works; the `interface=` conformance check (typed contract on the fetched
  cell) isn't built.
- **A trust layer.** Desmata *captures* Trustix-shaped provenance, but gossiping
  and reaching M-of-N consensus on build/run attestations is deferred (explored
  separately as Semantic Paint).
- **Cell metadata database** — stubbed (raises an informative error).
- **Bidirectional dependency graph** — recording who depends on a nucleus is not
  built.
- **Loose ends:** the `Hasher`/`Storage` protocol stubs on `DesmataBuiltins` are
  still `NotImplementedError` — the working implementations live as standalone
  functions in `desmata/cell_archive.py` and should be reconciled; and the sample
  cell's flake isn't shipped in the wheel, so the *container* CLI can't inspect it
  (the host can).

## Aspirations

The end state desmata is working toward.

**Resolve implementations by hash**, the way Unison does — fetching and building
non-Python dependencies transparently. The *capability* works today, including
the string-form hash and automatic peer discovery (publish a cell, `dsm serve`,
and a peer `from_hash`-es it knowing only the hash — see [What works
today](#what-works-today)); the `interface=` conformance check is the remaining
ergonomic target:

```python
# WORKS TODAY except interface= (the typed-contract check is not built)
from desmata.get import from_hash

adder = from_hash("dsm:ipfs:Qm…", interface=Arithmetic)
assert adder.add(1, 1) == 2
```

**Lightweight cells.** Not every device can carry nix, and not every cell needs
it. A planned second *weight class* of cell pins a prebuilt WebAssembly
component in its nucleus (recipe still present for dev experience): heavy peers
build from the recipe and attest that it reproduces the pinned artifact; light
peers — a browser tab, a phone — fetch the blob by hash and run it sandboxed,
with the component's WIT world as the typed membrane contract, no nix or python
at runtime. One cell format, N runners; desmata stays the authoring **foundry**
that fabricates and serves the lighter runners themselves. See
[agent_primers/lightweight-cells.md](./agent_primers/lightweight-cells.md).

**Collaborate by moving payloads, not by a shared namespace.** Desmata doesn't
aim to replace `git`; it aims to replace emailing code or passing a thumb drive.
No branches, no merges, no globally meaningful names — Alice publishes a cell and
shares a public key, Bob adds Alice as a peer and clones it by hash, edits, and
publishes back. (If you need branches and merges, use `git`; if a cell is complex
enough to need them, maybe it should be two cells.)

**Partition tolerance.** Because everything is hash-linked, a peer who already has
a cell's closure can serve it to you when the internet is gone. The core of this
is **demonstrated** (`dsm bootstrap --source peer`, proven in the container e2e);
the remaining goal is to make it automatic — `dsm bootstrap` choosing the
internet when no peers are available and peers when no internet is.

**Bidirectional dependency graph.** A traditional library can't see who depends on
it. Desmata records the relationship both ways, so a nucleus author can ask who
actually uses a given function rather than guessing whether a change will break
someone.

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and packaged for
Nix via [uv2nix](https://github.com/pyproject-nix/uv2nix).

```
nix develop                  # dev shell: editable desmata + uv, ruff, pyright
pytest                       # the fast suite (test/)
pytest e2e                   # the containerized partition test (needs podman)
pytest -s test/test_x.py::y  # single test with logs
uv lock                      # after editing pyproject.toml
nix build                    # build the package (a venv)
```

See [CLAUDE.md](./CLAUDE.md) for code-style and structure notes.
