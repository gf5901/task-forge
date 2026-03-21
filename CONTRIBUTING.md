# Contributing to Task Forge

Thanks for your interest. This project follows a small set of rules so CI and production (Python 3.9 on Amazon Linux) stay happy.

## Workflow

- Open **pull requests** against `main` (no direct pushes to `main` per maintainers’ policy).
- Use the PR template (`.github/PULL_REQUEST_TEMPLATE.md`): summary, changes, testing.
- Keep commits focused; **pre-commit** is configured (Ruff, formatting, frontend checks when `frontend/` changes).

## Python

- **3.9 only** — no `X | Y` unions, no `list[str]`, no `match`. Use `typing` (`List`, `Dict`, `Union`, …).
- Prefer explicit paths: `repo/.venv/bin/python3` in scripts and docs.
- **Config** lives in `.env` — never commit secrets or hardcode tokens.

## Tests & lint

```bash
./.venv/bin/pytest tests/ -n auto
./.venv/bin/pip install pre-commit && ./.venv/bin/pre-commit run --all-files
```

Frontend (from `frontend/`):

```bash
pnpm run typecheck && pnpm run lint
```

## Do not commit

- `.env` (secrets)
- `deploy.log`, `frontend/dist/`, `frontend/node_modules/` (build artifacts)

## Docs

- Update `README.md` or `docs/` when behavior changes (poller, DynamoDB, env vars).
- See [docs/local-dev.md](docs/local-dev.md) for local setup.

## Code of Conduct

We follow the [Contributor Covenant](CODE_OF_CONDUCT.md). Be respectful and constructive.
