"""Session.handle: ADPP v1 semantics, request by request, with no I/O."""

from __future__ import annotations

import math
import time
from typing import Any

import pytest
from anolis.deviceprovider.v1 import envelope_pb2, health_pb2, status_pb2, telemetry_pb2, value_pb2

from anolis_provider_sdk import Session

from .conftest import PumpProvider

Code = status_pb2.Status.Code
OK = Code.Value("CODE_OK")
INVALID = Code.Value("CODE_INVALID_ARGUMENT")
NOT_FOUND = Code.Value("CODE_NOT_FOUND")
PRECONDITION = Code.Value("CODE_FAILED_PRECONDITION")
OUT_OF_RANGE = Code.Value("CODE_OUT_OF_RANGE")
DEADLINE = Code.Value("CODE_DEADLINE_EXCEEDED")
UNAVAILABLE = Code.Value("CODE_UNAVAILABLE")
INTERNAL = Code.Value("CODE_INTERNAL")


def hello(session: Session, version: str = "v1") -> Any:
    req = envelope_pb2.Request(request_id=1)
    req.hello.protocol_version = version
    req.hello.client_name = "test"
    return session.handle(req)


def ready(session: Session) -> Session:
    assert hello(session).status.code == OK
    return session


def call(session: Session, *, fid: int = 0, name: str = "", device: str = "pump0", **args: Any) -> Any:
    req = envelope_pb2.Request(request_id=7)
    req.call.device_id = device
    req.call.function_id = fid
    req.call.function_name = name
    for key, raw in args.items():
        req.call.args[key].CopyFrom(raw)
    return session.handle(req)


def double(v: float) -> Any:
    return value_pb2.Value(type=value_pb2.VALUE_TYPE_DOUBLE, double_value=v)


def int64(v: int) -> Any:
    return value_pb2.Value(type=value_pb2.VALUE_TYPE_INT64, int64_value=v)


def uint64(v: int) -> Any:
    return value_pb2.Value(type=value_pb2.VALUE_TYPE_UINT64, uint64_value=v)


def string(v: str) -> Any:
    return value_pb2.Value(type=value_pb2.VALUE_TYPE_STRING, string_value=v)


# ---- handshake ---------------------------------------------------------------


def test_hello_advertises_the_framed_stdio_profile(session: Session) -> None:
    resp = hello(session)
    assert resp.request_id == 1 and resp.status.code == OK
    assert resp.hello.protocol_version == "v1"
    assert resp.hello.provider_name == "anolis-provider-test"
    assert resp.hello.provider_version == "1.2.3"
    md = dict(resp.hello.metadata)
    assert md["transport"] == "stdio+uint32_le"
    assert md["max_frame_bytes"] == "1048576"
    assert md["supports_wait_ready"] == "true"
    assert md["supports_deadlines"] == "true"
    assert "conformance_level" not in md  # advertised only when the provider declares one


def test_unsupported_version_is_refused(session: Session) -> None:
    resp = hello(session, "v999")
    assert resp.status.code == PRECONDITION
    # ... and the session is still not established.
    req = envelope_pb2.Request(request_id=2)
    req.list_devices.SetInParent()
    assert session.handle(req).status.code == PRECONDITION


@pytest.mark.parametrize(
    "kind", ["list_devices", "describe_device", "read_signals", "call", "wait_ready", "get_health"]
)
def test_requests_before_hello_are_refused_unprocessed(session: Session, provider: PumpProvider, kind: str) -> None:
    req = envelope_pb2.Request(request_id=3)
    getattr(req, kind).SetInParent()
    if kind == "call":
        req.call.device_id = "pump0"
        req.call.function_id = 1
    resp = session.handle(req)
    assert resp.request_id == 3 and resp.status.code == PRECONDITION
    assert provider.calls == []


def test_request_without_a_payload_is_invalid(session: Session) -> None:
    ready(session)
    assert session.handle(envelope_pb2.Request(request_id=9)).status.code == INVALID


def test_wait_ready_reports_init_time_and_runs_the_start_hook_once(session: Session) -> None:
    ready(session)
    started: list[float] = []
    session.provider.start = lambda budget_s: started.append(budget_s)  # type: ignore[method-assign]
    req = envelope_pb2.Request(request_id=4)
    req.wait_ready.max_wait_ms_hint = 2500
    resp = session.handle(req)
    assert resp.status.code == OK
    diags = dict(resp.wait_ready.diagnostics)
    assert int(diags["init_time_ms"]) >= 0
    assert diags["ready"] == "true" and diags["device_count"] == "1"
    session.handle(req)
    assert started == [2.5]


