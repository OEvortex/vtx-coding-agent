// Shutdown tests for the agent lifecycle, run against the real AcpClient and
// TerminalManager compiled out of src/. These guard the safeguard that matters
// most: when the extension host goes away, nothing it spawned is left running.
//
// A `vtx acp` agent cannot be handed to the next extension host (it runs its
// commands, file writes and permission prompts through us, over our stdio), so
// `shutdown()` has to actually finish the job, including against an agent that
// ignores a polite request to stop.
//
// The fake agents are shell wrappers so the client can invoke them the way it
// invokes the real CLI (`<cliPath> acp`), with the extra argument ignored.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "vtx-lifecycle-"));

// Bundle the vscode-free host modules so they can be required from a plain test.
function load(rel, name) {
  const outfile = path.join(TMP, name + ".js");
  esbuild.buildSync({
    entryPoints: [path.join(ROOT, rel)],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    logLevel: "error",
    alias: { vscode: path.join(__dirname, "vscode-stub.js") }
  });
  return require(outfile);
}
const { AcpClient } = load("src/acp/client.ts", "client");
const { TerminalManager } = load("src/acp/terminal.ts", "terminal");
const { cliCommand } = load("src/cli/locate.ts", "locate");
const { VsCodeTerminalRunner, OutputCleaner, stripAnsi } = load("src/acp/vscodeTerminal.ts", "vscodeTerminal");
// The copy of the stub the bundle above loaded, which is the one it will call.
const vscode = globalThis.__dvVscode;

const PID_FILE = path.join(TMP, "agent.pid");

