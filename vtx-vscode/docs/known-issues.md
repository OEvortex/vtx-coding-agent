# Known UI issues

## Integrated terminal adds a second approval layer

**Area:** vtx command execution with `vtx.useIntegratedTerminal` enabled.

**Observed:** vtx CLI can allow a command under its own permission rules, while VS Code terminal approval can still show a second prompt for the same command. The extra layer is controlled by VS Code settings such as `chat.tools.terminal.autoApprove` and `chat.tools.terminal.ignoreDefaultAutoApproveRules`.

**Expected:** vtx's ACP permission decision should be the only approval layer for commands launched by this extension, while the integrated terminal remains available for watching and taking over commands.

**Current behavior:** The extension sends integrated commands through `TerminalShellIntegration.executeCommand()`. VS Code does not expose a public per extension switch that disables its terminal approval layer for these commands.

**Workaround:** Set `vtx.useIntegratedTerminal` to `false`. vtx then runs commands through its child process path, so its own permission flow controls the command. Set it to `true` when the visible integrated terminal is required.

**Status:** Open. A complete fix needs a supported VS Code agent host integration or a different terminal execution design.
