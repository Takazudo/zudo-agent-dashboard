# Accepted round08 implementation reference

The user accepted this synthetic dashboard prototype for implementation. These
are inert reference files, not changes to the running product. Do not merge this
resource branch as if it implements the feature. Integrate the accepted behavior
in the repository's production architecture and preserve its security boundaries.

- Open `dist/index.html` for the complete self-contained accepted interface
- `editor-src/composer.js` is the authored real CodeMirror/Vim integration
- `dist/editor.js` is its original bundled output; no runtime CDN is needed
- `package.json` and `package-lock.json` are the original prototype manifests
- `accepted-decisions.md` is the consolidated accepted behavior and QA limits
- `references.md` contains the pinned design/implementation reference sources
- `dist/THIRD_PARTY_NOTICES.txt` retains dependency license notices
- `resource-manifest.json` records exact byte hashes and the one fixture sanitation

The editor bundle and authored HTML/CSS/JS are original accepted source bytes,
except that synthetic absolute-looking project paths in fixtures were changed to
explicit synthetic-root labels before public transfer. No private hosting metadata,
account identifiers, screenshots, recordings, messages, credentials, local executor
paths or earlier/rejected rounds are included. License-author credits are preserved.

To rebuild only the prototype's editor, run `npm ci --ignore-scripts` and
`npm run build:editor` within this directory. The checked-in static dist is already
built. These commands are not the production repository's install/build process.

The repository's current checks, package discovery and manifest target product
paths, so this inert directory requires no scanner exclusions. The root ignores
dist directories for build output; these reference files are intentionally tracked.
Do not add generated dependencies or replace the root dependency files with these.

Implement in the existing application, keep useful lasting tests/docs in their
proper production locations, then remove this temporary resource directory before
the implementation/root PR merges. The implementation coordinator owns branch/PR
integration, verification and merge decisions. This commit grants no live terminal
access and does not activate capture, network routes or persistent credentials.
