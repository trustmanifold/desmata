"""Test B of the lightweight-cell pilot (gnize-cell/PLAN.md): the runner path.

A headless browser -- with **no nix and no python in the page** -- fetches
gnize-cell by hash from a `dsm serve` gateway, verifies the blob against the
nucleus pin client-side (@helia/verified-fetch re-hashes every block), runs
the component on the browser's own wasm engine, and this harness asserts the
result matches the native `gn` oracle.

Moving parts (all opt-in, `pytest -m browser`):

* the kubo daemon serves its HTTP gateway on an ephemeral localhost port with
  permissive CORS -- the test-side config `dsm serve` would grow a
  `--gateway` flag for;
* the runner page is runner-cell's nix-built bundle (RUNNER_CELL_DIST, or
  built here from the pinned repo), served from a plain local http server;
* chromium comes from playwright's browser bundle (CHROMIUM_BIN, or built
  here via nixpkgs#playwright-driver) and needs no driver protocol: when the
  page finishes it POSTs its verdict back to its own origin (the static
  server below), so the harness just waits for that. If darwin ever refuses,
  the same page + this same flow run unchanged inside the podman e2e harness
  on linux.

For hand-driving during development: enable the gateway on a served repo,
`python -m http.server` in the runner dist, and open
    http://localhost:8000/?cell=dsm:ipfs:<cellhash>&gateway=http://127.0.0.1:<gwport>
"""

import functools
import json
import os
import shutil
import socketserver
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote

import pytest
from desmata import serve
from desmata.builtins.cell import DesmataBuiltins, Tools
from desmata.get import publish_cell

from conftest import isolate_node, make_ipfs
from gnize_oracle import oracle_records

GNIZE_CELL_SRC = os.environ.get("GNIZE_CELL_SRC")
RUNNER_CELL_REPO = "git+file:///Users/matt/src/runner-cell"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        GNIZE_CELL_SRC is None,
        reason="GNIZE_CELL_SRC is not set (the dev shell exports the pinned cell fixture)",
    ),
]

INPUT_TEXT = "the quick brown fox jumps over the lazy dog, twice over"


