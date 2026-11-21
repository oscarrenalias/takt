// Cost page: window selector, fleet total, hand-built SVG stacked bar chart and tables.
// Presentation only; the server supplies totals, aligned daily series and ordering (ADR-73d258d5).
import { html, useState } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchCost } from "./api.js";
import { hrefs } from "./util.js";

export const WINDOWS = [7, 30, 90];
export const load = (params) => fetchCost((params && params.days) || 30);

export const usd = (v) => {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return `$${v.toFixed(v !== 0 && Math.abs(v) < 0.01 ? 4 : 2)}`;
};

const PALETTE = 8;
const colorVar = (i) => `var(--cost-c${i % PALETTE})`;

// Round the axis maximum up to a "nice" number and return ticks.
export function niceScale(max, count = 4) {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const raw = max / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  const top = step * Math.ceil(max / step - 1e-9);
  const ticks = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { top, ticks };
}

const BAR_W = 18;
const GAP = 6;
const PAD = { l: 52, r: 8, t: 10, b: 38 };
const H = 240;

function Chart({ dates, projects }) {
  const [tip, setTip] = useState(null);
  const n = dates.length;
  const plotH = H - PAD.t - PAD.b;
  const slot = BAR_W + GAP;
  const plotW = Math.max(n * slot, 40);
  const W = PAD.l + plotW + PAD.r;
  const totals = dates.map((_, i) => projects.reduce((s, p) => s + ((p.daily && p.daily[i]) || 0), 0));
  const { top, ticks } = niceScale(Math.max(0, ...totals));
  const y = (v) => PAD.t + plotH - (v / top) * plotH;
  const labelEvery = Math.max(1, Math.ceil(n / 10));
  return html`<div class="cost-chart-wrap" data-testid="cost-chart-scroll">
    <svg class="cost-chart" width=${W} height=${H} viewBox=${`0 0 ${W} ${H}`} role="img"
      aria-label="Daily cost stacked by project" data-testid="cost-chart">
      ${ticks.map((t) => html`<g key=${t}>
        <line class="cost-grid" x1=${PAD.l} x2=${PAD.l + plotW} y1=${y(t)} y2=${y(t)} />
        <text class="cost-axis" x=${PAD.l - 6} y=${y(t) + 4} text-anchor="end">${usd(t)}</text>
      </g>`)}
      <line class="cost-axis-line" x1=${PAD.l} x2=${PAD.l} y1=${PAD.t} y2=${PAD.t + plotH} />
      <line class="cost-axis-line" x1=${PAD.l} x2=${PAD.l + plotW} y1=${PAD.t + plotH} y2=${PAD.t + plotH} />
      ${dates.map((d, i) => {
        const x = PAD.l + i * slot + GAP / 2;
        let acc = 0;
        return html`<g key=${d} class="cost-bar" data-testid="cost-bar" data-date=${d}>
          ${projects.map((p, pi) => {
            const v = (p.daily && p.daily[i]) || 0;
            if (v <= 0) return null;
            const y0 = y(acc);
            acc += v;
            const y1 = y(acc);
            return html`<rect key=${p.project} class="cost-seg" data-testid="cost-segment" data-project=${p.project}
              data-date=${d} data-amount=${v} x=${x} y=${y1} width=${BAR_W} height=${Math.max(y0 - y1, 0.5)}
              fill=${colorVar(pi)}
              onMouseEnter=${() => setTip({ d, project: p.project, v, x: x + BAR_W / 2, y: y1 })}
              onMouseMove=${() => setTip({ d, project: p.project, v, x: x + BAR_W / 2, y: y1 })}
              onMouseLeave=${() => setTip(null)}
              onFocus=${() => setTip({ d, project: p.project, v, x: x + BAR_W / 2, y: y1 })}
              onBlur=${() => setTip(null)} tabindex="0">
              <title>${`${d} · ${p.project} · ${usd(v)}`}</title></rect>`;
          })}
          ${i % labelEvery === 0
            ? html`<text class="cost-axis" x=${x + BAR_W / 2} y=${PAD.t + plotH + 14} text-anchor="end"
                transform=${`rotate(-45 ${x + BAR_W / 2} ${PAD.t + plotH + 14})`}>${d.slice(5)}</text>`
            : null}
        </g>`;
      })}
      ${tip
        ? html`<g class="cost-tip" data-testid="cost-tooltip" pointer-events="none"
            transform=${`translate(${Math.min(Math.max(tip.x, PAD.l + 70), W - 70)} ${Math.max(tip.y - 8, 44)})`}>
            <rect x="-70" y="-42" width="140" height="40" rx="4" />
            <text x="0" y="-26" text-anchor="middle">${tip.d}</text>
            <text x="0" y="-10" text-anchor="middle">${`${tip.project}: ${usd(tip.v)}`}</text>
          </g>`
        : null}
    </svg>
  </div>`;
}

