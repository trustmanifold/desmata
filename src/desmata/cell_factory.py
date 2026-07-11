import getpass
import importlib
import os
import platform
import re
import shutil
import stat
from pathlib import Path

from sqlalchemy import Engine

from desmata import provenance
from desmata.builtins.cell import Deps as DesmataBuiltinDeps
from desmata.builtins.cell import DesmataBuiltins
from desmata.builtins.cell import Tools as DesmataBuiltinTools
from desmata.cell_utils import get_nix
from desmata.content import BackendRegistry, ContentBackend
from desmata.exceptions import BadCellClassException
from desmata.fs import create_hard_links
from desmata.higher_protocols import CellFactory
from desmata.interface import Closure, Dependency, SpecificCell
from desmata.lower_protocols import (
    Caller,
    CellContext,
    DBFactory,
    DependencyHash,
    DependencyId,
    EnvFilter,
    EnvVars,
    ExternalPath,
    IdGetter,
    InternalPath,
    Loggers,
    ProtoDependency,
    UserspaceFiles,
)

default_passthrough_vars = [
    "TERM",
    "COLORTERM",
    "EDITOR",
    "VISUAL",
    "PAGER",
    "LANG",
    "LC_.*",
]

user = "desmata-user"

# home subdirs
cache = ".cache"
config = ".config"
share = ".local/share"
state = ".local/state"
tmp = ".tmp"
home_subdirs = [cache, config, share, state, tmp]


class LocalCaller(Caller):
    def __init__(self):
        self.node = platform.node()
        self.platform_desc = platform.platform()
        # getpass.getuser() reads env/pwd and works without a controlling tty;
        # os.getlogin() raises "Inappropriate ioctl for device" in containers,
        # daemons, and CI.
        self.user = getpass.getuser()
        self.pid = os.getpid()

    node: str
    user: str
    platform: str
    pid: int


