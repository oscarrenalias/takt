// Project screen: header card (with a 'running since' link), recent features, recent beads,
// then a secondary footer with the last run and a short recent-activity strip.
// The app shell refetches on project_updated / fleet_event for this project, so it stays live.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchProject, fetchEvents, fetchFeatures, fetchBeads } from "./api.js";
import { ProjectCard, formatElapsed } from "./card.js";
import { beadIdOf } from "./feed.js";
import { relativeTime, hrefs } from "./util.js";

export const FEATURE_LIMIT = 8;
export const BEAD_LIMIT = 10;
export const EVENT_LIMIT = 10;

const EMPTY_EVENTS = { events: [], next_cursor: null };
const EMPTY_FEATURES = { features: [] };
const EMPTY_BEADS = { beads: [], total: 0 };

export async function load({ name }) {
  try {
    const [detail, ev, feats, beads] = await Promise.all([
      fetchProject(name),
      fetchEvents({ project: name, limit: EVENT_LIMIT }).catch(() => EMPTY_EVENTS),
      fetchFeatures(name).catch(() => EMPTY_FEATURES),
      fetchBeads(name, { limit: BEAD_LIMIT }).catch(() => EMPTY_BEADS),
    ]);
    return {
      ...detail,
      events: ((ev && ev.events) || []).slice(0, EVENT_LIMIT),
      features: ((feats && feats.features) || []).slice(0, FEATURE_LIMIT),
      features_total: (feats && (feats.total ?? (feats.features || []).length)) || 0,
      beads: ((beads && beads.beads) || []).slice(0, BEAD_LIMIT),
      beads_total: (beads && beads.total) || 0,
    };
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

const STATUS_ORDER = ["done", "in_progress", "ready", "open", "blocked", "handed_off"];

function FeatureItem({ feature: f, name }) {
  const counts = f.counts || {};
  const stats = STATUS_ORDER.filter((s) => counts[s]);
  return html`<li class=${`project-row feature-row${f.in_flight ? " in-flight" : ""}`} data-testid="project-feature-row"
      data-root-id=${f.bead_id} style="display:flex;flex-wrap:wrap;gap:.25rem .75rem;align-items:baseline">
    <a href=${hrefs.feature(name, f.bead_id)} data-testid="feature-link" style="overflow-wrap:anywhere">${f.title || f.bead_id}</a>
    ${f.in_flight ? html`<span class="badge status-running">in flight</span>` : null}
    ${stats.map((s) => html`<span key=${s} class=${`muted status-${s}`}>${counts[s]} ${s.replace("_", " ")}</span>`)}
    <span class="muted" title=${f.last_activity_at || ""}>${relativeTime(f.last_activity_at)}</span>
  </li>`;
}

function BeadItem({ bead: b, name }) {
  return html`<li class="project-row bead-row" data-testid="project-bead-row" data-bead-id=${b.bead_id} data-status=${b.status}
      style="display:flex;flex-wrap:wrap;gap:.25rem .75rem;align-items:baseline">
    <span class=${`badge status-${b.status}`}>${b.status}</span>
    <span class="muted">${b.agent_type}</span>
    <a href=${hrefs.bead(name, b.bead_id)} data-testid="bead-link" style="overflow-wrap:anywhere">${b.title || b.bead_id}</a>
    ${(b.badges || []).map((x) => html`<span class="badge bead-badge" key=${x}>${x}</span>`)}
    <span class="muted" title=${b.updated_at || ""}>${relativeTime(b.updated_at)}</span>
  </li>`;
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
  const runs = detail.scheduler_runs || [];
  const running = runs.find((r) => r.status === "running");
  const last = runs[0];
  const features = detail.features || [];
  const beads = detail.beads || [];
  const events = (detail.events || []).slice(0, EVENT_LIMIT);

  return html`<section data-testid="screen-project" style="max-width:100%;overflow-wrap:anywhere">
    <h2>${name}</h2>
    <${ProjectCard} project=${detail} />
    ${running
      ? html`<p data-testid="project-running" class="status-running">
          <a href=${hrefs.run(name, running.run_id)} data-testid="running-since-link"><strong>running since <span title=${running.started_at}>${relativeTime(running.started_at)}</span></strong></a>
        </p>`
      : null}

    <h3>Recent features</h3>
    ${features.length === 0
      ? html`<p class="muted" data-testid="project-features-empty">No features yet.</p>`
      : html`<ul class="project-list" data-testid="project-features" style="list-style:none;padding:0;margin:0">
          ${features.map((f) => html`<${FeatureItem} key=${f.bead_id} feature=${f} name=${name} />`)}
        </ul>`}
    <p><a href=${hrefs.features(name)} data-testid="link-features">View all features</a></p>

    <h3>Recent beads</h3>
    ${beads.length === 0
      ? html`<p class="muted" data-testid="project-beads-empty">No beads yet.</p>`
      : html`<ul class="project-list" data-testid="project-beads" style="list-style:none;padding:0;margin:0">
          ${beads.map((b) => html`<${BeadItem} key=${b.bead_id} bead=${b} name=${name} />`)}
        </ul>`}
    <p><a href=${hrefs.beads(name)} data-testid="link-beads">View all beads</a></p>

    <footer class="project-footer muted" data-testid="project-footer" style="margin-top:2rem;font-size:.9em">
      <p data-testid="project-last-run">
        ${last
          ? html`Last run: <a href=${hrefs.run(name, last.run_id)} data-testid="last-run-link" class="mono">${last.run_id}</a>
              · ${last.status || "unknown"} · <span title=${last.started_at}>${relativeTime(last.started_at)}</span>`
          : "No scheduler runs yet."}
        · <a href=${hrefs.runs(name)} data-testid="link-runs">All runs</a>
      </p>
      <h4>Recent activity</h4>
      ${events.length === 0
        ? html`<p data-testid="activity-empty">No events.</p>`
        : html`<ul class="feed" data-testid="project-activity">
            ${events.map(
              (e, i) => html`<li key=${`${e.timestamp}|${e.event_type}|${i}`} class=${`feed-item sev-${e.severity || "info"}`}
                  data-testid="activity-item" data-severity=${e.severity || "info"}>
                <span class="time" title=${e.timestamp}>${relativeTime(e.timestamp)}</span>
                <span class="summary">${e.summary}</span>
                ${beadIdOf(e)
                  ? html`<a class="bead-link mono" href=${hrefs.bead(name, beadIdOf(e))} data-testid="activity-bead-link">${beadIdOf(e)}</a>`
                  : null}
              </li>`
            )}
          </ul>`}
      <p><a href="#/activity" data-testid="link-activity">View all activity</a></p>
    </footer>
  </section>`;
}
