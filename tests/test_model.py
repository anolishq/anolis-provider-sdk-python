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


@pytest.mark.parametrize(
    "vtype,lo,hi",
    [
        (ValueType.UINT64, -1, None),
        (ValueType.UINT64, None, 2**64),
        (ValueType.INT64, -(2**63) - 1, None),
        (ValueType.INT64, None, 2**63),
        (ValueType.INT64, None, float(2**63)),  # rounds to 2**63, one past INT64_MAX
    ],
)
def test_integer_bounds_must_fit_the_wire_type(vtype: ValueType, lo: float | None, hi: float | None) -> None:
    with pytest.raises(ValueError, match="does not fit"):
        Arg("x", vtype, min=lo, max=hi)


def test_full_range_integer_bounds_are_describable() -> None:
    from anolis.deviceprovider.v1 import types_pb2

    from anolis_provider_sdk.session import _fill_arg

    for arg in (
        Arg("u", ValueType.UINT64, min=0, max=2**64 - 1),
        Arg("i", ValueType.INT64, min=-(2**63), max=2**63 - 1),
    ):
        _fill_arg(arg, types_pb2.ArgSpec())  # raises ValueError if the field cannot hold the bound


def test_reference_actuate_functions_can_be_driven_to_zero() -> None:
    # The runtime's zero_is_safe rung calls an ACTUATE function with every required
    # argument set to 0, and refuses one with none or with a non-numeric one.
    from anolis_provider_sdk import Category
    from anolis_provider_sdk.reference import COUNTER

    for f in COUNTER.functions:
        if f.category is Category.ACTUATE:
            required = [a for a in f.args if a.required]
            assert required and all(a.type.numeric for a in required), f.name