// A fake agent, wrapped in a shell script so `<script> acp` runs it. `body` is
// the node source. It records its pid so the test can check it really died.
function fakeAgent(name, body) {
  const js = path.join(TMP, name + ".js");
  const sh = path.join(TMP, name + ".sh");
  fs.writeFileSync(js, `require("fs").writeFileSync(${JSON.stringify(PID_FILE)}, String(process.pid));\n${body}\n`);
  fs.writeFileSync(sh, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
  fs.chmodSync(sh, 0o755);
  return sh;
}

// Exits as soon as its stdin closes, like a well-behaved stdio agent.
const POLITE = () => fakeAgent("polite", `
  process.stdin.on("end", () => process.exit(0));
  process.stdin.resume();
`);

// Ignores both stdin EOF and SIGTERM: only SIGKILL ends it. This is the docker
// backed MCP case that used to survive as an orphan.
const STUBBORN = () => fakeAgent("stubborn", `
  process.on("SIGTERM", () => {});
  process.stdin.resume();
  setInterval(() => {}, 1000);
`);

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

async function waitGone(pid, ms = 2000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (!alive(pid)) return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return !alive(pid);
}

function startAgent(cliPath) {
  const client = new AcpClient({ cliPath, cwd: TMP, env: process.env });
  client.start();
  return client;
}

async function agentPid() {
  for (let i = 0; i < 100; i++) {
    if (fs.existsSync(PID_FILE)) {
      const pid = Number(fs.readFileSync(PID_FILE, "utf8").trim());
      if (pid > 0) return pid;
    }
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error("the fake agent never started");
}

test.beforeEach(() => {
  fs.rmSync(PID_FILE, { force: true });
});

test("shutdown lets a well-behaved agent exit on its own", { skip: process.platform === "win32" }, async () => {
  const client = startAgent(POLITE());
  const pid = await agentPid();
  assert.ok(alive(pid));

  const started = Date.now();
  await client.shutdown();
  // Closing stdin is enough, so this returns well inside the budget.
  assert.ok(Date.now() - started < 700, "a clean exit should not wait for the escalation");
  assert.ok(await waitGone(pid), "the agent should be gone");
});

test("shutdown kills an agent that ignores stdin EOF and SIGTERM", { skip: process.platform === "win32" }, async () => {
  const client = startAgent(STUBBORN());
  const pid = await agentPid();
  assert.ok(alive(pid));

  const started = Date.now();
  await client.shutdown(400);
  const took = Date.now() - started;
  // It must escalate all the way to SIGKILL, and still finish rather than waiting
  // on the agent forever, since VS Code only awaits deactivate for so long. The
  // ceiling is loose on purpose: this asserts boundedness, not a timing, and a
  // loaded CI runner is allowed to be slow.
  assert.ok(took >= 400, "expected the full escalation, took " + took + "ms");
  assert.ok(took < 4000, "shutdown must stay bounded, took " + took + "ms");
  assert.ok(await waitGone(pid), "a stubborn agent must not survive shutdown");
});

test("shutdown is safe to call twice, and after the agent has already gone", { skip: process.platform === "win32" }, async () => {
  const client = startAgent(POLITE());
  const pid = await agentPid();
  await client.shutdown();
  assert.ok(await waitGone(pid));
  // The second call must resolve rather than wait out the whole budget.
  const started = Date.now();
  await client.shutdown();
  assert.ok(Date.now() - started < 100);
});

test("an exiting host leaves no running command behind", { skip: process.platform === "win32" }, async () => {
  const terms = new TerminalManager(process.env, TMP);
  // A shell that traps SIGTERM, standing in for a build or test run that will
  // not stop when asked. These are children of the extension host, and the agent
  // reaper only looks for `vtx acp`, so nothing else would ever collect them.
  // It reports the pid of a child it spawned, so the test can prove the whole
  // tree went down rather than just the shell.
  // The inner shell announces itself only once its trap is installed, so the
  // test can never signal it before it is actually ignoring SIGTERM.
  const { terminalId } = terms.create({
    sessionId: "s1",
    command: "trap '' TERM; sh -c 'trap \"\" TERM; echo ready; while true; do sleep 1; done' & echo child=$!; wait"
  });
  assert.ok(terminalId);
  let child = 0;
  for (let i = 0; i < 250; i++) {
    const out = terms.output(terminalId).output;
    const m = /child=(\d+)/.exec(out);
    if (m && out.includes("ready")) {
      child = Number(m[1]);
      break;
    }
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.ok(child > 0, "the fake command never reported a trapping child");
  assert.ok(alive(child), "the child should be running before we stop it");

  terms.requestStopAll();
  await new Promise((r) => setTimeout(r, 200));
  // Everything here traps SIGTERM, so the polite pass cannot win. This is what
  // makes the forced second pass load bearing rather than belt and braces.
  assert.ok(alive(child), "the fixture must survive SIGTERM, or this proves nothing");
  terms.forceStopAll();
  assert.ok(await waitGone(child), "a stubborn command's children must not be left running");
});

test("terminating a chat escalates past a command that ignores SIGTERM", { skip: process.platform === "win32" }, async () => {
  // The shutdown path escalates, and this one did not: terminating a chat, an idle
  // exit and a surface going away all went through dispose, which asked once and
  // then dropped the entry, so a command that traps SIGTERM was left running with
  // nothing left able to find it.
  const terms = new TerminalManager(process.env, TMP);
  const { terminalId } = terms.create({
    sessionId: "s1",
    command: "trap '' TERM; sh -c 'trap \"\" TERM; echo ready; while true; do sleep 1; done' & echo child=$!; wait"
  });
  let child = 0;
  for (let i = 0; i < 250; i++) {
    const out = terms.output(terminalId).output;
    const m = /child=(\d+)/.exec(out);
    if (m && out.includes("ready")) { child = Number(m[1]); break; }
    await new Promise((r) => setTimeout(r, 20));
  }
  assert.ok(child > 0 && alive(child), "the fixture must be running and trapping first");

  terms.disposeAll();
  assert.ok(await waitGone(child), "a chat that goes away takes its commands with it");
});

test("a command stopped while its terminal is still starting is stopped for real", async () => {
  // Waiting for a real terminal's shell integration takes seconds, and a Stop
  // pressed in that window used to signal a terminal with nothing to signal yet,
  // so the command the user had just cancelled started anyway and ran to
  // completion. The wait is stood in for here by a runner that answers when the
  // test says so.
  let handOver = () => {};
  let killed = 0;
  const run = { exit: new Promise(() => {}), show() {}, kill() { killed++; } };
  const runner = {
    run: () => new Promise((resolve) => { handOver = () => resolve(run); }),
    dispose() {}
  };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);

  const { terminalId } = terms.create({ sessionId: "s1", command: "npm test" });
  // Stop lands while the terminal is still being made.
  terms.kill(terminalId);
  // Only now does the terminal become available.
  handOver();
  const status = await terms.waitForExit(terminalId);

  assert.strictEqual(killed, 1, "the command is stopped as soon as there is something to stop");
  assert.ok(status, "and the agent is told it is over instead of waiting for ever");
});

test("a command left running in the background survives the agent releasing it", async () => {
  // "Continue in Background" is a promise to the user, and the agent releases the
  // terminal as soon as it stops waiting. The protocol says a release kills what is
  // still running, so honouring that typed Ctrl+C into the user's own terminal and
  // killed the command they had just asked to keep.
  let killed = 0;
  const run = { exit: new Promise(() => {}), show() {}, kill() { killed++; } };
  const runner = { run: async () => run, dispose() {} };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);

  const { terminalId } = terms.create({ sessionId: "s1", command: "npm run dev" });
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(terms.skip(terminalId), true, "the user leaves it running");

  terms.release(terminalId);
  assert.strictEqual(killed, 0, "releasing it must not stop it");
  assert.strictEqual(terms.isSkipped(terminalId), true, "and it is still tracked, so shutdown can reach it");

  // Which it does, because nothing may outlive the host.
  terms.forceStopAll();
  assert.strictEqual(killed, 1, "the way out still stops it");
});

test("a finished terminal stops holding its whole output once the agent lets it go", async () => {
  // The entry has to stay, because its row can still be asked for what the command
  // printed. The megabyte behind it does not: a build loop's worth of released
  // commands added up to hundreds of megabytes that nothing could free until the
  // chat was closed.
  let emit = () => {};
  let settle = () => {};
  const run = { exit: new Promise((r) => { settle = r; }), show() {}, kill() {} };
  const runner = { run: async (_c, _cwd, onData) => { emit = onData; return run; }, dispose() {} };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);

  const { terminalId } = terms.create({ sessionId: "s1", command: "npm run build" });
  await new Promise((r) => setTimeout(r, 20));
  emit("noise\n".repeat(60000) + "the last line\n");
  settle({ exitCode: 0, signal: null });
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(terms.output(terminalId).output.length > 300000, "it held all of it while it ran");

  terms.release(terminalId);
  const after = terms.output(terminalId).output;
  assert.ok(Buffer.byteLength(after, "utf8") <= 64 * 1024, "and not after, " + after.length);
  assert.ok(after.endsWith("the last line\n"), "keeping the end, which is the part anyone reads");
  assert.strictEqual(terms.output(terminalId).truncated, true, "and saying it was cut");
});

