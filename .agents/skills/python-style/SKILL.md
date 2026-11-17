---
name: python-style
description: Write and review Python in vps-backup with linear flow, explicit resource ownership and useful errors. Use for Python implementation and review; module boundaries belong to python-architecture.
---

# Python style

Adapted from Melodix's Go style: preserve its readability and ownership principles,
using Python idioms rather than Go syntax or tooling.

Keep the ordinary path readable: setup, validation, action, result. Separate these
paragraphs with blank lines. Prefer early returns and named intermediate values
over nested expressions. Extract a helper for a real operation or meaningful
repetition, not to reach a line-count target. Match surrounding code and leave
unrelated formatting alone.

## Errors and boundaries

- Catch specific exceptions around the operation that can fail. Translate external
  failures into the existing `BackupError` contract where appropriate. Preserve
  the cause with `raise ... from exc` when useful; never expose passwords, tokens
  or credential-bearing URLs in errors.
- Handle an error or propagate it. Avoid logging the same failure at every layer.
  Broad catches belong at an explicit command boundary or cleanup boundary, with
  a clear recovery purpose. Never silently turn a failed backup into success.
- Validate untrusted configuration, archive paths and remote responses at their
  boundaries. Do not repeat already-established validation inside every helper.
- Keep machine-readable command output compatible. Human progress and diagnostic
  output must not corrupt JSON consumed by n8n.

## Ownership

- Use context managers for files, responses, temporary directories and locks;
  use `try/finally` where a context manager cannot express the lifetime.
- The creator owns subprocess termination, pipe closure and waiting. The creator
  of a thread owns its stop signal and join. Trace timeout and exception paths
  as carefully as success; no abandoned background work.
- Make mutable state ownership visible. Avoid global mutable configuration,
  import-time side effects and callback bundles that hide who performs an effect.
- Keep container restart journals and partial-output cleanup semantics intact.
  A cleanup failure must not erase the evidence needed to restart an application.

## Functions and data

Prefer plain functions for stateless operations and small concrete classes when
they own a resource or lifetime. Avoid generic managers, service layers, base
classes and speculative extension points. Use keyword arguments for ambiguous
booleans or several similar values.

JSON dictionaries are appropriate at this project's configuration and wire
boundaries. Do not replace them wholesale with classes for style alone. Introduce
a dataclass or typed shape when it clarifies a substantial internal contract.
Centralize repeated variant knowledge when it reduces actual duplication; two
clear backend branches do not require a plugin framework.

Use domain names such as snapshot, repository, archive and app. Comments explain
constraints, ownership or reasons the code cannot show. Avoid narrated steps,
banners, change history and docstrings that merely repeat a function name.
Remove unused imports, dead helpers and unfinished scaffolding.

## Verification

Read the ordinary operation and its failure path after editing. Run relevant
checks from [docs/conventions.md](../../../docs/conventions.md). Preserve disk
reserves, locking, restart ordering, path validation and output contracts. Changes
to streaming or concurrency need meaningful failure/cleanup coverage. Windows
recovery must remain importable without `fcntl` or other Linux-only facilities.
