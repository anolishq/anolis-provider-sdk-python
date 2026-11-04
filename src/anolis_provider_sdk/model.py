"""What a provider declares, and the base class it implements.

A provider declares each device type once, as data: its signals, its functions
and their argument specs. The SDK derives everything ADPP needs from that one
declaration (function ids, the default signal set, argument validation), so the
capabilities a client sees and the checks the SDK applies cannot drift apart.
"""

from __future__ import annotations

import enum
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from anolis.deviceprovider.v1 import status_pb2, telemetry_pb2, types_pb2, value_pb2

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


class ValueType(enum.IntEnum):
    BOOL = value_pb2.VALUE_TYPE_BOOL
    INT64 = value_pb2.VALUE_TYPE_INT64
    UINT64 = value_pb2.VALUE_TYPE_UINT64
    DOUBLE = value_pb2.VALUE_TYPE_DOUBLE
    STRING = value_pb2.VALUE_TYPE_STRING
    BYTES = value_pb2.VALUE_TYPE_BYTES

    @property
    def numeric(self) -> bool:
        return self in (ValueType.INT64, ValueType.UINT64, ValueType.DOUBLE)


class Category(enum.IntEnum):
    """FunctionPolicy.Category: what a call does, for the runtime's mode policy."""

    UNSPECIFIED = types_pb2.FunctionPolicy.CATEGORY_UNSPECIFIED
    READ = types_pb2.FunctionPolicy.CATEGORY_READ
    CONFIG = types_pb2.FunctionPolicy.CATEGORY_CONFIG
    ACTUATE = types_pb2.FunctionPolicy.CATEGORY_ACTUATE


class Quality(enum.IntEnum):
    OK = telemetry_pb2.SignalValue.QUALITY_OK
    STALE = telemetry_pb2.SignalValue.QUALITY_STALE
    FAULT = telemetry_pb2.SignalValue.QUALITY_FAULT
    UNKNOWN = telemetry_pb2.SignalValue.QUALITY_UNKNOWN


class StatusCode(enum.IntEnum):
    INVALID_ARGUMENT = status_pb2.Status.CODE_INVALID_ARGUMENT
    NOT_FOUND = status_pb2.Status.CODE_NOT_FOUND
    FAILED_PRECONDITION = status_pb2.Status.CODE_FAILED_PRECONDITION
    OUT_OF_RANGE = status_pb2.Status.CODE_OUT_OF_RANGE
    UNIMPLEMENTED = status_pb2.Status.CODE_UNIMPLEMENTED
    DEADLINE_EXCEEDED = status_pb2.Status.CODE_DEADLINE_EXCEEDED
    UNAVAILABLE = status_pb2.Status.CODE_UNAVAILABLE
    RESOURCE_EXHAUSTED = status_pb2.Status.CODE_RESOURCE_EXHAUSTED
    INTERNAL = status_pb2.Status.CODE_INTERNAL
    DATA_LOSS = status_pb2.Status.CODE_DATA_LOSS


class ProviderError(Exception):
    """Raise from read() or call() to answer with a specific ADPP status."""

    def __init__(self, code: StatusCode, message: str, details: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def _unique(kind: str, names: Sequence[str]) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"duplicate {kind} {name!r}")
        seen.add(name)


@dataclass(frozen=True)
class Arg:
    """A function argument or result (ADPP ArgSpec). Bounds are inclusive."""

    name: str
    type: ValueType
    required: bool = True
    min: float | None = None
    max: float | None = None
    unit: str = ""
    description: str = ""
    allowed_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("an argument needs a name")
        if (self.min is not None or self.max is not None) and not self.type.numeric:
            raise ValueError(f"argument {self.name!r}: bounds apply only to numeric types")
        for bound in (self.min, self.max):
            if bound is not None and not math.isfinite(bound):
                raise ValueError(f"argument {self.name!r}: bounds must be finite")
            if bound is not None and self.type in (ValueType.INT64, ValueType.UINT64) and bound != int(bound):
                raise ValueError(f"argument {self.name!r}: bounds on an integer type must be whole numbers")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"argument {self.name!r}: min {self.min} is above max {self.max}")
        if self.allowed_values and self.type is not ValueType.STRING:
            raise ValueError(f"argument {self.name!r}: allowed_values apply only to STRING arguments")


@dataclass(frozen=True)
class Signal:
    """A readable value (ADPP SignalSpec).

    ``default`` signals are returned by a read that names no signals
    (semantics.md §7.2). ``poll_hint_hz`` above 0 is what makes the Anolis
    runtime poll the signal; 0 means it is never polled.
    """

    id: str
    type: ValueType
    unit: str = ""
    description: str = ""
    name: str = ""
    default: bool = True
    poll_hint_hz: float = 1.0
    stale_after_ms: int = 0

    def __post_init__(self) -> None:
        if not _SNAKE_CASE.match(self.id):
            raise ValueError(f"signal id {self.id!r} must be snake_case ([a-z][a-z0-9_]*)")
        if self.poll_hint_hz < 0 or self.stale_after_ms < 0:
            raise ValueError(f"signal {self.id!r}: poll_hint_hz and stale_after_ms must not be negative")


