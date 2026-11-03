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
