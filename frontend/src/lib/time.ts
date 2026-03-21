export function timeAgo(dt: string): string {
  try {
    const seconds = (Date.now() - new Date(dt).getTime()) / 1000
    if (seconds < 60) return "just now"
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`
    return `${Math.floor(seconds / 604800)}w ago`
  } catch {
    return dt
  }
}
