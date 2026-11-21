// Features screen (placeholder: loads data and renders a JSON preview until the full view lands).
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchFeatures } from "./api.js";
import { Breadcrumb } from "./breadcrumb.js";

export const load = ({ name, rootId, beadId, seq, attempt, auditId }) => fetchFeatures(name);

export function Features({ data, params }) {
  return html`<section data-testid="screen-features">
    <${Breadcrumb} params=${{ ...params, section: "features" }} />
    <h2>Features</h2>
    <pre class="mono explorer-raw" data-testid="features-raw">${JSON.stringify(data, null, 2)}</pre>
  </section>`;
}
