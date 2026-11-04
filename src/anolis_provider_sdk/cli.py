"""The provider executable's command line (anolis-protocol
docs/profiles/anolis-executable-profile-v1.md §1, §2, §6).

Exit codes: 0 success; 1 an invalid config (--check-config, --config) or an
unmet host requirement (--check-host); 2 a usage error, or --check-host that
could not evaluate. Only the JSON envelopes and --version ever go to stdout.
"""

from __future__ import annotations

import json
import logging
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


def config_schema(provider_cls: type[Provider]) -> dict[str, Any]:
    schema = dict(provider_cls.config_schema)
    schema.setdefault("$schema", _SCHEMA_DIALECT)
    return schema


def load_config(provider_cls: type[Provider], path: str) -> Mapping[str, Any]:
    """Read a YAML (or JSON) config and validate it against the provider's schema."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as err:
        raise ConfigError(f"cannot read {path}: {err.strerror}") from None
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML: {err}") from None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the config must be a mapping, not a {type(data).__name__}")
    validator = jsonschema.Draft202012Validator(config_schema(provider_cls))
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
        requirements = list(provider_cls.check_host(config))
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
    # stdout carries frames only. Keep its binary stream for them and point
    # sys.stdout at stderr, so a stray print() in provider or driver code lands
    # in the log instead of corrupting the frame stream.
    frames_out = sys.stdout.buffer
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=f"{provider_cls.name}: %(levelname)s %(message)s")
    try:
        provider = provider_cls(config)
    except Exception:
        logging.getLogger("anolis_provider_sdk").exception("provider failed to initialise")
        return 1
    return serve(provider, sys.stdin.buffer, frames_out)
