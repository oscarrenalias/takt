// Features screen: one rollup row per feature tree, linking to the tree view.
// The app shell refetches this screen on beads_changed for the route's project
// (SCREENS.features.beads), so rows stay live.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFeatures } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { relativeTime, hrefs } from "./util.js";

export const load = ({ name }) => fetchFeatures(name);

const STATUS_ORDER = ["done", "in_progress", "ready", "open", "blocked", "handed_off"];

export function formatCost(v) {
  return typeof v === "number" && Number.isFinite(v) ? `$${v.toFixed(2)}` : "—";
}

function StatusBar({ counts }) {
  const present = STATUS_ORDER.filter((s) => counts[s]);
  if (present.length === 0) return html`<div class="status-bar empty" data-testid="feature-status-bar"></div>`;
  return html`<div class="status-bar" role="img" data-testid="feature-status-bar"
      aria-label=${present.map((s) => `${counts[s]} ${s}`).join(", ")}>
    ${present.map(
      (s) => html`<span key=${s} class=${`seg seg-${s}`} data-status=${s} style=${`flex-grow:${counts[s]}`}></span>`
    )}
  </div>`;
}

function FeatureRow({ feature, name }) {
  const f = feature;
  const counts = f.counts || {};
  const crit = f.criteria || {};
  const asm = f.assumptions || {};
  const merge = f.last_merge;
  return html`<li class=${`feature-row${f.in_flight ? " in-flight" : ""}`} data-testid="feature-row"
      data-root-id=${f.bead_id} data-in-flight=${f.in_flight ? "true" : "false"}>
    <div class="feature-head">
      <a class="feature-title" href=${hrefs.feature(name, f.bead_id)} data-testid="feature-link">${f.title || f.bead_id}</a>
      <span class="mono feature-id">${f.bead_id}</span>
      ${f.in_flight ? html`<span class="badge status-running" data-testid="feature-in-flight">in flight</span>` : null}
    </div>
    <${StatusBar} counts=${counts} />
    <ul class="feature-stats">
      ${STATUS_ORDER.filter((s) => counts[s]).map(
        (s) => html`<li key=${s} class=${`status-${s}`} data-testid=${`feature-count-${s}`}>${counts[s]} ${s.replace("_", " ")}</li>`
      )}
    </ul>
    <dl class="feature-meta">
      <div><dt>Criteria</dt><dd data-testid="feature-criteria">${crit.met || 0} met · <span class=${crit.unmet ? "status-error" : ""}>${crit.unmet || 0} unmet</span> · ${crit.descoped || 0} descoped</dd></div>
      <div><dt>Assumptions</dt><dd data-testid="feature-assumptions"><span class=${asm.structural ? "status-warn" : ""}>${asm.structural || 0} structural</span> · ${asm.local || 0} local</dd></div>
      <div><dt>Cost</dt><dd data-testid="feature-cost">${formatCost(f.cost_usd)}</dd></div>
      <div><dt>Last merge</dt><dd data-testid="feature-last-merge">${merge
        ? html`<span title=${merge.merged_at || ""}>${relativeTime(merge.merged_at)}${merge.approver ? ` by ${merge.approver}` : ""}</span>`
        : "not merged"}</dd></div>
      <div><dt>Audits</dt><dd data-testid="feature-audits">${f.audits_count || 0}</dd></div>
      <div><dt>Activity</dt><dd title=${f.last_activity_at || ""}>${relativeTime(f.last_activity_at)}</dd></div>
    </dl>
  </li>`;
}

export function Features({ data, params }) {
  const features = (data && data.features) || [];
  const name = params.name;
  return html`<section data-testid="screen-features">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <h2>Features</h2>
    <p class="muted" data-testid="feature-count">${features.length} feature(s)</p>
    ${features.length === 0
      ? html`<p class="muted" data-testid="features-empty">No features in this project.</p>`
      : html`<ul class="feature-list" data-testid="feature-list">
          ${features.map((f) => html`<${FeatureRow} key=${f.bead_id} feature=${f} name=${name} />`)}
        </ul>`}
  </section>`;
}
