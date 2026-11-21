// Activity screen: initial events from /api/events, live updates via ActivityFeed.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchEvents } from "./api.js";
import { ActivityFeed, MAX_EVENTS } from "./feed.js";

export const load = () => fetchEvents({ limit: MAX_EVENTS });

export function Activity({ data }) {
  const events = (data && data.events) || [];
  return html`<section data-testid="screen-activity">
    <h2>Activity</h2>
    <${ActivityFeed} events=${events} />
  </section>`;
}
