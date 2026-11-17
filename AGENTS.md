# Working in this repository

Use the project-local skills when they apply:

- Python implementation or review: [.agents/skills/python-style/SKILL.md](.agents/skills/python-style/SKILL.md).
- Module boundaries, ownership or refactoring: [.agents/skills/python-architecture/SKILL.md](.agents/skills/python-architecture/SKILL.md).
- Documentation structure: [.agents/skills/docs-pattern/SKILL.md](.agents/skills/docs-pattern/SKILL.md).

Read [docs/conventions.md](docs/conventions.md) for development checks and backup
invariants. The backup client and gateway run on Linux; the local recovery tool
also runs on Windows. Keep recovery imports usable without Linux-only modules.
The project uses the Python standard library; restic, rclone and Docker are
external programs, not Python dependencies.

Use fixtures and temporary repositories for ordinary validation. Production
configuration, cloud credentials, recovery bundles and exported application data
must stay outside commits. A local test does not authorize a production deployment.
