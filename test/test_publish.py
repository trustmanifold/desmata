"""Publishing and resolving cells without a network: publish pins, local
content resolves by hash alone (no CAR, no daemon), and a miss without a
daemon says how to fix it (`dsm serve`)."""

from pathlib import Path

import desmata.samples.greeter.cell as greeter
import pytest
from desmata.builtins.cell import DesmataBuiltins
from desmata.get import CellUnavailable, from_hash, publish_cell
from desmata.higher_protocols import CellFactory
from injector import Injector

from conftest import make_ipfs

GREETER_DIR = Path(greeter.__file__).parent


def test_publish_pins_the_cell(builtins: DesmataBuiltins):
    hashes = publish_cell(builtins.ipfs, GREETER_DIR)
    pinned = builtins.ipfs("pin", "ls", "--type=recursive", "-q").split()
    assert hashes.cell_hash.digest in pinned


def test_from_hash_resolves_local_content_without_car_or_daemon(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    hashes = publish_cell(builtins.ipfs, GREETER_DIR)

    # string-first and no CAR: published content is already local, so this
    # works with no daemon running
    factory = components.get(CellFactory)
    cell = from_hash(
        builtins.ipfs, factory, str(hashes.cell_hash), into=tmp_path / "fetched"
    )
    assert "no-car-needed" in cell.greet("no-car-needed")


def test_from_hash_miss_without_daemon_says_run_serve(
    builtins: DesmataBuiltins, components: Injector, tmp_path: Path
):
    # a hash whose content exists only in another repo...
    hashes = publish_cell(builtins.ipfs, GREETER_DIR)

    # ...is a miss here, and with no daemon there is no way to fetch it
    ipfs_b = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "peerB")
    factory = components.get(CellFactory)
    with pytest.raises(CellUnavailable, match="dsm serve"):
        from_hash(ipfs_b, factory, hashes.cell_hash, into=tmp_path / "fetched")