function Legend({ projects }) {
  return html`<ul class="cost-legend" data-testid="cost-legend">
    ${projects.map((p, i) => html`<li key=${p.project} data-testid="cost-legend-item">
      <span class="cost-swatch" style=${`background:${colorVar(i)}`}></span>${p.project}</li>`)}
  </ul>`;
}

function Table({ id, title, cols, rows, empty }) {
  return html`<section class="cost-table" data-testid=${`cost-table-${id}`}>
    <h3>${title}</h3>
    ${rows.length === 0
      ? html`<p class="muted">${empty}</p>`
      : html`<div class="table-wrap"><table><thead><tr>${cols.map((c) => html`<th key=${c.h} class=${c.num ? "num" : ""}>${c.h}</th>`)}</tr></thead>
          <tbody>${rows.map((r, i) => html`<tr key=${i} data-testid=${`cost-row-${id}`}>
            ${cols.map((c) => html`<td key=${c.h} class=${c.num ? "num" : ""}>${c.cell(r)}</td>`)}</tr>`)}</tbody></table></div>`}
  </section>`;
}

export function Cost({ data }) {
  const d = data || {};
  const days = d.days || 30;
  const projects = Array.isArray(d.projects) ? d.projects : [];
  const dates = Array.isArray(d.dates) ? d.dates : [];
  const nulls = d.null_cost_events || 0;
  return html`<section class="cost" data-testid="screen-cost">
    <h2>Cost</h2>
    <div class="cost-controls">
      <div class="cost-window" role="group" aria-label="Window" data-testid="cost-window">
        ${WINDOWS.map((w) => html`<a key=${w} href=${`#/cost?days=${w}`} data-testid=${`cost-window-${w}`}
          class=${w === days ? "active" : ""} aria-current=${w === days ? "true" : null}>${w}d</a>`)}
      </div>
      <div class="cost-total">
        <span class="cost-total-amount" data-testid="cost-total">${usd(d.total_cost_usd || 0)}</span>
        <span class="muted"> over the last ${days} days</span>
        <span class="muted" data-testid="cost-null-count"> · ${nulls} event${nulls === 1 ? "" : "s"} without cost</span>
      </div>
    </div>
    <section class="cost-chart-section">
      <h3>Daily cost by project</h3>
      ${projects.length === 0 || dates.length === 0
        ? html`<p class="muted" data-testid="cost-empty">No cost data in this window.</p>`
        : html`<${Chart} dates=${dates} projects=${projects} /><${Legend} projects=${projects} />`}
    </section>
    <${Table} id="projects" title="By project" empty="No projects."
      cols=${[{ h: "Project", cell: (r) => html`<a href=${hrefs.project(r.project)}>${r.project}</a>` },
        { h: "Cost", num: true, cell: (r) => usd(r.cost_usd) }]}
      rows=${[...projects].sort((a, b) => b.cost_usd - a.cost_usd)} />
    <${Table} id="agents" title="By agent type" empty="No agent cost recorded."
      cols=${[{ h: "Agent type", cell: (r) => r.agent_type }, { h: "Cost", num: true, cell: (r) => usd(r.cost_usd) }]}
      rows=${Array.isArray(d.agent_types) ? d.agent_types : []} />
    <${Table} id="beads" title="Top 10 beads" empty="No bead cost recorded."
      cols=${[{ h: "Bead", cell: (r) => html`<a href=${hrefs.bead(r.project, r.bead_id)} data-testid="cost-bead-link">${r.bead_id}</a>` },
        { h: "Project", cell: (r) => r.project },
        { h: "Title", cell: (r) => r.title || "" },
        { h: "Agent", cell: (r) => r.agent_type || "" },
        { h: "Cost", num: true, cell: (r) => usd(r.cost_usd) }]}
      rows=${Array.isArray(d.top_beads) ? d.top_beads : []} />
  </section>`;
}
