# AGENTS.md

Guidance for anyone (human or agent) working in this repository. This is also
read by Vtx as the workspace instructions, so keep it accurate.

## What this project is

**Vtx for VS Code** is a VS Code extension that brings the
[Vtx CLI](https://github.com/OEvortex/vtx-coding-agent) into a native chat panel.
Instead of running Vtx in a terminal, you chat with it in a side panel: streaming
replies, tool calls you can approve, and file edits shown as diffs you keep or
undo. Each chat is a real Vtx CLI session, driven over the Agent Client Protocol
(ACP), and saved per workspace.

It is a personal, open source project published on the VS Code Marketplace under the `OEvortex` publisher. The release workflow can also publish to Open VSX when `OVSX_PAT` is configured.

## How it fits together

- **`src/`** is the extension host (TypeScript, runs in Node inside VS Code).
  - `extension.ts` activates the extension, wires up commands, and shuts every
    agent down cleanly on the way out.
  - `acp/` speaks the Agent Client Protocol to a `vtx acp` process: the
    connection, the client, terminal handling, the throwaway agent the
    settings panel asks questions of, and the shared types.
  - `chat/` holds the `ChatManager` (one controller per chat surface: sidebar,
    editor tab, or window), the webview provider that renders each chat, and the
    transcript log each chat is replayed from.
  - `session/` stores sessions per workspace and lists them.
  - `diff/` tracks the working set of edits and exposes keep and undo.
  - `settings/` is the Vtx customisations editor and the config read and write
    services that back it. It edits Vtx's real files: `~/.vtx/config.yml`
    through `vtx config list`/`set`, `~/.vtx/mcp.json` and
    `<project>/.vtx/mcp.json`, `~/.vtx/hooks.yml` and
    `<project>/.vtx/hooks.yml`, the skill directories (`.agents/skills`,
    `~/.agents/skills`, `~/.vtx/skills`), and plugin rows from
    `vtx --dump-config` toggled with `vtx plugin enable|disable`.
  - `cli/` locates the `vtx` binary, lists models, reaps orphaned agents, and
    manages session locks.
  - `ui/` is the status bar.
- **`webview/`** is the browser side that runs inside the chat panel: `main.js`,
  markdown rendering, the Mermaid entry point, and the settings panel script.
- **`scripts/`** holds the tests, the harnesses they run on, the browser previews, the
  ACP probe and the release notes helper. Three harnesses, one per surface:
  `webview-harness.js` mounts the chat page in jsdom, `settings-harness.js` does the
  same for the settings panel, and `chat-harness.js` runs the real chat controller
  against a real ACP agent (see "Testing the host" below).
- **`media/`** is what the webviews are built from: the panel markup, the stylesheet,
  and the icons. **`resources/`** is the extension's own icons. **`docs/`** holds the
  screenshot set, the guide for regenerating it, and the known issues list.
- Build output goes to **`dist/`** (bundled by esbuild, do not edit by hand).

## Commands

Everything runs through npm scripts. Prefer these over ad hoc commands.

| Task | Command |
| --- | --- |
| Install dependencies | `npm install` |
| Build once (dev) | `npm run compile` |
| Rebuild on change | `npm run watch` |
| Production build | `npm run build` |
| Type check | `npm run check-types` |
| Run the tests | `npm test` |
| Run one test file | `npm run test:one -- scripts/webview.test.js` |
| Package a `.vsix` | `npm run package` |
| Preview the chat UI in a browser | `npm run preview -- --scenario full` |
| Preview the settings panel | `npm run preview:settings` |
| Drive the chat page by hand | `npm run harness` |
| Ask a real agent what it supports | `node scripts/acp-probe.js --methods session/list,_vtx.ai/rules/list --no-prompt` |

To run the extension itself, `npm run watch` and then press F5 in VS Code to
launch an Extension Development Host with the extension loaded.

The two preview commands render the UI in a plain browser from mock data, with no
Vtx CLI needed, which is the fastest way to iterate on the webview. The
screenshots in `docs/screenshots/` come from `npm run preview`. See
[docs/screenshots.md](docs/screenshots.md) for the scenarios and how to
regenerate them, and [CONTRIBUTING.md](CONTRIBUTING.md) for the full build and
release notes.

## The protocol Vtx speaks

Vtx's ACP server is implemented in `src/vtx/coding_agent/acp.py` in this
repository, so read that file rather than trusting a summary. The surface is
standard ACP plus two custom methods, all verified against the implementation:

- **Requests:** `initialize`, `authenticate` (aliased to `initialize`),
  `session/new`, `session/load`, `session/prompt`, `session/cancel`,
  `session/steer`, `session/rename`, `session/set_mode`, `session/set_model`,
  `session/list`, `session/delete`, `session/set_config_option`,
  `_vtx.ai/rules/list`, `_vtx.ai/hooks/list`.
- **Agent to client:** `session/update` notifications and
  `session/request_permission` requests. That is the whole list: Vtx never calls
  `fs/read_text_file`, `fs/write_text_file`, `terminal/*`, or elicitation, even
  though the extension still declares and serves them.
- **`initialize` result:** `agentCapabilities` is `loadSession: true`,
  `promptCapabilities: {image: true, audio: false, embeddedContext: true}`,
  `mcpCapabilities: {http: false, sse: false}`, `agentInfo: {name: "vtx"}`.
  There is no `_meta` capability block, so every capability check keyed on one
  answers false.
- **Config options:** `session/new` returns `mode`, `model`, and `thinking`
  select options. The `agent` option is added once the runtime has loaded its
  agent registry. Updates use `session/set_config_option` with `{sessionId,
  configId, value}` and apply to the live runtime. Only the `default` mode is
  currently supported. `session/set_mode` and `session/set_model` remain aliases.
- **Slash commands:** Vtx answers client-side commands like `/provider` with
  guidance toward the client equivalent rather than handling them. Model and
  provider picking is the client's job, filtered through the `model` config
  option. Changing model, thinking effort, or agent through that option updates
  the active `ConversationRuntime`.

**Session updates that are easy to miss**: `config_option_update` (the model
changed, possibly from `/model`) and `session_info_update` (the title). Both
arrive in every session, so handle them or the pickers and tab titles go stale.

**Dormant compatibility code.** `src/acp/client.ts`, `src/acp/types.ts`, and
`src/chat/chatViewProvider.ts` still carry the `_cognition.ai/*` surface this
extension was built against before Vtx: revert and fork, subagent control,
session share, document lifecycle, diagnostics, and the matching webview UI.
Every path is gated on an `agentCapabilities._meta` key Vtx never sends, so none
of it runs against `vtx acp`. It is dead code kept only so the diff stays
reviewable; remove it in one dedicated pass rather than letting it grow.

## Testing the host

`scripts/chat-harness.js` runs the real `ChatController` outside VS Code, against a
real ACP conversation. Nothing is mocked at the seam: the actual controller,
`ChangeTracker`, `SessionStore`, `AcpClient` and `TerminalManager`, with `vscode`
aliased to `scripts/vscode-stub.js`, and a fake agent that is a genuine peer over
stdio and answers `--version` and `auth status` too, so the health check takes the
real path as well.

**The agent's delays are the technique.** Every bug found in this file is the same
shape: work that takes seconds (spawning an agent, `session/new`, `session/load`, a
wake) finishing into a panel the user has moved on from. `createChat({ newDelay,
loadDelay, promptDelay })` and `setDelays(...)` hold a call open so a test can act
inside that window, and `setAgentMode` gives a chat a mode of its own so one chat's
settings can be told from another's.

Four things to know before writing one, each of which cost time to find:

- **The agent's environment comes from the `vtx.env` setting**, not from this
  process's environment, because the environment a client inherits is read from a
  login shell once and cached for the run: a delay set that way only ever reaches
  the first agent.
- **Every chat spawns its own agent**, so session ids have to be unique across them.
  Counting from one in each made every chat `s-1`, which silently makes two chats
  look like one to everything keyed on the id, and made a test pass for the wrong
  reason.
- **Never leave an agent running at the end of a test.** It keeps the runner's event
  loop alive, and the suite hangs with no output rather than failing. `cleanup()`
  disposes every harness for exactly this reason, so a failing test cannot strand
  one, and the same applies to a thinking block's label timer in the webview
  harness.
- **Wait for a condition, not a duration.** `until(predicate)` exists because the
  first chat in a run also pays for the health check and the login shell read, so a
  fixed wait is either flaky or slow.

**Check a new test by breaking the thing it covers.** Undo the fix, confirm the test
fails, put it back. Several tests here passed against the bug they were written for
until that was done.

## Releasing

Releases are automated. Bump the version and push the tag:

```
npm version patch   # or minor / major
git push --follow-tags
```

The tag push triggers the release workflow, which type checks, tests, builds, creates a GitHub Release with the `.vsix` attached, and publishes to the Marketplace when `VSCE_PAT` is configured. It also publishes to Open VSX when `OVSX_PAT` is configured.

The release body is taken from the matching `CHANGELOG.md` section by
`scripts/changelog-notes.js`, with GitHub's own "Full Changelog" link appended.
So add the `CHANGELOG.md` entry for the new version before you tag, or the
release notes fall back to a link to the changelog.

## Code philosophy

Write the simplest code that solves the problem. Every line should earn its
place.

- **Minimal over clever.** Keep logic flat and linear. Reduce nesting, collapse
  duplicate branches, and only extract an abstraction when reuse is real.
- **No dead code.** Remove unused imports, variables, functions, types, files,
  and commented out blocks. This project has had several cleanup passes for a
  reason.
- **No overengineering.** No abstractions or extensibility for hypothetical
  future needs. No defensive checks scattered everywhere: handle errors at the
  right boundary, not on every line.
- **Follow what is here.** Match the existing style and reuse the existing
  helpers, tokens, and patterns before adding new ones. Study a neighbouring file
  before writing a new one.
- **Never assume a library exists.** Check `package.json` first. The runtime
  dependencies are `markdown-it`, `highlight.js`, `mermaid`, and `js-yaml`,
  nothing else.
- **Readable names over comments.** Add a comment only when the code breaks a
  pattern or the data path is not obvious. Do not add or remove comments
  otherwise.

## UI work

- Match VS Code's own look. Use its theme tokens and codicons, and mirror the
  Settings editor and Copilot Chat layouts the extension is modelled on, rather
  than inventing controls.
- Handle the empty, loading, and error states, not just the happy path.
- **A command in a shared context menu carries "Vtx: " in its `title` and has
  no `category`.** Only the command palette reads `category`, every other menu
  reads `title` alone, and there is no per menu title (VS Code has rejected
  that). So a command in the editor or explorer menu is unbranded unless the
  title says Vtx, and it would read "Vtx: Vtx: ..." in the palette if it
  kept the category too. `shortTitle` is no help: it is for shorter labels, and
  the menu decides when to use it. Commands in Vtx's own surfaces (a chat tab,
  the Vtx source control group) stay unprefixed, since the surface has already
  said whose they are.

