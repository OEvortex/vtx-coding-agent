# AGENTS.md

Guidance for anyone (human or agent) working in this repository. This is also
read by the Devin CLI as the workspace instructions, so keep it accurate.

## What this project is

**Devin for VS Code** is a VS Code extension that brings the
[Devin CLI](https://docs.devin.ai/cli) into a native chat panel. Instead of
running Devin in a terminal, you chat with it in a side panel: streaming replies,
tool calls you can approve, and file edits shown as diffs you keep or undo. Each
chat is a real Devin CLI session, driven over the Agent Client Protocol (ACP),
and saved per workspace.

It is a personal, open source project published on the VS Code Marketplace under the `OEvortex` publisher. The release workflow can also publish to Open VSX when `OVSX_PAT` is configured.

## How it fits together

- **`src/`** is the extension host (TypeScript, runs in Node inside VS Code).
  - `extension.ts` activates the extension, wires up commands, and shuts every
    agent down cleanly on the way out.
  - `acp/` speaks the Agent Client Protocol to a `devin acp` process: the
    connection, the client, terminal handling, diagnostics, the throwaway agent the
    settings panel asks questions of, and the shared types.
  - `chat/` holds the `ChatManager` (one controller per chat surface: sidebar,
    editor tab, or window), the webview provider that renders each chat, and the
    transcript log each chat is replayed from.
  - `session/` stores sessions per workspace and lists them.
  - `diff/` tracks the working set of edits and exposes keep and undo.
  - `settings/` is the Devin customisations editor and the config read and write
    services that back it.
  - `cli/` locates the `devin` binary, lists models, reaps orphaned agents, and
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
  README screenshots and the guide for regenerating them.
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
| Ask a real agent what it supports | `node scripts/acp-probe.js --caps-report --no-prompt` |

To run the extension itself, `npm run watch` and then press F5 in VS Code to
launch an Extension Development Host with the extension loaded.

The two preview commands render the UI in a plain browser from mock data, with no
Devin CLI needed, which is the fastest way to iterate on the webview. The
screenshots in the README come from `npm run preview`. See
[docs/screenshots.md](docs/screenshots.md) for the scenarios and how to
regenerate them, and [CONTRIBUTING.md](CONTRIBUTING.md) for the full build and
release notes.

## The protocol Devin actually speaks

Devin's ACP surface is standard ACP plus a large set of `cognition.ai/*` extensions
that **are not documented anywhere**. The CLI ships `docs/acp/{zed,jetbrains,xcode}.mdx`
and none of them mention a single extension key. Everything below was established by
probing a real agent, so re-probe rather than trust it after a CLI upgrade:

```
node scripts/acp-probe.js --caps all --caps-report --no-prompt
node scripts/acp-probe.js --methods session/list,_cognition.ai/rules/list --no-prompt
node scripts/acp-probe.js --caps all --notify _cognition.ai/document/didOpen \
  --notify-params '{"path":"/abs/file.ts"}' -p "which files do I have open?"
```

**Capabilities are directional, and the echo is not an acknowledgement.** The
`agentCapabilities._meta` that comes back from `initialize` is the agent advertising
its *own* capabilities. It does **not** confirm the ones we declared:
`requestDiagnostics` and `subagentSupport` are never echoed, yet both take effect. So
gate a method **we call** on the echoed key (plus treat `-32601` as unsupported), and
never gate a capability **we serve** on the echo, or it silently stops working.

**Client capabilities this build looks for**, from the binary's own list: `revert`,
`subagentSupport`, `subagentControl`, `partialContent`, `groupedSessionConfigOptions`,
`stopOnReject`, `mcp`, `plugins`, `workspaceDirCommands`, `clipboardWrite`,
`windsurfConfigBridge`, `requestDiagnostics`, `documentLifecycle`, `editorContext`,
`terminalContext`, `fastContext`, `browserPreview`, `browserPreviewOpen`,
`messageGrouping`, `refTagsRaw`. Declaring one is a promise to serve it, so declare
it in the same change as its handler.

**Useful custom methods**, all verified to exist. A `sessionId` is required unless
noted, and the leading `_` is part of the wire name:

| Method | Notes |
| --- | --- |
| `session/list` | Standard ACP. Needs **no session** and **no capability**, and is **not** cwd scoped. Rows are `{sessionId, cwd, title, updatedAt}` plus `_meta["cognition.ai/isLocked"]`. `updatedAt` is ISO, while `devin list` reports epoch seconds |
| `_cognition.ai/revert/listSteps` | The authoritative step list. Never parse node ids out of an error message |
| `_cognition.ai/revert/{preview,execute,resume}` | Rewind. `preview` reports the file undo the client is expected to apply |
| `_cognition.ai/revert/forkFromStep` | `{sessionId, targetNodeId}`, **not** `stepNumber`. Returns `{forkedSessionId}`: it copies the conversation into a **new session** and leaves this one untouched, so nothing is discarded and no file is undone |
| `_cognition.ai/rules/list` | Every loaded rule with `path`, `provider`, `trigger`, `scope`. Beats scanning for `AGENTS.md`, which cannot see a plugin's rules. **Needs a sessionId** |
| `_cognition.ai/hooks/list` | Every loaded hook with `sourcePath`, `events`, `scope`, `format`. **Needs a sessionId** |
| `_cognition.ai/plugins/list` | Only exists once `plugins` is declared, and `{sessionId}` alone is not the right params |
| `_cognition.ai/session/share` | Errors with "Nothing to share yet" until the session has content |
| `_cognition.ai/command/revise` | Edit a command before approving it. `CommandReviseParams` has 3 fields, and the names are still unconfirmed, and finding them needs a probe that holds a permission request open instead of answering it |
| `_cognition.ai/reads/promptHistory` | Prompt history across sessions |
| `_cognition.ai/terminal/killBackgroundShell` | Kill a background command for real |

No MCP management method has been found: only the `mcp/serversChanged` notification.

**Node ids are a turn apart, and mixing them up is silent.** For a step,
`revertTargetNodeId` is the node BEFORE it ran (rewinding there discards it) and
`forkTargetNodeId` is the node after it finished, which is the conversation head.
So step N's fork target is step N+1's revert target. Reading the revert target
where the head is wanted rewinds every checkpoint one turn too far, and nothing
errors. With two turns the agent reports `rev=27/31` and `fork=31/34`, with the
head at 31 then 34.

**Permission requests already offer scoped grants.** One shell command comes back
with up to six options: `allow_once`, `allow_session`, `allow_always` (this
project), `allow_always_global`, `switch_bypass`, and `reject_once`. The last four
all carry `kind: "allow_always"`, so `kind` cannot tell them apart, only
`optionId`. Rendered as equal buttons, "always allow in all projects" and "switch
to bypass mode" look exactly as ordinary as "Allow", so the panel keeps the narrow
yes and the no as buttons and puts the rest behind a chevron. The request itself
carries no title: the command is in
`toolCall._meta["cognition.ai/editableCommand"]`.

**Notifications the agent sends** (all `_cognition.ai/`): `output`, `agent_stopped`,
`thinking_complete`, `compaction`, `connection_retry`, `billingInformation`,
`showModal`, `clipboard/write`, `mcp/serversChanged`, `plugins/changed`,
`revert/stepsUpdated`, `revert/historyRewound`, `browserPreview/capture`,
`browserPreview/opened`.

**Session updates that are easy to miss**: `config_option_update` (the model changed,
possibly from `/model`) and `session_info_update` (the title). Both arrive in every
session, so handle them or the pickers and tab titles go stale.

**Client to agent notifications** arrive as an `ext_notification` envelope and the
wire name keeps the `_` prefix:
`_cognition.ai/document/{didOpen,didClose,didChangeDirty,didFocus}` take
`{sessionId, uri, languageId}`. It must be a **uri**, not a path: a path is
rejected outright. A wrong shape shows up as `Failed to parse ... params` on the
agent's stderr, which is the fastest way to converge on the right one, and a
notification gets no reply so that log line is the only feedback there is.

**Diagnostics and document lifecycle are a pair.** The agent only surfaces problems
for documents it has been told are open, so declaring `requestDiagnostics` without
sending the document events gets you a pull that has nothing to attach to. Asked
what is open with neither, the agent says so plainly: "My editor client hasn't sent
me any open editor or active file context in this session."

The agent pulls on its own schedule (not per turn) by calling
`_cognition.ai/request_diagnostics`, and expects `RequestDiagnosticsResult`:
`{items: DiagnosticItem[]}` where an item is `{uri, id, message, range, severity,
source}` and a range is `{start, end}` of `{line, character}`. `uri` is **required**
("missing field `uri`"), a null reply is rejected outright ("invalid type: null"),
and ranges stay zero based because the agent renders them one based itself.

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

- **The agent's environment comes from the `devin.env` setting**, not from this
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

The tag push triggers the release workflow, which type checks, tests, builds, creates a GitHub Release with the `.vsix` attached, and publishes to the Marketplace. It also publishes to Open VSX when `OVSX_PAT` is configured.

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
  dependencies are `markdown-it`, `highlight.js`, and `mermaid`, nothing else.
- **Readable names over comments.** Add a comment only when the code breaks a
  pattern or the data path is not obvious. Do not add or remove comments
  otherwise.

## UI work

- Match VS Code's own look. Use its theme tokens and codicons, and mirror the
  Settings editor and Copilot Chat layouts the extension is modelled on, rather
  than inventing controls.
- Handle the empty, loading, and error states, not just the happy path.
- **A command in a shared context menu carries "Devin: " in its `title` and has
  no `category`.** Only the command palette reads `category`, every other menu
  reads `title` alone, and there is no per menu title (VS Code has rejected
  that). So a command in the editor or explorer menu is unbranded unless the
  title says Devin, and it would read "Devin: Devin: ..." in the palette if it
  kept the category too. `shortTitle` is no help: it is for shorter labels, and
  the menu decides when to use it. Commands in Devin's own surfaces (a chat tab,
  the Devin source control group) stay unprefixed, since the surface has already
  said whose they are.

