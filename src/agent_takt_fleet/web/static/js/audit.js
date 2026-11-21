// Audit screen: renders a stored audit snapshot section by section (data-testid="audit-section").
// Keys the view does not know about are never dropped: unknown top-level keys appear in an
// "Other" section and unknown nested keys in a generic key/value block inside their section.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchAudit } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { hrefs } from "./util.js";

const CSS_HREF = "/static/css/audit.css";
if (typeof document !== "undefined" && !document.querySelector(`link[href="${CSS_HREF}"]`)) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = CSS_HREF;
  document.head.appendChild(link);
}

export const load = ({ name, rootId, auditId }) => fetchAudit(name, rootId, auditId);

const NOT_RECORDED = "not recorded";
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isEmpty = (v) => v === null || v === undefined || v === "";
const show = (v) => (isEmpty(v) ? NOT_RECORDED : String(v));
export const fmtCost = (v) => (isEmpty(v) || Number.isNaN(Number(v)) ? NOT_RECORDED : `$${Number(v).toFixed(4)}`);
export const fmtSeconds = (v) => (isEmpty(v) || Number.isNaN(Number(v)) ? NOT_RECORDED : `${Number(v).toFixed(1)}s`);

// Entries of `obj` whose keys are not in `known`; null when there are none.
export function extras(obj, known) {
  if (!isObj(obj)) return null;
  const rest = {};
  for (const k of Object.keys(obj)) if (!known.includes(k)) rest[k] = obj[k];
  return Object.keys(rest).length ? rest : null;
}

// Generic recursive key/value rendering for data the view has no dedicated layout for.
export function KeyValue({ value }) {
  if (isObj(value)) {
    const keys = Object.keys(value);
    if (!keys.length) return html`<span class="muted">{}</span>`;
    return html`<dl class="audit-kv" data-testid="audit-generic">
      ${keys.map((k) => html`<div key=${k} class="audit-kv-row"><dt class="mono">${k}</dt><dd><${KeyValue} value=${value[k]} /></dd></div>`)}
    </dl>`;
  }
  if (Array.isArray(value)) {
    if (!value.length) return html`<span class="muted">[]</span>`;
    return html`<ul class="audit-kv-list">${value.map((v, i) => html`<li key=${i}><${KeyValue} value=${v} /></li>`)}</ul>`;
  }
  if (value === null || value === undefined) return html`<span class="muted">null</span>`;
  return html`<span class="mono">${String(value)}</span>`;
}

const Extra = ({ obj, known, label }) => {
  const rest = extras(obj, known);
  return rest ? html`<div class="audit-extra" data-testid="audit-extra"><h4>${label || "Other fields"}</h4><${KeyValue} value=${rest} /></div>` : null;
};

const Section = ({ id, title, children }) => html`<section class="audit-section" data-testid="audit-section" data-section=${id}>
  <h3>${title}</h3>${children}</section>`;

const Empty = ({ text }) => html`<p class="muted">${text}</p>`;
const Status = ({ value }) => html`<span class=${`audit-status audit-status-${String(value || "none").replace(/[^a-z_]/gi, "")}`}>${show(value)}</span>`;

function docLabel(d) {
  if (!isObj(d)) return String(d);
  if ("id" in d) {
    let s = [d.id, d.title].filter(Boolean).join(" ");
    if (d.status) s += ` [${d.status}]`;
    return d.current ? `${s} → ${d.current}` : `${s} (not found)`;
  }
  if ("recorded" in d) return d.current ? `${d.current} (recorded as ${d.recorded})` : `${d.recorded} (not found)`;
  return null;
}

