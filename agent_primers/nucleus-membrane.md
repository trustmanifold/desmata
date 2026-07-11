# Nucleus / membrane — trust stacking on shared nuclei

**Status:** design + first implementation slice (the cell_archive rework described
below). The Semantic Paint colors are forward-looking; the storage/transport
changes are landing now.

**Audience:** whoever touches `cell_archive.py`, `interface.py`, or the future SP
client. Read `desmata-as-semantic-paint-app.md` first for the SP boundary.

---

## The five ideas this design serves

1. **Widely trusted code goes in the nucleus.** Code that is widely reused should
   live under a hash that changes rarely. The nucleus is that hash's scope.
2. **Experimental / per-use-case code goes in the membrane.** Ideally membrane
   code is small enough to audit quickly: reviewing a fork means reading a small
   membrane diff against a nucleus you already trust.
3. **Nucleus siblings should find each other.** Two authors whose different cells
   share a nucleus have something in common. "Who else is using this nucleus?"
   should be answerable from Semantic Paint data.
4. **Fetching a cell by hash delivers nucleus *and* membrane** — even when the
   membrane is minimal or empty. A cell travels whole.
5. **Trust stacks on shared nuclei.** Users trust *cells*, mostly from
   experience. If 100 users trust 50 different cells that all include one nucleus
   unchanged, and they trust each other, they can derive high confidence in that
   nucleus — not because the nucleus is the only thing anyone trusts, but because
   it is *included, unchanged* in everything they trust. Without the split they
   would never notice the opportunity to keep stacking trust on the shared part
   while staying free to modify their membranes.

Idea 5 is the payoff and it constrains the design: the fact connecting a cell to
its nucleus must be **verifiable, not asserted**, or a popular cell could launder
unearned trust onto a nucleus it doesn't actually contain.

## The structural move: the cell manifest links the nucleus manifest

Previously the nucleus manifest and the whole-cell manifest were two flat,
unrelated IPLD objects; `cell_hash` and `nucleus_hash` were independent CIDs and
"this cell contains that nucleus" was something you re-derived by hashing files.

Now the cell manifest **embeds the nucleus manifest as an IPLD link**:

```
nucleus manifest = { "nucleus":  [ {name, blob-link} ... ] }          -> nucleus_hash
cell manifest    = { "cell": { "nucleus": <link to nucleus manifest>,
                               "membrane": [ {name, blob-link} ... ] } } -> cell_hash
```

Consequences, each doing one of the ideas above:

* `cell_hash` **structurally commits** to `nucleus_hash`. "Cell X contains
  nucleus N, unchanged" is readable off the manifest with a single `dag get`
  and verifiable by checking the linked content (`verify_has_nucleus` in
  `cell_archive.py`). This is what makes idea 5's connecting fact cheap and
  unfakeable, and idea 3's sibling query a manifest field lookup.
* `dag export` of the cell manifest pulls the whole DAG — nucleus manifest,
  nucleus blobs, membrane blobs — so one CAR moves the whole cell (idea 4).
  Fetching by nucleus hash remains valid and yields the degenerate
  empty-membrane cell; same code path.
* Nucleus blobs are shared sub-DAGs: fifty sibling cells stored on one peer
  hold one copy of the nucleus (the same dedup argument as phase-2 thread 2).

## Nucleus membership: core + declaration, not a fixed list

The nucleus used to be exactly `("flake.nix", "flake.lock", "cell.py")` — a
hardcoded filename list, so *filenames* decided what was trusted, not authors.
Idea 1 needs the author to choose.

Now: the **core** (`flake.nix`, `flake.lock`, `cell.py`) is always nucleus, and an
optional **`nucleus` declaration file** (plain text, one relative path per line,
`#` comments) adds more. The declaration file is itself part of the nucleus when
present — the boundary is inside the hash, so forks cannot disagree about where
the boundary sits. Declared paths may live in subdirectories. A declared file
that doesn't exist makes the directory an invalid cell.

The declaration is **data, not code**: computing a nucleus hash never executes
anything. (A ClassVar on the Closure was considered and rejected — reading it
means importing `cell.py`, i.e. running code just to hash a directory.)

## Membrane: everything else, recursively, and it travels

`membrane_files` used to collect top-level files only; a membrane with a
subdirectory was silently excluded from `cell_hash`. Now it walks recursively
with deterministic ordering, pruning hidden files/dirs (`.git`, `.envrc`, ...),
`__pycache__`, `*.pyc`, `__init__.py`, and symlinks (`result` from a stray
`nix build` must not enter the hash).

Membrane code is only ever *interpreted by* nucleus code: `load_cell_class`
still loads `cell.py` alone, and `cell.py` decides how to read its membrane
(via `context.cell_dir`). That is the audit story of idea 2 — the trusted
nucleus defines how the membrane is interpreted, so the membrane can stay small
and declarative (a `pipelines.toml`, not arbitrary import-time code).

## What Semantic Paint does with this (forward-looking)

Colors, in the vocabulary of `semantic-paint-trust-layer.md`:

* `has_nucleus(cell_hash, nucleus_hash)` — **verifiable**; the runner is nearly
  trivial (fetch cell manifest, check the link, spot-check content). Emitted by
  `publish_cell` once the SP client exists.
* `maps(cell_hash, method, args, input, output)` — **verifiable** purity claims
  about a cell's typed surface (the nushell-cell exercise); re-execution is the
  verification.
* "I use this cell" — **attest-only**, experiential, in a separate palette.
  Trust never flows between palettes, so machine-checkable inclusion and human
  experience can't launder into each other.

The trust derivation of idea 5 is an SP-side inference (Datalog over
`has_nucleus` + Appleseed over the attesters); desmata's whole job is making the
`has_nucleus` facts checkable for free. Note the third position that already
exists: when pipeline text is passed as *call-time arguments* (as in
nushell-cell's `str_to_str(stages=...)`), the function being asserted about is
`(nucleus_hash, args)` — fully pinned with no membrane involved at all. The
membrane earns its keep when pipelines become *named and durable* rather than
inline.

## Implementation notes (this slice)

* `interface.py`: `NUCLEUS` renamed in meaning to the mandatory core;
  `NUCLEUS_DECLARATION = "nucleus"`.
* `cell_archive.py`: `nucleus_names()` (core + declaration), recursive
  `membrane_files()`, two-part manifest, `pack_cell`/`publish_cell` return
  `CellHashes` (both hashes), `unpack_cell` reconstructs nucleus + membrane and
  dispatches on manifest shape (whole-cell vs nucleus-only bundles),
  `verify_has_nucleus()`.
* `from_hash` / `from_peer`: accept either hash and deliver whatever the bundle
  carries. `from_hash` has since gained a string form (`"dsm:ipfs:…"`) and a
  CAR-optional, offline-first resolution path (local repo, else fetch from
  peers via the `dsm serve` daemon).
* Deliberately untouched: `load_cell_class` (nucleus-only code loading),
  the factory, transport, provenance.

One forward-looking use of the declaration mechanism: a **lightweight cell**
declares its WIT file and a prebuilt-artifact pin into the nucleus, so trust in
the nucleus extends structurally to the exact wasm bytes — see
[lightweight-cells.md](./lightweight-cells.md).
