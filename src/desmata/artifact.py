"""Artifact-pinned dependencies: the lightweight weight class of cell.

A lightweight cell (agent_primers/lightweight-cells.md) pins a prebuilt wasm
component in its nucleus: the ``artifact`` manifest maps the blob's
cell-relative path to its content address, and the manifest is declared into
the nucleus, so trust in the nucleus extends to the exact bytes. This module
supplies the dependency class such a cell uses in place of (or alongside) the
nix-building kind -- same library, no bifurcation: the factory calls
``build_or_get`` and it is the cell's dependency class that decides whether
nix is involved.

Realization is offline-first (the primer's §2):

1. the blob is present in the cell dir and hashes to the nucleus pin -> use
   it (no nix, no network);
2. the blob is missing or mismatched but nix is available -> build the cell's
   recipe and verify the output against the pin. A mismatch is a hard error
   (the cell is lying about itself); a match is a locally-witnessed
   ``builds_to(nucleus, artifact)``, minted through the provenance layer so a
   heavy peer's build becomes the attestation that lets light peers skip
   building;
3. neither -> :class:`CellUnavailable`.
"""

import shutil
from pathlib import Path
from typing import Any, ClassVar

from desmata.cell_archive import ARTIFACT_MANIFEST, artifact_pins, nucleus_hash
from desmata.cell_utils import get_nix
from desmata.content import ContentBackend, Hash
from desmata.exceptions import ArtifactPinMismatch, CellUnavailable
from desmata.interface import Dependency
from desmata.invoke import DEFAULT_INVOKE_TIMEOUT, Invoker, build_invoker
from desmata.lower_protocols import CellContext, DependencyHash
from desmata.provenance import (
    SP_BUILDS_TO,
    SP_REPRODUCIBILITY_PALETTE,
    Brushstroke,
)


class WasmComponent(Dependency):
    """A dependency whose realization is a pinned wasm component, not a nix
    closure. Subclasses declare where the blob lives:

    * ``artifact_path`` -- the blob's cell-relative path, which is also its
      key in the ``artifact`` manifest (e.g. ``"gnize_wasm.wasm"``);
    * ``flake_output`` -- the flake package that rebuilds it (the recipe);
    * ``built_path`` -- the blob's path inside that package's output
      (e.g. ``"lib/gnize_wasm.wasm"``).

    ``root`` points at the verified blob itself. ``witnessed`` carries any
    ``builds_to`` brushstrokes minted while realizing (the factory persists
    them via the provenance layer)."""

    artifact_path: ClassVar[str]
    flake_output: ClassVar[str]
    built_path: ClassVar[str]

    witnessed: list[Brushstroke] = []

    @classmethod
    def build_or_get(
        cls, context: CellContext, hasher: ContentBackend
    ) -> "WasmComponent":
        cell_dir = Path(context.cell_dir)
        pins = artifact_pins(cell_dir)
        try:
            pin = pins[cls.artifact_path]
        except KeyError:
            raise ArtifactPinMismatch(
                f"{cell_dir} does not pin {cls.artifact_path!r} in its "
                f"'{ARTIFACT_MANIFEST}' manifest (found: {sorted(pins) or 'none'})"
            )

        # 1. a present, pin-true blob needs nothing else
        blob = cell_dir / cls.artifact_path
        if blob.exists() and hasher.hash_path(blob) == pin:
            return cls._realized(blob, pin)

        # 2. rebuild from the recipe and hold the output against the pin
        if shutil.which("nix") is None:
            raise CellUnavailable(
                f"{cls.artifact_path} is missing (or fails verification) in "
                f"{cell_dir}, and nix is not available to rebuild it from the "
                "recipe. Fetch the cell (with its blob) from a peer, or realize "
                "it on a foundry."
            )
        built_root, _ = get_nix(context).build(cls.flake_output)
        built = built_root / cls.built_path
        actual = hasher.hash_path(built)
        if actual != pin:
            raise ArtifactPinMismatch(
                f"{cell_dir} pins {cls.artifact_path} as {pin}, but its own "
                f"recipe ({cls.flake_output}) builds {actual} -- the cell is "
                "lying about itself"
            )
        # materialize the verified bytes where the pin says they live, so the
        # realized cell dir is again complete (and re-publishable as-is)
        blob.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, blob)
        blob.chmod(0o644)

        # the act we just performed is the attestation the trust layer wants
        dep = cls._realized(blob, pin)
        dep.witnessed = [
            Brushstroke(
                color=SP_BUILDS_TO,
                palette=SP_REPRODUCIBILITY_PALETTE,
                args=(str(nucleus_hash(_dag_capable(hasher), cell_dir)), str(pin)),
            )
        ]
        return dep

    @classmethod
    def _realized(cls, blob: Path, pin: Hash) -> "WasmComponent":
        return cls(
            id=cls.get_id(blob),
            hash=DependencyHash(pin),
            root=blob,
            immediate_dependencies={},
        )

    @staticmethod
    def get_id(root: Path) -> str:
        # not a /nix/store path: the blob's identity is its content, so the
        # file name is only a human-friendly handle
        return f"artifact-{Path(root).name}"

    def get_invoker(self, context: CellContext) -> Invoker:
        """An :class:`Invoker` for this component, built from the cell's own
        flake (its ``wasmtime`` output -- pinned by the nucleus's flake.lock)."""
        return build_invoker(context)

    def invoke(
        self,
        invoker: Invoker,
        function: str,
        args: list[Any],
        *,
        timeout: float | None = DEFAULT_INVOKE_TIMEOUT,
    ) -> Any:
        return invoker.invoke(Path(self.root), function, args, timeout=timeout)


def _dag_capable(hasher: ContentBackend):
    """Computing a nucleus hash requires the manifest-building backend (ipfs
    with dag_put), which is what the factory hands every cell in practice."""
    if not hasattr(hasher, "dag_put"):
        raise TypeError(
            f"{type(hasher).__name__} cannot build nucleus manifests; "
            "pass the builtins ipfs tool"
        )
    return hasher
