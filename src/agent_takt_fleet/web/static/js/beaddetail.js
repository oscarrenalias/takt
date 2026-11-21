// Bead screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchBead } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchBead(name, beadId);

export function BeadDetail({ data, params }) {
  return html`<section data-testid="screen-bead-detail">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Bead</h2>
    <pre class="mono explorer-raw" data-testid="bead-detail-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
