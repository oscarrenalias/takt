// Feature tree screen: rollup header, collapsible bead tree, merge records and audit links.
// Live refetch on beads_changed is handled by the app shell (SCREENS.featuretree.beads = true),
// which re-runs `load` and re-renders this component with fresh data; expansion state lives in
// this component so it survives those refetches.
import { html, useState } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFeature } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { hrefs, relativeTime } from "./util.js";

const CSS_HREF = "/static/css/featuretree.css";
if (typeof document !== "undefined" && !document.querySelector(`link[href="${CSS_HREF}"]`)) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = CSS_HREF;
  document.head.appendChild(link);
}

export const load = ({ name, rootId }) => fetchFeature(name, rootId);

const BADGE_LABELS = {
  structural_assumptions: "◆ structural",
  unmet_criteria: "✗ unmet",
  descoped_criteria: "~ descoped",
  merged: "✓ merged",
  discovered: "discovered",
  corrective: "corrective",
  recovery: "recovery",
  high_priority: "high priority",
};
const BADGE_CLASS = {
  structural_assumptions: "status-warn",
  unmet_criteria: "status-error",
  descoped_criteria: "status-warn",
  merged: "status-ok",
};

export const badgeLabel = (b) => BADGE_LABELS[b] || String(b).replace(/_/g, " ");

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
  const s = Math.round(Number(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export const formatCost = (usd) => (typeof usd === "number" ? `$${usd.toFixed(2)}` : "—");

// Default expansion: root and its direct children open, deeper nodes collapsed.
const defaultOpen = (depth) => depth < 2;

function Verdict({ verdict }) {
  if (!verdict) return null;
  const cls = verdict === "approved" ? "status-ok" : "status-warn";
  return html`<span class=${`badge ft-verdict ${cls}`} data-testid="bead-verdict">${verdict.replace(/_/g, " ")}</span>`;
}

function TreeNode({ node, depth, project, expanded, toggle }) {
  const id = node.bead_id;
  const children = node.children || [];
  const deps = node.dependencies || [];
  const expandable = children.length > 0 || deps.length > 0;
  const open = expandable && (expanded[id] === undefined ? defaultOpen(depth) : expanded[id]);
  return html`<li class="ft-node" role="treeitem" aria-expanded=${expandable ? String(open) : undefined}
      data-testid=${`bead-tree-node-${id}`} data-status=${node.status || ""} data-depth=${depth}>
    <div class="ft-row">
      ${expandable
        ? html`<button type="button" class="ft-toggle" data-testid=${`bead-tree-toggle-${id}`}
            aria-label=${`${open ? "Collapse" : "Expand"} ${id}`} onClick=${() => toggle(id, open, depth)}>${open ? "▾" : "▸"}</button>`
        : html`<span class="ft-toggle ft-leaf" aria-hidden="true">·</span>`}
      <span class=${`badge ft-status status-${node.status || "unknown"}`} data-testid="bead-status">${(node.status || "unknown").replace(/_/g, " ")}</span>
      <a class="ft-id mono" href=${hrefs.bead(project, id)} data-testid=${`bead-link-${id}`}>${id}</a>
      <span class="ft-title">${node.title || ""}</span>
      <span class="ft-agent muted" data-testid="bead-agent">${node.agent_type || ""}</span>
      <${Verdict} verdict=${node.verdict} />
      ${(node.badges || []).map(
        (b) => html`<span key=${b} class=${`ft-badge ${BADGE_CLASS[b] || ""}`} data-testid=${`bead-badge-${b}`}>${badgeLabel(b)}</span>`,
      )}
    </div>
    ${open
      ? html`<div class="ft-detail" data-testid=${`bead-tree-detail-${id}`}>
          <div class="ft-deps">
            <span class="muted">Depends on:</span>
            ${deps.length === 0
              ? html`<span class="muted" data-testid=${`bead-deps-none-${id}`}> none</span>`
              : deps.map(
                  (d) => html`<a key=${d.bead_id} class=${`ft-dep mono status-${d.status || "unknown"}`}
                    href=${hrefs.bead(project, d.bead_id)} data-testid=${`bead-dep-${id}-${d.bead_id}`}
                    title=${d.status || "unknown"}>${d.bead_id}${d.status ? ` (${d.status})` : ""}</a>`,
                )}
          </div>
          ${children.length > 0
            ? html`<ul class="ft-children" role="group">
                ${children.map(
                  (c) => html`<${TreeNode} key=${c.bead_id} node=${c} depth=${depth + 1} project=${project} expanded=${expanded} toggle=${toggle} />`,
                )}
              </ul>`
            : null}
        </div>`
      : null}
  </li>`;
}

function Rollup({ feature }) {
  if (!feature) return null;
  const counts = feature.counts || {};
  const c = feature.criteria || {};
  const a = feature.assumptions || {};
  return html`<div class="ft-rollup" data-testid="feature-rollup">
    <h2 class="ft-heading">${feature.title || feature.bead_id}</h2>
    <div class="ft-rollup-row">
      <span class=${`badge status-${feature.status || "unknown"}`} data-testid="feature-status">${(feature.status || "unknown").replace(/_/g, " ")}</span>
      ${feature.in_flight ? html`<span class="badge status-running" data-testid="feature-in-flight">in flight</span>` : null}
      ${Object.entries(counts).filter(([, n]) => n > 0).map(
        ([s, n]) => html`<span key=${s} class=${`ft-count status-${s}`} data-testid=${`feature-count-${s}`}>${n} ${s.replace(/_/g, " ")}</span>`,
      )}
    </div>
    <div class="ft-rollup-row muted">
      <span data-testid="feature-criteria">criteria: ${c.met || 0} met · ${c.unmet || 0} unmet · ${c.descoped || 0} descoped</span>
      <span data-testid="feature-assumptions">assumptions: ${a.structural || 0} structural · ${a.local || 0} local</span>
      <span data-testid="feature-cost">cost ${formatCost(feature.cost_usd)}</span>
      <span data-testid="feature-wall-clock">wall clock ${formatDuration(feature.wall_clock_seconds)}</span>
      <span data-testid="feature-last-activity">active ${relativeTime(feature.last_activity_at)}</span>
    </div>
  </div>`;
}

function MergeRecords({ records }) {
  return html`<section class="ft-section" data-testid="merge-records">
    <h3>Merge records</h3>
    ${records.length === 0
      ? html`<p class="muted" data-testid="merge-records-empty">Not merged yet.</p>`
      : html`<ul class="ft-list">
          ${records.map((r, i) => {
            const rec = r && typeof r === "object" ? r : {};
            const sha = (v) => (typeof v === "string" ? v.slice(0, 8) : "");
            return html`<li key=${i} class="ft-merge" data-testid=${`merge-record-${i}`}>
              <strong>${rec.approver || "unknown approver"}</strong>
              <span class="muted"> ${rec.merged_at ? relativeTime(rec.merged_at) : ""}</span>
              ${rec.branch ? html`<span class="mono"> ${rec.branch}</span>` : null}
              ${rec.main_sha_after ? html`<span class="mono muted"> → ${sha(rec.main_sha_after)}</span>` : null}
            </li>`;
          })}
        </ul>`}
  </section>`;
}

function Audits({ audits, project, rootId }) {
  return html`<section class="ft-section" data-testid="audit-list">
    <h3>Audits</h3>
    ${audits.length === 0
      ? html`<p class="muted" data-testid="audit-list-empty">No audit snapshots.</p>`
      : html`<ul class="ft-list">
          ${audits.map(
            (a) => html`<li key=${a.audit_id}>
              <a class="mono" href=${hrefs.audit(project, rootId, a.audit_id)} data-testid=${`audit-link-${a.audit_id}`}>${a.audit_id}</a>
              <span class="muted"> ${a.generated_at ? relativeTime(a.generated_at) : ""}</span>
            </li>`,
          )}
        </ul>`}
  </section>`;
}

export function FeatureTree({ data, params }) {
  const [expanded, setExpanded] = useState({});
  const toggle = (id, open) => setExpanded((prev) => ({ ...prev, [id]: !open }));
  const project = params.name;
  const rootId = params.rootId;
  const tree = data && data.tree;
  return html`<section class="ft" data-testid="screen-feature-tree">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <${Rollup} feature=${data && data.feature} />
    ${tree
      ? html`<ul class="ft-tree" role="tree" data-testid="bead-tree">
          <${TreeNode} node=${tree} depth=${0} project=${project} expanded=${expanded} toggle=${toggle} />
        </ul>`
      : html`<p class="muted" data-testid="bead-tree-empty">No beads.</p>`}
    <${MergeRecords} records=${(data && data.merge_records) || []} />
    <${Audits} audits=${(data && data.audits) || []} project=${project} rootId=${rootId} />
  </section>`;
}
