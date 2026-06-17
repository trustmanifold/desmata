"""The payoff behind the inspect commands, as an executable proof: when two
tools share a dependency, the shared bytes are stored *once*, so adding the
second tool grows storage only by its unique content.

This is what makes cells cheap to extend and cheap to ship: a smart cell author
who reuses a dependency another tool already packages pays almost nothing for it.
The mechanism is plain IPFS content-addressing — identical bytes hash to the same
CID — which the `dsm inspect <cell> <tool> ipfs` merkle-DAG view makes visible.

(Caveat, see transport.py: this free dedup holds for the per-store-path / per-file
storage model the inspect view uses; the Phase-1 NAR-blob *transport* does not yet
preserve it.)
"""

from pathlib import Path
from random import Random

from desmata.builtins.cell import DesmataBuiltins

from conftest import make_ipfs


def _blocks(ipfs, cid: str) -> set[str]:
    return set(ipfs.refs(cid)) | {cid}


def test_shared_dependency_is_stored_once(
    builtins: DesmataBuiltins, tmp_path: Path
):
    repo = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "repo")

    # a 2 MB "shared dependency", byte-identical in both tools (deterministic so
    # the test is reproducible); plus a small file unique to each tool
    shared = Random(0).randbytes(2_000_000)
    for name, seed in (("toolA", 1), ("toolB", 2)):
        d = tmp_path / name
        d.mkdir()
        (d / "shared-dep").write_bytes(shared)
        (d / f"unique-{name}").write_bytes(Random(seed).randbytes(50_000))

    cid_a = repo.add(tmp_path / "toolA", recursive=True)
    blocks_a = _blocks(repo, cid_a)
    cid_b = repo.add(tmp_path / "toolB", recursive=True)
    blocks_b = _blocks(repo, cid_b)

    shared_blocks = blocks_a & blocks_b      # reused across both tools
    new_from_b = blocks_b - blocks_a         # what toolB actually added

    # the 2 MB shared dependency (~8 × 256-KiB chunks + tree nodes) is reused...
    assert len(shared_blocks) >= 7
    # ...so adding toolB cost far less than re-storing the shared dependency
    assert len(new_from_b) < len(shared_blocks)


def test_identical_file_has_same_cid_regardless_of_tool(
    builtins: DesmataBuiltins, tmp_path: Path
):
    repo = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "repo")
    content = Random(7).randbytes(500_000)
    a = tmp_path / "a"
    a.write_bytes(content)
    b = tmp_path / "b"
    b.write_bytes(content)
    # content addressing: same bytes -> same CID, so a shared dep collapses to
    # one stored object no matter which tool references it
    assert repo.add(a) == repo.add(b)