# ---- inventory ---------------------------------------------------------------


def test_list_devices_with_health(session: Session) -> None:
    ready(session)
    req = envelope_pb2.Request(request_id=5)
    req.list_devices.include_health = True
    resp = session.handle(req)
    assert [d.device_id for d in resp.list_devices.devices] == ["pump0"]
    dev = resp.list_devices.devices[0]
    assert (dev.provider_name, dev.type_id, dev.label) == ("anolis-provider-test", "pump", "Pump 0")
    assert [h.device_id for h in resp.list_devices.device_health] == ["pump0"]
    assert resp.list_devices.device_health[0].state == health_pb2.DeviceHealth.STATE_OK


def test_describe_device_carries_ids_types_bounds(session: Session) -> None:
    ready(session)
    req = envelope_pb2.Request(request_id=6)
    req.describe_device.device_id = "pump0"
    caps = session.handle(req).describe_device.capabilities
    assert [s.signal_id for s in caps.signals] == ["flow", "running", "serial"]
    assert caps.signals[0].value_type == value_pb2.VALUE_TYPE_DOUBLE and caps.signals[0].unit == "L/min"
    assert caps.signals[0].poll_hint_hz > 0
    fns = {f.name: f for f in caps.functions}
    assert [f.function_id for f in caps.functions] == [1, 2, 3, 4, 5]
    arg = fns["set_flow"].args[0]
    assert arg.required and arg.HasField("min_double") and (arg.min_double, arg.max_double) == (0.0, 10.0)
    assert [r.name for r in fns["set_flow"].results] == ["flow"]
    assert fns["set_count"].args[0].min_int64 == -5 and fns["set_limit"].args[0].max_uint64 == 100
    assert list(fns["set_mode"].args[0].allowed_values) == ["auto", "manual"]
    assert not fns["set_mode"].args[1].required


def test_describe_unknown_device_is_not_found(session: Session) -> None:
    ready(session)
    req = envelope_pb2.Request(request_id=6)
    req.describe_device.device_id = "ghost"
    assert session.handle(req).status.code == NOT_FOUND


# ---- reads -------------------------------------------------------------------


def read(session: Session, *ids: str, device: str = "pump0") -> Any:
    req = envelope_pb2.Request(request_id=8)
    req.read_signals.device_id = device
    req.read_signals.signal_ids.extend(ids)
    return session.handle(req)


def test_default_read_returns_the_default_signals_stamped(session: Session) -> None:
    ready(session)
    before = time.time()
    resp = read(session)
    assert resp.status.code == OK
    vals = {v.signal_id: v for v in resp.read_signals.values}
    assert sorted(vals) == ["flow", "running"]
    assert vals["flow"].value.double_value == 1.5 and vals["running"].value.bool_value is False
    for v in vals.values():
        assert v.quality == telemetry_pb2.SignalValue.QUALITY_OK
        assert v.timestamp.seconds >= int(before) - 1


def test_explicit_read_and_unknown_signal(session: Session) -> None:
    ready(session)
    assert [v.signal_id for v in read(session, "serial").read_signals.values] == ["serial"]
    assert read(session, "flow", "ghost").status.code == NOT_FOUND
    assert read(session, device="ghost").status.code == NOT_FOUND


def test_provider_error_maps_to_its_status(session: Session) -> None:
    session.provider.config = {"fail_read": True}
    ready(session)
    resp = read(session)
    assert resp.status.code == UNAVAILABLE and "bus down" in resp.status.message


# ---- calls -------------------------------------------------------------------


def test_call_by_id_and_by_name_with_results(session: Session, provider: PumpProvider) -> None:
    ready(session)
    resp = call(session, fid=2, flow=double(4.0))
    assert resp.status.code == OK and resp.call.results["flow"].double_value == 4.0
    resp = call(session, name="start")
    assert resp.status.code == OK and len(resp.call.results) == 0
    assert provider.calls == [("pump0", "set_flow", {"flow": 4.0}), ("pump0", "start", {})]