## Writing style for docs and text

These rules apply to the docs, the changelog, commit messages, PR
descriptions, code comments, and any other prose in the repo.

- Never use an em dash or en dash. Use a comma, a colon, brackets, or a full
  stop, and rewrite the sentence if needed.
- Do not hyphenate compound modifiers. Write "read only", "full width", "icon
  only", "follow up". Genuine identifiers keep their form: package names, CLI
  flags, config keys, and code symbols (`markdown-it`, `--width`,
  `permissions.mode`).
- No semicolons in prose. Use a full stop or a comma. This does not apply to
  code.
- No bare URLs in prose. Link a short descriptive phrase instead.
- Keep each paragraph and list item on one physical source line. Never insert manual line breaks to enforce a column width. Use blank lines only to separate semantic blocks. Preserve line breaks required by Markdown syntax, code blocks, tables, or renderer specific formats.

## Changelog

`CHANGELOG.md` is a list you can scan, not a write up. One entry per released
version, newest first, and a new entry for every release.

- **One line per change.** Each bullet is a single line of at most 80 columns,
  including the `- `. If it does not fit, cut words until it does. Never wrap a
  bullet onto a second line and never write a paragraph.
- **Say what changed, from the reader's side.** No cause, no mechanism, no
  before and after story, no reasoning, no measurements. The commit message is
  where to be thorough, and it is the place for all of that.
