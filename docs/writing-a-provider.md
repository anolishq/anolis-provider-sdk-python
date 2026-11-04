# Writing a provider

A provider is an executable that the Anolis runtime starts with
`--config <path>` and talks to over stdin and stdout. This SDK turns a Python
class into that executable.

The contracts it implements are defined in
[anolis-protocol](https://github.com/anolishq/anolis-protocol):
`docs/semantics.md` (ADPP v1), `docs/profiles/framed-stdio-v1.md` (the stdio
transport) and `docs/profiles/anolis-executable-profile-v1.md` (the command
line).

## 1. Declare device types

A `DeviceType` lists its signals and functions once. Everything a client sees
about the device, and every check the SDK applies to a call, comes from this
declaration.

```python
from anolis_provider_sdk import Arg, Category, DeviceType, Function, Signal, ValueType

MOTOR = DeviceType(
    id="dc_motor",
    signals=(
        Signal("speed", ValueType.DOUBLE, unit="rpm"),
        Signal("throttle", ValueType.DOUBLE),
        Signal("fault_code", ValueType.INT64, default=False),
    ),
    functions=(
        Function(
            "set_throttle",
            args=(Arg("throttle", ValueType.DOUBLE, min=-1.0, max=1.0),),
            results=(Arg("throttle", ValueType.DOUBLE),),
            category=Category.ACTUATE,
        ),
        Function("clear_fault", category=Category.CONFIG),
    ),
)
```

- **Signals.** Signal ids are snake_case. A read that names no signals returns the
  `default` ones, so at least one signal must be a default. `poll_hint_hz`
  (default 1.0) above 0 is what makes the Anolis runtime poll a signal; set it to
  0 for a signal that should only be read on request.
- **Functions.** A function's id is its position in `functions`, starting at 1.
  Append new functions at the end, so existing ids stay stable.
- **Categories.** `category` defaults to `Category.ACTUATE`, so a function
  declared without one is an output the runtime's e-stop must cover. Give every
  `ACTUATE` function a required numeric argument for which 0 is the safe value,
  as `set_throttle` has. The runtime's `zero_is_safe` rung drives an output by
  calling it with every required argument set to 0; it refuses a function with
  no arguments or with a required boolean, string or bytes argument, and reports
  it as not driven. A command such as `stop` or `clear_fault` is not an output
  in that sense: declare it `CONFIG`, and stop the motor with
  `set_throttle(0)`.
- **Arguments.** Bounds are inclusive. `allowed_values` restricts a string
  argument. An argument is required unless `required=False`.

The SDK checks the declaration when it is made: duplicate names, a non-snake_case
signal id, bounds on a non-numeric argument, or an integer bound outside its
type's range (a negative bound on a `UINT64`, say) raise `ValueError` at import
time.

## 2. Implement the provider

```python
from anolis_provider_sdk import Device, Provider, ProviderError, StatusCode


class MotorProvider(Provider):
    name = "anolis-provider-motor"
    version = "0.1.0"
    config_schema = {
        "type": "object",
        "properties": {"address": {"type": "integer", "minimum": 0, "maximum": 127}},
        "required": ["address"],
        "additionalProperties": False,
    }

    def __init__(self, config):
        super().__init__(config)  # self.config is the validated config
        self.driver = None

    def start(self, budget_s):
        self.driver = open_driver(self.config["address"])  # slow start-up goes here

    def devices(self):
        return [Device("motor0", MOTOR, label="Motor 0")]

    def read(self, device, signal_ids):
        state = self.driver.state()
        return {sid: getattr(state, sid) for sid in signal_ids}

    def stop(self):
        if self.driver is not None:
            self.driver.set_throttle(0.0)  # the safe state, however the session ends

    def call(self, device, function, args):
        if function.name == "clear_fault":
            self.driver.clear_fault()
            return None
        if function.name == "set_throttle":
            self.driver.set_throttle(args["throttle"])
            return {"throttle": args["throttle"]}
```

What each method gets and returns:

- **`config`** has already been validated against `config_schema` (JSON Schema
  Draft 2020-12). The provider refuses to start, and `--check-config` exits 1, on
  a config that does not match it.
- **`check_config(config)`** is a classmethod for what a schema cannot say:
  duplicate device ids, two devices on one address. Raise `ValueError` with a
  message. The SDK calls it after schema validation for `--check-config`,
  `--check-host` and `--config` (before your constructor runs), so a config that
  `--check-config` accepts is one the provider will start with.
- **`start(budget_s)`** runs once, after Hello and before the first request after
  it. Put slow start-up there, so Hello is answered at once. If it raises, the
  provider keeps serving: `WaitReady` reports `ready=false` and `GetHealth`
  reports `STATE_FAULT`.
- **`read`** receives only signal ids the device type declares. Return a value
  for each one. To report a value with its own quality or observation time,
  return a `Reading(value, quality=..., timestamp=...)`. A plain value is stamped
  with the current time and `QUALITY_OK`, and a reading older than the signal's
  `stale_after_ms` is reported as `STALE`.
- **A signal you cannot read.** ADPP has no value-less signal: in a `CODE_OK`
  read every value carries a `Value` of the declared type, so a
  `Reading(None, ...)` fails the whole read with `CODE_INTERNAL`. When the device cannot
  be read at all, raise `ProviderError(StatusCode.UNAVAILABLE, ...)`. When one
  signal of several is bad, return a placeholder of the declared type with
  `quality=Quality.FAULT` (or `UNKNOWN`): `math.nan` for a `DOUBLE` cannot be
  mistaken for a real reading, as `0.0` can. A consumer must read `quality`
  before the value.
- **`call`** receives a declared function and arguments that have passed
  validation, already converted to Python values. Return a dict holding every
  declared result, or `None` when the function declares none.
- **`stop()`** runs exactly once when the session ends, however it ends: stdin
  EOF (the runtime shut down, crashed or closed the pipe), a malformed frame, a
  broken pipe, `SIGTERM`, or an exception in the loop. It is the place to drive
  outputs to their safe state (zero the motors, close the valves), because on
  hardware without a watchdog an output holds its last value after the provider
  exits. It can run without `start` having run, so check what you opened. An
  exception from it is logged and does not change the exit code. `SIGTERM` ends
  the session with exit 0 once `stop()` has run. The C++ SDK's equivalent is
  `LifecycleHooks::on_shutdown`.
- **Deadlines.** Hello advertises `supports_deadlines=true`, and the SDK
  handles a call's deadline for you, as follows. It checks the deadline once,
  before `call` runs: an expired call is refused with `CODE_DEADLINE_EXCEEDED`
  and nothing is done. It does not interrupt `call`. If the deadline passes while
  `call` runs, the call completes, and the SDK then answers
  `CODE_DEADLINE_EXCEEDED` with `side_effects=possible` in the status details,
  because its effects may have happened (semantics.md §8.4). `call` is not
  given the deadline, and the SDK makes no attempt to cancel a call that is
  running, so keep `call` short and do not start long waits in it.
- **Errors.** Raise `ProviderError(StatusCode.UNAVAILABLE, "bus not responding")`
  to answer with a specific status. Any other exception becomes
  `CODE_INTERNAL`; it is logged, and the provider keeps serving.

Health:

- `health()` and `device_health(device)` default to OK.
- Override them to report what you know, such as a bus fault or an unreachable
  device.
- They are answered on the request path, so return state you already hold. Do
  not talk to the hardware in them.

## 3. Make it an executable

End the module with:

```python
if __name__ == "__main__":
    sys.exit(run(MotorProvider))
```

You can also expose it as a console script in `pyproject.toml`
(`[project.scripts]`). The runtime and the conformance harness need one
executable file to start.

The command line follows the executable profile:

| Command | Does | Exit |
| --- | --- | --- |
| `--config <path>` | serve ADPP on stdin/stdout | 0 at end of input or on `SIGTERM`, 1 invalid config, 2/3 malformed input |
| `--check-config <path>` | validate a config | 0 valid, 1 invalid |
| `--config-schema` | print the config schema envelope | 0 |
| `--check-host <path>` | print the host requirements the config implies | 0 none unmet, 1 something unmet, 2 invalid config or `check_host` raised |
| `--version` | print `name version` | 0 |

To report host requirements, override the `check_host(config)` classmethod and
return `HostRequirement` entries. Keep it read-only: inspect device nodes and
parameters, do not talk to the hardware.

The SDK also runs `check_host` at startup, before `start`, as the executable
profile's §6 asks. If a requirement is `unmet`, the provider stays up but not
ready: `start` is not called, it exposes no devices, `WaitReady` reports
`ready=false`, `host_check=unmet` and a `host_unmet` summary of the unmet ids
and details, and `GetHealth` reports `STATE_FAULT`. Otherwise `WaitReady`
reports `host_check=ok`. A `check_host` that raises at startup is logged and does
not block `start`, as an `unknown` requirement would not.

While serving, stdout carries frames only. The SDK writes frames to a private
copy of file descriptor 1 and points fd 1 itself, and `sys.stdout`, at stderr.
A stray `print()`, a C extension writing to fd 1, or a child process that
inherits stdout therefore goes to the log instead of corrupting the stream.
Output written at import time, before `run()` is called, still reaches the real
stdout, so do not print at import.

## 4. Check it

Run the conformance harness against your provider, with a config that needs no
hardware and a manifest naming your provider:

```sh
pip install "anolis-protocol[conformance] @ https://github.com/anolishq/anolis-protocol/releases/download/v1.10.0/anolis_protocol-1.10.0-py3-none-any.whl"
anolis-adpp-conformance --provider-bin "$(command -v anolis-provider-motor)" \
  --provider-config config/conformance.yaml --provider-profile config/conformance.toml -ra
```

The reference provider in `src/anolis_provider_sdk/reference.py` is a complete
example. This repository's CI runs the harness against it.

## Vendor drivers

A provider for one vendor's boards drives them through that vendor's own
drivers, so a user of the vendor's boards finds the code they already know.
Two rules keep the provider from becoming a fork of the vendor's ecosystem.

**A driver the vendor lacks is staged, not buried.** When the vendor publishes
no driver for a chip on one of its boards, write one in its own package inside
the provider repository (the SparkFun provider's `anolis_qwiic_staging` is the
example), not inside a board module:

- write it in the vendor's driver conventions and against the vendor's bus
  interface, so it can be offered as it stands;
- it imports nothing from the provider, and a test enforces that;
- open an issue in the provider repository to track offering it;
- offer it to the vendor once it has run on real hardware;
- delete it when the vendor publishes it, and switch to the published package.

A driver that another vendor publishes does not count: each provider covers
its own vendor's boards.

**A bug in a vendor driver is reported upstream and worked around here, marked
for removal:**

- Report it to the vendor as an issue, with a reproduction that uses only the
  vendor's driver, after checking their tracker for an existing report. Open a
  pull request only once the issue is acknowledged or the fix is trivial.
- Work around it in the board module, behind the driver's public interface.
  Never patch the installed package or keep a patched copy of it. A workaround
  that would amount to rewriting the driver means staging a driver instead.
- Put a link to the upstream issue in a comment at the workaround, and name the
  issue in the pull request that adds it.
- Add a test that pins the bug: it exercises the vendor's driver directly,
  asserts the buggy behaviour, and links the same issue. When a dependency
  update brings the fix, that test fails, and it is the signal to remove the
  workaround and the test together.

## YAML

Configs are read with PyYAML, which follows YAML 1.1. An unquoted `yes`, `no`,
`on` or `off` loads as a boolean, and `010` loads as octal 8. Declare types in
`config_schema`, so a value that loaded as the wrong type is rejected rather
than used.

The SDK is stricter than PyYAML and plain JSON Schema in three places, so that
your code never sees a value JSON could not carry:

- A duplicated mapping key is refused. YAML parsers disagree on which
  occurrence wins, and the C++ SDK refuses it too. Merge keys (`<<`) still work.
- A non-finite number (`.nan`, `.inf`) anywhere in the config is refused.
- Where the schema says `type: integer`, only an integer is accepted: `2.0` is
  refused, although JSON Schema itself would accept it.
