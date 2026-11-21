// Live session page: header, summary bar and a timeline that grows as the stream reports new events.
// Events carry an `index`; fetches use ?after=<count> and re-sent indices replace the stored event.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchSession } from "./api.js";
import { connectStream, liveSessionMatches, mergeEventsByIndex } from "./stream.js";
import { Breadcrumb } from "./breadcrumb.js";
import { hrefs } from "./util.js";
import { Event, ensureStyles, text, num, fmtNum } from "./trace.js";

export const load = ({ name, beadId, sessionId }) => fetchSession(name, beadId, sessionId, 0);

const fmtElapsed = (ms) => {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m ${s % 60}s` : m ? `${m}m ${s % 60}s` : `${s}s`;
};

export function elapsedMs(meta, now = Date.now()) {
  const start = Date.parse(meta && meta.started_at);
  if (Number.isNaN(start)) return NaN;
  const end = meta && meta.ended && meta.ended_at ? Date.parse(meta.ended_at) : now;
  return (Number.isNaN(end) ? now : end) - start;
}

function Header({ meta, now }) {
  const m = meta || {};
  const status = m.ended ? "ended" : text(m.status) || "waiting";
  return html`<div class="trace-meta live-header" data-testid="live-header">
    <dl class="trace-dl">
      <dt>Bead</dt><dd data-testid="live-bead">${m.bead_id}${text(m.bead_title) ? ` · ${m.bead_title}` : ""}</dd>
      <dt>Agent</dt><dd data-testid="live-agent">${text(m.agent_type) || "—"}</dd>
      <dt>Role</dt><dd data-testid="live-role">${text(m.role) || "—"}</dd>
      <dt>Started</dt><dd data-testid="live-started">${text(m.started_at) || "—"}</dd>
      <dt>Elapsed</dt><dd data-testid="live-elapsed">${fmtElapsed(elapsedMs(m, now))}</dd>
      <dt>Status</dt><dd><span class=${`live-status live-status-${status}`} data-testid="live-status" data-status=${status}>${status}</span></dd>
    </dl>
  </div>`;
}

function SummaryBar({ summary }) {
  const s = summary || {};
  const tools = Object.values(s.tool_counts || {}).reduce((a, b) => a + num(b), 0);
  const u = s.usage || {};
  return html`<p class="trace-summary live-summary" data-testid="live-summary">
    Tool calls <strong data-testid="live-tool-calls">${fmtNum(tools)}</strong>
    · Errors <strong class=${num(s.error_count) ? "status-error" : ""} data-testid="live-errors">${fmtNum(s.error_count)}</strong>
    · Tokens <span data-testid="live-tokens">in ${fmtNum(u.input_tokens)} · out ${fmtNum(u.output_tokens)}</span>
  </p>`;
}

function StatusNotice({ status, error }) {
  if (status === "too_large")
    return html`<p class="live-notice live-too-large" role="status" data-testid="live-too-large">
      The transcript is too large to follow live; new events are no longer shown. The full trace is available once the session ends.</p>`;
  if (status === "waiting")
    return html`<p class="live-notice live-waiting" role="status" data-testid="live-waiting">
      Waiting for the agent transcript to appear…</p>`;
  return error ? html`<p class="status-error" data-testid="live-error">${text(error)}</p>` : null;
}

export function Live({ data, params }) {
  ensureStyles();
  const { name, beadId, sessionId } = params;
  const [state, setState] = useState(() => ({ meta: data.meta, summary: data.summary, error: data.error, events: mergeEventsByIndex([], data.events) }));
  const [follow, setFollow] = useState(true);
  const [now, setNow] = useState(Date.now());
  const [fetchError, setFetchError] = useState(null);
  const countRef = useRef(0);
  const followRef = useRef(true);
  const endRef = useRef(null);
  const programmatic = useRef(false);
  followRef.current = follow;
  countRef.current = state.events.length;

  const apply = (r) =>
    setState((prev) => ({
      meta: r.meta || prev.meta,
      summary: r.summary || prev.summary,
      error: r.error,
      events: mergeEventsByIndex(prev.events, r.events),
    }));

  // Reset when navigating to another session; seed from freshly loaded data.
  useEffect(() => {
    setState({ meta: data.meta, summary: data.summary, error: data.error, events: mergeEventsByIndex([], data.events) });
    setFollow(true);
  }, [name, beadId, sessionId, data]);

  // Stream: fetch only newer events (?after=<count>) on updates; refetch meta when the session ends.
  useEffect(() => {
    let cancelled = false;
    let busy = false;
    let again = false;
    const pull = () => {
      if (busy) {
        again = true;
        return;
      }
      busy = true;
      fetchSession(name, beadId, sessionId, countRef.current)
        .then((r) => {
          if (cancelled) return;
          setFetchError(null);
          apply(r);
        })
        .catch((e) => !cancelled && setFetchError(e))
        .finally(() => {
          busy = false;
          if (again && !cancelled) {
            again = false;
            pull();
          }
        });
    };
    const scope = { project: name, beadId, sessionId };
    const stream = connectStream({
      onReconnect: pull,
      onEvent: (n, d) => {
        if ((n === "live_session_updated" || n === "live_session_ended" || n === "trace_captured") && liveSessionMatches(d, scope)) pull();
      },
    });
    return () => {
      cancelled = true;
      stream.close();
    };
  }, [name, beadId, sessionId]);

  const ended = !!(state.meta && state.meta.ended);
  useEffect(() => {
    if (ended) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ended]);

  // Auto-scroll to the newest event while following.
  useEffect(() => {
    if (follow && endRef.current && endRef.current.scrollIntoView) {
      programmatic.current = true;
      endRef.current.scrollIntoView({ block: "end" });
      setTimeout(() => (programmatic.current = false), 100);
    }
  }, [state.events.length, follow]);

  // Pause following when the user scrolls up; resume when they return to the bottom.
  useEffect(() => {
    let last = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const nearBottom = window.innerHeight + y >= document.documentElement.scrollHeight - 40;
      if (!programmatic.current && y < last && followRef.current && !nearBottom) setFollow(false);
      last = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const meta = state.meta || {};
  const trace = meta.trace;
  const status = ended ? "ended" : meta.status;

  return html`<section data-testid="screen-live" class="trace-view live-view">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Live session <span class="muted">· <a href=${hrefs.bead(name, beadId)} data-testid="live-bead-link">${beadId}</a></span></h2>
    ${ended
      ? html`<div class="live-banner" role="status" data-testid="live-ended-banner">
          Session ended${text(meta.end_reason) ? ` (${meta.end_reason})` : ""}.
          ${trace && trace.seq !== undefined && trace.seq !== null
            ? html`<a href=${hrefs.trace(name, trace.bead_id || beadId, trace.seq)} data-testid="live-trace-link">View captured trace</a>`
            : html`<span class="muted" data-testid="live-trace-pending">Captured trace not available yet.</span>`}
        </div>`
      : null}
    <${Header} meta=${meta} now=${now} />
    <${SummaryBar} summary=${state.summary} />
    <${StatusNotice} status=${status} error=${state.error} />
    ${fetchError ? html`<p class="status-error" data-testid="live-load-error">Failed to update: ${fetchError.message}</p>` : null}
    <div class="live-toolbar">
      <label><input type="checkbox" checked=${follow} data-testid="live-autoscroll"
        onChange=${(e) => setFollow(e.currentTarget.checked)} /> Auto-scroll</label>
      <span class="muted" data-testid="live-count">${state.events.length} events</span>
    </div>
    ${state.events.length
      ? html`<ol class="trace-timeline" data-testid="live-timeline">${state.events.map((ev) => html`<${Event} key=${ev.index} ev=${ev} />`)}</ol>`
      : html`<p class="muted" data-testid="live-empty">No events yet.</p>`}
    <div ref=${endRef} data-testid="live-end"></div>
  </section>`;
}
