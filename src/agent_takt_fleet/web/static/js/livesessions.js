// Shared live-session state for screens that link to live pages: fetches /api/live and
// refetches (debounced) on live_session_started / updated / ended stream events.
import { useState, useEffect } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { getJSON } from "./api.js";
import { connectStream } from "./stream.js";

export const LIVE_URL = "/api/live";
const LIVE_EVENTS = ["live_session_started", "live_session_updated", "live_session_ended"];
export const isLiveEvent = (name) => LIVE_EVENTS.includes(name);

// Sessions still running (the fleet endpoint only lists live ones; guard against ended rows).
export const activeSessions = (data, project) =>
  ((data && data.sessions) || []).filter((s) => s && !s.ended && s.status !== "ended" && (!project || s.project === project));

// Map "<project>/<bead_id>" -> session, newest first wins.
export function sessionIndex(sessions) {
  const idx = new Map();
  for (const s of sessions || []) {
    const key = `${s.project}/${s.bead_id}`;
    if (!idx.has(key)) idx.set(key, s);
  }
  return idx;
}

export function useLiveSessions({ project, delay = 250, connect = connectStream, fetchLive = () => getJSON(LIVE_URL) } = {}) {
  const [sessions, setSessions] = useState([]);
  useEffect(() => {
    let closed = false;
    let timer = null;
    const pull = () =>
      fetchLive()
        .then((d) => !closed && setSessions(activeSessions(d, project)))
        .catch(() => {});
    pull();
    const stream = connect({
      onReconnect: pull,
      onEvent: (name, data) => {
        if (!isLiveEvent(name) || timer) return;
        if (project && data && data.project && data.project !== project) return;
        timer = setTimeout(() => {
          timer = null;
          pull();
        }, delay);
      },
    });
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      stream.close();
    };
  }, [project, delay]);
  return sessions;
}

// Small "watch live" anchor for a bead with a live session.
export function watchLiveHref(session) {
  return `#/projects/${encodeURIComponent(session.project)}/beads/${encodeURIComponent(session.bead_id)}/live/${encodeURIComponent(session.session_id)}`;
}