class BasicContext(CellContext):
    name: str
    caller: Caller
    id_dep_dir: InternalPath
    hash_dep_dir: InternalPath
    cell_dir: InternalPath
    home: InternalPath
    loggers: Loggers

    _userspace: UserspaceFiles

    def __init__(
        self, name: str, cell_dir: InternalPath, userspace: UserspaceFiles, loggers: Loggers
    ):
        self.cell_dir = cell_dir
        self.caller = LocalCaller()
        self._userspace = userspace

        self.home = self._userspace.data / "cells" / name / "home" / "desmata-user"
        for subdir in home_subdirs:
            home_subdir = self.home / subdir
            home_subdir.mkdir(parents=True, exist_ok=True)
        self.loggers = loggers.specialize(name)

    def _get_default_env(self) -> EnvVars:
        return {
            "USER": "desmata-user",
            "HOME": str(self.home),
            "TMP": str(self.home / tmp),
            "XDG_CACHE_HOME": str(self.home / cache),
            "XDG_CONFIG_HOME": str(self.home / config),
            "XDG_DATA_HOME": str(self.home / share),
            "XDG_STATE_HOME": str(self.home / state),
        }

    def _get_passed_thru_vars(self, passthru_vars: list[str]) -> EnvVars:
        """
        Called by the env filter, this passes external vars to the eventual subprocess,
        but only if they're on the list.
        """
        env: EnvVars = {}
        for var, val in os.environ.items():
            for passthru_var in passthru_vars:
                if re.match(passthru_var, var):
                    env[passthru_var] = val
        return env

    def get_internal_id_path(self, external_path: ExternalPath, *, id_getter: IdGetter) -> InternalPath | None:
        dep_id = id_getter(external_path)
        internal_path = self._userspace.deps_by_id / dep_id
        if internal_path.exists():
            return internal_path
        else:
            return None
            
    def internalize_ids_hashes(
        self,
        *,
        proto_dep: ProtoDependency,
        id_getter: IdGetter,
        hasher: ContentBackend,
    ) -> tuple[DependencyId, DependencyHash]:
        """
        Internalizes a dependency from an external path to the desmata-controlled storage.
        Creates both id-based and hash-based references for the dependency.
        Returns the ID and the content address.
        """

        # Get dependency ID and prepare paths
        dep_id = id_getter(proto_dep.target)
        id_path = self._userspace.deps_by_id / dep_id

        # If the id_path already exists, we just need to ensure the hash path exists
        if id_path.exists():
            hash_val = hasher.hash_path(id_path)
            hash_path = self._userspace.deps_by_hash / hash_val.dirname

            # Create the hash path if it doesn't exist
            if not hash_path.exists():
                create_hard_links(id_path, hash_path)

            return DependencyId(dep_id), DependencyHash(hash_val)
        
        # Create the id directory
        id_path.mkdir(parents=True, exist_ok=True)
        
        # Process the target path
        target_path = Path(proto_dep.target)
        
        # Check if target is read-only and on the same device (likely in nix store)
        target_stat = os.stat(target_path)
        data_stat = os.stat(self._userspace.data)
        is_readonly = not bool(target_stat.st_mode & stat.S_IWUSR)
        same_device = target_stat.st_dev == data_stat.st_dev
        
        # Either create hard links (if safe) or copy the files
        if is_readonly and same_device:
            create_hard_links(target_path, id_path)
        else:
            # Not in nix store or on different device - copy the data
            if target_path.is_dir():
                shutil.copytree(target_path, id_path, dirs_exist_ok=True)
            else:
                shutil.copy2(target_path, id_path / target_path.name)
        
        # Create the hash-based path
        hash_val = hasher.hash_path(id_path)
        hash_path = self._userspace.deps_by_hash / hash_val.dirname
        
        # Create hard links from id_path to hash_path
        if not hash_path.exists():
            create_hard_links(id_path, hash_path)

        return DependencyId(dep_id), DependencyHash(hash_val)

    def get_env_filter(
        self,
        *,
        exec_path: Path | list[Path] = [],
        passthru_vars: list[str] = default_passthrough_vars,
        set_default_env: bool = True,
        env_overrides: EnvVars = {},
    ) -> EnvFilter:
        passthru = self._get_passed_thru_vars(passthru_vars)
        env_vars = self._get_default_env() if set_default_env else {}

        match exec_path:
            case Path():
                deps = str(exec_path)
            case list():
                deps = list(map(str, exec_path))

        def filter(env: EnvVars) -> EnvVars:
            inner_env = env_vars.copy()
            inner_env.update(self._get_passed_thru_vars(passthru))
            if exec_path := inner_env.get("PATH"):
                exec_path = ":".join([*exec_path.split(":"), *deps])
            return inner_env

        return filter


