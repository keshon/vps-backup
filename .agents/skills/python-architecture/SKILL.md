---
name: python-architecture
description: Organize Python modules and resource ownership in vps-backup. Use when splitting modules, moving behavior or designing a feature boundary; ordinary statement edits only need python-style.
---

# Python architecture

Adapted from Melodix's Go architecture. Keep the principle that a maintainer can
find and change a feature without learning the whole program. Do not introduce
Go layouts, Go naming rules or a package per file into this Python project.

## Reading path and boundaries

The current `vpsbackup` modules are responsibility boundaries. CLI parsing and
presentation belong in `cli`; backup coordination and consistent source staging
in `client`; native archive storage in `archive`; snapshot selection and portable
export in `recovery`; gateway operations in `server`; schedules in `schedule`.
Client setup and app selection belong in `onboarding`, runtime publication in
`installation`, and terminal help and result formatting in `presentation`.
Check current source before moving behavior; this map is navigation, not a demand
for matching files in every feature.

Keep `recover.py` as the local entry point and share recovery behavior with the
CLI. Recovery must work on Windows independently of Linux client execution.
Existing `common` holds shared runtime primitives; do not grow it into a home for
unrelated feature logic. Introduce a named module when a coherent responsibility
earns it, rather than reorganizing existing code to satisfy a layout rule.

Split a file when it contains distinct responsibilities that can be understood
separately. Extract a subpackage only when it hides a coherent operation behind a
smaller interface. Sketch an ordinary call, its dependencies, result and cleanup
before moving it. If callers still need most internals, ownership needs work.
There is no target module length or file count.

## Shared backup behavior

Keep app validation, SQLite snapshotting, stopped-container copies, disk reserves,
locking and restart recovery shared across storage modes. Backend-specific
encoding and remote layout belong with that backend. Restic and native archives
should expose comparable user operations without forcing their internals into
identical classes or file structures.

Prefer direct calls and concrete values. Avoid dependency containers, generic
repository/service layers, callback registries and abstractions introduced only
to break an import cycle. Local imports can isolate platform-specific execution;
they should not conceal tangled ownership.

## Lifetimes and compatibility

Each operation owns its temporary files, processes, threads and cancellation.
Coordination belongs with the operation that starts the work, not a peer backend.
When moving code, trace startup, success, failure, interruption and cleanup as one
lifetime. Preserve restart journals on failed restart and never publish incomplete
archives or success markers.

Preserve configuration, manifest and CLI contracts used by installed clients and
n8n. Do not add compatibility wrappers for private renames without real callers.
Never copy secrets or exported app data into fixtures or committed examples.

Tests live under `tests`, following this repository's existing unittest discovery.
Test behavior at real boundaries with temporary files/repositories and external
programs where needed. Do not export internals or reproduce production logic in
fixtures merely to make a move easy. Keep assertions and failure coverage during
refactors; update the architecture guide when reading paths materially change.

Use [python-style](../python-style/SKILL.md) for implementation and
[docs/conventions.md](../../../docs/conventions.md) for actual checks.
