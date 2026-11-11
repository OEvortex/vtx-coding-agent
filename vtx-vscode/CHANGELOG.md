# Changelog

All notable changes to **Vtx for VS Code**, newest first. One line per change:
the commit messages carry the detail.

> This is a fast moving preview, so a few entries group several rapid builds
> under one milestone version where those builds were only ever installed
> locally. The builds between 0.6.65 and 0.6.91 reached the Marketplace together
> in 0.6.92.

## [0.13.3] - 2026-11-11

### Highlights
- Restyle the panel against the GitHub Copilot Chat UI: denser chrome, quieter
  tool rows, a request bubble that is a fill rather than an outline, and code
  blocks that read as embedded editors.
- Composer controls share one 22px tier, with a filled circular Send and pickers
  that compact to icon-only squares instead of disappearing.
- The context meter is now Copilot's 14px ring, amber past 75% and red past 90%,
  with the percentage revealed on hover.
- Sessions list gains tree semantics, roving arrow-key focus, and hover actions
  that keyboard users can reach.
- The empty state pins its suggested prompts above the composer and focuses the
  input when clicked.
- Code blocks carry a language pill in their floating chrome, and a session
  waiting on your answer gets a tinted row with an attention bar.
- A filtered session list that comes up empty offers a focusable "Reset filter"
  instead of a dead end.

### Under the hood
- Row, tool, composer and token metrics taken from microsoft/vscode's
  `contrib/chat` CSS at 8b38655; per-turn rows carry their own horizontal
  padding, the transcript uses `chat-list-background`, and the header sits in
  the same 950px column as the messages.
- Tool rows shimmer while running (spinner hidden), turn red on failure, use
  tabular numerals, and admit "taking longer than usual" after 90 seconds.
- Elapsed labels round to whole seconds, matching Copilot's formatting.

## [0.13.2] - 2026-11-11

### Highlights
- Give the agent the workspace tools it was missing: sessions opened over ACP
  now register `read`, `edit`, `write`, `bash`, `skill`, and `todo`, so the
  assistant can actually inspect and change your files instead of only talking.
- Honour your plugin profile in editor sessions, including extension-provided
  tools and any tool a profile adds or removes.

### Under the hood
- The ACP server mounts the plugin composition before serving and assembles the
  session tool list from `ctx.tools`; the fix ships in the Vtx CLI (`vtx acp`).

## [0.13.1] - 2026-11-11

### Highlights
- Show the real context-window meter instead of a pinned 100%: the agent now
  reports what the conversation occupies against the model's window, so the
  reading and its popup agree with compaction.
- Keep that reading correct after a compaction, after resuming a session, and
  after switching models.

### Under the hood
- The ACP client already accepted the context payload; the fix ships in the
  Vtx CLI (`vtx acp`), and this build is the matching extension release.

## [0.13.0] - 2026-10-01

### Highlights
- Configure Fusion lead models, effort, and sidekicks in one picker.
- Keep each model family's effort choices separate and accurate.
- Switch models and effort quickly without stale selections or flicker.
- Configure Fusion while a session is still loading.
- Show the agent's reason when a model configuration update fails.

### Under the hood
- Translate catalogue model variants to ACP accepted model values.
- Keep design previews out of Marketplace packages.

## [0.12.7] - 2026-09-03

### Highlights
- Point editor chat return controls to the right side panel.
- Show editor chat activity correctly in the session list.

## [0.12.6] - 2026-09-03

### Highlights
- Restore chats and visible session lists without avoidable startup work.
- Keep messages, queued attachments, and staged files with the right chat.
- Keep session handovers, terminal output, and editor context correct.
- Make Keep, Undo, and file links reliable across concurrent chats.
- Show model pricing, context limits, status badges, and model pins.
- Make the integrated terminal opt in and simplify Bypass command approval.
- Keep model and changed file controls readable at narrow widths.

### Under the hood
- Bound retained session, terminal, replay, and diff resources.
- Share session lists across ACP connections and bound CLI fallback time.
- Add timing summaries for health checks, loads, wakes, and session lists.
- Cut test and CI runtime while keeping all platform checks.

## [0.12.5] - 2026-09-02

### Highlights
- Balance padding in model hover cards without configuration controls.

