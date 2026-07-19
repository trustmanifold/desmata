# Primer: session cells — serverful software as a cell

**Status:** design note, 2026-07-18. **Not scheduled** — it records a
direction and reserves the seams, so later work doesn't re-confuse runtime
shape with the gossip contract. No code yet; spec + pseudocode only.

**Audience:** anyone who tries to package software that isn't a pure
function as a cell — a server you send messages to, a docker-compose /
k8s fleet, an airflow-trigger-a-DAG-then-query pipeline — or who works on
packaging Semantic Paint itself as a cell. Read
[lightweight-cells.md](./lightweight-cells.md) (the pure `cell-wasm`
contract this note carves *away* from) and
[verifiable-computation.md](./verifiable-computation.md) (verification
policies, which this note leans on) first.

---

## 1. The confusion this note dissolves

The working picture — "a cell is a container for gossipable pure
functions" — asks one idea to carry two independent things:

1. **Runtime shape** — *how you drive the software*: one pure call, or
   start-a-server-and-send-N-messages, or bring-up-a-fleet /
   trigger-a-DAG / wait / query. A **lifecycle** question.
2. **The gossip claim** — *what deterministic input→output association
   goes on the wire*: `evaluates_to(C, F, X, Y)`, re-verifiable by
   re-execution. A **memoization** question.

The whole desmata ⇄ Semantic Paint thesis lives on axis 2. The
`cell-wasm` contract (zero-capability, statically-pure, bit-deterministic
— lightweight-cells.md §4) is *one* way to make axis-2 claims cheaply
re-verifiable by any pocket node. **Servers live on axis 1 and say
nothing about axis 2's purity requirement.** The failure mode is letting a
server's runtime shape leak into the pure-function contract. It need not:
the two axes are already separable in the code.

## 2. What already exists (do not rebuild)

