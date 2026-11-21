// ActivityFeed: newest-first event list that prepends live fleet_event messages.
// Pass `project` to pin the feed to one project (no filter control is shown).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { connectStream } from "./stream.js";
import { relativeTime } from "./util.js";

export const MAX_EVENTS = 1000;

const keyOf = (e) => `${e.project}|${e.timestamp}|${e.event_type}|${e.summary}`;

// Merge `incoming` into `current`: dedupe, newest first, capped at `max`.
export function mergeEvents(current, incoming, max = MAX_EVENTS) {
  const seen = new Set();
  const all = [...incoming, ...current].filter((e) => {
    const k = keyOf(e);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  // Array.prototype.sort is stable: equal timestamps keep incoming-first order.
  all.sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0));
  return all.length > max ? all.slice(0, max) : all;
}

export function ActivityFeed({ events = [], project = null, connect = connectStream }) {
  const [items, setItems] = useState(() => mergeEvents([], events));
  const [filter, setFilter] = useState("");
  const [now, setNow] = useState(Date.now());
  const pinned = useRef(project);
  pinned.current = project;

  // Seed/refresh from the loaded view model without losing live items.
  useEffect(() => {
    setItems((cur) => mergeEvents(cur, events));
  }, [events]);

  useEffect(() => {
    const stream = connect({
      onEvent: (name, data) => {
        if (name !== "fleet_event" || !data) return;
        if (pinned.current && data.project !== pinned.current) return;
        setItems((cur) => mergeEvents(cur, [data]));
      },
    });
    return () => stream.close();
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const projects = [...new Set(items.map((e) => e.project))].sort();
  const active = project || filter;
  const shown = active ? items.filter((e) => e.project === active) : items;

  return html`<div class="feed-wrap" data-testid="activity-feed">
    ${project
      ? null
      : html`<label class="feed-filter">Project
          <select data-testid="activity-filter" value=${filter} onChange=${(e) => setFilter(e.target.value)}>
            <option value="">All projects</option>
            ${projects.map((p) => html`<option key=${p} value=${p}>${p}</option>`)}
          </select>
        </label>`}
    ${shown.length === 0
      ? html`<p class="muted" data-testid="activity-empty">No events.</p>`
      : html`<ul class="feed">
          ${shown.map(
            (e) => html`<li
              key=${keyOf(e)}
              class=${`feed-item sev-${e.severity || "info"}`}
              data-testid="activity-item"
              data-severity=${e.severity || "info"}
              data-project=${e.project}
            >
              <span class="project">${e.project}</span>
              <span class="time" title=${e.timestamp}>${relativeTime(e.timestamp, now)}</span>
              <span class="summary">${e.summary}</span>
            </li>`
          )}
        </ul>`}
  </div>`;
}
