// Beads screen: searchable, filterable bead list. Filtering happens server-side (view model);
// the client passes filters to the API, renders rows and refetches on beads_changed.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchBeads } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";
import { useBeadsChanged } from "./stream.js";
import { hrefs, relativeTime } from "./util.js";

export const STATUSES = ["open", "ready", "in_progress", "done", "blocked", "handed_off"];
export const AGENTS = ["planner", "developer", "tester", "documentation", "review", "recovery", "defect"];
const SEARCH_DELAY = 200;

export const load = ({ name }) => fetchBeads(name);

// Build API query params from the filter state; empty values are dropped by fetchBeads.
export function filterParams({ q, status, agent, label }) {
  const labels = (label || "").split(/[\s,]+/).filter(Boolean);
  return { q: (q || "").trim(), status: status ? [status] : [], agent: agent ? [agent] : [], label: labels };
}

function BeadRow({ bead, project }) {
  return html`<tr data-testid="bead-row" data-bead-id=${bead.bead_id} data-status=${bead.status}>
    <td class="mono"><a href=${hrefs.bead(project, bead.bead_id)} data-testid="bead-link">${bead.bead_id}</a></td>
    <td>${bead.title}</td>
    <td><span class=${`badge status-${bead.status}`}>${bead.status}</span></td>
    <td>${bead.agent_type}</td>
    <td>${(bead.badges || []).map((b) => html`<span class="badge bead-badge" key=${b}>${b}</span> `)}</td>
    <td class="muted">${relativeTime(bead.updated_at)}</td>
  </tr>`;
}

export function Beads({ data, params }) {
  const project = params.name;
  const [filters, setFilters] = useState({ q: "", status: "", agent: "", label: "" });
  const [applied, setApplied] = useState(filters); // debounced copy that drives fetching
  const [result, setResult] = useState(data || { beads: [], total: 0 });
  const [error, setError] = useState(null);
  const appliedRef = useRef(applied);
  appliedRef.current = applied;
  const seq = useRef(0);

  const PAGE = 25;
  const MAX_PAGE = 200;
  const [loadingMore, setLoadingMore] = useState(false);
  const loadedRef = useRef(0);
  loadedRef.current = (result && result.beads ? result.beads.length : 0);

  // Fetch pages from the top until `want` rows are loaded (or no more pages). want=PAGE resets paging.
  const refetch = (want = PAGE) => {
    const n = ++seq.current;
    const base = filterParams(appliedRef.current);
    const run = async () => {
      let r = await fetchBeads(project, { ...base, limit: Math.min(MAX_PAGE, Math.max(PAGE, want)) });
      let beads = r.beads || [];
      while (beads.length < want && r.next_cursor && n === seq.current) {
        const more = await fetchBeads(project, { ...base, limit: Math.min(MAX_PAGE, want - beads.length), cursor: r.next_cursor });
        beads = beads.concat(more.beads || []);
        r = { ...more, beads };
      }
      return { ...r, beads };
    };
    run()
      .then((r) => n === seq.current && (setResult(r), setError(null)))
      .catch((e) => n === seq.current && setError(e));
  };

  const loadOlder = () => {
    const cursor = result && result.next_cursor;
    if (!cursor || loadingMore) return;
    const n = ++seq.current;
    setLoadingMore(true);
    fetchBeads(project, { ...filterParams(appliedRef.current), cursor })
      .then((r) => {
        if (n !== seq.current) return;
        setResult((prev) => ({ ...r, beads: [...((prev && prev.beads) || []), ...(r.beads || [])] }));
        setError(null);
      })
      .catch((e) => n === seq.current && setError(e))
      .finally(() => setLoadingMore(false));
  };

  // Debounce typing so each keystroke does not hit the API.
  useEffect(() => {
    const t = setTimeout(() => setApplied(filters), filters.q === applied.q && filters.label === applied.label ? 0 : SEARCH_DELAY);
    return () => clearTimeout(t);
  }, [filters]);

  // Refetch when applied filters change (skip the initial unfiltered load).
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    refetch(PAGE);
  }, [applied]);

  useBeadsChanged(() => refetch(Math.max(PAGE, loadedRef.current)), { project });

  const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }));
  const rows = (result && result.beads) || [];
  const total = result && typeof result.total === "number" ? result.total : rows.length;
  const clear = () => setFilters({ q: "", status: "", agent: "", label: "" });

  return html`<section data-testid="screen-beads">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Beads</h2>
    <div class="bead-filters" data-testid="bead-filters">
      <input type="search" class="bead-search" placeholder="Search id or title" aria-label="Search beads"
        data-testid="bead-search" value=${filters.q} onInput=${set("q")} />
      <select aria-label="Status" data-testid="bead-filter-status" value=${filters.status} onChange=${set("status")}>
        <option value="">All statuses</option>
        ${STATUSES.map((s) => html`<option value=${s} key=${s}>${s}</option>`)}
      </select>
      <select aria-label="Agent" data-testid="bead-filter-agent" value=${filters.agent} onChange=${set("agent")}>
        <option value="">All agents</option>
        ${AGENTS.map((a) => html`<option value=${a} key=${a}>${a}</option>`)}
      </select>
      <input type="text" placeholder="Label" aria-label="Label" data-testid="bead-filter-label"
        value=${filters.label} onInput=${set("label")} />
      <button type="button" data-testid="bead-filter-clear" onClick=${clear}>Clear</button>
      <span class="muted bead-count" data-testid="bead-count" aria-live="polite">
        ${rows.length < total ? `${rows.length} of ${total}` : total} ${total === 1 ? "bead" : "beads"}
      </span>
    </div>
    ${error ? html`<p class="status-error" data-testid="beads-error">Failed to load: ${error.message}</p>` : null}
    ${rows.length === 0
      ? html`<p class="muted" data-testid="beads-empty">No beads match.</p>`
      : html`<div class="table-wrap">
          <table data-testid="bead-table" class="bead-table">
            <thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Agent</th><th>Badges</th><th>Updated</th></tr></thead>
            <tbody>${rows.map((b) => html`<${BeadRow} key=${b.bead_id} bead=${b} project=${project} />`)}</tbody>
          </table>
        </div>`}
    ${result && result.next_cursor
      ? html`<p><button type="button" data-testid="bead-load-older" disabled=${loadingMore} onClick=${loadOlder}>
          ${loadingMore ? "Loading…" : "Load older"}</button></p>`
      : null}
  </section>`;
}