- **A serverful cell already ships.** The ipfs builtin *is* a long-running
  daemon. `desmata/serve.py` already has the lifecycle: `spawn_daemon` /
  `wait_ready` / `shutdown`, a `running()` context manager ("daemon up for
  the duration of this block"), and `serve_forever`. "Server up, do a
  thing, server down" is that file today — just not yet generalized off
  the ipfs tool onto the `Cell` interface.
- **Runners are named, pluggable contracts, not engines**
  (lightweight-cells §4). `cell-wasm` and `nix` are two names in one
  namespace; a third adds without touching the pure lane.
- **Verification policy already rides the color, not the stroke**
  (verifiable-computation §3: `exact-hash | canonicalize-then-hash |
  tolerance | attest-only`). Weaker policies are already first-class.
- **Trust is already partitioned by palette** (SP protocol_design §8.3),
  so cheap machine recompute never launders into expensive human trust.
- **The escape hatch is already cut** (lightweight-cells §4): *"A future
  weight class that legitimately needs I/O (a WASI-http cell, say) must
  take a different contract name — a different trust bar, a different
  determinism story, and never a valid runner for a verifiable color."*
  Servers are that weight class.
- **Home-state primitives exist**: `dsm cells` (state by size),
  `dsm clean <cell>` / `dsm clean --all`.

## 3. The three seams that are genuinely missing

### 3.1 A session lifecycle on the `Cell` interface

Today `Cell` (`interface.py`) has no lifecycle past `__init__`.
`greeter.greet()` is the degenerate session-per-call case; ipfs's daemon
lifecycle is bolted onto the *tool*, not the cell. Generalize `serve.py`'s
`running()` into the interface:

```python
class Cell(ABC, Generic[SpecificClosure]):
    @contextmanager
    def session(self, *, home: HomePolicy = Ephemeral()) -> Iterator[Session]:
        """Bring the cell's processes up, yield a handle you send N
        messages to, tear them down on exit. Default: a no-op whose
        `Session` calls the cell's functions directly — a pure cell's
        session IS a single call. A serverful cell overrides to
        spawn / wait_ready / shutdown (the serve.py shape)."""
```

The unit of setup/teardown becomes the **session**, not the call. Batch
100 operations inside one `with cell.session() as s:` and you pay one
bring-up — no more "99 superfluous server up/downs." Want a fresh server
mid-batch? Open a second session. This **decouples "how many servers" from
"how many operations."** Docker-compose / k8s / airflow are the same
shape: the cell wraps the *orchestrator*, `session()` brings the fleet up,
`s.trigger(dag)` / `s.wait()` / `s.query()` are messages, teardown takes
it all down — one session interface regardless of process count. (SP's own
test harness already does this: its "per-session process-compose stack"
is a fleet-bring-up wrapped as one session.)

### 3.2 Hermetic-home discipline (ephemeral default + snapshot-to-hash)

The deep seam: hermeticity is not hygiene, it is **the precondition for
any gossipable claim about a serverful computation.** If the cell home is
ambient, the *input* is underspecified, so no attestation reproduces and a
friend's machine legitimately diverges. The "works on my machine because
the home happened to contain an artifact I forgot about" bug and the
"un-memoizable output" problem are the *same* bug: non-hermetic state.

desmata already solved this for *build inputs* (declared, content-addressed
closure). Apply the identical philosophy to *runtime state*:

- **Ephemeral by default.** A session starts from a fresh scratch home,
  discarded on teardown. The common path is reproducible; accidental
  reliance on accumulated state is impossible-by-default.
- **Persistence is opt-in and declared.** When accumulation is wanted (the
  legitimate case), you name it, and you can **snapshot the home to a
  hash** and declare it as a session input. Then "the missing step" is a
  content-addressed dependency that either travels with the cell or is
  *loudly absent* (`CellUnavailable`) — never silently present.

```python
class HomePolicy: ...
class Ephemeral(HomePolicy):        # fresh scratch home, discarded — default
    ...
class Persistent(HomePolicy):       # a named home that accumulates
    name: str
class FromSnapshot(HomePolicy):     # start from a content-addressed home,
    seed: CellHash                  # fail loudly if the seed can't be resolved

# Any policy may carry an overlay: content-addressed files placed into the
# home before bring-up. Each overlaid file is hashed and is PART OF THE
# SESSION'S INPUT IDENTITY — see §6. The overlay generalizes FromSnapshot
# (a whole-home seed is the all-files overlay); a bug repro is the
# one-config-file overlay.
class HomePolicy:
    overlay: dict[RelPath, ContentRef] = {}
```

`dsm clean` / `dsm cells` are the raw materials; missing is (i)
ephemeral-by-default sessions and (ii) home-snapshot-to-hash. The
`UserspaceFiles` split (home/config/cache/data/state) already gives a
natural boundary for *what* a snapshot captures.

### 3.3 A `cell-session` runner contract (servers out of the pure lane)

A server touches clock / network / disk, so its outputs **cannot** be
re-verified by the cheap zero-capability static-purity route. Give session
computations their own runner name (`cell-session`, say) with an honest
weaker verification policy:

- `canonicalize-then-hash` where the output normalizes (strip
  timestamps / ordering), or
- `attest-only` where it doesn't (M-of-N peers ran the whole session and
  agree — the trust fallback the thesis already has).

It **must not** back an `exact-hash` verifiable color, and it carries its
own name so a pocket node knows to fall back to trust. Entirely within the
existing runner-plurality + verification-policy + palette-partition
machinery — a new (weaker) contract in an existing slot, no new trust
concepts.

## 4. Two reasons to be a cell — and where SP lands

There are **two orthogonal reasons** for something to be a cell:

- **Category 1 — a gossipable / verifiable *function*.** Needs `cell-wasm`
  purity. gnize-cell is this.
- **Category 2 — a reproducibly-*distributable artifact*.** Needs only
  build reproducibility + a run recipe. **The ipfs builtin is already
  exactly this, and it's serverful.**

**Semantic Paint packaged as a cell is Category 2, not Category 1.** Nobody
memoizes `spd(X)=Y` and re-verifies it by re-execution — SP is
*infrastructure you distribute reproducibly*, not a function you gossip
about. So SP-as-a-cell needs seams **3.1 (session)** and **3.2 (hermetic
home)** and *nothing* from **3.3**'s verification story: `spd` is another
daemon in the ipfs-builtin shape (`session()` starts spd, you send it
publish / ask / gossip calls, teardown stops it). The ipfs builtin already
proves a serverful Category-2 cell works end to end — most of the
SP-as-a-cell risk is retired before a line is written.