## Writing style for docs and text

These rules apply to the README, the changelog, commit messages, PR
descriptions, code comments, and any other prose in the repo.

- Never use an em dash or en dash. Use a comma, a colon, brackets, or a full
  stop, and rewrite the sentence if needed.
- Do not hyphenate compound modifiers. Write "read only", "full width", "icon
  only", "follow up". Genuine identifiers keep their form: package names, CLI
  flags, config keys, and code symbols (`markdown-it`, `--width`,
  `read_config_from`).
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
  Devin or bot co author trailer.
- Never commit or push unless explicitly asked.
- Keep commit messages about the "why", and follow the writing style rules above.

## Before you call it done

1. Choose checks from the files changed, and run only checks that can detect a problem introduced by those changes.
2. For documentation, badges, links, or prose comments, inspect the rendered Markdown, verify links, review wording and style, and run `git diff --check`. Do not run TypeScript checks, builds, or application tests.
3. Run `npx vsce ls --no-dependencies` only when `package.json`, `.vscodeignore`, or bundled extension contents change.
4. For TypeScript or runtime behaviour changes, run `npm run check-types` and the relevant tests. Run the full `npm test` suite when the scope can affect shared behaviour.
5. Review your own diff for dead code and style drift, then clean up temporary files or scratch scripts.
