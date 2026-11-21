// Telemetry screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchTelemetry } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchTelemetry(name, beadId, attempt);

export function Telemetry({ data, params }) {
  return html`<section data-testid="screen-telemetry">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Telemetry</h2>
    <pre class="mono explorer-raw" data-testid="telemetry-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
