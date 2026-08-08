"""
Shared HTTP client for Agent tools.

All tools communicate with the local DocGuard FastAPI server over HTTP.
If the server is not running, tools can optionally start it in-process
(via uvicorn) so the Skill works even when invoked standalone.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Server base URL — always localhost.
DEFAULT_HOST = os.environ.get("DOCGUARD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("DOCGUARD_PORT", "8765"))
BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"

# Ensure project root is importable so tools can auto-start the server.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def is_server_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.5) -> bool:
    """Check whether the DocGuard server port is listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def wait_for_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 60.0) -> bool:
    """Wait until the server responds to /api/health."""
    import urllib.request
    import urllib.error

    deadline = time.time() + timeout
    url = f"http://{host}:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1.0)
    return False


def ensure_running(auto_start: bool = True) -> str:
    """Make sure the server is up, starting it if necessary. Returns base URL."""
    if is_server_running():
        return BASE_URL
    if not auto_start:
        raise ConnectionError(
            f"DocGuard server not running at {BASE_URL}. "
            "Start it with: python -m server.main"
        )
    _start_server_subprocess()
    if not wait_for_server():
        raise ConnectionError(f"DocGuard server did not become ready at {BASE_URL}")
    return BASE_URL


def _start_server_subprocess() -> None:
    """Start the server as a detached background subprocess."""
    log_dir = PROJECT_ROOT / "data" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "server.log"
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    with log_file.open("a", encoding="utf-8") as lf:
        subprocess.Popen(
            [sys.executable, "-m", "server.main"],
            cwd=str(PROJECT_ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )


def post_json(path: str, payload: Dict[str, Any], timeout: float = 300.0) -> Dict[str, Any]:
    """POST JSON to the local server and return the parsed response."""
    import urllib.request
    import urllib.error

    url = BASE_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {body}") from exc


def get_json(path: str, timeout: float = 30.0) -> Dict[str, Any]:
    import urllib.request
    import urllib.error

    url = BASE_URL + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API error {exc.code}: {body}") from exc


def upload_file(local_path: str, timeout: float = 60.0) -> str:
    """Upload a local document to the server's isolated uploads folder.

    Returns the server-side absolute file_path that /api/analyze accepts.
    Uses only the standard library (multipart/form-data) so the tool has
    no third-party dependency beyond the runtime.
    """
    import urllib.request
    import urllib.error

    path = Path(local_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Document not found: {path}")
    boundary = "----docguardboundary7Q3k9"
    filename = path.name
    with path.open("rb") as fh:
        file_bytes = fh.read()
    crlf = b"\r\n"
    body = (
        b"--" + boundary.encode() + crlf
        + f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
        + crlf
        + b"Content-Type: application/octet-stream" + crlf + crlf
        + file_bytes + crlf
        + b"--" + boundary.encode() + b"--" + crlf
    )
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    req = urllib.request.Request(
        BASE_URL + "/api/upload", data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Upload failed {exc.code}: {err}") from exc
    if not data.get("success"):
        raise RuntimeError(f"Upload failed: {data.get('error') or data.get('message')}")
    return data["data"]["file_path"]


def print_json(data: Any) -> None:
    """Print JSON to stdout for the Agent to consume."""
    print(json.dumps(data, ensure_ascii=False, indent=2))
