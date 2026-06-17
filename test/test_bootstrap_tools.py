"""Conformance tests for desmata's trusted bootstrap tools.

Desmata assumes the user has installed nix and git (alongside desmata itself and
its python dependencies). Rather than managing these peer-to-peer, desmata
trusts the user's installation and verifies that its *interface* behaves as
expected. These tests exercise the host's nix and git through the same wrappers
desmata uses, asserting the contract the rest of the system relies on.

If these fail on a user's machine, it means their environment doesn't satisfy
desmata's assumptions -- which is exactly what we want to surface early.
"""

import logging
from pathlib import Path

from desmata.git import Git
from desmata.nix import Nix

log = logging.getLogger("test.bootstrap")


# --- git -------------------------------------------------------------------

def git_tool() -> Git:
    return Git(log=log)


def test_git_meets_minimum_version():
    git = git_tool()
    # check() raises EnvironmentError if git is missing or too old
    git.check()
    assert git.version >= (2, 0, 0)


def test_git_locates_the_project_root():
    git = git_tool()
    root = git.project_root
    # the wrapper found a real repo: the desmata source tree
    assert (root / "pyproject.toml").exists()
    assert git.repo_name == root.stem


def test_git_reports_repo_state():
    git = git_tool()
    commit = git.current_commit()
    assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit)
    short = git.current_commit(short=True)
    assert commit.startswith(short)
    # is_dirty answers the "are there uncommitted changes" question as a bool
    assert isinstance(git.is_dirty(), bool)


# --- nix -------------------------------------------------------------------

def nix_tool() -> Nix:
    return Nix(cwd=Path.cwd(), log=log)


def test_nix_meets_minimum_version():
    nix = nix_tool()
    # check() raises EnvironmentError if nix is missing or too old
    nix.check()
    major, _minor, _patch = nix.version
    assert major >= 2


def test_nix_get_id_strips_the_store_prefix():
    # the contract desmata relies on: a /nix/store path maps to its bare id
    path = Path("/nix/store/bilkygayml6y9lbr1xhc6v4yxcnxx154-kubo-0.28.0")
    assert Nix.get_id(path) == "bilkygayml6y9lbr1xhc6v4yxcnxx154-kubo-0.28.0"
