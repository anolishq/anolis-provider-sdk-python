# anolis-provider-sdk-python dev commands. `check` + `test` match CI's check and
# test jobs; CI's docs check (markdownlint, link check) has no recipe here.

# List available recipes
default:
    @just --list

# Install the locked dev environment
setup:
    uv sync --locked --extra dev

# Format the code
fmt:
    uv run ruff format .

# Lint
lint:
    uv run ruff check .

# CI-equivalent static checks: format, lint, strict typecheck
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy

# Unit tests, the stdio loop, and the conformance harness against the reference provider
test:
    uv run pytest
