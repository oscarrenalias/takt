// Small helpers: relative time and hash route parsing.
export function relativeTime(iso, now = Date.now()) {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "unknown";
  const s = Math.round((now - t) / 1000);
  if (s < 5) return "just now";
  if (s < 0) return "in the future";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function parseRoute(hash) {
  const path = (hash || "").replace(/^#/, "") || "/";
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0) return { name: "overview", params: {} };
  if (parts[0] === "activity" && parts.length === 1) return { name: "activity", params: {} };
  if (parts[0] === "projects" && parts.length === 2) {
    let project = parts[1];
    try {
      project = decodeURIComponent(project);
    } catch (_) {
      // keep raw segment
    }
    return { name: "project", params: { name: project } };
  }
  return { name: "notfound", params: {} };
}