test("a terminal honors its requested output limit", async () => {
  let emit = () => {};
  let settle = () => {};
  const run = { exit: new Promise((resolve) => { settle = resolve; }), show() {}, kill() {} };
  const runner = { run: async (_command, _cwd, onData) => { emit = onData; return run; }, dispose() {} };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);
  const id = terms.create({ sessionId: "s1", command: "build", outputByteLimit: 2 * 1024 * 1024 }).terminalId;
  await new Promise((resolve) => setTimeout(resolve, 20));
  emit("x".repeat(1_200_000));
  settle({ exitCode: 0, signal: null });
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.strictEqual(Buffer.byteLength(terms.output(id).output, "utf8"), 1_200_000);
});

test("late output after release keeps the terminal retention bound", async () => {
  let emit = () => {};
  let settle = () => {};
  const run = { exit: new Promise((resolve) => { settle = resolve; }), show() {}, kill() {} };
  const runner = { run: async (_command, _cwd, onData) => { emit = onData; return run; }, dispose() {} };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);
  const id = terms.create({ sessionId: "s1", command: "build" }).terminalId;
  await new Promise((resolve) => setTimeout(resolve, 20));
  emit("x".repeat(100_000));
  terms.release(id);
  settle({ exitCode: 0, signal: null });
  await new Promise((resolve) => setTimeout(resolve, 20));
  emit("y".repeat(100_000));
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.ok(Buffer.byteLength(terms.output(id).output, "utf8") <= 64 * 1024);
});

