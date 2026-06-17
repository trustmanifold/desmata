from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins


def test_ipfs_hash(tmp_path: Path, builtins: DesmataBuiltins):
    f = tmp_path / "foo"
    f.write_text("bar")
    hash = builtins.ipfs.get_hash(f)
    # content-addressing is deterministic: "bar" always hashes to this CID
    assert hash == "QmW3J3czdUzxRaaN31Gtu5T1U5br3t631b8AHdvxHdsHWg"
