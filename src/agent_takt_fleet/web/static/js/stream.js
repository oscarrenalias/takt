// EventSource wrapper: dispatches fleet_event / project_updated / fleet_changed,
// reports connection state ("live" | "reconnecting") and resubscribes after drops.
import { STREAM_URL } from "./api.js";

const EVENT_NAMES = ["fleet_event", "project_updated", "fleet_changed"];
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