test("released terminal history evicts the oldest completed output", async () => {
  const runner = {
    run: async () => ({ exit: Promise.resolve({ exitCode: 0, signal: null }), show() {}, kill() {} }),
    dispose() {}
  };
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, runner);
  const ids = [];
  for (let i = 0; i < 51; i++) {
    const id = terms.create({ sessionId: "s1", command: "echo done" }).terminalId;
    await terms.waitForExit(id);
    terms.release(id);
    ids.push(id);
  }

  assert.strictEqual(terms.output(ids[0]).exitStatus, null);
  assert.deepStrictEqual(terms.output(ids.at(-1)).exitStatus, { exitCode: 0, signal: null });
});

// --- Commands in the user's own terminal -----------------------------------

// Stands in for a shell that reports what it runs. `chunks` are written into the
// execution's stream, and the test decides when it ends and with what.
function fakeShell(terminal) {
  const state = { execution: null, push: null, close: null, commands: [] };
  terminal.shellIntegration = {
    executeCommand: (commandLine) => {
      state.commands.push(commandLine);
      const queue = [];
      let waiting = null;
      let ended = false;
      state.push = (text) => {
        if (waiting) { const w = waiting; waiting = null; w({ value: text, done: false }); }
        else queue.push(text);
      };
      state.close = () => {
        ended = true;
        if (waiting) { const w = waiting; waiting = null; w({ value: undefined, done: true }); }
      };
      state.execution = {
        commandLine,
        read: () => ({
          [Symbol.asyncIterator]: () => ({
            next: () => {
              if (queue.length) return Promise.resolve({ value: queue.shift(), done: false });
              if (ended) return Promise.resolve({ value: undefined, done: true });
              return new Promise((resolve) => { waiting = resolve; });
            }
          })
        })
      };
      return state.execution;
    }
  };
  return state;
}

const tick = () => new Promise((r) => setTimeout(r, 5));

test("a command runs in the user's own terminal, and its output is readable", async () => {
  globalThis.__dvConfig = { useIntegratedTerminal: true };
  vscode.window.terminals.length = 0;
  const runner = new VsCodeTerminalRunner("/tmp", {}, () => {});
  const seen = [];
  const started = runner.run("npm test", undefined, (t) => seen.push(t));
  await tick();

  const terminal = vscode.window.terminals[0];
  assert.ok(terminal, "it opens a terminal of its own");
  assert.strictEqual(terminal.options.name, "Vtx");
  assert.strictEqual(terminal.options.hideFromUser, true, "out of the way until it is wanted");
  assert.strictEqual(terminal.options.env.GIT_PAGER, "cat", "or git diff would wait for a keypress for ever");

  // What the user set in `vtx.env` reaches a command run here too. It used to
  // reach only the fallback (a child process), so the same command worked or
  // failed depending on which path took it, with nothing said either way.
  vscode.window.terminals.length = 0;
  const configured = new VsCodeTerminalRunner("/tmp", { API_KEY: "k", GIT_PAGER: "less" }, () => {});
  void configured.run("env", undefined, () => {});
  await tick();
  assert.strictEqual(vscode.window.terminals[0].options.env.API_KEY, "k", "the setting is in the terminal");
  assert.strictEqual(vscode.window.terminals[0].options.env.GIT_PAGER, "cat", "and the pager it cannot escape still wins");

  // The shell reports itself, so the command can be run through it.
  const shell = fakeShell(terminal);
  vscode.window.__fire.shellIntegrationChanged.fire({ terminal });
  const run = await started;
  assert.ok(run, "with shell integration the command is the terminal's");
  assert.deepStrictEqual(shell.commands, ["npm test"]);

  // Raw terminal output: colours, and a progress bar redrawing one line.
  shell.push("\u001b[32mpassed\u001b[0m\n");
  shell.push("50%\r75%\r100%\n");
  await tick();
  assert.strictEqual(seen.join(""), "passed\n100%\n", "cleaned of colour, and only the last draw of a line");

  run.show();
  assert.strictEqual(terminal.shown, 1, "Show Terminal opens the real one");

  vscode.window.__fire.shellExecutionEnded.fire({ execution: shell.execution, exitCode: 0 });
  assert.deepStrictEqual(await run.exit, { exitCode: 0, signal: null });
});

