// Feature screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFeature } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchFeature(name, rootId);

export function FeatureTree({ data, params }) {
  return html`<section data-testid="screen-feature-tree">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <h2>Feature</h2>
    <pre class="mono explorer-raw" data-testid="feature-tree-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
