"""The self-describing hash and backend dispatch (desmata.content).

A hash must carry which backend produced it, so that anyone who encounters one
can tell how to resolve it -- the seam a second content backend (e.g. iroh, see
agent_primers/iroh.md) plugs into. Pure unit tests: no nix, no ipfs.
"""

from pathlib import Path

import pytest

from desmata.content import Backend, BackendRegistry, ContentBackend, Hash
from desmata.exceptions import UnknownBackendException

CID = "QmW3J3czdUzxRaaN31Gtu5T1U5br3t631b8AHdvxHdsHWg"


def test_hash_string_form_round_trips():
    h = Hash(backend=Backend.ipfs, digest=CID)
    assert str(h) == f"dsm:ipfs:{CID}"
    assert Hash.parse(str(h)) == h


def test_hash_is_a_value():
    a = Hash(backend=Backend.ipfs, digest=CID)
    b = Hash(backend=Backend.ipfs, digest=CID)
    assert a == b
    assert len({a, b}) == 1  # frozen -> hashable


def test_dirname_is_filesystem_safe():
    h = Hash(backend=Backend.ipfs, digest=CID)
    assert ":" not in h.dirname
    assert h.dirname == f"ipfs-{CID}"


@pytest.mark.parametrize(
    "text",
    [
        CID,                    # a bare digest doesn't say who can resolve it
        f"ipfs:{CID}",          # missing the dsm scheme
        "dsm:ipfs:",            # missing the digest
        "dsm::abc",             # missing the backend
        "",
    ],
)
def test_parse_rejects_non_hashes(text: str):
    with pytest.raises(ValueError):
        Hash.parse(text)


def test_parse_rejects_unknown_backends():
    with pytest.raises(UnknownBackendException):
        Hash.parse("dsm:carrier-pigeon:abc")


class FakeBackend:
    """A minimal ContentBackend, enough to exercise registry dispatch."""

    backend = Backend.ipfs

    def hash_path(self, path: Path) -> Hash:
        return Hash(backend=self.backend, digest="fake")

    def publish(self, path: Path) -> Hash:
        return Hash(backend=self.backend, digest="fake")

    def fetch(self, hash: Hash, into: Path) -> None:
        pass

    def can_handle(self, hash: Hash) -> bool:
        return hash.backend is self.backend


def test_fake_backend_satisfies_the_protocol():
    assert isinstance(FakeBackend(), ContentBackend)


def test_registry_dispatches_a_hash_to_its_backend():
    registry = BackendRegistry()
    impl = FakeBackend()
    registry.register(impl)
    h = Hash(backend=Backend.ipfs, digest=CID)
    assert registry.for_hash(h) is impl
    assert registry.get(Backend.ipfs) is impl


def test_registry_degrades_loudly_when_no_backend_can_serve():
    with pytest.raises(UnknownBackendException):
        BackendRegistry().get(Backend.ipfs)
