// Bead detail screen: collapsible sections rendered from the server-side view model.
// Live updates: the app shell refetches (debounced, scoped to this bead) when beads_changed names it;
// this component is purely a function of `data`, and <details> state lives in the DOM so it survives refetches.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchBead } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { hrefs, relativeTime } from "./util.js";
import { useLiveSessions, sessionIndex, watchLiveHref } from "./livesessions.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchBead(name, beadId);

const fmtCost = (v) => (typeof v === "number" ? `$${v.toFixed(4)}` : "—");
const fmtDuration = (ms) => {
  if (typeof ms !== "number") return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};
const fmtNum = (v) => (typeof v === "number" ? String(v) : "—");
const text = (v) => (v === null || v === undefined || v === "" ? "" : String(v));
const pad2 = (n) => String(n).padStart(2, "0");
// Local-time "YYYY-MM-DD HH:MM:SS"; falls back to the raw text when unparseable.
const fmtLocalTimestamp = (iso) => {
  const t = text(iso);
  const d = new Date(t);
  if (!t || Number.isNaN(d.getTime())) return t;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
};
// Colour family for a history event: lifecycle | failure | verdict | scheduler.
const eventFamily = (event) => {
  const e = text(event).toLowerCase();
  if (/(fail|error|blocked|timed_out|timeout|exhausted|conflict)/.test(e)) return "failure";
  if (/(verdict|approved|needs_changes|override|criteria|assumption)/.test(e)) return "verdict";
  if (/(scheduler|lease|retried|requeue|corrective|recovery|followup|skills_loaded|guardrails|dependency|discovered|merge|commit)/.test(e)) return "scheduler";
  return "lifecycle";
};

function Section({ id, title, count, open = true, children }) {
  return html`<details class=${`bd-section bd-sec-${id}`} open=${open} data-testid=${`bead-section-${id}`}>
    <summary>${title}${count !== undefined ? html`<span class="bd-count" data-testid=${`bead-section-count-${id}`}>${count}</span>` : null}</summary>
    <div class="bd-body">${children}</div>
  </details>`;
}

const Empty = ({ what }) => html`<p class="muted bd-empty">No ${what}.</p>`;

function BeadLink({ name, item }) {
  return html`<a href=${hrefs.bead(name, item.bead_id)} data-testid="bead-ref">${item.bead_id}</a>
    ${item.status ? html` <span class="muted">[${item.status}]</span>` : null}
    ${item.title ? html` ${item.title}` : null}`;
}

function FileList({ label, items }) {
  if (!items || !items.length) return null;
  return html`<div class="bd-files"><h4>${label}</h4>
    <ul>${items.map((f) => html`<li key=${f} class="mono">${f}</li>`)}</ul></div>`;
}

