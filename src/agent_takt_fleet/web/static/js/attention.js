// Attention page: fleet-wide "needs attention" sections. Presentation only; the server supplies
// counts, ordering and link targets (ADR-73d258d5).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchAttention } from "./api.js";
import { hrefs, relativeTime } from "./util.js";

export const load = () => fetchAttention();

const text = (v) => (v === null || v === undefined || v === "" ? "" : String(v));

function beadLink(it) {
  return html`<a href=${hrefs.bead(it.project, it.bead_id)} data-testid="attention-item-link">${it.bead_id}</a>`;
}

function Head({ it, when }) {
  return html`<span class="at-head">
    <a href=${hrefs.project(it.project)} class="at-project">${it.project}</a>
    ${it.bead_id ? html` · ${beadLink(it)}` : null}
    ${it.title ? html` <span class="at-title">${it.title}</span>` : null}
    ${when ? html` <span class="muted at-when">${relativeTime(when)}</span>` : null}
  </span>`;
}

const SECTIONS = [
  {
    id: "blocked",
    title: "Blocked beads",
    row: (it) => html`<${Head} it=${it} when=${it.blocked_at} />
      ${it.block_reason ? html`<div class="at-detail">${it.block_reason}</div>` : null}
      <div class="muted at-detail">${it.has_correctives_remaining
        ? `${it.correctives_remaining} corrective attempt(s) remaining`
        : "no corrective attempts remaining"}</div>`,
  },
  {
    id: "discovered",
    title: "Discovered issues",
    row: (it) => html`<${Head} it=${it} when=${it.created_at} />
      ${it.reported_by ? html`<div class="muted at-detail">reported by ${it.reported_by}</div>` : null}`,
  },
  {
    id: "unmet_criteria",
    title: "Unmet criteria (unmerged)",
    row: (it) => html`<${Head} it=${it} when=${it.at} />
      <div class="at-detail">${typeof it.index === "number" ? `#${it.index} ` : ""}${text(it.criterion)}</div>
      ${it.evidence ? html`<div class="muted at-detail">${it.evidence}</div>` : null}`,
  },
  {
    id: "structural_assumptions",
    title: "Structural assumptions (unmerged)",
    row: (it) => html`<${Head} it=${it} when=${it.at} />
      <div class="at-detail">${text(it.statement)}</div>
      ${it.rationale ? html`<div class="muted at-detail">${it.rationale}</div>` : null}`,
  },
  {
    id: "stale_runs",
    title: "Stale scheduler runs",
    row: (it) => html`<span class="at-head">
        <a href=${hrefs.project(it.project)} class="at-project" data-testid="attention-item-link">${it.project}</a>
        ${it.run_id ? html` · <span class="mono">${it.run_id}</span>` : null}
        ${it.started_at ? html` <span class="muted at-when">started ${relativeTime(it.started_at)}</span>` : null}
      </span>`,
  },
];

function Section({ def, section }) {
  const items = section && Array.isArray(section.items) ? section.items : [];
  const count = section && typeof section.count === "number" ? section.count : items.length;
  const empty = count === 0 && items.length === 0;
  return html`<section class=${`at-section${empty ? " at-empty" : ""}`} data-testid=${`attention-section-${def.id}`}
    data-count=${count}>
    <h3>${def.title} <span class="at-count" data-testid=${`attention-count-${def.id}`}>${count}</span>
      ${empty ? html`<span class="muted at-none" data-testid=${`attention-empty-${def.id}`}> — nothing here</span>` : null}</h3>
    ${empty
      ? null
      : html`<ul class="at-list">${items.map(
          (it, i) => html`<li key=${`${it.project}:${it.bead_id || it.run_id || ""}:${i}`} class="at-item"
            data-testid="attention-item">${def.row(it)}</li>`,
        )}</ul>`}
  </section>`;
}

export function Attention({ data }) {
  const d = data || {};
  return html`<section data-testid="screen-attention" class="attention">
    <h2>Needs attention <span class="at-count" data-testid="attention-total">${d.total || 0}</span></h2>
    ${SECTIONS.map((def) => html`<${Section} key=${def.id} def=${def} section=${d[def.id]} />`)}
  </section>`;
}
