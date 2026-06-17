"""The bootstrap transport: a peer who lacks ipfs receives a closure over the
*trusted tools* (nix, and over ssh between machines), not over ipfs — the
chicken-and-egg breaker for peer bootstrap.

The ssh form (`nix copy --from ssh://...`) needs sshd/keys and is covered by the
later container/LAN phase. Here we exercise the same mechanism on one machine:
copy a closure into a fresh, isolated nix store via nix-store --export | --import,
proving a store that lacked the dependency now has it — using only nix.
"""

from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins
from desmata.log import TestLoggers
from desmata.nix import Nix
from desmata.transport import acquire_closure_local


def test_local_bootstrap_acquire_into_isolated_store(
    builtins: DesmataBuiltins, tmp_path: Path
):
    nix = Nix(cwd=tmp_path, log=TestLoggers().proc)
    # the closure to bootstrap: ipfs itself (the builtin managed dependency)
    ipfs_path = str(builtins.closure.ipfs.root)

    # peer B's store genuinely lacks it
    bstore = (tmp_path / "bstore").resolve()
    rel = ipfs_path.lstrip("/")
    assert not (bstore / rel).exists()

    imported = acquire_closure_local(
        nix, ipfs_path, into_store=bstore, workdir=tmp_path
    )

    # the whole closure (ipfs + its references) is reconstructed, with no ipfs
    # involved — only nix moved the bytes
    assert ipfs_path in imported
    assert (bstore / rel).exists()
    # references came along too (kubo's data deps)
    assert any("tzdata" in p for p in imported)
