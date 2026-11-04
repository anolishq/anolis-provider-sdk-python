"""The provider executable's command line (anolis-protocol
docs/profiles/anolis-executable-profile-v1.md §1, §2, §6).

Exit codes: 0 success; 1 an invalid config (--check-config, --config) or an
unmet host requirement (--check-host); 2 a usage error, or --check-host that
could not evaluate (an invalid config, or check_host raised). Only the JSON envelopes and --version ever go to stdout.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import jsonschema
import yaml

from .model import Provider
from .session import serve

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

USAGE = """usage:
  {name} --config <path>         serve ADPP over stdin/stdout
  {name} --check-config <path>   validate a config; exit 0 valid, 1 invalid
  {name} --check-host <path>     report the host requirements a config implies
  {name} --config-schema         print the config JSON Schema envelope
  {name} --version               print the version
"""


class ConfigError(Exception):
    pass


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a duplicated mapping key. YAML parsers disagree
    on which occurrence wins (PyYAML keeps the last, yaml-cpp serves the first),
    so the C++ SDK refuses the document and so does this one."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:  # an unhashable key; the base class reports it
                continue
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


# JSON Schema counts 2.0 as an integer. A provider that declares an integer
# expects a Python int, so a float is refused wherever the schema says integer.
_Validator = jsonschema.validators.extend(  # type: ignore[no-untyped-call]
    jsonschema.Draft202012Validator,
    type_checker=jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: isinstance(value, int) and not isinstance(value, bool)
    ),
)


def _non_finite(data: Any, path: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    """The path of the first NaN or infinity in ``data``, which JSON cannot carry."""
    if isinstance(data, float) and not math.isfinite(data):
        return path
    items = data.items() if isinstance(data, dict) else enumerate(data) if isinstance(data, list) else ()
    for key, value in items:
        found = _non_finite(value, (*path, key))
        if found is not None:
            return found
    return None


def config_schema(provider_cls: type[Provider]) -> dict[str, Any]:
    schema = dict(provider_cls.config_schema)
    schema.setdefault("$schema", _SCHEMA_DIALECT)
    return schema


def load_config(provider_cls: type[Provider], path: str) -> Mapping[str, Any]:
    """Read a YAML (or JSON) config and validate it against the provider's schema."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_StrictLoader)
    except OSError as err:
        raise ConfigError(f"cannot read {path}: {err.strerror}") from None
    except UnicodeDecodeError as err:
        raise ConfigError(f"{path} is not UTF-8 text: {err.reason} at byte {err.start}") from None
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML: {err}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the config must be a mapping, not a {type(data).__name__}")
    bad = _non_finite(data)
    if bad is not None:
        raise ConfigError(f"{path}: {'/'.join(map(str, bad))}: a number must be finite (JSON has no NaN or infinity)")
    validator = _Validator(config_schema(provider_cls))
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"  {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]
        raise ConfigError(f"{path} does not match the config schema:\n" + "\n".join(lines))
    try:
        provider_cls.check_config(data)
    except ValueError as err:
        raise ConfigError(f"{path}: {err}") from None
    return data


def run(provider_cls: type[Provider], argv: Sequence[str] | None = None) -> int:
    """Entry point for a provider executable: ``sys.exit(run(MyProvider))``."""
    args = list(sys.argv[1:] if argv is None else argv)
    name = provider_cls.name

    if args == ["--version"]:
        print(f"{name} {provider_cls.version}")
        return 0
    if args == ["--config-schema"]:
        envelope = {"config_schema_version": 1, "provider": name, "schema": config_schema(provider_cls)}
        print(json.dumps(envelope, indent=2))
        return 0
    if len(args) != 2 or args[0] not in ("--config", "--check-config", "--check-host"):
        sys.stderr.write(USAGE.format(name=name))
        return 2

    verb, path = args
    try:
        config = load_config(provider_cls, path)
    except ConfigError as err:
        sys.stderr.write(f"{name}: {err}\n")
        # --check-host could not evaluate (§6: exit 2); otherwise the config is invalid.
        return 2 if verb == "--check-host" else 1

    if verb == "--check-config":
        sys.stderr.write(f"{name}: config valid\n")
        return 0
    if verb == "--check-host":
        try:
            requirements = list(provider_cls.check_host(config))
        except Exception as err:
            sys.stderr.write(f"{name}: could not evaluate the host requirements: {type(err).__name__}: {err}\n")
            return 2  # §6: could not evaluate
        envelope = {
            "check_host_version": 1,
            "provider": name,
            "requirements": [
                {k: v for k, v in (("id", r.id), ("status", r.status), ("detail", r.detail), ("remedy", r.remedy)) if v}
                for r in requirements
            ],
        }
        print(json.dumps(envelope, indent=2))
        return 1 if any(r.status == "unmet" for r in requirements) else 0
    return _serve(provider_cls, config)


def _serve(provider_cls: type[Provider], config: Mapping[str, Any]) -> int:
    # stdout carries frames only. Frames go to a private duplicate of fd 1, and
    # fd 1 itself (and sys.stdout) points at stderr while serving, so a stray
    # print(), a C extension's printf or a child process that inherits stdout
    # lands in the log instead of corrupting the frame stream.
    sys.stdout.flush()
    frames_fd = os.dup(1)
    os.dup2(2, 1)
    frames_out = os.fdopen(frames_fd, "wb")
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=f"{provider_cls.name}: %(levelname)s %(message)s")
    try:
        try:
            provider = provider_cls(config)
        except Exception:
            logging.getLogger("anolis_provider_sdk").exception("provider failed to initialise")
            return 1
        return serve(provider, sys.stdin.buffer, frames_out)
    finally:
        if sys.__stdout__ is not None:  # what was written to it while serving belongs to the log
            with contextlib.suppress(OSError, ValueError):
                sys.__stdout__.flush()
        os.dup2(frames_fd, 1)
        with contextlib.suppress(OSError):  # the reader may be gone
            frames_out.close()
