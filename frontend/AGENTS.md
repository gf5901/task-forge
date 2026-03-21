# Frontend — React SPA

## Stack

React 19, TypeScript 5.9, Vite 8, Tailwind CSS v4, shadcn/ui, Lucide React icons, react-router-dom v7.

## Design System

Linear-inspired dark theme on zinc-950 background. Key patterns:

- **Flat sections** — no heavy Card wrappers. Use section headers (`text-xs font-medium uppercase tracking-wider text-zinc-500`) with content below.
- **Status colors** — pending: zinc-500, in_progress: yellow-500, completed: emerald-500, cancelled: red-500.
- **Accent** — indigo-600 for primary actions (buttons, FAB, links). Focus rings: `focus:ring-2 focus:ring-indigo-500/40`.
- **Typography** — Geist Variable font (`--font-sans`). 13px for body text, 11px for metadata, 12px for mobile tabs.
- **Spacing** — tight. `py-2.5` for list rows, `gap-2` between inline items, `space-y-5` between sections.

## File Structure

```
src/
  components/Layout.tsx   — App shell (sidebar + mobile tabs + FAB)
  components/Markdown.tsx — Shared markdown renderer (react-markdown + remark-gfm + remark-breaks)
  components/ui/          — shadcn/ui primitives (Button, Badge, Input, etc.)
  pages/TaskList.tsx      — Task list with status icons and priority bars; on the "all" tab tasks are grouped by status in order: in_review → in_progress → pending → completed → cancelled
  pages/TaskDetail.tsx    — Flat detail view: metadata, subtasks, md content; status changed via inline badge dropdown; Run/Delete in header; polls every 3s while reply_pending, shows spinner while agent composes reply; agent comments styled in indigo
  pages/TaskCreate.tsx    — New task form: priority selector, plan-only toggle, role dropdown (fetches GET /api/roles directly via fetchRoles(), hidden when no roles are configured), and target repo field with datalist autocomplete
  pages/ActivityLog.tsx   — Pipeline activity log with Lucide icons per event type, stage color badges, and mobile-responsive layout
  pages/Stats.tsx         — Usage & cost (today / all-time tokens, budget bar, 14-day chart via GET /api/stats and /api/budget)
  pages/ProjectList.tsx   — Project list with status, task progress, last directive, target repo
  pages/ProjectCreate.tsx — New project form: title, spec (markdown) with optional “Generate from prompt” (Bedrock via API), priority, target repo, autopilot toggle
  pages/ProjectDetail.tsx — Spec editor (Generate while editing), progress bar, autopilot toggle + today’s plan section (proposed/approve/progress/completed), directive timeline, active tasks, directive composer; polls 3s while busy
  pages/Login.tsx         — Sign-in page
  lib/api.ts              — Fetch client (credentials: "include", auto-reload on 401); exports fetchRoles() → GET /api/roles; project CRUD (fetchProjects, createProject, patchProject, postProjectDirective, etc.); plan APIs (fetchPlans, fetchPlanDetail, approvePlan, regeneratePlan, patchPlanItems)
  lib/types.ts            — TypeScript interfaces matching the /api/* responses; Task includes role: string, project_id, directive_sk; Project (with autopilot boolean), Directive, DailyPlan, PlanItem, ProjectListItem types
  lib/time.ts             — timeAgo utility
  index.css               — Tailwind imports, dark theme CSS vars, .prose-custom styles (colors use CSS var tokens, not hardcoded oklch literals)
```

## Conventions

- Import from `@/` (alias for `src/`). Example: `import { Button } from "@/components/ui/button"`.
- Use `--save-exact` when adding new packages: `pnpm add --save-exact <pkg>`
- Use `lucide-react` for icons. Size classes: `size-4` standard, `size-3.5` compact, `size-3` inline.
- API calls go through `lib/api.ts` — never call `fetch` directly from components.
- Auth uses JWT Bearer tokens stored in `localStorage`. The `login()` function stores the token automatically; all subsequent requests attach `Authorization: Bearer <token>`. On 401, the token is cleared and the page reloads to show the login screen.
- Markdown content (agent output, descriptions, comments) must use the `<Markdown>` component.
- Use `--legacy-peer-deps` when running `npm install` (Tailwind v4 peer dep conflicts).

## Build

```bash
pnpm run typecheck   # tsc -b (same as first step of build; CI + pre-commit use npm run typecheck)
pnpm run lint        # eslint .
pnpm run build       # tsc -b && vite build → outputs to dist/
pnpm dev             # vite dev server on :5173, proxies /api → :8080
```

## Agent Scripts

From within a frontend worktree you can run the project-level helper scripts:

```bash
# Validate you're in a worktree, not the main checkout
bash ../scripts/agent/validate-worktree.sh

# Run all checks (tsc, eslint, pnpm build, pytest)
bash ../scripts/agent/build-check.sh

# Commit, push, and open a PR
bash ../scripts/agent/commit-pr.sh
```
