"""Conversion between ADPP Value messages and Python values, by declared type."""

from __future__ import annotations

import math
from typing import Any

from anolis.deviceprovider.v1 import value_pb2

from .model import ValueType

_FIELD = {
    ValueType.BOOL: "bool_value",
    ValueType.INT64: "int64_value",
    ValueType.UINT64: "uint64_value",
    ValueType.DOUBLE: "double_value",
    ValueType.STRING: "string_value",
    ValueType.BYTES: "bytes_value",
}
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1
_UINT64_MAX = 2**64 - 1


def to_value(vtype: ValueType, py: Any) -> Any:
    """Build a Value of the declared type; TypeError when ``py`` does not fit it."""
    v = value_pb2.Value(type=int(vtype))
    if vtype is ValueType.BOOL and isinstance(py, bool):
        v.bool_value = py
    elif (
        vtype is ValueType.INT64 and isinstance(py, int) and not isinstance(py, bool) and _INT64_MIN <= py <= _INT64_MAX
    ):
        v.int64_value = py
    elif vtype is ValueType.UINT64 and isinstance(py, int) and not isinstance(py, bool) and 0 <= py <= _UINT64_MAX:
        v.uint64_value = py
    elif vtype is ValueType.DOUBLE and isinstance(py, (int, float)) and not isinstance(py, bool):
        v.double_value = float(py)
    elif vtype is ValueType.STRING and isinstance(py, str):
        v.string_value = py
    elif vtype is ValueType.BYTES and isinstance(py, (bytes, bytearray)):
        v.bytes_value = bytes(py)
    else:
        raise TypeError(f"{py!r} is not a valid {vtype.name}")
    return v


def from_value(vtype: ValueType, value: Any) -> Any:
    """The Python value of a Value that must be of the declared type; TypeError otherwise."""
    kind = value.WhichOneof("kind")
    if value.type not in (int(vtype), value_pb2.VALUE_TYPE_UNSPECIFIED) or kind not in (_FIELD[vtype], None):
        got = value_pb2.ValueType.Name(value.type) if value.type in value_pb2.ValueType.values() else value.type
        raise TypeError(f"expects {vtype.name}, got {got}")
    if kind is None and value.type == value_pb2.VALUE_TYPE_UNSPECIFIED:
        raise TypeError(f"expects {vtype.name}, got an empty value")
    return getattr(value, _FIELD[vtype])


def is_finite(py: Any) -> bool:
    return not isinstance(py, float) or math.isfinite(py)
