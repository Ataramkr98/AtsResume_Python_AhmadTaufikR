from __future__ import annotations

import io
import socket
import struct
from dataclasses import dataclass
from typing import BinaryIO, Protocol

from django.conf import settings


class MalwareScanner(Protocol):
    def scan(self, stream: BinaryIO) -> bool: ...


@dataclass
class ClamAVScanner:
    host: str
    port: int = 3310

    def scan(self, stream: BinaryIO) -> bool:
        with socket.create_connection((self.host, self.port), timeout=30) as sock:
            sock.sendall(b"zINSTREAM\0")
            while chunk := stream.read(8192):
                sock.sendall(struct.pack(">I", len(chunk)) + chunk)
            sock.sendall(struct.pack(">I", 0))
            response = sock.recv(4096).decode(errors="replace")
        return response.rstrip("\0").endswith("OK")


class NoopScanner:
    def scan(self, stream: BinaryIO) -> bool:
        stream.read(1)
        return True


def get_malware_scanner() -> MalwareScanner:
    if settings.CLAMAV_HOST:
        return ClamAVScanner(settings.CLAMAV_HOST, settings.CLAMAV_PORT)
    if settings.MALWARE_SCAN_REQUIRED:
        raise RuntimeError("Malware scanning is required but CLAMAV_HOST is not configured")
    return NoopScanner()


def stream_bytes(data: bytes) -> BinaryIO:
    return io.BytesIO(data)
