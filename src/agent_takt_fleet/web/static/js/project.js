// Placeholder project screen: renders the server view model from /api/projects/{name}.
import { html } from "../vendor/htm-preact-standalone-3.1.1.mjs";
import { fetchProject } from "./api.js";

export const load = ({ name }) => fetchProject(name);

export function Project({ params }) {
  return html`<section data-testid="screen-project">
    <h2>${params.name}</h2>
  </section>`;
}