## [0.12.4] - 2026-09-02

### Highlights
- Connect model context limits to their values with dotted lines.

## [0.12.3] - 2026-09-02

### Highlights
- Make model hover price details compact with connected cost lines.
- Show current CLI price formats in model hover cards.

## [0.12.2] - 2026-08-17

### Highlights
- Keep the changed files actions readable at narrow widths.
- Show the thinking control in compact mode with a thought icon.
- Show model cost and status details when the CLI reports them.
- Pin available models and hide pins that are unavailable.
- Keep queued message attachments with their own messages.

## [0.12.1] - 2026-08-17

### Highlights
- Make the integrated terminal opt in by default.
- Let Bypass approve vtx command requests without an extra setting.
- Remove the redundant auto approve command setting.

## [0.12.0] - 2026-08-17

### Highlights
- Add an opt in setting to auto approve shell commands.
- Rename Bypass Permissions to Bypass in the mode picker.

## [0.11.12] - 2026-08-17

### Highlights
- Keep All accepts files from this chat when another chat touched them.
- File links wrapped in code formatting still open as file pills.
- Undo All continues to protect files changed by another chat.

## [0.11.11] - 2026-08-16

### Highlights
- File references in replies render as clickable links and open the referenced file.
- External links remain external instead of opening as workspace files.
- Idle chats no longer start a turn from editor diagnostics.

## [0.11.10]

Chats stop borrowing each other's things.

### Highlights
- A message carries the files staged with it, once, and only its own.
- Another chat's staged files are never sent, and never deleted for you.
- A chat left part way through opening comes back whole, not half drawn.
- A message written while a chat wakes waits its turn instead of racing.
- Queued messages survive closing the tab and an agent dying unasked.
- A chat starting in the background leaves your mode and model alone.
- Undo all in one chat leaves a file another chat changed, and says so.
- Keep or undo on a file that has since been deleted just clears it.
- An edit row says a change was undone only once it really has been.
- A thought stays where you scrolled it while the agent keeps working.
- An edited request is sent once, however many times Enter is pressed.
- Restore Checkpoint refuses a rewind from a control drawn for another state.
- A chat with a command still running is no longer treated as idle.
- A finished command keeps its output in the transcript.
- Switching chats closes an edit left open in the one you left.
- The settings panel shows a config value whatever shape it is in.
- A corrected path to the CLI takes effect without reopening the panel.
- A config file that is a symlink stays one, and keeps its permissions.
- A comma inside a config value survives every later save.
- One oversized file no longer costs you every other pending undo.

### Under the hood
- Every protocol call but a turn is bounded, so nothing waits for ever.
- The agent behind the settings panel is stopped on the way out.
- The chat controller runs under test, against a real agent.
- Staged files are held per chat rather than per composer.

## [0.11.9] - 2026-08-14

A pass over every way a chat could lose your work.

### Highlights
- Restoring a checkpoint no longer abandons the edits it cannot undo.
- An undo that fails says which file and why, and stays undoable.
- Keep and Undo on an edit row name the file they act on.
- Only the newest change to a file offers to keep or undo it.
- A settings toggle no longer replaces a config file it could not parse.
- A file that is not UTF-8 is left alone instead of being mangled.
- A byte order mark survives an edit.
- Messages queued behind a turn come back as a draft, not lost.
- A prompt left half written comes back, and stays there.
- A new chat no longer offers the previous chat's message.
- Opening a slow chat no longer takes the panel from the one you picked.
- Retrying a chat that failed to open no longer strands its agent.
- A chat that never answers gives up instead of breaking New chat.
- Enter while an IME is composing no longer sends the message.
- A late file suggestion no longer eats the Enter that sends.
- Stop now stops a command that was still starting.
- Continue in Background now keeps the command running.
- Terminating a chat stops a command that ignores being asked.
- One chat's failed MCP server is no longer blamed on another.
- The plugin list no longer turns an error message into a plugin.
- Log in (OAuth) stops spinning once it has opened its terminal.
- With two folders open, loaded rules and hooks match the one selected.
- A noisy command no longer stalls the panel, and shows its latest output.
- A turn starting no longer takes the keyboard away from a control.

