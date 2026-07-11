"""The native `gn` binary as an oracle for the lightweight-cell pilot tests.

Both proof tests (Test A: foundry path via wasmtime; Test B: browser path via
the runner page) hold their fingerprints against the same native binary,
built from the same SemanticPaint rev that gnize-cell pins -- read straight
out of gnize-cell's flake.lock, so there is one source of truth about which
gnize the pilot is talking about.
"""

import json
import subprocess
from pathlib import Path


def native_gn(gnize_cell_src: Path) -> Path:
    lock = json.loads((Path(gnize_cell_src) / "flake.lock").read_text())
    locked = lock["nodes"]["semanticpaint"]["locked"]
    assert locked["type"] == "git", f"unexpected input type: {locked}"
    flake_url = f"git+{locked['url']}?ref={locked['ref']}&rev={locked['rev']}"
    out = subprocess.run(
        [
            "nix", "--extra-experimental-features", "nix-command",
            "--extra-experimental-features", "flakes",
            "build", "--no-link", "--print-out-paths", f"{flake_url}#gnize-cli",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(out) / "bin" / "gn"


def oracle_records(gnize_cell_src: Path, data: bytes, *, channel: int = 0) -> list[dict]:
    """`gn --json --all` on ``data``: every window, default width ladder --
    the parameters the pilot tests use on the wasm side too."""
    out = subprocess.run(
        [str(native_gn(gnize_cell_src)), "--json", "--all", "--channel", str(channel)],
        input=data,
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(out)
