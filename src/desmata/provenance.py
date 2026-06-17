"""Provenance capture: a verifiable-computation attestation, with the nix build
as its first (and currently only) instance.

A build is a special case of a verifiable computation
(``agent_primers/verifiable-computation.md``): a *recipe* + content-addressed
*inputs* produce a content-addressed *output*, which a peer can attest and others
can verify by re-execution or trust via M-of-N consensus. We model that general
shape (:class:`Attestation`) and project it onto the Trustix nix protocol
(:class:`NarInfo`, ``agent_primers/trustix-interop.md``) so a captured record can
later *become* a Trustix log entry without re-importing.

So: the general :class:`Attestation` is the schema; :class:`NarInfo` is the
nix-build projection and the Trustix-compatible wire record. Runtime computations
(e.g. running a tool on some data) would add new runners/projections without
changing the core shape.
"""

import json
from dataclasses import dataclass

from desmata.interface import Dependency
from desmata.messages import NixPathInfo
from desmata.nix import Nix


@dataclass(frozen=True)
class Artifact:
    """A content-addressed input or output of a computation.

    For a nix build the nix identities are set; other backends (an IPFS CID, a
    BLAKE3 hash, a runtime dataset) can populate different fields later without
    changing the attestation shape."""

    store_path: str | None = None  # nix input-addressed identity
    nar_hash: str | None = None    # nix content (NAR) hash


@dataclass(frozen=True)
class Attestation:
    """A (signable, verifiable) claim that a computation produced its outputs.

    A nix build is one instance: ``runner="nix"`` and ``recipe`` is the deriver
    ``.drv``. Runtime computations would set a different runner/recipe and carry
    arbitrary input/output artifacts; the shape is deliberately not nix-specific.
    """

    runner: str                        # "nix" today; "process"/"container"/... later
    recipe: str | None                 # nix: the deriver (.drv path)
    inputs: tuple[Artifact, ...]       # nix: the references (closure edges)
    outputs: tuple[Artifact, ...]      # nix: the built store path
    # How an output is checked against an attestation. See
    # verifiable-computation.md §3. Nix builds are exact-hash; non-reproducible
    # runtime tools will need other policies.
    determinism: str = "exact-hash"


@dataclass(frozen=True)
class NarInfo:
    """The nix-build projection of an :class:`Attestation`, and the Trustix nix
    protocol record.

    The field set (``path``, ``narHash``, ``narSize``, ``references``) matches
    Trustix's narinfo Value exactly; ``deriver`` is kept for the recipe link but
    is **not** part of the Trustix Value.
    """

    path: str
    nar_hash: str
    nar_size: int
    references: tuple[str, ...]  # sorted, kept verbatim (incl. self-references)
    deriver: str | None = None

    @classmethod
    def from_path_info(cls, info: NixPathInfo) -> "NarInfo":
        return cls(
            path=str(info.path),
            nar_hash=info.narHash,
            nar_size=info.narSize,
            references=tuple(sorted(str(r) for r in info.references)),
            deriver=str(info.deriver) if info.deriver else None,
        )

    def to_attestation(self) -> Attestation:
        """Lift this nix record into the general attestation shape — making
        explicit that a build is one kind of verifiable computation."""
        return Attestation(
            runner="nix",
            recipe=self.deriver,
            inputs=tuple(Artifact(store_path=r) for r in self.references),
            outputs=(Artifact(store_path=self.path, nar_hash=self.nar_hash),),
            determinism="exact-hash",
        )

    def trustix_key(self) -> bytes:
        """Trustix nix-protocol Key: the store path string."""
        return self.path.encode()

    def trustix_value(self) -> bytes:
        """Trustix nix-protocol Value: JSON of {path, narHash, narSize,
        references} in that field order, compact — matching Go's
        ``json.Marshal(NarInfo)`` so digests/proofs line up byte-for-byte."""
        obj = {
            "path": self.path,
            "narHash": self.nar_hash,
            "narSize": self.nar_size,
            "references": list(self.references),
        }
        return json.dumps(obj, separators=(",", ":")).encode()

    def fingerprint(self) -> bytes:
        """The classic nix-cache signing string
        (``1;<path>;<narHash>;<narSize>;<refs,csv>``), as Trustix's
        ``NarInfo.Fingerprint``. ``narHash`` is kept as nix reports it (SRI);
        match the consumer's expected encoding when signatures must verify."""
        return (
            f"1;{self.path};{self.nar_hash};{self.nar_size};"
            f"{','.join(self.references)}"
        ).encode()


def closure_provenance(nix: Nix, dep: Dependency) -> list[NarInfo]:
    """One :class:`NarInfo` per store path in a tool's closure — the capture unit
    that mirrors Trustix's ``submit-closure`` (a KV pair per requisite)."""
    return [NarInfo.from_path_info(info) for info in nix.closure_info(str(dep.root))]
