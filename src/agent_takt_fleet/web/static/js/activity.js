// Placeholder activity screen: renders the server view model from /api/events.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchEvents } from "./api.js";

export const load = () => fetchEvents({ limit: 100 });

export function Activity({ data }) {
  const events = (data && data.events) || [];
  return html`<section data-testid="screen-activity">
    <h2>Activity</h2>
    <p class="muted">${events.length} event(s)</p>
  </section>`;
}
