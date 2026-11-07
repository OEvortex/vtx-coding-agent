import { spawn, execFile, ChildProcess } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { StringDecoder } from "string_decoder";

// How often live output is handed to the panel. Fast enough to read as live, slow
// enough that a build's worth of chunks is a few dozen posts rather than hundreds
// of full buffer copies.
const OUTPUT_POST_MS = 80;
// What a finished, released terminal keeps. Enough that its row can still be
// asked for its output, and not the whole megabyte it was allowed while running.
const RELEASED_OUTPUT_BYTES = 64 * 1024;

export interface EnvVariable {
  name: string;
  value: string;
}

export interface CreateTerminalParams {
  sessionId: string;
  command: string;
  args?: string[];
  env?: EnvVariable[];
  cwd?: string;
  outputByteLimit?: number;
}

export interface TerminalExitStatus {
  exitCode: number | null;
  signal: string | null;
}

// The shell a bare command line is run through on Windows, resolved once.
// The agent writes POSIX one liners (`cd x && ls -la`, pipes, `$(...)`, single
// quotes), and `cmd.exe` chokes on most of them, so Git Bash comes first when
// it is installed and PowerShell after it. `cmd.exe` is only the last resort.
let winShell: { file: string; args: string[] } | undefined;

function windowsShell(env: NodeJS.ProcessEnv): { file: string; args: string[] } {
  if (winShell) {
    return winShell;
  }
  const programFiles = [env.ProgramFiles, env["ProgramFiles(x86)"], env.LOCALAPPDATA && path.join(env.LOCALAPPDATA, "Programs")];
  for (const base of programFiles) {
    const bash = base && path.join(base, "Git", "bin", "bash.exe");
    if (bash && fs.existsSync(bash)) {
      winShell = { file: bash, args: ["-c"] };
      return winShell;
    }
  }
  const system32 = path.join(env.SystemRoot || "C:\\Windows", "System32");
  const pwsh = path.join(system32, "WindowsPowerShell", "v1.0", "powershell.exe");
  winShell = fs.existsSync(pwsh)
    ? { file: pwsh, args: ["-NoProfile", "-NonInteractive", "-Command"] }
    : { file: env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c"] };
  return winShell;
}

// A command running in a real terminal somewhere else (see vscodeTerminal.ts).
// The manager owns the output and the waiting either way; this is only what it
// cannot do itself.
export interface TerminalRun {
  // Settles when the command ends, with what it ended with.
  exit: Promise<TerminalExitStatus>;
  show(): void;
  kill(): void;
}

export interface TerminalRunner {
  // Answers undefined when it cannot take this command, and the manager runs it
  // as a child process instead.
  run(command: string, cwd: string | undefined, onData: (text: string) => void): Promise<TerminalRun | undefined>;
  dispose(): void;
}

interface Term {
  child?: ChildProcess;
  run?: TerminalRun;
  output: string;
  bytes: number;
  limit: number;
  truncated: boolean;
  exitStatus: TerminalExitStatus | null;
  waiters: ((status: TerminalExitStatus) => void)[];
  // Left running on purpose: the agent stopped waiting for it (see `skip`), so
  // its exit is nobody's business but the transcript's.
  skipped?: boolean;
  // Its terminal has been shown, so it is no longer hidden from the user.
  revealed?: boolean;
  released?: boolean;
  // Asked to stop before it had anything to stop. Starting is asynchronous (a real
  // terminal reports its shell integration first, which can take seconds), and a
  // Stop pressed in that window used to signal nothing at all, so the command the
  // user had just cancelled started anyway.
  stopRequested?: boolean;
}

// Runs shell commands on behalf of the agent and exposes the ACP terminal
// methods (create/output/wait_for_exit/kill/release). Output is buffered and
// trimmed to the requested byte limit; each change is streamed to a listener
// so the chat can render live output.
export class TerminalManager {
  private readonly terminals = new Map<string, Term>();
  private seq = 0;
  private static readonly MAX_RELEASED_TERMINALS = 50;

  constructor(
    private readonly baseEnv: NodeJS.ProcessEnv,
    private readonly defaultCwd: string,
    private onOutput?: (terminalId: string, output: string, exitStatus: TerminalExitStatus | null) => void,
    private log?: (line: string) => void,
    // Where a command runs when it can: a real terminal. Absent, or declining a
    // particular command, and it is run as a child process here.
    private readonly runner?: TerminalRunner
  ) {}

  // Re-point the listeners when the session this manager belongs to moves to
  // another chat surface: the running commands keep going, their output just
  // needs to reach the panel now showing them.
  retarget(
    onOutput?: (terminalId: string, output: string, exitStatus: TerminalExitStatus | null) => void,
    log?: (line: string) => void
  ): void {
    this.onOutput = onOutput;
    this.log = log;
  }

  create(params: CreateTerminalParams): { terminalId: string } {
    const terminalId = `term-${++this.seq}`;
    const term: Term = {
      output: "",
      bytes: 0,
      limit: params.outputByteLimit && params.outputByteLimit > 0 ? params.outputByteLimit : 1_048_576,
      truncated: false,
      exitStatus: null,
      waiters: []
    };
    this.terminals.set(terminalId, term);
    // Starting is asynchronous (a real terminal has to report its shell
    // integration first) but creating one is not: the agent gets its id now and
    // asks for the output and the exit separately, both of which wait properly.
    //
    // A failure to start has to end as an exit, not as a rejected promise nobody
    // is holding: the agent is waiting on this terminal, and an unhandled rejection
    // in the extension host is a crash rather than a failed command.
    this.start(terminalId, term, params).catch((err) => {
      this.log?.(`[terminal] ${terminalId} could not start: ${err instanceof Error ? err.message : String(err)}`);
      term.exitStatus = term.exitStatus || { exitCode: null, signal: null };
      for (const w of term.waiters.splice(0)) {
        w(term.exitStatus);
      }
      this.onOutput?.(terminalId, term.output, term.exitStatus);
    });
    return { terminalId };
  }

  private async start(terminalId: string, term: Term, params: CreateTerminalParams): Promise<void> {
    // A chatty command arrives in hundreds of chunks, and every one of them used to
    // post the whole buffer, up to a megabyte, and have the panel rebuild the card
    // around it. That is quadratic in the output, and it made the panel stall during
    // a build. The buffer is coalesced instead: at most one post per interval, plus
    // one at the end so the last lines are never left unsent.
    let pendingPost: NodeJS.Timeout | undefined;
    const flush = () => {
      if (pendingPost) {
        clearTimeout(pendingPost);
        pendingPost = undefined;
      }
      this.onOutput?.(terminalId, term.output, term.exitStatus);
    };
    const postSoon = () => {
      if (pendingPost) {
        return;
      }
      pendingPost = setTimeout(flush, OUTPUT_POST_MS);
      pendingPost.unref?.();
    };
    // Tracked as it grows rather than measured per chunk: measuring the whole buffer
    // on every write flattens the string each time, which is quadratic in the output
    // and put back on the extension host exactly the cost the coalescing above took
    // out of the panel.
    const append = (text: string) => {
      term.output += text;
      term.bytes += Buffer.byteLength(text, "utf8");
      const limit = term.released && term.exitStatus ? Math.min(term.limit, RELEASED_OUTPUT_BYTES) : term.limit;
      while (term.bytes > limit) {
        term.truncated = true;
        let cut = Math.ceil(term.output.length * 0.1) || 1;
        // Never between the halves of a surrogate pair: a lone one is not valid text,
        // and the agent's parser rejects the whole reply that carries it.
        const code = term.output.charCodeAt(cut);
        if (code >= 0xdc00 && code <= 0xdfff) {
          cut++;
        }
        term.bytes -= Buffer.byteLength(term.output.slice(0, cut), "utf8");
        term.output = term.output.slice(cut);
      }
      postSoon();
    };
    const settle = (status: TerminalExitStatus) => {
      term.exitStatus = status;
      for (const w of term.waiters.splice(0)) {
        w(status);
      }
      if (term.released) {
        this.trim(term, RELEASED_OUTPUT_BYTES);
      }
      flush();
      if (term.released) {
        this.trimReleased();
      }
      this.log?.(`[terminal] ${terminalId} exited code=${status.exitCode} signal=${status.signal || ""}`);
    };

    // Stopped before it began, which is the Stop button landing while the terminal
    // was still being made. Nothing has run, so there is nothing to signal: say it
    // is over so the agent stops waiting.
    const stoppedEarly = (): boolean => {
      if (!term.stopRequested) {
        return false;
      }
      this.log?.(`[terminal] ${terminalId} was stopped before it started`);
      settle({ exitCode: null, signal: "SIGTERM" });
      return true;
    };
    if (stoppedEarly()) {
      return;
    }

    // A program with its own arguments, or its own environment, is not something
    // to type into the user's shell: those run as a child process, as they
    // always did. Everything else is the command line the agent would have typed.
    const own = (params.args && params.args.length) || (params.env && params.env.length);
    if (this.runner && !own) {
      const run = await this.runner.run(params.command, params.cwd, append).catch(() => undefined);
      if (run) {
        term.run = run;
        // The wait for shell integration is the widest part of the window, so ask
        // again now that there is something to ask.
        if (term.stopRequested) {
          try { run.kill(); } catch { /* the shell may already be gone */ }
          this.log?.(`[terminal] ${terminalId} was stopped while its terminal was starting`);
          settle({ exitCode: null, signal: "SIGTERM" });
          return;
        }
        this.log?.(`[terminal] ${terminalId} start (integrated): ${params.command}`);
        void run.exit.then(settle);
        // Say it is running before it has printed anything, so its row can offer
        // the terminal it is in and the chance to leave it running.
        this.onOutput?.(terminalId, term.output, null);
        return;
      }
      if (stoppedEarly()) {
        return;
      }
    }
    this.spawn(terminalId, term, params, append, settle);
    this.onOutput?.(terminalId, term.output, null);
  }

  private spawn(
    terminalId: string,
    term: Term,
    params: CreateTerminalParams,
    append: (text: string) => void,
    settle: (status: TerminalExitStatus) => void
  ): void {
    const env: NodeJS.ProcessEnv = { ...this.baseEnv };
    for (const e of params.env || []) {
      env[e.name] = e.value;
    }

    // The agent sends either a program + args (run it directly) or, more often,
    // a full shell command line as `command` with no args (e.g. `cd x && ls`).
    // Running that string directly fails with ENOENT (there is no program by that
    // name), so a compound command surfaces as "Command failed". Run the
    // no-args form through a shell, the way a real terminal does, so cd, &&,
    // pipes, globs and quoting all work.
    const win = process.platform === "win32";
    const hasArgs = Array.isArray(params.args) && params.args.length > 0;
    let file: string;
    let args: string[];
    if (hasArgs) {
      file = params.command;
      args = params.args as string[];
    } else if (win) {
      const shell = windowsShell(env);
      file = shell.file;
      args = [...shell.args, params.command];
    } else {
      file = this.baseEnv.SHELL || "/bin/bash";
      args = ["-c", params.command];
    }

    const child = spawn(file, args, {
      cwd: params.cwd || this.defaultCwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      // Own process group (POSIX) so we can signal the shell and everything it
      // spawns on kill/release, rather than orphaning children.
      detached: !win
    });

    term.child = child;

    // Decode each stream through its own StringDecoder so a multi-byte UTF-8
    // sequence split across chunk boundaries is not corrupted.
    const outDecoder = new StringDecoder("utf8");
    const errDecoder = new StringDecoder("utf8");
    child.stdout?.on("data", (chunk: Buffer) => append(outDecoder.write(chunk)));
    child.stderr?.on("data", (chunk: Buffer) => append(errDecoder.write(chunk)));
    // Through `append` like every other write: added straight to the buffer, it
    // was not counted, so the byte limit the agent asked for could be exceeded by
    // a reply it then had to parse.
    child.on("error", (err) => append(`\n[spawn error] ${err.message}\n`));
    child.on("close", (code, signal) => settle({ exitCode: code, signal: signal ? String(signal) : null }));

    this.log?.(`[terminal] ${terminalId} start: ${params.command} ${(params.args || []).join(" ")}`);
  }

  output(terminalId: string): { output: string; truncated: boolean; exitStatus: TerminalExitStatus | null } {
    const term = this.terminals.get(terminalId);
    if (!term) {
      return { output: "", truncated: false, exitStatus: null };
    }
    return { output: term.output, truncated: term.truncated, exitStatus: term.exitStatus };
  }

  snapshots(): { terminalId: string; output: string; exitStatus: TerminalExitStatus | null }[] {
    return [...this.terminals].map(([terminalId, term]) => ({ terminalId, output: term.output, exitStatus: term.exitStatus }));
  }

  waitForExit(terminalId: string): Promise<TerminalExitStatus> {
    const term = this.terminals.get(terminalId);
    if (!term) {
      return Promise.resolve({ exitCode: null, signal: null });
    }
    if (term.exitStatus) {
      return Promise.resolve(term.exitStatus);
    }
    // Already told the agent this one was over when the user left it running. Asking
    // again is answered the same way: waiting would never settle, and an unanswered
    // request stalls the turn with nothing to show why.
    if (term.skipped) {
      return Promise.resolve({ exitCode: null, signal: null });
    }
    return new Promise((resolve) => term.waiters.push(resolve));
  }

  kill(terminalId: string): void {
    this.signal(this.terminals.get(terminalId), "SIGTERM");
  }

  // Bring up the real terminal a command is running in, if it is running in one,
  // and remember that it is no longer hidden: the action that reveals it is
  // named for what it does, and after the first time it only focuses.
  show(terminalId: string): void {
    const term = this.terminals.get(terminalId);
    if (term?.run) {
      term.run.show();
      term.revealed = true;
    }
  }

  // Whether its terminal has been brought up at least once.
  isRevealed(terminalId: string): boolean {
    return !!this.terminals.get(terminalId)?.revealed;
  }

  // Stop waiting on a command without stopping the command: the agent is told it
  // is over so it can get on, and the transcript keeps showing where it gets to.
  // Only for a command still running, and only once.
  skip(terminalId: string): boolean {
    const term = this.terminals.get(terminalId);
    if (!term || term.exitStatus || term.skipped) {
      return false;
    }
    term.skipped = true;
    for (const w of term.waiters.splice(0)) {
      w({ exitCode: null, signal: null });
    }
    this.log?.(`[terminal] ${terminalId} left running, the agent moved on`);
    return true;
  }

  // Whether this command is one the agent is no longer waiting for.
  isSkipped(terminalId: string): boolean {
    return !!this.terminals.get(terminalId)?.skipped;
  }

  // Whether any command is still going. A chat with one is not idle, whatever the
  // conversation is doing: the user left it running on purpose.
  hasRunning(): boolean {
    for (const [, term] of this.terminals) {
      if (!term.exitStatus) {
        return true;
      }
    }
    return false;
  }

  // Whether it is running somewhere the user could go and look at.
  isIntegrated(terminalId: string): boolean {
    return !!this.terminals.get(terminalId)?.run;
  }

  // Signal one terminal's whole process tree. The commands run here are children
  // of the extension host, so anything left alive when the host exits would be
  // orphaned (the agent reaper only looks for `vtx acp`), which is why every
  // shutdown path has to reach them.
  private signal(term: Term | undefined, sig: "SIGTERM" | "SIGKILL"): void {
    if (!term || term.exitStatus) {
      return;
    }
    if (!term.run && !term.child) {
      // Nothing to signal yet: it is still being started. Remember, so whichever
      // half of `start` gets there next stops instead of carrying on.
      term.stopRequested = true;
      return;
    }
    if (term.run) {
      // The user's own terminal: interrupt the command, leave the terminal.
      term.run.kill();
      return;
    }
    if (!term.child) {
      return;
    }
    const pid = term.child.pid;
    if (process.platform === "win32") {
      // Windows has no process groups or signals, so end the tree with
      // taskkill, and let `/F` be the escalation a SIGKILL asks for.
      const force = sig === "SIGKILL" ? ["/F"] : [];
      if (pid) {
        try { execFile("taskkill", ["/PID", String(pid), "/T", ...force], { windowsHide: true }, () => {}); } catch { /* ignore */ }
      }
      if (force.length) {
        try { term.child.kill(); } catch { /* already gone */ }
      }
      return;
    }
    try {
      // Negative pid signals the group, so the shell and its children go down
      // together; fall back to the direct child if that is not possible.
      if (pid) {
        process.kill(-pid, sig);
      } else {
        term.child.kill(sig);
      }
    } catch {
      try {
        term.child.kill(sig);
      } catch {
        // already gone
      }
    }
  }

  private trimReleased(): void {
    let released = 0;
    for (const [, term] of this.terminals) {
      if (term.released) {
        released++;
      }
    }
    for (const [id, term] of this.terminals) {
      if (released <= TerminalManager.MAX_RELEASED_TERMINALS) {
        return;
      }
      if (term.released) {
        this.terminals.delete(id);
        released--;
      }
    }
  }

  // The agent releases a terminal once it is done with it, and the protocol says a
  // release kills whatever is still running. That is right for a command it waited
  // for, and wrong for one the user chose to leave running: "Continue in
  // Background" is a promise, and the agent releases the terminal the moment it
  // stops waiting, so honouring the kill typed Ctrl+C into the user's own terminal
  // and stopped the very command they had asked to keep. It stays tracked, so the
  // shutdown paths can still reach it.
  release(terminalId: string): void {
    const term = this.terminals.get(terminalId);
    if (!term) {
      return;
    }
    if (term.skipped && !term.exitStatus) {
      this.log?.(`[terminal] ${terminalId} released while left running, so it keeps going`);
      return;
    }
    term.released = true;
    if (term.exitStatus) {
      // Over and done with. The entry stays: the row can still be asked for its
      // output, and answering an empty one wiped a finished command's output out of
      // the transcript. What it does not need is the whole buffer, up to a megabyte
      // of it, held for the rest of the chat: a build loop's worth of released
      // commands added up to hundreds of megabytes that nothing could free.
      this.trim(term, RELEASED_OUTPUT_BYTES);
      this.onOutput?.(terminalId, term.output, term.exitStatus);
      this.trimReleased();
      return;
    }
    // Still running and not something the user asked to keep, so stop it the way
    // every other teardown does, escalating rather than asking once and forgetting
    // it, which left anything trapping SIGTERM running with nothing able to reach it.
    this.signal(term, "SIGTERM");
    const t = setTimeout(() => this.signal(term, "SIGKILL"), 1500);
    t.unref?.();
  }

  // Cut a finished terminal's buffer back to its last `limit` bytes. Only ever
  // called once the command has exited, so the running byte count its writer
  // keeps cannot be left disagreeing with the buffer.
  private trim(term: Term, limit: number): void {
    const bytes = Buffer.byteLength(term.output, "utf8");
    if (bytes <= limit) {
      term.bytes = bytes;
      return;
    }
    let out = term.output.slice(-limit);
    while (Buffer.byteLength(out, "utf8") > limit) {
      out = out.slice(Math.ceil(out.length * 0.1) || 1);
    }
    // Never starting on the second half of a surrogate pair: a lone one is not
    // valid text, and the agent's parser rejects the whole reply carrying it.
    const first = out.charCodeAt(0);
    if (first >= 0xdc00 && first <= 0xdfff) {
      out = out.slice(1);
    }
    term.output = out;
    term.bytes = Buffer.byteLength(out, "utf8");
    term.truncated = true;
  }

  // Everything this chat was running, on the way out (terminating it, an idle
  // exit, a surface going away). The polite pass cannot win against a command that
  // traps SIGTERM, so it escalates on a timer, the way the agent's own dispose
  // does: this path runs while the host is still alive to fire it.
  disposeAll(): void {
    const terms = [...this.terminals.values()];
    this.requestStopAll();
    const t = setTimeout(() => {
      for (const term of terms) {
        this.signal(term, "SIGKILL");
      }
    }, 1500);
    t.unref?.();
    this.terminals.clear();
    this.runner?.dispose();
  }

  // Shutdown, step one: ask every running command to stop, keeping the entries so
  // survivors can be forced afterwards.
  requestStopAll(): void {
    for (const [, term] of this.terminals) {
      this.signal(term, "SIGTERM");
    }
  }

  // Shutdown, step two: SIGKILL whatever ignored the SIGTERM, once it has had
  // time to land, so the exiting host leaves nothing behind.
  forceStopAll(): void {
    for (const [, term] of this.terminals) {
      this.signal(term, "SIGKILL");
    }
    this.terminals.clear();
    // A command running in the user's own terminal cannot be signalled past an
    // interrupt, so the last word is disposing the terminal itself, which takes its
    // shell and everything under it. This is the end of the shutdown sequence, so
    // there is nothing left that needs the runner.
    this.runner?.dispose();
  }
}
