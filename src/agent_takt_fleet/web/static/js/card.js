// Reusable ProjectCard: renders one project view model from /api/fleet.
import { html, useState, useEffect } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { relativeTime } from "./util.js";

export const BEAD_STATUS_ORDER = ["open", "ready", "in_progress", "done", "blocked", "handed_off"];

export function formatElapsed(iso, now = Date.now()) {
  const t = Date.parse(iso || "");
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

// Re-renders every `ms` so relative/elapsed times stay current between events.
function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export function ProjectCard({ project }) {
  const now = useNow();
  const name = project.name;
  const sched = project.scheduler || {};
  const state = sched.state || "idle";
  const counts = project.bead_counts || {};
  const statuses = BEAD_STATUS_ORDER.concat(Object.keys(counts).filter((k) => !BEAD_STATUS_ORDER.includes(k)));
  const blocked = Number(counts.blocked || 0);
  const href = `#/projects/${encodeURIComponent(name)}`;
  const go = (e) => {
    // Let the anchor handle modified clicks; otherwise navigate explicitly.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;
    e.preventDefault();
    window.location.hash = `/projects/${encodeURIComponent(name)}`;
  };
  const elapsed = state === "running" ? formatElapsed(sched.started_at, now) : "";
  const drift = project.version_drift;

  return html`<article
    class=${`card project-card health-${project.health || "unknown"} sched-${state}${blocked > 0 ? " has-blocked" : ""}`}
    data-testid="project-card"
    data-name=${name}
    data-health=${project.health}
  >
    <div data-testid=${`project-card-${name}`} class="project-card-body">
      <h3><a href=${href} onClick=${go} data-testid=${`project-link-${name}`}>${name}</a></h3>
      <div class="tags" data-testid="project-tags">
        ${(project.tags || []).map((t) => html`<span class="tag" key=${t}>${t}</span>`)}
        <span class=${`badge status-${project.health || "unknown"}`} data-testid="project-health">${project.health || "unknown"}</span>
      </div>
      <div class=${`scheduler scheduler-${state}`} data-testid="scheduler-state" data-state=${state}>
        <span class=${`badge status-${state === "running" ? "running" : state === "stale" ? "stale" : "idle"}`}>
          ${state === "running" ? html`<span class="pulse" aria-hidden="true"></span>` : null}${state}
        </span>
        ${elapsed ? html`<span class="elapsed" data-testid="scheduler-elapsed">${elapsed}</span>` : null}
      </div>
      <ul class="bead-counts" data-testid="bead-counts">
        ${statuses.map((s) => {
          const n = Number(counts[s] || 0);
          const cls = `bead-count status-${s}${s === "blocked" && n > 0 ? " emphasis" : ""}`;
          return html`<li class=${cls} data-testid=${`bead-count-${s}`} data-count=${n} key=${s}>
            <span class="count">${n}</span> <span class="label">${s.replace("_", " ")}</span>
          </li>`;
        })}
      </ul>
      <div class="meta">
        <span data-testid="project-version">takt ${project.takt_version || "unknown"}</span>
        ${drift === "behind" ? html`<span class="badge status-warn" data-testid="version-drift">upgrade available</span>` : null}
        ${drift === "unknown" && !project.takt_version ? html`<span class="badge status-idle" data-testid="version-drift">version unknown</span>` : null}
      </div>
      <div class="meta" data-testid="project-last-activity">Last activity: ${relativeTime(project.last_event_at, now)}</div>
    </div>
  </article>`;
}
