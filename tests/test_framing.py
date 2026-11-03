"""framed-stdio v1 §1-§2: uint32_le length prefix, 1 MiB cap both ways."""

from __future__ import annotations

import io
import struct

import pytest

from anolis_provider_sdk.framing import MAX_FRAME_BYTES, FrameError, read_frame, write_frame


class Trickle(io.RawIOBase):
    """A stream that hands out at most one byte per read, like a fragmenting pipe."""

    def __init__(self, data: bytes) -> None:
        self._data = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray) -> int:  # type: ignore[override]
        chunk = self._data.read(1)
        b[: len(chunk)] = chunk
        return len(chunk)


def test_round_trip_and_coalesced_frames() -> None:
    out = io.BytesIO()
    write_frame(out, b"abc")
    write_frame(out, b"")
    write_frame(out, b"xyz")
    stream = io.BytesIO(out.getvalue())
    assert [read_frame(stream), read_frame(stream), read_frame(stream)] == [b"abc", b"", b"xyz"]
    assert read_frame(stream) is None  # clean EOF at a frame boundary


def test_fragmented_frame_is_reassembled() -> None:
    data = struct.pack("<I", 5) + b"hello"
    assert read_frame(Trickle(data)) == b"hello"  # type: ignore[arg-type]


def test_eof_inside_a_frame_is_an_error() -> None:
    with pytest.raises(FrameError):
        read_frame(io.BytesIO(struct.pack("<I", 10) + b"short"))
    with pytest.raises(FrameError):
        read_frame(io.BytesIO(b"\x01\x00"))  # partial length prefix


def test_oversized_prefix_is_rejected_without_reading_the_body() -> None:
    stream = io.BytesIO(struct.pack("<I", MAX_FRAME_BYTES + 1) + b"x" * 8)
    with pytest.raises(FrameError):
        read_frame(stream)
    assert stream.tell() == 4  # only the prefix was consumed


def test_frame_at_the_cap_is_accepted_and_one_over_is_not_written() -> None:
    body = b"x" * MAX_FRAME_BYTES
    out = io.BytesIO()
    write_frame(out, body)
    assert read_frame(io.BytesIO(out.getvalue())) == body
    with pytest.raises(FrameError):
        write_frame(io.BytesIO(), body + b"x")
