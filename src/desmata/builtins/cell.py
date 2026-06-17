from pathlib import Path

from desmata.cell_utils import get_nix
from desmata.higher_protocols import (
    CellHash,
    CellHashes,
    DependencyHash,
    Hasher,
    NucleusHash,
    Storage,
)
from desmata.interface import Cell, Closure, Dependency
from desmata.lower_protocols import PathHasher, CellContext, ProtoDependency
from desmata.tool import Tool


class Tools:
    class SQLite(Tool):
        def __init__(self, root: Path, context: CellContext):
            loggers = context.loggers.specialize("sqlite")
            sqlite_path_entry = root / "bin"
            sqlite_exe = sqlite_path_entry / "sqlite3"
            super().__init__(
                name="sqlite",
                path=sqlite_exe,
                log=loggers.proc,
                env_filter=context.get_env_filter(exec_path=sqlite_path_entry),
            )

    class IPFS(Tool, PathHasher):
        def __init__(self, root: Path, context: CellContext):
            loggers = context.loggers.specialize("ipfs")
            ipfs_path_entry = root / "bin"
            ipfs_exe = ipfs_path_entry / "ipfs"
            super().__init__(
                name="ipfs",
                path=ipfs_exe,
                log=loggers.proc,
                env_filter=context.get_env_filter(exec_path=ipfs_path_entry),
            )

        def get_hash(self, target: Path) -> str:
            output = self("add", "-r", "--only-hash", str(target.resolve()))
            # sample output:
            #   added QmWfbz6Tvds3X2y3iUv994ootBQ8JdyspiEqYXtAVHPfVB builtins/flake.lock
            #   added QmcA67vzYhWSCBB3KKFFtTbyVxy349SRQpzUF4Be8r4hft builtins/flake.nix
            #   added QmS2CjUTboH59Pfz2BwRFJpBbQboAiqQvyoq3wPF9e9Wwf builtins
            # Take the last hash, which will be the toplevel dir
            # (or just the file if the target wasn't a dir)
            return output.splitlines()[-1].split()[1]

        # --- content-addressed store/move -----------------------------------
        # get_hash above only *computes* a CID (``--only-hash``); the methods
        # below actually store and move bytes between repos. Each Tools.IPFS is
        # bound (via its context's HOME) to its own ipfs repo, so two instances
        # on different homes are two independent peers. All operations are
        # ``--offline`` -- the partition-tolerant data path never touches the
        # network; moving blocks between repos is done with a CAR file.

        def init(self) -> None:
            """Initialize this repo if it isn't already (idempotent)."""
            self("init", tolerate_err=True)

        def add(
            self, target: Path, *, recursive: bool = False, offline: bool = True
        ) -> str:
            """Store ``target`` in this repo and return its (root) CID.

            ``recursive`` is needed when ``target`` is a directory (e.g. a nix
            store path); ``-Q`` returns only the top-level CID."""
            args = ["add", "-Q"]
            if recursive:
                args += ["-r", "-H"]  # include directory contents + hidden files
            if offline:
                args.append("--offline")
            return self(*args, str(target.resolve())).strip().splitlines()[-1]

        def refs(self, cid: str, *, offline: bool = True) -> list[str]:
            """The CIDs of every distinct block reachable under ``cid`` (its
            descendants, not ``cid`` itself). The block count of a stored object
            is ``len(refs(cid)) + 1``."""
            args = ["refs", "-r", "-u"]
            if offline:
                args.append("--offline")
            out = self(*args, cid)
            return [line.strip() for line in out.splitlines() if line.strip()]

        def ls(self, cid: str) -> list[tuple[str, int, str]]:
            """One level of the UnixFS DAG under ``cid`` as (child_cid, size,
            name) tuples. Directory entries have names (``bin/``); a chunked
            file's internal nodes come back with an empty name."""
            entries: list[tuple[str, int, str]] = []
            for line in self("ls", cid).splitlines():
                if not line.strip():
                    continue
                parts = line.split(maxsplit=2)
                child = parts[0]
                try:
                    size = int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    size = 0  # directories report '-'
                name = parts[2] if len(parts) > 2 else ""
                entries.append((child, size, name))
            return entries

        def dag_export(self, cid: str, dest: Path) -> None:
            """Write the blocks under ``cid`` to a CAR file at ``dest``."""
            self.run_to_file("dag", "export", cid, dest=dest)

        def dag_import(self, car: Path) -> None:
            """Load a CAR file's blocks into this repo."""
            self("dag", "import", str(car.resolve()))

        def get(self, cid: str, dest: Path, *, offline: bool = True) -> None:
            """Materialize ``cid`` from this repo's blocks into ``dest``."""
            args = ["get"]
            if offline:
                args.append("--offline")
            self(*args, cid, "-o", str(dest))


