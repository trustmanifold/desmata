# nushell as core nucleus — structured `dsm` output via a rust plugin

Design + build note, 2026-07-20.

## 1. The premise

desmata's future "core nucleus" (the widely-audited, rarely-changed shared
trust unit — nix's `stdenv`, but for cells; see [shared-nuclei.md](./shared-nuclei.md))
should carry a lowest-common-denominator toolset and nothing superfluous. One
deliberate exception to that minimalism: **nushell**. `sh`/`bash` have carried
us far, but foundational cells authored here draw the line and move forward —
we treat `nu` as already-available-and-trusted (true enough today: there are no
users). So foundational cells prefer `nu` to `sh`/`bash`, and `dsm` speaks
structured data to a nushell that's present.

First fruits:
- **gnize-cell's `repin`** is a nushell script (was bash). Its dev shell carries
  `nu`. The pin it computes is byte-identical to the bash version.
- **`dsm`** emits structured json when nushell is present, and a rust nushell
  **plugin** turns that json into native nushell tables/records.

## 2. Two layers, one source of truth

The integration is split so the Python CLI stays authoritative and the rust
never reimplements a command's output:

**Layer 1 — `--output` on the Python CLI (`src/desmata/cli/dsm.py`).** The data
commands take `--output {auto,text,json}` (`-o`). `auto` (the default) emits
json when `NU_VERSION` is set — nushell exports it into the environment of the
commands it launches — and the human text otherwise. So a plain sh/bash caller
keeps the readable text (the fallback); a nushell user gets structured data.
Covered today: `anatomy`, `cells`, `check`, `inspect`. (`ls` is a stub with no
data; skipped.) `text` output is unchanged byte-for-byte from before.

**Layer 2 — the `nu_plugin_desmata` rust crate (`rust/nu_plugin_desmata/`).**
desmata's first rust. Each command's multi-word name (`dsm anatomy`,
`dsm cells`, ...) shadows exactly that subcommand of the external `dsm` when the
plugin is loaded; every other `dsm ...` still falls through to the Python CLI.
`run` execs `dsm <cmd> ... --output json` (a direct OS exec — no recursion back
through nushell) and converts the json with **one generic
`json_to_value` converter** (objects→records, arrays→lists, scalars→scalars).
That converter is why a new command is a ~3-line declaration (its name + which
`dsm` subcommand to run), not a bespoke reimplementation. Adding a command:
add a `SimplePluginCommand` struct that builds its args and calls
`run_dsm_json`, and register it in `Plugin::commands`.

```nu
dsm anatomy /path/to/cell | get nucleus | where present   # a real table
dsm check | get tools | where not ok                      # structured query
dsm cells | get cells | sort-by size_bytes
```

Without the plugin, the same data is one pipe away in any nushell:
`dsm anatomy /path -o json | from json`. The plugin only buys ergonomics
(a real command name, `describe`, no `| from json`) — at the cost of the
version lock below.

## 3. The version treadmill (read before bumping anything)

A nu plugin is **minor-version-locked** to the nushell it loads into: for 0.x,
differing *minor* versions are incompatible, and the handshake refuses a
mismatch (`plugin_failed_to_load`). Consequences baked into the flake:

- `flake.nix` pins a **second** nixpkgs, `nixpkgs-tools`, to the exact rev
  gnize-cell resolves — **nushell 0.113.1, rustc 1.96.1**. The main `nixpkgs`
  (which builds the Python side via uv2nix) is untouched, so the hybrid rust
  does not perturb the Python packaging.
- `rust/nu_plugin_desmata/Cargo.toml` pins `nu-plugin`/`nu-protocol` to
  **0.113**, matching. `Cargo.lock` is vendored (crates.io only, no git deps),
  so `buildRustPackage` needs no network and no hand-maintained hash.
- **To bump nushell:** move the `nixpkgs-tools` rev *and* the `nu-plugin`/
  `nu-protocol` versions in lockstep, regenerate `Cargo.lock`, rebuild. Keep it
  aligned with whatever nushell the cells' dev shells provide.

`NU_VERSION` reflects the nushell REPL you *launched*, not the `nu` on PATH.
So if your login shell is (say) 0.108 and you `cd` into a cell whose dev shell
puts 0.113 on PATH, `dsm`'s `auto` detection still fires (NU_VERSION is set),
but the **plugin** won't load into the 0.108 REPL — you need a 0.113 REPL.
See §5.

