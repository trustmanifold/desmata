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

A third projection, :class:`Brushstroke`, targets a Semantic Paint web-of-trust
trust layer (``agent_primers/semantic-paint-trust-layer.md``) — a richer
alternative to Trustix's flat M-of-N, scoring transitive weighted trust. The same
captured record thus speaks to both: Trustix for flat-quorum interop, SP for
trust-graph gossip.
"""

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from desmata.interface import Dependency
from desmata.lower_protocols import UserspaceFiles
from desmata.messages import NixPathInfo
from desmata.nix import DerivationInfo, Nix


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


# Semantic Paint color/palette identifiers for the nix-build projection.
# A reproducibility palette carries *verifiable* colors (SP §2.8): claims a
# recipient can confirm by re-execution, where trust scoring is a recompute-saving
# fallback rather than the only basis for belief.
SP_REPRODUCIBILITY_PALETTE = "reproducibility/v1"
SP_BUILDS_TO = "builds_to"    # builds_to(Recipe|StorePath [key], NarHash [value])
SP_REFERENCES = "references"  # references(StorePath [key], Dep [value]) — a closure edge


@dataclass(frozen=True)
class Brushstroke:
    """The Semantic Paint projection of an :class:`Attestation` — a signed, typed,
    content-addressed claim consumable by an SP web-of-trust trust layer, exactly
    as :class:`NarInfo` is consumable by Trustix.

    This is the third projection of the same captured record (Trustix
    KeyValuePair, general Attestation, SP Brushstroke); see
    ``agent_primers/semantic-paint-trust-layer.md``. Fields mirror SP's public
    record (protocol_design.md §6.3): a ``color`` in a ``palette``, positional
    ``args``, and a signature by the peer's ed25519 key — the *same* key that acts
    as a Trustix ``LogSigner``. ``key``/``value`` arg roles live in the SP color
    definition, not here; for ``builds_to`` the first arg is the key (the recipe
    or input-addressed identity) and the second is the value (the output hash),
    which is what lets SP detect *conflicting attestations* (SP §2.8)."""

    color: str
    palette: str
    args: tuple[str, ...]
    determinism: str = "exact-hash"     # SP verification facet; nix builds are exact-hash
    suite: str | None = None            # SP crypto suite id; set when signed
    author_sig: bytes | None = None     # ed25519 sig by the peer key; set when signed

    def signing_input(self) -> bytes:
        """Canonical bytes a peer signs to author this brushstroke. Field order is
        pinned (as with :meth:`NarInfo.trustix_value`) so signatures verify
        byte-for-byte across implementations."""
        obj = {
            "color": self.color,
            "palette": self.palette,
            "args": list(self.args),
            "determinism": self.determinism,
        }
        return json.dumps(obj, separators=(",", ":")).encode()

    def signed(self, suite: str, sign: Callable[[bytes], bytes]) -> "Brushstroke":
        """Return a signed copy. ``sign`` is the peer's ed25519 signer (the desmata
        peer key == Trustix LogSigner == SP author key)."""
        return replace(self, suite=suite, author_sig=sign(self.signing_input()))

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "palette": self.palette,
            "args": list(self.args),
            "determinism": self.determinism,
            "suite": self.suite,
            "authorSig": self.author_sig.hex() if self.author_sig else None,
        }


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

    def to_dict(self) -> dict:
        """desmata's own (richer-than-Trustix) persisted form: the Trustix Value
        fields plus the ``deriver`` recipe link."""
        return {
            "path": self.path,
            "narHash": self.nar_hash,
            "narSize": self.nar_size,
            "references": list(self.references),
            "deriver": self.deriver,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NarInfo":
        return cls(
            path=d["path"],
            nar_hash=d["narHash"],
            nar_size=d["narSize"],
            references=tuple(d["references"]),
            deriver=d.get("deriver"),
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

    def to_brushstrokes(self) -> list[Brushstroke]:
        """Project this nix record onto Semantic Paint brushstrokes (SP §2.8):

        - one ``builds_to`` claim — key = the recipe (``deriver``) when known, else
          the input-addressed store path; value = the content (NAR) hash. This is
          the verifiable claim a recipient can confirm by rebuilding, and whose
          key/value split lets SP flag a *conflicting* attestation (same recipe,
          different output hash = Trustix's "Unreproduced").
        - one ``references`` edge per closure reference — the bidirectional
          dependency graph desmata wants, expressed as SP links.

        Unsigned; call :meth:`Brushstroke.signed` with the peer key when emitting.
        """
        key = self.deriver or self.path
        build = Brushstroke(
            color=SP_BUILDS_TO,
            palette=SP_REPRODUCIBILITY_PALETTE,
            args=(key, self.nar_hash),
        )
        edges = [
            Brushstroke(
                color=SP_REFERENCES,
                palette=SP_REPRODUCIBILITY_PALETTE,
                args=(self.path, ref),
            )
            for ref in self.references
        ]
        return [build, *edges]

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


def witnessed_brushstrokes(dep: Dependency) -> list[Brushstroke]:
    """Brushstrokes a dependency minted while realizing itself — e.g. an
    artifact dependency's locally-witnessed ``builds_to(nucleus, artifact)``
    (:mod:`desmata.artifact`). Empty for dependency kinds that witness
    nothing."""
    return list(getattr(dep, "witnessed", []) or [])


# --- persistence -----------------------------------------------------------
# Records are keyed by store path (the Trustix Key) and written one file per
# path, so the same store path captured by two tools/cells lands on one file --
# dedup, like the block store. This is desmata's local provenance ledger; the
# eventual trust layer projects it to Trustix entries via trustix_key/value.

def _store_dir(files: UserspaceFiles) -> Path:
    return files.data / "provenance"


def _ident(store_path: str) -> str:
    return store_path.replace("/nix/store/", "")


def save(files: UserspaceFiles, records: Iterable[NarInfo]) -> None:
    """Persist provenance records into the userspace, one file per store path."""
    directory = _store_dir(files)
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        path = directory / f"{_ident(record.path)}.json"
        path.write_text(json.dumps(record.to_dict(), separators=(",", ":")))


def _brushstroke_dir(files: UserspaceFiles) -> Path:
    return files.data / "provenance" / "brushstrokes"


def save_brushstrokes(files: UserspaceFiles, strokes: Iterable[Brushstroke]) -> None:
    """Persist brushstrokes (e.g. ``builds_to`` witnesses), one file per claim.
    Files are keyed by the claim's canonical signing bytes, so re-witnessing
    the same fact lands on the same file -- dedup, like the narinfo store."""
    directory = _brushstroke_dir(files)
    strokes = list(strokes)
    if not strokes:
        return
    directory.mkdir(parents=True, exist_ok=True)
    for stroke in strokes:
        ident = hashlib.sha256(stroke.signing_input()).hexdigest()
        (directory / f"{ident}.json").write_text(
            json.dumps(stroke.to_dict(), separators=(",", ":"))
        )


def load_brushstrokes(files: UserspaceFiles) -> list[Brushstroke]:
    """All persisted brushstrokes."""
    directory = _brushstroke_dir(files)
    if not directory.exists():
        return []
    out: list[Brushstroke] = []
    for path in sorted(directory.glob("*.json")):
        d = json.loads(path.read_text())
        out.append(
            Brushstroke(
                color=d["color"],
                palette=d["palette"],
                args=tuple(d["args"]),
                determinism=d.get("determinism", "exact-hash"),
                suite=d.get("suite"),
                author_sig=bytes.fromhex(d["authorSig"]) if d.get("authorSig") else None,
            )
        )
    return out


def load(files: UserspaceFiles) -> dict[str, NarInfo]:
    """All persisted provenance records, keyed by store path."""
    directory = _store_dir(files)
    if not directory.exists():
        return {}
    out: dict[str, NarInfo] = {}
    for path in directory.glob("*.json"):
        record = NarInfo.from_dict(json.loads(path.read_text()))
        out[record.path] = record
    return out


# --- derivation (build-recipe) graphs --------------------------------------
# The build closure behind an output: the .drv graph + sources needed to rebuild
# (and so to verify by rebuild, and to rebuild offline / on another arch). Stored
# as one graph file per root derivation; the heavy env/args are already dropped
# (the .drv path content-addresses them).

def _deriv_dir(files: UserspaceFiles) -> Path:
    return files.data / "derivations"


def save_derivations(
    files: UserspaceFiles, root_drv: str, graph: dict[str, DerivationInfo]
) -> None:
    directory = _deriv_dir(files)
    directory.mkdir(parents=True, exist_ok=True)
    obj = {
        "root": root_drv,
        "derivations": {
            p: {
                "name": di.name,
                "system": di.system,
                "outputs": di.outputs,
                "inputDrvs": list(di.input_drvs),
                "inputSrcs": list(di.input_srcs),
            }
            for p, di in graph.items()
        },
    }
    path = directory / f"{_ident(root_drv)}.json"
    path.write_text(json.dumps(obj, separators=(",", ":")))


def load_derivations(
    files: UserspaceFiles,
) -> dict[str, dict[str, DerivationInfo]]:
    """Persisted derivation graphs, keyed by root .drv path."""
    directory = _deriv_dir(files)
    if not directory.exists():
        return {}
    result: dict[str, dict[str, DerivationInfo]] = {}
    for path in directory.glob("*.json"):
        obj = json.loads(path.read_text())
        graph = {
            p: DerivationInfo(
                drv_path=p,
                name=v["name"],
                system=v["system"],
                outputs=v["outputs"],
                input_drvs=tuple(v["inputDrvs"]),
                input_srcs=tuple(v["inputSrcs"]),
            )
            for p, v in obj["derivations"].items()
        }
        result[obj["root"]] = graph
    return result


def derivation_root(graph: dict[str, DerivationInfo], output_path: str) -> str | None:
    """The .drv in ``graph`` whose output is ``output_path`` (the closure root)."""
    return next(
        (p for p, di in graph.items() if output_path in di.outputs.values()), None
    )
