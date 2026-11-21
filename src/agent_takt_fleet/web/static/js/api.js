// Same-origin fetch helpers for the read-only fleet API (ADR-31a0427e).
export const STREAM_URL = "/api/stream";

export async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin" });
  if (!res.ok) {
    const err = new Error(`${path}: HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const fetchFleet = () => getJSON("/api/fleet");

export const fetchProject = (name) => getJSON(`/api/projects/${encodeURIComponent(name)}`);

export function fetchEvents({ project, limit } = {}) {
  const q = new URLSearchParams();
  if (project) q.set("project", project);
  if (limit) q.set("limit", String(limit));
  const qs = q.toString();
  return getJSON(`/api/events${qs ? `?${qs}` : ""}`);
}
