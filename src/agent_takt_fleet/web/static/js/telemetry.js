// Telemetry attempt screen: prompt/response in self-scrolling monospace blocks, truncation notices,
// parsed result and metrics. Presentation only; the server supplies the (capped) text and flags.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchTelemetry } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchTelemetry(name, beadId, attempt);

const present = (v) => v !== null && v !== undefined && v !== "";
const show = (v) => (present(v) ? String(v) : "—");

function TextBlock({ id, title, text, truncated }) {
  return html`<section class="tm-block" data-testid=${`telemetry-${id}`}>
    <h3>${title}</h3>
    ${truncated
      ? html`<p class="tm-truncated" role="note" data-testid=${`telemetry-${id}-truncated`}>
          Truncated: only the first 256 KiB of the ${title.toLowerCase()} is shown.</p>`
      : null}
    ${typeof text === "string" && text !== ""
      ? html`<pre class="mono tm-pre" tabindex="0" data-testid=${`telemetry-${id}-text`}>${text}</pre>`
      : html`<p class="muted">No ${title.toLowerCase()} recorded.</p>`}
  </section>`;
}

function Json({ id, title, value }) {
  const empty = value === null || value === undefined || (typeof value === "object" && Object.keys(value).length === 0);
  return html`<section class="tm-block" data-testid=${`telemetry-${id}`}>
    <h3>${title}</h3>
    ${empty
      ? html`<p class="muted">None.</p>`
      : html`<pre class="mono tm-pre" tabindex="0" data-testid=${`telemetry-${id}-json`}>${JSON.stringify(value, null, 2)}</pre>`}
  </section>`;
}

function Metrics({ metrics }) {
  const entries = metrics && typeof metrics === "object" ? Object.entries(metrics) : [];
  return html`<section class="tm-block" data-testid="telemetry-metrics">
    <h3>Metrics</h3>
    ${entries.length
      ? html`<dl class="tm-metrics">${entries.map(
          ([k, v]) => html`<div key=${k} class="tm-metric"><dt>${k}</dt>
            <dd class="mono">${v !== null && typeof v === "object" ? JSON.stringify(v) : show(v)}</dd></div>`,
        )}</dl>`
      : html`<p class="muted">No metrics recorded.</p>`}
  </section>`;
}

export function Telemetry({ data, params }) {
  const d = data || {};
  const error = d.error && typeof d.error === "object" ? d.error : null;
  return html`<section data-testid="screen-telemetry" class="telemetry">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Telemetry attempt ${show(d.attempt !== undefined ? d.attempt : params.attempt)}</h2>
    <dl class="tm-summary" data-testid="telemetry-summary">
      <dt>Agent</dt><dd>${show(d.agent_type)}</dd>
      <dt>Outcome</dt><dd data-testid="telemetry-outcome">${show(d.outcome)}</dd>
      <dt>Started</dt><dd>${show(d.started_at)}</dd>
      <dt>Finished</dt><dd>${show(d.finished_at)}</dd>
    </dl>
    ${error
      ? html`<p class="tm-error" role="alert" data-testid="telemetry-error">${Object.values(error).filter(present).join(": ")}</p>`
      : null}
    <${TextBlock} id="prompt" title="Prompt" text=${d.prompt_text} truncated=${d.prompt_truncated === true} />
    <${TextBlock} id="response" title="Response" text=${d.response_text} truncated=${d.response_truncated === true} />
    <${Json} id="parsed" title="Parsed result" value=${d.parsed_result} />
    <${Metrics} metrics=${d.metrics} />
  </section>`;
}
