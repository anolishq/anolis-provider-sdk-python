"""The stdio loop, end to end: the reference provider as a real subprocess."""

from __future__ import annotations

import io
import shutil
import signal
import struct
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from anolis.deviceprovider.v1 import envelope_pb2, status_pb2

from anolis_provider_sdk import Provider, serve

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config" / "conformance.yaml"


def provider_bin() -> str:
    found = shutil.which("anolis-provider-reference", path=str(Path(sys.executable).parent))
    assert found, "anolis-provider-reference is not installed in this environment (uv sync)"
    return found


def frame(msg: bytes) -> bytes:
    return struct.pack("<I", len(msg)) + msg


def spawn() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [provider_bin(), "--config", str(CONFIG)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_response(proc: subprocess.Popen[bytes]) -> envelope_pb2.Response:
    assert proc.stdout is not None
    (n,) = struct.unpack("<I", proc.stdout.read(4))
    resp = envelope_pb2.Response()
    resp.ParseFromString(proc.stdout.read(n))
    return resp


def test_hello_then_clean_exit_on_eof() -> None:
    proc = spawn()
    assert proc.stdin is not None
    req = envelope_pb2.Request(request_id=1)
    req.hello.protocol_version = "v1"
    proc.stdin.write(frame(req.SerializeToString()))
    proc.stdin.flush()
    resp = read_response(proc)
    assert resp.status.code == status_pb2.Status.Code.Value("CODE_OK")
    assert resp.hello.provider_name == "anolis-provider-reference"
    proc.stdin.close()
    assert proc.wait(timeout=10) == 0


@pytest.mark.parametrize(
    "payload,code",
    [
        (struct.pack("<I", 2 * 1024 * 1024), 2),  # over-cap length prefix: frame error
        (struct.pack("<I", 10) + b"abc", 2),  # truncated frame then EOF
        (frame(b"\xde\xad not protobuf \x00\x01"), 3),  # unparseable body
    ],
)
def test_malformed_input_exits_with_a_documented_code(payload: bytes, code: int) -> None:
    proc = spawn()
    out, err = proc.communicate(payload, timeout=10)
    assert proc.returncode == code, err.decode()
    assert out == b""  # nothing but frames ever reaches stdout


def test_author_prints_cannot_corrupt_the_frame_stream(tmp_path: Path) -> None:
    script = tmp_path / "noisy.py"
    script.write_text(
        "from anolis_provider_sdk import Provider, run\n"
        "import sys\n"
        "class Noisy(Provider):\n"
        "    name = 'noisy'\n"
        "    version = '0.0.1'\n"
        "    def devices(self):\n"
        "        print('stray output')\n"
        "        return []\n"
        "sys.exit(run(Noisy))\n"
    )
    cfg = tmp_path / "c.yaml"
    cfg.write_text("{}\n")
    hello = envelope_pb2.Request(request_id=1)
    hello.hello.protocol_version = "v1"
    listing = envelope_pb2.Request(request_id=2)
    listing.list_devices.SetInParent()
    proc = subprocess.run(
        [sys.executable, str(script), "--config", str(cfg)],
        input=frame(hello.SerializeToString()) + frame(listing.SerializeToString()),
        capture_output=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"stray output" in proc.stderr
    # stdout holds exactly two well-formed frames.
    data, offset, ids = proc.stdout, 0, []
    while offset < len(data):
        (n,) = struct.unpack_from("<I", data, offset)
        resp = envelope_pb2.Response()
        resp.ParseFromString(data[offset + 4 : offset + 4 + n])
        ids.append(resp.request_id)
        offset += 4 + n
    assert ids == [1, 2]


# ---- Provider.stop: once, on every way out of the loop ----------------------


class Recorder(Provider):
    name = "recorder"
    version = "0.0.1"

    def __init__(self, config: Mapping[str, Any], fail_stop: bool = False) -> None:
        super().__init__(config)
        self.stops = 0
        self.fail_stop = fail_stop

    def stop(self) -> None:
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("stop broke")


def hello_frame() -> bytes:
    req = envelope_pb2.Request(request_id=1)
    req.hello.protocol_version = "v1"
    return frame(req.SerializeToString())


class BrokenPipeOut(io.BytesIO):
    def write(self, data: Any) -> int:
        raise BrokenPipeError


class FailingOut(io.BytesIO):
    def write(self, data: Any) -> int:
        raise OSError("disk on fire")


@pytest.mark.parametrize(
    "payload,out,code",
    [
        (hello_frame(), io.BytesIO(), 0),  # clean EOF
        (struct.pack("<I", 10) + b"abc", io.BytesIO(), 2),  # framing error
        (frame(b"\xde\xad not protobuf \x00\x01"), io.BytesIO(), 3),  # unparseable request
        (hello_frame(), BrokenPipeOut(), 0),  # the reader went away
    ],
)
def test_stop_runs_once_on_every_exit(payload: bytes, out: io.BytesIO, code: int) -> None:
    provider = Recorder({})
    assert serve(provider, io.BytesIO(payload), out) == code
    assert provider.stops == 1


def test_stop_runs_when_the_loop_raises() -> None:
    provider = Recorder({})
    with pytest.raises(OSError):
        serve(provider, io.BytesIO(hello_frame()), FailingOut())
    assert provider.stops == 1


def test_a_failing_stop_is_logged_and_keeps_the_exit_code(caplog: pytest.LogCaptureFixture) -> None:
    provider = Recorder({}, fail_stop=True)
    assert serve(provider, io.BytesIO(struct.pack("<I", 10)), io.BytesIO()) == 2
    assert provider.stops == 1
    assert "provider stop failed" in caplog.text


def test_serve_restores_the_previous_sigterm_handler() -> None:
    before = signal.getsignal(signal.SIGTERM)
    serve(Recorder({}), io.BytesIO(b""), io.BytesIO())
    assert signal.getsignal(signal.SIGTERM) is before


def test_sigterm_ends_the_session_in_order_and_runs_stop(tmp_path: Path) -> None:
    script = tmp_path / "stoppable.py"
    script.write_text(
        "from anolis_provider_sdk import Provider, run\n"
        "import sys\n"
        "class Stoppable(Provider):\n"
        "    name = 'stoppable'\n"
        "    version = '0.0.1'\n"
        "    def stop(self):\n"
        "        sys.stderr.write('STOP RAN\\n')\n"
        "sys.exit(run(Stoppable))\n"
    )
    cfg = tmp_path / "c.yaml"
    cfg.write_text("{}\n")
    proc = subprocess.Popen(
        [sys.executable, str(script), "--config", str(cfg)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stderr is not None
    proc.stdin.write(hello_frame())
    proc.stdin.flush()
    assert read_response(proc).request_id == 1  # the loop is now blocked reading stdin
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
    assert proc.stderr.read().count(b"STOP RAN") == 1
    proc.stdin.close()
