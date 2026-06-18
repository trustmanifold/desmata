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

The staged flow (`run.sh`):

1. build image, partitioned network, start peers
2. the partition is real (B reaches A, not the internet)
3. `dsm check` passes in both (trusted tools present)
4. peer A `dsm bootstrap`s over the internet → probe `CID_A`
5. peer B's `dsm bootstrap` **fails** offline — it has no ipfs and no internet,
   so it needs a peer
6. B acquires ipfs from A over ssh — the trusted-tools bootstrap transport
   (`nix copy --from ssh://A`), the chicken-and-egg breaker
7. B reproduces the probe hash, `CID_B == CID_A`

## Status

Stages 1–6 stand up the partitioned harness, run `dsm` in-container, prove the
partition is real, and move ipfs A→B over the trusted tools. **Stage 7** —
closing the loop with `dsm bootstrap --source ssh://root@A` so B reproduces A's
hash via desmata's own flow — is the remaining thread-1 wiring (see
`agent_primers/phase-2.md`); this harness is the test that drives it.

## Notes

- The image pre-builds `.#default` (the `dsm` CLI) for `aarch64-linux`; the first
  image build is slow.
- nix's build sandbox is disabled in the image (no user namespaces inside the
  container).
