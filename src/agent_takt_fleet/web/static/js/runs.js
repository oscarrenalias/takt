// Runs screens: paged scheduler-run list and a single run page (header, cycle timeline, beads, cost).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchRuns, fetchRun } from "./api.js";
import { connectStream } from "./stream.js";
import { Breadcrumb } from "./breadcrumb.js";
import { relativeTime, hrefs } from "./util.js";

export const loadList = ({ name }) => fetchRuns(name);
export const loadRun = ({ name, runId }) => fetchRun(name, runId);

export function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function formatCost(v) {
  return typeof v === "number" && Number.isFinite(v) ? `$${v.toFixed(2)}` : "—";
}

const OUTCOME_CLASS = { completed: "status-done", blocked: "status-blocked", failed: "status-failed", in_progress: "status-running" };
const workersOf = (r) => r.max_workers ?? r.workers;

export function Runs({ data, params }) {
  const name = params.name;
  const [extra, setExtra] = useState({ runs: [], next: undefined });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const base = data || { runs: [], total: 0, next_cursor: null };
  const rows = [...(base.runs || []), ...extra.runs];
  const cursor = extra.next === undefined ? base.next_cursor : extra.next;

  const more = () => {
    if (!cursor || busy) return;
    setBusy(true);
    fetchRuns(name, { cursor })
      .then((r) => {
        setExtra((e) => ({ runs: [...e.runs, ...(r.runs || [])], next: r.next_cursor || null }));
        setError(null);
      })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  const open = (id) => () => { window.location.hash = hrefs.run(name, id); };

  return html`<section data-testid="screen-runs">
    <${Breadcrumb} params=${{ ...params, section: "runs" }} />
    <h2>Runs</h2>
    <p class="muted" data-testid="run-count">${rows.length < (base.total || 0) ? `${rows.length} of ${base.total}` : rows.length} run(s)</p>
    ${error ? html`<p class="status-error" data-testid="runs-error">Failed to load: ${error.message}</p>` : null}
    ${rows.length === 0
      ? html`<p class="muted" data-testid="runs-empty">No scheduler runs.</p>`
      : html`<div class="table-wrap"><table class="runs-table" data-testid="runs-table">
          <thead><tr><th>Run</th><th>Started</th><th>Duration</th><th>Status</th></tr></thead>
          <tbody>${rows.map((r) => html`<tr key=${r.run_id} class="run-row" data-testid="run-row" data-run-id=${r.run_id}
              data-status=${r.status} onClick=${open(r.run_id)}>
            <td class="mono"><a href=${hrefs.run(name, r.run_id)} data-testid="run-link" onClick=${(e) => e.stopPropagation()}>${r.run_id}</a></td>
            <td title=${r.started_at}>${relativeTime(r.started_at)}</td>
            <td>${formatDuration(r.duration_seconds)}</td>
            <td><span class=${`badge status-${r.status === "running" ? "running" : r.status === "quiescence" ? "done" : r.status === "unknown" ? "idle" : "warn"}`}>${r.status || "unknown"}</span></td>
          </tr>`)}</tbody>
        </table></div>`}
    ${cursor
      ? html`<p><button type="button" data-testid="run-load-older" disabled=${busy} onClick=${more}>${busy ? "Loading…" : "Load older"}</button></p>`
      : null}
  </section>`;
}

function Timeline({ cycles }) {
  if (!cycles.length) return html`<p class="muted" data-testid="run-cycles-empty">No cycles recorded.</p>`;
  return html`<ol class="cycle-timeline" data-testid="run-cycles">
    ${cycles.map((c) => html`<li key=${c.index} class="cycle" data-testid="run-cycle" data-cycle=${c.index}>
      <span class="cycle-index">Cycle ${c.index}</span>
      <span class="cycle-time muted" title=${c.started_at || ""}>${relativeTime(c.started_at)}</span>
      <span class="cycle-counts">
        <span class="status-running">${c.started_count} started</span>
        <span class="status-done">${c.completed_count} completed</span>
        <span class=${c.blocked_count ? "status-blocked" : "muted"}>${c.blocked_count} blocked</span>
        <span class="muted">${c.deferred_count} deferred</span>
      </span>
    </li>`)}
  </ol>`;
}

const OUTCOME_OF = { bead_completed: "completed", bead_blocked: "blocked", bead_failed: "failed" };
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

// Apply one bead_started / bead_completed / bead_blocked / bead_failed fleet_event for this run
// to the run detail (pure; returns `data` unchanged for anything else). Cycle counts and the
// run header are left to the refetch at cycle end / run finish.
export function applyRunEvent(data, evt, name, runId) {
  if (!data || !data.run || !evt || evt.project !== name) return data;
  const payload = evt.payload || {};
  if (payload.run_id !== runId) return data;
  const type = evt.event_type;
  const id = payload.bead_id;
  if (typeof id !== "string" || !id || (type !== "bead_started" && !OUTCOME_OF[type])) return data;
  const beads = data.beads || [];
  const idx = beads.findIndex((b) => b.bead_id === id);
  const prev = idx >= 0 ? beads[idx] : { bead_id: id, title: null, agent_type: null, outcome: null, duration_seconds: null, cost_usd: null };
  const next = { ...prev };
  if (typeof payload.title === "string" && payload.title && !next.title) next.title = payload.title;
  if (typeof payload.agent_type === "string" && payload.agent_type) next.agent_type = payload.agent_type;
  let total = data.total_cost_usd;
  if (type === "bead_started") {
    next.outcome = next.outcome || "in_progress";
  } else {
    next.outcome = OUTCOME_OF[type];
    const d = num(payload.duration_seconds);
    if (d !== null) next.duration_seconds = d;
    const c = num(payload.cost_usd);
    if (c !== null) {
      next.cost_usd = (next.cost_usd || 0) + c;
      total = (num(total) || 0) + c;
    }
  }
  const list = idx >= 0 ? beads.map((b, i) => (i === idx ? next : b)) : [...beads, next];
  return { ...data, beads: list, total_cost_usd: total };
}

export function Run({ data: loaded, params }) {
  const name = params.name;
  const [data, setData] = useState(loaded);
  const lastLoaded = useRef(loaded);
  if (lastLoaded.current !== loaded) {
    lastLoaded.current = loaded;
    setData(loaded);
  }
  const runId = data && data.run ? data.run.run_id : null;
  const running = !!(data && data.run && data.run.status === "running");

  useEffect(() => {
    if (!running || !runId) return undefined;
    let closed = false;
    const stream = connectStream({
      onEvent: (evt, msg) => {
        if (evt !== "fleet_event" || !msg || msg.project !== name) return;
        const payload = msg.payload || {};
        if (payload.run_id !== runId) return;
        if (msg.event_type === "scheduler_cycle_completed" || msg.event_type === "scheduler_run_finished") {
          fetchRun(name, runId).then((d) => !closed && setData(d)).catch(() => {});
        } else {
          setData((cur) => applyRunEvent(cur, msg, name, runId));
        }
      },
    });
    return () => {
      closed = true;
      stream.close();
    };
  }, [name, runId, running]);

  if (!data || !data.run) return html`<p data-testid="run-notfound">Run not found.</p>`;
  const { run, cycles = [], beads = [], correctives_created: correctives = [], total_cost_usd: cost } = data;
  const workers = workersOf(run);
  return html`<section data-testid="screen-run">
    <${Breadcrumb} params=${{ ...params, section: "runs" }} />
    <h2 class="mono">${run.run_id}</h2>
    <dl class="run-header" data-testid="run-header">
      <div><dt>Status</dt><dd data-testid="run-status">${run.status || "unknown"}</dd></div>
      <div><dt>Started</dt><dd title=${run.started_at}>${relativeTime(run.started_at)}</dd></div>
      <div><dt>Duration</dt><dd data-testid="run-duration">${formatDuration(run.duration_seconds)}</dd></div>
      <div><dt>Exit reason</dt><dd data-testid="run-exit-reason">${run.exit_reason || "—"}</dd></div>
      <div><dt>Workers</dt><dd data-testid="run-workers">${workers ?? "—"}</dd></div>
      <div><dt>Runner</dt><dd data-testid="run-runner">${run.runner || "—"}</dd></div>
      <div><dt>Total cost</dt><dd data-testid="run-total-cost">${formatCost(cost)}</dd></div>
    </dl>
    <h3>Cycles</h3>
    <${Timeline} cycles=${cycles} />
    <h3>Beads</h3>
    ${beads.length === 0
      ? html`<p class="muted" data-testid="run-beads-empty">No beads ran.</p>`
      : html`<div class="table-wrap"><table class="run-beads" data-testid="run-beads">
          <thead><tr><th>Bead</th><th>Title</th><th class="hide-narrow">Agent</th><th>Outcome</th><th class="hide-narrow">Duration</th><th>Cost</th></tr></thead>
          <tbody>${beads.map((b) => html`<tr key=${b.bead_id} data-testid="run-bead" data-bead-id=${b.bead_id} data-outcome=${b.outcome}>
            <td class="mono"><a href=${hrefs.bead(name, b.bead_id)} data-testid="run-bead-link">${b.bead_id}</a></td>
            <td>${b.title || "—"}</td>
            <td class="hide-narrow">${b.agent_type || "—"}</td>
            <td><span class=${`badge ${OUTCOME_CLASS[b.outcome] || "status-idle"}`}>${(b.outcome || "unknown").replace("_", " ")}</span></td>
            <td class="hide-narrow">${formatDuration(b.duration_seconds)}</td>
            <td>${formatCost(b.cost_usd)}</td>
          </tr>`)}</tbody>
        </table></div>`}
    ${correctives.length
      ? html`<p class="muted" data-testid="run-correctives">Correctives created: ${correctives.map((id, i) => html`<span key=${id}>${i ? ", " : ""}<a class="mono" href=${hrefs.bead(name, id)}>${id}</a></span>`)}</p>`
      : null}
  </section>`;
}