class DefaultCellFactory(CellFactory):
    userspace: UserspaceFiles
    db_factory: DBFactory
    _engine: Engine | None = None

    def __init__(self, log: Loggers, userspace: UserspaceFiles, db_factory: DBFactory):
        self.log = log
        self.userspace = userspace
        self.db_factory = db_factory
        # every content backend this factory has built, keyed by Backend --
        # the dispatch point for resolving a Hash to something that can serve it
        self.backends = BackendRegistry()

    @property
    def engine(self) -> Engine:
        if not self._engine:
            self._engine = self.db_factory.get_engine()
        return self._engine

    def _pick_name(self, candidates: list[str]):
        choice = candidates[0]
        self.log.msg.debug(
            f"TODO: pick from among name candidates {candidates} to avoid collisions"
            f"\nfor now, defaulting to {choice}"
        )
        return choice

    def _cell_name(self, cell_class_file: Path) -> str:
        parts = cell_class_file.parent.parts  # /foo/bar/baz/cell.py
        name_candidates = [
            "/".join(parts[-1 * i :]) for i in range(1, len(parts))
        ]  # ["baz", "bar/baz", "foo/bar/baz"]
        self.log.msg.debug(f"Cell name candidates: {name_candidates}")
        return self._pick_name(name_candidates)

    def _cell_file_inode_device(self, cell_class_file: Path) -> tuple[int, int]:
        stat_info = os.stat(str(cell_class_file))
        return (stat_info.st_ino, stat_info.st_dev)

    @staticmethod
    def _get_closure_type(CellType: type[SpecificCell]) -> type[Closure]:
        closure_types = []
        for base in CellType.__orig_bases__:
            if hasattr(base, "__args__"):
                for arg in base.__args__:
                    if issubclass(arg, Closure):
                        closure_types.append(arg)

        if len(closure_types) != 1:
            raise BadCellClassException(
                f"Expected exactly one closure type for "
                f"cell type {CellType}, instead got {closure_types}. "
                "Cells should have Cell[SomeClosure] as a base class, "
                "where SomeClosure has Closure as a base class"
            )
        return closure_types[0]

    @staticmethod
    def _get_dependency_types(ClosureType: type[Closure]) -> dict[str, Dependency]:
        dependency_types: dict[str, Dependency] = {}
        for attr, annotated_type in ClosureType.__annotations__.items():
            if issubclass(annotated_type, Dependency):
                dependency_types[attr] = annotated_type
        return dependency_types

    def get(self, CellType: type[SpecificCell]) -> SpecificCell:
        # build a cell context
        module_name = CellType.__module__
        module = importlib.import_module(module_name)
        cell_file = Path(module.__file__)
        name = self._cell_name(cell_file)
        inode, device_id = self._cell_file_inode_device(cell_file)
        context = BasicContext(
            name=name,
            cell_dir=cell_file.parent,
            userspace=self.userspace,
            loggers=self.log,
        )
        self.log.msg.debug(f"CONTEXT, {context.__dict__}")

        # get a content backend to hash with (ipfs is the only one so far)
        hasher: ContentBackend
        ipfs_dep: DesmataBuiltinDeps.IPFS
        if CellType is DesmataBuiltins:
            ipfs_dep = DesmataBuiltinDeps.IPFS.build_or_get(context)
            hasher = DesmataBuiltinTools.IPFS(root=ipfs_dep.root, context=context)
        else:
            builtins = self.get(DesmataBuiltins)
            hasher = builtins.ipfs
            ipfs_dep = builtins.closure.ipfs
        self.backends.register(hasher)

        # populate the closure
        deps: dict[str, Dependency] = {}
        ClosureType = DefaultCellFactory._get_closure_type(CellType)
        for attr_name, AttrType in DefaultCellFactory._get_dependency_types(
            ClosureType
        ).items():
            dep: Dependency

            # populate dep.root
            if AttrType is DesmataBuiltinDeps.IPFS and attr_name == "ipfs":
                dep = ipfs_dep
            else:
                dep = AttrType.build_or_get(context, hasher=hasher)

            # populate dep.root and dep.id based on dep.root
            dep.hash = DependencyHash(hasher.hash_path(dep.root))
            dep.id = dep.get_id(dep.root)
            deps[attr_name] = dep

        closure = ClosureType(local_name=name, **deps)

        # capture build provenance: a Trustix-projectable narinfo per store path
        # in each managed dependency's closure (see provenance.py / phase-2.md).
        # Artifact dependencies root outside the nix store (the blob itself);
        # their provenance is whatever they witnessed while realizing (e.g. a
        # builds_to mint), not a store-path closure.
        nix = get_nix(context)
        records: list = []
        strokes: list = []
        for dep in deps.values():
            if Path(dep.root).parts[:3] == ("/", "nix", "store"):
                records.extend(provenance.closure_provenance(nix, dep))
            strokes.extend(provenance.witnessed_brushstrokes(dep))
        provenance.save(self.userspace, records)
        provenance.save_brushstrokes(self.userspace, strokes)

        return CellType(closure, context)
