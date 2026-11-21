# Vendored third-party files

Files in this directory are pinned and unmodified. Pages never load scripts or
styles from a CDN (ADR-73d258d5).

## htm-preact-standalone-3.1.1.mjs

- Package: `htm` (includes Preact)
- Version: 3.1.1
- Source: https://registry.npmjs.org/htm/-/htm-3.1.1.tgz, file `package/preact/standalone.module.js`
- Also at: https://unpkg.com/htm@3.1.1/preact/standalone.module.js
- SHA-256: `72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7`
- Tarball integrity (npm registry): `sha512-983Vyg8NwUE7JkZ6NmOqpCZ+sh1bKv2iYTlUkzlWmA5JD2acKoxd4KVxbMmxX/85mtfdnDmTFoNKcg5DGAvxNQ==`
- Status: vendored byte-for-byte from the tarball above; unmodified.
- Exports: `h`, `html`, `render`, `Component`, `createContext` and the Preact hooks.

Upgrading is manual: replace the file, rename it with the new version and update
this manifest.
