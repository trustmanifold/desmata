# Desmata

Desmata is an experimental package manager that addresses code and its
dependencies by **cryptographic hash** instead of by name. It leans on two
existing tools: [Nix](https://nixos.org) for reproducible builds, and
[IPFS](https://ipfs.tech) for content-addressing.

> ### Status: early / pre-alpha
>
> The content-addressing **foundation** works end to end: desmata can build a
> managed dependency (IPFS itself) with Nix, bring its whole dependency closure
> under hash-addressed control, and verify the result — over the internet.
>
> The peer-to-peer distribution and the "call any function by its hash" developer
> experience described under [Aspirations](#aspirations) are **not built yet**.
> This README is careful to say which is which. See
> [What works today](#what-works-today) and [What doesn't work yet](#what-doesnt-work-yet).

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

- **Trusted (you install them):** `nix`, `git`, and desmata itself (plus its
  Python deps). Desmata does **not** distribute these. It assumes you installed
  conforming versions and only *verifies their interfaces* (`dsm check`).
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

### Nucleus and membrane *(aspirational)*

The files in a cell are intended to split into a **nucleus** (stable: `cell.py`,
`flake.nix`, `flake.lock`) and a **membrane** (the part you're encouraged to fork
and republish: config, glue). The idea is that you find a nucleus you trust, pick
a membrane that resembles your use case, and start from something that already
runs rather than a blank slate. *This split is not enforced in code yet.*

## What works today

You need `nix` (with flakes) and `git` installed. Enter the dev environment:

```
nix develop      # or: direnv allow
```

**Check the trusted tools.** Verifies your nix/git satisfy desmata's expectations:

```
$ dsm check
Checking the tools desmata trusts you to provide.
(desmata relies on your installation of these; it does not manage them.)

  [ok  ] nix  version 2.31.2  — builds and pins desmata's managed dependencies
  [ok  ] git  version 2.51.2  — local repository operations

All trusted tools are present and conform. You're ready to bootstrap.
```

**Bootstrap.** Builds the builtin cell (IPFS/kubo) with Nix, internalizes its
whole dependency closure under content-addressed control, and uses IPFS to
content-address a probe — end-to-end proof the managed-dependency path works:

```
$ dsm bootstrap
...
Bootstrapped 'builtins' via internet.
  ipfs dependency   : bilkygayml...-kubo-0.28.0
  builtin cell hash : Qm…              ← its content address
  probe "desmata"   → Qm…              ← produced by the managed ipfs
```

The first run may download from the internet (via Nix); afterwards it's served
from cache. Bootstrapping is idempotent. Run with `--verbose` to watch Nix and
IPFS work.

**Inspect and reset local state.** Each cell keeps its runtime state (for the
builtin cell, the IPFS repo and keys) in a home directory:

```
$ dsm cells
Cells with local state:
  builtins                9.6 KiB

$ dsm clean builtins      # clear one cell's home (works for any cell type)
$ dsm clean --all         # clear every cell's home
```

Everything above runs against the trusted+managed split: `nix`/`git` are checked,
IPFS is built and content-addressed. There are **27 passing tests** covering the
builtin cell, the trusted-tool checks, bootstrap, and cleaning.

## What doesn't work yet

These are real goals with partial or no implementation:

- **Peer-to-peer distribution.** `dsm bootstrap --source peer` exists but raises
  "not implemented"; there is no `publish` / `clone` / `peers` yet. Acquiring a
  cell from a peer when offline is the headline goal and is unbuilt.
- **Calling code by hash.** The `from_hash(...)` / "write Python like Unison"
  developer experience (below) does not work; `desmata/get.py` is a stale stub.
- **Cell packing/hashing.** `pack_cell`, `unpack_cell`, `get_cell_hash`,
  `get_nucleus_hash` are `NotImplementedError` stubs. Today desmata content-
  addresses dependency *paths* (`ipfs add --only-hash`), not whole cells.
- **Nucleus/membrane split** — conceptual; not enforced.
- **Cell metadata database** — stubbed (raises an informative error).
- **`dsm publish` / `dsm clone` / `dsm peers` / `dsm ls`** — not implemented
  (`ls` is a placeholder).

## Aspirations

The end state desmata is working toward.

**Resolve implementations by hash**, the way Unison does — fetching and building
non-Python dependencies transparently:

```python
# NOT YET WORKING — this is the target API
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
a cell's closure can serve it to you when the internet is gone. The reframed goal
for `dsm bootstrap`: use the internet when no peers are available, and use peers
when no internet is available.

**Bidirectional dependency graph.** A traditional library can't see who depends on
it. Desmata records the relationship both ways, so a nucleus author can ask who
actually uses a given function rather than guessing whether a change will break
someone.

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and packaged for
Nix via [uv2nix](https://github.com/pyproject-nix/uv2nix).

```
nix develop                  # dev shell: editable desmata + uv, ruff, pyright
pytest                       # run the test suite
pytest -s test/test_x.py::y  # single test with logs
uv lock                      # after editing pyproject.toml
nix build                    # build the package (a venv)
```

See [CLAUDE.md](./CLAUDE.md) for code-style and structure notes.