### Under the hood
- Protocol calls are bounded, so a silent agent cannot wedge a chat.
- Every agent is stopped on the way out, including one still starting.
- Config and working set writes are atomic, so a crash cannot truncate one.
- A name from a config file is never handed to a shell unquoted.

## [0.11.8] - 2026-08-14

vtx can see what the editor sees.

### Highlights
- vtx reads the errors and warnings the editor is already showing.
- It knows which files you have open, focused, and have edited but not saved.
- Right click in the editor for Explain This and Fix Problems Here.
- Right click a file in the editor or the explorer to add it to the chat.
- Typing `@` now finds symbols too, not just file names.
- Fork a turn into a new chat, leaving the original untouched.
- A permission prompt keeps every broader grant behind a chevron.
- What you allowed or rejected stays in the transcript.
- The mode picker offers every mode your CLI has, including Smart.
- Instructions and Hooks list what is really loaded, plugins included.
- Copy a link to a chat from its header.
- A model that cannot read images no longer receives one.
- A link vtx asks you to open is a link, and Open opens it.
- Screen readers are told when a turn starts, ends, or needs you.
- New setting: `vtx.sandbox` runs the agent's commands sandboxed.

### Under the hood
- Checkpoints use the agent's own step list instead of parsing an error string.
- Sessions are listed over the protocol while an agent is running.
- The MCP server churn no longer floods the output channel.
- `scripts/acp-probe.js` can report capabilities and probe methods.

## [0.11.7] - 2026-08-13

### Highlights
- The word Plan no longer prints over the count beside it.
- A long step is cut short with an ellipsis while the plan is folded.
- Opening the plan shows the step it is on in full, over several lines.

## [0.11.6] - 2026-08-13

### Highlights
- An SVG pasted or dropped in attaches as its source, not as an image.
- A HEIC or AVIF attaches as a file instead of breaking the chat for good.
- A TIFF or ICO attaches inline as an image.

## [0.11.5] - 2026-08-10

### Highlights
- Cmd+1 to Cmd+9 switch chats while the chat has the keyboard.
- They act on the chat you are in, not always the side panel's.
- A queued message shows what is attached to it, as a row of pills.
- Editing one shows the same in the composer, and the row keeps saying so.
- A long file name no longer takes a whole row of pills to itself.
- A diagram vtx gets wrong stays a code block, and stays in the chat.
- Send Immediately on a queued message ends the current turn and sends it.

### Under the hood
- The published package no longer carries a stray screenshot.

## [0.11.4] - 2026-08-10

### Highlights
- A run's line keeps one pixel further clear of each icon, not two.

## [0.11.3] - 2026-08-10

### Highlights
- A one paragraph thought no longer breaks the run's line around it.

## [0.11.2] - 2026-08-10

### Highlights
- A run's line and elbow keep further clear of each icon.

## [0.11.1] - 2026-08-10

### Highlights
- A skill vtx invoked is a pill on one line, not a section to open.
- Click it to open the skill's own SKILL.md.

## [0.11.0] - 2026-08-10

### Highlights
- A running command offers Continue in Background and Focus Terminal.
- Both sit at the end of its row, as icons, where VS Code puts them.
- The row says "in background" once the agent has moved on from it.
- Two commands at once no longer land in the same shell.

## [0.10.6] - 2026-08-10

### Highlights
- Every step of a thought in a run is on the chain, not just the first.

## [0.10.5] - 2026-08-10

### Highlights
- Reasoning that turned out to be blank leaves no gap in a run.

## [0.10.4] - 2026-08-10

### Highlights
- A reloaded run shows the reasoning in it, instead of a line to nothing.

## [0.10.3] - 2026-08-10

### Highlights
- The chain runs through the middle of every glyph, not past the side of it.

## [0.10.2] - 2026-08-10

### Highlights
- A subagent's line stops clear of its icons instead of running into them.
- A thought's dot is the larger one, so it reads as a node, not a speck.

## [0.10.1] - 2026-08-10

### Highlights
- A thought in a run is a row like any other: same column, same spacing.

## [0.10.0] - 2026-08-10

Commands you can watch, and a panel that keeps saying what it is doing.

