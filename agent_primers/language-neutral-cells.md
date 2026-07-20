# Language-neutral cells — demoting Python to just-another-language

**Status:** design note. Not scheduled — a deferred row in both roadmaps
(desmata `agent_primers/whats-next.md` §5, SP `docs/ROADMAP.md` §5). This is
the *at-rest* (repo-layout) cash-out of the interface-palette manifest
([interface-palette.md](./interface-palette.md) §4); the *in-flight* (gossip)
half is the `interface/v1` palette, already landed (§3.1–3.3).

**Audience:** whoever touches `interface.py`, `cell_archive.py`, or a cell
repo's layout. Read [nucleus-membrane.md](./nucleus-membrane.md) first — its
five ideas, especially trust-stacks-on-shared-nuclei (idea 5), are the whole
argument here.

---

## 1. The problem: `cell.py` fuses three roles, one of them accidental

Today a cell repo's root carries `cell.py`, and `interface.py` makes it
load-bearing:

```python
NUCLEUS: tuple[str, ...] = ("flake.nix", "flake.lock", "cell.py")
```

So `cell.py` is doing three jobs at once:

1. **Trust anchor.** Its bytes are hashed into `nucleus_hash`, the hash trust
   stacks on. This is *structural*, not incidental.
2. **Driver / veneer.** Its methods (gnize-cell's `fingerprints`,
   `fingerprints_json`) are, by its own docstring, "optional sugar for the
   desktop dev experience... a browser-tier runner never executes it." The real
   callable surface is `component.wit`.
