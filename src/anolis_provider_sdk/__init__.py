"""Anolis provider SDK for Python.

Declare device types as data, implement ``devices``, ``read`` and ``call`` on a
``Provider`` subclass, and end the module with ``sys.exit(run(MyProvider))``.
See docs/writing-a-provider.md.
"""

from ._version import __version__
from .cli import run
from .model import (
    Arg,
    Category,
    Device,
    DeviceHealth,
    DeviceState,
    DeviceType,
    Function,
    Health,
    HealthState,
    HostRequirement,
    Provider,
    ProviderError,
    Quality,
    Reading,
    Signal,
    StatusCode,
    ValueType,
)
from .session import Session, serve

__all__ = [
    "Arg",
    "Category",
    "Device",
    "DeviceHealth",
    "DeviceState",
    "DeviceType",
    "Function",
    "Health",
    "HealthState",
    "HostRequirement",
    "Provider",
    "ProviderError",
    "Quality",
    "Reading",
    "Session",
    "Signal",
    "StatusCode",
    "ValueType",
    "__version__",
    "run",
    "serve",
]
