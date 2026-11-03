"""What the installed package carries."""

from __future__ import annotations

from importlib.resources import files


def test_the_package_is_marked_typed() -> None:
    # PEP 561: without the marker, mypy treats the SDK as untyped in a provider
    # that imports it, and a strict provider cannot subclass Provider.
    assert files("anolis_provider_sdk").joinpath("py.typed").is_file()