export function BeadDetail({ data, params }) {
  const name = params.name;
  const d = data || {};
  const row = d.row || {};
  const liveSession = sessionIndex(useLiveSessions({ project: name })).get(`${name}/${row.bead_id || params.beadId}`);
  const handoff = d.handoff || {};
  const criteria = d.acceptance_criteria || [];
  const assumptions = d.assumptions || [];
  const files = d.files || {};
  const deps = d.dependencies || [];
  const children = d.children || [];
  const history = d.execution_history || [];
  const tele = d.telemetry || { rows: [], totals: {} };
  const traces = d.traces || [];
  const discovered = d.discovered_issues || [];
  const handoffFields = [
    ["completed", "Completed"], ["remaining", "Remaining"], ["risks", "Risks"], ["next_action", "Next action"],
    ["block_reason", "Block reason"], ["design_decisions", "Design decisions"],
    ["test_coverage_notes", "Test coverage notes"], ["known_limitations", "Known limitations"],
    ["conflict_risks", "Conflict risks"],
  ].filter(([k]) => text(handoff[k]));
  const blockReason = text(d.block_reason);
  const verdict = text(handoff.verdict || row.verdict);
  const fileCount = Object.values(files).reduce((n, v) => n + (Array.isArray(v) ? v.length : 0), 0);

  return html`<section data-testid="bead-detail" class="bead-detail">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    ${liveSession
      ? html`<div class="live-banner" role="status" data-testid="bead-live-banner">
          <span class="live-dot" aria-hidden="true"></span> A session is running for this bead.
          <a href=${watchLiveHref(liveSession)} data-testid="bead-live-link">Watch live</a>
        </div>`
      : null}

    <${Section} id="summary" title="Summary">
      <h2 data-testid="bead-title">${row.title || row.bead_id || params.beadId}</h2>
      <dl class="bd-summary">
        <dt>ID</dt><dd class="mono">${row.bead_id}</dd>
        <dt>Status</dt><dd data-testid="bead-status">${text(row.status) || "—"}</dd>
        <dt>Agent</dt><dd>${text(row.agent_type) || "—"}</dd>
        ${row.bead_type ? html`<dt>Type</dt><dd>${row.bead_type}</dd>` : null}
        <dt>Verdict</dt><dd>${verdict ? html`<span class=${`bd-badge bd-verdict-${verdict}`} data-testid="bead-verdict">${verdict}</span>` : "—"}</dd>
        ${row.parent_id ? html`<dt>Parent</dt><dd><a href=${hrefs.bead(name, row.parent_id)}>${row.parent_id}</a></dd>` : null}
        ${row.feature_root_id
          ? html`<dt>Feature</dt><dd><a href=${hrefs.feature(name, row.feature_root_id)} data-testid="bead-feature-link">${row.feature_root_id}</a></dd>`
          : null}
        <dt>Created</dt><dd>${relativeTime(row.created_at)}</dd>
        <dt>Updated</dt><dd>${relativeTime(row.updated_at)}</dd>
        ${(row.labels || []).length ? html`<dt>Labels</dt><dd>${row.labels.join(", ")}</dd>` : null}
        ${(row.badges || []).length
          ? html`<dt>Badges</dt><dd>${row.badges.map((b) => html`<span key=${b} class="bd-badge" data-testid="bead-badge">${b}</span>`)}</dd>`
          : null}
      </dl>
      ${blockReason ? html`<p class="bd-block-reason" data-testid="bead-block-reason"><span class="bd-badge">Blocked</span>${blockReason}</p>` : null}
    <//>

    <${Section} id="description" title="Description">
      ${text(d.description) ? html`<pre class="bd-scroll bd-text">${d.description}</pre>` : html`<${Empty} what="description" />`}
    <//>

    <${Section} id="criteria" title="Acceptance criteria" count=${criteria.length}>
      ${criteria.length
        ? html`<ul class="bd-list">${criteria.map((c) => html`<li key=${c.index} class=${`bd-criterion bd-${c.status}`} data-testid="criteria-item" data-status=${c.status}>
            <span class="bd-pill" data-testid="criteria-status">${c.status}</span>
            <span class="bd-crit-text"><span class="muted">[${c.index}]</span> ${c.criterion}</span>
            ${c.evidence ? html`<div class="bd-evidence bd-scroll" data-testid="criteria-evidence">${c.evidence}</div>` : null}
          </li>`)}</ul>`
        : html`<${Empty} what="acceptance criteria" />`}
    <//>

    <${Section} id="handoff" title="Handoff" count=${handoffFields.length}>
      ${handoffFields.length
        ? handoffFields.map(([k, label]) => html`<div key=${k} class="bd-field" data-testid=${`handoff-${k}`}>
            <h4>${label}</h4><pre class="bd-scroll bd-text">${handoff[k]}</pre></div>`)
        : html`<${Empty} what="handoff" />`}
    <//>

    <${Section} id="assumptions" title="Assumptions" count=${assumptions.length}>
      ${assumptions.length
        ? html`<ul class="bd-list">${assumptions.map((a, i) => html`<li key=${i} class=${`bd-assumption${a.scope === "local" ? "" : " bd-structural"}`} data-testid="assumption-item" data-scope=${a.scope || "structural"}>
            <span class="bd-pill">${a.scope || "structural"}</span> ${a.statement}
            ${a.rationale ? html`<div class="muted bd-scroll">${a.rationale}</div>` : null}
          </li>`)}</ul>`
        : html`<${Empty} what="assumptions" />`}
    <//>

    <${Section} id="files" title="Files" count=${fileCount}>
      <${FileList} label="Expected files" items=${files.expected_files} />
      <${FileList} label="Expected globs" items=${files.expected_globs} />
      <${FileList} label="Touched files" items=${files.touched_files} />
      <${FileList} label="Changed files" items=${files.changed_files} />
      <${FileList} label="Updated docs" items=${files.updated_docs} />
      ${Object.values(files).every((v) => !v || !v.length) ? html`<${Empty} what="files" />` : null}
    <//>

    <${Section} id="relations" title="Dependencies and children" count=${deps.length + children.length}>
      <h4>Dependencies</h4>
      ${deps.length
        ? html`<ul>${deps.map((x) => html`<li key=${x.bead_id} data-testid="dependency-item"><${BeadLink} name=${name} item=${x} /></li>`)}</ul>`
        : html`<${Empty} what="dependencies" />`}
      <h4>Children</h4>
      ${children.length
        ? html`<ul>${children.map((x) => html`<li key=${x.bead_id} data-testid="child-item"><${BeadLink} name=${name} item=${x} /></li>`)}</ul>`
        : html`<${Empty} what="children" />`}
    <//>

    <${Section} id="history" title="History" count=${history.length}>
      ${history.length
        ? html`<ol class="bd-timeline bd-scroll">${history.map((h, i) => {
            const family = eventFamily(h.event);
            const hasDetails = h.details && typeof h.details === "object" && Object.keys(h.details).length > 0;
            return html`<li key=${`${h.timestamp}-${i}`} data-testid="history-item" data-event=${h.event}>
              <div class="bd-hist-row">
                <span class="bd-hist-ts mono muted" title=${text(h.timestamp)} data-testid="history-timestamp">${fmtLocalTimestamp(h.timestamp)}</span>
                <span class=${`bd-hist-event bd-ev-${family}`} data-testid="history-event" data-family=${family}>${h.event}</span>
                <span class="bd-hist-agent muted" data-testid="history-agent">${text(h.agent_type)}</span>
                <span class="bd-hist-summary" data-testid="history-summary">${text(h.summary)}</span>
              </div>
              ${hasDetails
                ? html`<details class="bd-hist-details" data-testid="history-details">
                    <summary>Details</summary>
                    <pre class="mono bd-scroll bd-text">${JSON.stringify(h.details, null, 2)}</pre>
                  </details>`
                : null}
            </li>`;
          })}</ol>`
        : html`<${Empty} what="history" />`}
    <//>

    <${Section} id="cost" title="Cost and attempts" count=${(tele.rows || []).length}>
      <p data-testid="telemetry-totals">Attempts ${fmtNum(tele.totals && tele.totals.attempts)} · Cost ${fmtCost(tele.totals && tele.totals.cost_usd)} · Duration ${fmtDuration(tele.totals && tele.totals.duration_ms)} · Tokens ${fmtNum(tele.totals && tele.totals.tokens)}</p>
      ${(tele.rows || []).length
        ? html`<div class="table-wrap"><table class="bd-table"><thead><tr><th>Attempt</th><th>Outcome</th><th>Model</th><th>Cost</th><th>Duration</th></tr></thead>
          <tbody>${tele.rows.map((r, i) => html`<tr key=${i} data-testid="attempt-row">
            <td>${r.attempt !== null && r.attempt !== undefined
              ? html`<a href=${hrefs.telemetry(name, row.bead_id || params.beadId, r.attempt)} data-testid="telemetry-link">${r.attempt}</a>`
              : "—"}</td>
            <td>${text(r.outcome) || "—"}</td><td>${text(r.model) || "—"}</td>
            <td>${fmtCost(r.cost_usd)}</td><td>${fmtDuration(r.duration_ms)}</td></tr>`)}</tbody></table></div>`
        : html`<${Empty} what="recorded attempts" />`}
    <//>

    <${Section} id="traces" title="Traces" count=${traces.length}>
      ${traces.length
        ? html`<ul>${traces.map((t) => html`<li key=${t.seq} data-testid="trace-link-item">
            <a href=${hrefs.trace(name, row.bead_id || params.beadId, t.seq)} data-testid="trace-link">Trace ${t.seq}</a>
            <span class="muted"> ${[t.role, t.status, t.outcome].filter(Boolean).join(" · ")}</span></li>`)}</ul>`
        : html`<${Empty} what="traces" />`}
    <//>

    <${Section} id="discovered" title="Discovered issues" count=${discovered.length}>
      ${discovered.length
        ? html`<ul>${discovered.map((x) => html`<li key=${x.bead_id} data-testid="discovered-item"><${BeadLink} name=${name} item=${x} /></li>`)}</ul>`
        : html`<${Empty} what="discovered issues" />`}
    <//>

    <${Section} id="raw" title="Raw JSON" open=${false}>
      <pre class="mono explorer-raw bd-scroll" data-testid="bead-detail-raw">${JSON.stringify(d.raw !== undefined ? d.raw : d, null, 2)}</pre>
    <//>
  </section>`;
}
