"""
Git worktree management for the task runner.

Handles creating isolated worktrees per task, installing deps, cleanup,
and resolving which repo directory to operate in.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .pipeline_log import emit as plog

log = logging.getLogger(__name__)

WORK_DIR = Path(os.getenv("WORK_DIR", str(Path.home())))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_ROOT.parent
WORKTREE_BASE = Path(os.getenv("WORKTREE_BASE", "/tmp/task-worktrees"))
GH_BIN = os.getenv("GH_BIN", "gh")


def _run_cmd(args, cwd=None, timeout=30):
    # type: (list, Optional[str], int) -> subprocess.CompletedProcess
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or str(WORK_DIR),
    )


def _slugify_branch(text):
    # type: (str) -> str
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)[:40]


def _get_default_branch(repo_dir):
    # type: (str) -> str
    result = _run_cmd(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=repo_dir,
    )
    branch = result.stdout.strip().replace("refs/remotes/origin/", "")
    if branch:
        return branch
    # origin/HEAD not set — ask the remote directly
    result2 = _run_cmd(["git", "remote", "show", "origin"], cwd=repo_dir, timeout=15)
    for line in result2.stdout.splitlines():
        line = line.strip()
        if line.startswith("HEAD branch:"):
            detected = line.split(":", 1)[1].strip()
            if detected and detected != "(unknown)":
                return detected
    return "main"


def _resolve_repo_dir(task):
    """Return the repo root to create worktrees from.

    target_repo can be:
    - a bare name like "my-project" → resolved to WORKSPACE_DIR/my-project
    - a legacy absolute path → used as-is if it's a valid git repo
    Falls back to PROJECT_ROOT (the bot's own repo).
    """
    if task.target_repo:
        name = task.target_repo.strip()
        if "/" not in name:
            candidate = WORKSPACE_DIR / name
        else:
            candidate = Path(name).expanduser()
        if candidate.is_dir() and (candidate / ".git").exists():
            return str(candidate)
    return str(PROJECT_ROOT)


def ensure_repo(task):
    # type: (...) -> Optional[str]
    """Ensure the target repo exists locally and on GitHub.

    If target_repo is set but the directory doesn't exist yet:
    - Creates WORKSPACE_DIR/<name>
    - git init with an initial commit
    - Creates a private GitHub repo via `gh repo create` and pushes

    Returns the repo path if it exists or was created, None if no target_repo.
    """
    if not task.target_repo:
        return None

    name = task.target_repo.strip()
    if "/" in name:
        # Absolute path — not managed here
        return None

    repo_path = WORKSPACE_DIR / name

    if repo_path.is_dir() and (repo_path / ".git").exists():
        log.info("Repo %s already exists at %s", name, repo_path)
        return str(repo_path)

    log.info("Creating new repo %s at %s", name, repo_path)
    plog(task.id, "repo_create_start", "setup", "Creating repo %s" % name)

    repo_path.mkdir(parents=True, exist_ok=True)

    # Determine GitHub username from gh CLI
    whoami = _run_cmd(["gh", "api", "user", "--jq", ".login"], cwd=str(repo_path))
    gh_user = whoami.stdout.strip() if whoami.returncode == 0 else ""

    # git init + initial commit so gh can push
    _run_cmd(["git", "init", "-b", "main"], cwd=str(repo_path))
    _run_cmd(["git", "config", "user.email", "bot@agent.local"], cwd=str(repo_path))
    _run_cmd(["git", "config", "user.name", "Agent Bot"], cwd=str(repo_path))
    readme = repo_path / "README.md"
    readme.write_text("# %s\n" % name)
    _run_cmd(["git", "add", "README.md"], cwd=str(repo_path))
    _run_cmd(["git", "commit", "-m", "Initial commit"], cwd=str(repo_path))

    # Create GitHub repo and push
    gh_create = _run_cmd(
        [
            "gh",
            "repo",
            "create",
            "%s/%s" % (gh_user, name) if gh_user else name,
            "--private",
            "--source=.",
            "--push",
        ],
        cwd=str(repo_path),
        timeout=60,
    )
    if gh_create.returncode == 0:
        log.info("Created GitHub repo for %s", name)
        plog(task.id, "repo_create_done", "setup", "GitHub repo created: %s/%s" % (gh_user, name))
    else:
        log.warning("gh repo create failed for %s: %s", name, gh_create.stderr[:200])
        plog(task.id, "repo_create_warn", "setup", "gh repo create failed — repo is local only")

    return str(repo_path)


def create_worktree(task):
    # type: (...) -> Optional[str]
    """Create an isolated worktree for this task, branched from the default branch.

    Returns the worktree path, or None on failure.
    """
    repo_dir = _resolve_repo_dir(task)

    is_repo = _run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_dir)
    if is_repo.returncode != 0:
        log.info("Repo dir %s is not a git repo, running without worktree", repo_dir)
        return None

    repo_root = _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir).stdout.strip()

    _run_cmd(["git", "fetch", "origin"], cwd=repo_root, timeout=60)

    default_branch = _get_default_branch(repo_root)
    slug = _slugify_branch(task.title)
    branch = "task/%s-%s" % (task.id, slug)

    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
    wt_path = str(WORKTREE_BASE / ("task-%s" % task.id))

    # Clean up stale worktree if it exists from a previous failed run
    if Path(wt_path).exists():
        _run_cmd(["git", "worktree", "remove", "--force", wt_path], cwd=repo_root)
        if Path(wt_path).exists():
            shutil.rmtree(wt_path, ignore_errors=True)

    # Delete stale branch if it exists
    _run_cmd(["git", "branch", "-D", branch], cwd=repo_root)

    result = _run_cmd(
        ["git", "worktree", "add", "-b", branch, wt_path, "origin/%s" % default_branch],
        cwd=repo_root,
    )
    if result.returncode != 0:
        log.error("Failed to create worktree: %s", result.stderr)
        return None

    log.info("Created worktree at %s on branch %s", wt_path, branch)
    plog(task.id, "worktree_created", "worktree", "Created at %s on branch %s" % (wt_path, branch))

    _install_worktree_deps(wt_path)

    return wt_path


def _install_worktree_deps(wt_path):
    # type: (str) -> None
    """Install dependencies in the worktree if lockfiles are present."""
    wt = Path(wt_path)

    fe_pkg = wt / "frontend" / "package.json"
    if fe_pkg.exists():
        wt_nm = wt / "frontend" / "node_modules"
        main_nm = PROJECT_ROOT / "frontend" / "node_modules"
        if not wt_nm.exists():
            if main_nm.exists():
                # Hard-link copy: fast (~1-2s vs 20-30s for npm install) and
                # fully isolated — new packages installed in the worktree don't
                # touch the main checkout. Symlinks would share the directory.
                log.info("Hard-linking node_modules from main checkout into worktree")
                r = _run_cmd(
                    ["cp", "-al", str(main_nm), str(wt_nm)],
                    cwd=str(wt / "frontend"),
                    timeout=30,
                )
                if r.returncode != 0:
                    log.warning("cp -al failed (%s), falling back to pnpm install", r.stderr[:100])
                    main_nm = Path("")  # force pnpm install below

            if not wt_nm.exists():
                log.info("Installing frontend deps in worktree via pnpm")
                pnpm_bin = shutil.which("pnpm") or "pnpm"
                r = _run_cmd(
                    [pnpm_bin, "install", "--frozen-lockfile"],
                    cwd=str(wt / "frontend"),
                    timeout=120,
                )
                if r.returncode != 0:
                    log.warning("pnpm install failed in worktree: %s", r.stderr[:200])

    req = wt / "requirements.txt"
    if req.exists() and (wt / ".venv").exists():
        log.info("Installing Python deps in worktree")
        _run_cmd(
            [str(wt / ".venv" / "bin" / "pip"), "install", "-r", str(req), "-q"],
            cwd=wt_path,
            timeout=120,
        )


def cleanup_worktree(task, wt_path):
    # type: (..., str) -> None
    """Remove a task worktree and its branch."""
    repo_dir = _resolve_repo_dir(task)
    repo_root = (
        _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir).stdout.strip() or repo_dir
    )

    _run_cmd(["git", "worktree", "remove", "--force", wt_path], cwd=repo_root)
    if Path(wt_path).exists():
        shutil.rmtree(wt_path, ignore_errors=True)

    slug = _slugify_branch(task.title)
    branch = "task/%s-%s" % (task.id, slug)
    _run_cmd(["git", "branch", "-D", branch], cwd=repo_root)
    log.info("Cleaned up worktree %s and branch %s", wt_path, branch)
    plog(task.id, "worktree_cleaned", "cleanup", "Removed %s" % wt_path)


def delete_task_artifacts(task):
    # type: (...) -> Dict[str, bool]
    """Best-effort cleanup of all artifacts left by a task after it is deleted.

    Removes:
    - The remote git branch (reduces agent search space / GitHub branch list)
    - The local git branch (if it somehow wasn't cleaned up after the run)
    - The worktree directory (if a crash left it behind)
    - The pidfile (stale after task file is gone)

    Returns a dict of what was attempted and whether it succeeded, for logging.
    """
    from .runner import remove_pidfile

    results = {}  # type: Dict[str, bool]
    slug = _slugify_branch(task.title)
    branch = "task/%s-%s" % (task.id, slug)

    repo_dir = _resolve_repo_dir(task)
    repo_root = (
        _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir).stdout.strip() or repo_dir
    )

    # Remove remote branch so agents don't see it in `git branch -r`
    r = _run_cmd(["git", "push", "origin", "--delete", branch], cwd=repo_root, timeout=30)
    results["remote_branch"] = r.returncode == 0

    # Remove local branch (may already be gone — ignore errors)
    r = _run_cmd(["git", "branch", "-D", branch], cwd=repo_root)
    results["local_branch"] = r.returncode == 0

    # Remove worktree if it was somehow left behind
    wt_path = str(WORKTREE_BASE / ("task-%s" % task.id))
    if Path(wt_path).exists():
        _run_cmd(["git", "worktree", "remove", "--force", wt_path], cwd=repo_root)
        shutil.rmtree(wt_path, ignore_errors=True)
        results["worktree"] = not Path(wt_path).exists()
    else:
        results["worktree"] = True

    # Remove stale pidfile
    remove_pidfile(task.id)
    results["pidfile"] = True

    log.info("delete_task_artifacts %s: %s", task.id, results)
    return results


def resolve_target_repo_path(target_repo):
    # type: (str) -> Optional[str]
    """Return git repo root for project's target_repo, or None if unset/invalid.

    Does not fall back to PROJECT_ROOT — daily cycle only links a real target checkout.
    """
    if not target_repo or not str(target_repo).strip():
        return None
    name = str(target_repo).strip()
    if "/" not in name:
        candidate = WORKSPACE_DIR / name
    else:
        candidate = Path(name).expanduser()
    if candidate.is_dir() and (candidate / ".git").exists():
        return str(candidate)
    return None


def cycle_worktree_path(project_id):
    # type: (str) -> str
    """Detached read worktree path for daily cycle (distinct from task-* paths)."""
    safe = re.sub(r"[^\w\-]", "-", project_id)[:64]
    return str(WORKTREE_BASE / ("cycle-%s" % safe))


def create_cycle_read_worktree(repo_dir, project_id):
    # type: (str, str) -> Optional[str]
    """Create detached worktree at cycle-<project_id> for read-only browsing. Returns path or None."""
    wt = cycle_worktree_path(project_id)
    repo_root = (
        _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir).stdout.strip() or repo_dir
    )
    if Path(wt).exists():
        _run_cmd(["git", "worktree", "remove", "--force", wt], cwd=repo_root, timeout=60)
        shutil.rmtree(wt, ignore_errors=True)
    fetch = _run_cmd(["git", "fetch", "origin"], cwd=repo_root, timeout=120)
    if fetch.returncode != 0:
        log.warning("create_cycle_read_worktree: fetch failed: %s", fetch.stderr[:200])
    default_branch = _get_default_branch(repo_root)
    ref = "origin/%s" % default_branch
    add = _run_cmd(
        ["git", "worktree", "add", "--detach", wt, ref],
        cwd=repo_root,
        timeout=120,
    )
    if add.returncode != 0:
        log.warning("create_cycle_read_worktree: worktree add failed: %s", add.stderr[:300])
        return None
    return wt


def remove_cycle_worktree(worktree_path, repo_dir):
    # type: (str, str) -> None
    """Remove a cycle-* worktree created by create_cycle_read_worktree."""
    if not worktree_path or not Path(worktree_path).exists():
        return
    repo_root = (
        _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo_dir).stdout.strip() or repo_dir
    )
    r = _run_cmd(["git", "worktree", "remove", "--force", worktree_path], cwd=repo_root, timeout=60)
    if r.returncode != 0:
        log.warning("remove_cycle_worktree: %s", r.stderr[:200])
        shutil.rmtree(worktree_path, ignore_errors=True)
