// Audit screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchAudit } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchAudit(name, rootId, auditId);

export function Audit({ data, params }) {
  return html`<section data-testid="screen-audit">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <h2>Audit</h2>
    <pre class="mono explorer-raw" data-testid="audit-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