function Feature({ f }) {
  if (!isObj(f)) return html`<${KeyValue} value=${f} />`;
  const docs = Array.isArray(f.linked_docs) ? f.linked_docs : [];
  return html`<div>
    <dl class="audit-props">
      <div><dt>Root bead</dt><dd class="mono" data-testid="audit-feature-id">${show(f.bead_id)}</dd></div>
      <div><dt>Title</dt><dd>${show(f.title)}</dd></div>
      <div><dt>Branch</dt><dd class="mono">${show(f.branch)}</dd></div>
      <div><dt>Worktree</dt><dd class="mono">${show(f.worktree_path)}</dd></div>
      <div><dt>Generated</dt><dd>${show(f.generated_at)}</dd></div>
      <div><dt>takt version</dt><dd>${show(f.takt_version)}</dd></div>
    </dl>
    ${docs.length ? html`<ul class="audit-docs">${docs.map((d, i) => html`<li key=${i}>${docLabel(d) ?? html`<${KeyValue} value=${d} />`}</li>`)}</ul>` : null}
    <${Extra} obj=${f} known=${["bead_id", "title", "linked_docs", "branch", "worktree_path", "generated_at", "takt_version"]} />
  </div>`;
}

function StatusSection({ s }) {
  if (!isObj(s)) return html`<${KeyValue} value=${s} />`;
  const counts = isObj(s.counts) ? s.counts : {};
  const notDone = Array.isArray(s.not_done) ? s.not_done : [];
  return html`<div>
    <div class="audit-counts" data-testid="audit-status-counts">
      ${Object.keys(counts).map((k) => html`<span key=${k} class="audit-count"><${Status} value=${k} /> ${counts[k]}</span>`)}
    </div>
    ${notDone.length
      ? html`<ul class="audit-list" data-testid="audit-not-done">${notDone.map((b, i) => html`<li key=${i}><span class="mono">${show(b && b.bead_id)}</span> ${(b && b.title) || ""}<${Extra} obj=${b} known=${["bead_id", "title"]} /></li>`)}</ul>`
      : html`<p class="muted">All beads done.</p>`}
    <${Extra} obj=${s} known=${["counts", "not_done"]} />
  </div>`;
}

const BEAD_KNOWN = ["bead_id", "agent_type", "bead_type", "status", "verdict", "findings_count", "corrective_children", "recovery_children", "wall_clock_seconds", "cost_usd", "trace"];

function TraceText({ t }) {
  if (!isObj(t)) return html`<span class="muted">no traces</span>`;
  const d = isObj(t.documents) ? t.documents : {};
  return html`<span>${t.traces ?? 0} trace(s), ${t.tool_calls ?? 0} calls (${t.tool_errors ?? 0} errors), docs ${d.linked ?? 0}/${d.proactive ?? 0}/${d.search ?? 0}</span>`;
}

function Beads({ rows }) {
  if (!Array.isArray(rows)) return html`<${KeyValue} value=${rows} />`;
  if (!rows.length) return html`<${Empty} text="No beads." />`;
  return html`<div class="table-wrap"><table class="audit-table" data-testid="audit-beads">
    <thead><tr><th>Bead</th><th>Agent</th><th>Type</th><th>Status</th><th>Verdict</th><th>Findings</th><th>Corrective</th><th>Recovery</th><th>Wall clock</th><th>Cost</th><th>Trace</th><th>Other</th></tr></thead>
    <tbody>${rows.map((b, i) => html`<tr key=${i} data-testid="audit-bead-row">
      <td class="mono">${show(b.bead_id)}</td>
      <td>${show(b.agent_type)}</td><td>${show(b.bead_type)}</td><td><${Status} value=${b.status} /></td>
      <td><${Status} value=${b.verdict} /></td><td>${show(b.findings_count)}</td>
      <td>${show(b.corrective_children)}</td><td>${show(b.recovery_children)}</td>
      <td>${fmtSeconds(b.wall_clock_seconds)}</td><td>${fmtCost(b.cost_usd)}</td>
      <td><${TraceText} t=${b.trace} /></td>
      <td><${Extra} obj=${b} known=${BEAD_KNOWN} label="" /></td>
    </tr>`)}</tbody></table></div>`;
}

