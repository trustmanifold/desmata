# Primer: shared-dependency nuclei — trust factoring follows content-addressing

**Status:** design note, 2026-07-19. **Not scheduled** — records a direction
and the one cheap thing already done to keep the door open (FOD vendoring as a
lockfile projection). No new mechanism built.

**Audience:** whoever touches `cell_archive.py`'s manifest shape, designs the
SP trust palettes, or wonders whether "keep dependencies up to date" is the
right default. Read [nucleus-membrane.md](./nucleus-membrane.md) first — this
generalizes its idea 5.

---

## 1. The observation

nucleus-membrane.md idea 5: *trust stacks on shared nuclei.* If 100 users
trust 50 different cells that all include one nucleus **unchanged**, they can
derive high confidence in that nucleus — it is *included, unchanged* in
everything they already trust. The connecting fact is verifiable
(`verify_has_nucleus`, one `dag get`), not asserted, so trust can't be
laundered.

Today that stacking works at exactly one granularity: the **whole nucleus**,
shared among **sibling** cells (same nucleus, different membrane). But most
shared code isn't a shared nucleus — it's a shared **dependency**. Two
otherwise-unrelated cells that both vendor, say, `mist 6.0.3` have something
in common worth trusting as a unit, and today nothing lets that trust factor:
the dependency is baked into each cell's own (different) nucleus or artifact,
so it is audited, and attested, **per cell**. N cells vendoring one library =
N independent audit burdens for one set of bytes.

## 2. The move: a cell references multiple content-addressed trust units

Let a cell commit to **more than one** nucleus-shaped unit — in particular, a
shared dependency factored into its own content-addressed unit with an
**identical hash across otherwise-unrelated cells**. Three payoffs, in
increasing order of importance:

