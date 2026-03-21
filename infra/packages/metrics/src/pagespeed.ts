export interface PageSpeedResult {
  lighthouse_seo: number | null;
  lighthouse_perf: number | null;
  lighthouse_accessibility: number | null;
  lighthouse_best_practices: number | null;
}

const API_BASE = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed";

export async function fetchPageSpeedMetrics(url: string): Promise<PageSpeedResult> {
  const params = new URLSearchParams({
    url,
    category: "seo",
    strategy: "mobile",
  });
  // Run separate requests for each category since the API accepts one at a time more reliably
  const categories = ["seo", "performance", "accessibility", "best-practices"] as const;
  const allParams = new URLSearchParams({ url, strategy: "mobile" });
  for (const cat of categories) allParams.append("category", cat);

  const resp = await fetch(`${API_BASE}?${allParams.toString()}`);
  if (!resp.ok) {
    console.error(`PageSpeed API error: ${resp.status} ${await resp.text()}`);
    return { lighthouse_seo: null, lighthouse_perf: null, lighthouse_accessibility: null, lighthouse_best_practices: null };
  }

  const data = (await resp.json()) as {
    lighthouseResult?: {
      categories?: Record<string, { score?: number }>;
    };
  };

  const cats = data.lighthouseResult?.categories;
  const score = (key: string): number | null => {
    const s = cats?.[key]?.score;
    return typeof s === "number" ? Math.round(s * 100) : null;
  };

  return {
    lighthouse_seo: score("seo"),
    lighthouse_perf: score("performance"),
    lighthouse_accessibility: score("accessibility"),
    lighthouse_best_practices: score("best-practices"),
  };
}
