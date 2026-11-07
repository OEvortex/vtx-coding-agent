import * as vscode from "vscode";
import { TerminalExitStatus, TerminalRun, TerminalRunner } from "./terminal";

// Runs the agent's commands in a real VS Code terminal, the way Copilot's own
// terminal tool does, rather than in a hidden child process: the same shell the
// user has, with their profile, their environment and their history, and a
// window they can open and take over.
//
// It only works through shell integration, which is what tells us where a
// command started, what it printed, and what it exited with
// (window.onDidStartTerminalShellExecution and friends, finalised in 1.93).
// Shell integration is not always there: an unsupported shell, a profile that
// blocks the injection, or it simply has not activated yet. Everything here is
// therefore allowed to answer "not me", and the caller runs the command itself.

// How long a fresh terminal is given to report shell integration. VS Code waits
// the same 5s when it is injecting it (terminalEnvironment.ts).
const INTEGRATION_MS = 5000;

// A pager turns `git diff` into a command that never returns, and the agent has
// no way to press q.
const PAGERS = { GIT_PAGER: "cat", PAGER: "cat" };

export class VsCodeTerminalRunner implements TerminalRunner {
  // Terminals waiting for their next command, and every one we have opened. A
  // terminal is only reused once the command it was running has ended, which is
  // what VS Code does (runInTerminalTool reuses the session's terminal unless it
  // has been left running). Two commands at once would otherwise be typed into
  // the same shell, one on top of the other.
  private readonly free: vscode.Terminal[] = [];
  private readonly opened: vscode.Terminal[] = [];
  private readonly ready = new WeakMap<vscode.Terminal, Promise<boolean>>();
  // Set once a terminal has come up without shell integration: waiting the full
  // 5s again for every command after that is just a slow way to fail.
  private unsupported = false;

  constructor(
    private readonly cwd: string,
    // What `vtx.env` and the sandbox setting add, and nothing else: the shell
    // this terminal opens brings its own environment. Without it a command the
    // agent ran here missed the API key or the PATH entry the same command got
    // when it ran as a child process, which is the fallback path, so the two
    // disagreed with nothing to say why.
    private readonly env: Record<string, string>,
    private readonly log?: (line: string) => void
  ) {}

  // Whether the user asked for this at all. Read per command so turning it off
  // takes effect on the next one rather than the next window.
  private enabled(): boolean {
    return vscode.workspace.getConfiguration("vtx").get<boolean>("useIntegratedTerminal", true);
  }

  async run(command: string, cwd: string | undefined, onData: (text: string) => void): Promise<TerminalRun | undefined> {
    // A command with its own working directory is not this terminal's command:
    // the session terminal keeps its own cwd across calls, as VS Code's does.
    if (!this.enabled() || this.unsupported || (cwd && cwd !== this.cwd)) {
      return undefined;
    }
    // Shell integration reports one command per line, so a script fed in as a
    // single string never reports its end (microsoft/vscode#250764).
    if (/[\r\n]/.test(command)) {
      return undefined;
    }
    const terminal = await this.terminalWithIntegration();
    const integration = terminal?.shellIntegration;
    if (!terminal || !integration) {
      this.unsupported = !!terminal;
      this.log?.("[terminal] no shell integration, running the command directly instead");
      return undefined;
    }

    const execution = integration.executeCommand(command);
    // read() only yields what arrives after the first call, so it has to be the
    // very next thing that happens.
    const stream = execution.read();
    const clean = new OutputCleaner(onData);
    let settle: (status: TerminalExitStatus) => void = () => {};
    const exit = new Promise<TerminalExitStatus>((resolve) => {
      settle = resolve;
    });
    let done = false;
    const finish = (status: TerminalExitStatus) => {
      if (done) {
        return;
      }
      done = true;
      clean.flush();
      listeners.forEach((l) => l.dispose());
      // Its terminal is free for the next command, unless it has gone.
      if (!terminal.exitStatus && this.opened.includes(terminal)) {
        this.free.push(terminal);
      }
      settle(status);
    };
    const listeners = [
      vscode.window.onDidEndTerminalShellExecution((e) => {
        if (e.execution !== execution) {
          return;
        }
        // The reader is a loop of its own, and the end event can land while chunks
        // are still queued in it. Finishing here flushed a half line and left the
        // rest to arrive afterwards, so the output the agent read at
        // wait_for_exit could be missing the last thing the command said. Capped,
        // because a stream that never ends must not hold the turn.
        const status = { exitCode: e.exitCode ?? null, signal: null };
        void Promise.race([drained, wait(250)]).then(() => finish(status));
      }),
      // The terminal being closed under a running command is that command's end,
      // and without this the agent waits on it for ever.
      vscode.window.onDidCloseTerminal((t) => {
        if (t === terminal) {
          this.forget(terminal);
          finish({ exitCode: null, signal: "SIGHUP" });
        }
      })
    ];
    const drained = (async () => {
      try {
        for await (const chunk of stream) {
          clean.write(chunk);
        }
      } catch {
        // The stream ends with the terminal; the exit is settled either way.
      }
    })();

    return {
      exit,
      show: () => terminal.show(false),
      // Ctrl+C into the real terminal, which is what a user would press. The
      // terminal itself stays, since it is the session's.
      kill: () => terminal.sendText("\u0003", false)
    };
  }

