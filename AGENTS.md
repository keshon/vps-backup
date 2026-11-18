# Working in this repository

This is a Go terminal application. Keep backup operations in `internal/backup`
and application interaction in `internal/tui`. Gektor's copied controls live in
`internal/tui/components`; they must not import backup logic or persistence.
Preserve their tests and record any changes beyond module import paths.

Use [.agents/skills/go-style/SKILL.md](.agents/skills/go-style/SKILL.md) and
[.agents/skills/go-architecture/SKILL.md](.agents/skills/go-architecture/SKILL.md),
copied from Melodix. Run gofmt, go vet, and go test
on changed packages; use the race detector when changing asynchronous work.
Tests use temporary folders and fake command boundaries, never live containers
or production clouds. Keep credentials out of configuration, results and logs.

No services, timers, REST gateway or Python runtime. Archive operations must
release their local OS lock, restart only previously running containers, and
finish cleanup before reporting completion or letting the TUI quit.

The old implementation is retained in the Git tag
`archive/python-before-go-2026-11-18`. Do not alter ignored operator files or tmp/.
