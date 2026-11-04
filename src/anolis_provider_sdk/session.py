"""One ADPP session: a request in, a response out (anolis-protocol docs/semantics.md).

``Session.handle`` holds every protocol rule and does no I/O, so it can be
tested request by request. ``serve`` runs it over framed stdio.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

from anolis.deviceprovider.v1 import envelope_pb2, health_pb2, status_pb2
from google.protobuf.message import DecodeError

from ._version import __version__
from .framing import MAX_FRAME_BYTES, FrameError, read_frame, write_frame
from .model import (
    Arg,
    Device,
    DeviceState,
    Function,
    HealthState,
    Provider,
    ProviderError,
    Quality,
    Reading,
    StatusCode,
    ValueType,
)
from .values import from_value, is_finite, to_value

log = logging.getLogger("anolis_provider_sdk")

PROTOCOL_VERSION = "v1"
_OK = status_pb2.Status.CODE_OK
# Valid google.protobuf.Timestamp range (0001-01-01 .. 9999-12-31).
_TS_MIN_SECONDS, _TS_MAX_SECONDS = -62135596800, 253402300799
_PROVIDER_STATE = {
    HealthState.OK: health_pb2.ProviderHealth.STATE_OK,
    HealthState.DEGRADED: health_pb2.ProviderHealth.STATE_DEGRADED,
    HealthState.FAULT: health_pb2.ProviderHealth.STATE_FAULT,
}
_DEVICE_STATE = {
    DeviceState.OK: health_pb2.DeviceHealth.STATE_OK,
    DeviceState.STALE: health_pb2.DeviceHealth.STATE_STALE,
    DeviceState.UNREACHABLE: health_pb2.DeviceHealth.STATE_UNREACHABLE,
    DeviceState.FAULT: health_pb2.DeviceHealth.STATE_FAULT,
}


def _quote(text: str, limit: int = 64) -> str:
    """Quote client-supplied text for an error message without echoing it unbounded."""
    return repr(text if len(text) <= limit else text[:limit] + "...")


def _fail(code: StatusCode, message: str) -> ProviderError:
    return ProviderError(code, message)


class Session:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self._hello_done = False
        self._started = False
        self._start_error: str | None = None
        # Executable profile §6: the --check-host checks, run again at startup.
        self._host_check: str | None = None  # "ok" or "unmet"; None if they could not run
        self._host_unmet = ""
        self._init_ms = 0
        self._last_seen: dict[str, float] = {}

    # ---- dispatch -------------------------------------------------------------

    def handle(self, request: Any) -> Any:
        response = envelope_pb2.Response(request_id=request.request_id)
        kind = request.WhichOneof("payload")
        try:
            if kind is None:
                raise _fail(StatusCode.INVALID_ARGUMENT, "request carries no payload")
            if kind == "hello":
                self._hello(request.hello, response)
            elif not self._hello_done:
                # semantics.md §3.2: nothing is processed before a successful Hello.
                raise _fail(StatusCode.FAILED_PRECONDITION, f"{kind} before Hello; send Hello first")
            else:
                budget_s = request.wait_ready.max_wait_ms_hint / 1000 if kind == "wait_ready" else 0.0
                self._ensure_started(budget_s)
                getattr(self, f"_{kind}")(getattr(request, kind), response)
            response.status.code = _OK
        except ProviderError as err:
            response.ClearField("payload")
            response.status.code = int(err.code)
            response.status.message = err.message
            response.status.details.update(err.details)
        except Exception as err:  # a provider bug must not take the session down
            log.exception("%s failed", kind)
            response.ClearField("payload")
            response.status.code = int(StatusCode.INTERNAL)
            response.status.message = f"{type(err).__name__}: {err}"[:512]
        return response

    def _ensure_started(self, budget_s: float) -> None:
        if self._started:
            return
        self._started = True
        began = time.monotonic()
        self._run_host_check()
        if self._host_check == "unmet":
            # §6: stay up and not ready, exposing no devices, rather than exit.
            log.error("host requirements unmet: %s", self._host_unmet)
        else:
            try:
                self.provider.start(budget_s)
            except Exception as err:
                log.exception("provider start failed")
                self._start_error = f"{type(err).__name__}: {err}"[:512]
        self._init_ms = int((time.monotonic() - began) * 1000)

    def _run_host_check(self) -> None:
        try:
            requirements = list(type(self.provider).check_host(self.provider.config))
        except Exception:
            # Could not evaluate: like an `unknown` requirement, this does not block start.
            log.exception("host check could not run")
            return
        unmet = [f"{r.id}: {r.detail}" if r.detail else r.id for r in requirements if r.status == "unmet"]
        self._host_check = "unmet" if unmet else "ok"
        self._host_unmet = "; ".join(unmet)[:512]

    def _devices(self) -> Sequence[Device]:
        return [] if self._host_check == "unmet" else self.provider.devices()

    # ---- handshake ------------------------------------------------------------

    def _hello(self, req: Any, resp: Any) -> None:
        if req.protocol_version != PROTOCOL_VERSION:
            raise _fail(
                StatusCode.FAILED_PRECONDITION,
                f"protocol_version {_quote(req.protocol_version)} is not supported; "
                f"this provider speaks {PROTOCOL_VERSION}",
            )
        p = self.provider
        resp.hello.protocol_version = PROTOCOL_VERSION
        resp.hello.provider_name = p.name
        resp.hello.provider_version = p.version
        md = resp.hello.metadata
        md["transport"] = "stdio+uint32_le"
        md["max_frame_bytes"] = str(MAX_FRAME_BYTES)
        md["supports_wait_ready"] = "true"
        md["supports_deadlines"] = "true"
        if p.conformance_level is not None:
            md["conformance_level"] = str(p.conformance_level)
        self._hello_done = True

    def _wait_ready(self, req: Any, resp: Any) -> None:
        d = resp.wait_ready.diagnostics
        d["init_time_ms"] = str(self._init_ms)
        d["ready"] = "false" if self._start_error or self._host_check == "unmet" else "true"
        d["device_count"] = str(len(self._devices()))
        d["provider_impl"] = f"{self.provider.name} {self.provider.version} (anolis-provider-sdk {__version__})"
        if self._host_check is not None:
            d["host_check"] = self._host_check
        if self._host_unmet:
            d["host_unmet"] = self._host_unmet
        if self._start_error:
            d["start_error"] = self._start_error

    # ---- inventory ------------------------------------------------------------

    def _device(self, device_id: str) -> Device:
        for device in self._devices():
            if device.id == device_id:
                return device
        raise _fail(StatusCode.NOT_FOUND, f"unknown device_id {_quote(device_id)}")

    def _fill_device(self, device: Device, out: Any) -> None:
        out.device_id = device.id
        out.provider_name = self.provider.name
        out.type_id = device.type.id
        out.type_version = device.type.version
        out.label = device.label
        out.address = device.address
        out.tags.update(device.tags)

    def _list_devices(self, req: Any, resp: Any) -> None:
        resp.list_devices.SetInParent()
        for device in self._devices():
            self._fill_device(device, resp.list_devices.devices.add())
            if req.include_health:
                self._fill_device_health(device, resp.list_devices.device_health.add())

    def _describe_device(self, req: Any, resp: Any) -> None:
        device = self._device(req.device_id)
        self._fill_device(device, resp.describe_device.device)
        caps = resp.describe_device.capabilities
        caps.SetInParent()
        for s in device.type.signals:
            spec = caps.signals.add()
            spec.signal_id, spec.name, spec.description = s.id, s.name or s.id, s.description
            spec.value_type, spec.unit = int(s.type), s.unit
            spec.poll_hint_hz, spec.stale_after_ms = s.poll_hint_hz, s.stale_after_ms
        for f in device.type.functions:
            spec = caps.functions.add()
            spec.function_id, spec.name, spec.description = device.type.function_id(f), f.name, f.description
            spec.policy.category = int(f.category)
            spec.policy.allowed_modes.extend(f.allowed_modes)
            spec.policy.min_interval_ms = f.min_interval_ms
            spec.policy.is_idempotent = f.is_idempotent
            for a in f.args:
                _fill_arg(a, spec.args.add())
            for r in f.results:
                _fill_arg(r, spec.results.add())

    # ---- reads ----------------------------------------------------------------

    def _read_signals(self, req: Any, resp: Any) -> None:
        device = self._device(req.device_id)
        signal_ids = list(req.signal_ids) or device.type.default_signal_ids()
        unknown = [sid for sid in signal_ids if device.type.signal(sid) is None]
        if unknown:  # semantics.md §7.4: one consistent behaviour, NOT_FOUND
            raise _fail(StatusCode.NOT_FOUND, f"unknown signal_id {_quote(unknown[0])} on device {_quote(device.id)}")
        out = resp.read_signals
        out.device_id = device.id
        if not signal_ids:
            return
        values = self.provider.read(device, signal_ids)
        now = time.time()
        for sid in signal_ids:
            signal = device.type.signal(sid)
            assert signal is not None
            if sid not in values:
                raise _fail(StatusCode.INTERNAL, f"provider returned no value for signal {sid!r}")
            raw = values[sid]
            reading = raw if isinstance(raw, Reading) else Reading(raw)
            try:
                value = to_value(signal.type, reading.value)
            except TypeError as err:
                raise _fail(StatusCode.INTERNAL, f"signal {sid!r}: {err}") from None
            observed = reading.timestamp if reading.timestamp is not None else now
            quality = reading.quality
            if quality is Quality.OK and signal.stale_after_ms and (now - observed) * 1000 > signal.stale_after_ms:
                quality = Quality.STALE
            sv = out.values.add()
            sv.signal_id = sid
            sv.value.CopyFrom(value)
            sv.timestamp.FromNanoseconds(int(observed * 1e9))
            sv.quality = int(quality)
        self._last_seen[device.id] = now

    # ---- calls ----------------------------------------------------------------

    def _call(self, req: Any, resp: Any) -> None:
        device = self._device(req.device_id)
        function = self._resolve_function(device, req)
        args = _validate_args(function, req.args)
        deadline = _deadline(req) if req.HasField("deadline") else None
        if deadline is not None and time.time() >= deadline:
            raise _fail(StatusCode.DEADLINE_EXCEEDED, "deadline expired before the call started; nothing was done")
        results = self.provider.call(device, function, args) or {}
        if deadline is not None and time.time() > deadline:
            raise ProviderError(
                StatusCode.DEADLINE_EXCEEDED,
                "deadline expired while the call ran; it completed, so its effects may have happened",
                {"side_effects": "possible"},
            )
        out = resp.call
        out.device_id = device.id
        declared = {r.name: r for r in function.results}
        extra = sorted(set(results) - set(declared))
        if extra:
            raise _fail(StatusCode.INTERNAL, f"{function.name} returned undeclared results {extra}")
        for name, spec in declared.items():
            if name not in results:
                raise _fail(StatusCode.INTERNAL, f"{function.name} declares result {name!r} but did not return it")
            try:
                out.results[name].CopyFrom(to_value(spec.type, results[name]))
            except TypeError as err:
                raise _fail(StatusCode.INTERNAL, f"{function.name} result {name!r}: {err}") from None

    def _resolve_function(self, device: Device, req: Any) -> Function:
        # §6.2: a nonzero function_id wins and function_name is then ignored.
        if req.function_id:
            function = device.type.function_by_id(req.function_id)
            if function is None:
                raise _fail(
                    StatusCode.NOT_FOUND, f"unknown function_id {req.function_id} on device {_quote(device.id)}"
                )
            return function
        if req.function_name:
            function = device.type.function_by_name(req.function_name)
            if function is None:
                raise _fail(
                    StatusCode.NOT_FOUND,
                    f"unknown function_name {_quote(req.function_name)} on device {_quote(device.id)}",
                )
            return function
        raise _fail(StatusCode.NOT_FOUND, "the call names no function (function_id 0 and no function_name)")

    # ---- health ---------------------------------------------------------------

    def _fill_device_health(self, device: Device, out: Any) -> None:
        out.device_id = device.id
        reported = self.provider.device_health(device)
        if reported is None:
            out.state = health_pb2.DeviceHealth.STATE_OK
        else:
            out.state = _DEVICE_STATE[reported.state]
            out.message = reported.message
            out.metrics.update(reported.metrics)
        seen = self._last_seen.get(device.id)
        if seen is not None:
            out.last_seen.FromNanoseconds(int(seen * 1e9))

    def _get_health(self, req: Any, resp: Any) -> None:
        health = self.provider.health()
        provider = resp.get_health.provider
        if self._host_check == "unmet":
            provider.state = health_pb2.ProviderHealth.STATE_FAULT
            provider.message = f"host requirements unmet: {self._host_unmet}"
        elif self._start_error:
            provider.state = health_pb2.ProviderHealth.STATE_FAULT
            provider.message = f"start failed: {self._start_error}"
        else:
            provider.state = _PROVIDER_STATE[health.state]
            provider.message = health.message
        provider.metrics.update(health.metrics)
        for device in self._devices():
            self._fill_device_health(device, resp.get_health.devices.add())


def _fill_arg(arg: Arg, out: Any) -> None:
    out.name, out.type, out.unit, out.description, out.required = (
        arg.name,
        int(arg.type),
        arg.unit,
        arg.description,
        arg.required,
    )
    out.allowed_values.extend(arg.allowed_values)
    suffix = {ValueType.DOUBLE: "double", ValueType.INT64: "int64", ValueType.UINT64: "uint64"}.get(arg.type)
    if suffix is None:
        return
    cast = float if arg.type is ValueType.DOUBLE else int
    if arg.min is not None:
        setattr(out, f"min_{suffix}", cast(arg.min))
    if arg.max is not None:
        setattr(out, f"max_{suffix}", cast(arg.max))


def _validate_args(function: Function, raw: Mapping[str, Any]) -> dict[str, Any]:
    """semantics.md §8.3: required, type, finite, then inclusive bounds."""
    declared = {a.name: a for a in function.args}
    undeclared = sorted(set(raw) - set(declared))
    if undeclared:
        raise _fail(StatusCode.INVALID_ARGUMENT, f"{function.name} takes no argument {_quote(undeclared[0])}")
    args: dict[str, Any] = {}
    for arg in function.args:
        if arg.name not in raw:
            if arg.required:
                raise _fail(StatusCode.INVALID_ARGUMENT, f"{function.name} requires argument {arg.name!r}")
            continue
        try:
            value = from_value(arg.type, raw[arg.name])
        except TypeError as err:
            raise _fail(StatusCode.INVALID_ARGUMENT, f"argument {arg.name!r} {err}") from None
        if not is_finite(value):
            raise _fail(StatusCode.INVALID_ARGUMENT, f"argument {arg.name!r} must be finite, got {value}")
        if (arg.min is not None and value < arg.min) or (arg.max is not None and value > arg.max):
            raise _fail(StatusCode.OUT_OF_RANGE, f"argument {arg.name!r} = {value} is outside [{arg.min}, {arg.max}]")
        if arg.allowed_values and value not in arg.allowed_values:
            raise _fail(StatusCode.INVALID_ARGUMENT, f"argument {arg.name!r} must be one of {list(arg.allowed_values)}")
        args[arg.name] = value
    return args


def _deadline(req: Any) -> float:
    ts = req.deadline
    if not (0 <= ts.nanos <= 999_999_999) or not (_TS_MIN_SECONDS <= ts.seconds <= _TS_MAX_SECONDS):
        raise _fail(StatusCode.INVALID_ARGUMENT, "deadline is not a valid timestamp")
    return float(ts.seconds + ts.nanos / 1e9)


class _Terminated(BaseException):
    """Raised by the SIGTERM handler. A BaseException, so that neither the
    session's error handling nor a provider's ``except Exception`` swallows it."""


def serve(provider: Provider, stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Run a session over framed stdio until EOF. Returns the process exit code:
    0 on a clean EOF, 2 on a framing error, 3 on an unparseable request
    (framed-stdio v1 §4), and 0 on SIGTERM, which ends the session in order.

    ``provider.stop()`` runs exactly once on every way out of the loop. SIGTERM
    is handled only when ``serve`` runs on the main thread, where Python
    delivers signals; the previous handler is restored on return.
    """
    previous: Any = None
    on_main_thread = threading.current_thread() is threading.main_thread()
    terminating = False

    def on_sigterm(signum: int, frame: Any) -> None:
        nonlocal terminating
        if not terminating:  # a second SIGTERM must not interrupt stop()
            terminating = True
            raise _Terminated

    if on_main_thread:
        previous = signal.signal(signal.SIGTERM, on_sigterm)
    try:
        return _loop(Session(provider), stdin, stdout)
    except _Terminated:
        log.info("SIGTERM: ending the session")
        return 0
    finally:
        terminating = True
        try:
            provider.stop()
        except Exception:
            log.exception("provider stop failed")
        if on_main_thread:
            signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


def _loop(session: Session, stdin: BinaryIO, stdout: BinaryIO) -> int:
    while True:
        try:
            payload = read_frame(stdin)
        except FrameError as err:
            log.error("framing error: %s", err)
            return 2
        if payload is None:
            return 0
        request = envelope_pb2.Request()
        try:
            request.ParseFromString(payload)
        except DecodeError as err:
            log.error("unparseable request: %s", err)
            return 3
        data = session.handle(request).SerializeToString()
        if len(data) > MAX_FRAME_BYTES:
            too_big = envelope_pb2.Response(request_id=request.request_id)
            too_big.status.code = int(StatusCode.RESOURCE_EXHAUSTED)
            too_big.status.message = f"response of {len(data)} bytes exceeds the {MAX_FRAME_BYTES}-byte frame limit"
            data = too_big.SerializeToString()
        try:
            write_frame(stdout, data)
        except BrokenPipeError:
            return 0


__all__ = ["Session", "serve", "PROTOCOL_VERSION"]
