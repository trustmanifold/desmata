"""Self-describing content addresses and the backends that serve them.

Desmata addresses cells and dependencies by hash. Today every hash is an IPFS
CID, but the hash format should not assume that forever (see
``agent_primers/iroh.md``): a hash must carry enough information that anyone who
encounters it can tell which backend produced it and therefore how to resolve
it. This module is the one place that knows all the schemes:

* :class:`Backend` -- the enum of content-addressing backends desmata knows.
* :class:`Hash` -- a backend-tagged content address with a canonical string
  form, ``dsm:<backend>:<digest>``.
* :class:`ContentBackend` -- the protocol a backend implementation satisfies
  (hash a path offline, publish it, fetch by hash).
* :class:`BackendRegistry` -- dispatch from a :class:`Hash` to the
  :class:`ContentBackend` that can serve it.

This module must stay independent of the rest of desmata (stdlib + pydantic
only) so that anything -- protocols, tools, tests -- can import it freely.
"""

from enum import StrEnum, auto
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from desmata.exceptions import UnknownBackendException

# the URI-ish scheme prefixing every desmata hash's string form
SCHEME = "dsm"


class Backend(StrEnum):
    """A content-addressing backend desmata can hash with and fetch from.

    One entry today; iroh is the anticipated second (``agent_primers/iroh.md``).
    """

    ipfs = auto()


class Hash(BaseModel):
    """A self-describing content address.

    :param backend: which backend produced (and can resolve) this hash.
    :param digest: the backend-native digest string -- for IPFS, a CID.

    The canonical string form is ``dsm:<backend>:<digest>`` (e.g.
    ``dsm:ipfs:QmW3J3...``); :meth:`parse` is its inverse. Instances are frozen
    (hashable, usable as dict keys) and compare by value, so "same backend, same
    digest" means equal.
    """

    model_config = ConfigDict(frozen=True)

    backend: Backend
    digest: str

    def __str__(self) -> str:
        return f"{SCHEME}:{self.backend}:{self.digest}"

    @property
    def dirname(self) -> str:
        """A filesystem-safe form (no ``:``), used for content-addressed
        directory names such as ``deps/by_hash/<dirname>``."""
        return f"{self.backend}-{self.digest}"

    @classmethod
    def parse(cls, text: str) -> "Hash":
        """The inverse of ``str(hash)``.

        :raises ValueError: if ``text`` is not ``dsm:<backend>:<digest>``.
        :raises UnknownBackendException: if the backend tag isn't one desmata knows.
        """
        scheme, _, rest = text.partition(":")
        backend_name, _, digest = rest.partition(":")
        if scheme != SCHEME or not backend_name or not digest:
            raise ValueError(
                f"expected a hash of the form '{SCHEME}:<backend>:<digest>', got {text!r}"
            )
        try:
            backend = Backend(backend_name)
        except ValueError:
            raise UnknownBackendException(
                f"unknown content backend {backend_name!r} in {text!r} "
                f"(known: {', '.join(Backend)})"
            )
        return cls(backend=backend, digest=digest)


@runtime_checkable
class ContentBackend(Protocol):
    """What a content-addressing backend must be able to do.

    The generalization of the old ``PathHasher``: hashing and moving content are
    two halves of the same capability, so they live behind one protocol. IPFS
    (``desmata.builtins.cell.Tools.IPFS``) is the sole implementation today.
    """

    backend: Backend

    def hash_path(self, path: Path) -> Hash:
        """Content-address ``path`` without storing it. Must be deterministic
        and work offline -- this is what makes 'the same bytes hash to this
        everywhere' true."""
        raise NotImplementedError()

    def publish(self, path: Path) -> Hash:
        """Store ``path``'s content in this backend and return its address."""
        raise NotImplementedError()

    def fetch(self, hash: Hash, into: Path) -> None:
        """Materialize the content addressed by ``hash`` at ``into``."""
        raise NotImplementedError()

    def can_handle(self, hash: Hash) -> bool:
        """Whether this backend can resolve ``hash`` (i.e. produced its scheme)."""
        raise NotImplementedError()


class BackendRegistry:
    """Dispatch from a :class:`Hash` to the backend that can serve it.

    One entry today, but resolving 'which backend handles this hash?' through
    the registry (rather than assuming IPFS) is what keeps the door open for a
    second backend.
    """

    def __init__(self) -> None:
        self._backends: dict[Backend, ContentBackend] = {}

    def register(self, impl: ContentBackend) -> None:
        self._backends[impl.backend] = impl

    def get(self, backend: Backend) -> ContentBackend:
        try:
            return self._backends[backend]
        except KeyError:
            raise UnknownBackendException(
                f"no {backend} backend registered "
                f"(registered: {', '.join(self._backends) or 'none'})"
            )

    def for_hash(self, hash: Hash) -> ContentBackend:
        return self.get(hash.backend)
