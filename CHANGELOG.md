# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The SDK: declare device types (signals, functions, arguments, results) as
  data and implement `devices`, `read` and `call` on a `Provider` subclass.
  The SDK handles framed stdio, the Hello gate, request dispatch, the default
  signal set, timestamps and quality, argument validation, declared results,
  deadlines, `WaitReady`, `GetHealth` and the executable-profile command line.
- A reference provider (`anolis-provider-reference`, a synthetic counter). It
  passes the anolis-protocol 1.10.0 conformance harness at level 2 with no
  skipped tests, in this repository's CI.
- A `py.typed` marker (PEP 561), so type checkers read the SDK's annotations
  in code that builds on it.
- `Provider.stop()`, called exactly once when the session ends (EOF, a framing
  or decode error, a broken pipe, an exception in the loop, or `SIGTERM`, which
  now ends the session in order with exit 0). An actuating provider drives its
  outputs safe there.
- `Provider.check_config(config)`, a classmethod for checks a schema cannot
  express. It runs after schema validation for `--check-config`, `--check-host`
  and `--config`, so `--check-config` refuses what the provider would refuse.
  The reference provider uses it to refuse duplicate device ids.

### Fixed

- The host requirement check now runs at startup too (executable profile §6).
  On an unmet requirement the provider stays up but not ready, exposes no
  devices, and reports `host_check` and `host_unmet` in `WaitReady`.
- `--check-host` exits 2 (could not evaluate) when `check_host` raises, instead
  of 1 with a traceback.
- A config file that is not UTF-8 is reported as a config error, not a
  traceback.
- Configs are loaded strictly: a duplicated mapping key, a non-finite number,
  and a float where the schema says `integer` are refused rather than passed to
  the provider.
- While serving, file descriptor 1 points at stderr and frames go to a private
  duplicate of it, so a child process or a C-level write cannot corrupt the
  frame stream. Before, only `sys.stdout` was redirected.
- An `INT64` or `UINT64` argument bound outside the type's range raises
  `ValueError` when it is declared, instead of failing every `DescribeDevice`
  with `CODE_INTERNAL`.
