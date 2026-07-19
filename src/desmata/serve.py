"""Serving cells to peers: desmata's first long-running process.

Everything else desmata does with ipfs is ``--offline``: hashing, publishing,
and unpacking cells touch only the local repo, and bundles move between peers
as CAR files over the trusted tools. ``dsm serve`` is the online counterpart:
it runs the builtin cell's ipfs daemon, which

* announces this repo's pinned content (``publish_cell`` pins what it
  publishes), so peers can find and fetch cells published here by hash alone;
* lets :func:`desmata.cell_archive.from_hash` fetch non-local cells from
  whichever peer has them (its online fallback).

While the daemon runs, kubo writes ``<repo>/api`` and every ipfs CLI call
desmata makes against that repo routes through the daemon automatically, so
the rest of desmata needs no daemon-awareness beyond
``Tools.IPFS.daemon_running()``. Local-only work never needs ``dsm serve``.
"""

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

from desmata.builtins.cell import Tools


class DaemonFailed(Exception):
    """The ipfs daemon exited early or never became ready."""


def isolate(ipfs: Tools.IPFS, *, swarm_key: str | None = None) -> None:
    """Cut a repo off from the public ipfs network and from fixed ports, so a
    throwaway session node can't collide with a developer's daemon, another
    test, or the world: no bootstrap peers, no mDNS, random localhost-only
    ports, DHT in server mode (a localhost node would otherwise demote itself
    to client, leaving a tiny swarm with no DHT servers).

    ``swarm_key`` makes the network private: nodes sharing the key form their
    own swarm and reject everyone else. Must run before the daemon starts."""
    ipfs("bootstrap", "rm", "--all")
    ipfs("config", "--json", "Addresses.Swarm", '["/ip4/127.0.0.1/tcp/0"]')
    ipfs("config", "Addresses.API", "/ip4/127.0.0.1/tcp/0")
    ipfs("config", "--json", "Addresses.Gateway", "[]")
    ipfs("config", "--json", "Discovery.MDNS.Enabled", "false")
    ipfs("config", "Routing.Type", "dhtserver")
    # kubo treats loopback addresses as undialable and won't put them in DHT
    # records, which silently breaks discovery on an all-localhost swarm; this
    # opts loopback into the LAN DHT (the knob exists exactly for test nets)
    ipfs("config", "--json", "Routing.LoopbackAddressesOnLanDHT", "true")
    if swarm_key is not None:
        (ipfs.repo / "swarm.key").write_text(swarm_key)


def spawn_daemon(ipfs: Tools.IPFS) -> subprocess.Popen:
    """Start ``ipfs daemon`` on this tool's repo (initializing it if needed)
    and return the process without waiting for readiness."""
    if not (ipfs.repo / "config").exists():
        ipfs.init()
    return ipfs.spawn("daemon")


def wait_ready(
    ipfs: Tools.IPFS, process: subprocess.Popen, *, timeout: float = 60.0
) -> None:
    """Block until the daemon is serving requests, or raise
    :class:`DaemonFailed` if it dies or the timeout passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DaemonFailed(
                f"ipfs daemon exited with code {process.returncode} "
                "before becoming ready"
            )
        if ipfs.daemon_running():
            return
        time.sleep(0.1)
    shutdown(process)
    raise DaemonFailed(f"ipfs daemon did not become ready within {timeout:.0f}s")


def shutdown(process: subprocess.Popen, *, grace: float = 15.0) -> None:
    """Stop a daemon: ask nicely (SIGTERM lets kubo remove ``<repo>/api`` and
    close the repo lock cleanly), kill after ``grace`` seconds."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def running(ipfs: Tools.IPFS) -> Iterator[subprocess.Popen]:
    """A daemon on ``ipfs``'s repo for the duration of the block."""
    process = spawn_daemon(ipfs)
    try:
        wait_ready(ipfs, process)
        yield process
    finally:
        shutdown(process)


def serve_forever(ipfs: Tools.IPFS, *, on_ready=None) -> int:
    """Run the daemon in the foreground until it exits or the user interrupts;
    returns the daemon's exit code. ``on_ready`` (if given) is called once the
    daemon is serving -- the CLI uses it to tell the user."""
    process = spawn_daemon(ipfs)
    try:
        wait_ready(ipfs, process)
        if on_ready is not None:
            on_ready()
        return process.wait()
    except KeyboardInterrupt:
        # ^C already reached the child (same process group); just wait it out
        shutdown(process)
        return 0
    except BaseException:
        shutdown(process)
        raise
