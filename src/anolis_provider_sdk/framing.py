"""framed-stdio v1: a uint32 little-endian length prefix, then that many bytes of
protobuf, capped at 1 MiB in both directions (anolis-protocol
docs/profiles/framed-stdio-v1.md)."""

from __future__ import annotations

import struct
from typing import BinaryIO

MAX_FRAME_BYTES = 1024 * 1024
_PREFIX = struct.Struct("<I")


class FrameError(Exception):
    """The byte stream is not a valid frame sequence."""


def _read_exact(stream: BinaryIO, n: int) -> bytes | None:
    """Read exactly ``n`` bytes. None on EOF before the first byte; FrameError
    on EOF part-way through."""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            if not buf:
                return None
            raise FrameError(f"stream ended after {len(buf)} of {n} bytes")
        buf += chunk
    return bytes(buf)


def read_frame(stream: BinaryIO) -> bytes | None:
    """Return the next frame's payload, or None on a clean EOF between frames."""
    prefix = _read_exact(stream, _PREFIX.size)
    if prefix is None:
        return None
    (length,) = _PREFIX.unpack(prefix)
    if length > MAX_FRAME_BYTES:
        raise FrameError(f"frame of {length} bytes exceeds the {MAX_FRAME_BYTES}-byte limit")
    if length == 0:
        return b""
    payload = _read_exact(stream, length)
    if payload is None:
        raise FrameError(f"stream ended before a {length}-byte frame")
    return payload


def write_frame(stream: BinaryIO, payload: bytes) -> None:
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameError(f"frame of {len(payload)} bytes exceeds the {MAX_FRAME_BYTES}-byte limit")
    stream.write(_PREFIX.pack(len(payload)) + payload)
    stream.flush()
