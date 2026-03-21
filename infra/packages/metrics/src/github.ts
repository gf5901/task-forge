import { Resource } from "sst";

export interface GitHubResult {
  content_pages: number | null;
  recent_commits_7d: number | null;
  open_prs: number | null;
}

function getToken(): string {
  const res = Resource as { GitHubToken?: { value: string } };
  return res.GitHubToken?.value ?? "";
}

async function ghFetch(path: string): Promise<unknown> {
  const token = getToken();
  const resp = await fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: "application/vnd.github+json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!resp.ok) {
    console.error(`GitHub API error for ${path}: ${resp.status}`);
    return null;
  }
  return resp.json();
}

export async function fetchGitHubMetrics(
  owner: string,
  repo: string,
): Promise<GitHubResult> {
  const result: GitHubResult = {
    content_pages: null,
    recent_commits_7d: null,
    open_prs: null,
  };

  // Count content files (MDX pages in content/ directory)
  const tree = (await ghFetch(
    `/repos/${owner}/${repo}/git/trees/main?recursive=1`,
  )) as { tree?: { path: string }[] } | null;
  if (tree?.tree) {
    result.content_pages = tree.tree.filter(
      (f) =>
        (f.path.startsWith("content/") || f.path.startsWith("app/")) &&
        (f.path.endsWith(".mdx") || f.path.endsWith(".tsx")) &&
        f.path.includes("page"),
    ).length;

    // More precise: count .mdx files + page.tsx files
    const mdxCount = tree.tree.filter((f) => f.path.endsWith(".mdx")).length;
    const pageCount = tree.tree.filter(
      (f) => f.path.endsWith("page.tsx") && f.path.startsWith("app/"),
    ).length;
    result.content_pages = mdxCount + pageCount;
  }

  // Count commits in last 7 days
  const since = new Date(Date.now() - 7 * 86400_000).toISOString();
  const commits = (await ghFetch(
    `/repos/${owner}/${repo}/commits?since=${since}&per_page=100`,
  )) as unknown[] | null;
  if (Array.isArray(commits)) {
    result.recent_commits_7d = commits.length;
  }

  // Count open PRs
  const prs = (await ghFetch(
    `/repos/${owner}/${repo}/pulls?state=open&per_page=100`,
  )) as unknown[] | null;
  if (Array.isArray(prs)) {
    result.open_prs = prs.length;
  }

  return result;
}
