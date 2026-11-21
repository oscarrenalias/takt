// Activity screen: newest page from /api/events, live updates and paging via ActivityFeed.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchEvents } from "./api.js";
import { ActivityFeed, PAGE_SIZE } from "./feed.js";

export const load = () => fetchEvents({ limit: PAGE_SIZE });

export function Activity({ data }) {
  const events = (data && data.events) || [];
  const nextCursor = (data && data.next_cursor) || null;
  return html`<section data-testid="screen-activity">
    <h2>Activity</h2>
    <${ActivityFeed} events=${events} nextCursor=${nextCursor} />
  </section>`;
}