The blind-first-fetch bootstrap paradox (needing SP to judge whether to
trust SP) is real but *social*, not a cell-mechanics problem
(lightweight-cells §5: "identity and trust state cannot be fabricated
after the fact"). The "here's the hash, trust me until I introduce you to
others" onboarding is the correct and only answer; the cell format needs
nothing extra for it.

## 5. The de-risking demo (proposed, unbuilt)

Simplest thing that exercises 3.1 + 3.2 (the seams SP-as-a-cell needs) —
not a toy that only re-proves the ipfs daemon. A **tiny stateful-server
cell** (sqlite-backed HTTP counter, or lean on nushell-cell's http):

1. **One session, N ops** — open a session, do 100 increments, one
   bring-up/tear-down. Proves no per-op restart.
2. **Fresh vs. persistent** — a second session from an *ephemeral* home
   starts at zero; a session from a *declared snapshot* resumes the count.
   Proves the ephemeral-default + snapshot-to-hash distinction.
3. **The forgotten-artifact catch** — the same session on a home missing
   the declared snapshot fails *loudly* (`CellUnavailable`), not silently
   off ambient state. The exact "works on my machine" bug, turned into a
   caught error.

SP-as-a-cell is these three behaviors with `spd` swapped for the counter.

## 6. Door to keep open: injected inputs → crowd-verified reproduction

Not day-1, but the seams above must not foreclose it. The payoff is the
thesis (crowd-sourced memoization over a web of trust) applied to bug
reports: **a reproduction becomes a gossipable fact, so a variety of peers
have already confirmed it by the time the maintainer looks.**

The example: *"with a config file containing a `6`, the app throws an
exception whose message contains `Foo`."* Once the app is a `cell-session`
cell, that is an `evaluates_to`-shaped claim:

- **Input `X` = the session's declared inputs = the home overlay (§3.2:
  the content-addressed config file) + the message sequence** (here, just
  "start it up"). Because the overlay is hashed and part of the session's
  input identity, `X` is fully specified and travels — a peer fetches the
  exact config by hash and re-injects it. No "works on my machine": the
  input is content-addressed, not ambient.
- **Runner = `cell-session`** (§3.3), so verification is re-execution
  under an honest weaker policy — never `exact-hash`.
- **Output `Y`** is the observed exception. This surfaces one genuinely
  new color shape worth reserving now:

**Predicate colors.** A bug report does *not* assert the whole exception
(which carries timestamps, addresses, stack noise — irreproducible across
peers). It asserts a *stable feature*: `contains(output, "Foo")`. So the
verification facet's vocabulary should admit a **predicate** verification
(substring / regex / structured match) alongside byte-equality — the
claim is `predicate(run(C, X))`, confirmed by re-running and checking the
predicate. This is strictly more robust than `canonicalize-then-hash` for
the serverful case, *and* it is the natural shape of a repro. Keeping the
verification facet open to a predicate (not only an equality `Y`) is the
one forward-looking requirement this use case adds. It composes with
everything else: predicate `cell-session` claims live in their own palette,
score on their own trust graph, and accumulate peer confirmations exactly
as `evaluates_to` claims do today.

Nothing here is built; the obligations are only: (a) the home overlay is a
declared, hashed part of session input identity (§3.2 already shapes it),
and (b) the verification-facet vocabulary is not hardwired to equality.
Both are "don't paint a corner," not work.

## 7. Relationship to the other primers

- [lightweight-cells.md](./lightweight-cells.md) §4 reserved the
  "different contract name for I/O" this note fills in as `cell-session`;
  `cell-wasm` stays pure by definition.
- [verifiable-computation.md](./verifiable-computation.md) §3 supplies the
  verification policies `cell-session` uses (`canonicalize-then-hash` /
  `attest-only`) — a serverful cell is that primer's "container/process
  runner," the non-nix, non-wasm case it always anticipated.
- [desmata-as-semantic-paint-app.md](./desmata-as-semantic-paint-app.md):
  SP-as-a-cell is the mirror image — there, desmata is an app *on* an SP
  node; here, SP is a Category-2 artifact *packaged as* a desmata cell.
  Both can be true; they meet at the node's local API either way.