3. **Closure declaration (the accidental one).** The `Deps`/`Closure` classes
   encode *which flake output to build* and *which artifact to pin*:

   ```python
   class Deps:
       class GnizeWasm(WasmComponent):
           artifact_path = "gnize_wasm.wasm"
           flake_output  = "gnize-wasm"
           built_path    = "lib/gnize_wasm.wasm"
   ```

   This is build metadata trapped in Python, so *fetching or hashing* a cell
   would mean *executing* Python — which the hashing path explicitly refuses
   (`interface.py`: "reading a ClassVar means executing `cell.py`, and computing
   a hash must never run code").

The net effect: **Python is first-class by structure, not by choice.** A Haskell
or Java user can't have a cell without a `cell.py` in its trusted core, and
can't build one without running Python.

## 2. The move: `cell.toml` + `languages/`

Split the three roles across an inert manifest and a bindings folder.

| Concern | Today | Proposed | Nucleus or membrane? |
|---|---|---|---|
| Interface contract | `cell.py` methods + `component.wit` | `cell.toml` manifest → `component.wit` | **nucleus** |
| Closure declaration | `Deps`/`Closure` in `cell.py` | `cell.toml` `[closure]` (flake outputs, artifact pins) | **nucleus** |
| Build recipe | `flake.{nix,lock}` | `flake.{nix,lock}` (unchanged) | **nucleus** |
| Artifact pin | `artifact` file | folded into `cell.toml` | **nucleus** |
| Language binding | `cell.py` (Python only) | `languages/{python,haskell,java,…}/` | **membrane** |

- **`cell.toml`** is the manifest: a list of `(name, kind, kind-specific spec)`
  interface entries (§4's `wit`/`devshell`/`http`/…), plus the closure
  declaration. It is *inert* — parseable and hashable without executing code,
  the property `cell.py` violates. It subsumes today's `nucleus` declaration
  file and the `artifact` file (both are already just pin data).
- **`languages/`** holds bindings. `cell.py` is demoted from *required →
  optional* and *nucleus → membrane*: it becomes `languages/python/`, peer to
  `languages/haskell/`. **A cell with no `cell.py` at all is valid.**

Root markers a human or a tool (in any language) scans for become: `cell.toml`
(is this a cell? what does it offer?) + `flake.{nix,lock}` (how is it built?) +
the specs `cell.toml` names (`component.wit`). No Python anywhere in
discover → verify → call.

## 3. The load-bearing decision: bindings are *membrane*

Everything above is bookkeeping; this is the choice that makes the payoff work.

Put the contract (`cell.toml`, `component.wit`, artifact, flake) in the
**nucleus** and `languages/` in the **membrane**. Then:

> **Adding a language leaves `nucleus_hash` byte-identical.** The cell hash
> changes (it covers nucleus + membrane), but the nucleus does not.

By nucleus-membrane idea 5, every user who trusted the Python-era nucleus
*automatically* shares a nucleus with the Haskell-enriched variant —
`verify_has_nucleus` confirms "same core, unchanged," and trust transfers with
zero re-audit. The Haskell binding is a small, separately-auditable membrane
diff (idea 2) that *cannot* launder anything onto the contract, because it never
touched the nucleus. "x is like y, but now with Haskell bindings" is not a new
mechanism — it is the membrane doing exactly its job.

The contrast makes the stakes clear: if bindings were *nucleus*, every binding
addition would fork the trust community — the Python-only cell and the
+Haskell cell would no longer share a nucleus, and nobody's trust would stack
across them. Membrane placement is what keeps the core fixed while the reach
grows.

## 4. Generated vs authored bindings

A binding has two layers, and separating them buys a verifiability win:

- **Generated layer** — a pure function of the nucleus WIT (`wit-bindgen` output
  for the language). Because it is derivable, a verifier can *check* it: "these
  bindings are the honest `wit-bindgen` output of this `component.wit`" — a
  `builds_to`-style verifiable relationship for bindings, not a trust-me. Raw
  generated bindings arguably need not even be committed (regenerable on
  demand); committing them is a convenience for users without the toolchain.
  This edge is exactly the general case worked out in
  [derivation-manifest.md](./derivation-manifest.md): a generated binding is a
  `generates_to(component.wit, bindgen, languages/<lang>/)` edge, so
  "verifiable-by-regeneration" is not a hand-wave but a concrete claim.
- **Veneer layer** — authored ergonomic sugar no bindgen emits (gnize-cell's
  `fingerprints_json`, a composition helper). This is genuine membrane content,
  audited as a diff.

`cell.py` today is both fused; `languages/python/` would keep them distinct.

## 5. Relation to version lenses — this sorts the cheap case out of the expensive

The deferred version-lenses row (SP `docs/design/version_lenses.md`; the cell
half, "this cell is like that one, but different in this way," is desmata's
vantage) is about publishing "v2 relates to v1 modulo Δ" as hash-pinned data.
Binding additions look like that use case but **don't need it**:

- A binding addition leaves the nucleus unchanged, so "same cell, +Haskell" is
  expressed *natively* by the shared `nucleus_hash`. The shared-nucleus fact
  already says it; no compatibility declaration required.
- Version lenses are for deltas that touch the **nucleus** — the WIT changes,
  the artifact rebuilds, the contract evolves. *That* is when populations must
  interoperate across a real difference.

So the rule: **binding additions are a free membrane operation; contract
changes are what actually needs a lens.**

## 6. The wider-audience payoff is bigger than "authors ship updates"

Because enrichment is a membrane op on an unchanged nucleus, it need not be the
*original author*. Anyone forks, adds `languages/haskell/`, republishes "same
nucleus, +Haskell." The shared-nucleus machinery makes the contribution
discoverable ("who else uses this nucleus?", idea 3) *and* trust-transferable —
without the original author lifting a finger. The wider-audience effect isn't a
maintainer graciously updating; it's a community fanning a cell out across
languages while the trusted core stays fixed and everyone who trusted it keeps
benefiting. Same instinct as [shared-nuclei.md](./shared-nuclei.md): factor the
trusted-and-shared thing out, let people stack on it.

## 7. Two views of one contract

The interface palette already built the second view; this note is the first:

| View | Where | What you read |
|---|---|---|
| **At rest** (browsing a repo) | `cell.toml` at the cell root | interface kinds + `component.wit` |
| **In flight** (a node meeting a cell over gossip) | `type_def` / `exports` strokes (§3.1–3.3) | the same WIT, projected as verifiable brushstrokes |

Both bottom out in *the WIT embedded in the component bytes* as the single
source of truth. `cell.toml` is the manifest; the interface palette is its
gossip projection. A generated cross-language client works identically whether
the cell was found by cloning its repo or by hearing about it from a peer.

## 8. The discipline (and the way it breaks)

**Nucleus answers "what does it do." Membrane answers "how do I call it from
X."** This holds only as long as `cell.toml` + `component.wit` + artifact + flake
carry the *entire* "what is this / what does it promise" load, so that
everything in `languages/` is provably inessential to the contract. If any
behavior-defining decision leaks into a binding, contract has quietly moved into
the membrane and the trust math of §3 breaks — a "binding" would then change
what the cell *does*, not just how it's called, and the unchanged `nucleus_hash`
would be a lie. Bindings must be provably inessential.

## 9. What this would take (sketch, not scheduled)

- **`interface.py`**: `NUCLEUS` becomes `("flake.nix", "flake.lock",
  "cell.toml")` (or `cell.toml` *is* the declaration, listing the rest); the
  `Deps`/`Closure` metadata moves to `cell.toml` parsing so hashing never
  executes a cell.
- **A `cell.toml` schema**: the interface-kind vocabulary is a closed
  enumeration, unknown kind fails closed (same doctrine as runners / arg types,
  SP ROADMAP §2.4).
- **`cell_archive.py` / the loader**: read the closure from `cell.toml` instead
  of importing a `Cell` subclass and reading ClassVars.
- **Migration**: existing cells (gnize / sha256 / nushell / runner) each grow a
  `cell.toml` and move `cell.py` → `languages/python/`; the Python `Cell` class
  stays available as the Python binding, no longer the definition.

**Wake-up condition:** a second language wants first-class cell access, or a
cell author (or forker) wants to publish a cross-language enrichment — i.e. the
first time Python-in-the-nucleus is the thing standing in the way.