def test_function_id_wins_over_name(session: Session) -> None:
    ready(session)
    assert call(session, fid=1, name="no_such_function").status.code == OK


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"device": "ghost", "fid": 1}, NOT_FOUND),
        ({"fid": 99}, NOT_FOUND),
        ({"name": "nope"}, NOT_FOUND),
        ({"fid": 0}, NOT_FOUND),
        ({"fid": 2}, INVALID),  # missing required
        ({"fid": 2, "flow": int64(3)}, INVALID),  # wrong type
        ({"fid": 2, "flow": double(3.0), "extra": double(1.0)}, INVALID),  # undeclared arg
        ({"fid": 2, "flow": double(10.5)}, OUT_OF_RANGE),
        ({"fid": 2, "flow": double(-0.1)}, OUT_OF_RANGE),
        ({"fid": 2, "flow": double(math.nan)}, INVALID),
        ({"fid": 2, "flow": double(math.inf)}, INVALID),  # finiteness before bounds
        ({"fid": 3, "count": int64(6)}, OUT_OF_RANGE),
        ({"fid": 4, "limit": uint64(0)}, OUT_OF_RANGE),
        ({"fid": 5, "mode": string("off")}, INVALID),
    ],
)
def test_call_validation(session: Session, provider: PumpProvider, kw: dict[str, Any], code: int) -> None:
    ready(session)
    assert call(session, **kw).status.code == code
    assert provider.calls == []  # never reached the provider


@pytest.mark.parametrize(
    "kw",
    [
        {"fid": 2, "flow": double(0.0)},
        {"fid": 2, "flow": double(10.0)},
        {"fid": 3, "count": int64(-5)},
        {"fid": 5, "mode": string("auto")},
    ],
)
def test_bounds_are_inclusive_and_optional_args_may_be_absent(session: Session, kw: dict[str, Any]) -> None:
    ready(session)
    assert call(session, **kw).status.code == OK


def test_declared_results_must_be_returned(session: Session, provider: PumpProvider) -> None:
    ready(session)
    provider.call = lambda device, function, args: {}  # type: ignore[method-assign]
    resp = call(session, fid=2, flow=double(1.0))
    # The message names the broken promise, so the provider author can fix it.
    assert resp.status.code == INTERNAL and "declares result 'flow'" in resp.status.message


def test_unexpected_exception_is_internal_not_a_crash(session: Session, provider: PumpProvider) -> None:
    ready(session)

    def boom(*_: Any) -> None:
        raise RuntimeError("kaboom")

    provider.call = boom  # type: ignore[method-assign,assignment]
    resp = call(session, fid=1)
    assert resp.status.code == INTERNAL and "kaboom" in resp.status.message


def test_expired_deadline_is_refused_before_the_call(session: Session, provider: PumpProvider) -> None:
    ready(session)
    req = envelope_pb2.Request(request_id=10)
    req.call.device_id, req.call.function_id = "pump0", 1
    req.call.deadline.seconds = 1  # 1970
    assert session.handle(req).status.code == DEADLINE
    req.call.deadline.seconds, req.call.deadline.nanos = int(time.time()) + 60, 2_000_000_000
    assert session.handle(req).status.code == INVALID  # malformed timestamp
    assert provider.calls == []
    req.call.deadline.nanos = 0
    assert session.handle(req).status.code == OK


# ---- health ------------------------------------------------------------------


def test_get_health(session: Session) -> None:
    ready(session)
    read(session)
    req = envelope_pb2.Request(request_id=11)
    req.get_health.SetInParent()
    resp = session.handle(req)
    assert resp.status.code == OK
    assert resp.get_health.provider.state == health_pb2.ProviderHealth.STATE_OK
    (dev,) = resp.get_health.devices
    assert dev.device_id == "pump0" and dev.state == health_pb2.DeviceHealth.STATE_OK
    assert dev.last_seen.seconds > 0  # set by the successful read


def test_long_device_ids_are_not_echoed_unbounded(session: Session) -> None:
    ready(session)
    resp = read(session, device="x" * 5000)
    assert resp.status.code == NOT_FOUND and len(resp.status.message) < 400


def test_failed_start_keeps_serving_and_reports_it(session: Session) -> None:
    ready(session)

    def broken(budget_s: float) -> None:
        raise OSError("no such bus")

    session.provider.start = broken  # type: ignore[method-assign]
    req = envelope_pb2.Request(request_id=12)
    req.wait_ready.SetInParent()
    diags = dict(session.handle(req).wait_ready.diagnostics)
    assert diags["ready"] == "false" and "no such bus" in diags["start_error"]
    req = envelope_pb2.Request(request_id=13)
    req.get_health.SetInParent()
    resp = session.handle(req)
    assert resp.status.code == OK
    assert resp.get_health.provider.state == health_pb2.ProviderHealth.STATE_FAULT
    assert "no such bus" in resp.get_health.provider.message
