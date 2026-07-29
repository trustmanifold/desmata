"""The desmata peer key: one Ed25519 keypair per userspace.

This is the single signing identity a desmata peer presents to every trust
layer it speaks to (``agent_primers/semantic-paint-trust-layer.md``): the
Semantic Paint *signer* key and the Trustix ``LogSigner`` are the same key,
which is what lets one captured attestation serve both projections without
re-signing ceremonies.

The identity handle (:attr:`PeerKey.signer`) is the lowercase-hex SHA-256 of
the raw 32-byte public key — byte-for-byte the fingerprint SP's
``spd/core/identity.gleam`` derives for its ``node_id``, so a desmata signer
is addressable in SP exactly like a node identity.

The key material lives in the userspace (``data/identity/peer.ed25519``, raw
32-byte seed, mode 0600) and is minted on first use; there is no rotation
story yet (SP defers that too — protocol_design §5.2's LTK-only MVP).
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from desmata.lower_protocols import UserspaceFiles


@dataclass(frozen=True)
class PeerKey:
    """A loaded peer identity: the signer fingerprint plus a signer."""

    signer: str  # lowercase-hex sha256 of the raw public key
    public_key: bytes  # raw 32 bytes
    _key: Ed25519PrivateKey = field(repr=False)

    def sign(self, msg: bytes) -> bytes:
        """Raw 64-byte Ed25519 signature — the shape SP's crypto suite
        (``v1-ed25519-sha256``) verifies."""
        return self._key.sign(msg)


def key_path(files: UserspaceFiles) -> Path:
    return files.data / "identity" / "peer.ed25519"


def fingerprint(public_key: bytes) -> str:
    """SP's identity fingerprint (``identity.gleam``): lowercase-hex SHA-256
    of the raw public key."""
    return hashlib.sha256(public_key).hexdigest()


def peer_key(files: UserspaceFiles) -> PeerKey:
    """Load the userspace's peer key, minting it on first use."""
    path = key_path(files)
    if path.exists():
        key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
    else:
        key = Ed25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_bytes(
            key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        )
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return PeerKey(signer=fingerprint(public), public_key=public, _key=key)
