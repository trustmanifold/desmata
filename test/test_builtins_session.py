"""The ipfs builtin's daemon over the Cell.session() seam.

desmata's original serverful cell (the thing behind `dsm serve`) driven through
the same uniform lifecycle as any session cell: one bring-up per block, the
home policy governs which repo it serves, teardown stops it. Tests use isolated
homes (Ephemeral/Persistent -> fresh node kept off the public swarm); the
Inherit default that serves the peer's real identity repo is exercised by
`dsm serve`, not here.
"""

import json
from pathlib import Path

from desmata.builtins.cell import DesmataBuiltins
from desmata.session import Ephemeral, Persistent


def _peer_id(ipfs) -> str:
    return json.loads(ipfs("id"))["ID"]


def test_ephemeral_daemon_up_and_down(builtins: DesmataBuiltins):
    with builtins.session(home=Ephemeral()) as s:
        ipfs = s.handle.ipfs
        assert ipfs.daemon_running()  # a real daemon is serving the session repo
        assert s.handle.process.poll() is None
        assert _peer_id(ipfs)  # commands route through the daemon and work
        repo = ipfs.repo
        assert repo.exists()
    assert not ipfs.daemon_running()  # torn down cleanly
    assert not repo.exists()  # ephemeral home (and its repo) discarded


def test_persistent_daemon_keeps_identity_across_sessions(builtins: DesmataBuiltins):
    with builtins.session(home=Persistent(name="serve")) as s:
        first = _peer_id(s.handle.ipfs)
    # a second session over the same named home reuses the repo -- same keys,
    # so the same peer identity: the daemon was brought up on persisted state
    with builtins.session(home=Persistent(name="serve")) as s:
        assert s.handle.ipfs.daemon_running()
        assert _peer_id(s.handle.ipfs) == first


def test_one_daemon_serves_many_ops(builtins: DesmataBuiltins):
    with builtins.session(home=Ephemeral()) as s:
        ipfs = s.handle.ipfs
        pid = s.handle.process.pid
        # store and read back several blobs -- all through the one daemon
        for i in range(5):
            blob = Path(ipfs.repo).parent / f"blob-{i}"
            blob.write_text(f"payload-{i}")
            cid = ipfs.add(blob)
            assert ipfs.have(cid)
            assert s.handle.process.pid == pid  # never respawned