### Highlights
- Commands run in a real terminal you can open, watch and take over.
- Show Terminal opens it, Skip leaves it running and lets vtx move on.
- The gap between one action and the next says the agent is still working.
- When something is waiting on you, it says that instead.
- A delegated task's header follows its work, not the last tool it ran.
- An edit row opens what that edit did, the tray still opens the whole file.
- A queued message keeps what you attached to it, and shows it.
- Editing a queued one hands its attachments back to the composer.
- The MCP warning can be turned off, for this window or for good.
- Cmd+1 to Cmd+9 open your nine most recent chats from the panel.
- Each of those chats wears the shortcut that opens it in the list.
- Cmd+K Cmd+D opens the session list, Ctrl+K Ctrl+D on Windows and Linux.
- It is in the command palette as "vtx: Session List".
- Folded work keeps its spacing when you open it, instead of cramming.
- A run's line joins its first step instead of stopping short of it.
- Every step's line breaks around its own glyph, by the same margin.
- Windows: the CLI is found and run even when npm installed it as a shim.
- Windows: sessions, locks and take over read the right data directory.
- Windows: a dropped file attaches, and its diff and Undo find it.
- A file's own line endings survive an edit, so git sees the real change.
- Linux: an agent stranded by a crash is cleaned up on a systemd desktop.

### Under the hood
- VS Code 1.93 or newer, for the terminal shell integration API.
- The tests and the type check now run on Windows and macOS too.

## [0.9.3] - 2026-08-10

Saying what the agent is doing, and folding it away once it is done.

### Highlights
- A finished turn folds its work behind one line, leaving the answer out.
- While vtx works the transcript says so, in a word that shimmers.
- No spinner beside it: one signal, not two saying the same thing.
- Reduced motion keeps the word readable instead of washing it out.
- Opening a new file's diff no longer fails with a model error.

## [0.9.2] - 2026-08-10

Nothing you typed or were asked goes missing.

### Highlights
- A message that cannot be sent goes back in the box, and says why.
- Stopping a turn takes its open question with it.

## [0.9.1] - 2026-08-10

Sections that fold themselves, and stay folded when you come back.

### Highlights
- A run folds to its summary once it is over, and a finished thought with it.
- A reloaded session comes back folded, not laid out end to end.
- A folded section is out of reach of Tab and of find in page.
- A section is not folded away while you are reading inside it.
- A run or a thought you opened by hand stays open when it ends.
- More of the reasoning stays in view while it is still streaming.
- A run's chevron appears on hover, like every other one.

## [0.9.0] - 2026-08-08

A chat you can move, and a transcript that reads like VS Code's own.

### Highlights
- Move a chat between the side panel and an editor tab, live agent and all.
- A detached chat is only that chat, named after it, and survives a reload.
- What a chat holds follows it: draft, answers, attachments, plan and edits.
- The session list keeps itself up to date, so the Refresh button is gone.
- Type while vtx works: queue the message, or stop and send it now.
- A run of tools says what it did, chained like a chain of thought.
- Reasoning inside a run reads as text on that chain, with nothing to open.
- A command titles its own row and shows its output as it is produced.
- Changes waiting to be reviewed survive a window reload, counts included.
- Keep all and Undo all mark every edit in the transcript, not just one.
- A sent message keeps what was attached to it, a picture as its thumbnail.
- An MCP server that will not start is named in the chat, and can be dismissed.
- Settings lists Windsurf's MCP servers, and manages them in place.
- A finished turn says how long it took, with what it cost on hover.
- The sessions panel docks either side, and drags by its own header.
- A permission prompt shows the command it is asking you to allow.

### Under the hood
- A moved chat hands over its live runtime: no process, lock or turn restarts.
- Every surface is told when another starts, moves, stops or renames a chat.
- Drafts, attachments and the working set outlive the agent, in storage.
- vtx's output and turn stats notifications are read, not just logged.

## [0.8.0] - 2026-08-06

vtx's subagents, rendered in the panel.

### Highlights
- A subagent gets its own collapsible block: brief, everything it did, report.
- A subagent's tool calls no longer sit in the transcript stuck at pending.
- A running subagent can be moved between foreground and background.
- A file or folder dropped from outside VS Code now says where it came from.

### Under the hood
- Subagent streaming needs `cognition.ai/subagentSupport` to be advertised.
- An OS drag carries no filesystem path into a webview, hence the listing.

## [0.7.1] - 2026-08-06

