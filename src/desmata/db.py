from sqlmodel import (
    SQLModel,
    create_engine,
)
from sqlalchemy.engine import Engine

# SQLModel.metadata_create_all needs this:
from desmata import models  # noqa: F401
from desmata.lower_protocols import DBFactory, Loggers, UserspaceFiles


class LocalSqlite(DBFactory):
    log: Loggers
    userspace: UserspaceFiles

    @property
    def file(self) -> str:
        return str(self.userspace.state / "cells.db").absolute()


    def __init__(self, log: Loggers, userspace: UserspaceFiles):
        self.log = log.specialize("dbfactory.sqlite")
        self.userspace = userspace

    def _unfinished(self) -> NotImplementedError:
        # __init__ used to drop `userspace` on the floor, and the connection
        # string below is malformed (`sqlite://<abspath>` instead of
        # `sqlite:////<abspath>`). Nothing persists cell metadata yet, so rather
        # than fail with a cryptic AttributeError when something eventually does,
        # raise something that says what's actually going on.
        return NotImplementedError(
            "desmata's cell-metadata database is not wired up yet; "
            "DBFactory.get_engine/delete_db are stubs (see desmata/db.py)"
        )

    def delete_db(self) -> None:
        raise self._unfinished()

    def get_engine(self) -> Engine:
        raise self._unfinished()


        
