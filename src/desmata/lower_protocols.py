"a protocol is a 'lower' protocol if it does not depend on desmata.interfaces"
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import NewType, Protocol, TypeAlias, runtime_checkable

from sqlalchemy.engine import Engine

from desmata.content import ContentBackend, Hash

class LogSubject(StrEnum):
    proc = auto()
    msg = auto()


@runtime_checkable
class Loggers(Protocol):
    proc: logging.Logger
    msg: logging.Logger

    def get(self, subject: LogSubject):
        pass

    def specialize(self, name: str) -> "Loggers":
        pass


EnvVars: TypeAlias = dict[str, str]
EnvFilter: TypeAlias = Callable[[EnvVars], EnvVars]

LogMatcher: TypeAlias = Callable[[...], bool]
LogCallback: TypeAlias = Callable[[...], None]


# All content addresses are a desmata.content.Hash -- backend-tagged, so a
# hash always knows how to resolve itself. These NewTypes distinguish *what*
# was hashed (a path, a dependency, a nucleus, a whole cell) so that treating
# one like another is caught by the type checker.

PathHash = NewType("PathHash", Hash)
DependencyHash = NewType("DependencyHash", Hash)
NucleusHash = NewType("NucleusHash", Hash)
CellHash = NewType("CellHash", Hash)

# distinguish these (despite them being builtin types)
# so that treating one like the other is caught by the type checker

DependencyId = NewType("DependencyId", str)
InternalPath = NewType("InternalPath", Path) # somewhere that desmata controls, like ~/.desmata/data
ExternalPath = NewType("ExternalPath", Path) # elsewhere in the system, like /nix/store

@dataclass
class CellHashes:
    cell_hash: CellHash
    nucleus_hash: NucleusHash
    dependency_hashes: dict[str, DependencyHash]



@runtime_checkable
class LogListener(Protocol):
    def register(
        self, key: str, subject: LogSubject, matcher: LogMatcher, callback: LogCallback
    ):
        pass

    def unregister(self, key: str):
        pass


class Caller(Protocol):
    node: str
    user: str
    platform: str
    pid: int


@runtime_checkable
class UserspaceFiles(Protocol):
    home: InternalPath
    config: InternalPath
    cache: InternalPath
    data: InternalPath
    state: InternalPath
    deps_by_id: InternalPath
    deps_by_hash: InternalPath

@runtime_checkable
class DBFactory(Protocol):
    def get_engine(self) -> Engine:
        "creates a db if it doesn't exist"
        raise NotImplementedError()

    def delete_db(self) -> None:
        raise NotImplementedError()

@dataclass
class ProtoDependency:
    """
    A Dependency is already under desmata control, has an ID and a hash, etc. A
    A ProtoDependency has its dependencies under desmata control, but it iself is not yet.
    """
    target : ExternalPath
    dependencies: list[tuple[str, InternalPath]]

IdGetter : TypeAlias = Callable[[Path], str]

class CellContext(Protocol):

    name: str
    caller: Caller
    cell_dir: InternalPath
    home: InternalPath
    loggers: Loggers

    def get_env_filter(
        self,
        *,
        exec_path: Path | list[Path],
        passthru_vars: list[str],
        set_default_env: bool,
        env_overrides: EnvVars,
    ) -> EnvFilter:
        """
        Cell tools might not inherit env vars from the surrounding environment,
        instead they get this env.  This allows cells to encapsulate their dependencies
        properly rather than depending on data from places in the user's environment
        which desmata does not control.
        """
        raise NotImplementedError()

    def internalize_ids_hashes(
        self,
        *,
        proto_dep: ProtoDependency,
        id_getter: IdGetter,
        hasher: ContentBackend,
    ) -> tuple[DependencyId, DependencyHash]:
        raise NotImplementedError()
