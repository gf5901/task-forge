# Local development

Task Forge targets **Python 3.9** (Amazon Linux 2023). Use the repo venv for all commands — do not rely on a system `python` that lacks dependencies.

## Python backend

```bash
cd /path/to/task-forge
python3.9 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Optional: `bash scripts/setup.sh` creates the venv, installs deps, and optionally builds the frontend.

### Tests

```bash
./.venv/bin/pytest tests/ -n auto
```

Sequential debugging: `./.venv/bin/pytest tests/ -n0` or `--pdb`.

### Running API + bot (needs AWS)

The task store is **DynamoDB** (`DynamoTaskStore`). Running `run_web.py` or `main.py` locally requires:

- `DYNAMO_TABLE` and `AWS_REGION` in `.env`
- Credentials with DynamoDB access (`AWS_PROFILE`, `~/.aws/credentials`, or env vars)

There is no file-based fallback in production entrypoints. For isolated tests, use the pytest fixtures in `tests/` (they mock or use temporary stores).

### Cursor agent CLI

Task execution expects the **Cursor `agent` binary** on `PATH` (or `AGENT_BIN`). Install per your Cursor license; on EC2 it is often `~/.local/bin/agent`.

## Frontend

Requires **Node.js 22** and **pnpm**.

```bash
cd frontend
pnpm install
pnpm dev
```

Vite serves on `:5173` and proxies `/api` to the FastAPI backend (default `http://127.0.0.1:8080`). Start the backend separately:

```bash
./.venv/bin/python run_web.py
```

Build production assets:

```bash
pnpm run build
```

Same checks as CI: `pnpm run typecheck`, `pnpm run lint`.

## Pre-commit

```bash
./.venv/bin/pip install pre-commit
./.venv/bin/pre-commit install
```

`.pre-commit-config.yaml` runs Ruff, frontend `typecheck`/`lint` when `frontend/` changes, and other checks.

## Infra (SST)

Do **not** run `sst deploy` on the EC2 task host (disk / provider downloads). Run from a developer machine or CI. See [infra/README.md](../infra/README.md) and [infra-deploy.md](infra-deploy.md).