test("a second command while the first is still running gets its own terminal", async () => {
  globalThis.__dvConfig = { useIntegratedTerminal: true };
  vscode.window.terminals.length = 0;
  const runner = new VsCodeTerminalRunner("/tmp", {}, () => {});

  const first = runner.run("npm run dev", undefined, () => {});
  await tick();
  const shellA = fakeShell(vscode.window.terminals[0]);
  vscode.window.__fire.shellIntegrationChanged.fire({ terminal: vscode.window.terminals[0] });
  const runA = await first;

  // The first command is still going, so typing the second into the same shell
  // would put one command on top of the other.
  const second = runner.run("npm test", undefined, () => {});
  await tick();
  assert.strictEqual(vscode.window.terminals.length, 2, "so it opens another terminal");
  const shellB = fakeShell(vscode.window.terminals[1]);
  vscode.window.__fire.shellIntegrationChanged.fire({ terminal: vscode.window.terminals[1] });
  await second;
  assert.deepStrictEqual(shellA.commands, ["npm run dev"]);
  assert.deepStrictEqual(shellB.commands, ["npm test"]);

  // Once a command ends, its terminal takes the next one rather than piling up.
  vscode.window.__fire.shellExecutionEnded.fire({ execution: shellA.execution, exitCode: 0 });
  await runA.exit;
  await tick();
  const third = runner.run("git status", undefined, () => {});
  await tick();
  assert.strictEqual(vscode.window.terminals.length, 2, "the free one is reused");
  await third;
  assert.deepStrictEqual(shellA.commands, ["npm run dev", "git status"]);
  runner.dispose();
});

test("a command the terminal cannot report is run in the background instead", async () => {
  globalThis.__dvConfig = { useIntegratedTerminal: true };
  vscode.window.terminals.length = 0;
  const runner = new VsCodeTerminalRunner("/tmp", {}, () => {});
  assert.strictEqual(
    await runner.run("python3 - <<'PY'\nprint(1)\nPY", undefined, () => {}),
    undefined,
    "a script over several lines is never reported back line by line, so it is not run here"
  );
  assert.strictEqual(
    await runner.run("ls", "/somewhere/else", () => {}),
    undefined,
    "nor is one that has to run somewhere else"
  );
  globalThis.__dvConfig = { useIntegratedTerminal: false };
  assert.strictEqual(await runner.run("ls", undefined, () => {}), undefined, "nor when it is switched off");
  assert.strictEqual(vscode.window.terminals.length, 0, "and none of that opens a terminal");
});

