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

const proj = (name) => `/api/projects/${encodeURIComponent(name)}`;
const seg = (v) => encodeURIComponent(v);

export function fetchBeads(name, params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, String(x)));
    else q.set(k, String(v));
  }
  const qs = q.toString();
  return getJSON(`${proj(name)}/beads${qs ? `?${qs}` : ""}`);
}
export const fetchBead = (name, beadId) => getJSON(`${proj(name)}/beads/${seg(beadId)}`);
export const fetchTrace = (name, beadId, seq) => getJSON(`${proj(name)}/beads/${seg(beadId)}/traces/${seg(seq)}`);
export const fetchTelemetry = (name, beadId, attempt) =>
  getJSON(`${proj(name)}/beads/${seg(beadId)}/telemetry/${seg(attempt)}`);
export const fetchFeatures = (name) => getJSON(`${proj(name)}/features`);
export const fetchFeature = (name, rootId) => getJSON(`${proj(name)}/features/${seg(rootId)}`);
export const fetchAudit = (name, rootId, auditId) =>
  getJSON(`${proj(name)}/features/${seg(rootId)}/audits/${seg(auditId)}`);
