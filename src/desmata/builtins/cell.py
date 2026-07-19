import json
import subprocess
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from desmata.cell_utils import get_nix
from desmata.content import Backend, Hash
from desmata.exceptions import UnknownBackendException
from desmata.higher_protocols import (
    CellHash,
    CellHashes,
    DependencyHash,
    Hasher,
    NucleusHash,
    Storage,
)
from desmata.interface import Cell, Closure, Dependency
from desmata.lower_protocols import CellContext, ProtoDependency
from desmata.session import Inherit, Session
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

    class IPFS(Tool):
        """The ipfs/kubo CLI as a desmata tool.

        Implements the ``ContentBackend`` protocol (see :mod:`desmata.content`):
        the methods under "ContentBackend" below speak backend-tagged
        :class:`Hash` values, while the rest of the surface (``add``, ``dag_*``,
        ``get``, ...) is the backend-native layer that traffics in raw CIDs.
        """

        backend = Backend.ipfs

        def __init__(self, root: Path, context: CellContext, home: Path | None = None):
            loggers = context.loggers.specialize("ipfs")
            ipfs_path_entry = root / "bin"
            ipfs_exe = ipfs_path_entry / "ipfs"
            # kubo derives the repo location from $HOME by default; a session may
            # bind the tool to a specific home instead (Cell.session over the
            # builtin), in which case IPFS_PATH pins the repo for every call --
            # init, add, and the daemon all agree on it.
            repo_home = Path(home) if home is not None else Path(context.home)
            self.repo = repo_home / ".ipfs"
            env_overrides = {} if home is None else {"IPFS_PATH": str(self.repo)}
            super().__init__(
                name="ipfs",
                path=ipfs_exe,
                log=loggers.proc,
                env_filter=context.get_env_filter(
                    exec_path=ipfs_path_entry, env_overrides=env_overrides
                ),
            )

        def daemon_running(self) -> bool:
            """Whether a daemon is serving this repo. kubo writes ``<repo>/api``
            while its daemon runs (and removes it on shutdown); when the file is
            present, every CLI call against this repo automatically routes
            through the daemon -- which is what gives offline-by-default
            commands their online counterpart (see :mod:`desmata.serve`)."""
            return (self.repo / "api").exists()

        def get_hash(self, target: Path) -> str:
            output = self("add", "-r", "--only-hash", str(target.resolve()))
            # sample output:
            #   added QmWfbz6Tvds3X2y3iUv994ootBQ8JdyspiEqYXtAVHPfVB builtins/flake.lock
            #   added QmcA67vzYhWSCBB3KKFFtTbyVxy349SRQpzUF4Be8r4hft builtins/flake.nix
            #   added QmS2CjUTboH59Pfz2BwRFJpBbQboAiqQvyoq3wPF9e9Wwf builtins
            # Take the last hash, which will be the toplevel dir
            # (or just the file if the target wasn't a dir)
            return output.splitlines()[-1].split()[1]

        # --- ContentBackend --------------------------------------------------
        # the backend-agnostic surface: desmata's protocols (hashing a
        # dependency, publishing/fetching content) speak Hash, not raw CIDs.

        def hash_path(self, path: Path) -> Hash:
            """Content-address ``path`` without storing it (offline, deterministic)."""
            return Hash(backend=self.backend, digest=self.get_hash(path))

        def publish(self, path: Path) -> Hash:
            """Store ``path`` in this repo and return its content address."""
            cid = self.add(path, recursive=path.is_dir())
            return Hash(backend=self.backend, digest=cid)

        def fetch(self, hash: Hash, into: Path) -> None:
            """Materialize the content addressed by ``hash`` at ``into``."""
            if not self.can_handle(hash):
                raise UnknownBackendException(f"ipfs cannot resolve {hash}")
            self.get(hash.digest, into)

        def can_handle(self, hash: Hash) -> bool:
            return hash.backend is Backend.ipfs

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

        def pin_add(
            self, cid: str, *, offline: bool = True, timeout: float | None = None
        ) -> None:
            """Pin ``cid`` recursively so GC can never drop it (published cells
            must outlive their publishing process). With ``offline=False`` the
            daemon first fetches any missing blocks from peers -- this is the
            online 'materialize a hash from the network' primitive."""
            args = ["pin", "add", "--recursive"]
            if offline:
                args.append("--offline")
            self(*args, cid, timeout=timeout)

        def have(self, cid: str) -> bool:
            """Whether every block under ``cid`` is already in this repo: an
            offline traversal of the whole DAG succeeds iff nothing is missing
            (a root block alone doesn't count as having the cell)."""
            completed = self.run(
                "refs", "-r", "--offline", cid, tolerate_err=True, no_stdout=True
            )
            return completed.exit_code == 0

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

        def dag_put(self, obj: dict) -> str:
            """Store ``obj`` as an IPLD node (dag-cbor) and return its CID. Links
            are expressed as ``{"/": "<cid>"}``; ``dag_export`` of the result
            follows them, so a manifest that links per-path NARs exports them
            all in one CAR."""
            return self(
                "dag", "put",
                "--input-codec", "dag-json", "--store-codec", "dag-cbor",
                stdin=json.dumps(obj),
            ).strip()

        def dag_get(self, cid: str) -> dict:
            """Read an IPLD node back as dag-json (links as ``{"/": cid}``)."""
            return json.loads(self("dag", "get", cid))

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
                    hasher=ipfs_tool,
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


@dataclass
class Daemon:
    """A running ipfs daemon and the tool bound to the repo it serves. The
    handle a :meth:`DesmataBuiltins.session` yields: ``ipfs`` routes commands
    through ``process`` for the life of the session."""

    process: subprocess.Popen
    ipfs: "Tools.IPFS"


class DesmataBuiltins(Cell[BuiltinsClosure], Hasher, Storage):
    ipfs: Tools.IPFS

    def __init__(self, closure: BuiltinsClosure, context: CellContext):
        super().__init__(closure, context)
        self.ipfs = closure.ipfs.get_tool(context)

    # --- serverful lifecycle: the ipfs daemon over Cell.session() ------------
    # The builtin is desmata's original serverful cell (`dsm serve` runs its
    # daemon). Exposing it through the uniform session seam is what SP-as-a-cell
    # (its spd daemon) will reuse. See agent_primers/session-cells.md §3.1.

    def session(self, *, home=None) -> "AbstractContextManager[Session]":
        """A live ipfs daemon for the duration of the block. Defaults to
        :class:`~desmata.session.Inherit` -- serve this peer's own repo (its
        keys), like ``dsm serve`` -- because an ipfs repo is durable identity,
        not per-session scratch. Pass ``home=Ephemeral()`` for a fresh,
        network-isolated throwaway node instead."""
        return super().session(home=home if home is not None else Inherit())

    def setup(self, home: Path) -> "Daemon":
        from desmata import serve  # local: serve.py imports Tools from here

        ipfs = Tools.IPFS(root=Path(self.closure.ipfs.root), context=self.context, home=home)
        # a fresh session repo (Ephemeral/Persistent's first run) is initialized
        # and isolated so a throwaway node never joins the public swarm; an
        # existing repo (Inherit's identity, a returning Persistent) is served
        # as-is, keys and network config intact.
        if not (ipfs.repo / "config").exists():
            ipfs.init()
            serve.isolate(ipfs)
        process = serve.spawn_daemon(ipfs)
        serve.wait_ready(ipfs, process)
        return Daemon(process=process, ipfs=ipfs)

    def teardown(self, handle: "Daemon | None") -> None:
        from desmata import serve

        if handle is not None:
            serve.shutdown(handle.process)

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
