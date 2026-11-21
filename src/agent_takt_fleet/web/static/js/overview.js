// Overview screen: one ProjectCard per project from the /api/fleet view model.
// The app shell refetches on fleet_changed / project_updated, so props stay live.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFleet } from "./api.js";
import { ProjectCard } from "./card.js";

export const load = () => fetchFleet();

export function Overview({ data }) {
  const projects = (data && data.projects) || [];
  return html`<section data-testid="screen-overview">
    <h2>Overview</h2>
    <p class="muted" data-testid="project-count">${projects.length} project(s)</p>
    ${projects.length === 0
      ? html`<p class="muted" data-testid="overview-empty">No projects registered.</p>`
      : html`<div class="cards" data-testid="project-cards">
          ${projects.map((p) => html`<${ProjectCard} key=${p.name} project=${p} />`)}
        </div>`}
  </section>`;
}