### Highlights
- Dropping files onto the chat works again.
- The whole panel takes a drop, the sessions panel included.
- A drag started inside VS Code needs Shift held, which is VS Code's gesture.
- A folder dropped from outside attaches as a listing of what it holds.
- Escape stops the turn from the composer, not only Ctrl/Cmd+Esc.
- New Session is a split button: the label acts, the chevron picks where.

### Under the hood
- VS Code takes drag events off a webview, so they are claimed on the window.

## [0.7.0] - 2026-08-05

Chat quality: a message queue, background sessions, and calmer reading.

### Highlights
- Type while vtx works: messages queue, to edit, send now or drop.
- Drag files, images and folders onto the chat to attach them as context.
- The + button offers folders, and an image is attached as a real image.
- Copilot keyboard shortcuts for stop, the mode and model pickers, and recall.
- A session you leave keeps working, and replays its progress when you return.
- A background session needing input notifies you and pulses in the list.
- Questions get real checkboxes, a gated Submit, and text that follows the step.
- A large session no longer scrolls and reflows in front of you as it loads.
- Expanding a tool card or a thought no longer yanks you to the bottom.
- A plan step the agent skips shows struck through.
- Returning to the session list is instant, from cache, revalidated behind you.
- Loading shows as a bar along the top edge instead of a spinner.
- The list filter takes several states, and sorts by activity, state or name.
- An unsent draft stays with the session it was typed in.
- No carried over Stop button, frozen "Working…" line, or twice opened links.

### Under the hood
- A backgrounded session's stream is buffered on its runtime and replayed later.
- The queue lives on the runtime, so it survives every way of leaving a session.

## [0.6.93] - 2026-08-04

### Highlights
- Settings tags no longer break one letter per line in a narrow panel.

### Under the hood
- Every README screenshot is captured at one size, via a `--width` flag.

## [0.6.92] - 2026-08-04

### Highlights
- Every settings action shows that it is working and cannot fire twice.
- A disabled MCP server reads as switched off, struck through and dimmed.

### Under the hood
- The extension replies to every write, so a control cannot spin forever.

## [0.6.91] - 2026-08-04

### Highlights
- Icon only buttons carry a tooltip, on hover and on keyboard focus.
- A typed value sits under its label at full width, like VS Code's own editor.
- The add and install forms are a single full width column.
- Placeholders are useful examples again, with hints on the trickier fields.

### Under the hood
- Moving between settings sections re-reads the config, so no view is stale.

## [0.6.90] - 2026-08-04

### Highlights
- Every settings dropdown matches VS Code's own, keyboard handling included.
- Sandbox network mode is Full or Limited, with the detail in the row's hint.

## [0.6.89] - 2026-08-04

### Highlights
- The working set above the composer collapses, and scrolls once it grows tall.
- A turn cut short by a window reload says so, and offers "Send it again".

### Under the hood
- Shutdown waits for each agent to exit, so no orphan keeps the session lock.
- On Windows the whole agent process tree is killed, MCP servers included.

## [0.6.88] - 2026-08-04

A third of the vertical space, with nothing removed.

### Highlights
- Pick the scope once at the top instead of every setting repeating per scope.
- The **User** scope is now **Global**, since it applies to every workspace.
- Ten sections became eight, with small headings instead of bordered cards.
- A search box filters settings across every section at once.
- Each row says if the scope sets or inherits the value, with Clear override.
- A VS Code setting that overrides the CLI's model now says so, with links.
- The panel refreshes itself when the config changes, so Refresh is gone.

### Under the hood
- Setting a value back to the one that applies removes the key, as VS Code does.
- Import rules from other tools is scope aware instead of always writing Global.

## [0.6.87] - 2026-08-04

### Highlights
- Create flows for a skill, an MCP server, a hook and a plugin.
- A Plugins section, with install from a Git source or a local path.
- MCP sign in and out, following the real sign in state.
- Edit and remove on skills, instructions, hooks and MCP servers.
- Reset to defaults, per section and per scope.
- Multi root support: every project aware section groups by scope.

### Under the hood
- MCP servers are read from the right files, and never show secrets.
- Leaving a session mid turn and returning re-attaches instead of reloading.

## [0.6.65] - 2026-07-31