test("a command left running lets the agent move on, and keeps going", async () => {
  const terms = new TerminalManager(process.env, TMP, undefined, undefined, {
    run: async () => undefined,
    dispose: () => {}
  });
  const { terminalId } = terms.create({ sessionId: "s1", command: "sleep 5" });
  await tick();
  let settled = null;
  void terms.waitForExit(terminalId).then((s) => { settled = s; });

  assert.strictEqual(terms.skip(terminalId), true);
  await tick();
  assert.deepStrictEqual(settled, { exitCode: null, signal: null }, "the agent stops waiting on it");
  assert.strictEqual(terms.isSkipped(terminalId), true);
  assert.strictEqual(terms.skip(terminalId), false, "and only once");
  assert.strictEqual(terms.output(terminalId).exitStatus, null, "the command itself is still going");
  terms.disposeAll();
});

test("output is cleaned the way a terminal would have drawn it", async () => {
  assert.strictEqual(stripAnsi("\u001b[31mred\u001b[0m"), "red");
  assert.strictEqual(stripAnsi("\u001b]633;C\u0007done"), "done", "and the shell's own markers go too");
  const seen = [];
  const cleaner = new OutputCleaner((t) => seen.push(t));
  cleaner.write("one\r\ntw");
  cleaner.write("o\n[  ] 0%\r[==] 100%\n");
  cleaner.write("no newline yet");
  assert.strictEqual(seen.join(""), "one\ntwo\n[==] 100%\n", "CRLF, split lines, and a progress bar");
  cleaner.flush();
  assert.strictEqual(seen.join(""), "one\ntwo\n[==] 100%\nno newline yet\n", "and what was left over when it ended");

  // A sequence can arrive in two pieces, and half of one matches nothing: the
  // leftover escape was then deleted as a stray control character and the rest of
  // it was left in the transcript as "[0mdone".
  const split = [];
  const c2 = new OutputCleaner((t) => split.push(t));
  c2.write("red\u001b[");
  c2.write("0mdone\n");
  assert.strictEqual(split.join(""), "reddone\n", "a sequence split across chunks is still a sequence");

  // And an escape that never completes is not held for ever.
  const stray = [];
  const c3 = new OutputCleaner((t) => stray.push(t));
  c3.write("plain\u001b");
  c3.flush();
  assert.strictEqual(stray.join(""), "plain\n", "what is left is emitted, not swallowed");
});

test("a Windows .cmd shim is run through the interpreter, quoting and all", async () => {
  // npm installs the CLI as a .cmd on Windows, which Node refuses to spawn
  // directly, so it goes through a shell and the quoting becomes ours.
  const plain = cliCommand("/usr/local/bin/vtx", ["acp", "--flag"]);
  assert.deepStrictEqual(plain, { file: "/usr/local/bin/vtx", args: ["acp", "--flag"], shell: false });

  const win = { value: process.platform };
  Object.defineProperty(process, "platform", { value: "win32" });
  try {
    assert.deepStrictEqual(
      cliCommand("C:\\Program Files\\vtx\\vtx.cmd", ["acp", "--dir", "C:\\my code"]),
      {
        file: '"C:\\Program Files\\vtx\\vtx.cmd"',
        args: ["acp", "--dir", '"C:\\my code"'],
        shell: true
      },
      "a path with a space cannot reach the shell bare"
    );
    assert.strictEqual(
      cliCommand("C:\\tools\\vtx.exe", ["acp"]).shell,
      false,
      "a real executable still spawns directly"
    );
    // cmd.exe expands these even inside double quotes and there is no escape for
    // them on a command line, so passing one through would run something else.
    assert.throws(
      () => cliCommand("C:\\tools\\vtx.cmd", ["mcp", "add", "%USERPROFILE%"]),
      /no escape/,
      "a value carrying % is refused rather than quoted and mangled"
    );
    assert.throws(
      () => cliCommand("C:\\tools\\vtx.cmd", ["mcp", "add", "a!b!"]),
      /no escape/,
      "and so is one carrying !"
    );
  } finally {
    Object.defineProperty(process, "platform", win);
  }
});

test.after(() => {
  try { fs.rmSync(TMP, { recursive: true, force: true }); } catch { /* ignore */ }
});
