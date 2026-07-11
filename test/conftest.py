from pathlib import Path

from injector import Binder, Injector, Module, singleton
from pytest import fixture

from desmata.builtins.cell import DesmataBuiltins, Tools
from desmata.cell_factory import BasicContext, DefaultCellFactory
from desmata.db import LocalSqlite
from desmata.fs import DesmataFiles
from desmata.higher_protocols import CellFactory
from desmata.log import TestLoggers
from desmata.lower_protocols import DBFactory, Loggers, UserspaceFiles


def make_ipfs(kubo_root: Path, home_root: Path, name: str = "peer") -> Tools.IPFS:
    """An independent ipfs repo (its own HOME under ``home_root``) sharing the
    kubo binary at ``kubo_root``. Two of these are two peers."""
    log = TestLoggers()
    files = DesmataFiles.sandbox(home_root, log=log)
    context = BasicContext(
        name=name, cell_dir=Path(kubo_root), userspace=files, loggers=log
    )
    ipfs = Tools.IPFS(root=Path(kubo_root), context=context)
    ipfs.init()
    return ipfs


def isolate_node(ipfs: Tools.IPFS, *, swarm_key: str | None = None) -> None:
    """Cut a repo off from the public ipfs network and from fixed ports, so
    test daemons can't collide with each other, with a daemon the developer may
    be running, or with the world: no bootstrap peers, no mdns, random
    localhost-only ports, DHT in server mode (a localhost node would otherwise
    demote itself to client, leaving a tiny test swarm with no DHT servers).

    ``swarm_key`` makes the network private: nodes sharing the key form their
    own swarm and reject everyone else."""
    ipfs("bootstrap", "rm", "--all")
    ipfs("config", "--json", "Addresses.Swarm", '["/ip4/127.0.0.1/tcp/0"]')
    ipfs("config", "Addresses.API", "/ip4/127.0.0.1/tcp/0")
    ipfs("config", "--json", "Addresses.Gateway", "[]")
    ipfs("config", "--json", "Discovery.MDNS.Enabled", "false")
    ipfs("config", "Routing.Type", "dhtserver")
    # kubo treats loopback addresses as undialable and won't put them in DHT
    # records, which silently breaks discovery on an all-localhost swarm; this
    # opts loopback into the LAN DHT (the knob exists exactly for test nets)
    ipfs("config", "--json", "Routing.LoopbackAddressesOnLanDHT", "true")
    if swarm_key is not None:
        (ipfs.repo / "swarm.key").write_text(swarm_key)


def _injector(root: Path) -> Injector:
    """Build an injector backed by a desmata sandbox rooted at ``root``."""

    class TestContext(Module):
        def configure(self, binder: Binder) -> None:
            log = TestLoggers()
            userspace = DesmataFiles.sandbox(root, log=log)
            db_factory = LocalSqlite(log=log, userspace=userspace)
            cell_factory = DefaultCellFactory(
                log=log, userspace=userspace, db_factory=db_factory
            )

            binder.bind(Loggers, to=log, scope=singleton)
            binder.bind(UserspaceFiles, to=userspace, scope=singleton)
            binder.bind(DBFactory, to=db_factory, scope=singleton)
            binder.bind(CellFactory, to=cell_factory, scope=singleton)

    return Injector([TestContext])


@fixture
def components(tmp_path: Path) -> Injector:
    """A fresh, per-test sandbox/injector."""
    return _injector(tmp_path)


# Building the builtins cell shells out to nix and ipfs and copies kubo's whole
# closure into the sandbox, so it is expensive. The tests below only read from
# the resulting cell, so build it once per session and share it.
@fixture(scope="session")
def session_components(tmp_path_factory) -> Injector:
    return _injector(tmp_path_factory.mktemp("builtins"))


@fixture(scope="session")
def builtins(session_components: Injector) -> DesmataBuiltins:
    return session_components.get(CellFactory).get(DesmataBuiltins)