function Gates({ rows }) {
  if (!Array.isArray(rows)) return html`<${KeyValue} value=${rows} />`;
  if (!rows.length) return html`<${Empty} text="No gate beads." />`;
  return html`<ul class="audit-list" data-testid="audit-gates">${rows.map((g, i) => {
    const ov = Array.isArray(g.verdict_overridden) ? g.verdict_overridden : [];
    return html`<li key=${i} data-testid="audit-gate">
      <span class="mono">${show(g.bead_id)}</span> ${show(g.agent_type)} — final <${Status} value=${g.final_verdict} />,
      prior needs_changes: ${show(g.prior_needs_changes)}
      ${ov.length ? html`<ul class="audit-overrides">${ov.map((o, j) => html`<li key=${j}>${show(o && o.timestamp)} ${(o && o.summary) || ""}${o && !isEmpty(o.details) && Object.keys(o.details).length ? html`<${KeyValue} value=${o.details} />` : null}<${Extra} obj=${o} known=${["timestamp", "summary", "details"]} /></li>`)}</ul>` : null}
      <${Extra} obj=${g} known=${["bead_id", "agent_type", "final_verdict", "prior_needs_changes", "verdict_overridden"]} />
    </li>`;
  })}</ul>`;
}

function Assumptions({ a }) {
  if (!isObj(a)) return html`<${KeyValue} value=${a} />`;
  const items = Array.isArray(a.items) ? a.items : [];
  const c = isObj(a.counts) ? a.counts : {};
  return html`<div>
    <p data-testid="audit-assumption-counts">Structural: ${c.structural ?? 0} · Local: ${c.local ?? 0}</p>
    ${items.length ? html`<ul class="audit-list">${items.map((it, i) => html`<li key=${i} data-scope=${it && it.scope}>
      <strong>${(it && it.scope) || "structural"}</strong> <span class="mono">${show(it && it.bead_id)}</span> ${(it && it.statement) || ""}
      ${it && it.rationale ? html`<div class="muted">${it.rationale}</div>` : null}
      <${Extra} obj=${it} known=${["statement", "rationale", "scope", "bead_id"]} />
    </li>`)}</ul>` : html`<${Empty} text="None recorded." />`}
    <${Extra} obj=${a} known=${["items", "counts"]} />
  </div>`;
}

function Criteria({ c }) {
  if (c === null || c === undefined) return html`<${Empty} text="Not recorded." />`;
  if (!isObj(c)) return html`<${KeyValue} value=${c} />`;
  const gaps = Array.isArray(c.gaps) ? c.gaps : [];
  return html`<div>
    <p data-testid="audit-criteria-totals">Met: ${c.met ?? 0} · Unmet: ${c.unmet ?? 0} · Descoped: ${c.descoped ?? 0}</p>
    ${gaps.length ? html`<ul class="audit-list" data-testid="audit-criteria-gaps">${gaps.map((g, i) => html`<li key=${i} data-status=${g && g.status}>
      <${Status} value=${g && g.status} /> <span class="mono">${show(g && g.bead_id)} #${show(g && g.index)}</span> ${(g && g.criterion) || ""}
      ${g && g.evidence ? html`<div class="muted">${g.evidence}</div>` : null}
      <${Extra} obj=${g} known=${["bead_id", "index", "status", "criterion", "evidence"]} />
    </li>`)}</ul>` : html`<${Empty} text="No gaps." />`}
    <${Extra} obj=${c} known=${["met", "unmet", "descoped", "gaps"]} />
  </div>`;
}

function Cost({ c }) {
  if (!isObj(c)) return html`<${KeyValue} value=${c} />`;
  return html`<div><dl class="audit-props">
    <div><dt>Total cost</dt><dd data-testid="audit-cost-total">${fmtCost(c.total_cost_usd)}</dd></div>
    <div><dt>Wall clock</dt><dd>${fmtSeconds(c.total_wall_clock_seconds)}</dd></div>
    <div><dt>Beads with telemetry</dt><dd>${show(c.beads_with_telemetry)}</dd></div>
  </dl><${Extra} obj=${c} known=${["total_cost_usd", "total_wall_clock_seconds", "beads_with_telemetry"]} /></div>`;
}

