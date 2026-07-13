# What's next — the desmata ⇄ Semantic Paint thesis

**Status:** living plan, written 2026-07-13. This is the answer to
"what's next here." Its twin is SemanticPaint's `docs/ROADMAP.md`
(`~/src/SemanticPaint`), which carries the same plan from SP's vantage —
when priorities move, move them in both. Items are tagged **[desmata]**,
**[SP]**, or **[both]**.

**Background:** [semantic-paint-trust-layer.md](./semantic-paint-trust-layer.md)
(the record projection, and the verification loop closed 2026-07),
[desmata-as-semantic-paint-app.md](./desmata-as-semantic-paint-app.md)
(the process boundary), [lightweight-cells.md](./lightweight-cells.md)
(artifact-pinned wasm cells, WAVE determinism).

## The thesis

Crowd-sourced memoization over a web of trust: every verified-pin
`dsm call` witnesses `evaluates_to(C, F, X, Y)` (`paint.witnessed_call`),
`dsm paint` signs the ledger under the peer key and ships claim + component
bytes to an SP node, and any node can verify the claim mechanically — fetch
C by hash, re-execute F(X) in a zero-capability wasmtime, byte-compare
against Y. Confirmation outranks trust on the SP side; trust is the
fallback that lets a reader accept the memoized answer because peers they
trust already re-executed it.

**Where it stands:** every piece exists but is proven only piecewise. Our
end-to-end test (`test/test_paint.py::test_witness_then_paint_lands_claim_and_component_on_the_node`)
runs against a stub node; SP's verifier tests use stub engines; the one
live witness → paint → `/api/verify` → `confirmed` run was manual and
unrecorded. No SP scenario exercises a verifiable color.

---

## 1. Now: the whole-thesis demo scenario [both]

**Wire `dsm paint` against a real `spd` in a scenario — the natural next
commit.** An SP scenario (SP repo, plus harness support) where a desmata
peer witnesses via `dsm call` (gnize-cell is the concrete candidate),
paints to a real node, a wasmtime-equipped node re-executes →
`confirmed` — landing the conclusion for a reader with *no trust path* to
the desmata placer — with contrast legs: engine-less node falls back to
trust (`unavailable`), forged result `refuted`, confirming relayer
re-grounds. Recorded and replayable like `mangled_sign.yaml`.

desmata's share: whatever the harness needs to drive `dsm call`/`dsm
paint` non-interactively against a scenario-managed node (it may already
suffice — `test_gnize_cell.py` and the `dsm` CLI are close).

**Why first:** retires the biggest integration risk (the halves have
never met inside a repeatable artifact), it *is* the demo, and everything
below builds on the pipeline it proves.

## 2. Soon

- **Ingest-time signature verification [SP]** — `canonical.verify_sig`
  still has no caller; the demo's attribution story is hollow until the
  node actually checks our signatures. (Tracked here because our
  `PublishMismatch` round-trip is the other half of that contract.)
- **Forward-compatibility rule [both]** — spec + implement "ignore
  unknown fields" on the brushstroke wire record and palette JSON
  (atproto's reserved-field lesson). Nearly free now, painful to
  retrofit, prerequisite for palette evolution. desmata's share:
  `provenance.Brushstroke.from_dict` tolerates unknown keys; keep
  `canonical_bytes()` closed over the pinned field list.

## 3. Next: the interface palette [both]

Design: [interface-palette.md](./interface-palette.md) (desmata's half);
canonical doc in SP `docs/design/interface_palette.md`. The problem:
cross-cell wiring leans on Python type hints — nothing language-agnostic
tells a consumer how to craft X or whether C₁'s output feeds C₂'s input,
so shapes are learned by runtime failure. The fix is projection, not
invention: every component already carries its WIT world; emit it as
verifiable `type_def`/`exports` brushstrokes (`interface/v1` palette,
`wit-parse` runner on the SP side), and wiring compatibility becomes a
derived Datalog fact over content-addressed type hashes. Typed DataRefs
(add type ref + size to `{hash, suite}`) ride along.

**Why after the demo:** it rides exactly the pipeline §1 proves, and its
acceptance demo — a node discovering *how to call a cell it has never
seen* from gossiped interface facts — is demo v2.

## 4. Deferred, deliberately

| Item | Why deferred | Wake-up condition |
|---|---|---|
| Content-addressed palettes (reference by hash, not name+version) | name+version unambiguous at current scale | palettes evolve incompatibly / second palette author |
| Spec'd deterministic canonical encoding (DAG-CBOR/DRISL-style) replacing the hand-rolled JSON array | twin byte-pinned test vectors (`test_provenance.py` ↔ `canonical.gleam`) do the job; migration breaks every sig and content id | a second independent wire implementation |
| `nix` runner (rebuild-and-compare) | no foundry-class node exists; `builds_to` staying trust-mediated is fine — we deliberately don't ship those artifacts (`paint.py`) | someone stands up a foundry node |
| Blob co-shipping along dependency closures (replaces `put_data` preload) | `put_data` is the declared MVP stand-in, fine at demo scale | SP interest-driven sync matures |
| iroh content backend (`content.py`) | IPFS covers the thesis path | phase-2 transport work resumes |
| Key rotation (`keys.py`) | single peer key per userspace suffices pre-demo | multi-device / revocation needs |

## 5. Considered and not adopted (AT Protocol comparison, 2026-07)

Reviewed atproto's data model for the wire layer. Adopted: typed blob
refs, forward-compat rule, and the two deferred rows above. Rejected:
the **no-floats rule** (their fix for float-hash nondeterminism; ours is
the engine's canonical WAVE rendering captured verbatim — already
closed) and **multiformat CIDs** (`dsm:<backend>:<digest>` and
`DataRef.suite` are already the self-describing-hash idea; the
cells-as-CIDs vs components-as-bare-sha256 spelling split can be absorbed
by `suite` if it ever bites). Full rationale: SP `docs/ROADMAP.md` §5.
