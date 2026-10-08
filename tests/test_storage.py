"""Malware scanning on the upload path (`apps/common/storage.py`).

Every resume passes the scanner before it is parsed, so this is a gate on the product's main
entry point. The `NoopScanner` fallback was the only branch the suite had ever run: the ClamAV
client -- the code that actually runs in production -- had never been executed at all. A defect
in its framing would either quarantine every resume or let every file through, and neither
would be visible locally where `CLAMAV_HOST` is unset.

The two protocol tests below stand up a real loopback TCP server that speaks ClamAV's INSTREAM
protocol, so the framing is verified rather than assumed.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading

import pytest

from apps.common.storage import (
    ClamAVScanner,
    NoopScanner,
    get_malware_scanner,
    stream_bytes,
)


def _recv_exactly(conn, size: int) -> bytes:
    buffer = b""
    while len(buffer) < size:
        chunk = conn.recv(size - len(buffer))
        if not chunk:
            break
        buffer += chunk
    return buffer


@contextlib.contextmanager
def clamav_server(response: bytes):
    """A one-shot loopback ClamAV server. Yields (port, received-bytes dict).

    Closed on the way out: a leaked listening socket is a ResourceWarning, and the suite runs
    with `-W error`.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    received: dict[str, bytes] = {}

    def serve():
        conn, _ = server.accept()
        with conn:
            # b"zINSTREAM\0" is ten bytes, not nine -- "zINSTREAM" is already nine characters.
            # Reading one byte short leaves the NUL in the stream, where it is then mistaken
            # for the start of a length header and the daemon sees a corrupt upload.
            _recv_exactly(conn, len(b"zINSTREAM\0"))
            chunks = []
            while True:
                header = _recv_exactly(conn, 4)
                if len(header) < 4:
                    break
                (size,) = struct.unpack(">I", header)
                if size == 0:
                    break
                chunks.append(_recv_exactly(conn, size))
            received["data"] = b"".join(chunks)
            conn.sendall(response)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, received
    finally:
        thread.join(timeout=5)
        server.close()


# ------------------------------------------------------------------ scanner selection


def test_malware_scanner_falls_back_to_noop_when_unconfigured(settings):
    """Local development has no ClamAV; uploads must still work."""
    settings.CLAMAV_HOST = ""
    settings.MALWARE_SCAN_REQUIRED = False

    assert isinstance(get_malware_scanner(), NoopScanner)


def test_malware_scanner_uses_clamav_when_host_is_configured(settings):
    settings.CLAMAV_HOST = "clamav.internal"
    settings.CLAMAV_PORT = 3311

    scanner = get_malware_scanner()

    assert isinstance(scanner, ClamAVScanner)
    assert scanner.host == "clamav.internal"
    assert scanner.port == 3311


def test_malware_scanner_fails_loudly_when_required_but_unconfigured(settings):
    """Silently skipping a *required* scan would be a security hole, so it must raise."""
    settings.CLAMAV_HOST = ""
    settings.MALWARE_SCAN_REQUIRED = True

    with pytest.raises(RuntimeError, match="CLAMAV_HOST is not configured"):
        get_malware_scanner()


def test_noop_scanner_accepts_everything():
    """Documented behaviour: the fallback is permissive and says so by name."""
    assert NoopScanner().scan(stream_bytes(b"anything")) is True


# ------------------------------------------------------------------ INSTREAM protocol


def test_clamav_scanner_streams_the_file_and_accepts_a_clean_result():
    payload = b"%PDF-1.4 a perfectly clean resume"

    with clamav_server(b"stream: OK\0") as (port, received):
        result = ClamAVScanner("127.0.0.1", port).scan(stream_bytes(payload))

    assert result is True
    # The framing is the whole risk: if lengths were wrong, the daemon would see corrupt data.
    assert received["data"] == payload


def test_clamav_scanner_quarantines_an_infected_result():
    with clamav_server(b"stream: Eicar-Signature FOUND\0") as (port, received):
        result = ClamAVScanner("127.0.0.1", port).scan(stream_bytes(b"malicious payload"))

    assert result is False
    assert received["data"] == b"malicious payload"


def test_stream_bytes_returns_an_independently_readable_stream():
    stream = stream_bytes(b"abc")

    assert stream.read() == b"abc"
    assert stream_bytes(b"abc").read(1) == b"a"