class Deps:
    class IPFS(Dependency):
        @staticmethod
        def build_or_get(context: CellContext) -> "Deps.IPFS":
            nix = get_nix(context)

            # ensure ipfs exists externally
            _root, _deps = nix.build("ipfs")
            ipfs_tool = Tools.IPFS(root=_root, context=context)

            # `ipfs init` creates a repo (keys + config) under the cell's HOME
            # and errors if run a second time. Only initialize when absent so
            # build_or_get stays idempotent: re-bootstrapping, and building any
            # cell that depends on builtins, must not fail just because ipfs is
            # already initialized. Use `dsm clean ipfs keys` to force a fresh repo.
            ipfs_repo = Path(context.home) / ".ipfs"
            if not (ipfs_repo / "config").exists():
                ipfs_tool("init")

            # bring it and its deps under desmata's control
            deps_by_id: dict[str, "Deps.IPFS"] = {}
            root_dep: "Deps.IPFS | None" = None
            for subdag in nix.dep_dags(_deps, str(_root)):
                proto_dep = ProtoDependency(
                    target=subdag.info.path,
                    dependencies=[
                        (nix.get_id(x.info.path), x.info.path)
                        for x in subdag.immediate_dependencies
                    ],
                )
                dep_id, dep_hash = context.internalize_ids_hashes(
                    proto_dep=proto_dep,
                    id_getter=nix.get_id,
                    path_hasher=ipfs_tool,
                )

                # nix.dep_dags provides leaves first and works towards the root,
                # so each immediate dependency already has a Dependency in
                # deps_by_id; resolve the /nix/store deps to Dependencies.
                immediate_dependencies: dict[str, Dependency] = {}
                for child in subdag.immediate_dependencies:
                    child_id = nix.get_id(child.info.path)
                    immediate_dependencies[child_id] = deps_by_id[child_id]

                dependency = Deps.IPFS(
                    id=dep_id,
                    hash=dep_hash,
                    root=subdag.info.path,
                    immediate_dependencies=immediate_dependencies,
                )
                deps_by_id[dep_id] = dependency
                # dep_dags yields the root last
                root_dep = dependency

            if root_dep is None:
                raise ValueError("Nothing to build for ipfs")

            return root_dep

        def get_tool(self, context: CellContext) -> "Tools.IPFS":
            return Tools.IPFS(root=Path(self.root), context=context)


class BuiltinsClosure(Closure):
    # ipfs is the only *managed* builtin dependency: desmata builds it, content
    # addresses it, and (eventually) shares it peer-to-peer. nix and git are
    # trusted bootstrap tools the user is expected to have installed; their
    # conformance is checked via their interfaces, not managed here.
    ipfs: Deps.IPFS


class DesmataBuiltins(Cell[BuiltinsClosure], Hasher, Storage):
    ipfs: Tools.IPFS

    def __init__(self, closure: BuiltinsClosure, context: CellContext):
        super().__init__(closure, context)
        self.ipfs = closure.ipfs.get_tool(context)

    def get_dependency_hash(self, dep: Dependency) -> DependencyHash:
        raise NotImplementedError()

    def get_cell_hash(self, closure: Closure) -> CellHash:
        raise NotImplementedError()

    def get_nucleus_hash(self, closure: Closure) -> NucleusHash:
        raise NotImplementedError()

    def pack_cell(self, closure: Closure) -> CellHashes:
        raise NotImplementedError()

    def unpack_cell(self, hash: CellHash, into: Path) -> CellHashes:
        raise NotImplementedError()