function TraceTotals({ t }) {
  if (t === null || t === undefined) return html`<${Empty} text="Not recorded." />`;
  if (!isObj(t)) return html`<${KeyValue} value=${t} />`;
  const d = isObj(t.documents) ? t.documents : {};
  return html`<div><dl class="audit-props">
    <div><dt>Traces</dt><dd>${show(t.traces)}</dd></div>
    <div><dt>Tool calls</dt><dd>${show(t.tool_calls)}</dd></div>
    <div><dt>Tool errors</dt><dd>${show(t.tool_errors)}</dd></div>
    <div><dt>Docs linked</dt><dd>${show(d.linked)}</dd></div>
    <div><dt>Docs proactive</dt><dd>${show(d.proactive)}</dd></div>
    <div><dt>Docs search</dt><dd>${show(d.search)}</dd></div>
  </dl><${Extra} obj=${t} known=${["traces", "tool_calls", "tool_errors", "documents"]} />
  <${Extra} obj=${d} known=${["linked", "proactive", "search"]} label="Other document fields" /></div>`;
}

function Merges({ rows }) {
  if (!Array.isArray(rows)) return html`<${KeyValue} value=${rows} />`;
  if (!rows.length) return html`<${Empty} text="No merge records." />`;
  return html`<ul class="audit-list" data-testid="audit-merges">${rows.map((m, i) => html`<li key=${i}><${KeyValue} value=${m} /></li>`)}</ul>`;
}

function ChangedFiles({ files }) {
  if (!Array.isArray(files)) return html`<${KeyValue} value=${files} />`;
  return files.length ? html`<ul class="audit-files mono">${files.map((f) => html`<li key=${f}>${f}</li>`)}</ul>` : html`<${Empty} text="None." />`;
}

// Ordered known sections: [key, title, renderer]
const SECTIONS = [
  ["feature", "Feature", (v) => html`<${Feature} f=${v} />`],
  ["status", "Status", (v) => html`<${StatusSection} s=${v} />`],
  ["beads", "Beads", (v) => html`<${Beads} rows=${v} />`],
  ["trace", "Agent activity", (v) => html`<${TraceTotals} t=${v} />`],
  ["gates", "Gates", (v) => html`<${Gates} rows=${v} />`],
  ["assumptions", "Assumptions", (v) => html`<${Assumptions} a=${v} />`],
  ["criteria", "Criteria", (v) => html`<${Criteria} c=${v} />`],
  ["changed_files", "Changed files", (v) => html`<${ChangedFiles} files=${v} />`],
  ["cost", "Cost", (v) => html`<${Cost} c=${v} />`],
  ["merges", "Merges", (v) => html`<${Merges} rows=${v} />`],
];
const META_KEYS = ["audit_schema_version", "audit_id", "generated_at"];
export const KNOWN_TOP_LEVEL = [...SECTIONS.map(([k]) => k), ...META_KEYS];

export function Audit({ data, params }) {
  const d = isObj(data) ? data : {};
  const unknown = extras(d, KNOWN_TOP_LEVEL);
  return html`<section class="audit" data-testid="screen-audit">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <h2>Audit <span class="mono">${d.audit_id || params.auditId}</span></h2>
    <p class="muted" data-testid="audit-meta">Schema ${show(d.audit_schema_version)} · generated ${show(d.generated_at)}
      · <a href=${hrefs.feature(params.name, params.rootId)} data-testid="audit-back">Back to feature tree</a></p>
    ${SECTIONS.filter(([k]) => k in d).map(([k, title, render]) => html`<${Section} key=${k} id=${k} title=${title}>${render(d[k])}<//>`)}
    ${unknown ? html`<${Section} id="other" title="Other"><${KeyValue} value=${unknown} /><//>` : null}
  </section>`;
}
