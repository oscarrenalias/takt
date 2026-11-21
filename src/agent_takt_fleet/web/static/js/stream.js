// EventSource wrapper: dispatches fleet_event / project_updated / fleet_changed / beads_changed,
// reports connection state ("live" | "reconnecting") and resubscribes after drops.
import { useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { STREAM_URL } from "./api.js";

const EVENT_NAMES = ["fleet_event", "project_updated", "fleet_changed", "beads_changed"];
const MAX_BACKOFF_MS = 10000;

export function connectStream({ onEvent, onState, onReconnect, url = STREAM_URL, factory } = {}) {
  const make = factory || ((u) => new EventSource(u));
  let source = null;
  let timer = null;
  let closed = false;
  let attempts = 0;
  let wasDropped = false;
  let state = null;

  const setState = (next) => {
    if (next !== state) {
      state = next;
      if (onState) onState(next);
    }
  };

  const schedule = () => {
    if (closed || timer) return;
    const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempts);
    attempts += 1;
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  };

  function open() {
    if (closed) return;
    source = make(url);
    source.onopen = () => {
      attempts = 0;
      setState("live");
      if (wasDropped) {
        wasDropped = false;
        if (onReconnect) onReconnect();
      }
    };
    source.onerror = () => {
      wasDropped = true;
      setState("reconnecting");
      // Browser retries while CONNECTING; if it gave up (CLOSED), resubscribe ourselves.
      if (source && source.readyState === 2) {
        source.close();
        source = null;
        schedule();
      }
    };
    for (const name of EVENT_NAMES) {
      source.addEventListener(name, (msg) => {
        let data = null;
        try {
          data = JSON.parse(msg.data);
        } catch (_) {
          return;
        }
        if (onEvent) onEvent(name, data);
      });
    }
  }

  setState("reconnecting");
  open();

  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      if (source) source.close();
    },
  };
}

// Does a beads_changed payload ({project, bead_ids}) concern the given scope?
// `project` limits to one project; `beadIds` (optional) to changes touching any of those ids.
export function beadsChangeMatches(data, { project, beadIds } = {}) {
  if (!data) return false;
  if (project && data.project !== project) return false;
  if (beadIds && beadIds.length) {
    const changed = data.bead_ids || [];
    return beadIds.some((id) => changed.includes(id));
  }
  return true;
}

// Debounced beads_changed handler. Feed every stream event to `handle(name, data)`;
// `onChange` fires once per `delay` ms window for matching beads_changed events.
// `scope` may be an object or a function returning one (read at event time).
export function createBeadsRefetcher(onChange, scope = {}, delay = 250) {
  let timer = null;
  return {
    handle(name, data) {
      if (name !== "beads_changed") return;
      const sc = typeof scope === "function" ? scope() : scope;
      if (!sc || !beadsChangeMatches(data, sc) || timer) return;
      timer = setTimeout(() => {
        timer = null;
        onChange(data);
      }, delay);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}

// Preact hook: subscribe to the stream and call `onChange` (debounced) when beads change
// for `{ project, beadIds }`. Screens that cannot rely on the app shell refetch use this.
export function useBeadsChanged(onChange, { project, beadIds, delay = 250, connect = connectStream } = {}) {
  const cb = useRef(onChange);
  cb.current = onChange;
  const scope = useRef({ project, beadIds });
  scope.current = { project, beadIds };
  useEffect(() => {
    const r = createBeadsRefetcher((d) => cb.current(d), () => scope.current, delay);
    const stream = connect({ onEvent: r.handle });
    return () => {
      r.cancel();
      stream.close();
    };
  }, [delay]);
}
