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
6. A is set up to serve its nix store over ssh (as an unprivileged `peer` user)
7. B runs **`dsm bootstrap --source peer --from ssh://peer@A --ipfs-path …`** —
   desmata's own flow pulls ipfs's closure over the trusted tools (nix+ssh, the
   chicken-and-egg breaker), constructs the builtin cell from it without
   rebuilding, and hashes the probe: **`CID_B == CID_A`**

## Status

Passing, end to end through desmata's own CLI: peer B, partitioned from the
internet, reproduces peer A's content hash by `dsm bootstrap --source peer`-ing
ipfs from A over the trusted tools. (Automatic discovery of the peer's ipfs path
— so `--ipfs-path` isn't needed — awaits cell manifests; see Phase 4 / item 4 in
`agent_primers/phase-2.md`.)

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
