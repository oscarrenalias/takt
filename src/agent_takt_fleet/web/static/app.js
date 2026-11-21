// App shell: header, hash router, SSE connection and view data loading.
import { html, render, useState, useEffect, useRef } from "./vendor/htm-preact-standalone-3.1.1.mjs";
import { connectStream, createBeadsRefetcher } from "./js/stream.js";
import { parseRoute } from "./js/util.js";
import * as overview from "./js/overview.js";
import * as activity from "./js/activity.js";
import * as project from "./js/project.js";
import * as features from "./js/features.js";
import * as featuretree from "./js/featuretree.js";
import * as audit from "./js/audit.js";
import * as beads from "./js/beads.js";
import * as beaddetail from "./js/beaddetail.js";
import * as trace from "./js/trace.js";
import * as telemetry from "./js/telemetry.js";

const SCREENS = {
  overview: { load: overview.load, View: overview.Overview },
  activity: { load: activity.load, View: activity.Activity },
  project: { load: project.load, View: project.Project },
  features: { load: features.load, View: features.Features, beads: true },
  featuretree: { load: featuretree.load, View: featuretree.FeatureTree, beads: true },
  audit: { load: audit.load, View: audit.Audit },
  beads: { load: beads.load, View: beads.Beads, beads: true },
  beaddetail: { load: beaddetail.load, View: beaddetail.BeadDetail, beads: true, beadScoped: true },
  trace: { load: trace.load, View: trace.Trace, beads: true, beadScoped: true },
  telemetry: { load: telemetry.load, View: telemetry.Telemetry },
};

// Scope for beads_changed refetch: the route's project, and the bead id when bead-scoped.
export function beadsScope(route) {
  const screen = SCREENS[route.name];
  if (!screen || !screen.beads) return null;
  return { project: route.params.name, beadIds: screen.beadScoped ? [route.params.beadId] : undefined };
}

function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

function App() {
  const hash = useHash();
  const route = parseRoute(hash);
  const [connection, setConnection] = useState("reconnecting");
  const [version, setVersion] = useState(0);
  const [view, setView] = useState({ data: null, error: null, loading: true });
  const routeRef = useRef(route);
  routeRef.current = route;

  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    let debounce = null;
    const refetchSoon = () => {
      if (debounce) return;
      debounce = setTimeout(() => {
        debounce = null;
        bump();
      }, 250);
    };
    const beadsRefetch = createBeadsRefetcher(bump, () => beadsScope(routeRef.current));
    const stream = connectStream({
      onState: setConnection,
      onReconnect: bump, // refetch the current view after a drop
      onEvent: (name, data) => {
        const r = routeRef.current;
        beadsRefetch.handle(name, data);
        if (name === "fleet_changed" && r.name === "overview") refetchSoon();
        else if (name === "project_updated") {
          const pname = data && (data.name || data.project);
          if (r.name === "overview" || (r.name === "project" && (!pname || pname === r.params.name))) refetchSoon();
        } else if (name === "fleet_event") {
          // Activity prepends live events itself and refetches only on reconnect.
          if (r.name === "project" && data && (!data.project || data.project === r.params.name)) refetchSoon();
        }
      },
    });
    return () => {
      if (debounce) clearTimeout(debounce);
      beadsRefetch.cancel();
      stream.close();
    };
  }, []);

  const key = `${route.name}:${JSON.stringify(route.params)}`;
  useEffect(() => {
    const screen = SCREENS[route.name];
    if (!screen) {
      setView({ data: null, error: null, loading: false });
      return undefined;
    }
    let cancelled = false;
    // Keep stale data on screen while refetching the same view.
    setView((prev) => (prev.key === key ? prev : { data: null, error: null, loading: true, key }));
    screen
      .load(route.params)
      .then((data) => !cancelled && setView({ data, error: null, loading: false, key }))
      .catch((error) => !cancelled && setView((prev) => ({ ...prev, error, loading: false, key })));
    return () => {
      cancelled = true;
    };
  }, [key, version]);

  const screen = SCREENS[route.name];
  let body;
  if (!screen) body = html`<p data-testid="screen-notfound">Page not found.</p>`;
  else if (view.error && !view.data) body = html`<p class="status-error" data-testid="load-error">Failed to load: ${view.error.message}</p>`;
  else if (view.loading && !view.data) body = html`<p class="muted" data-testid="loading">Loading…</p>`;
  else body = html`<${screen.View} data=${view.data} params=${route.params} />`;

  const active = (n) => (route.name === n ? "page" : null);
  return html`
    <header class="header">
      <h1>takt-fleet</h1>
      <nav data-testid="nav">
        <a href="#/" data-testid="nav-overview" aria-current=${active("overview")}>Overview</a>
        <a href="#/activity" data-testid="nav-activity" aria-current=${active("activity")}>Activity</a>
      </nav>
      <span class="spacer"></span>
      <span class="muted" data-testid="connection-state" data-state=${connection}>${connection}</span>
    </header>
    <main data-testid="view">${body}</main>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