def _nix_build(installable: str) -> Path:
    out = subprocess.run(
        [
            "nix", "--extra-experimental-features", "nix-command",
            "--extra-experimental-features", "flakes",
            "build", "--no-link", "--print-out-paths", installable,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(out)


def _runner_dist() -> Path:
    if given := os.environ.get("RUNNER_CELL_DIST"):
        return Path(given)
    return _nix_build(RUNNER_CELL_REPO)


def _chromium() -> Path:
    if given := os.environ.get("CHROMIUM_BIN"):
        return Path(given)
    browsers = _nix_build("nixpkgs#playwright-driver.browsers-chromium")
    for pattern in (
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-linux/chrome",
        "chromium_headless_shell-*/chrome-mac*/headless_shell",
        "chromium_headless_shell-*/chrome-linux/headless_shell",
    ):
        if found := sorted(browsers.glob(pattern)):
            return found[0]
    raise AssertionError(f"no chromium binary under {browsers}")


def enable_gateway(ipfs: Tools.IPFS) -> None:
    """Turn the kubo HTTP gateway on (ephemeral localhost port) with CORS open:
    the browser tier fetches blocks from here, and hash-verifies them itself --
    the gateway is transport, not trust, so `*` gives nothing away."""
    ipfs("config", "Addresses.Gateway", "/ip4/127.0.0.1/tcp/0")
    ipfs(
        "config", "--json", "Gateway.HTTPHeaders",
        json.dumps(
            {
                "Access-Control-Allow-Origin": ["*"],
                "Access-Control-Allow-Methods": ["GET", "HEAD", "OPTIONS"],
            }
        ),
    )


def gateway_url(ipfs: Tools.IPFS) -> str:
    """The gateway's bound address, from the file kubo writes to
    ``<repo>/gateway`` while the daemon runs (a URL in current kubo, a
    multiaddr in older ones)."""
    addr = (ipfs.repo / "gateway").read_text().strip()
    if addr.startswith("http"):
        return addr.rstrip("/")
    _, ip_kind, host, proto, port = addr.split("/")[:5]
    assert (ip_kind, proto) == ("ip4", "tcp"), f"unexpected gateway addr {addr}"
    return f"http://{host}:{port}"


class _RunnerHost(SimpleHTTPRequestHandler):
    """Serves the runner bundle, and collects the verdict the finished page
    POSTs back to its own origin (``__result``)."""

    def log_message(self, *args):  # keep pytest output readable
        pass

    def do_POST(self):
        if self.path.endswith("__result"):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.server.result = json.loads(body)
            self.server.result_ready.set()
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)


@pytest.fixture()
def static_server():
    """Serve a directory over localhost HTTP for the duration of a test; the
    returned server object collects the page's POSTed verdict."""
    servers = []

    def start(directory: Path) -> socketserver.TCPServer:
        handler = functools.partial(_RunnerHost, directory=str(directory))
        httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        httpd.result = None
        httpd.result_ready = threading.Event()
        httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return httpd

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _page_result(
    chromium: Path, host: socketserver.TCPServer, url: str, workdir: Path,
    *, timeout: float = 120.0,
) -> dict:
    """Load ``url`` headless and wait for the page to POST its verdict back."""
    process = subprocess.Popen(
        [
            str(chromium),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            f"--user-data-dir={workdir / 'chromium-profile'}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert host.result_ready.wait(timeout), (
            f"page never reported a result within {timeout:.0f}s"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return host.result


def test_browser_fetches_verifies_and_executes_by_hash_alone(
    builtins: DesmataBuiltins, tmp_path: Path, static_server
):
    # a serving peer: isolated repo, gateway on -- the foundry anchor
    ipfs = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "foundry")
    isolate_node(ipfs)
    enable_gateway(ipfs)
    hashes = publish_cell(ipfs, Path(GNIZE_CELL_SRC))

    dist = _runner_dist()
    chromium = _chromium()

    with serve.running(ipfs):
        host = static_server(dist)
        url = (
            f"{host.url}/?cell={hashes.cell_hash}"
            f"&gateway={gateway_url(ipfs)}"
            f"&input={quote(INPUT_TEXT)}&channel=0&min-zeros=0"
        )
        result = _page_result(chromium, host, url, tmp_path)

    assert result.get("ok"), f"runner page failed: {result.get('error')}"
    # the page verified the fetched bytes against the nucleus pin, and proved
    # the bytes it executed are those bytes
    pins = (Path(GNIZE_CELL_SRC) / "artifact").read_text()
    assert result["artifact"]["pin"] in pins

    # no nix, no python in the page -- and the same answer the native binary gives
    records = result["records"]
    assert records, "expected fingerprints from the browser run"
    oracle = oracle_records(Path(GNIZE_CELL_SRC), INPUT_TEXT.encode(), channel=0)
    assert max(r["score"] for r in records) == max(r["score"] for r in oracle)
    assert sorted((r["width"], r["score"]) for r in records) == sorted(
        (r["width"], r["score"]) for r in oracle
    )


def test_browser_refuses_bytes_the_nucleus_disowns(
    builtins: DesmataBuiltins, tmp_path: Path, static_server
):
    """Fetch-verify is only honest if it can say no: publish a fork of the cell
    whose pin doesn't match its blob (bypassing publish's own refusal by
    editing after hashing is impossible -- so fork the *pin*), and the page
    must refuse to execute."""
    liar = tmp_path / "liar-cell"
    shutil.copytree(GNIZE_CELL_SRC, liar)  # from the read-only nix store
    for path in [liar, *liar.rglob("*")]:
        path.chmod(path.stat().st_mode | 0o200)
    # re-pin to a hash of different bytes: nucleus now disowns the blob
    other = tmp_path / "other-bytes"
    other.write_bytes(b"not the component")
    ipfs = make_ipfs(Path(builtins.closure.ipfs.root), tmp_path / "foundry")
    isolate_node(ipfs)
    enable_gateway(ipfs)
    bogus_pin = ipfs.hash_path(other)
    (liar / "artifact").write_text(f"gnize_wasm.wasm  {bogus_pin}\n")

    # publish_cell would refuse this cell (see test_artifact), which is the
    # point -- so stage it the way a malicious peer would: pin + hash by hand
    from desmata.cell_archive import cell_hashes

    hashes = cell_hashes(ipfs, liar)
    ipfs.pin_add(hashes.cell_hash.digest)

    with serve.running(ipfs):
        host = static_server(_runner_dist())
        url = f"{host.url}/?cell={hashes.cell_hash}&gateway={gateway_url(ipfs)}"
        result = _page_result(_chromium(), host, url, tmp_path)

    assert result.get("ok") is False
    assert "sha256" in str(result.get("error", "")) or "lying" in str(result.get("error", ""))
