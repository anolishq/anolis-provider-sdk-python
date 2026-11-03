"""Executable profile v1 §1, §2 and §6: the CLI verbs and their exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anolis_provider_sdk import HostRequirement, run

from .conftest import PumpProvider


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return str(path)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(PumpProvider, ["--version"]) == 0
    assert capsys.readouterr().out.strip() == "anolis-provider-test 1.2.3"


def test_config_schema_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(PumpProvider, ["--config-schema"]) == 0
    env = json.loads(capsys.readouterr().out)
    assert env["config_schema_version"] == 1 and env["provider"] == "anolis-provider-test"
    assert env["schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert env["schema"]["additionalProperties"] is False


@pytest.mark.parametrize("text", ["{}\n", "fail_read: false\n", ""])
def test_check_config_accepts_valid(tmp_path: Path, text: str) -> None:
    assert run(PumpProvider, ["--check-config", write(tmp_path, text)]) == 0


@pytest.mark.parametrize(
    "text",
    [
        "{ this: is: not [valid yaml or json\n",  # the harness's unparseable document
        "fail_read: maybe\n",  # wrong type
        "unknown_key: 1\n",  # additionalProperties: false
        "- a list\n",  # not a mapping
    ],
)
def test_check_config_rejects_invalid_with_exit_1(
    tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(PumpProvider, ["--check-config", write(tmp_path, text)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err  # the reason goes to stderr


def test_check_config_missing_file_is_invalid(tmp_path: Path) -> None:
    assert run(PumpProvider, ["--check-config", str(tmp_path / "absent.yaml")]) == 1


def test_check_host_reports_requirements(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "{}\n")
    assert run(PumpProvider, ["--check-host", path]) == 0
    env = json.loads(capsys.readouterr().out)
    assert env == {"check_host_version": 1, "provider": "anolis-provider-test", "requirements": []}

    class Picky(PumpProvider):
        @classmethod
        def check_host(cls, config: object) -> list[HostRequirement]:
            return [
                HostRequirement("bus.present", "met", detail="/dev/i2c-1 exists"),
                HostRequirement("bus.access", "unmet", detail="permission denied", remedy="add the user to i2c"),
            ]

    assert run(Picky, ["--check-host", path]) == 1
    reqs = json.loads(capsys.readouterr().out)["requirements"]
    assert reqs[1] == {
        "id": "bus.access",
        "status": "unmet",
        "detail": "permission denied",
        "remedy": "add the user to i2c",
    }


def test_check_host_on_invalid_config_cannot_evaluate(tmp_path: Path) -> None:
    assert run(PumpProvider, ["--check-host", write(tmp_path, "unknown_key: 1\n")]) == 2


@pytest.mark.parametrize("argv", [[], ["--bogus"], ["--config"], ["--check-config"]])
def test_usage_errors_exit_2_with_nothing_on_stdout(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(PumpProvider, argv) == 2
    assert capsys.readouterr().out == ""


def test_serving_refuses_an_invalid_config(tmp_path: Path) -> None:
    assert run(PumpProvider, ["--config", write(tmp_path, "unknown_key: 1\n")]) == 1