1. **Byte dedup** — identical sub-DAGs are already stored once (the "fifty
   sibling cells, one copy of the nucleus" property, nucleus-membrane.md).
   *Caveat the idea's originator already flagged:* aligning a *version* does
   not **guarantee** a smaller total, because IPFS packs bytes into a DAG by
   its own chunking; byte savings are a maybe, not a theorem.
2. **Audit dedup** — the real win. A shared dependency unit is audited **once**
   and every cell that structurally commits to it inherits that scrutiny. One
   fewer distinct version in the world is one fewer thing anyone has to read.
3. **Community version normalization** — with shared units as first-class
   subjects, a community can converge on *which versions it trusts*, and the
   incentive to align (shed your bespoke version, inherit the well-audited
   one) is the reduced audit burden of (2), not just bytes.

## 3. What is already there vs. what is new

Most of the substrate exists — this is a generalization, not an invention:

- **Content-addressed dependencies already exist.** desmata internalizes every
  dependency by hash (`internalize_ids_hashes`, `deps_by_hash`) and captures a
  Trustix-shaped `references(path, dep)` closure (`provenance.py`). A dep is
  *already* a hash; nix store paths and FOD checksums are already the atoms.
- **Byte-level sub-DAG sharing already works** — identical content is stored
  once; `verify_has_nucleus` already relies on it.
- **SP attestation is already hash-agnostic.** `has_nucleus(cell, nucleus)` is
  a verifiable color whose subject is a content hash; nothing about the trust
  layer cares whether that hash is a cell, a nucleus, a wasm blob, or a
  dependency closure. `attest(hash)` + Appleseed over attesters + confirmation-
  outranks-trust is the same machine regardless of subject.

What is **new** is small and structural:

- **Manifest shape.** `cell manifest = { nucleus: <link>, membrane: [...] }`
  becomes `{ nuclei: [<link>...], membrane: [...] }` (or a nucleus that itself
  links sub-nuclei — a trust DAG). `verify_has_nucleus` generalizes to a
  membership check; the sibling query becomes "cells sharing unit U" for any U.
- **Exposing the dep closure as referenceable units** in that DAG, so another
  cell can link the *same* hash (the thing that makes (2) and (3) bite).
- **An SP palette** whose subject is a dependency hash: `has_dependency(cell,
  dep)` (verifiable, the `has_nucleus` template) plus an *attest-only* audit
  palette (`audited(dep, by)`, `vulnerable(dep, claim)`) in its own trust area,
  so machine-checkable inclusion and human audit never launder into each other
  (the palette-partition rule).

**Framing note.** "Multiple nuclei" (author-declared trust boundaries) and
"deps are already content-addressed" (automatic boundaries) converge: a
**dependency nucleus** is a content-addressed dependency unit the community
*elevates* to a first-class audit subject. The hash gives the boundary for
free; the nucleus framing adds the editorial "this bundle is a coherent thing
worth normalizing on."

## 4. Why this beats "update ASAP" (the load-bearing argument)

The industry default — dependabot, `npm audit`, "upgrade to the patched
version now" — routes all trust through whoever announces *"X is bad, use Y
instead."* That person is a **high-value target for corruption**: they are, by
construction, positioned to place malware on every machine that obeys. A
poisoned `Y` shipped under a real vulnerability disclosure is a supply-chain
attack wearing a security advisory's clothes. **Expect high-value targets to
be corrupt**, and have a fallback when that expectation feels prescient.

This model *is* the fallback, because it changes what a version **is**: not
"current" or "outdated" but a **content hash with an attestation history**.

- A vulnerability disclosure is a *new attestation* (`vulnerable(dep, …)`) from
  some source — **evidence to weigh against the trust graph, not a command**.
- The proposed fix `Y` is *also* just a hash, and a brand-new one: it has
  **thin attestation** (often only the announcer). Under confirmation-outranks-
  trust, a version no independent peer has audited does not outrank a version
  your community has vouched for over months. **The model naturally resists the
  rush** — it prefers dense, independently-corroborated attestation to a
  single loud source, in *both* directions ("X is bad" and "Y is good").
- Factoring shared deps (§2) concentrates audit attention onto fewer subjects,
  so there is **more manpower per version** — to evaluate the vuln claim *and*
  to evaluate whether the fix is itself poisoned before the population moves.

This is "slow trust": trust earned by independent re-audit, resistant to
single-source manipulation. It is the same shape as SP's core thesis
(confirmation outranks trust; the graph, not the authority, decides), pointed
at dependency versions. It also feeds an already-planned SP item: a vuln claim
vs. good-standing claims about the same hash is exactly the **conflicting-
attestation** case (SP ROADMAP §5, detection specced, scoring deferred) — real
conflicting strokes to design the penalty against.

The honest boundary: this does **not** say "never update." It says updates
compete on corroborated trust like everything else, and a disclosure is input
to that competition, not an override of it. Sometimes the community converges
fast (a well-corroborated fix); sometimes it holds (a thin, suspicious one).
The point is the graph decides, not the announcer.

## 5. Door left open (done) and deferred (not)

**Done (2026-07-19):** spd's FOD vendoring is a **projection of the lockfile**
(`SemanticPaint/flake.nix`: `builtins.fromTOML` over `node/manifest.toml`, each
hex dep an FOD keyed by its `outer_checksum`). Dependencies are content-
addressed *data* with one source of truth, not hand-copied constants — the atoms
a shared-dependency unit would factor over already have stable hashes and a
canonical list. Nothing about the current single-nucleus manifest forecloses
the generalization.

**Deferred (wake-up conditions):**

- *Multi-nucleus manifest + dep-closure-as-referenceable-units* — wakes when a
  second cell in the wild shares a substantial dependency with an existing one,
  or when byte/audit cost of duplicated deps is actually observed.
- *`has_dependency` verifiable color + audit/vuln attest-only palette* — wakes
  with the first real cross-cell shared dependency to attest about, or the
  first community wanting to normalize a version.
- *Version-interchangeability vouching* ("these two versions are swappable
  without breaking either cell") — the claim that makes alignment safe; relates
  to the deferred **version lenses** (SP ROADMAP §5), which already model
  "v2 relates to v1 modulo Δ" as hash-pinned data.

## 6. Relationship to the other primers

- [nucleus-membrane.md](./nucleus-membrane.md): this is idea 5 pushed below the
  whole-nucleus granularity — from "sibling cells share a nucleus" to
  "unrelated cells share a dependency unit." The `verify_has_nucleus` /
  structural-commitment trick is reused verbatim, just N times.
- [session-cells.md](./session-cells.md) §4: SP-as-a-cell is what surfaced the
  FOD vendoring (§5); a serverful cell's dependency closure is exactly the kind
  of large, shared, worth-factoring artifact this note is about.
- [verifiable-computation.md](./verifiable-computation.md): a `builds_to`
  attestation over a shared dependency unit is the machine half; the audit
  palette is the human half — the two-kinds-of-trust boundary that primer
  insists on, here applied per dependency.
