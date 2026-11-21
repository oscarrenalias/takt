// Placeholder overview screen: renders the server view model from /api/fleet.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFleet } from "./api.js";

export const load = () => fetchFleet();

export function Overview({ data }) {
  const projects = (data && data.projects) || [];
  return html`<section data-testid="screen-overview">
    <h2>Overview</h2>
    <p class="muted">${projects.length} project(s)</p>
  </section>`;
}
