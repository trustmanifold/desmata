from pathlib import Path
from injector import Binder, Injector, Module, singleton
from pytest import fixture

from desmata.builtins.cell import DesmataBuiltins
from desmata.db import LocalSqlite
from desmata.fs import DesmataFiles
from desmata.log import TestLoggers
from desmata.lower_protocols import DBFactory, Loggers, UserspaceFiles
from desmata.higher_protocols import CellFactory
from desmata.cell_factory import DefaultCellFactory


@fixture
def components(tmp_path: Path) -> Injector:
    class TestContext(Module):
        def configure(self, binder: Binder) -> None:
            log = TestLoggers()
            userspace = DesmataFiles.sandbox(tmp_path, log=log)
            db_factory = LocalSqlite(log=log, userspace=userspace)
            cell_factory = DefaultCellFactory(log=log, userspace=userspace, db_factory=db_factory)

            binder.bind(Loggers, to=log, scope=singleton)
            binder.bind(
                UserspaceFiles,
                to=DesmataFiles.sandbox(tmp_path, log=log),
                scope=singleton,
            )
            binder.bind(DBFactory, to=db_factory, scope=singleton)
            binder.bind(CellFactory, to=cell_factory, scope=singleton)

    return Injector([TestContext])


def test_local_files(tmp_path: Path, components: Injector):
    cell_factory = components.get(CellFactory)
    builtins = cell_factory.get(DesmataBuiltins)

    foo = (tmp_path / "foo")
    foo.write_text("bar")
    hash = builtins.ipfs.get_hash(foo)
    print(hash)
