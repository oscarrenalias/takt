// Beads screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchBeads } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchBeads(name);

export function Beads({ data, params }) {
  return html`<section data-testid="screen-beads">
    <${Breadcrumb} params=${{ ...params, section: "beads" }} />
    <h2>Beads</h2>
    <pre class="mono explorer-raw" data-testid="beads-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
