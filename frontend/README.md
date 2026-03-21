# Task Forge — frontend

React 19 + TypeScript + Vite 8 + Tailwind CSS v4 + shadcn/ui. The SPA talks to the backend over `/api/*` (FastAPI on EC2 or the Lambda API in production).

## Requirements

- **Node.js 22**
- **pnpm** (`curl -fsSL https://get.pnpm.io/install.sh | bash -`)

## Commands

| Command | Purpose |
|---------|---------|
| `pnpm install` | Install dependencies |
| `pnpm dev` | Vite dev server on **:5173**, proxies `/api` → `http://127.0.0.1:8080` |
| `pnpm run build` | Production build → `frontend/dist/` |
| `pnpm run typecheck` | `tsc -b` (same as CI) |
| `pnpm run lint` | ESLint |

## Configuration

- **`VITE_API_BASE_URL`** — Base URL for the JSON API when the SPA is hosted on S3/CloudFront (empty in dev: rely on Vite proxy or same-origin `/api`).

## Layout

See [AGENTS.md](./AGENTS.md) for routes, components, and design tokens (Linear-inspired dark UI).

## Parent repo

- Backend, bot, poller, and infra: [`../README.md`](../README.md)
- Local development: [`../docs/local-dev.md`](../docs/local-dev.md)
