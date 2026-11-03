"""Declarations are checked when they are made, so a provider cannot start with
capabilities the executable profile (§5) or semantics.md §7.2 would reject."""

from __future__ import annotations

import pytest

from anolis_provider_sdk import Arg, DeviceType, Function, Signal, ValueType

from .conftest import PUMP


def test_function_ids_are_per_type_from_one_in_declaration_order() -> None:
    assert [PUMP.function_id(f) for f in PUMP.functions] == [1, 2, 3, 4, 5]
    assert PUMP.function_by_id(2) is PUMP.functions[1]
    assert PUMP.function_by_name("set_mode") is PUMP.functions[4]
    assert PUMP.function_by_id(0) is None
    assert PUMP.function_by_id(99) is None
    assert PUMP.function_by_name("nope") is None


def test_default_signals_are_the_declared_defaults() -> None:
    assert PUMP.default_signal_ids() == ["flow", "running"]


@pytest.mark.parametrize("bad", ["Flow", "ph.value", "1st", "trailing-", ""])
def test_signal_ids_must_be_snake_case(bad: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        DeviceType(id="t", signals=(Signal(bad, ValueType.DOUBLE),))


def test_signals_need_at_least_one_default() -> None:
    with pytest.raises(ValueError, match="default"):
        DeviceType(id="t", signals=(Signal("a", ValueType.DOUBLE, default=False),))


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        DeviceType(id="t", signals=(Signal("a", ValueType.DOUBLE), Signal("a", ValueType.BOOL)))
    with pytest.raises(ValueError, match="duplicate"):
        DeviceType(id="t", functions=(Function("f"), Function("f")))
    with pytest.raises(ValueError, match="duplicate"):
        Function("f", args=(Arg("x", ValueType.BOOL), Arg("x", ValueType.BOOL)))


def test_bounds_only_on_numeric_args_and_min_not_above_max() -> None:
    with pytest.raises(ValueError, match="numeric"):
        Arg("s", ValueType.STRING, min=1)
    with pytest.raises(ValueError, match="min"):
        Arg("x", ValueType.DOUBLE, min=2.0, max=1.0)
    with pytest.raises(ValueError, match="allowed_values"):
        Arg("x", ValueType.DOUBLE, allowed_values=("a",))