Three features carried over from GitHub Copilot Chat.

### Highlights
- New chat locations: the sidebar, an editor, a window, or a CLI terminal.
- A sessions panel beside the chat, with its own toolbar, search and filter.
- A settings editor for the whole vtx CLI configuration.

### Under the hood
- The chat provider became one controller per surface under a `ChatManager`.

## [0.6.64] - 2026-07-31

### Highlights
- Composer attachments no longer carry over into the wrong chat.
- A background session can queue more than one pending message.

### Under the hood
- Opening a session twice in quick succession no longer spawns a second agent.
- Prompts are cancelled on every teardown path, closing several reload leaks.
- A relative path from the agent resolves against the session's directory.

## [0.6.63] - 2026-07-30

### Under the hood
- Rewrote the README around the vtx CLI, with corrected links.

## [0.6.62] - 2026-07-30

### Under the hood
- Trimmed the README to end user content, build notes to `CONTRIBUTING.md`.
- Fixed the Marketplace badge and dropped a flaky install count.

## [0.6.61] - 2026-07-30

First public release on the VS Code Marketplace and Open VSX.

### Highlights
- Richer tool call cards, grouped tool runs, and Mermaid diagrams in replies.
- A closer match to VS Code and Copilot chat in sessions, editing, scrolling.

### Under the hood
- Release automation: a pushed tag builds, releases and publishes the extension.

## 0.6.38 - 2026-07-30

### Highlights
- Images in replies and tool results, inline keep and undo, clickable fetches.

## 0.6.37 - 2026-07-30

### Highlights
- A terminate control, Copilot send and stop icons, tidier terminal cards.

## 0.6.36 - 2026-07-30

### Highlights
- Transcripts are retained, so returning to an idle session is instant.

## 0.6.35 - 2026-07-30

### Highlights
- One agent process per session, with a live status dot in the list.

## 0.6.34 - 2026-07-30

### Highlights
- Retry regenerates in place, reloads are cleaner, and startup has a loader.

## 0.6.33 - 2026-07-30

### Under the hood
- A round of review fixes across the composer and the session lifecycle.

## 0.6.30 to 0.6.31 - 2026-07-30

### Highlights
- Tool cards render a body suited to the tool instead of raw argument JSON.

### Under the hood
- Agent commands run through a shell, so `cd`, `&&`, pipes and globs behave.

## 0.6.23 to 0.6.29 - 2026-07-30

A second pass at matching VS Code's Copilot Chat.

### Highlights
- Entrance animations for streamed parts, and a polished composer.
- A docked plan, a working set summary, and a live peek at the reasoning.

## 0.6.8 to 0.6.22 - 2026-07-29

A first pass at matching VS Code's Copilot Chat.

### Highlights
- Interactive questions from the agent, with a carousel and answered recaps.
- Collapsible thinking with "Thought for Xs", and expandable tool call cards.
- A welcome screen, message actions, context usage, and a to do style plan.
- A hover toolbar on code blocks: copy, insert, apply, run.

## 0.6.0 to 0.6.7 - 2026-07-28 to 2026-07-29

The foundation of the extension.

### Highlights
- A vtx chat panel for VS Code with streaming replies, backed by ACP.
- Workspace scoped sessions, vtx CLI detection, and a first run setup.
- A diff working set with keep and undo per file, and context attachments.
- A model picker from the CLI, grouped by family, usable before a session.
- Live terminal output, edit in place, checkpoints and undo.
- A status bar popover with the account, plan, version and links.
- Rich markdown rendering, with `markdown-it` and `highlight.js`.

### Under the hood
- The agent's whole process group is killed on dispose, so no MCP server leaks.
- A jsdom webview test harness, and a browser preview for visual iteration.

[0.11.9]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.11.9
[0.11.8]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.11.8
[0.9.3]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.9.3
[0.9.2]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.9.2
[0.9.1]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.9.1
[0.9.0]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.9.0
[0.8.0]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.8.0
[0.7.1]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.7.1
[0.7.0]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.7.0
[0.6.93]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.6.93
[0.6.92]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.6.92
[0.6.63]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.6.63
[0.6.62]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.6.62
[0.6.61]: https://github.com/OEvortex/vtx-coding-agent/releases/tag/v0.6.61
