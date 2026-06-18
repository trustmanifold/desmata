# e2e — containerized peer/partition tests

Slow, infrastructure-dependent end-to-end tests, kept **out of the fast unit
suite** (`pytest.ini` sets `testpaths = test`). They use **podman** to stand up
two desmata peers on a partitioned network and exercise the real `dsm` CLI across
a simulated internet outage.

## Run

```
pytest e2e            # via pytest (skips if podman isn't running)
e2e/run.sh            # or directly (prints staged progress)
```

On macOS, podman needs its VM running (`podman machine start`).

## What it proves

Two containers from `e2e/Containerfile` (nix + git + ssh + a pre-built `dsm`):

- **peerA** — on the internet *and* an `--internal` podman network.
- **peerB** — on *only* the internal network: it can reach A, but **not** the
  internet. A real partition, not a flag.

The staged flow (`run.sh`), all green:

1. build image, partitioned network, start peers (B's default route deleted)
2. the partition is real (A online; B reaches A, not the internet)
3. `dsm check` passes in both (trusted tools present)
4. peer A `dsm bootstrap`s over the internet → probe `CID_A`
5. peer B's `dsm bootstrap` **fails** offline — it has no ipfs and no internet,
   so it needs a peer
6. B pulls ipfs's closure from A over ssh (`nix copy --from ssh://peer@A`) — the
   trusted-tools bootstrap transport, the chicken-and-egg breaker
7. B reproduces the probe hash with the A-sourced ipfs: **`CID_B == CID_A`**

## Status

Passing: peer B, partitioned from the internet, reproduces peer A's content hash
using ipfs it received from A over the trusted tools. Wrapping the manual stage-6
transport into a `dsm bootstrap --source ssh://peer@A` subcommand (so it's
desmata's own flow rather than the harness's `nix copy`) is the remaining thread-1
polish — the mechanism is proven here.

## Notes

- The image installs the `dsm` CLI with `uv` (Python wheels), **not** `nix build`
  — so it doesn't drag in desmata's whole nix closure. nix stays for the runtime
  cell builds.
- nix's build sandbox is disabled in the image (no user namespaces inside the
  container).
- In-container ssh quirks handled in `run.sh`: nixpkgs' sshd denies root login
  (use a `peer` user), needs the privsep `sshd` user + `/etc/nsswitch.conf`, a
  `remote-program` so the non-login ssh session finds `nix-store`, and relaxed
  `/nix/var/nix/db` perms so the unprivileged peer can serve the store.
