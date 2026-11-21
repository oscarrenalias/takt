// Trace screen: meta header, summary, filterable and pageable event timeline.
// `load` fetches the first unfiltered page; filters and "Load more" fetch further pages client-side.
// Live updates: the app shell refetches (scoped to this bead) on beads_changed, which refreshes meta and
// summary from `data` while the loaded timeline pages and filter state are kept.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { getJSON } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { hrefs } from "./util.js";

const PAGE_LIMIT = 200;
const KINDS = ["tool_call", "assistant_text", "usage", "session"];

// Self-contained styles: link trace.css once (index.html is owned by the app shell).
function ensureStyles() {
  const href = "/static/css/trace.css";
  if (typeof document === "undefined" || document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

const tracePath = (name, beadId, seq) =>
  `/api/projects/${encodeURIComponent(name)}/beads/${encodeURIComponent(beadId)}/traces/${encodeURIComponent(seq)}`;

export function fetchTracePage(name, beadId, seq, { offset = 0, limit = PAGE_LIMIT, kind, errors, tool } = {}) {
  const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (kind) q.set("kind", kind);
  if (errors) q.set("errors_only", "true");
  if (tool) q.set("tool", tool);
  return getJSON(`${tracePath(name, beadId, seq)}?${q}`);
}

export const load = ({ name, beadId, seq }) => fetchTracePage(name, beadId, seq);

const text = (v) => (v === null || v === undefined || v === "" ? "" : String(v));
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const fmtNum = (v) => num(v).toLocaleString("en-US");
const fmtMs = (ms) => {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
};
const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// One-line summary of a tool call's input.
export function summarizeInput(input) {
  if (input === null || input === undefined) return "";
  if (typeof input === "string") return clip(input.replace(/\s+/g, " "), 140);
  if (typeof input !== "object") return String(input);
  for (const k of ["command", "file_path", "path", "pattern", "url", "query", "description", "prompt"]) {
    if (typeof input[k] === "string" && input[k]) return clip(input[k].replace(/\s+/g, " "), 140);
  }
  for (const v of Object.values(input)) {
    if (typeof v === "string" && v) return clip(v.replace(/\s+/g, " "), 140);
  }
  try {
    return clip(JSON.stringify(input), 140);
  } catch (_) {
    return "";
  }
}

function Meta({ meta }) {
  const m = meta || {};
  const rows = [
    ["Status", m.status, "trace-status"],
    ["Role", m.role, "trace-role"],
    ["Backend", m.backend],
    ["Session", m.session_id, "trace-session", true],
    ["Started", m.started_at],
    ["Finished", m.finished_at],
    ["Tool calls", m.tool_calls === null || m.tool_calls === undefined ? "" : String(m.tool_calls), "trace-tool-calls"],
    ["Outcome", m.outcome],
  ].filter(([, v]) => text(v));
  return html`<div class="trace-meta" data-testid="trace-meta">
    <dl class="trace-dl">
      ${rows.map(([k, v, tid, mono]) => html`<dt key=${`k${k}`}>${k}</dt>
        <dd key=${`v${k}`} class=${mono ? "mono" : ""} data-testid=${tid || null}>${v}</dd>`)}
    </dl>
    ${text(m.error) ? html`<p class="status-error" data-testid="trace-error">${m.error}</p>` : null}
  </div>`;
}

function Summary({ summary }) {
  const s = summary || {};
  const tools = Object.entries(s.tool_counts || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const usage = s.usage || {};
  return html`<div class="trace-summary" data-testid="trace-summary">
    <p data-testid="trace-summary-totals">
      Errors <strong class=${num(s.error_count) ? "status-error" : ""} data-testid="trace-error-count">${fmtNum(s.error_count)}</strong>
      · Tool time ${fmtMs(num(s.total_duration_ms))}
      · Assistant messages ${fmtNum(s.assistant_messages)}
    </p>
    <p data-testid="trace-tokens">Tokens: in ${fmtNum(usage.input_tokens)} · out ${fmtNum(usage.output_tokens)}
      · cache read ${fmtNum(usage.cache_read_tokens)} · cache write ${fmtNum(usage.cache_creation_tokens)}</p>
    ${tools.length
      ? html`<div class="table-wrap"><table class="trace-tools" data-testid="trace-tools-table">
          <thead><tr><th>Tool</th><th>Calls</th></tr></thead>
          <tbody>${tools.map(([t, c]) => html`<tr key=${t} data-testid="trace-tool-row"><td class="mono">${t || "(unnamed)"}</td><td>${fmtNum(c)}</td></tr>`)}</tbody>
        </table></div>`
      : html`<p class="muted">No tool calls.</p>`}
  </div>`;
}

function Event({ ev }) {
  const kind = ev.kind;
  if (kind === "tool_call") {
    const err = ev.is_error === true;
    const excerpt = text(ev.result_excerpt);
    const chars = num(ev.result_chars);
    return html`<li class=${`trace-event trace-tool${err ? " trace-err" : ""}`} data-testid="trace-event" data-kind="tool_call" data-error=${err ? "true" : "false"}>
      <div class="trace-line">
        <span class="trace-tool-name mono">${text(ev.tool) || "(unnamed)"}</span>
        <span class="trace-input mono">${summarizeInput(ev.input)}</span>
        <span class="trace-dur muted">${fmtMs(ev.duration_ms)}</span>
        ${err ? html`<span class="trace-err-mark" data-testid="trace-error-marker" title="Tool call failed">✗ error</span>` : null}
      </div>
      ${excerpt
        ? html`<details class="trace-result" data-testid="trace-result">
            <summary>Result${chars > excerpt.length ? html` <span class="muted">(${fmtNum(chars)} chars, excerpt)</span>` : null}</summary>
            <pre class="trace-pre">${excerpt}</pre>
          </details>`
        : null}
    </li>`;
  }
  if (kind === "assistant_text") {
    return html`<li class="trace-event trace-text" data-testid="trace-event" data-kind="assistant_text">
      <span class="trace-label muted">assistant</span> <span class="trace-excerpt">${text(ev.text_excerpt)}</span>
    </li>`;
  }
  const label = text(kind) || "event";
  return html`<li class="trace-event trace-other" data-testid="trace-event" data-kind=${label}>
    <span class="trace-label muted">${label}</span>
    ${kind === "usage"
      ? html` <span class="mono muted">in ${fmtNum(ev.input_tokens)} · out ${fmtNum(ev.output_tokens)}</span>`
      : kind === "session"
      ? html` <span class="mono muted">${text(ev.model)} ${text(ev.session_id)}</span>`
      : null}
  </li>`;
}

export function Trace({ data, params }) {
  ensureStyles();
  const d = data || {};
  const { name, beadId, seq } = params;
  const [filters, setFilters] = useState({ errors: false, kind: "", tool: "" });
  const [list, setList] = useState(null); // {fkey, events, total} once fetched beyond the base page
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const reqRef = useRef(0);
  const routeKey = `${name}/${beadId}/${seq}`;
  const fkey = `${routeKey}|${filters.errors}|${filters.kind}|${filters.tool}`;
  const isDefault = !filters.errors && !filters.kind && !filters.tool;

  // Reset when navigating to another trace.
  useEffect(() => {
    setFilters({ errors: false, kind: "", tool: "" });
    setList(null);
    setError(null);
    reqRef.current += 1;
  }, [routeKey]);

  // Fetch the first page when a non-default filter combination is selected.
  useEffect(() => {
    if (isDefault || (list && list.fkey === fkey)) return undefined;
    const id = ++reqRef.current;
    setBusy(true);
    setError(null);
    fetchTracePage(name, beadId, seq, filters)
      .then((r) => {
        if (reqRef.current !== id) return;
        setList({ fkey, events: r.events || [], total: num(r.total) });
        setBusy(false);
      })
      .catch((e) => {
        if (reqRef.current !== id) return;
        setError(e);
        setBusy(false);
      });
    return undefined;
  }, [fkey]);

  const current = list && list.fkey === fkey
    ? list
    : isDefault
    ? { events: d.events || [], total: num(d.total) }
    : { events: [], total: 0 };
  const events = current.events;
  const total = Math.max(num(current.total), events.length);
  const hasMore = events.length < total;

  const loadMore = () => {
    if (busy) return;
    const id = ++reqRef.current;
    setBusy(true);
    setError(null);
    fetchTracePage(name, beadId, seq, { ...filters, offset: events.length })
      .then((r) => {
        if (reqRef.current !== id) return;
        const more = r.events || [];
        setList({ fkey, events: events.concat(more), total: more.length ? num(r.total) : events.length });
        setBusy(false);
      })
      .catch((e) => {
        if (reqRef.current !== id) return;
        setError(e);
        setBusy(false);
      });
  };

  const toolNames = Object.keys((d.summary && d.summary.tool_counts) || {}).sort();
  const set = (patch) => setFilters((f) => ({ ...f, ...patch }));
  const filtering = !isDefault;
  const loadingFirst = filtering && !(list && list.fkey === fkey) && !error;

  return html`<section data-testid="screen-trace" class="trace-view">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Trace ${seq} <span class="muted">· <a href=${hrefs.bead(name, beadId)} data-testid="trace-bead-link">${beadId}</a></span></h2>
    <${Meta} meta=${d.meta} />
    <${Summary} summary=${d.summary} />

    <h3>Events</h3>
    <div class="trace-filters" data-testid="trace-filters">
      <label><input type="checkbox" checked=${filters.errors} data-testid="trace-filter-errors"
        onChange=${(e) => set({ errors: e.currentTarget.checked })} /> Errors only</label>
      <label>Kind
        <select value=${filters.kind} data-testid="trace-filter-kind" onChange=${(e) => set({ kind: e.currentTarget.value })}>
          <option value="">All</option>
          ${KINDS.map((k) => html`<option key=${k} value=${k}>${k}</option>`)}
        </select></label>
      <label>Tool
        <select value=${filters.tool} data-testid="trace-filter-tool" onChange=${(e) => set({ tool: e.currentTarget.value })}>
          <option value="">All</option>
          ${toolNames.map((t) => html`<option key=${t} value=${t}>${t || "(unnamed)"}</option>`)}
        </select></label>
      ${filtering
        ? html`<button type="button" data-testid="trace-filter-clear" onClick=${() => setFilters({ errors: false, kind: "", tool: "" })}>Clear</button>`
        : null}
      <span class="muted" data-testid="trace-count">Showing ${events.length} of ${total}</span>
    </div>

    ${error ? html`<p class="status-error" data-testid="trace-load-error">Failed to load events: ${error.message}</p>` : null}
    ${loadingFirst ? html`<p class="muted" data-testid="trace-loading">Loading…</p>` : null}
    ${events.length
      ? html`<ol class="trace-timeline" data-testid="trace-timeline">${events.map((ev, i) => html`<${Event} key=${i} ev=${ev || {}} />`)}</ol>`
      : !loadingFirst
      ? html`<p class="muted" data-testid="trace-empty">${text(d.meta && d.meta.status) === "missing" ? "No trace events: the trace was not captured." : "No events."}</p>`
      : null}
    ${hasMore
      ? html`<button type="button" class="trace-more" data-testid="trace-load-more" disabled=${busy} onClick=${loadMore}>${busy ? "Loading…" : "Load more"}</button>`
      : null}
  </section>`;
}
