# Contributing

Thanks for your interest. Issues and pull requests are welcome.

## Building and running

- `npm install` to install dependencies. Node 22 or newer (`npm test` leaves its
  glob to Node, which only expands one from Node 21).
- `npm run watch` (or `npm run compile`), then press F5 in VS Code to launch an Extension Development Host with the extension loaded.
- `npm run check-types` type checks and `npm test` runs every suite: the webview, the settings panel, the chat controller, the working set, the ACP client, and the CLI helpers.
- `npm run package` builds a `.vsix` you can install with `code --install-extension`.
- `npm run preview -- --scenario full` opens a mock chat in a browser for fast UI iteration without the CLI. See [the screenshots guide](docs/screenshots.md) for the scenarios behind the images in `docs/screenshots/` and how to regenerate them.
- `npm run preview:settings` does the same for the settings panel. It takes `--section <id>`, `--multi-root` (two workspace folders, so the scope tabs show both), and `--empty` (nothing configured, for the empty states). The mock payload lives in `scripts/settings-fixture.js` and is shared with the tests.

## Releasing

Releases are automated. To cut one:

1. Add the new version's entry to [`CHANGELOG.md`](CHANGELOG.md). The release notes are taken from it.
2. Bump the version and create the tag: `npm version patch` (or `minor` / `major`).
3. Push it: `git push --follow-tags`.

The [release workflow](.github/workflows/release.yml) then type checks, tests, builds the extension, creates a GitHub Release with the `.vsix` attached, and publishes to the VS Code Marketplace when `VSCE_PAT` is configured. When `OVSX_PAT` is configured, it also publishes to Open VSX as a best effort step. The release body is the matching `CHANGELOG.md` section (via `scripts/changelog-notes.js`) with GitHub's "Full Changelog" link appended, so if the entry is missing the notes fall back to a link to the changelog. The [CI workflow](.github/workflows/ci.yml) runs the same checks on pushes and pull requests to `main`, and the tests again on Windows and macOS, since the extension runs wherever VS Code does.

One time setup for Marketplace publishing:

- Create the `OEvortex` publisher on the [Marketplace publisher portal](https://marketplace.visualstudio.com/manage).
- Generate an Azure DevOps personal access token scoped to Marketplace, Manage.
- Add it as a repository Actions secret named `VSCE_PAT` (and optionally `OVSX_PAT` for Open VSX).

While `"preview": true` is set in `package.json`, the Marketplace listing keeps the preview badge.
