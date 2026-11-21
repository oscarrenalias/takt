// Overview screen: one ProjectCard per project from the /api/fleet view model.
// The app shell refetches on fleet_changed / project_updated, so props stay live.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFleet } from "./api.js";
import { useState, useEffect } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { ProjectCard, formatElapsed } from "./card.js";
import { useLiveSessions, watchLiveHref } from "./livesessions.js";

export const load = () => fetchFleet();

function useTick(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

// "Live now" strip: one linked row per live session; renders nothing when none are live.
export function LiveNow({ sessions, now = Date.now() }) {
  if (!sessions || sessions.length === 0) return null;
  return html`<section class="live-now" data-testid="live-now" aria-label="Live now">
    <h3>Live now <span class="muted" data-testid="live-now-count">${sessions.length}</span></h3>
    <ul class="live-now-list">
      ${sessions.map((s) => html`<li key=${`${s.project}/${s.session_id}`} class="live-now-item" data-testid="live-now-item"
          data-project=${s.project} data-bead-id=${s.bead_id}>
        <span class="live-dot" aria-hidden="true"></span>
        <a href=${watchLiveHref(s)} data-testid="live-now-link">${s.bead_title || s.bead_id}</a>
        <span class="muted" data-testid="live-now-project">${s.project}</span>
        <span class="muted" data-testid="live-now-agent">${s.agent_type || "—"}</span>
        <span class="muted" data-testid="live-now-elapsed">${formatElapsed(s.started_at, now) || "—"}</span>
        <span class="muted mono" data-testid="live-now-tool">${s.last_tool || "—"}</span>
      </li>`)}
    </ul>
  </section>`;
}

export function Overview({ data }) {
  const projects = (data && data.projects) || [];
  const live = useLiveSessions();
  const now = useTick();
  return html`<section data-testid="screen-overview">
    <h2>Overview</h2>
    <${LiveNow} sessions=${live} now=${now} />
    <p class="muted" data-testid="project-count">${projects.length} project(s)</p>
    ${projects.length === 0
      ? html`<p class="muted" data-testid="overview-empty">No projects registered.</p>`
      : html`<div class="cards" data-testid="project-cards">
          ${projects.map((p) => html`<${ProjectCard} key=${p.name} project=${p} />`)}
        </div>`}
  </section>`;
}
