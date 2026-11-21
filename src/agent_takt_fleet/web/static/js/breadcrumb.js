// Breadcrumb: Overview › project › feature › bead › trace, as applicable to the route.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { hrefs } from "./util.js";

// Build segments [{label, href?, testid}] from route params; last segment has no href.
export function breadcrumbSegments(params = {}) {
  const { name, rootId, beadId, seq, attempt, auditId, runId, section } = params;
  const segs = [{ label: "Overview", href: "#/", key: "overview" }];
  if (!name) return segs;
  segs.push({ label: name, href: hrefs.project(name), key: "project" });
  if (section === "features" || rootId) {
    segs.push({ label: "Features", href: hrefs.features(name), key: "features" });
    if (rootId) segs.push({ label: rootId, href: hrefs.feature(name, rootId), key: "feature" });
    if (auditId) segs.push({ label: `Audit ${auditId}`, href: hrefs.audit(name, rootId, auditId), key: "audit" });
  } else if (section === "runs" || runId) {
    segs.push({ label: "Runs", href: hrefs.runs(name), key: "runs" });
    if (runId) segs.push({ label: runId, href: hrefs.run(name, runId), key: "run" });
  } else if (section === "beads" || beadId) {
    segs.push({ label: "Beads", href: hrefs.beads(name), key: "beads" });
    if (beadId) segs.push({ label: beadId, href: hrefs.bead(name, beadId), key: "bead" });
    if (seq !== undefined) segs.push({ label: `Trace ${seq}`, href: hrefs.trace(name, beadId, seq), key: "trace" });
    if (attempt !== undefined) segs.push({ label: `Telemetry ${attempt}`, href: hrefs.telemetry(name, beadId, attempt), key: "telemetry" });
  }
  return segs;
}

export function Breadcrumb({ params }) {
  const segs = breadcrumbSegments(params);
  return html`<nav class="breadcrumb" aria-label="Breadcrumb" data-testid="breadcrumb">
    ${segs.map((s, i) => {
      const last = i === segs.length - 1;
      return html`<span key=${s.key} class="crumb">
        ${i > 0 ? html`<span class="crumb-sep" aria-hidden="true"> › </span>` : null}
        ${last
          ? html`<span aria-current="page" data-testid=${`breadcrumb-${s.key}`}>${s.label}</span>`
          : html`<a href=${s.href} data-testid=${`breadcrumb-${s.key}`}>${s.label}</a>`}
      </span>`;
    })}
  </nav>`;
}