  private async terminalWithIntegration(): Promise<vscode.Terminal | undefined> {
    let terminal = this.free.pop();
    while (terminal && terminal.exitStatus) {
      terminal = this.free.pop();
    }
    if (!terminal) {
      terminal = vscode.window.createTerminal({
        name: "Vtx",
        iconPath: new vscode.ThemeIcon("sparkle"),
        cwd: this.cwd,
        // The pagers last: a pager set here is one the agent cannot get out of.
        env: { ...this.env, ...PAGERS },
        // Out of the way until it is wanted: the chat shows the output, and the
        // Show Terminal action is what brings this one up.
        hideFromUser: true,
        isTransient: true
      });
      this.opened.push(terminal);
    }
    if (terminal.shellIntegration) {
      return terminal;
    }
    const ready = this.ready.get(terminal) || waitForIntegration(terminal);
    this.ready.set(terminal, ready);
    await ready;
    return terminal;
  }

  private forget(terminal: vscode.Terminal): void {
    for (const list of [this.free, this.opened]) {
      const i = list.indexOf(terminal);
      if (i !== -1) {
        list.splice(i, 1);
      }
    }
  }

  dispose(): void {
    this.opened.splice(0).forEach((t) => t.dispose());
    this.free.length = 0;
  }
}

function waitForIntegration(terminal: vscode.Terminal): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      sub.dispose();
      resolve(false);
    }, INTEGRATION_MS);
    const sub = vscode.window.onDidChangeTerminalShellIntegration((e) => {
      if (e.terminal === terminal) {
        clearTimeout(timer);
        sub.dispose();
        resolve(true);
      }
    });
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    t.unref?.();
  });
}

// A terminal writes for a screen, not for a transcript: escape sequences that
// colour and move the cursor, and carriage returns that redraw a line in place
// (every progress bar). Reading the stream rather than the screen means undoing
// that here, line by line, so a download that redrew itself two thousand times
// is the one line it ended on.
export class OutputCleaner {
  private pending = "";
  // The tail of the raw stream that may still be half of an escape sequence.
  private held = "";

  constructor(private readonly emit: (text: string) => void) {}

  write(chunk: string): void {
    const raw = this.held + chunk;
    // A sequence split across two chunks matches nothing, and the half that is
    // left is then deleted as a stray control character, so "\x1b[" + "0mdone"
    // reached the transcript as "[0mdone". Anything that could still be the start
    // of one waits for the rest of it.
    const end = safeEnd(raw);
    this.held = raw.slice(end);
    this.pending += stripAnsi(raw.slice(0, end)).replace(/\r\n/g, "\n");
    const lines = this.pending.split("\n");
    this.pending = lines.pop() ?? "";
    if (lines.length) {
      this.emit(lines.map(lastDraw).join("\n") + "\n");
    }
  }

  // Whatever is left when the command ends, which is a prompt line with no
  // newline after it more often than not.
  flush(): void {
    if (this.held) {
      // Nothing more is coming, so whatever was being held is all there is of it.
      this.pending += stripAnsi(this.held).replace(/\r\n/g, "\n");
      this.held = "";
    }
    const left = lastDraw(this.pending);
    this.pending = "";
    if (left) {
      this.emit(left + "\n");
    }
  }
}

// What a line looked like after the last time it was drawn.
function lastDraw(line: string): string {
  if (line.indexOf("\r") === -1) {
    return line;
  }
  const draws = line.split("\r").filter((d) => d.length);
  return draws.length ? draws[draws.length - 1] : "";
}

// VS Code's own removeAnsiEscapeCodes (base/common/strings.ts), which is the
// list of sequences a shell really emits.
const CSI = /(?:\x1b\[|\x9b)[=?>!]?[\d;:]*["$#'* ]?[a-zA-Z@^`{}|~]/;
const OSC = /(?:\x1b\]|\x9d).*?(?:\x1b\\|\x07|\x9c)/;
const ESC = /\x1b(?:[ #%()*+\-./]?[a-zA-Z0-9|}~@])/;
const CONTROL = new RegExp(`(?:${CSI.source}|${OSC.source}|${ESC.source})`, "g");

// How much of `text` can be stripped now. An escape sequence still arriving is
// held back, unless it has grown past anything a sequence could be, which stops
// an unterminated OSC holding the output for ever.
const MAX_PARTIAL = 128;
const STARTS_WITH_SEQUENCE = new RegExp(`^(?:${CSI.source}|${OSC.source}|${ESC.source})`);

function safeEnd(text: string): number {
  const at = Math.max(text.lastIndexOf("\x1b"), text.lastIndexOf("\x9b"), text.lastIndexOf("\x9d"));
  if (at === -1 || text.length - at > MAX_PARTIAL) {
    return text.length;
  }
  return STARTS_WITH_SEQUENCE.test(text.slice(at)) ? text.length : at;
}

export function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(CONTROL, "").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");
}
