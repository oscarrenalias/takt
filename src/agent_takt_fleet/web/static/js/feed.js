// ActivityFeed: newest-first event list that prepends live fleet_event messages.
// Pass `project` to pin the feed to one project (no filter control is shown).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { connectStream } from "./stream.js";
import { fetchEvents } from "./api.js";
import { relativeTime, hrefs } from "./util.js";

export const PAGE_SIZE = 25;
export const MAX_KEPT = 500;

// Events carry bead_id in their payload (top-level bead_id is honoured too).
export const beadIdOf = (e) => e.bead_id || (e.payload && e.payload.bead_id) || null;

const keyOf = (e) => `${e.project}|${e.timestamp}|${e.event_type}|${e.summary}`;

// Merge `incoming` into `current`: dedupe, newest first, capped at `max` (oldest dropped).
export function mergeEvents(current, incoming, max = MAX_KEPT) {
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

// Cursor to request older events from. `pages` records, per fetched page, the cursor used to
// fetch it (`req`), the cursor after it (`next`) and its event keys. Events are opaque to the
// cursor, so after the oldest events are dropped we resume from the last page that still has
// kept events: its `next` if fully kept, else its `req` (re-fetched duplicates are deduped).
export function resumeCursor(pages, items) {
  const kept = new Set(items.map(keyOf));
  for (let i = pages.length - 1; i >= 0; i--) {
    const pg = pages[i];
    const some = pg.keys.some((k) => kept.has(k));
    if (!some) continue;
    return pg.keys.every((k) => kept.has(k)) ? pg.next : pg.req;
  }
  return pages.length ? pages[0].next : null;
}

const pageOf = (req, res) => ({
  req,
  next: (res && res.next_cursor) || null,
  keys: ((res && res.events) || []).map(keyOf),
});

export function ActivityFeed({
  events = [],
  nextCursor = null,
  project = null,
  connect = connectStream,
  fetchPage = fetchEvents,
}) {
  const [items, setItems] = useState(() => mergeEvents([], events));
  const [pages, setPages] = useState(() => [pageOf(null, { events, next_cursor: nextCursor })]);
  const [filter, setFilter] = useState("");
  const [known, setKnown] = useState(() => [...new Set(events.map((e) => e.project))]);
  const [loading, setLoading] = useState(false);
  const [now, setNow] = useState(Date.now());
  const active = project || filter;
  const activeRef = useRef(active);
  activeRef.current = active;
  const first = useRef(true);

  const reset = (list, cursor) => {
    setItems(mergeEvents([], list));
    setPages([pageOf(null, { events: list, next_cursor: cursor })]);
    setKnown((k) => [...new Set([...k, ...list.map((e) => e.project)])]);
  };

  // New server data (initial load or reconnect refetch): replace the list.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (filter && !project) {
      fetchPage({ project: filter, limit: PAGE_SIZE }).then((r) => reset(r.events || [], r.next_cursor)).catch(() => {});
    } else {
      reset(events, nextCursor);
    }
  }, [events, nextCursor]);

  // Changing the project filter reloads the newest page for that project.
  const onFilter = (value) => {
    setFilter(value);
    fetchPage({ project: value || undefined, limit: PAGE_SIZE })
      .then((r) => reset(r.events || [], r.next_cursor))
      .catch(() => {});
  };

  useEffect(() => {
    const stream = connect({
      onEvent: (name, data) => {
        if (name !== "fleet_event" || !data) return;
        if (activeRef.current && data.project !== activeRef.current) return;
        setItems((cur) => mergeEvents(cur, [data]));
        setKnown((k) => (k.includes(data.project) ? k : [...k, data.project]));
      },
    });
    return () => stream.close();
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const cursor = resumeCursor(pages, items);

  const loadOlder = async () => {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const r = await fetchPage({ project: activeRef.current || undefined, limit: PAGE_SIZE, cursor });
      const list = r.events || [];
      setItems((cur) => mergeEvents(cur, list));
      setPages((cur) => [...cur, pageOf(cursor, r)]);
    } catch (err) {
      // keep the button; the operator can retry
    } finally {
      setLoading(false);
    }
  };

  const projects = [...new Set([...known, ...items.map((e) => e.project)])].sort();
  const shown = active ? items.filter((e) => e.project === active) : items;

  return html`<div class="feed-wrap" data-testid="activity-feed">
    ${project
      ? null
      : html`<label class="feed-filter">Project
          <select data-testid="activity-filter" value=${filter} onChange=${(e) => onFilter(e.target.value)}>
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
              ${beadIdOf(e)
                ? html`<a class="bead-link mono" href=${hrefs.bead(e.project, beadIdOf(e))} data-testid="activity-bead-link">${beadIdOf(e)}</a>`
                : null}
            </li>`
          )}
        </ul>`}
    ${cursor
      ? html`<button type="button" class="feed-older" data-testid="activity-load-older" disabled=${loading} onClick=${loadOlder}>Load older</button>`
      : null}
  </div>`;
}
