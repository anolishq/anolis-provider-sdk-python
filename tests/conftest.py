"""Shared fixtures: a small provider declared with the SDK, and a session over it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from anolis_provider_sdk import (
    Arg,
    Device,
    DeviceType,
    Function,
    Provider,
    ProviderError,
    Session,
    Signal,
    StatusCode,
    ValueType,
)

PUMP = DeviceType(
    id="pump",
    signals=(
        Signal("flow", ValueType.DOUBLE, unit="L/min"),
        Signal("running", ValueType.BOOL),
        Signal("serial", ValueType.STRING, default=False),
    ),
    functions=(
        Function("start"),
        Function(
            "set_flow",
            args=(Arg("flow", ValueType.DOUBLE, min=0.0, max=10.0),),
            results=(Arg("flow", ValueType.DOUBLE),),
        ),
        Function("set_count", args=(Arg("count", ValueType.INT64, min=-5, max=5),)),
        Function("set_limit", args=(Arg("limit", ValueType.UINT64, min=1, max=100),)),
        Function(
            "set_mode",
            args=(
                Arg("mode", ValueType.STRING, allowed_values=("auto", "manual")),
                Arg("note", ValueType.STRING, required=False),
            ),
        ),
    ),
)


class PumpProvider(Provider):
    name = "anolis-provider-test"
    version = "1.2.3"
    config_schema = {"type": "object", "properties": {"fail_read": {"type": "boolean"}}, "additionalProperties": False}

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(config)
        self.flow = 1.5
        self.running = False
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def devices(self) -> list[Device]:
        return [Device("pump0", PUMP, label="Pump 0")]

    def read(self, device: Device, signal_ids: list[str]) -> Mapping[str, Any]:
        if self.config.get("fail_read"):
            raise ProviderError(StatusCode.UNAVAILABLE, "bus down")
        values = {"flow": self.flow, "running": self.running, "serial": "SN-1"}
        return {sid: values[sid] for sid in signal_ids}

    def call(self, device: Device, function: Function, args: Mapping[str, Any]) -> Mapping[str, Any] | None:
        self.calls.append((device.id, function.name, dict(args)))
        if function.name == "start":
            self.running = True
        if function.name == "set_flow":
            self.flow = args["flow"]
            return {"flow": self.flow}
        return None


@pytest.fixture
def provider() -> PumpProvider:
    return PumpProvider({})


@pytest.fixture
def session(provider: PumpProvider) -> Session:
    return Session(provider)
