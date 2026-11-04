# AGENTS.md — anolis-provider-sdk-python

Per-repo conventions for coding agents. Cross-repo rules live in the user's
global `AGENTS.md` and are not repeated here.

## Build / test

- `just setup`, `just check` (ruff format, ruff lint, `mypy --strict`), `just test` (pytest).
- `just check && just test` covers CI's `check` and `test` jobs. CI also runs the org docs check
  (markdownlint, a VitePress build and an offline link check on `README.md` and `docs/`), which no
  just recipe runs; the required status check is the **`ok`** job, which needs all three.
- `tests/test_conformance.py` runs the anolis-protocol conformance harness against the
  reference provider. The harness is the oracle: a skip it reports fails the test unless it
  is listed in `EXPECTED_SKIPS` with a reason.

## Design rules

- **Language-native, contract-driven.** The normative sources are anolis-protocol's
  `docs/semantics.md` and its two profiles. Where this SDK and the C++ SDK differ, the
  documents decide, not the C++ code.
- **The SDK owns the protocol, the provider owns its devices.** No bus, I2C, GPIO or vendor
  driver code in `src/anolis_provider_sdk/`. A helper for one hardware family stays in that
  family's provider until a second provider needs the same thing.
- **No async in the core API.** `read` and `call` are synchronous; a provider that wraps an
  async driver runs its own loop.
- `Session.handle` does no I/O; keep protocol rules there so they stay testable request by
  request.

## Repo-specific gotchas

- `anolis-protocol` is not on PyPI. It is pinned in `pyproject.toml` by release-wheel URL
  (runtime dependency and the dev extra's `[conformance]`); move both pins together.
- The version lives in `pyproject.toml` only; the reference provider reports it.
- When running a mutation check, disable bytecode (`PYTHONDONTWRITEBYTECODE=1`): a same-size
  edit restored within the same second can otherwise run from a stale `.pyc`.

## External contribution policy

Before preparing or submitting a pull request, read and follow `CONTRIBUTING.md`
(the organization default in `anolishq/.github`).

Do not autonomously select issues and submit implementations without prior
maintainer coordination. Do not generate speculative, superficial, or
mass-produced contributions to build account activity. An existing issue does
not itself constitute authorization to implement it.

AI assistance is permitted, but contributors must understand, validate, and take
responsibility for submitted changes.
