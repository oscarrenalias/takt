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

const dec = (v) => {
  try {
    return decodeURIComponent(v);
  } catch (_) {
    return v; // keep raw segment
  }
};

// Hash router. Returns { name, params }; unknown paths give name "notfound".
//   #/                                              overview
//   #/activity                                      activity
//   #/projects/<name>                               project
//   #/projects/<name>/features                      features
//   #/projects/<name>/features/<root_id>            featuretree
//   #/projects/<name>/features/<root_id>/audits/<audit_id>   audit
//   #/projects/<name>/runs                          runs
//   #/projects/<name>/runs/<run_id>                 run
//   #/projects/<name>/beads                         beads
//   #/projects/<name>/beads/<bead_id>               beaddetail
//   #/projects/<name>/beads/<bead_id>/traces/<seq>  trace
//   #/projects/<name>/beads/<bead_id>/telemetry/<attempt>    telemetry
export function parseRoute(hash) {
  const path = (hash || "").replace(/^#/, "") || "/";
  const parts = path.split("/").filter(Boolean).map(dec);
  const nf = { name: "notfound", params: {} };
  if (parts.length === 0) return { name: "overview", params: {} };
  if (parts[0] === "activity" && parts.length === 1) return { name: "activity", params: {} };
  if (parts[0] !== "projects" || parts.length < 2) return nf;
  const name = parts[1];
  const rest = parts.slice(2);
  if (rest.length === 0) return { name: "project", params: { name } };
  const [sec, id, sub, subId] = rest;
  if (sec === "features") {
    if (rest.length === 1) return { name: "features", params: { name } };
    if (rest.length === 2) return { name: "featuretree", params: { name, rootId: id } };
    if (rest.length === 4 && sub === "audits") return { name: "audit", params: { name, rootId: id, auditId: subId } };
  } else if (sec === "runs") {
    if (rest.length === 1) return { name: "runs", params: { name } };
    if (rest.length === 2) return { name: "run", params: { name, runId: id } };
  } else if (sec === "beads") {
    if (rest.length === 1) return { name: "beads", params: { name } };
    if (rest.length === 2) return { name: "beaddetail", params: { name, beadId: id } };
    if (rest.length === 4 && sub === "traces") return { name: "trace", params: { name, beadId: id, seq: subId } };
    if (rest.length === 4 && sub === "telemetry") return { name: "telemetry", params: { name, beadId: id, attempt: subId } };
  }
  return nf;
}

// Hash builders for links.
const enc = encodeURIComponent;
export const hrefs = {
  project: (n) => `#/projects/${enc(n)}`,
  features: (n) => `#/projects/${enc(n)}/features`,
  feature: (n, r) => `#/projects/${enc(n)}/features/${enc(r)}`,
  audit: (n, r, a) => `#/projects/${enc(n)}/features/${enc(r)}/audits/${enc(a)}`,
  runs: (n) => `#/projects/${enc(n)}/runs`,
  run: (n, r) => `#/projects/${enc(n)}/runs/${enc(r)}`,
  beads: (n) => `#/projects/${enc(n)}/beads`,
  bead: (n, b) => `#/projects/${enc(n)}/beads/${enc(b)}`,
  trace: (n, b, q) => `#/projects/${enc(n)}/beads/${enc(b)}/traces/${enc(q)}`,
  telemetry: (n, b, a) => `#/projects/${enc(n)}/beads/${enc(b)}/telemetry/${enc(a)}`,
};
