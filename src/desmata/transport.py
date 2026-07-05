"""Moving a built dependency between peers by hash, offline — per store path.

This is the heart of desmata's partition-tolerance bet: a peer who already has a
nix store closure can hand it to a peer who lacks it, with no rebuild and no
internet, because everything is addressed by hash.

Transport is **per store path**, not one opaque blob. Each path in the closure is
exported as its own NAR (`nix-store --export <path>`, which is byte-deterministic
for a given exporter) and added to IPFS, so a path shared by two closures gets the
*same* CID and is stored/transferred once — the dedup the inspect/dedup tooling
measures, now preserved over the wire. A small IPLD **manifest** links each path
to its NAR CID; exporting the manifest yields a single CAR containing the manifest
plus every (deduplicated) NAR sub-DAG — the "sneakernet" artifact handed to peer B.

Import replays the manifest in dependency order (leaves first), importing each
path's NAR into the nix store (a single-path import requires its references to be
present already, hence the order).

These functions are what a future ``dsm bootstrap --source peer`` will call; for
now they are exercised by the partition spike test.

Residual caveat: `nix-store --export`'s envelope includes the recorded `deriver`,
so the per-path CID is deterministic per *exporter* (perfect for "adding a tool to
a cell grows it modestly") but two independent exporters that recorded different
derivers for the same output would produce different CIDs. Stripping the deriver
(or CA-derivations) would make dedup machine-independent; see
agent_primers/trustix-interop.md §8.
"""

from pathlib import Path

from desmata.builtins.cell import Tools
from desmata.content import Hash
from desmata.exceptions import UnknownBackendException
from desmata.nix import Nix


# --- bootstrap transport (trusted tools: ssh + nix; no ipfs) ----------------
# The chicken-and-egg: to receive ipfs (the builtin managed dependency) from a
# peer, a node would need ipfs already. So the *first* dependency travels over
# the trusted tools instead -- nix's own closure transport, over ssh between
# machines or as a plain file copy on one. Everything *after* ipfs rides the
# content-addressed ipfs path below. See agent_primers/phase-2.md (two tiers).

def acquire_closure_over_ssh(
    nix: Nix, peer: str, path: str, *, into_store: Path | None = None
) -> None:
    """Pull ``path``'s closure from ``peer``'s nix store over ssh, using only the
    trusted tools (nix delegates to ssh). ``peer`` is an ssh destination such as
    ``user@host``; ``into_store`` targets an alternate local store root.

    This is the production peer-bootstrap transport; on a single host it can't be
    exercised without sshd/keys, so it's covered by the container/LAN phase while
    ``acquire_closure_local`` covers the same mechanism here."""
    args = ["copy", "--from", f"ssh://{peer}", "--no-check-sigs"]
    if into_store is not None:
        args += ["--to", str(into_store)]
    args.append(path)
    nix(*args)


def acquire_closure_local(
    nix: Nix, path: str, *, into_store: Path, workdir: Path
) -> list[str]:
    """Copy ``path``'s closure into ``into_store`` on the same machine, via
    ``nix-store --export | --import`` -- the "just a file copy" / sneakernet form
    of the bootstrap transport (no ipfs, no ssh). Returns the imported paths."""
    nar = workdir / "bootstrap.nar"
    nix.export_closure(nix.closure_paths(path), dest=nar)
    return nix.import_closure(nar, store=into_store)


def _topo_order(nix: Nix, root_path: str) -> list[str]:
    """The closure of ``root_path``, leaves first — the order single-path imports
    must follow (a path's references must already be in the store). Deterministic
    (ties broken by path), so the same closure yields the same manifest."""
    infos = nix.closure_info(root_path)
    members = {str(i.path) for i in infos}
    deps = {
        str(i.path): {str(r) for r in i.references if str(r) != str(i.path)} & members
        for i in infos
    }
    ordered: list[str] = []
    done: set[str] = set()
    while len(ordered) < len(members):
        ready = sorted(p for p in members if p not in done and deps[p] <= done)
        if not ready:  # a reference cycle (rare in the store) — emit the rest
            ready = sorted(p for p in members if p not in done)
        ordered.extend(ready)
        done.update(ready)
    return ordered


def export_closure_to_car(
    nix: Nix, ipfs: Tools.IPFS, path: str, *, workdir: Path
) -> tuple[Hash, Path]:
    """Peer A: package ``path``'s closure as per-path NARs under an IPLD
    manifest, exported to a single CAR. Returns ``(manifest_hash, car_path)``.

    The manifest's ``paths`` are in dependency order; each links to its NAR's CID,
    so a store path shared with another closure reuses that CID (dedup).
    """
    entries: list[dict] = []
    for index, store_path in enumerate(_topo_order(nix, path)):
        nar = workdir / f"{index}.nar"
        nix.export_closure([store_path], dest=nar)  # single-path NAR
        nar_cid = ipfs.add(nar)
        entries.append({"path": store_path, "nar": {"/": nar_cid}})

    manifest_cid = ipfs.dag_put({"root": path, "paths": entries})
    car = workdir / f"{manifest_cid}.car"
    ipfs.dag_export(manifest_cid, dest=car)
    return Hash(backend=ipfs.backend, digest=manifest_cid), car


def import_car_to_store(
    nix: Nix,
    ipfs: Tools.IPFS,
    car: Path,
    manifest: Hash,
    *,
    workdir: Path,
    store: Path | None = None,
) -> list[str]:
    """Peer B: reconstruct a closure from a CAR and its manifest, importing each
    path's NAR in manifest (dependency) order into the nix store (``store``
    selects an alternate local store root). Returns the store paths reconstructed.
    """
    if not ipfs.can_handle(manifest):
        raise UnknownBackendException(f"this {ipfs.backend} repo cannot resolve {manifest}")
    ipfs.dag_import(car)
    manifest_doc = ipfs.dag_get(manifest.digest)

    imported: list[str] = []
    for index, entry in enumerate(manifest_doc["paths"]):
        nar = workdir / f"{index}.nar"
        ipfs.get(entry["nar"]["/"], nar)
        imported.extend(nix.import_closure(nar, store=store))
    return imported
