"""The reference provider: a synthetic ``counter`` device and no hardware.

It is the SDK's own conformance fixture and a worked example. Its device type is
chosen so that every generic test in the anolis-protocol harness has something
to exercise: a bounded double, bounded int64 and uint64 arguments, a function
with a declared result, a function with no arguments, and default and
non-default signals.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import Any

from ._version import __version__
from .cli import run
from .model import Arg, Category, Device, DeviceType, Function, Provider, Signal, ValueType

COUNTER = DeviceType(
    id="counter",
    signals=(
        Signal("count", ValueType.INT64, description="Current count"),
        Signal("step", ValueType.INT64, description="Amount added by increment"),
        Signal("rate", ValueType.DOUBLE, unit="1/s", description="Nominal rate"),
        Signal("enabled", ValueType.BOOL, description="Whether increment changes the count"),
        Signal("limit", ValueType.UINT64, description="Largest allowed count", default=False),
        Signal("name", ValueType.STRING, description="Device label", default=False, poll_hint_hz=0.0),
    ),
    functions=(
        Function("reset", description="Set the count to zero", category=Category.CONFIG),
        Function(
            "increment",
            description="Add step to the count when enabled",
            results=(Arg("count", ValueType.INT64, description="The count after the call"),),
            category=Category.CONFIG,
        ),
        Function(
            "set_step",
            args=(Arg("step", ValueType.INT64, min=-1000, max=1000, description="Amount per increment"),),
            category=Category.CONFIG,
        ),
        Function(
            "set_rate",
            args=(Arg("rate", ValueType.DOUBLE, min=0.0, max=100.0, unit="1/s", description="Nominal rate"),),
            category=Category.CONFIG,
        ),
        Function(
            "set_limit",
            args=(Arg("limit", ValueType.UINT64, min=1, max=1_000_000, description="Largest allowed count"),),
            category=Category.CONFIG,
        ),
        # CONFIG, not ACTUATE: the runtime's zero_is_safe rung can only drive an
        # ACTUATE function through a required numeric argument, and this one has none.
        Function("set_enabled", args=(Arg("enabled", ValueType.BOOL),), category=Category.CONFIG),
    ),
)


class Counter:
    def __init__(self, start: int) -> None:
        self.count = start
        self.step = 1
        self.rate = 1.0
        self.enabled = True
        self.limit = 1_000_000


class ReferenceProvider(Provider):
    name = "anolis-provider-reference"
    version = __version__
    conformance_level = 2
    config_schema = {
        "title": "anolis-provider-reference config",
        "type": "object",
        "properties": {
            "devices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                        "label": {"type": "string"},
                        "start": {"type": "integer", "minimum": 0},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }

    @classmethod
    def check_config(cls, config: Mapping[str, Any]) -> None:
        seen: set[str] = set()
        for entry in config.get("devices", []):
            if entry["id"] in seen:
                raise ValueError(f"device id {entry['id']!r} is configured twice")
            seen.add(entry["id"])

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(config)
        entries = config.get("devices", [])
        self._devices = [Device(e["id"], COUNTER, label=e.get("label", e["id"])) for e in entries]
        self._state = {e["id"]: Counter(e.get("start", 0)) for e in entries}

    def devices(self) -> Sequence[Device]:
        return self._devices

    def read(self, device: Device, signal_ids: list[str]) -> Mapping[str, Any]:
        c = self._state[device.id]
        values = {
            "count": c.count,
            "step": c.step,
            "rate": c.rate,
            "enabled": c.enabled,
            "limit": c.limit,
            "name": device.label,
        }
        return {sid: values[sid] for sid in signal_ids}

    def call(self, device: Device, function: Function, args: Mapping[str, Any]) -> Mapping[str, Any] | None:
        c = self._state[device.id]
        if function.name == "reset":
            c.count = 0
        elif function.name == "increment":
            if c.enabled:
                c.count = max(-c.limit, min(c.limit, c.count + c.step))
            return {"count": c.count}
        elif function.name == "set_step":
            c.step = args["step"]
        elif function.name == "set_rate":
            c.rate = args["rate"]
        elif function.name == "set_limit":
            c.limit = args["limit"]
        elif function.name == "set_enabled":
            c.enabled = args["enabled"]
        return None


def main() -> None:
    sys.exit(run(ReferenceProvider))
