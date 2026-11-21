// Project screen: full ProjectCard, scheduler runs table and a project-pinned ActivityFeed.
// The app shell refetches on project_updated / fleet_event for this project; the feed
// additionally prepends live fleet_event messages itself.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchProject, fetchEvents } from "./api.js";
import { ProjectCard, formatElapsed } from "./card.js";
import { ActivityFeed, PAGE_SIZE } from "./feed.js";
import { relativeTime, hrefs } from "./util.js";

export async function load({ name }) {
  try {
    const [detail, ev] = await Promise.all([
      fetchProject(name),
      fetchEvents({ project: name, limit: PAGE_SIZE }).catch(() => ({ events: [], next_cursor: null })),
    ]);
    return { ...detail, events: (ev && ev.events) || [], events_next_cursor: (ev && ev.next_cursor) || null };
  } catch (err) {
    if (err && err.status === 404) return { notFound: true, name };
    throw err;
  }
}

export function runDuration(run, now = Date.now()) {
  const start = Date.parse(run.started_at || "");
  if (Number.isNaN(start)) return "";
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  if (Number.isNaN(end)) return "";
  return formatElapsed(new Date(start).toISOString(), Math.max(end, start));
}

function RunsTable({ runs }) {
  if (runs.length === 0) return html`<p class="muted" data-testid="scheduler-runs-empty">No scheduler runs.</p>`;
  return html`<div class="table-wrap">
    <table data-testid="scheduler-runs">
      <thead><tr><th>Run</th><th>Started</th><th>Duration</th><th>Exit reason</th></tr></thead>
      <tbody>
        ${runs.map((r) => {
          const running = r.status === "running";
          return html`<tr key=${r.run_id} data-testid="scheduler-run" data-run-id=${r.run_id} data-status=${r.status}>
            <td class="mono">${r.run_id}</td>
            <td title=${r.started_at}>${relativeTime(r.started_at)}</td>
            <td>${runDuration(r) || "—"}</td>
            <td>${running
              ? html`<span class="badge status-running" data-testid="run-status">running</span>`
              : html`<span data-testid="run-status">${r.status || "unknown"}</span>`}</td>
          </tr>`;
        })}
      </tbody>
    </table>
  </div>`;
}

export function Project({ data, params }) {
  const name = params.name;
  if (data && data.notFound) {
    return html`<section data-testid="screen-project-notfound">
      <h2>Project not found</h2>
      <p data-testid="project-notfound">No project named “${name}” is registered. <a href="#/">Back to overview</a></p>
    </section>`;
  }
  const detail = data || {};
  return html`<section data-testid="screen-project">
    <h2>${name}</h2>
    <p class="explorer-links" data-testid="project-explore-links">
      <a href=${hrefs.features(name)} data-testid="link-features">Features</a>
      <a href=${hrefs.beads(name)} data-testid="link-beads">Beads</a>
    </p>
    <${ProjectCard} project=${detail} />
    <h3>Scheduler runs</h3>
    <${RunsTable} runs=${detail.scheduler_runs || []} />
    <h3>Activity</h3>
    <${ActivityFeed} events=${detail.events || []} nextCursor=${detail.events_next_cursor || null} project=${name} />
  </section>`;
}
