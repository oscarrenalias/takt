// Trace screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchTrace } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchTrace(name, beadId, seq);

export function Trace({ data, params }) {
  return html`<section data-testid="screen-trace">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Trace</h2>
    <pre class="mono explorer-raw" data-testid="trace-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
