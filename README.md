# Desmata

Desmata is an experimental package manager that addresses code and its
dependencies by **cryptographic hash** instead of by name. It leans on two
existing tools: [Nix](https://nixos.org) for reproducible builds, and
[IPFS](https://ipfs.tech) for content-addressing.

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
> `from_hash("Qm…")` with automatic discovery, and gossiped build/run
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

The files in a cell split into a **nucleus** (stable, defining: `flake.nix`,
`flake.lock`, `cell.py`) and a **membrane** (the part you're encouraged to fork
and republish: config, glue — everything else in the cell directory). The idea is
that you find a nucleus you trust, pick a membrane that resembles your use case,
and start from something that already runs rather than a blank slate.

This split is **enforced**: a cell's `nucleus_hash` is computed over the nucleus
files only (so forking the membrane doesn't change it — that's how you find
sibling cells), while `cell_hash` covers nucleus + membrane (so siblings are
distinguishable), and a directory missing nucleus files is rejected as not a
cell. See `desmata/cell_archive.py`.

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
  builtin cell hash : Qm…              ← its content address
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

**Resolve a cell by its hash, and run it.** Content-address a cell's nucleus,
fetch it (locally or from a peer that has it), reconstruct it, and run its tool —
the foundation of the "call code by hash" idea:

```python
from desmata.get import publish_cell, from_peer

cid = publish_cell(peer_ipfs, cell_dir)          # peer A: store the cell's nucleus
cell = from_peer(peer_ipfs, my_ipfs, factory, cid, into=…, workdir=…)  # peer B: fetch by hash + build
cell.greet("hello")                              # …and run it
```

**Provenance capture.** Every build records, per store path, a canonical
`{path, narHash, narSize, references, deriver}` — projectable to a Trustix
`KeyValuePair` (and a Semantic Paint brushstroke) for a future trust layer.

**Inspect and reset local state:**

```
$ dsm cells                # cells with local state, by size
$ dsm clean builtins       # clear one cell's home (any cell type)
$ dsm clean --all
```

There are **54 fast tests** (`pytest`) plus the containerized partition e2e
(`pytest e2e`).

## What doesn't work yet

Real goals with partial or no implementation:

- **The friendly collaboration CLI.** `dsm publish` / `dsm clone` / `dsm peers`
  don't exist (`dsm ls` is a placeholder). The Alice/Bob workflow below is doable
  with the building blocks (`publish_cell`, `from_peer`, the ssh/ipfs transport)
  but isn't wrapped into commands.
- **`from_hash("Qm…", interface=…)` as a one-liner.** Resolving a cell *by hash
  alone* works (`from_peer(peer, cid)`), but the polished single-argument form —
  automatic peer discovery (so `--ipfs-path` isn't needed) and an `interface=`
  conformance check — isn't built.
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
non-Python dependencies transparently. The *capability* works today (publish a
cell, `from_peer` it by hash, run its tool — see [What works
today](#what-works-today)); the polished one-liner below — a single hash
argument, automatic discovery, and an interface check — is the remaining ergonomic
target:

```python
# TARGET API — discovery + interface check not built; from_peer(peer, cid) works
from desmata.get import from_hash

adder = from_hash("Qm…", interface=Arithmetic)
assert adder.add(1, 1) == 2
```

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