@dataclass(frozen=True)
class Function:
    """A callable operation (ADPP FunctionSpec). Its id is its 1-based position
    in its device type's ``functions``."""

    name: str
    args: tuple[Arg, ...] = ()
    results: tuple[Arg, ...] = ()
    description: str = ""
    category: Category = Category.ACTUATE
    allowed_modes: tuple[str, ...] = ()
    min_interval_ms: int = 0
    is_idempotent: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a function needs a name")
        _unique(f"argument of {self.name!r}", [a.name for a in self.args])
        _unique(f"result of {self.name!r}", [r.name for r in self.results])


@dataclass(frozen=True)
class DeviceType:
    """A kind of device: every device of one type has the same capabilities."""

    id: str
    signals: tuple[Signal, ...] = ()
    functions: tuple[Function, ...] = ()
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a device type needs an id")
        _unique(f"signal in {self.id!r}", [s.id for s in self.signals])
        _unique(f"function in {self.id!r}", [f.name for f in self.functions])
        if self.signals and not any(s.default for s in self.signals):
            raise ValueError(f"device type {self.id!r} declares signals but no default signal (semantics.md §7.2)")

    def function_id(self, function: Function) -> int:
        return self.functions.index(function) + 1

    def function_by_id(self, function_id: int) -> Function | None:
        if 1 <= function_id <= len(self.functions):
            return self.functions[function_id - 1]
        return None

    def function_by_name(self, name: str) -> Function | None:
        return next((f for f in self.functions if f.name == name), None)

    def signal(self, signal_id: str) -> Signal | None:
        return next((s for s in self.signals if s.id == signal_id), None)

    def default_signal_ids(self) -> list[str]:
        return [s.id for s in self.signals if s.default]


@dataclass(frozen=True)
class Device:
    """One device instance. ``id`` must stay stable for the provider process."""

    id: str
    type: DeviceType
    label: str = ""
    address: str = ""
    tags: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Reading:
    """A signal value with its own quality or observation time.

    read() may return a plain value instead; the SDK then stamps the current
    time and QUALITY_OK. A reading older than the signal's ``stale_after_ms``
    is reported as STALE.
    """

    value: Any
    quality: Quality = Quality.OK
    timestamp: float | None = None  # seconds since the epoch


class HealthState(enum.Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAULT = "fault"


@dataclass(frozen=True)
class Health:
    state: HealthState = HealthState.OK
    message: str = ""
    metrics: Mapping[str, str] = field(default_factory=dict)


class DeviceState(enum.Enum):
    OK = "ok"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    FAULT = "fault"


@dataclass(frozen=True)
class DeviceHealth:
    state: DeviceState = DeviceState.OK
    message: str = ""
    metrics: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HostRequirement:
    """One entry of the --check-host envelope (executable profile §6)."""

    id: str
    status: Literal["met", "unmet", "unknown"]
    detail: str = ""
    remedy: str = ""


class Provider:
    """Subclass this, declare ``name``, ``version`` and ``config_schema``, and
    implement ``devices``, ``read`` and ``call``.

    The SDK owns the protocol: framing, the Hello gate, request dispatch, the
    default signal set, argument validation, deadlines, timestamps and the CLI
    verbs. A provider owns its devices and its driver.

    Health: ``health`` and ``device_health`` are answered on the request path,
    so return state you already hold. Do not talk to hardware in them.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    # A JSON Schema (Draft 2020-12) for the provider's config. The SDK validates
    # the config against it at startup and for --check-config, and prints it for
    # --config-schema, so the three cannot disagree.
    config_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    # The conformance level the provider claims, advertised in Hello. Leave None
    # to advertise none (absent means level 1).
    conformance_level: ClassVar[int | None] = None

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = config

    def start(self, budget_s: float) -> None:
        """Called once, after Hello and before the first request after it.

        Put slow start-up here (opening a bus, probing devices) so Hello is
        answered at once. ``budget_s`` is the client's WaitReady hint, or 0.
        """

    def devices(self) -> Sequence[Device]:
        """The devices this provider currently exposes."""
        return []

    def read(self, device: Device, signal_ids: list[str]) -> Mapping[str, Any]:
        """Return a value (or a Reading) for every signal id asked for."""
        raise ProviderError(StatusCode.UNIMPLEMENTED, f"{self.name} does not implement read")

    def call(self, device: Device, function: Function, args: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Carry out a validated call. Return the declared results, if any."""
        raise ProviderError(StatusCode.UNIMPLEMENTED, f"{self.name} does not implement call")

    def health(self) -> Health:
        return Health()

    def device_health(self, device: Device) -> DeviceHealth | None:
        """None lets the SDK report OK with the time of the last successful read."""
        return None

    def stop(self) -> None:
        """Called exactly once when the session ends, however it ends.

        That is stdin EOF (the runtime closed the pipe or went away), a framing
        or decode error, a broken pipe, SIGTERM, or an exception in the loop.
        Drive outputs to their safe state here: on hardware without a watchdog
        they otherwise hold their last value after the provider exits. It may
        run without ``start`` having run. An exception from it is logged and
        does not change the exit code. The default does nothing.
        """

    @classmethod
    def check_config(cls, config: Mapping[str, Any]) -> None:
        """Refuse a config that ``config_schema`` accepts but this provider would not.

        Raise ``ValueError`` with a message (duplicate ids, two devices on one
        address). Called after schema validation, for ``--check-config``,
        ``--check-host`` and before the provider is constructed for
        ``--config``, so all three agree. The default accepts everything.
        """

    @classmethod
    def check_host(cls, config: Mapping[str, Any]) -> Sequence[HostRequirement]:
        """What this config needs from the host. Read-only; see --check-host."""
        return []
