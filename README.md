# anolis-provider-sdk-python

A Python SDK for writing [Anolis](https://github.com/anolishq/anolis) device providers.

A provider wraps a driver for some hardware and exposes its devices to the Anolis
runtime over the Anolis Device Provider Protocol (ADPP). With this SDK you
declare each device type once, as data, and implement three methods. The SDK
handles the protocol:

- framing, the Hello handshake, and request dispatch;
- the default signal set, timestamps and quality on every value;
- argument validation (required, type, finite, inclusive bounds) and declared call results;
- deadlines, `WaitReady` and `GetHealth`;
- the provider command line (`--config`, `--check-config`, `--config-schema`, `--check-host`, `--version`).

It is a language-native implementation of the same contracts the C++
[anolis-provider-sdk](https://github.com/anolishq/anolis-provider-sdk) implements, not a wrapper around it. Both
are checked by the same conformance harness from
[anolis-protocol](https://github.com/anolishq/anolis-protocol).

> **Status: unreleased (0.1.0 in development).** No version has been tagged yet. The bundled reference
> provider passes the anolis-protocol 1.10.0 conformance harness at conformance level 2, with no skipped
> tests. Not yet published to PyPI.

## A provider in brief

```python
import sys
from anolis_provider_sdk import Arg, Device, DeviceType, Function, Provider, Signal, ValueType, run

PUMP = DeviceType(
    id="pump",
    signals=(Signal("flow", ValueType.DOUBLE, unit="L/min"),),
    functions=(Function("set_flow", args=(Arg("flow", ValueType.DOUBLE, min=0.0, max=10.0),)),),
)


class PumpProvider(Provider):
    name = "anolis-provider-pump"
    version = "0.1.0"
    config_schema = {"type": "object", "properties": {"port": {"type": "string"}}, "required": ["port"]}

    def devices(self):
        return [Device("pump0", PUMP)]

    def read(self, device, signal_ids):
        return {"flow": my_driver.flow()}

    def call(self, device, function, args):
        my_driver.set_flow(args["flow"])  # already validated against 0.0..10.0


if __name__ == "__main__":
    sys.exit(run(PumpProvider))
```

See [docs/writing-a-provider.md](docs/writing-a-provider.md) for the full guide.

To start a provider, use the template:
[anolis-provider-template-python](https://github.com/anolishq/anolis-provider-template-python)
is a working provider on this SDK (simulated heaters) with CI, a frozen
single-binary release, the conformance harness and a rename script. Click
**Use this template**, then run `scripts/rename.sh <kind>`.

## Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

```sh
just setup   # uv sync --locked --extra dev
just check   # ruff format check, ruff lint, mypy --strict
just test    # unit tests, the stdio loop, and the conformance harness
```

## License

Apache-2.0. See [LICENSE](LICENSE). A provider built with this SDK can be
distributed under any licence compatible with Apache-2.0, proprietary included.
Contributions are accepted under Apache-2.0 (§5 of the licence).