**Future simplification — watch [nushell/nushell#18079](https://github.com/nushell/nushell/pull/18079).**
The whole "bundle a matching nushell + keep `nu-plugin`/`nu-protocol` in
lockstep" burden exists *only* because the engine hard-refuses a minor-version
mismatch at the handshake. That PR moves plugin version compatibility toward
negotiation / graceful degradation across a version boundary. When it lands,
this can relax into a genuine **fallback**: use the plugin when a compatible
`nu` is present, otherwise fall back to the Python `--output json` path — and we
can likely drop the `nixpkgs-tools` pin and the lockstep treadmill entirely.
Revisit here (and the `nixpkgs-tools` comment in `flake.nix`) when it merges.

## 4. Registering the plugin

The desmata dev shell puts `nu` (0.113.1) and the plugin on PATH and exports
`DESMATA_NU_PLUGIN` (the binary's full path). Once per plugin-registry:

```nu
plugin add $env.DESMATA_NU_PLUGIN
plugin use desmata
```

`plugin add` records signatures into the registry file (`$nu.plugin-path`);
`plugin use` loads them into scope (and they auto-load in later sessions that
use the same registry). The registry is machine-local — don't copy it between
machines.

**Cross-repo ordering.** gnize-cell gets `dsm` from its `desmata` flake input
(pinned to the `interface` branch). Until desmata's `interface` branch carries
this work and gnize-cell runs `nix flake update desmata`, gnize-cell's `dsm`
predates `--output` and there is no `nu-plugin-desmata` output to pull in. After
that bump, add to gnize-cell's dev shell:
`desmata.packages.${system}.nu-plugin-desmata`, and register as above.

## 5. direnv → the right `nu` per cell

Yes — `cd` into a cell can select that cell's pinned `nu`. Mechanism:

1. **flake.nix**: `nu` (and the plugin) in the dev shell's `buildInputs` —
   already done for gnize-cell and desmata. No `shellHook`-`exec nu` (that
   breaks direnv).
2. **.envrc**: `use flake` (both cells already have this). Use **nix-direnv**
   for caching.
3. **config.nu**: a direnv `env_change.PWD` hook (nushell ≥ 0.104), so entering
   the dir re-imports direnv's PATH — the pinned `nu` then resolves through
   PATH for any script/subshell that shells out to `nu`:

```nu
use std/config *

$env.config.hooks.env_change.PWD = (
  $env.config.hooks.env_change.PWD? | default []
)
$env.config.hooks.env_change.PWD ++= [{||
  if (which direnv | is-empty) { return }
  direnv export json | from json | default {} | load-env
  # direnv hands PATH back as a string; nu needs it as a list
  $env.PATH = do (env-conversions).path.from_string $env.PATH
}]
```

**Caveat that bites the plugin:** the hook swaps the PATH `nu`, but your running
REPL is still whatever version it launched as, and so is `$env.NU_VERSION`.
direnv can't swap a live process. Since the plugin is minor-locked to the REPL,
using it means actually running a matching (0.113) REPL. Two ways:

- Launch `nu` fresh inside the dev shell (a new 0.113 REPL), register, use it.
- Pin the REPL itself: a guarded `exec nu` in the hook. Set `PROJECT_NU = "1"`
  in the cell's `mkShell` `env`, then:

```nu
if ($env.PROJECT_NU? | is-not-empty) and ($env.NU_PINNED? | is-empty) {
  $env.NU_PINNED = "1"
  exec nu   # ~/.config/nushell still loads (no -n), so config carries over
}
```

  This forks a `nu` on every `cd` into the project and loses interactively-
  defined state — skip it unless you specifically want the plugin in your
  everyday REPL. (`PROJECT_NU` is not set by the dev shells today; add it if you
  opt in.)

## 6. Status

- gnize-cell `repin` → nushell, dev shell carries `nu` (0.113.1). Verified: the
  computed pin is byte-identical to the bash version.
- `dsm anatomy|cells|check` `--output json` + plugin commands: verified
  end-to-end in nushell 0.113.1 (real records/tables, structured queries, error
  surfacing). Text output unchanged. Full test suite green (147 passed).
- `dsm inspect --output json`: implemented for all four views (nix/ipfs/
  provenance/drv) as an early-return that leaves the text renderers untouched.
  **Not** runtime-exercised — the json path needs a bootstrapped cell + nix
  builds; the text path is unchanged.