- **Highlights** is what a user notices. **Under the hood** is optional, at most
  four lines, only for what they cannot see but should know: shutdown, security,
  the protocol, test infrastructure.
- An optional one line summary may sit under the version heading, and only when
  the release has a single theme worth naming.
- Add the release link at the bottom only when the git tag actually exists.

A good bullet: `- Drag the panel by its header to move it to the other side.`
Not: three lines on what the panel used to do and why that was wrong.

## Git and commits

- This is a personal project, so commits are authored by Shayan only, with no
  bot co author trailer.
- Never commit or push unless explicitly asked.
- Keep commit messages about the "why", and follow the writing style rules above.

## Before you call it done

1. Choose checks from the files changed, and run only checks that can detect a problem introduced by those changes.
2. For documentation, badges, links, or prose comments, inspect the rendered Markdown, verify links, review wording and style, and run `git diff --check`. Do not run TypeScript checks, builds, or application tests.
3. Run `npx vsce ls --no-dependencies` only when `package.json`, `.vscodeignore`, or bundled extension contents change.
4. For TypeScript or runtime behaviour changes, run `npm run check-types` and the relevant tests. Run the full `npm test` suite when the scope can affect shared behaviour.
5. Review your own diff for dead code and style drift, then clean up temporary files or scratch scripts.
