// Regression tests for the chat webview, run against the real bundle via the
// jsdom harness. Run with: npm test   (compiles the bundle first).
//
// These guard the rendering path that broke when `busy` was undefined: a throw
// inside buildTurnChrome left user request bubbles empty on session load and
// skipped scroll-to-bottom on live sends.

const { after, describe, test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { createHarness: mountHarness } = require("./webview-harness");

const ROOT = path.resolve(__dirname, "..");
const harnesses = [];
const createHarness = (...args) => {
  const harness = mountHarness(...args);
  harnesses.push(harness);
  return harness;
};
after(() => harnesses.forEach(({ window }) => window.close()));

describe("webview", { concurrency: 8 }, () => {
test("session load replays user request bubbles with their text", async () => {
  const h = createHarness();
  h.replay([
    { role: "user", text: "first user question" },
    { role: "assistant", text: "first assistant answer" },
    { role: "user", text: "second user question" },
    { role: "assistant", text: "second assistant answer" }
  ]);
  await h.settle();

  assert.deepStrictEqual(h.reqTexts(), ["first user question", "second user question"]);
  assert.deepStrictEqual(h.respTexts(), ["first assistant answer", "second assistant answer"]);
  assert.strictEqual(
    h.errors().length,
    0,
    "webview handler threw: " + JSON.stringify(h.errors())
  );
});

test("ready reveals the chat before session data arrives", async () => {
  const h = createHarness();
  const boot = h.document.getElementById("boot");
  assert.ok(!boot.classList.contains("hidden"));

  h.post({ type: "ready" });
  await h.settle();

  assert.ok(boot.classList.contains("hidden"));
});

test("a thread send reports that its transcript can be preserved", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "an earlier message" });
  h.post({ type: "busy", value: false });
  const input = h.document.getElementById("input");
  input.value = "continue";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  h.document.getElementById("send").click();
  await h.settle();

  const sent = h.posted.filter((m) => m.type === "send").pop();
  assert.deepStrictEqual(sent, { type: "send", text: "continue", newSession: false, preserveTranscript: true });
});

test("a replay batch preserves historical update order", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true });
  h.post({
    type: "replay",
    items: [
      { type: "userChunk", text: "earlier question", messageId: "u1" },
      { type: "assistantChunk", text: "earlier answer", messageId: "a1" }
    ]
  });
  h.post({ type: "loaded" });
  await h.settle();

  assert.deepStrictEqual(h.reqTexts(), ["earlier question"]);
  assert.deepStrictEqual(h.respTexts(), ["earlier answer"]);
});

test("terminal output cache releases the oldest completed entry", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  for (let i = 0; i < 51; i++) {
    h.post({ type: "terminalOutput", terminalId: `term-${i}`, output: `output ${i}`, exitStatus: { exitCode: 0 } });
  }
  h.post({ type: "toolCall", id: "old", kind: "execute", status: "completed", rawInput: { command: "old" }, content: [{ type: "terminal", terminalId: "term-0" }] });
  h.post({ type: "toolCall", id: "new", kind: "execute", status: "completed", rawInput: { command: "new" }, content: [{ type: "terminal", terminalId: "term-50" }] });
  await h.settle();

  assert.strictEqual(h.thread().querySelector('pre[data-terminal="term-0"]').textContent, "…");
  assert.match(h.thread().querySelector('pre[data-terminal="term-50"]').textContent, /output 50/);
});

test("terminal output cache retains an oldest running terminal", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  for (let i = 0; i < 50; i++) {
    h.post({ type: "terminalOutput", terminalId: `running-${i}`, output: `running ${i}`, exitStatus: null });
  }
  h.post({ type: "terminalOutput", terminalId: "completed", output: "finished", exitStatus: { exitCode: 0 } });
  h.post({ type: "toolCall", id: "running", kind: "execute", status: "in_progress", rawInput: { command: "watch" }, content: [{ type: "terminal", terminalId: "running-0" }] });
  await h.settle();

  assert.match(h.thread().querySelector('pre[data-terminal="running-0"]').textContent, /running 0/);
});

test("live user message renders without a handler error", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "userMessage", text: "hello from a live send" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "hi there" });
  h.post({ type: "assistantEnd" });
  await h.settle();

  assert.deepStrictEqual(h.reqTexts(), ["hello from a live send"]);
  assert.deepStrictEqual(h.respTexts(), ["hi there"]);
  assert.strictEqual(h.errors().length, 0, "webview handler threw: " + JSON.stringify(h.errors()));
});

test("assistant file references render as clickable links", async () => {
  const h = createHarness();
  const href = "file:///Users/shayan.khaksar/VSCode/vtx-vscode/src/acp/client.ts#L20";
  h.replay([{ role: "assistant", text: `See [client.ts:243-251](${href})` }]);
  await h.settle();

  const link = h.thread().querySelector(".resp-text a");
  assert.ok(link, "file references should render as anchors");
  assert.ok(link.classList.contains("file-change"), "file references use the normal file pill class");
  assert.ok(link.classList.contains("file-pill"), "file references use the shared file pill base");
  assert.strictEqual(link.querySelector(".file-pill-name").textContent, "client.ts:243-251");
  assert.ok(link.querySelector(".file-pill-icon.codicon-file-code"), "file references use the path based file icon");
  assert.strictEqual(link.querySelector(".file-pill-icon").getAttribute("aria-hidden"), "true");
  assert.strictEqual(link.getAttribute("href"), href);
  assert.strictEqual(link.tabIndex, 0);
  assert.strictEqual(link.getAttribute("role"), "button");
  link.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  link.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: " ", bubbles: true }));
  link.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  assert.deepStrictEqual(h.posted.at(-1), { type: "openFile", path: href });
  assert.strictEqual(h.errors().length, 0, "webview handler threw: " + JSON.stringify(h.errors()));
});

test("code wrapped file links become file pills without changing code", async () => {
  const h = createHarness();
  const href = "file:///Users/shayan.khaksar/VSCode/OpenIPTV/package.json";
  h.replay([{
    role: "assistant",
    text: `The version is in \`[package.json](${href})\`, \`[config](src/config.ts)\`, \`[spaced](<src/config file.ts>)\`, and \`[web](<https://example.com>)\`.\n\n\`\`\`text\n[leave this alone](file:///tmp/example.ts)\n\`\`\``
  }]);
  await h.settle();

  const links = [...h.thread().querySelectorAll(".resp-text a.file-pill")];
  assert.strictEqual(links.length, 3, "code wrapped file links become file pills");
  assert.deepStrictEqual(links.map((link) => link.querySelector(".file-pill-name").textContent), ["package.json", "config", "spaced"]);
  assert.ok([...h.thread().querySelectorAll("code")].some((code) => code.textContent.includes("[web]")),
    "angle wrapped external links remain code");
  assert.ok(h.thread().querySelector("pre code").textContent.includes("leave this alone"),
    "fenced code remains code");
});

test("external links stay external and relative links open decoded paths", async () => {
  const h = createHarness();
  h.replay([{
    role: "assistant",
    text: "[FTP](ftp://example.com/file) [Doc](docs/C%23.md#section) " +
      "[Drive](C:/repo/src/app.ts) [Query](?view=raw) [Section](./#section)"
  }]);
  await h.settle();

  const [ftp, doc, drive, query, section] = h.thread().querySelectorAll(".resp-text a");
  assert.ok(ftp && doc && drive && query && section, "all links should render");
  assert.ok(!ftp.classList.contains("file-pill"), "external links stay plain");
  assert.ok(doc.classList.contains("file-pill"), "relative links use file styling");
  assert.ok(drive.classList.contains("file-pill"), "drive paths use file styling");
  assert.ok(!query.classList.contains("file-pill"), "query links stay plain");
  assert.ok(!section.classList.contains("file-pill"), "empty relative fragments stay plain");
  ftp.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  query.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  section.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  assert.strictEqual(h.posted.some((m) => m.type === "openFile"), false, "non file links stay with VS Code");
  drive.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  assert.deepStrictEqual(h.posted.at(-1), { type: "openFile", path: "C:/repo/src/app.ts" });
  doc.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  assert.deepStrictEqual(h.posted.at(-1), { type: "openFile", path: "docs/C#.md" });
});

test("changing inline reference style updates existing links", async () => {
  const h = createHarness();
  h.replay([{ role: "assistant", text: "See [client.ts](src/client.ts)" }]);
  await h.settle();
  const link = h.thread().querySelector(".resp-text a");
  assert.ok(link.classList.contains("file-pill"));

  h.post({ type: "capabilities", inlineReferencesStyle: "link" });
  await h.settle();
  assert.ok(!link.classList.contains("file-pill"), "link mode removes existing pill styling");
  assert.strictEqual(link.querySelector(".file-pill-icon"), null, "link mode removes the file icon");

  h.post({ type: "capabilities", inlineReferencesStyle: "box" });
  await h.settle();
  assert.ok(link.classList.contains("file-pill"), "box mode restores existing pill styling");
  assert.ok(link.querySelector(".file-pill-icon"), "box mode restores the file icon");
});

test("elicitation renders oneOf/anyOf options and submits the chosen consts", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "Focus",
    allowOther: true,
    schema: {
      type: "object",
      required: ["q0", "q1"],
      properties: {
        q0: {
          type: "string",
          title: "Focus",
          description: "Focus",
          oneOf: [{ const: "A", title: "Option A" }, { const: "B", title: "Option B" }]
        },
        q1: {
          type: "array",
          title: "Tasks",
          description: "Tasks",
          minItems: 1,
          items: { anyOf: [{ const: "X", title: "Option X" }, { const: "Y", title: "Option Y" }] }
        }
      }
    }
  });
  await h.settle(30);

  const tray = h.window.document.getElementById("elicitation-tray");
  assert.ok(tray.querySelectorAll(".elicit-option").length >= 4, "options should render");

  // VS Code layout: a leading 1-based number and a trailing check per row.
  const firstOpt = tray.querySelector(".elicit-option");
  assert.strictEqual(firstOpt.querySelector(".elicit-number").textContent, "1", "leading option number");
  assert.ok(firstOpt.lastElementChild.classList.contains("elicit-indicator"), "check indicator is trailing");

  const submitBtn = [...tray.querySelectorAll("button")].find((b) => b.textContent === "Submit");
  // Submit is disabled until every question is answered (no default selection).
  assert.ok(submitBtn.disabled, "Submit is disabled before any option is chosen");

  const radios = [...tray.querySelectorAll('input[type="radio"]')];
  const checks = [...tray.querySelectorAll('input[type="checkbox"]')];
  const pick = (input) => { input.checked = true; input.dispatchEvent(new h.window.Event("change", { bubbles: true })); };
  pick(radios.find((r) => r.value === "A"));
  assert.ok(submitBtn.disabled, "still disabled with one of two questions answered");
  pick(checks.find((c) => c.value === "X"));
  assert.ok(!submitBtn.disabled, "enabled once all questions are answered");
  submitBtn.click();
  await h.settle(10);

  const resp = h.posted.find((m) => m.type === "elicitationResponse");
  assert.ok(resp, "an elicitationResponse should be posted");
  assert.strictEqual(resp.action, "accept");
  assert.deepStrictEqual(resp.content, { q0: "A", q1: ["X"] });
  assert.strictEqual(h.errors().length, 0);
});

test("opening the session list delegates refresh to its visibility signal", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "sessionReady", sessionId: "A" });
  await h.settle();
  h.posted.length = 0;

  h.document.getElementById("history-btn").click();
  await h.settle();

  assert.ok(h.posted.some((message) => message.type === "listVisible" && message.value === true));
  assert.strictEqual(h.posted.some((message) => message.type === "refreshSessions"), false);
});

test("leaving a running session to the list detaches the composer", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "userMessage", text: "do a big task" });
  h.post({ type: "assistantStart" });
  h.post({ type: "busy", value: true });
  h.post({ type: "attachments", items: [{ id: "a1", label: "file.ts", type: "file" }] });
  const input = h.document.getElementById("input");
  input.value = "draft I never sent";
  await h.settle(20);

  // Go back to history while the turn is still running.
  h.document.getElementById("history-btn").click();
  await h.settle(20);

  assert.ok(h.posted.some((m) => m.type === "leaveToList"), "should ask the host to cancel/detach");
  assert.strictEqual(input.value, "", "the draft should be cleared for a clean new-chat box");
  assert.ok(
    h.document.getElementById("composer").classList.contains("list-mode"),
    "the composer should switch to list mode"
  );
  assert.strictEqual(
    h.document.getElementById("attachments").children.length,
    0,
    "attachment pills from the old session should be cleared"
  );
  assert.ok(
    h.document.getElementById("stop").classList.contains("hidden"),
    "the Stop/working indicator should not persist in the list view"
  );
  assert.ok(
    !h.document.getElementById("sessions-list").classList.contains("hidden"),
    "the sessions list should be visible"
  );
  assert.strictEqual(h.errors().length, 0, "leaving to list threw: " + JSON.stringify(h.errors()));
});

test("session list shows liveness dots and offers take-over", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [
      { id: "aaa", short_id: "aaa", title: "Running one", working_directory: "/w" },
      { id: "bbb", short_id: "bbb", title: "Idle one", working_directory: "/w" },
      { id: "ccc", short_id: "ccc", title: "Dead one", working_directory: "/w" }
    ],
    activeId: "aaa",
    statuses: { aaa: "running", bbb: "idle" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(20);

  const cls = [...h.document.querySelectorAll("#sessions-list .session-dot")].map((d) => d.className);
  assert.ok(cls.some((c) => c.includes("dot-running")), "a running (green) dot");
  assert.ok(cls.some((c) => c.includes("dot-idle")), "an idle (amber) dot");
  assert.ok(cls.some((c) => c.includes("dot-dead")), "a dead (gray) dot for the session with no status");

  // Live sessions offer a terminate action; dead ones do not.
  const rows = [...h.document.querySelectorAll("#sessions-list .session-item")];
  const terminate = rows[0].querySelector(".session-actions .kill-glyph");
  assert.ok(terminate, "a live session row has a terminate (kill) button");
  assert.ok(!rows[2].querySelector(".kill-glyph"), "a dead session row has no terminate button");
  terminate.parentElement.click();
  await h.settle(5);
  assert.ok(
    h.posted.some((m) => m.type === "terminateSession" && m.id === "aaa" && m.title === "Running one" && !m.returnToList),
    "row terminate posts terminateSession with the title and no returnToList"
  );

  // A locked session offers take-over, which posts the decision back.
  h.post({ type: "lockConflict", requestId: "lock-1", id: "aaa", pid: 4242 });
  await h.settle(10);
  const takeover = [...h.document.querySelectorAll("#permission-tray button")].find((b) => b.textContent === "Take over");
  assert.ok(takeover, "a Take over button renders");
  takeover.click();
  await h.settle(10);
  const decision = h.posted.find((m) => m.type === "takeoverDecision");
  assert.ok(decision && decision.decision === "takeover" && decision.requestId === "lock-1", "take-over decision posted");
  assert.strictEqual(h.errors().length, 0, "status/lock handling threw: " + JSON.stringify(h.errors()));
});

test("returning to an idle session restores its transcript without reloading", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "hello from A" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "hi A" });
  h.post({ type: "assistantEnd" });
  await h.settle(20);
  assert.deepStrictEqual(h.reqTexts(), ["hello from A"]);

  // Go back to the list (session A is snapshotted, kept alive in the background).
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: { A: "idle" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(10);

  const before = h.posted.filter((m) => m.type === "loadSession").length;
  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);

  assert.ok(h.posted.some((m) => m.type === "activateSession" && m.id === "A"), "activate without reload");
  assert.strictEqual(
    h.posted.filter((m) => m.type === "loadSession").length,
    before,
    "an idle, unchanged session must not be reloaded"
  );
  assert.deepStrictEqual(h.reqTexts(), ["hello from A"], "the transcript is restored, not rebuilt");
  assert.strictEqual(h.errors().length, 0, "restore threw: " + JSON.stringify(h.errors()));
});

test("a changed, now-idle session is reloaded, not restored", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "hello from A" });
  h.post({ type: "assistantEnd" });
  await h.settle(20);
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  // A changed while backgrounded and has since gone idle: its snapshot is stale,
  // and reloading an idle runtime is safe.
  h.post({ type: "sessionActivity", id: "A" });
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: { A: "idle" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(10);
  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "loadSession" && m.id === "A"), "changed idle session reloads");
  assert.strictEqual(h.errors().length, 0);
});

test("a chat left while it was still opening is reloaded, not restored half rendered", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  // A full load: the spinner is up and the history streams in behind it.
  h.post({ type: "clear", loading: true });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "sessionStatuses", statuses: { A: "starting" }, activeId: "A" });
  h.post({ type: "userChunk", text: "the first of several turns" });
  await h.settle(20);

  // Left before the replay finished, so what is cached is a part of the transcript
  // with the spinner still in it, and the rest of the replay is dropped: the host
  // stops painting for a chat that is no longer on screen.
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: { A: "idle" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(10);

  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "loadSession" && m.id === "A"),
    "it loads the chat again rather than showing what it managed to render"
  );
  assert.ok(
    !h.posted.some((m) => m.type === "activateSession" && m.id === "A"),
    "and does not just re-point at it"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("returning to a session with a turn in flight re-attaches, never reloads", async () => {
  // Reloading a running session over its live channel aborts the prompt
  // ("Agent communication channel closed"), so a running session must activate.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", pendingSend: true });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "hello from A" });
  h.post({ type: "assistantStart" });
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: "A",
    statuses: { A: "running" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(20);
  // Leave to the list mid-turn, then reopen while still running.
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: { A: "running" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(10);
  const loadsBefore = h.posted.filter((m) => m.type === "loadSession").length;
  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "activateSession" && m.id === "A"), "running session re-attaches");
  assert.strictEqual(
    h.posted.filter((m) => m.type === "loadSession").length,
    loadsBefore,
    "a running session must not be reloaded"
  );
  assert.deepStrictEqual(h.reqTexts(), ["hello from A"], "the user message is preserved and stays first");
  assert.strictEqual(h.errors().length, 0);
});

test("reselecting a terminated session restores its transcript and wakes silently", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "hello from A" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "hi A" });
  h.post({ type: "assistantEnd" });
  await h.settle(20);
  assert.deepStrictEqual(h.reqTexts(), ["hello from A"]);

  // Terminate returns us to the list (host-driven body:list) and the session is
  // now dead (no live status).
  h.post({ type: "body", body: "list" });
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: {},
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(10);

  const loadsBefore = h.posted.filter((m) => m.type === "loadSession").length;
  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);

  assert.ok(h.posted.some((m) => m.type === "wakeSession" && m.id === "A"), "a dead session wakes in the background");
  assert.strictEqual(
    h.posted.filter((m) => m.type === "loadSession").length,
    loadsBefore,
    "no full reload (so no 'Waking…' spinner replaces the thread)"
  );
  assert.strictEqual(h.document.querySelectorAll(".thread-loading").length, 0, "no loading spinner is shown");
  assert.deepStrictEqual(h.reqTexts(), ["hello from A"], "the transcript stays on screen");
  assert.strictEqual(h.errors().length, 0);
});

test("renders response images and inline keep/undo on edits", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "take a screenshot and edit a file" });
  h.post({ type: "assistantStart" });
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  h.post({ type: "assistantImage", mime: "image/png", data: px });
  h.post({ type: "fileChange", path: "/w/app.ts", added: 3, removed: 1 });
  h.post({ type: "assistantEnd" });
  await h.settle(20);

  const img = h.document.querySelector("#thread .dv-thumb");
  assert.ok(img && img.src.startsWith("data:image/png;base64,"), "response image renders");
  // A picture opens to full size in place, rather than only ever being a thumbnail.
  img.click();
  await h.settle(5);
  assert.ok(img.classList.contains("expanded"), "and can be opened up");

  const pill = h.document.querySelector("#thread .edit-pill");
  assert.ok(pill, "edit pill renders");
  const keep = pill.querySelector(".edit-pill-actions .codicon-check");
  assert.ok(keep, "inline Keep action renders");
  keep.parentElement.click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "acceptFile" && m.path === "/w/app.ts"), "Keep posts acceptFile");
  h.post({ type: "changesResolved", paths: ["/w/app.ts"], action: "accept" });
  await h.settle(5);
  assert.ok(pill.classList.contains("resolved"), "pill marks resolved once the host says so");
  assert.strictEqual(h.errors().length, 0, "image/edit rendering threw: " + JSON.stringify(h.errors()));
});

test("web search, fetch, and MCP tools render distinctly via _meta", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "research" });
  h.post({ type: "assistantStart" });

  // Web search reports the coarse kind "fetch" but is identified via _meta.
  h.post({ type: "toolCall", id: "s1", title: "Searched web for node lts", kind: "fetch",
    meta: { inferenceToolName: "web_search" }, status: "pending", rawInput: { query: "node lts" } });
  h.post({ type: "toolCallUpdate", id: "s1", status: "completed", meta: { inferenceToolName: "web_search" },
    content: [{ type: "text", text: 'Found 5 result(s) for "node lts"' }] });

  // Web fetch: clickable URL.
  h.post({ type: "toolCall", id: "f1", title: "Fetched https://example.com", kind: "fetch",
    meta: { inferenceToolName: "webfetch" }, status: "pending", rawInput: { url: "https://example.com" } });
  h.post({ type: "toolCallUpdate", id: "f1", status: "completed", meta: { inferenceToolName: "webfetch" },
    content: [{ type: "text", text: "Fetched 176 characters from https://example.com" }] });

  // MCP tool call with a JSON result.
  h.post({ type: "toolCall", id: "m1", title: "Calling get_current_time from time",
    meta: { eventType: "mcp_tool_call", toolName: "mcp__time__get_current_time", inferenceToolName: "mcp__time__get_current_time" },
    status: "pending", rawInput: { timezone: "UTC" } });
  h.post({ type: "toolCallUpdate", id: "m1", status: "completed",
    meta: { eventType: "mcp_tool_call", toolName: "mcp__time__get_current_time" },
    content: [{ type: "text", text: '{\n  "timezone": "UTC",\n  "datetime": "2026-01-01T00:00:00+00:00"\n}' }] });

  await h.settle(20);

  const tools = [...h.document.querySelectorAll("#thread .tool")];
  const search = tools.find((t) => /Searched web/.test(t.textContent));
  assert.ok(search, "search tool renders");
  assert.ok(search.querySelector(".tool-kind.codicon-globe"), "search uses the globe icon");
  assert.ok(search.querySelector(".tool-summary-label"), "search shows a summary line, not raw JSON");
  assert.ok(search.querySelector(".tool-result-note"), "search shows a dim result caption");

  const fetchT = tools.find((t) => /Fetched https/.test(t.textContent));
  assert.ok(fetchT && fetchT.querySelector("a.tool-summary-value"), "fetch renders a clickable URL");

  const mcp = tools.find((t) => /Calling get_current_time/.test(t.textContent));
  assert.ok(mcp && mcp.querySelector(".tool-kind.codicon-mcp"), "mcp uses the VS Code MCP icon");
  assert.ok(
    [...mcp.querySelectorAll(".tool-section-title")].some((e) => e.textContent === "Arguments"),
    "mcp shows an Arguments section"
  );
  assert.ok(mcp.querySelector(".tool-pre.hljs"), "mcp JSON result is highlighted");

  assert.strictEqual(h.errors().length, 0, "tool rendering threw: " + JSON.stringify(h.errors()));
});

test("consecutive tool calls collapse into a grouped disclosure", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "explore" });
  h.post({ type: "assistantStart" });

  for (const id of ["t1", "t2", "t3"]) {
    h.post({ type: "toolCall", id, title: "Read src/" + id + ".ts", kind: "read", status: "pending" });
    h.post({ type: "toolCallUpdate", id, status: "completed" });
  }
  await h.settle(10);

  let groups = [...h.document.querySelectorAll("#thread .tool-group")];
  assert.strictEqual(groups.length, 1, "the three tools form a single group");
  assert.strictEqual(
    groups[0].querySelectorAll(".tool-group-body > .tool").length,
    3,
    "all three tools nest inside the group"
  );
  assert.ok(/^Read 3 files/.test(groups[0].textContent), "the group says what the run did");
  assert.ok(!groups[0].classList.contains("running"), "a finished group is not marked running");

  // Assistant text ends the run; a following tool starts fresh and ungrouped.
  h.post({ type: "assistantChunk", text: "done reading" });
  h.post({ type: "toolCall", id: "t4", title: "Read src/final.ts", kind: "read", status: "completed" });
  await h.settle(10);

  groups = [...h.document.querySelectorAll("#thread .tool-group")];
  assert.strictEqual(groups.length, 1, "the lone post-text tool does not form a new group");
  const t4 = [...h.document.querySelectorAll("#thread .tool")].find((t) => /final\.ts/.test(t.textContent));
  assert.ok(t4 && !t4.closest(".tool-group"), "a lone tool after text stays ungrouped");
  assert.strictEqual(h.errors().length, 0, "grouping threw: " + JSON.stringify(h.errors()));
});

test("a subagent nests its prompt, tools, output and report on one timeline", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, subagentControl: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "delegate this" });
  h.post({ type: "assistantStart" });

  h.post({ type: "subagentStart", id: "sa1", profile: "Explore", background: true,
    title: "Map session persistence", task: "Work out how sessions persist.\n\nStart at sessionStore.ts." });
  h.post({ type: "subagentChunk", parentId: "sa1", stream: "thought", text: "Reading the store first." });
  h.post({ type: "subagentChunk", parentId: "sa1", stream: "message", text: "See [client.ts](src/client.ts)." });
  h.post({ type: "toolCall", id: "sa1-t1", parentId: "sa1", title: "Read src/session/sessionStore.ts", kind: "read", status: "pending" });
  h.post({ type: "toolCallUpdate", id: "sa1-t1", parentId: "sa1", status: "in_progress" });
  await h.settle(15);

  const sub = h.document.querySelector("#thread .subagent");
  assert.ok(sub, "the subagent block renders");
  assert.ok(sub.classList.contains("subagent-active"), "a running subagent is marked active");
  assert.ok(sub.classList.contains("dv-collapsed"), "it starts collapsed, like VS Code");
  assert.strictEqual(sub.querySelector(".subagent-title").textContent, "Explore: Map session persistence",
    "the header is the capitalised profile then the task");
  assert.ok(sub.querySelector(".dv-collapsible-header > .subagent-glyph.codicon-hubot"),
    "the row leads with the agent glyph");
  assert.strictEqual(sub.querySelector(".subagent-detail").textContent, " \u2014 Read src/session/sessionStore.ts",
    "the running tool is appended to the header");
  assert.ok(sub.querySelector(".subagent-item.subagent-prompt"), "the prompt opens the timeline");
  assert.ok(sub.querySelector(".subagent-item.subagent-thought"), "its reasoning is on the timeline");
  const subLink = sub.querySelector(".subagent-prose-content a");
  assert.ok(subLink && subLink.classList.contains("file-pill"), "subagent file references use the file pill");
  assert.ok(subLink.querySelector(".file-pill-icon"), "subagent file references use the file icon");
  subLink.click();
  subLink.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  subLink.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: " ", bubbles: true }));
  assert.ok(h.posted.some((m) => m.type === "openFile" && m.path === "src/client.ts"),
    "subagent file references open from mouse and keyboard");
  h.post({ type: "capabilities", inlineReferencesStyle: "link" });
  await h.settle(5);
  assert.ok(!subLink.classList.contains("file-pill"), "link mode updates subagent references");
  h.post({ type: "capabilities", inlineReferencesStyle: "box" });
  await h.settle(5);
  assert.ok(subLink.classList.contains("file-pill"), "box mode restores subagent references");
  assert.ok(sub.querySelector(".subagent-item.subagent-tool > .tool"), "its tool call nests as a row");
  assert.ok(sub.querySelector(".subagent-item.subagent-spinner"), "a working row shows while it runs");
  assert.strictEqual(h.document.querySelectorAll("#thread > .turn .tool-group").length, 0,
    "a subagent's tools do not join the turn's tool run");

  // The tool it was naming finishes and it thinks again: the header has to move
  // on with it, or a working subagent reads as stuck on the last thing it ran.
  const working = /^ \u2014 (Processing|Preparing|Loading|Analyzing|Evaluating|Thinking|Reasoning|Considering|Working|Executing|Running)$/;
  h.post({ type: "toolCallUpdate", id: "sa1-t1", parentId: "sa1", status: "completed" });
  await h.settle(10);
  assert.match(sub.querySelector(".subagent-detail").textContent, working,
    "with no tool running it says it is working, not which tool it last ran");
  h.post({ type: "subagentChunk", parentId: "sa1", stream: "thought", text: "Now the write path." });
  await h.settle(10);
  assert.match(sub.querySelector(".subagent-detail").textContent, working);
  assert.strictEqual(
    sub.querySelector(".subagent-spinner .subagent-spinner-label").textContent,
    sub.querySelector(".subagent-detail").textContent.replace(" \u2014 ", ""),
    "and its own working row says the same thing"
  );

  // The control flips optimistically and tells the host.
  sub.querySelector(".subagent-action").dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "subagentMode" && m.id === "sa1" && m.background === false),
    "the action asks the host to bring it to the foreground");
  assert.ok(sub.classList.contains("dv-collapsed"), "using the control does not toggle the disclosure");

  h.post({ type: "subagentEnd", id: "sa1", success: true, summary: "Sessions live in workspace state.\n\nUnder a single key." });
  await h.settle(15);

  assert.ok(!sub.classList.contains("subagent-active"), "a finished subagent is no longer active");
  assert.strictEqual(sub.querySelector(".subagent-detail").textContent, "", "the tool suffix is dropped when done");
  assert.ok(!sub.querySelector(".subagent-spinner"), "the working row is removed");
  const result = sub.querySelector(".subagent-item.subagent-result");
  assert.ok(result, "the report closes the timeline");
  assert.strictEqual(result.querySelector(".subagent-section-label").textContent, "Sessions live in workspace state.",
    "the report is titled with its first line");
  assert.ok(!sub.querySelector(".subagent-action"), "the controls go once it is no longer running");
  assert.strictEqual(h.errors().length, 0, "subagent rendering threw: " + JSON.stringify(h.errors()));
});

test("a turn ending settles a foreground subagent but leaves a background one running", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "delegate" });
  h.post({ type: "busy", value: true });
  h.post({ type: "subagentStart", id: "fg", profile: "Explore", title: "Look around", background: false });
  h.post({ type: "subagentStart", id: "bg", profile: "Explore", title: "Keep digging", background: true });
  await h.settle(10);
  h.post({ type: "busy", value: false });
  await h.settle(10);

  const subs = [...h.document.querySelectorAll("#thread .subagent")];
  const fg = subs.find((s) => /Look around/.test(s.textContent));
  const bg = subs.find((s) => /Keep digging/.test(s.textContent));
  assert.ok(fg && !fg.classList.contains("subagent-active"), "an interrupted foreground subagent stops shimmering");
  assert.ok(!fg.querySelector(".subagent-spinner"), "and drops its working row");
  assert.ok(bg.classList.contains("subagent-active"), "a background subagent keeps working past the turn");

  // Its report can land long after the turn that spawned it ended.
  h.post({ type: "subagentEnd", id: "bg", success: true, summary: "Found it.\n\nIn the store." });
  await h.settle(10);
  assert.ok(!bg.classList.contains("subagent-active"), "the late report settles it");
  assert.ok(bg.querySelector(".subagent-result"), "and is still rendered");
  assert.strictEqual(h.errors().length, 0, "finalising threw: " + JSON.stringify(h.errors()));
});

test("a subagent tool with no known parent still renders in the thread", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "replay" });
  h.post({ type: "assistantStart" });
  // A history replay can carry the tagged tool without the lifecycle that made
  // it, so the tool must fall back to the top level rather than vanish.
  h.post({ type: "toolCall", id: "orphan", parentId: "gone", title: "Read src/a.ts", kind: "read", status: "completed" });
  await h.settle(10);

  const tool = h.document.querySelector("#thread .tool");
  assert.ok(tool && !tool.closest(".subagent"), "an orphaned subagent tool renders at the top level");
  assert.strictEqual(h.errors().length, 0, "orphan rendering threw: " + JSON.stringify(h.errors()));
});

test("terminating the open session uses the kill glyph and asks to return to the list", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionStatuses", statuses: { S1: "running" }, activeId: "S1" });
  h.post({ type: "sessionReady", sessionId: "S1" });
  h.post({ type: "userMessage", text: "hi" });
  await h.settle(10);

  const term = h.document.getElementById("terminate-btn");
  assert.ok(!term.classList.contains("hidden"), "header terminate shows for a live open session");
  assert.ok(term.querySelector(".kill-glyph"), "header terminate uses the power/kill glyph, not a codicon");
  term.click();
  await h.settle(5);
  assert.ok(
    h.posted.some((m) => m.type === "terminateSession" && m.id === "S1" && m.returnToList === true),
    "header terminate posts returnToList so the host returns to the sessions list"
  );
  assert.strictEqual(h.errors().length, 0, "header terminate threw: " + JSON.stringify(h.errors()));
});

test("mermaid fences render as a source block (upgraded lazily)", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "draw" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "A diagram:\n\n```mermaid\ngraph TD; A-->B;\n```\n" });
  h.post({ type: "assistantEnd" });
  await h.settle(20);

  const pre = h.document.querySelector("#thread .resp-text pre.mermaid-src");
  assert.ok(pre, "mermaid fence renders as a mermaid-src block");
  assert.ok(/graph TD/.test(pre.textContent), "the mermaid source is preserved");
  assert.ok(!pre.querySelector(".code-toolbar"), "mermaid source is not given a code toolbar");
  assert.ok(!pre.parentElement.classList.contains("code-block-wrap"), "and is not wrapped for one");
  // No data-mermaid-src is set in the harness, so the lazy load no-ops; the
  // source block must simply survive without throwing.
  assert.strictEqual(h.errors().length, 0, "mermaid rendering threw: " + JSON.stringify(h.errors()));
});

test("mermaid is told to keep its errors to itself", async () => {
  // A diagram it cannot parse draws a bomb and "Syntax error in text" into a
  // node it hangs off document.body, and leaves it there: outside the
  // transcript, under the whole panel. Suppressing that is what makes it throw
  // instead, which is how the source block survives in its place.
  const bundle = fs.readFileSync(path.join(ROOT, "dist", "webview.js"), "utf8");
  assert.ok(
    /suppressErrorRendering\s*:\s*!0|suppressErrorRendering\s*:\s*true/.test(bundle),
    "mermaid must be initialised with suppressErrorRendering"
  );
});

// Helper: two completed live turns with known heads.
function twoLiveTurns(h) {
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "sessionReady", sessionId: "S" });
  h.post({ type: "userMessage", text: "first" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a1" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 10, reliable: true });
  h.post({ type: "busy", value: false });
  h.post({ type: "userMessage", text: "second" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a2" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 20, reliable: true });
  h.post({ type: "busy", value: false });
}

test("a second Enter while an edit is being checked does not send it twice", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true, confirmRemoval: true });
  h.post({ type: "sessionReady", sessionId: "S" });
  h.post({ type: "userMessage", text: "first" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a1" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 10, reliable: true });
  h.post({ type: "busy", value: false });
  h.post({ type: "userMessage", text: "second" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a2" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 20, reliable: true });
  h.post({ type: "busy", value: false });
  await h.settle(20);

  const reqBodies = [...h.document.querySelectorAll("#thread .turn-request .req-body")];
  reqBodies[1].click();
  await h.settle(5);
  const ta = h.document.querySelector(".req-editor-input");
  assert.ok(ta, "the editor is open");
  ta.value = "second, but better";

  // Submitting asks the host what the rewind would discard first, and nothing about
  // the editor changes while that answer is on its way: the text is still there and
  // the editor is still open, so a user who saw nothing happen presses Enter again.
  const enter = () => ta.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  enter();
  enter();
  await h.settle(10);

  for (const asked of h.posted.filter((m) => m.type === "revertPreview")) {
    h.post({
      type: "revertPreview",
      token: asked.token,
      head: asked.head,
      result: { fileActions: [], irreversibleWarnings: [], conflicts: [] },
      pendingFiles: 0
    });
  }
  await h.settle(20);

  // Two rewinds on one channel is the edited prompt sent twice, and the second
  // landing under the resend of the first.
  assert.strictEqual(
    h.posted.filter((m) => m.type === "revertExecute").length,
    1,
    "the edit is sent once"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("clearing a transcript cancels its pending revert preview timer", async () => {
  const h = createHarness();
  const setTimeout = h.window.setTimeout.bind(h.window);
  const clearTimeout = h.window.clearTimeout.bind(h.window);
  const timer = {};
  let cleared = false;
  h.window.setTimeout = (fn, delay, ...args) => delay === 4000 ? timer : setTimeout(fn, delay, ...args);
  h.window.clearTimeout = (value) => {
    if (value === timer) cleared = true;
    else clearTimeout(value);
  };
  try {
    h.post({ type: "ready" });
    h.post({ type: "body", body: "thread" });
    h.post({ type: "clear" });
    h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true, confirmRemoval: true });
    h.post({ type: "sessionReady", sessionId: "S" });
    h.post({ type: "userMessage", text: "first" });
    h.post({ type: "assistantStart" }); h.post({ type: "assistantEnd" });
    h.post({ type: "turnHead", head: 10, reliable: true });
    h.post({ type: "busy", value: false });
    h.post({ type: "userMessage", text: "second" });
    h.post({ type: "assistantStart" }); h.post({ type: "assistantEnd" });
    h.post({ type: "turnHead", head: 20, reliable: true });
    h.post({ type: "busy", value: false });
    await h.settle(10);
    h.document.querySelectorAll("#thread .turn-request .req-body")[1].click();
    await h.settle(5);
    const input = h.document.querySelector(".req-editor-input");
    input.value = "revised";
    input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await h.settle(5);
    assert.strictEqual(h.posted.filter((message) => message.type === "revertPreview").length, 1);

    h.post({ type: "clear" });

    assert.strictEqual(cleared, true);
  } finally {
    h.window.setTimeout = setTimeout;
    h.window.clearTimeout = clearTimeout;
  }
});

test("a delayed revert preview cannot execute against a newly selected session", async () => {
  const h = createHarness();
  twoLiveTurns(h);
  await h.settle(10);
  h.document.querySelectorAll("#thread .turn-request .req-body")[1].click();
  await h.settle(5);
  const input = h.document.querySelector(".req-editor-input");
  input.value = "revised";
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await h.settle(5);
  const preview = h.posted.find((message) => message.type === "revertPreview");
  assert.ok(preview);

  h.post({ type: "sessionReady", sessionId: "other" });
  h.post({ type: "revertPreview", token: preview.token, head: preview.head, result: { fileActions: [], irreversibleWarnings: [] }, pendingFiles: 0 });
  await h.settle(20);

  assert.strictEqual(h.posted.filter((message) => message.type === "revertExecute").length, 0);
});

test("only one inline request edits at a time and Cancel closes it", async () => {
  const h = createHarness();
  twoLiveTurns(h);
  await h.settle(20);

  const reqBodies = [...h.document.querySelectorAll("#thread .turn-request .req-body")];
  assert.strictEqual(reqBodies.length, 2, "two request rows");

  reqBodies[0].click();
  await h.settle(5);
  assert.strictEqual(h.document.querySelectorAll(".req-editor").length, 1, "one editor opens");

  // Opening a second edit closes the first (single editable request).
  reqBodies[1].click();
  await h.settle(5);
  assert.strictEqual(h.document.querySelectorAll(".req-editor").length, 1, "still exactly one editor open");

  const cancel = [...h.document.querySelectorAll(".req-editor button")].find((b) => b.textContent === "Cancel");
  assert.ok(cancel, "Cancel button present");
  cancel.click();
  await h.settle(5);
  assert.strictEqual(h.document.querySelectorAll(".req-editor").length, 0, "Cancel closes the editor and it does not reopen");
  assert.strictEqual(h.errors().length, 0, "edit flow threw: " + JSON.stringify(h.errors()));
});

test("Retry shows only on the last response", async () => {
  const h = createHarness();
  twoLiveTurns(h);
  await h.settle(20);

  const footers = [...h.document.querySelectorAll("#thread .chat-footer")];
  assert.strictEqual(footers.length, 2, "two response footers");
  const lastFooters = footers.filter((f) => f.classList.contains("is-last"));
  assert.strictEqual(lastFooters.length, 1, "exactly one footer is marked last");
  assert.ok(footers[footers.length - 1].classList.contains("is-last"), "the last turn's footer carries Retry");
  assert.ok(!footers[0].classList.contains("is-last"), "the earlier turn's footer does not");
  assert.strictEqual(h.errors().length, 0);
});

test("reloaded turns are only revertable once a reliable (on-expansion) head is known", async () => {
  // The agent re-expands the conversation on load, orphaning any pre-load node
  // id. So historical turns and the FIRST new turn after a reload must NOT be
  // revertable (reverting would hit an orphaned node -> "Invalid params"); a
  // later turn, whose head-before was captured live, is revertable.
  const editableTexts = () =>
    [...h.document.querySelectorAll("#thread .turn-request.editable-inline .req-text")].map((e) => e.textContent.trim());

  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  // Replay history, then the host reports the load head as NOT reliable.
  h.post({ type: "userChunk", text: "old q" });
  h.post({ type: "assistantChunk", text: "old a" });
  h.post({ type: "loaded" });
  h.post({ type: "turnHead", head: 27, reliable: false });
  await h.settle(20);
  assert.deepStrictEqual(editableTexts(), [], "historical turn is not editable");
  assert.strictEqual(h.document.querySelectorAll("#thread .footer-retry").length, 0, "no Retry on history");

  // First new turn: head-before is the unreliable load head -> not revertable.
  h.post({ type: "userMessage", text: "new q1" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a1" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 64, reliable: true });
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.deepStrictEqual(editableTexts(), [], "the first turn after a reload is not revertable");

  // Second new turn: head-before (64) was captured live -> revertable.
  h.post({ type: "userMessage", text: "new q2" });
  h.post({ type: "assistantStart" }); h.post({ type: "assistantChunk", text: "a2" }); h.post({ type: "assistantEnd" });
  h.post({ type: "turnHead", head: 67, reliable: true });
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.deepStrictEqual(editableTexts(), ["new q2"], "only the second new turn (reliable head) is editable");
  assert.strictEqual(h.document.querySelectorAll("#thread .footer-retry").length, 1, "Retry only on the revertable last turn");
  assert.strictEqual(h.errors().length, 0, "reliability handling threw: " + JSON.stringify(h.errors()));
});

test("starting a new chat from the list does not flash the welcome screen", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "capabilities", revert: true });

  // The host clears with pendingSend and then renders the message: no welcome
  // should appear in the gap while the ACP session spins up.
  h.post({ type: "clear", pendingSend: true });
  await h.settle(10);
  assert.strictEqual(
    h.document.querySelectorAll("#thread .welcome").length,
    0,
    "no welcome during a pending new-chat send"
  );
  h.post({ type: "userMessage", text: "hello new chat" });
  await h.settle(10);
  assert.strictEqual(h.document.querySelectorAll("#thread .welcome").length, 0, "still no welcome after the message renders");
  assert.deepStrictEqual(h.reqTexts(), ["hello new chat"]);

  // A normal clear (no pending send) still shows the welcome.
  h.post({ type: "clear" });
  await h.settle(10);
  assert.ok(h.document.querySelector("#thread .welcome"), "a plain clear still shows the welcome");
  assert.strictEqual(h.errors().length, 0);
});

test("a freshly sent message does not flash the Copy/Retry footer", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, editRequests: "inline", checkpoints: true });
  h.post({ type: "sessionReady", sessionId: "S" });
  h.post({ type: "userMessage", text: "hello" });
  await h.settle(10);

  // Before the response completes the turn must not be "complete" (its footer
  // is hidden by CSS while not complete), so no Copy/Retry flashes.
  const turn = h.document.querySelector("#thread .turn");
  assert.ok(turn, "turn exists");
  assert.ok(!turn.classList.contains("complete"), "the in-flight turn is not marked complete");

  // After the response finishes it becomes complete and the footer appears.
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "hi" });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(10);
  assert.ok(turn.classList.contains("complete"), "the turn is complete after the response");
  assert.strictEqual(h.errors().length, 0, "send flow threw: " + JSON.stringify(h.errors()));
});

test("Copy is hidden when the response is empty", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "S" });

  // A turn that produced no assistant output.
  h.post({ type: "userMessage", text: "hi" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(10);
  const emptyFooter = h.document.querySelector("#thread .chat-footer");
  assert.ok(emptyFooter, "footer exists");
  assert.strictEqual(emptyFooter.querySelectorAll(".dv-copy").length, 0, "no Copy button on an empty response");

  // A turn with output shows Copy.
  h.post({ type: "userMessage", text: "again" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "here is an answer" });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(10);
  const footers = [...h.document.querySelectorAll("#thread .chat-footer")];
  assert.strictEqual(footers[footers.length - 1].querySelectorAll(".dv-copy").length, 1, "Copy shows when there is a response");
  assert.strictEqual(h.errors().length, 0);
});

test("stopping the turn closes an open question widget", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({
    type: "elicitation", requestId: "e1", mode: "form", message: "Pick",
    schema: { type: "object", required: ["q0"], properties: { q0: { type: "string", title: "Q0", oneOf: [{ const: "A", title: "A" }, { const: "B", title: "B" }] } } }
  });
  await h.settle(10);
  assert.ok(h.document.querySelector("#elicitation-tray .qc"), "the question widget is shown");

  // The host reports the request was cancelled (Stop, or leaving the turn).
  h.post({ type: "cancelPrompts" });
  await h.settle(10);
  assert.strictEqual(h.document.querySelectorAll("#elicitation-tray .qc").length, 0, "the question widget is closed on cancel");
  assert.strictEqual(h.errors().length, 0);
});

test("per-turn edit/restore chrome builds while busy (no throw)", async () => {
  const h = createHarness();
  h.replay([{ role: "user", text: "q" }, { role: "assistant", text: "a" }]);
  // Toggling busy rebuilds every turn's chrome (canEditTurn/canRestoreTurn read `busy`).
  h.post({ type: "busy", value: true });
  h.post({ type: "busy", value: false });
  await h.settle();

  assert.strictEqual(h.errors().length, 0, "toggling busy threw: " + JSON.stringify(h.errors()));
});

test("queued messages render as bubbles, edit in place keeps position, remove drops them", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "queued", items: [{ id: "q1", text: "first queued" }, { id: "q2", text: "second queued" }] });
  await h.settle(20);

  const box = h.document.querySelector("#thread .queued-inline");
  assert.ok(box, "the queued block renders at the bottom of the transcript");
  assert.ok(box.querySelector(".queued-divider"), "it has a Queued divider");
  const rows = box.querySelectorAll(".queued-item");
  assert.strictEqual(rows.length, 2, "one bubble per queued message");
  assert.strictEqual(box.querySelectorAll(".queued-bubble")[0].textContent, "first queued");

  // Edit the first: it loads into the composer for an in-place edit and is NOT
  // dropped from the queue (its slot is kept), so it cannot jump to the end.
  rows[0].querySelector(".queued-actions").querySelectorAll("button")[0].click();
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("input").value, "first queued", "edit loads text into the composer");
  assert.ok(!h.posted.some((m) => m.type === "removeQueued" && m.id === "q1"), "editing does not drop it from the queue");
  assert.ok(h.document.getElementById("input-editing-banner"), "an editing banner is shown");
  assert.ok(rows[0].classList.contains("editing"), "the edited bubble is marked");
  // The host is told which message is being edited, so only that one is held
  // back when it reaches the head (earlier ones keep sending).
  assert.ok(h.posted.some((m) => m.type === "queueEditing" && m.id === "q1"), "editing holds just that message");

  // Submitting the edit updates it in place (same position), not a fresh send.
  h.document.getElementById("input").value = "first edited";
  h.document.getElementById("input").dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await h.settle(10);
  const edit = h.posted.find((m) => m.type === "editQueued");
  assert.ok(edit && edit.id === "q1" && edit.text === "first edited", "submit updates the queued item in place");
  assert.ok(!h.posted.some((m) => m.type === "send"), "editing a queued item is not a fresh send");
  assert.ok(!h.document.getElementById("input-editing-banner"), "the editing banner clears after submit");
  // Committing the edit releases the hold so that message can be sent.
  assert.ok(h.posted.some((m) => m.type === "queueEditing" && m.id === null), "committing releases the hold");

  // Each bubble carries edit / send-immediately / remove, like VS Code's pending row.
  const actionBtns = rows[1].querySelector(".queued-actions").querySelectorAll("button");
  assert.strictEqual(actionBtns.length, 3, "edit, send immediately, and remove");

  // Send the second immediately: the host ends the turn in flight and drains
  // this one into the gap, which its hover says in VS Code's own words.
  assert.match(actionBtns[1].title, /^Cancel the current request and send this message immediately$/);
  actionBtns[1].click();
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "sendQueuedNow" && m.id === "q2"), "send immediately promotes it");

  // Remove the second: drops it.
  actionBtns[2].click();
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "removeQueued" && m.id === "q2"), "remove asks the host to drop it");

  // An empty update removes the block.
  h.post({ type: "queued", items: [] });
  await h.settle(10);
  assert.ok(!h.document.querySelector("#thread .queued-inline"), "an empty queue removes the block");
  assert.strictEqual(h.errors().length, 0);
});

test("session load hides the replay behind the spinner until it settles", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true });
  await h.settle(10);

  const thread = h.thread();
  assert.ok(thread.classList.contains("loading-replay"), "the transcript is hidden while replaying");
  assert.ok(thread.querySelector(".thread-loading"), "the loading spinner is shown");

  h.post({ type: "userChunk", text: "old question" });
  h.post({ type: "assistantChunk", text: "old answer" });
  await h.settle(10);
  // The replay is what the spinner is covering, so the first chunk of it must not
  // take the spinner away: every renderer clears the welcome screen, and the
  // spinner used to go with it, leaving a blank panel for the rest of the load.
  assert.ok(thread.querySelector(".thread-loading"), "the spinner stays for the whole replay");

  h.post({ type: "loaded" });
  await h.settle(10);

  assert.ok(!thread.classList.contains("loading-replay"), "the transcript is revealed once loaded");
  assert.ok(!thread.querySelector(".thread-loading"), "the spinner is gone once loaded");
  assert.deepStrictEqual(h.reqTexts(), ["old question"]);
  assert.strictEqual(h.errors().length, 0);
});

test("a backgrounded session waiting on input shows the attention dot", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [{ id: "aaa", short_id: "aaa", title: "Waiting one", working_directory: "/w" }],
    activeId: null,
    statuses: { aaa: "attention" }
  });
  await h.settle(20);

  const dot = h.document.querySelector("#sessions-list .session-dot");
  assert.ok(dot.className.includes("dot-attention"), "the attention dot is rendered");
  assert.strictEqual(dot.title, "Needs your input");
  // The row is still treated as live, so it keeps its terminate action.
  assert.ok(h.document.querySelector("#sessions-list .session-item .kill-glyph"), "an attention session is live");
  assert.strictEqual(h.errors().length, 0);
});

test("the nine most recent chats wear the number that opens them", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [
      { id: "aaa", short_id: "aaa", title: "Oldest", working_directory: "/w", last_activity_at: 100 },
      { id: "bbb", short_id: "bbb", title: "Newest", working_directory: "/w", last_activity_at: 300 },
      { id: "ccc", short_id: "ccc", title: "Middle", working_directory: "/w", last_activity_at: 200 }
    ],
    activeId: null,
    statuses: { aaa: "idle", bbb: "idle", ccc: "idle" },
    folders: [{ path: "/w", name: "w" }]
  });
  await h.settle(20);

  const keyOf = (code) => [...h.document.querySelectorAll("#sessions-list .session-item")]
    .find((r) => r.querySelector(".session-code").textContent === code)
    .querySelector(".session-key").textContent;
  // Numbered by recency, whatever order the rows come out in.
  assert.ok(/1$/.test(keyOf("bbb")), "the most recent chat is number 1");
  assert.ok(/2$/.test(keyOf("ccc")), "the next most recent is number 2");
  assert.ok(/3$/.test(keyOf("aaa")), "the oldest of the three is number 3");

  // The host's Ctrl/Cmd+2 opens the chat wearing 2.
  h.post({ type: "pickSession", index: 2 });
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "loadSession" && m.id === "ccc"), "Ctrl/Cmd+2 opens that chat");
  assert.strictEqual(h.errors().length, 0);
});

test("keyboard shortcuts: Ctrl/Cmd+Esc stops, ArrowUp recalls, Ctrl/Cmd+. opens pickers", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "userMessage", text: "recall me" });
  h.post({ type: "assistantStart" });
  h.post({ type: "assistantChunk", text: "working" });
  await h.settle(20);

  // Ctrl/Cmd+Esc while busy stops the turn.
  h.post({ type: "busy", value: true });
  await h.settle(5);
  h.document.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", metaKey: true, bubbles: true }));
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "cancel"), "Ctrl/Cmd+Esc asks the host to cancel");

  // A bare Escape from the composer stops it too, with nothing else open to close.
  h.posted.length = 0;
  h.document.getElementById("input").dispatchEvent(
    new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })
  );
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "cancel"), "Escape in the composer asks the host to cancel");
  h.post({ type: "busy", value: false });
  await h.settle(5);

  // Idle, Escape is not a stop: it must not fire a stray cancel.
  h.posted.length = 0;
  h.document.getElementById("input").dispatchEvent(
    new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true })
  );
  await h.settle(5);
  assert.deepStrictEqual(h.posted.filter((m) => m.type === "cancel"), [], "Escape while idle cancels nothing");

  // ArrowUp on an empty composer recalls the last message.
  const input = h.document.getElementById("input");
  input.value = "";
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
  await h.settle(5);
  assert.strictEqual(input.value, "recall me", "ArrowUp recalls the last message into the composer");

  assert.strictEqual(h.errors().length, 0);
});

test("leaving a working session and returning never shows two Working lines", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [
      { id: "aaa", short_id: "aaa", title: "A", working_directory: "/w" },
      { id: "bbb", short_id: "bbb", title: "B", working_directory: "/w" }
    ],
    activeId: null,
    statuses: { aaa: "idle", bbb: "idle" }
  });
  await h.settle(20);
  const openRow = (code) => [...h.document.querySelectorAll("#sessions-list .session-item")]
    .find((r) => r.querySelector(".session-code").textContent === code)
    .querySelector(".session-main").click();

  // Open A and leave it mid turn, with the "Working…" line on screen.
  openRow("aaa");
  h.post({ type: "sessionReady", sessionId: "aaa" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "assistantStart" });
  h.post({ type: "busy", value: true });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelectorAll(".working").length, 1, "one Working line while it runs");

  // Switch away and back (the transcript is retained), then send again.
  openRow("bbb");
  h.post({ type: "sessionReady", sessionId: "bbb" });
  await h.settle(10);
  openRow("aaa");
  await h.settle(10);
  h.post({ type: "userMessage", text: "second" });
  h.post({ type: "assistantStart" });
  await h.settle(20);

  assert.strictEqual(
    h.thread().querySelectorAll(".working").length,
    1,
    "the retained one is not left behind next to a new one"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("an unsent draft stays with its own session when switching", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [
      { id: "aaa", short_id: "aaa", title: "A", working_directory: "/w" },
      { id: "bbb", short_id: "bbb", title: "B", working_directory: "/w" }
    ],
    activeId: null,
    statuses: { aaa: "idle", bbb: "idle" }
  });
  await h.settle(20);
  const input = h.document.getElementById("input");
  const openRow = (code) => [...h.document.querySelectorAll("#sessions-list .session-item")]
    .find((r) => r.querySelector(".session-code").textContent === code)
    .querySelector(".session-main").click();

  // Open A, leave a draft, then switch to B: B must start clean.
  openRow("aaa");
  h.post({ type: "sessionReady", sessionId: "aaa" });
  await h.settle(10);
  input.value = "draft for A";
  openRow("bbb");
  h.post({ type: "sessionReady", sessionId: "bbb" });
  await h.settle(10);
  assert.strictEqual(input.value, "", "the draft does not follow into the other session");

  // Back to A: its own draft comes back.
  openRow("aaa");
  await h.settle(10);
  assert.strictEqual(input.value, "draft for A", "the session's own draft is restored");
  assert.strictEqual(h.errors().length, 0);
});

test("the status filter is multi select, offers Terminated, and can sort by state", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [
      { id: "run", short_id: "run", title: "Running one", working_directory: "/w", last_activity_at: 10 },
      { id: "idle", short_id: "idle", title: "Idle one", working_directory: "/w", last_activity_at: 30 },
      { id: "dead", short_id: "dead", title: "Dead one", working_directory: "/w", last_activity_at: 20 }
    ],
    activeId: null,
    statuses: { run: "running", idle: "idle" }
  });
  await h.settle(20);

  h.document.getElementById("list-filter-btn").click();
  await h.settle(10);
  const labels = [...h.document.querySelectorAll(".session-filter-menu .dv-menu-item span")].map((s) => s.textContent);
  assert.ok(labels.includes("Terminated"), "the dead state is called Terminated");
  assert.ok(!labels.includes("Ended"), "the old Ended label is gone");
  assert.ok(labels.includes("Last activity") && labels.includes("State"), "a Sort by group is offered");

  // Tick Running, then also Terminated: both are kept (multi select).
  const click = (text) => [...h.document.querySelectorAll(".session-filter-menu .dv-menu-item")]
    .find((r) => r.textContent.trim() === text).click();
  click("Running");
  await h.settle(10);
  let ids = [...h.document.querySelectorAll("#sessions-list .session-item .session-code")].map((e) => e.textContent);
  assert.deepStrictEqual(ids, ["run"], "only running shows");

  click("Terminated");
  await h.settle(10);
  ids = [...h.document.querySelectorAll("#sessions-list .session-item .session-code")].map((e) => e.textContent).sort();
  assert.deepStrictEqual(ids, ["dead", "run"], "both selected states show together");
  assert.strictEqual(h.errors().length, 0);
});

test("New Session is a split button: the label starts one, the chevron picks where", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "sessions", sessions: [], activeId: null, statuses: {} });
  await h.settle(20);

  const split = h.document.querySelector("#new-session-dd .new-session-split");
  assert.ok(split, "the control is a split button");
  const main = split.querySelector(".new-session-btn");
  const more = split.querySelector(".new-session-more");
  assert.strictEqual(main.textContent.trim(), "New Session");

  // The labelled half starts a session here, with no menu in the way.
  main.click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "newSessionAt" && m.target === "view"),
    "clicking the label starts a session in the panel"
  );
  assert.ok(!h.document.querySelector(".dv-menu"), "no menu is opened by the label");

  // The chevron opens the menu with the other places to open one, and only those:
  // the labelled half is already "here".
  more.click();
  await h.settle(10);
  const labels = [...h.document.querySelectorAll(".dv-menu .dv-menu-item span")].map((s) => s.textContent);
  assert.deepStrictEqual(labels, [
    "New Session (Editor)",
    "New Session (Window)",
    "New Vtx CLI Session (Terminal)"
  ]);
  assert.strictEqual(h.errors().length, 0);
});

test("the session list keeps itself live, with nothing to refresh by hand", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [{ id: "aaa", short_id: "aaa", title: "One", working_directory: "/w" }],
    activeId: null,
    statuses: {}
  });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("list-refresh-btn"), null, "no refresh button in the header");
  assert.ok(h.posted.some((m) => m.type === "listVisible" && m.value === true), "it says the list is on screen");

  // The switcher's own toolbar has no refresh either.
  h.post({ type: "body", body: "thread" });
  h.post({ type: "sessionReady", sessionId: "aaa" });
  await h.settle(10);
  const left = h.posted.filter((m) => m.type === "listVisible").pop();
  assert.strictEqual(left.value, false, "and says when it is not, so nothing is listed for no one");
  h.document.getElementById("panel-toggle").click();
  await h.settle(10);
  const tools = [...h.document.querySelectorAll("#title-menu .session-toolbar button")].map((b) => b.title);
  assert.deepStrictEqual(tools, ["New session", "New session in\u2026", "Search sessions", "Filter sessions"]);
  assert.strictEqual(h.posted.filter((m) => m.type === "listVisible").pop().value, true, "the switcher counts too");
  assert.strictEqual(h.errors().length, 0);
});

test("returning to the list keeps the cached rows and shows the top loading bar", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [{ id: "aaa", short_id: "aaa", title: "One", working_directory: "/w" }],
    activeId: null,
    statuses: {}
  });
  await h.settle(20);
  assert.strictEqual(h.document.querySelectorAll("#sessions-list .session-item").length, 1);

  const bar = h.document.querySelector("#body .dv-top-loading");
  assert.ok(bar, "the loading bar exists");
  assert.ok(bar.classList.contains("hidden"), "it is hidden when idle");

  // A revalidate must not blank the list: the cached rows stay, the bar runs.
  h.post({ type: "sessionsLoading" });
  await h.settle(10);
  assert.ok(!bar.classList.contains("hidden"), "the bar runs while revalidating");
  assert.strictEqual(
    h.document.querySelectorAll("#sessions-list .session-item").length,
    1,
    "the cached rows stay on screen instead of being replaced by a spinner"
  );

  h.post({ type: "sessions", sessions: [{ id: "aaa", short_id: "aaa", title: "One", working_directory: "/w" }], activeId: null, statuses: {} });
  await h.settle(10);
  assert.ok(bar.classList.contains("hidden"), "the bar stops once the listing arrives");
  assert.strictEqual(h.errors().length, 0);
});

test("a session load runs the top loading bar instead of a spinner", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true });
  await h.settle(10);

  const bar = h.document.querySelector("#body .dv-top-loading");
  assert.ok(!bar.classList.contains("hidden"), "the bar runs while the session loads");
  const label = h.thread().querySelector(".thread-loading");
  assert.ok(label, "a load label is shown");
  assert.strictEqual(label.querySelectorAll(".codicon-modifier-spin").length, 0, "no spinner icon");

  h.post({ type: "loaded" });
  await h.settle(10);
  assert.ok(bar.classList.contains("hidden"), "the bar stops once loaded");
  assert.strictEqual(h.errors().length, 0);
});

test("dragging shows the attach overlay and dropping a file URI attaches it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  const chatMain = h.document.getElementById("chat-main");
  const overlay = h.document.getElementById("chat").querySelector(".chat-dnd-overlay");
  assert.ok(overlay, "the drop overlay exists");

  // VS Code's own host script listens on the webview window and takes the drag
  // away the moment it sees one, so nothing may reach the window listeners.
  const host = [];
  for (const type of ["dragenter", "dragover", "drop"]) {
    h.window.addEventListener(type, (e) => host.push(e.type));
  }

  // Dragging a file over the chat reveals the overlay.
  const enter = new h.window.Event("dragenter", { bubbles: true, cancelable: true });
  Object.defineProperty(enter, "dataTransfer", { value: { types: ["Files"] } });
  chatMain.dispatchEvent(enter);
  assert.ok(overlay.classList.contains("visible"), "the overlay shows while dragging a file");

  // Dropping an internal file URI (Explorer / editor tab) attaches it by path.
  const drop = new h.window.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(drop, "dataTransfer", {
    value: {
      types: ["text/uri-list"],
      files: [],
      getData: (t) => (t === "text/uri-list" ? "file:///Users/me/src/app%20one.ts" : "")
    }
  });
  chatMain.dispatchEvent(drop);
  await h.settle(10);
  assert.ok(!overlay.classList.contains("visible"), "the overlay hides after the drop");
  assert.ok(
    h.posted.some((m) => m.type === "addMention" && m.path === "file:///Users/me/src/app%20one.ts"),
    "the dropped file is attached by its URI, which only the host can decode"
  );
  assert.deepStrictEqual(host, [], "no drag event escapes to VS Code's host listeners");
  assert.strictEqual(h.errors().length, 0);
});

test("a folder dropped from outside VS Code attaches as a listing, not silently nothing", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  // An OS drag gives no path for a folder, only an entry, so the top level is read
  // through the entries API. readEntries hands back one batch, then an empty one.
  let batches = [[{ name: "api.ts", isDirectory: false }, { name: "models", isDirectory: true }, { name: ".env", isDirectory: false }], []];
  const dir = {
    name: "orders",
    isDirectory: true,
    createReader: () => ({ readEntries: (cb) => cb(batches.shift() || []) })
  };
  const drop = new h.window.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(drop, "dataTransfer", {
    value: {
      types: ["Files"],
      files: [{ name: "orders" }],
      items: [{ kind: "file", webkitGetAsEntry: () => dir, getAsFile: () => ({ name: "orders" }) }],
      getData: () => ""
    }
  });
  h.document.getElementById("chat-main").dispatchEvent(drop);
  await h.settle(10);

  const folder = h.posted.find((m) => m.type === "attachDroppedFolder");
  assert.ok(folder, "the folder is attached rather than read as a file");
  assert.strictEqual(folder.name, "orders");
  assert.deepStrictEqual(folder.entries, ["api.ts", "models/"], "the listing marks folders and skips dotfiles");
  assert.ok(!h.posted.some((m) => m.type === "attachDroppedText"), "the folder is not also attached as text");
  assert.strictEqual(h.errors().length, 0);
});

test("an image the agent cannot decode is attached as a file, never as an image", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  // An svg sent as an image block has the whole request rejected, and the block
  // stays in the session, so every later message in that chat is rejected too.
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="8" height="8"/></svg>';
  const files = [
    new h.window.File([svg], "icon.svg", { type: "image/svg+xml" }),
    new h.window.File(["\u0089PNG"], "shot.png", { type: "image/png" })
  ];
  const drop = new h.window.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(drop, "dataTransfer", {
    value: {
      types: ["Files"],
      files,
      items: files.map((f) => ({ kind: "file", webkitGetAsEntry: () => null, getAsFile: () => f })),
      getData: () => ""
    }
  });
  h.document.getElementById("chat-main").dispatchEvent(drop);
  await h.until(() => h.posted.filter((m) => m.type === "attachImage" || m.type === "attachDroppedText").length === 2);

  const images = h.posted.filter((m) => m.type === "attachImage");
  assert.deepStrictEqual(images.map((m) => m.name), ["shot.png"], "only the png goes inline as an image");
  const text = h.posted.find((m) => m.type === "attachDroppedText");
  assert.ok(text, "the svg is attached as a file instead of being dropped");
  assert.strictEqual(text.name, "icon.svg");
  assert.ok(text.text.includes("<svg"), "its source is what gets attached");
  assert.strictEqual(h.errors().length, 0);
});

test("a pasted image the agent cannot decode is attached as a file too", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  const file = new h.window.File(["<svg xmlns='http://www.w3.org/2000/svg'/>"], "logo.svg", { type: "image/svg+xml" });
  const paste = new h.window.Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(paste, "clipboardData", {
    value: { items: [{ kind: "file", type: "image/svg+xml", getAsFile: () => file }] }
  });
  h.document.getElementById("input").dispatchEvent(paste);
  await h.until(() => h.posted.some((m) => m.type === "attachDroppedText"));

  assert.ok(!h.posted.some((m) => m.type === "attachImage"), "it does not go inline as an image");
  const text = h.posted.find((m) => m.type === "attachDroppedText");
  assert.ok(text, "the pasted svg is attached as a file");
  assert.strictEqual(text.name, "logo.svg");
  assert.strictEqual(h.errors().length, 0);
});

test("a drag anywhere in the panel, not just the chat column, is a drop target", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  const overlay = h.document.getElementById("chat").querySelector(".chat-dnd-overlay");
  const enter = new h.window.Event("dragenter", { bubbles: true, cancelable: true });
  Object.defineProperty(enter, "dataTransfer", { value: { types: ["Files"] } });
  h.document.getElementById("sessions-panel").dispatchEvent(enter);
  assert.ok(overlay.classList.contains("visible"), "dragging over the sessions panel still offers the drop");

  const leave = new h.window.Event("dragleave", { bubbles: true, cancelable: true });
  Object.defineProperty(leave, "dataTransfer", { value: { types: ["Files"] } });
  h.document.getElementById("sessions-panel").dispatchEvent(leave);
  assert.ok(!overlay.classList.contains("visible"), "leaving hides the overlay again");
  assert.strictEqual(h.errors().length, 0);
});

test("an internal multi file drag attaches every file, not just the first", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  // VS Code truncates the standard text/uri-list to the FIRST resource and puts
  // them all in application/vnd.code.uri-list, so we must read the internal one.
  const chatMain = h.document.getElementById("chat-main");
  const drop = new h.window.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(drop, "dataTransfer", {
    value: {
      types: ["application/vnd.code.uri-list", "text/uri-list"],
      files: [],
      getData: (t) =>
        t === "application/vnd.code.uri-list"
          ? "file:///w/a.ts\nfile:///w/b.ts\nfile:///w/c.ts"
          : t === "text/uri-list" ? "file:///w/a.ts" : ""
    }
  });
  chatMain.dispatchEvent(drop);
  await h.settle(10);

  const added = h.posted.filter((m) => m.type === "addMention").map((m) => m.path);
  assert.deepStrictEqual(
    added,
    ["file:///w/a.ts", "file:///w/b.ts", "file:///w/c.ts"],
    "all three dragged files are attached"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("while busy, Send becomes a split button: queue it, or stop and send", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "assistantStart" });
  h.post({ type: "busy", value: true });
  await h.settle(10);

  const send = h.document.getElementById("send");
  const more = h.document.getElementById("send-more");
  const stop = h.document.getElementById("stop");
  assert.ok(!stop.classList.contains("hidden"), "Stop is shown while busy");
  assert.ok(send.classList.contains("hidden"), "Send is hidden while busy with an empty composer");
  assert.ok(more.classList.contains("hidden"), "and so is the chevron, with nothing to send");

  const input = h.document.getElementById("input");
  input.value = "a follow up";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(10);
  assert.ok(!send.classList.contains("hidden"), "Send appears once there is something to send");
  assert.ok(!more.classList.contains("hidden"), "with the chevron beside it");
  assert.ok(h.document.getElementById("send-group").classList.contains("split"), "joined as one control");
  // Stop is the filled action while a turn runs, and the send control stays at the
  // far end after it, which is the order VS Code's chat uses.
  const order = [...h.document.querySelector(".toolbar-right").children].map((c) => c.id);
  assert.ok(order.indexOf("stop") < order.indexOf("send-group"), "Stop comes first, Send last");
  assert.match(send.querySelector("i").className, /codicon-newline/, "the default half queues it");
  assert.strictEqual(send.title, "Send to Queue (Enter)");
  assert.ok(!stop.classList.contains("hidden"), "Stop stays available alongside it");

  // The chevron offers both, each saying what it does and on which key.
  more.click();
  await h.settle(10);
  const rows = [...h.document.querySelectorAll(".dv-menu .dv-menu-item")];
  assert.deepStrictEqual(rows.map((r) => r.querySelector(".dv-menu-text span").textContent),
    ["Send to Queue", "Stop and Send"]);
  assert.deepStrictEqual(rows.map((r) => r.querySelector(".dv-menu-keys").textContent), ["Enter", "Alt+Enter"]);
  assert.match(rows[1].querySelector(".dv-menu-detail").textContent, /Stop what Vtx is doing/);

  rows[1].click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "stopAndSend" && m.text === "a follow up"),
    "the second one ends the turn and sends"
  );
  assert.strictEqual(input.value, "", "and the composer is cleared either way");

  // Enter queues, Alt+Enter stops and sends.
  input.value = "another";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(5);
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "send" && m.text === "another"), "Enter queues it");
  input.value = "urgent";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(5);
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", altKey: true, bubbles: true }));
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "stopAndSend" && m.text === "urgent"), "Alt+Enter takes over");
  assert.strictEqual(h.errors().length, 0);
});

test("the split button's default follows the setting, and Alt shows the other", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true, sendWhileWorking: "stopAndSend" });
  h.post({ type: "busy", value: true });
  const input = h.document.getElementById("input");
  input.value = "take over";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(10);

  const send = h.document.getElementById("send");
  assert.strictEqual(send.title, "Stop and Send (Enter)", "the setting decides what Enter does");
  send.click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "stopAndSend" && m.text === "take over"));

  // Holding Alt flips the primary half, so it never claims the wrong thing.
  input.value = "and this after";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  h.document.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Alt", altKey: true, bubbles: true }));
  await h.settle(10);
  assert.strictEqual(send.title, "Send to Queue (Alt+Enter)");
  assert.match(send.querySelector("i").className, /codicon-newline/);
  send.click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "send" && m.text === "and this after"));
  assert.strictEqual(h.errors().length, 0);
});

test("a run of tools says what it did, not how many tools it used", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "toolCall", id: "t1", title: "Read src/a.ts", kind: "read", status: "completed", locations: [{ path: "/w/src/a.ts" }] });
  h.post({ type: "toolCall", id: "t2", title: "Read src/b.ts", kind: "read", status: "completed", locations: [{ path: "/w/src/b.ts" }] });
  await h.settle(15);
  const label = () => h.thread().querySelector(".tool-group-label").textContent;
  assert.strictEqual(label(), "Read a.ts and b.ts", "two files are named");
  assert.strictEqual(
    h.thread().querySelector(".tool-group-label .tool-verb").textContent,
    "Read",
    "the verb leads, the rest is dimmed, like every other row"
  );
  assert.strictEqual(h.thread().querySelectorAll(".tool-group .codicon-tools").length, 0, "no icon of its own");

  // Past a couple of files it counts them, and other work adds its own clause.
  h.post({ type: "toolCall", id: "t3", title: "Read src/c.ts", kind: "read", status: "completed", locations: [{ path: "/w/src/c.ts" }] });
  h.post({ type: "toolCall", id: "t4", title: "Run npm test", kind: "execute", status: "completed", rawInput: { command: "npm test" } });
  await h.settle(15);
  assert.strictEqual(label(), "Read 3 files and ran npm test");

  h.post({ type: "toolCall", id: "t5", title: "Grep for auth", kind: "search", status: "completed", rawInput: { pattern: "auth", path: "/w/src" } });
  await h.settle(15);
  assert.strictEqual(label(), "Read 3 files and searched for auth in w/src");

  // A clause that already names two things carries its own "and", so the clauses
  // are separated by commas instead.
  h.post({ type: "assistantChunk", text: "now the other run" });
  h.post({ type: "toolCall", id: "u1", title: "Read one.ts", kind: "read", status: "completed", locations: [{ path: "/w/one.ts" }] });
  h.post({ type: "toolCall", id: "u2", title: "Read two.ts", kind: "read", status: "completed", locations: [{ path: "/w/two.ts" }] });
  h.post({ type: "toolCall", id: "u3", title: "Run npm run build", kind: "execute", status: "completed", rawInput: { command: "npm run build" } });
  await h.settle(15);
  const labels = [...h.thread().querySelectorAll(".tool-group-label")].map((l) => l.textContent);
  assert.strictEqual(labels[1], "Read one.ts and two.ts, ran npm run build");
  assert.strictEqual(h.errors().length, 0);
  // However much a run did, the summary is two clauses, as VS Code's chat keeps
  // it: chaining all of them ran the header past the panel and off its chevron.
  labels.forEach((l) => assert.ok(l.split(/, | and /).length <= 3, "at most two clauses: " + l));
});

test("a run holds its reasoning and its edits together, under one summary", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userChunk", text: "build the thing" });
  h.post({ type: "toolCall", id: "t1", title: "Read plan.ts", kind: "read", status: "completed", locations: [{ path: "/w/plan.ts" }] });
  h.post({ type: "fileChange", path: "/w/new.ts", added: 20, removed: 0, created: true });
  // Reasoning part way through the work used to split the run in two.
  h.post({ type: "thoughtChunk", text: "Now the callers need updating too.", messageId: "th1" });
  h.post({ type: "fileChange", path: "/w/shared.ts", added: 4, removed: 2 });
  await h.settle(30);

  const groups = [...h.thread().querySelectorAll(".tool-group")];
  assert.strictEqual(groups.length, 1, "one run, not one per interruption");
  const body = groups[0].querySelector(".tool-group-body");
  assert.strictEqual(body.querySelectorAll(".tool").length, 1, "the tool call is in it");
  assert.strictEqual(body.querySelectorAll(".edit-pill").length, 2, "so are both edits");
  assert.strictEqual(body.querySelectorAll(".thinking").length, 1, "and the reasoning between them");
  assert.strictEqual(
    groups[0].querySelector(".tool-group-label").textContent,
    "Created new.ts and updated shared.ts",
    "the summary names what it did to each file, and reads like VS Code's"
  );

  // The reply itself ends the run, and stays outside it.
  h.post({ type: "assistantChunk", text: "All done." });
  h.post({ type: "toolCall", id: "t2", title: "Read after.ts", kind: "read", status: "completed", locations: [{ path: "/w/after.ts" }] });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelectorAll(".tool-group").length, 1, "the answer closed the run");
  const reply = h.thread().querySelector(".resp-text");
  assert.ok(reply && !reply.closest(".tool-group"), "and the answer is not inside it");
  assert.strictEqual(h.errors().length, 0);
});

test("a tool that came back with a picture shows it, open or shut", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  h.post({
    type: "toolCall",
    id: "s1",
    title: "Capture browser screenshot",
    kind: "other",
    status: "completed",
    content: [{ type: "image", mime: "image/png", data: px }]
  });
  await h.settle(20);
  const tool = h.thread().querySelector(".tool");
  const shot = tool.querySelector(".tool-media .dv-thumb");
  assert.ok(shot, "the picture is the result, so it is shown");
  assert.ok(shot.src.startsWith("data:image/png;base64,"));
  assert.ok(tool.classList.contains("dv-collapsed"), "and it does not need the row opened");
  assert.ok(!tool.querySelector(".tool-body .dv-thumb"), "it is not buried inside the body");
  assert.strictEqual(h.errors().length, 0);
});

test("an executed command is the command and what it printed, uncaptioned", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "c1",
    title: "Run npm test",
    kind: "execute",
    status: "completed",
    rawInput: { command: "npm test" },
    content: [{ type: "text", text: "2 passing" }]
  });
  await h.settle(20);
  // VS Code's chat captions neither: a command followed by its output needs no
  // telling apart, and the captions were pure chrome on the most common row there is.
  const titles = [...h.thread().querySelectorAll(".tool-section-title")].map((t) => t.textContent);
  assert.deepStrictEqual(titles, [], "no Input and Output captions on a command");
  assert.strictEqual(h.thread().querySelector(".tool-command code").textContent, "npm test");
  // The row is titled by the command itself, highlighted, not by a sentence.
  assert.strictEqual(h.thread().querySelector(".tool .tool-label-code").textContent, "npm test");
  assert.strictEqual(h.errors().length, 0);
});

test("the chat says when it holds the keyboard, so its shortcuts can", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  await h.settle(10);

  // A keybinding's `when` cannot see into a webview, so Ctrl/Cmd+1..9 would go on
  // meaning what they mean everywhere else in VS Code unless the panel says this.
  h.window.dispatchEvent(new h.window.Event("focus"));
  await h.settle(5);
  assert.deepStrictEqual(h.posted.filter((m) => m.type === "chatFocus").pop(), { type: "chatFocus", value: true });

  h.window.dispatchEvent(new h.window.Event("blur"));
  await h.settle(5);
  assert.deepStrictEqual(h.posted.filter((m) => m.type === "chatFocus").pop(), { type: "chatFocus", value: false });
  assert.strictEqual(h.errors().length, 0);
});

test("a queued message shows what it is queued with, and keeps it when sent", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "busy", value: true });
  h.post({
    type: "queued",
    items: [{ id: "q-1", text: "look at this too", attachments: [{ label: "token.ts", type: "file" }] }]
  });
  await h.settle(20);

  const row = h.thread().querySelector(".queued-item .chat-attached-context");
  assert.ok(row, "the queued row says what is attached to it");
  assert.strictEqual(row.querySelector(".chat-attached-context-attachment").textContent, "token.ts");

  // The queue drains: the message becomes a request, still carrying its context.
  h.post({ type: "queued", items: [] });
  h.post({ type: "userMessage", text: "look at this too", attachments: [{ label: "token.ts", type: "file" }] });
  await h.settle(20);
  const sent = h.thread().querySelector(".turn-request .chat-attached-context-attachment");
  assert.ok(sent, "and the sent request still shows it");
  assert.strictEqual(sent.textContent, "token.ts");
  assert.strictEqual(h.document.getElementById("attachments").children.length, 0, "the composer is left empty");
  assert.strictEqual(h.errors().length, 0);
});

test("editing a queued message hands its attachments back to the composer", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "busy", value: true });
  h.post({ type: "queued", items: [{ id: "q-1", text: "look at this", attachments: [{ label: "token.ts", type: "file" }] }] });
  await h.settle(20);

  h.thread().querySelector(".queued-item .codicon-edit").parentElement.click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "queueEditing" && m.id === "q-1"),
    "the host is told which message is being edited, so it can hand its context over"
  );
  // The host answers by staging a copy; the composer shows them as removable
  // pills, and the message keeps its own, so its row still says what it carries.
  h.post({ type: "attachments", items: [{ id: "a1", label: "token.ts", type: "file" }] });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("attachments").querySelectorAll(".chip").length, 1);
  assert.strictEqual(
    h.thread().querySelectorAll(".queued-item .chat-attached-context-attachment").length,
    1,
    "and the queued row goes on showing what it will send if this edit is abandoned"
  );

  const input = h.document.getElementById("input");
  input.value = "look at this, closely";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  h.document.getElementById("send").click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "editQueued" && m.id === "q-1" && m.text === "look at this, closely"),
    "committing the edit updates the message in place"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a request keeps what was attached to it, above the message", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "userMessage",
    text: "why does this look wrong?",
    attachments: [
      { label: "main.css", type: "file" },
      { label: "Screenshot.png", type: "image", thumb: "data:image/png;base64,iVBORw0KGgo=" }
    ]
  });
  await h.settle(20);

  const req = h.thread().querySelector(".turn-request");
  const row = req.querySelector(".chat-attached-context");
  assert.ok(row, "the context came with the message, so it stays with it");
  assert.ok(row.compareDocumentPosition(req.querySelector(".req-body")) & 4,
    "above the bubble, where VS Code puts it");
  const pills = [...row.querySelectorAll(".chat-attached-context-attachment")];
  assert.deepStrictEqual(pills.map((p) => p.textContent), ["main.css", "Screenshot.png"]);
  assert.ok(!pills[0].classList.contains("file-pill"), "attached context keeps its own pill styling");
  assert.ok(pills[0].querySelector(".attachment-icon"), "a file gets its file glyph");
  const img = pills[1].querySelector("img.chat-attached-context-pill-image");
  assert.ok(img, "a picture is its own thumbnail");
  assert.strictEqual(img.getAttribute("src"), "data:image/png;base64,iVBORw0KGgo=");

  // A replayed request carries them too, so a reload does not strip the context.
  h.post({ type: "clear" });
  h.post({ type: "userChunk", text: "again", messageId: "m1", attachments: [{ label: "notes.md", type: "file" }] });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelectorAll(".chat-attached-context-attachment").length, 1);
  assert.strictEqual(h.errors().length, 0);
});

test("a long chain of commands is cut to a scannable length", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  const long = 'pkill -f "http.server 8899"; cd /Users/shayan/VSCode/vtx-vscode && ' +
    "playwright-cli close >/dev/null 2>&1; rm -rf .playwright-cli /tmp/tg.png";
  h.post({ type: "toolCall", id: "c9", kind: "execute", status: "completed", rawInput: { command: long } });
  await h.settle(20);

  // VS Code stops at 80 characters in code rather than letting the panel decide,
  // so the row cannot run away however long the command is.
  const label = h.thread().querySelector(".tool .tool-label-code");
  assert.strictEqual(label.textContent.length, 80);
  assert.ok(label.textContent.endsWith("..."));
  assert.ok(long.startsWith(label.textContent.slice(0, 77)), "it is the start of the command");
  assert.strictEqual(h.thread().querySelector(".tool .dv-collapsible-header").title, long, "all of it on hover");
  assert.strictEqual(h.errors().length, 0);
});

test("an edit row waits for the host before it says a file was undone", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "do it" });
  h.post({ type: "fileChange", path: "/w/a.ts", added: 3, removed: 1 });
  await h.settle(20);

  const pill = () => h.thread().querySelector(".edit-pill");
  const undo = pill().querySelector('.edit-pill-actions button[title="Undo this file\'s changes"]');
  assert.ok(undo, "the row offers it");
  undo.click();
  assert.ok(h.posted.some((m) => m && m.type === "rejectFile" && m.path === "/w/a.ts"), "the host was asked");
  await h.settle(10);

  // An undo can fail: a read only file, a lock, a directory that has gone. The
  // host says so and keeps the file in the working set, so a row that had already
  // written "Undone" over itself would be the only thing claiming otherwise.
  assert.strictEqual(pill().querySelector(".edit-pill-label").textContent, "Edited", "it does not guess");
  assert.ok(!pill().classList.contains("resolved"));
  assert.ok(pill().querySelector(".edit-pill-actions"), "so it can be undone again");

  h.post({ type: "changesResolved", paths: ["/w/a.ts"], action: "reject" });
  await h.settle(10);
  assert.strictEqual(pill().querySelector(".edit-pill-label").textContent, "Undone", "and says so once it is");
  assert.strictEqual(h.thread().querySelectorAll(".edit-pill-actions").length, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("changed files header lets its controls wrap", () => {
  const css = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  const header = /\.ws-header\s*\{([^}]*)\}/.exec(css);
  assert.ok(header, "the changed files header has a style block");
  assert.match(header[1], /flex-wrap:\s*wrap/, "the header can reflow at narrow widths");
});

test("keeping every change marks every edit row, not just the one clicked", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "do it" });
  h.post({ type: "fileChange", path: "/w/a.ts", added: 3, removed: 1 });
  h.post({ type: "fileChange", path: "/w/b.ts", added: 2, removed: 0, created: true });
  h.post({ type: "workingSet", files: [{ path: "/w/a.ts", name: "a.ts" }, { path: "/w/b.ts", name: "b.ts" }] });
  await h.settle(20);

  const pills = () => [...h.thread().querySelectorAll(".edit-pill")];
  assert.strictEqual(pills().length, 2);
  assert.ok(pills().every((p) => !p.classList.contains("resolved")), "nothing is resolved yet");

  // "Keep all" resolves files nobody clicked, so the rows can only learn of it
  // from the host.
  const keepAll = [...h.document.querySelectorAll("#working-set button")].find((b) => b.textContent === "Keep all");
  assert.ok(keepAll, "the tray offers it");
  keepAll.click();
  assert.ok(h.posted.some((m) => m && m.type === "acceptAll"), "the tray asked the host");
  await h.settle(10);
  assert.ok(pills().every((p) => !p.classList.contains("resolved")), "and does not guess the answer");

  h.post({ type: "changesResolved", paths: ["/w/a.ts", "/w/b.ts"], action: "accept" });
  await h.settle(10);
  assert.ok(pills().every((p) => p.classList.contains("resolved")), "every row says so once it is done");
  assert.deepStrictEqual(
    pills().map((p) => p.querySelector(".edit-pill-label").textContent),
    ["Kept", "Kept"]
  );
  assert.strictEqual(h.thread().querySelectorAll(".edit-pill-actions").length, 0, "with nothing left to click");
  assert.strictEqual(h.errors().length, 0);
});

test("undoing every change says undone, and a later report cannot revive it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "do it" });
  h.post({ type: "fileChange", path: "/w/a.ts", added: 3, removed: 1 });
  await h.settle(20);

  h.post({ type: "changesResolved", paths: ["/w/a.ts"], action: "reject" });
  await h.settle(10);
  const pill = h.thread().querySelector(".edit-pill");
  assert.strictEqual(pill.querySelector(".edit-pill-label").textContent, "Undone");

  // The same edit arriving again must not put Keep and Undo back on a row that
  // has already been dealt with.
  h.post({ type: "fileChange", path: "/w/a.ts", added: 3, removed: 1 });
  await h.settle(10);
  assert.strictEqual(h.thread().querySelector(".edit-pill-label").textContent, "Undone");
  assert.strictEqual(h.thread().querySelectorAll(".edit-pill-actions").length, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("a run folds to its summary once it is over", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  await h.settle(20);
  const run = () => h.thread().querySelector(".tool-group");
  assert.ok(run(), "the two calls made a run");
  assert.ok(!run().classList.contains("dv-collapsed"), "open while the work is going on");

  // A word from the agent ends the run, as it does in VS Code's chat.
  h.post({ type: "assistantChunk", text: "Both read." });
  h.post({ type: "assistantEnd" });
  await h.settle(20);
  assert.ok(run().classList.contains("dv-collapsed"), "and folds to its summary after");
  assert.match(run().querySelector(".tool-group-label").textContent, /Read/, "and says what it did");

  // A turn that ends on its work, with no closing word, still ends its run.
  const h2 = createHarness();
  h2.post({ type: "ready" });
  h2.post({ type: "body", body: "thread" });
  h2.post({ type: "clear" });
  h2.post({ type: "userMessage", text: "go" });
  h2.post({ type: "busy", value: true });
  h2.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h2.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  await h2.settle(20);
  assert.ok(!h2.thread().querySelector(".tool-group").classList.contains("dv-collapsed"));
  h2.post({ type: "busy", value: false });
  await h2.settle(20);
  assert.ok(h2.thread().querySelector(".tool-group").classList.contains("dv-collapsed"),
    "otherwise the last run of every turn is the one left standing open");
  assert.strictEqual(h.errors().length + h2.errors().length, 0);
});

test("a finished turn puts its work behind one line, and keeps the answer out", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "assistantChunk", text: "Reading first." });
  h.post({ type: "assistantEnd" });
  h.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  h.post({ type: "assistantChunk", text: "Here is the answer." });
  h.post({ type: "assistantEnd" });
  h.post({ type: "turnStats", totalTimeMs: 83000, dimensions: [] });
  h.post({ type: "busy", value: false });
  await h.settle(30);

  const box = h.thread().querySelector("details.completed-work");
  assert.ok(box, "the work of a finished turn folds away");
  assert.strictEqual(
    box.querySelector(".completed-work-summary span").textContent,
    "Completed 3 steps in 1m 23s",
    "every node before the answer, counted and timed as VS Code does"
  );
  // The answer itself must never end up inside: folding it would hide the point.
  const answers = [...h.thread().querySelectorAll(".resp-text")];
  const last = answers[answers.length - 1];
  assert.ok(!box.contains(last), "the answer stays in the open");
  assert.match(last.textContent, /Here is the answer/);
  assert.strictEqual(h.errors().length, 0);
});

test("one step is left as itself, and a turn with no answer is left alone", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "assistantChunk", text: "Done." });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(30);
  assert.strictEqual(h.thread().querySelector("details.completed-work"), null,
    "one step reads better than a line saying there is one step");

  // A turn that ends on its work has no answer to keep out, so folding it all
  // away would leave the turn showing nothing at all.
  const h2 = createHarness();
  h2.post({ type: "ready" });
  h2.post({ type: "body", body: "thread" });
  h2.post({ type: "clear" });
  h2.post({ type: "userMessage", text: "go" });
  h2.post({ type: "busy", value: true });
  h2.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h2.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  h2.post({ type: "busy", value: false });
  await h2.settle(30);
  assert.strictEqual(h2.thread().querySelector("details.completed-work"), null, "nothing would be left showing");
  assert.strictEqual(h.errors().length + h2.errors().length, 0);
});

test("the working row shimmers a word, with no spinner beside it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "assistantStart" });
  await h.settle(20);

  const row = h.thread().querySelector(".working");
  assert.ok(row, "something has to say the agent is alive");
  const label = row.querySelector(".dv-shimmer");
  assert.match(label.textContent, /^(Thinking|Reasoning|Considering|Analyzing|Evaluating|Working)$/,
    "one of VS Code's words, and no ellipsis: " + label.textContent);
  assert.strictEqual(row.querySelector(".codicon-modifier-spin"), null,
    "the shimmer is the signal, so a spinner beside it says the same thing twice");
  // The row is rebuilt as the turn streams, so its sweep is pinned to a shared
  // clock: without this every rebuild restarts at 0% and reads as frozen.
  assert.match(label.style.animationDelay, /^-\d+ms$/);

  h.post({ type: "assistantChunk", text: "Here." });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelector(".working"), null, "and it goes when the turn does");
  assert.strictEqual(h.errors().length, 0);
});

test("the gap between one action and the next still says the agent is working", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "assistantStart" });
  h.post({ type: "toolCall", id: "t1", title: "Run npm test", kind: "execute", status: "in_progress" });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelector(".working"), null,
    "a running command speaks for itself, so nothing is added beside it");

  // The command finishes and the agent goes quiet while it decides what is next.
  // That silence is exactly where the panel used to look like it had stopped.
  h.post({ type: "toolCallUpdate", id: "t1", status: "completed", content: [{ type: "text", text: "ok" }] });
  await h.settle(20);
  const row = h.thread().querySelector(".working");
  assert.ok(row, "the gap after a finished command says the agent is still working");
  assert.ok(row.previousElementSibling, "below the work it has done, at the end of the turn");
  assert.match(row.textContent, /^(Thinking|Reasoning|Considering|Analyzing|Evaluating|Working)$/);

  // A question is not work: say what is actually being waited on, as VS Code does.
  h.post({ type: "permission", requestId: "p1", title: "Vtx wants to run a command", options: [{ optionId: "allow", name: "Allow", kind: "allow" }] });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelector(".working").textContent, "1 confirmation pending");

  h.post({ type: "cancelPrompts" });
  h.post({ type: "assistantChunk", text: "All done." });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelector(".working"), null, "streamed text speaks for itself");

  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelector(".working"), null, "and the turn ending takes it away");
  assert.strictEqual(h.errors().length, 0);
});

test("stopping a turn takes the question with it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({
    type: "elicitation",
    requestId: "q1",
    message: "Which one?",
    schema: { type: "object", properties: { pick: { type: "string", enum: ["a", "b"] } } }
  });
  await h.settle(20);
  assert.ok(h.document.querySelector("#elicitation-tray").children.length, "the agent asked something");

  // Stop answers it with "cancelled", so the question goes with the turn: leaving
  // it on screen offers to answer something nobody is waiting on.
  h.post({ type: "cancelPrompts" });
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.strictEqual(h.document.querySelector("#elicitation-tray").children.length, 0, "and stopping clears it");
  assert.strictEqual(h.document.querySelector("#permission-tray").children.length, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("a reloaded session comes back folded, and folded means hidden", async () => {
  const h = createHarness();
  h.replay([{ role: "user", text: "go" }]);
  h.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  h.post({ type: "thoughtChunk", text: "A replayed thought.", messageId: "t1", replayed: true });
  h.post({ type: "assistantChunk", text: "Done.", messageId: "a1" });
  await h.settle(30);

  // Before "loaded" the replay is still drawing, so nothing is folded yet.
  h.post({ type: "loaded" });
  await h.settle(30);

  const run = h.thread().querySelector(".tool-group");
  assert.ok(run.classList.contains("dv-collapsed"), "a whole session laid out end to end is not history, it is a wall");
  const think = [...h.thread().querySelectorAll(".thinking")].find((t) => !t.closest(".tool-group-body"));
  if (think) assert.ok(think.classList.contains("dv-collapsed"), "a replayed thought comes back folded too");
  // Except the reasoning inside the run, which folds to nothing rather than to a
  // header: an empty row on the chain, with its text beyond reach.
  const inRun = h.thread().querySelector(".tool-group-body > .thinking-plain");
  assert.ok(inRun, "the replayed thought joined the run it led to");
  assert.ok(!inRun.classList.contains("dv-collapsed"), "and opening the run shows it, rather than a line to nowhere");
  assert.strictEqual(inRun.querySelector(".dv-collapsible-anim-inner").inert, false, "so it is readable and searchable");

  // Folded is not merely invisible: Tab must not walk into it and find in page
  // must not match inside it.
  assert.strictEqual(run.querySelector(".dv-collapsible-anim-inner").inert, true);
  h.post({ type: "clear" });
  await h.settle(10);
  assert.strictEqual(h.errors().length, 0);
});

test("a run the user opened is left open when it ends", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "busy", value: true });
  h.post({ type: "toolCall", id: "a", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "toolCall", id: "b", kind: "read", status: "completed", rawInput: { path: "/w/b.ts" } });
  await h.settle(20);

  // Closing something somebody deliberately opened is not tidying up.
  const run = h.thread().querySelector(".tool-group");
  run.querySelector(".dv-collapsible-header").click();  // collapse
  run.querySelector(".dv-collapsible-header").click();  // and open again, by hand
  await h.settle(10);
  h.post({ type: "busy", value: false });
  await h.settle(20);
  assert.ok(!run.classList.contains("dv-collapsed"), "left as the user left it");
  assert.strictEqual(h.errors().length, 0);
});

test("scrolling back through a thought is not undone by the next one", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "busy", value: true });
  h.post({ type: "thoughtChunk", text: "First, the callers still import the old helpers." });
  await h.settle(30);

  // The peek is a fixed height box with its own scrollbar. jsdom has no layout, so
  // the geometry the pin reads is stated here.
  const box = h.thread().querySelector(".thinking .dv-collapsible-anim-inner");
  assert.ok(box, "the thought has a box of its own");
  Object.defineProperty(box, "scrollHeight", { value: 1000, configurable: true });
  Object.defineProperty(box, "clientHeight", { value: 200, configurable: true });

  // The reader scrolls up inside it to read back through the chain.
  box.scrollTop = 300;
  h.post({ type: "thoughtChunk", text: "\n\nSecond, that means the rename has to cover them too." });
  await h.settle(30);
  const afterScrollingUp = box.scrollTop;

  // And back at the bottom, where it should follow the newest thought again.
  box.scrollTop = 800;
  h.post({ type: "thoughtChunk", text: "\n\nThird, the tests pin the old names." });
  await h.settle(30);
  const afterReturning = box.scrollTop;

  // Ending the thought stops the timer ticking its label, which is what keeps the
  // runner's event loop alive, so it happens before anything can throw.
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  await h.settle(10);

  assert.strictEqual(afterScrollingUp, 300, "the box stays where the reader left it");
  assert.strictEqual(afterReturning, 1000, "and follows again once they are back at the bottom");
  assert.strictEqual(h.errors().length, 0);
});

test("reasoning inside a run is text on the chain, not a section to open", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "toolCall", id: "t1", kind: "read", status: "completed", rawInput: { path: "/w/a.ts" } });
  h.post({ type: "thoughtChunk", text: "See [a.ts](src/a.ts)." });
  h.post({ type: "toolCall", id: "t2", kind: "edit", status: "completed", rawInput: { path: "/w/b.ts" } });
  await h.settle(30);

  const think = h.thread().querySelector(".thinking");
  assert.ok(think.classList.contains("thinking-plain"), "no header, nothing to open");
  // The stylesheet keys the headerless treatment off being inside a run, so this
  // is the invariant that decides how it is drawn.
  assert.ok(think.closest(".tool-group-body"), "and it sits on the run's chain with the work it led to");
  assert.match(think.textContent, /See a\.ts/, "the reasoning reads as itself");
  // A thought is a row of the run like any other, so it leads with a codicon in
  // the same column as the rows around it, and the chain treats it the same. The
  // stylesheet measures the node from those, so they are the invariant.
  const bullet = think.querySelector(".thinking-item > .thinking-icon");
  assert.ok(bullet, "the row leads with its glyph, before the text");
  assert.ok(bullet.classList.contains("codicon-circle-filled"), "a codicon, not a dot drawn by hand");
  assert.strictEqual(bullet.nextElementSibling.className, "thinking-item-content", "then what it thought");
  const thoughtLink = think.querySelector(".thinking-item-content a");
  assert.ok(thoughtLink && thoughtLink.classList.contains("file-pill"), "thinking references use the file pill");
  thoughtLink.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  thoughtLink.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: " ", bubbles: true }));
  assert.ok(h.posted.some((m) => m.type === "openFile" && m.path === "src/a.ts"),
    "thinking references open from the keyboard");
  h.post({ type: "capabilities", inlineReferencesStyle: "link" });
  await h.settle(5);
  assert.ok(!thoughtLink.classList.contains("file-pill"), "link mode updates thinking references");
  h.post({ type: "capabilities", inlineReferencesStyle: "box" });
  await h.settle(5);
  assert.ok(thoughtLink.classList.contains("file-pill"), "box mode restores thinking references");
  assert.ok(!think.classList.contains("dv-collapsed"), "and it is open: there is no header left to open it with");

  // A thought on its own is a section of its own, and keeps its header: it is
  // not a step in anything, so headerless text would be a paragraph from nowhere.
  const h3 = createHarness();
  h3.post({ type: "ready" });
  h3.post({ type: "body", body: "thread" });
  h3.post({ type: "clear" });
  h3.post({ type: "thoughtChunk", text: "Weighing it up." });
  h3.post({ type: "assistantChunk", text: "Answer." });
  h3.post({ type: "assistantEnd" });
  await h3.settle(30);
  const lone = h3.thread().querySelector(".thinking");
  assert.ok(!lone.closest(".tool-group-body"), "not in a run, so the header stays");
  assert.strictEqual(lone.querySelector(".thinking-label").textContent, "Thought for 1s");
  assert.strictEqual(h3.errors().length, 0);

  // Asking for the folded style still gets a folded section.
  const h2 = createHarness();
  h2.post({ type: "ready" });
  h2.post({ type: "body", body: "thread" });
  h2.post({ type: "clear" });
  h2.post({ type: "capabilities", thinkingStyle: "collapsed" });
  h2.post({ type: "thoughtChunk", text: "Weighing two options." });
  // Settle the block: a live one keeps its "Thinking… Ns" ticker running.
  h2.post({ type: "assistantChunk", text: "Going with the first." });
  h2.post({ type: "assistantEnd" });
  await h2.settle(30);
  const folded = h2.thread().querySelector(".thinking");
  assert.ok(!folded.classList.contains("thinking-plain"));
  assert.ok(folded.querySelector(".thinking-label"), "which keeps its header");
  assert.strictEqual(h.errors().length + h2.errors().length, 0);
});

test("every step of a thought is a node on the run's chain", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "toolCall", id: "z0", kind: "execute", status: "completed", rawInput: { command: "echo one" } });
  h.post({ type: "thoughtChunk", text: "First this.\n\nThen that.\n\nAnd finally the other.", messageId: "t1" });
  h.post({ type: "toolCall", id: "z1", kind: "execute", status: "completed", rawInput: { command: "echo two" } });
  h.post({ type: "assistantChunk", text: "Done." });
  h.post({ type: "assistantEnd" });
  await h.settle(30);

  // The run's line only ever breaks around one node per row, so a thought of
  // several steps has to chain its own: the stylesheet keys that off the steps
  // being separate elements, each with its own bullet.
  const steps = [...h.thread().querySelectorAll(".tool-group-body > .thinking-plain .thinking-item")];
  assert.strictEqual(steps.length, 3, "one step per paragraph of reasoning");
  steps.forEach((s) => assert.ok(s.querySelector(".thinking-icon"), "each wearing its own bullet"));
  assert.strictEqual(h.errors().length, 0);
});

test("invoking a skill is one line, and the skill opens from it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "commit it" });
  h.post({
    type: "toolCall",
    id: "s1",
    kind: "other",
    status: "completed",
    title: "Invoked skill git-workflow",
    meta: { inferenceToolName: "skill" },
    rawInput: { skill: "git-workflow" }
  });
  await h.settle(30);

  const row = h.thread().querySelector(".tool");
  // Nothing to open: the body only ever held the skill's own name as JSON.
  assert.ok(row.classList.contains("dv-nocollapse"), "a skill invocation is a fact, not a step to expand");
  assert.strictEqual(row.querySelector(".tool-body").innerHTML, "");

  // The name is an inline anchor, as VS Code renders a skill in a tool title.
  const pill = row.querySelector(".chat-inline-anchor");
  assert.ok(pill, "the skill is a pill");
  assert.strictEqual(pill.textContent, "git-workflow");
  assert.ok(pill.querySelector(".codicon"), "with its icon on the left");
  assert.match(row.querySelector(".label").textContent, /^Invoked skill/, "after what the agent did with it");

  pill.click();
  await h.settle(5);
  assert.deepStrictEqual(
    h.posted.filter((m) => m.type === "openSkill"),
    [{ type: "openSkill", name: "git-workflow" }],
    "and it opens where the skill is written"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a thought that says nothing is not left on the chain", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userMessage", text: "go" });
  h.post({ type: "toolCall", id: "z0", kind: "execute", status: "completed", rawInput: { command: "echo one" } });
  // Reasoning that turned out to be whitespace. Left in, it is a step with
  // nothing beside it and a gap in the run where the reader looks for one.
  h.post({ type: "thoughtChunk", text: "   \n\n", messageId: "t9" });
  h.post({ type: "toolCall", id: "z1", kind: "execute", status: "completed", rawInput: { command: "echo two" } });
  h.post({ type: "assistantChunk", text: "Done." });
  h.post({ type: "assistantEnd" });
  await h.settle(30);

  const rows = [...h.thread().querySelectorAll(".tool-group-body > *")];
  assert.deepStrictEqual(rows.map((r) => r.className.split(" ")[0]), ["tool", "tool"], "two commands, and nothing between them");
  assert.strictEqual(h.thread().querySelector(".tool-group-label").textContent, "Ran 2 commands");
  assert.strictEqual(h.errors().length, 0);
});

test("a reloaded command does not report its own input as its output", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  // How a replayed run arrives: the command comes back as the script it was sent
  // as, which is not output. Rendering it showed every old row its input twice.
  const cmd = "npm test -- --coverage";
  h.post({
    type: "toolCall",
    id: "r1",
    kind: "execute",
    status: "completed",
    rawInput: { command: cmd },
    content: [{ type: "text", text: cmd }]
  });
  await h.settle(20);

  const body = h.thread().querySelector(".tool-body");
  assert.strictEqual(body.querySelectorAll("pre").length, 0, "the command is not also the result");
  assert.strictEqual(h.thread().querySelector(".tool-command code").textContent, cmd, "it is still the command");

  // Real output is still shown.
  h.post({ type: "toolCallUpdate", id: "r1", content: [{ type: "text", text: "2 passing" }] });
  await h.settle(20);
  assert.match(h.thread().querySelector(".tool-body pre").textContent, /2 passing/);
  assert.strictEqual(h.errors().length, 0);
});

test("a command's row drops the cd that got it there", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "cd1",
    kind: "execute",
    status: "completed",
    rawInput: { command: "cd /Users/shayan/VSCode/vtx-vscode && npm test" }
  });
  await h.settle(20);

  // VS Code's extractCdPrefix: the row is for the command, not for the path it
  // was run in, which is long enough to push the command off the end.
  assert.strictEqual(h.thread().querySelector(".tool .tool-label-code").textContent, "npm test");
  assert.strictEqual(
    h.thread().querySelector(".tool-command code").textContent,
    "cd /Users/shayan/VSCode/vtx-vscode && npm test",
    "expanding it still shows what actually ran"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a listing is grouped by folder, not a wall of pills", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", root: "/repo" });
  const files = [
    "/repo/src/acp/client.ts", "/repo/src/acp/types.ts", "/repo/src/acp/terminal.ts",
    "/repo/src/chat/chatManager.ts", "/repo/src/chat/chatViewProvider.ts",
    "/repo/src/cli/locate.ts", "/repo/src/extension.ts"
  ];
  h.post({
    type: "toolCall",
    id: "g1",
    kind: "search",
    status: "completed",
    rawInput: { query: "src/**/*.ts" },
    content: files.map((path) => ({ type: "link", path }))
  });
  await h.settle(20);

  const heads = [...h.thread().querySelectorAll(".file-group-name")].map((n) => n.textContent);
  assert.deepStrictEqual(heads, ["src/acp", "src/chat", "src/cli", "src"], "one heading per folder, in the order found");
  const counts = [...h.thread().querySelectorAll(".file-group-count")].map((n) => n.textContent);
  assert.deepStrictEqual(counts, ["3", "2", "1", "1"], "each says how many are in it");
  assert.strictEqual(h.thread().querySelectorAll(".file-group-row").length, 7, "every file is still a row that opens");
  assert.strictEqual(h.thread().querySelectorAll(".tool-files").length, 0, "and none of it is pills");

  // The row carries what was looked for and how many turned up, so the body does
  // not repeat it, and the raw argument fallback must not dump it back as JSON.
  const row = [...h.thread().querySelectorAll(".tool .label")].find((l) => l.textContent.includes("src/**/*.ts"));
  assert.match(row.textContent, /src\/\*\*\/\*\.ts, 7 results/);
  assert.strictEqual(h.thread().querySelectorAll(".tool-summary").length, 0, "the query is not said twice");
  assert.strictEqual(h.thread().querySelectorAll(".tool-body pre").length, 0, "and never as raw JSON");

  // A handful stays as pills: grouping four files under one heading is worse.
  h.post({
    type: "toolCall",
    id: "g2",
    kind: "search",
    status: "completed",
    content: files.slice(0, 3).map((path) => ({ type: "link", path }))
  });
  await h.settle(20);
  assert.strictEqual(h.thread().querySelectorAll(".tool-files").length, 1, "a short result is still pills");
  assert.strictEqual(h.errors().length, 0);
});

test("a Windows listing is grouped under the workspace, not under its full path", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  // VS Code lower cases the drive letter and uses backslashes; the agent answers
  // with the drive as the user typed it and forward slashes. Same folder.
  h.post({ type: "capabilities", root: "c:\\repo" });
  const files = [
    "C:/repo/src/acp/client.ts", "C:/repo/src/acp/types.ts", "C:/repo/src/acp/terminal.ts",
    "C:/repo/src/chat/chatManager.ts", "C:/repo/src/chat/chatViewProvider.ts",
    "C:/repo/src/cli/locate.ts", "C:/repo/extension.ts"
  ];
  h.post({
    type: "toolCall",
    id: "w1",
    kind: "search",
    status: "completed",
    rawInput: { query: "src/**/*.ts" },
    content: files.map((path) => ({ type: "link", path }))
  });
  await h.settle(20);

  const heads = [...h.thread().querySelectorAll(".file-group-name")].map((n) => n.textContent);
  assert.deepStrictEqual(heads, ["src/acp", "src/chat", "src/cli", "."], "folders relative to the workspace");
  assert.strictEqual(h.errors().length, 0);
});

test("a command spanning many lines still takes one row", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  const heredoc = "python3 - <<'PY'\nimport re\nprint(re)\nPY";
  h.post({
    type: "toolCall",
    id: "c2",
    title: "Run a script",
    kind: "execute",
    status: "completed",
    rawInput: { command: heredoc },
    content: [{ type: "text", text: "done" }]
  });
  await h.settle(20);

  const label = h.thread().querySelector(".tool .tool-label-code");
  // VS Code's buildCommandDisplayText: one line, newlines collapsed to spaces.
  assert.strictEqual(label.textContent, "python3 - <<'PY' import re print(re) PY");
  assert.ok(!label.querySelector("span"), "and plain, not highlighted: on the row a command is a label");
  assert.strictEqual(
    h.thread().querySelector(".tool .dv-collapsible-header").title,
    heredoc,
    "the whole command is still there on hover"
  );
  // And in full where it belongs, in the Input section.
  assert.strictEqual(h.thread().querySelector(".tool-command code").textContent, heredoc);
  assert.strictEqual(h.errors().length, 0);
});

test("a command's output opens itself only while it is still running", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "c1",
    title: "Run npm test",
    kind: "execute",
    status: "in_progress",
    rawInput: { command: "npm test" },
    content: [{ type: "terminal", terminalId: "term-1" }]
  });
  await h.settle(20);
  const tool = () => h.thread().querySelector(".tool");
  assert.ok(tool().classList.contains("dv-collapsed"), "a command that may finish at once stays shut");

  // Still going a moment later: the output is worth watching, so it opens.
  await h.settle(2200);
  assert.ok(!tool().classList.contains("dv-collapsed"), "a long running one opens itself");

  // Finished well: what was only opened to watch it closes again.
  h.post({ type: "toolCallUpdate", id: "c1", status: "completed" });
  await h.settle(20);
  assert.ok(tool().classList.contains("dv-collapsed"), "and closes when it succeeds");
  assert.strictEqual(h.errors().length, 0);
});

test("a search says what it looked for and where, in one line", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "s1",
    title: "Grep for showInfo",
    kind: "search",
    status: "completed",
    rawInput: { pattern: "vtx.showInfo", path: "/w/src/ui" },
    content: [{ type: "text", text: "3 matches" }]
  });
  await h.settle(20);
  const tool = h.thread().querySelector(".tool");
  assert.strictEqual(tool.querySelector(".tool-verb").textContent, "Grep");
  assert.strictEqual(tool.querySelector(".tool-detail").textContent, " vtx.showInfo in src/ui");
  assert.ok(tool.classList.contains("dv-nocollapse"), "there is nothing left to expand");
  assert.strictEqual(tool.querySelector(".tool-body").childElementCount, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("stepping between questions updates the question text, not just the options", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({
    type: "elicitation", requestId: "e1", mode: "form", message: "First question about auth?",
    schema: {
      type: "object",
      required: ["q0", "q1"],
      properties: {
        q0: { type: "string", title: "Auth", description: "First question about auth?", oneOf: [{ const: "a", title: "A" }, { const: "b", title: "B" }] },
        q1: { type: "string", title: "DB", description: "Second question about the database?", oneOf: [{ const: "x", title: "X" }, { const: "y", title: "Y" }] }
      }
    }
  });
  await h.settle(20);

  const titleEl = h.document.querySelector("#elicitation-tray .qc-title");
  assert.strictEqual(titleEl.textContent, "First question about auth?", "the header shows the first question");

  const nextBtn = [...h.document.querySelectorAll("#elicitation-tray .qc-nav button")].find((b) => (b.title || "").includes("Next"));
  assert.ok(nextBtn, "there is a Next button");
  nextBtn.click();
  await h.settle(10);
  assert.strictEqual(titleEl.textContent, "Second question about the database?", "the header updates to the second question");
  assert.strictEqual(h.errors().length, 0);
});

test("clicking an external link defers to VS Code (no duplicate open)", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "assistantStart" });
  h.post({ type: "toolCall", id: "f1", title: "Fetched https://example.com", kind: "fetch",
    meta: { inferenceToolName: "webfetch" }, status: "pending", rawInput: { url: "https://example.com" } });
  h.post({ type: "toolCallUpdate", id: "f1", status: "completed", meta: { inferenceToolName: "webfetch" } });
  await h.settle(20);

  const link = h.document.querySelector("#thread a.tool-summary-value");
  assert.ok(link, "the fetch tool renders a clickable URL");
  link.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await h.settle(10);

  // http(s) links are opened by VS Code's built-in webview link handling; the
  // webview must NOT also post openExternal or the link opens in two tabs.
  const opens = h.posted.filter((m) => m.type === "openExternal");
  assert.strictEqual(opens.length, 0, "the webview does not duplicate VS Code's open");
  assert.strictEqual(h.errors().length, 0);
});

test("a skipped plan entry renders struck through with a slash icon", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "plan", entries: [
    { content: "Do A", status: "completed" },
    { content: "Skip B", status: "skipped" },
    { content: "Do C", status: "in_progress" }
  ] });
  await h.settle(20);

  const skipped = h.document.querySelector("#todo-widget .plan-skipped");
  assert.ok(skipped, "a skipped row is rendered");
  assert.ok(skipped.querySelector(".plan-mark.codicon-circle-slash"), "a skipped entry uses the slash icon");
  assert.strictEqual(skipped.querySelector(".plan-entry-text").textContent, "Skip B");
  assert.strictEqual(h.errors().length, 0);
});

test("a command shows the output it is producing, not just its exit code", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "c1",
    kind: "execute",
    status: "in_progress",
    rawInput: { command: "npm test" },
    terminalId: "term-1"
  });
  h.post({ type: "terminalOutput", terminalId: "term-1", output: "running 12 tests\n" });
  await h.settle(20);

  const pre = h.thread().querySelector('pre[data-terminal="term-1"]');
  assert.ok(pre, "the row shows the terminal it is running in");
  assert.match(pre.textContent, /running 12 tests/, "and the output as it arrives");

  // Vtx reports only the exit code when it finishes, which the output already
  // ends with, so it must not be repeated as a second Output block.
  h.post({ type: "terminalOutput", terminalId: "term-1", output: "running 12 tests\nall passed\n", exitStatus: { exitCode: 0 } });
  h.post({
    type: "toolCallUpdate",
    id: "c1",
    status: "completed",
    content: [{ type: "text", text: "Exited with code 0" }]
  });
  await h.settle(20);

  const live = h.thread().querySelector('pre[data-terminal="term-1"]');
  assert.match(live.textContent, /all passed/);
  assert.match(live.textContent, /exited code 0/);
  const blocks = h.thread().querySelectorAll(".tool-body pre");
  assert.strictEqual(blocks.length, 1, "one output block, not two saying the same thing");
  assert.strictEqual(h.errors().length, 0);
});

test("a command running in a real terminal offers what VS Code offers", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "toolCall", id: "c1", kind: "execute", status: "in_progress", rawInput: { command: "npm run dev" }, terminalId: "term-9" });
  h.post({ type: "terminalOutput", terminalId: "term-9", output: "listening on 3000\n", integrated: true });
  await h.settle(20);

  // The row's own title carries them, icon only, in VS Code's order and wording
  // (chatTerminalToolProgressPart: Continue in Background, then focus).
  const bar = () => h.thread().querySelector("#thread .tool .tool-actions");
  const actions = () => [...bar().querySelectorAll("button")].map((b) => [b.title, b.querySelector("i").className.split(" ")[1]]);
  assert.deepStrictEqual(actions(), [
    ["Continue in Background", "codicon-debug-continue-small"],
    ["Show and Focus Terminal", "codicon-open-in-product"]
  ]);
  // And the disclosure is the third of them, named for what it would do.
  assert.strictEqual(h.thread().querySelector(".tool .tool-chevron").title, "Show Output");

  bar().querySelectorAll("button")[1].click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "showTerminal" && m.terminalId === "term-9"));
  // Once shown it is no longer hidden, so the action stops offering to show it.
  h.post({ type: "terminalOutput", terminalId: "term-9", output: "listening on 3000\n", integrated: true, revealed: true });
  await h.settle(10);
  assert.deepStrictEqual(actions().map((a) => a[0]), ["Continue in Background", "Focus Terminal"]);

  bar().querySelectorAll("button")[0].click();
  await h.settle(5);
  assert.ok(
    h.posted.some((m) => m.type === "continueInBackground" && m.terminalId === "term-9"),
    "backgrounding it tells the host to stop waiting"
  );

  // The host confirms: the command is still going, the agent is not waiting, and
  // there is nothing left to background.
  h.post({ type: "terminalOutput", terminalId: "term-9", output: "listening on 3000\n", integrated: true, revealed: true, skipped: true });
  await h.settle(10);
  assert.deepStrictEqual(actions().map((a) => a[0]), ["Focus Terminal"]);
  assert.match(
    h.thread().querySelector("#thread .tool .label").textContent,
    /^Running.*npm run dev in background$/,
    "and the row says it is still going, as VS Code titles it"
  );

  // A command that never had a terminal of its own has nothing to offer.
  h.post({ type: "toolCall", id: "c2", kind: "execute", status: "in_progress", rawInput: { command: "ls" }, terminalId: "term-10" });
  h.post({ type: "terminalOutput", terminalId: "term-10", output: "a.ts\n" });
  await h.settle(10);
  const rows = [...h.thread().querySelectorAll("#thread .tool")];
  assert.strictEqual(rows[rows.length - 1].querySelector(".tool-actions").children.length, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("an MCP server that would not start is said out loud", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({
    type: "mcpProblems",
    servers: [{ name: "godot-ai", message: "MCP server 'godot-ai' connection failed: refused" }]
  });
  await h.settle(10);

  const card = h.document.getElementById("mcp-problems");
  assert.ok(card, "a chat missing half its tools has to say so");
  assert.match(card.textContent, /godot-ai did not start/);
  assert.match(card.textContent, /connection failed: refused/, "with what the CLI actually said");

  // A second one folds into the same row rather than stacking.
  h.post({
    type: "mcpProblems",
    servers: [
      { name: "godot-ai", message: "MCP server 'godot-ai' connection failed: refused" },
      { name: "telegram", message: "Failed to connect to MCP server 'telegram'" }
    ]
  });
  await h.settle(10);
  assert.strictEqual(h.document.querySelectorAll("#mcp-problems").length, 1);
  assert.match(h.document.getElementById("mcp-problems").textContent, /2 MCP servers did not start/);

  // Read once, it is noise: it can be sent away and must stay away.
  h.document.querySelector("#mcp-problems .mcp-dismiss").click();
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("mcp-problems"), null, "dismissed");
  h.post({
    type: "mcpProblems",
    servers: [
      { name: "godot-ai", message: "MCP server 'godot-ai' connection failed: refused" },
      { name: "telegram", message: "Failed to connect to MCP server 'telegram'" }
    ]
  });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("mcp-problems"), null, "and does not come back on its own");

  // Dismiss is for this one. The other two say how much longer than that.
  h.post({ type: "mcpProblems", servers: [{ name: "fetch", message: "MCP server 'fetch' failed" }] });
  await h.settle(10);
  const mutes = [...h.document.querySelectorAll("#mcp-problems .dv-link-btn")];
  assert.deepStrictEqual(mutes.map((b) => b.textContent), ["Don't show again in this window", "Don't show again"]);
  mutes[1].click();
  await h.settle(10);
  assert.ok(
    h.posted.some((m) => m.type === "muteMcpWarnings" && m.scope === "always"),
    "turning it off for good is a setting, so the host owns it"
  );
  assert.strictEqual(h.document.getElementById("mcp-problems"), null, "and the card goes with it");

  // A server that fails afterwards is news again.
  h.post({
    type: "mcpProblems",
    servers: [
      { name: "godot-ai", message: "MCP server 'godot-ai' connection failed: refused" },
      { name: "fetch", message: "MCP server 'fetch' connection failed: timeout" }
    ]
  });
  await h.settle(10);
  const back = h.document.getElementById("mcp-problems");
  assert.ok(back, "a server that failed later says so");
  assert.match(back.textContent, /The MCP server fetch did not start/, "naming only the new one");

  // And a different chat starts over, dismissed or not.
  h.post({ type: "clear" });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("mcp-problems"), null, "the warning belongs to the chat it came from");
  assert.strictEqual(h.errors().length, 0);
});

test("a finished turn shows what it cost, in the CLI's own figures", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", verbose: true });
  h.post({ type: "userMessage", text: "do the thing" });
  h.post({ type: "assistantChunk", text: "done" });
  h.post({ type: "assistantEnd" });
  h.post({ type: "busy", value: false });
  h.post({
    type: "turnStats",
    model: "Claude Opus 5 High",
    totalTimeMs: 6605,
    dimensions: [
      { label: "ACUs spent", value: "0.11 ACUs" },
      { label: "Agent messages", value: "3 messages" }
    ]
  });
  await h.settle(20);

  const det = h.thread().querySelector(".chat-footer-details");
  assert.ok(det, "the footer carries the detail");
  assert.match(det.textContent, /7s/, "how long it took, rounded like VS Code's chat elapsed label");
  assert.match(det.textContent, /Claude Opus 5 High/);
  assert.match(det.title, /ACUs spent: 0\.11 ACUs/, "and what it cost, on hover");
  assert.match(det.title, /Agent messages: 3 messages/);
  assert.strictEqual(h.errors().length, 0);
});

test("a folded plan says where it has got to, not just how far", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "plan", entries: [
    { content: "Read the auth module", status: "completed" },
    { content: "Extract the token service", status: "in_progress" },
    { content: "Run the tests", status: "pending" }
  ] });
  await h.settle(10);

  const widget = h.document.getElementById("todo-widget");
  assert.ok(widget._ctrl.isCollapsed(), "it folds itself once work is under way");
  assert.strictEqual(widget.querySelector(".plan-count").textContent, "1/3");
  assert.strictEqual(
    widget.querySelector(".plan-at").textContent,
    "Extract the token service",
    "and names the item being worked on, so a plan that stops moving reads as stale"
  );

  // Nothing under way: it names what is waiting instead.
  h.post({ type: "plan", entries: [
    { content: "Read the auth module", status: "completed" },
    { content: "Extract the token service", status: "pending" }
  ] });
  await h.settle(10);
  const at = widget.querySelector(".plan-at");
  assert.strictEqual(at.textContent, "Extract the token service");
  assert.ok(at.classList.contains("plan-at-next"), "marked as what is next rather than what is happening");
  assert.strictEqual(h.errors().length, 0);
});

test("the plan remembers a manual collapse for the next run in the session", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  // All pending -> the plan opens expanded (no auto-collapse yet).
  h.post({ type: "plan", entries: [{ content: "Task", status: "pending" }] });
  await h.settle(10);
  const widget = h.document.getElementById("todo-widget");
  let plan = widget.querySelector(".plan-docked");
  assert.ok(!plan.classList.contains("dv-collapsed"), "the plan starts expanded");

  // The user collapses it by hand.
  plan.querySelector(".dv-collapsible-header").click();
  await h.settle(10);
  assert.ok(plan.classList.contains("dv-collapsed"), "the plan collapses on the user's click");

  // Finish the turn (docked plan is cleared) and start a new one that raises a
  // fresh plan: it must stay collapsed, honouring the remembered choice.
  h.post({ type: "busy", value: true });
  h.post({ type: "busy", value: false });
  await h.settle(10);
  h.post({ type: "plan", entries: [{ content: "Task", status: "in_progress" }] });
  await h.settle(10);
  plan = widget.querySelector(".plan-docked");
  assert.ok(plan.classList.contains("dv-collapsed"), "the plan stays collapsed for the next run");

  // A session change (clear) forgets the preference: a fresh plan auto-decides.
  h.post({ type: "clear" });
  h.post({ type: "plan", entries: [{ content: "Task", status: "pending" }] });
  await h.settle(10);
  plan = widget.querySelector(".plan-docked");
  assert.ok(!plan.classList.contains("dv-collapsed"), "a new session forgets the collapse preference");
  assert.strictEqual(h.errors().length, 0);
});

test("host-initiated openSession opens the session like a click", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    sessions: [{ id: "aaa", short_id: "aaa", title: "Background one", working_directory: "/w" }],
    activeId: null,
    statuses: { aaa: "attention" }
  });
  await h.settle(10);
  h.post({ type: "body", body: "thread" });
  h.post({ type: "openSession", id: "aaa" });
  await h.settle(10);

  // An attention (alive, no cached view yet) session opens via a full load.
  assert.ok(h.posted.some((m) => m.type === "loadSession" && m.id === "aaa"), "opens the session");
  assert.strictEqual(h.errors().length, 0);
});

test("a re-posted question replaces the open one instead of stacking", async () => {
  // The host re-posts every outstanding request whenever a session is reopened,
  // so going back and forth used to leave one copy of the same question per
  // visit, until they covered the whole transcript.
  const h = createHarness();
  const question = {
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "Which branch?",
    schema: {
      type: "object",
      required: ["q0"],
      properties: {
        q0: { type: "string", title: "Branch", description: "Branch", oneOf: [{ const: "main", title: "main" }] }
      }
    }
  };
  const tray = () => h.document.getElementById("elicitation-tray");
  const sessions = (activeId) => ({
    type: "sessions",
    activeId,
    statuses: { A: "attention", B: "idle" },
    folders: [{ path: "/w", name: "w" }],
    sessions: [
      { id: "A", short_id: "A", title: "A", working_directory: "/w" },
      { id: "B", short_id: "B", title: "B", working_directory: "/w" }
    ]
  });
  const rowFor = (title) =>
    [...h.document.querySelectorAll("#sessions-list .session-item")]
      .find((r) => r.textContent.includes(title))
      .querySelector(".session-main");

  // Open B first so it has a cached transcript, then A, which asks a question.
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "sessionReady", sessionId: "B" });
  h.post({ type: "userChunk", text: "hello from B" });
  h.post({ type: "assistantEnd" });
  h.post({ type: "body", body: "list" });
  h.post(sessions(null));
  await h.settle(20);
  rowFor("A").click();
  await h.settle(10);
  h.post({ type: "clear", loading: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userChunk", text: "hello from A" });
  h.post({ type: "loaded" });
  h.post(question);
  h.post(sessions("A"));
  await h.settle(20);
  assert.strictEqual(tray().querySelectorAll(".qc").length, 1, "A shows its question");

  // Switch to B: the question belongs to A, so it must not hang over B's thread.
  h.document.getElementById("history-btn").click();
  h.post(sessions(null));
  await h.settle(10);
  rowFor("B").click();
  await h.settle(10);
  assert.strictEqual(tray().querySelectorAll(".qc").length, 0, "B does not show A's question");

  // Go back to A twice; the host re-posts the same still-pending request each
  // time and it must never stack.
  for (let i = 0; i < 2; i++) {
    h.document.getElementById("history-btn").click();
    h.post(sessions(null));
    await h.settle(10);
    rowFor("A").click();
    h.post(question);
    await h.settle(10);
    assert.strictEqual(tray().querySelectorAll(".qc").length, 1, "only ever one copy of the question");
    h.document.getElementById("history-btn").click();
    h.post(sessions(null));
    await h.settle(10);
    rowFor("B").click();
    await h.settle(10);
  }
  assert.strictEqual(h.errors().length, 0, "switching threw: " + JSON.stringify(h.errors()));
});

test("a re-posted permission replaces the open one instead of stacking", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  const perm = {
    type: "permission",
    requestId: "p1",
    title: "Vtx wants to run: `git push`",
    options: [{ optionId: "allow", name: "Allow", kind: "allow" }, { optionId: "reject", name: "Reject", kind: "reject" }]
  };
  h.post(perm);
  h.post(perm);
  h.post(perm);
  await h.settle(10);
  assert.strictEqual(
    h.document.getElementById("permission-tray").querySelectorAll(".cw").length,
    1,
    "the same permission request renders once"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a replayed message renders its text as soon as it arrives", async () => {
  // On a session load a whole message arrives as one chunk. Waiting for the next
  // frame to render it left an empty bubble between the tool cards, so the
  // transcript came back as tool calls separated by blank gaps.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "userChunk", text: "the original question" });
  h.post({ type: "assistantChunk", text: "the first answer" });
  h.post({ type: "toolCall", id: "t1", title: "Read src/a.ts", kind: "read", status: "completed" });
  h.post({ type: "assistantChunk", text: "the second answer" });

  // No settle: nothing has had a chance to run on a later frame yet.
  assert.deepStrictEqual(h.respTexts(), ["the first answer", "the second answer"]);
  h.post({ type: "loaded" });
  await h.settle(20);
  assert.deepStrictEqual(h.respTexts(), ["the first answer", "the second answer"]);
  assert.strictEqual(h.errors().length, 0);
});

test("the sessions switcher offers the same New Session split button", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "sessions", sessions: [], activeId: null, statuses: {} });
  await h.settle(10);

  // Narrow surfaces (and jsdom, which has no layout) open the switcher menu
  // rather than the docked panel; both mount the same list controls.
  h.document.getElementById("panel-toggle").click();
  await h.settle(10);
  const split = h.document.querySelector("#title-menu .session-toolbar .new-session-split");
  assert.ok(split, "the switcher toolbar has the split control");
  split.querySelector(".new-session-btn").click();
  await h.settle(5);
  assert.ok(
    h.posted.some((m) => m.type === "newSessionAt" && m.target === "view"),
    "the icon half starts a session here"
  );
  assert.ok(!h.document.querySelector(".dv-menu"), "no menu is opened by the icon half");
  split.querySelector(".new-session-more").click();
  await h.settle(10);
  assert.deepStrictEqual(
    [...h.document.querySelectorAll(".dv-menu .dv-menu-item span")].map((s) => s.textContent),
    ["New Session (Editor)", "New Session (Window)", "New Vtx CLI Session (Terminal)"],
    "the menu offers the other places, not the one the button already does"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a replayed thought is labelled without a made up duration", async () => {
  // Nothing records how long Vtx thought for, so a reloaded transcript used to
  // time the replay itself and claim "Thought for 1s" for every block.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "userChunk", text: "a question" });
  h.post({ type: "thoughtChunk", text: "old reasoning", replayed: true, at: "2026-08-05T16:21:07.110866+00:00" });
  h.post({ type: "assistantChunk", text: "an answer" });
  h.post({ type: "loaded" });
  await h.settle(20);
  const replayedLabel = h.document.querySelector(".thinking-label");
  assert.strictEqual(replayedLabel.textContent, "Thought");
  // The CLI does record when it happened, so that is offered on hover instead.
  assert.match(replayedLabel.title, /^Thought at \d/, "the original time is shown on hover");

  // A live turn still reports how long it took.
  h.post({ type: "assistantStart" });
  h.post({ type: "thoughtChunk", text: "live reasoning" });
  await h.settle(20);
  const live = [...h.document.querySelectorAll(".thinking-label")].pop();
  assert.match(live.textContent, /^Thinking/, "a live thought is timed while it streams");
  h.post({ type: "assistantEnd" });
  await h.settle(10);
  assert.match(live.textContent, /^Thought for \d+s$/, "and keeps its duration once settled");
  assert.strictEqual(h.errors().length, 0);
});

test("Enter steps through the questions and submits the last one", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "Two questions",
    allowOther: true,
    schema: {
      type: "object",
      required: ["q0", "q1"],
      properties: {
        q0: { type: "string", title: "First", description: "First", oneOf: [{ const: "a", title: "A" }, { const: "b", title: "B" }] },
        q1: { type: "string", title: "Second", description: "Second", oneOf: [{ const: "c", title: "C" }] }
      }
    }
  });
  await h.settle(20);
  const qc = h.document.querySelector(".qc");
  const step = qc.querySelector(".qc-step");
  const enter = (target, init) =>
    target.dispatchEvent(new h.window.KeyboardEvent("keydown", Object.assign({ key: "Enter", bubbles: true, cancelable: true }, init)));
  const fields = [...qc.querySelectorAll(".elicit-field")];

  // Answer the first question, then Enter moves on rather than submitting.
  fields[0].querySelectorAll(".elicit-native")[0].click();
  await h.settle(5);
  assert.strictEqual(step.textContent, "1 / 2");
  enter(qc);
  await h.settle(5);
  assert.strictEqual(step.textContent, "2 / 2", "Enter advances to the next question");
  assert.ok(!h.posted.some((m) => m.type === "elicitationResponse"), "and does not submit early");

  // Enter on the last question submits, once it has an answer.
  enter(qc);
  await h.settle(5);
  assert.ok(!h.posted.some((m) => m.type === "elicitationResponse"), "an unanswered last question is not submitted");
  fields[1].querySelectorAll(".elicit-native")[0].click();
  await h.settle(5);
  enter(qc);
  await h.settle(10);
  const res = h.posted.find((m) => m.type === "elicitationResponse");
  assert.deepStrictEqual(res && res.content, { q0: "a", q1: "c" }, "Enter on the last question submits every answer");
  assert.strictEqual(h.errors().length, 0);
});

test("Shift+Enter and Ctrl+Enter write a newline in an Other answer", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "One question",
    allowOther: true,
    schema: {
      type: "object",
      required: ["q0"],
      properties: { q0: { type: "string", title: "Pick", description: "Pick", oneOf: [{ const: "a", title: "A" }] } }
    }
  });
  await h.settle(20);
  const other = h.document.querySelector(".elicit-other");
  other.value = "line one";
  other.selectionStart = other.selectionEnd = other.value.length;
  const enter = (init) =>
    other.dispatchEvent(new h.window.KeyboardEvent("keydown", Object.assign({ key: "Enter", bubbles: true, cancelable: true }, init)));

  // Shift+Enter is the browser's own newline, so the widget must leave it alone.
  const shift = enter({ shiftKey: true });
  assert.strictEqual(shift, true, "Shift+Enter is not intercepted");
  assert.ok(!h.posted.some((m) => m.type === "elicitationResponse"));

  // Ctrl+Enter writes the newline itself (the browser does not).
  enter({ ctrlKey: true });
  await h.settle(5);
  assert.strictEqual(other.value, "line one\n");
  assert.ok(!h.posted.some((m) => m.type === "elicitationResponse"), "Ctrl+Enter never submits");
  assert.strictEqual(h.errors().length, 0);
});

test("switching sessions keeps each one's plan and changed files", async () => {
  const h = createHarness();
  const sessions = (activeId) => ({
    type: "sessions",
    activeId,
    statuses: { A: "idle", B: "idle" },
    folders: [{ path: "/w", name: "w" }],
    sessions: [
      { id: "A", short_id: "A", title: "A", working_directory: "/w" },
      { id: "B", short_id: "B", title: "B", working_directory: "/w" }
    ]
  });
  const rowFor = (title) =>
    [...h.document.querySelectorAll("#sessions-list .session-item")]
      .find((r) => r.textContent.includes(title))
      .querySelector(".session-main");
  const planRows = () => [...h.document.querySelectorAll("#todo-widget .plan-entry")].map((r) => r.textContent.trim());
  const wsRows = () => [...h.document.querySelectorAll("#working-set .file-pill-name")].map((r) => r.textContent.trim());

  // A is working through a plan and has changed a file.
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, showFileChanges: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "do the work" });
  h.post({ type: "assistantStart" });
  h.post({ type: "plan", entries: [{ content: "step one", status: "completed" }, { content: "step two", status: "in_progress" }] });
  h.post({ type: "fileChange", path: "/w/a.ts", added: 3, removed: 1 });
  h.post({ type: "workingSet", files: [{ path: "/w/a.ts", name: "a.ts" }] });
  h.post(sessions("A"));
  await h.settle(20);
  assert.deepStrictEqual(planRows(), ["step one", "step two"]);
  assert.deepStrictEqual(wsRows(), ["a.ts"]);

  // Leave for the list and open B, which has neither.
  h.document.getElementById("history-btn").click();
  h.post(sessions(null));
  await h.settle(10);
  rowFor("B").click();
  h.post({ type: "clear", loading: true });
  h.post({ type: "sessionReady", sessionId: "B" });
  h.post({ type: "userChunk", text: "hello from B" });
  h.post({ type: "loaded" });
  h.post({ type: "workingSet", files: [] });
  await h.settle(20);
  assert.deepStrictEqual(planRows(), [], "B does not inherit A's plan");
  assert.deepStrictEqual(wsRows(), [], "nor A's changed files");

  // Back to A: both come back, once each.
  h.document.getElementById("history-btn").click();
  h.post(sessions(null));
  await h.settle(10);
  rowFor("A").click();
  h.post({ type: "workingSet", files: [{ path: "/w/a.ts", name: "a.ts" }] });
  await h.settle(20);
  assert.deepStrictEqual(planRows(), ["step one", "step two"], "A's plan is restored");
  assert.deepStrictEqual(wsRows(), ["a.ts"], "A's changed files are restored, not doubled");
  assert.strictEqual(
    h.document.querySelectorAll("#todo-widget .plan-docked").length,
    1,
    "one docked plan widget, not one per visit"
  );
  assert.match(
    h.document.querySelector("#working-set .file-change").textContent,
    /\+3/,
    "the line counts come back with the files"
  );
  assert.strictEqual(h.errors().length, 0, "switching threw: " + JSON.stringify(h.errors()));
});

test("the sessions panel side follows the setting, and moves the toggle with it", async () => {
  const h = createHarness();
  const chat = h.document.getElementById("chat");
  // jsdom reports no layout, so say how wide the chat area is: wide enough for a
  // docked panel to begin with.
  let width = 1000;
  Object.defineProperty(chat, "clientWidth", { get: () => width, configurable: true });

  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true, panelSide: "right" });
  await h.settle(10);
  const icon = () => h.document.querySelector("#panel-toggle .codicon").className;
  assert.strictEqual(chat.dataset.panelSide, "right", "the layout is driven from one attribute");
  assert.match(icon(), /layout-sidebar-right/, "the toggle points at the side it opens on");

  h.post({ type: "capabilities", revert: true, panelSide: "left" });
  await h.settle(10);
  assert.strictEqual(chat.dataset.panelSide, "left");
  assert.match(icon(), /layout-sidebar-left/);

  // Too narrow for a docked panel: the same button becomes the session switcher,
  // whichever side it sits on.
  width = 420;
  h.post({ type: "capabilities", revert: true, panelSide: "right" });
  await h.settle(10);
  assert.match(icon(), /list-tree/, "no room for a docked panel means the switcher");
  assert.strictEqual(h.document.getElementById("panel-toggle").title, "Switch session");
  assert.strictEqual(h.errors().length, 0);
});

test("dragging the panel header to the other half moves and remembers the side", async () => {
  const h = createHarness();
  const chat = h.document.getElementById("chat");
  // Wide enough for the docked panel, with a box so the drag knows its midpoint.
  Object.defineProperty(chat, "clientWidth", { value: 1000, configurable: true });
  chat.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800 });

  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, panelSide: "right" });
  h.post({ type: "sessions", sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }], activeId: "A", statuses: { A: "idle" } });
  await h.settle(10);
  h.document.getElementById("panel-toggle").click();
  await h.settle(10);

  const toolbar = h.document.querySelector("#sessions-panel .session-toolbar");
  assert.ok(toolbar, "the docked panel is mounted");
  assert.ok(toolbar.classList.contains("session-toolbar-movable"), "and its header is the grab handle");

  const down = (x) => toolbar.dispatchEvent(new h.window.MouseEvent("mousedown", { clientX: x, clientY: 20, bubbles: true, button: 0 }));
  // The drag is tracked on the document, so that is where the rest is dispatched.
  const at = (type, x) => h.document.dispatchEvent(new h.window.MouseEvent(type, { clientX: x, clientY: 20, bubbles: true, button: 0 }));

  // A press with no movement is a click, not a move: the side must not change.
  down(800);
  at("mouseup", 800);
  await h.settle(5);
  assert.strictEqual(chat.dataset.panelSide, "right", "a click does not move the panel");
  assert.ok(!h.posted.some((m) => m.type === "setConfig"), "and nothing is written to settings");

  // Drag past the midpoint: the target edge is marked, then committed on release.
  down(800);
  at("mousemove", 700);
  at("mousemove", 200);
  assert.strictEqual(chat.dataset.dropSide, "left", "the edge it would land on is highlighted");
  assert.ok(h.document.body.classList.contains("dv-panel-dragging"), "and the pointer shows a drag");
  at("mouseup", 200);
  await h.settle(10);

  assert.strictEqual(chat.dataset.panelSide, "left", "the panel moves on release");
  assert.strictEqual(chat.dataset.dropSide, undefined, "the drop marker is cleared");
  assert.ok(!h.document.body.classList.contains("dv-panel-dragging"));
  assert.ok(
    h.posted.some((m) => m.type === "setConfig" && m.key === "sessionsPanel.side" && m.value === "left"),
    "and the choice is remembered in settings"
  );

  // Dragging back to the same side it is already on is a no-op.
  const writes = h.posted.filter((m) => m.type === "setConfig").length;
  down(200);
  at("mousemove", 100);
  assert.strictEqual(chat.dataset.dropSide, undefined, "no marker at all when it would not move");
  at("mouseup", 100);
  await h.settle(5);
  assert.strictEqual(h.posted.filter((m) => m.type === "setConfig").length, writes, "and nothing is rewritten");
  assert.strictEqual(h.errors().length, 0);
});

test("unsent text is saved as a draft and restored from the host", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  await h.settle(10);
  const input = h.document.getElementById("input");

  // Typing is saved against the session, debounced.
  input.value = "half a prompt";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(600);
  const saved = h.posted.filter((m) => m.type === "draft");
  assert.deepStrictEqual(saved[saved.length - 1], { type: "draft", id: "A", text: "half a prompt" });

  // Leaving flushes immediately, without waiting for the debounce.
  input.value = "half a prompt, plus more";
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  const onLeave = h.posted.filter((m) => m.type === "draft").pop();
  assert.deepStrictEqual(onLeave, { type: "draft", id: "A", text: "half a prompt, plus more" });
  assert.strictEqual(input.value, "", "the list gets a clean new chat box");

  // The host puts a stored draft back into an empty composer.
  h.post({ type: "draft", id: null, text: "a new chat I started earlier" });
  await h.settle(10);
  assert.strictEqual(input.value, "a new chat I started earlier");

  // A draft for a session we are no longer in is ignored.
  h.post({ type: "draft", id: "B", text: "belongs to another session" });
  await h.settle(10);
  assert.strictEqual(input.value, "a new chat I started earlier");
  assert.strictEqual(h.errors().length, 0);
});

test("a restored transcript keeps its own fresher draft over the stored one", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "hello" });
  h.post({ type: "assistantEnd" });
  await h.settle(10);
  const input = h.document.getElementById("input");
  input.value = "the freshest text";
  h.document.getElementById("history-btn").click();
  h.post({
    type: "sessions",
    sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }],
    activeId: null,
    statuses: { A: "idle" }
  });
  await h.settle(10);
  h.document.querySelector("#sessions-list .session-main").click();
  await h.settle(10);
  assert.strictEqual(input.value, "the freshest text", "the retained transcript brings its draft back");

  // The host's copy lands late and must not overwrite it.
  h.post({ type: "draft", id: "A", text: "an older copy" });
  await h.settle(10);
  assert.strictEqual(input.value, "the freshest text");
  assert.strictEqual(h.errors().length, 0);
});

test("sending clears the draft, and editing a queued message gives it back", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true });
  h.post({ type: "busy", value: true });
  h.post({ type: "queued", items: [{ id: "q1", text: "queued message" }] });
  await h.settle(10);
  const input = h.document.getElementById("input");

  input.value = "my draft";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(600);

  // Editing a queued message borrows the composer; the draft must not be
  // overwritten while it does, and comes back when the edit ends.
  const row = h.document.querySelector("#thread .queued-item");
  assert.ok(row, "the queued row renders");
  const before = h.posted.filter((m) => m.type === "draft").length;
  const editBtn = [...row.querySelectorAll(".queued-actions button")].find((b) => /edit/i.test(b.title || ""));
  assert.ok(editBtn, "the queued row offers an edit action");
  editBtn.click();
  await h.settle(10);
  assert.strictEqual(input.value, "queued message", "the composer shows the queued text");
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(600);
  assert.strictEqual(
    h.posted.filter((m) => m.type === "draft").length,
    before,
    "an edit in progress is never stored as the draft"
  );

  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await h.settle(10);
  assert.strictEqual(input.value, "my draft", "cancelling the edit hands the draft back");
  assert.strictEqual(h.errors().length, 0);
});

test("answers given before leaving a session come back with the question", async () => {
  const h = createHarness();
  const question = (answers) => ({
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "Two questions",
    allowOther: true,
    answers,
    schema: {
      type: "object",
      required: ["q0", "q1"],
      properties: {
        q0: { type: "string", title: "First", description: "First", oneOf: [{ const: "a", title: "A" }, { const: "b", title: "B" }] },
        q1: { type: "string", title: "Second", description: "Second", oneOf: [{ const: "c", title: "C" }] }
      }
    }
  });
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post(question());
  await h.settle(20);

  // Answer the first question and half type an Other answer on the second.
  const fields = () => [...h.document.querySelectorAll(".qc .elicit-field")];
  fields()[0].querySelectorAll(".elicit-native")[0].click();
  h.document.querySelector(".qc").dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await h.settle(10);
  assert.strictEqual(h.document.querySelector(".qc .qc-step").textContent, "2 / 2");
  const other = fields()[1].querySelector(".elicit-other");
  other.value = "something of my own";
  other.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(400);

  const state = h.posted.filter((m) => m.type === "answerDraft").pop();
  assert.ok(state, "the answers so far are sent to the host");
  assert.deepStrictEqual(state.state.by.q0.picked, ["a"], "the chosen option is recorded");
  assert.strictEqual(state.state.by.q1.other, "something of my own", "so is the free text");

  // Leaving flushes, and the host re-posts the question with those answers.
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  assert.strictEqual(h.document.querySelectorAll(".qc").length, 0, "the widget goes with the session");
  const flushed = h.posted.filter((m) => m.type === "answerDraft").pop();
  h.post({ type: "body", body: "thread" });
  h.post(question(flushed.state));
  await h.settle(20);

  assert.strictEqual(h.document.querySelectorAll(".qc").length, 1);
  assert.strictEqual(
    fields()[0].querySelectorAll(".elicit-native")[0].checked,
    true,
    "the option chosen before is still chosen"
  );
  assert.strictEqual(
    fields()[1].querySelector(".elicit-other").value,
    "something of my own",
    "and the half typed answer is still there"
  );
  assert.strictEqual(h.document.querySelector(".qc .qc-step").textContent, "2 / 2", "reopening on the question we were on");
  assert.strictEqual(h.errors().length, 0);
});

test("the header offers detach in the panel and attach in an editor tab", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true, surface: "view", panelSide: "right" });
  h.post({ type: "sessionStatuses", statuses: { A: "idle" }, activeId: "A" });
  await h.settle(10);
  const btn = h.document.getElementById("detach-btn");
  const header = [...h.document.getElementById("chat-header").children].map((c) => c.id);

  assert.ok(!btn.classList.contains("hidden"), "the control shows inside a session");
  // Moving the chat reads before stopping it, so terminate stays at the far end.
  assert.ok(header.indexOf("detach-btn") < header.indexOf("terminate-btn"), "it sits beside terminate, on its left");
  assert.match(btn.querySelector(".codicon").className, /link-external/);
  assert.strictEqual(btn.title, "Open this chat in an editor tab");
  btn.click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "detachSession" && m.id === "A"), "the panel asks to detach");

  // No session, no control.
  h.document.getElementById("history-btn").click();
  await h.settle(10);
  assert.ok(btn.classList.contains("hidden"), "the list has nothing to move");
  assert.strictEqual(h.errors().length, 0);
});

test("an editor tab is one chat: no list, no back, no terminate", async () => {
  const h = createHarness({ surface: "editor" });
  h.post({ type: "ready" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A", title: "Detached chat" });
  h.post({ type: "capabilities", revert: true, surface: "editor", panelSide: "left" });
  h.post({ type: "sessionStatuses", statuses: { A: "running" }, activeId: "A" });
  await h.settle(10);

  const shown = (id) => !h.document.getElementById(id).classList.contains("hidden");
  assert.ok(!shown("history-btn"), "no way back to a list it does not have");
  assert.ok(!shown("panel-toggle"), "and no sessions panel");
  assert.ok(!shown("terminate-btn"), "closing the tab is what stops the chat");
  assert.ok(!shown("header-divider"), "the divider has nothing left to divide");
  assert.ok(shown("detach-btn"), "moving it back to the side panel is the one control");
  assert.strictEqual(h.document.getElementById("detach-btn").title, "Move this chat to the side panel");
  assert.match(
    h.document.getElementById("detach-btn").querySelector(".codicon").className,
    /layout-sidebar-right-dock/,
    "the return icon points to the right sidebar"
  );
  assert.ok(h.document.getElementById("title-btn").classList.contains("as-heading"), "the title is a name, not a control");
  h.document.getElementById("title-btn").click();
  await h.settle(5);
  assert.ok(!h.posted.some((m) => m.type === "renameSession"), "the tab renames from its own context menu");

  // Which chat the tab holds is remembered, so a window reload restores it.
  assert.strictEqual(h.state().sessionId, "A");

  // Even asked to, it never falls back to a session list.
  h.post({ type: "body", body: "list" });
  await h.settle(10);
  assert.ok(h.document.getElementById("sessions-list").classList.contains("hidden"), "it stays on its chat");
  assert.strictEqual(h.errors().length, 0);
});

test("a chat open on the other surface says so instead of showing a stale copy", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    activeId: null,
    statuses: { B: "running" },
    elsewhere: ["B"],
    sessions: [{ id: "B", short_id: "B", title: "In a tab", working_directory: "/w" }]
  });
  await h.settle(10);
  // The row is honest about it before it is even clicked.
  const row = h.document.querySelector("#sessions-list .session-item");
  assert.ok(row.querySelector(".session-dot").className.includes("dot-running"), "it shows the editor tab's real status");
  assert.strictEqual(row.querySelector(".session-elsewhere").title, "Open in an editor tab");
  assert.ok(!row.querySelector(".kill-glyph"), "only the surface that owns the session can terminate it");

  row.querySelector(".session-main").click();
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "loadSession" && m.id === "B"), "the host still gets the final say");
  h.post({ type: "elsewhere", id: "B", where: "an editor tab", here: "the side panel", title: "In a tab" });
  await h.settle(10);

  assert.match(h.thread().querySelector(".welcome-title").textContent, /open in an editor tab/);
  assert.ok(h.document.getElementById("composer").classList.contains("hidden"), "and cannot be typed into");
  const actions = [...h.thread().querySelectorAll(".welcome-actions .btn")];
  assert.deepStrictEqual(actions.map((b) => b.textContent), [
    "Continue in the side panel",
    "Show it in an editor tab"
  ]);
  actions[0].click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "moveHere" && m.id === "B"), "the first brings it here, agent and all");
  actions[1].click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "revealSession" && m.id === "B"), "the second goes to it");

  // Once it is actually loaded here, the placeholder and the lock go.
  h.post({ type: "clear", loading: true });
  h.post({ type: "sessionReady", sessionId: "B", title: "In a tab" });
  h.post({ type: "loaded" });
  await h.settle(10);
  assert.ok(!h.document.getElementById("composer").classList.contains("hidden"));
  assert.strictEqual(h.errors().length, 0);
});

test("a chat leaving a surface writes back its draft and its half given answers", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({
    type: "elicitation",
    requestId: "r1",
    questions: [{ id: "q1", question: "Which one?", options: [{ id: "o1", label: "First" }] }]
  });
  await h.settle(10);
  const input = h.document.getElementById("input");
  input.value = "half written prompt";
  input.dispatchEvent(new h.window.Event("input"));
  const opt = h.document.querySelector(".qc .elicit-native");
  if (opt) { opt.checked = true; opt.dispatchEvent(new h.window.Event("change", { bubbles: true })); }

  // The handover is instant, so the host asks for both before the chat leaves and
  // waits for the reply: a debounce would lose them.
  h.posted.length = 0;
  h.post({ type: "flushState" });
  await h.settle(5);

  const draft = h.posted.find((m) => m.type === "draft");
  assert.ok(draft && draft.text === "half written prompt", "the prompt goes back with its session");
  assert.ok(h.posted.some((m) => m.type === "answerDraft" && m.requestId === "r1"), "so do the answers so far");
  const order = h.posted.map((m) => m.type);
  assert.ok(
    order.indexOf("stateFlushed") === order.length - 1,
    "and the all clear comes last, so nothing is still in flight"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a chat whose agent stopped says so in a tab, and can be started again", async () => {
  const h = createHarness({ surface: "editor" });
  h.post({ type: "ready" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", surface: "editor" });
  h.post({ type: "sessionReady", sessionId: "A", title: "Stopped chat" });
  h.post({ type: "userChunk", text: "still here" });
  h.post({ type: "loaded" });
  h.post({ type: "sessionEnded" });
  await h.settle(10);

  const row = h.thread().querySelector(".ended-row");
  assert.ok(row, "the tab has no list to say it in, so the chat says it");
  assert.match(row.textContent, /Send a message to start it again/);
  assert.deepStrictEqual(h.reqTexts(), ["still here"], "the conversation is left alone");

  // Said once, not once per exit event.
  h.post({ type: "sessionEnded" });
  await h.settle(10);
  assert.strictEqual(h.thread().querySelectorAll(".ended-row").length, 1);
  assert.strictEqual(h.errors().length, 0);
});

test("a session running on another surface is marked in the list", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({
    type: "sessions",
    activeId: null,
    statuses: { A: "idle", B: "running" },
    elsewhere: ["B"],
    sessions: [
      { id: "A", short_id: "A", title: "Here", working_directory: "/w" },
      { id: "B", short_id: "B", title: "In a tab", working_directory: "/w" }
    ]
  });
  await h.settle(10);
  const rows = [...h.document.querySelectorAll("#sessions-list .session-item")];
  const badged = rows.filter((r) => r.querySelector(".session-elsewhere")).map((r) => r.textContent);
  assert.strictEqual(badged.length, 1, "only the one held elsewhere is marked");
  assert.match(badged[0], /In a tab/);

  // The marker follows a status tick, without a full list refresh.
  h.post({ type: "sessionStatuses", statuses: { A: "idle", B: "running" }, activeId: null, elsewhere: [] });
  await h.settle(10);
  assert.strictEqual(
    h.document.querySelectorAll("#sessions-list .session-elsewhere").length,
    0,
    "and clears when the session comes back"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a chat moved from another surface arrives with nothing said about it", async () => {
  // Moving a chat is not an event in the conversation, so the transcript carries
  // no note about it: it just carries on where it left off.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userChunk", text: "carried over" });
  h.post({ type: "loaded" });
  await h.settle(10);
  assert.deepStrictEqual(h.reqTexts(), ["carried over"], "the transcript it was handed is intact");
  assert.strictEqual(h.thread().querySelectorAll(".restored-row").length, 0, "and nothing is announced");
  assert.strictEqual(h.errors().length, 0);
});

test("a chat being handed to a new surface is not sent back to the list", async () => {
  // A freshly created surface announces its readiness twice while the chat it is
  // being handed paints into it. That used to land mid load, before the session id
  // arrived, and reset the panel to its session list: the detached tab opened on
  // the list and the chat had to be clicked again.
  const h = createHarness();
  h.post({ type: "ready" });
  await h.settle(5);
  assert.ok(!h.document.getElementById("sessions-list").classList.contains("hidden"), "a new surface starts on the list");

  // The host starts handing over a chat.
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", loading: true, waking: false });
  h.post({ type: "capabilities", revert: true, surface: "editor" });
  h.post({ type: "userChunk", text: "the chat that was handed over" });

  // Its readiness lands mid load, before the session id has arrived.
  h.post({ type: "ready" });
  await h.settle(5);
  assert.ok(h.document.getElementById("sessions-list").classList.contains("hidden"), "the list does not take over");
  assert.ok(!h.thread().classList.contains("hidden"), "the thread stays on screen");

  h.post({ type: "assistantChunk", text: "with its reply" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "loaded" });
  h.post({ type: "body", body: "thread" });
  await h.settle(20);

  assert.deepStrictEqual(h.reqTexts(), ["the chat that was handed over"]);
  assert.deepStrictEqual(h.respTexts(), ["with its reply"]);
  assert.strictEqual(h.errors().length, 0);
});

test("a chat handed to a surface mid turn still knows which session it is", async () => {
  // The busy handover re-points the surface instead of reloading, so the host has
  // to name the session: without it the tab had no session id, which hid the move
  // back and terminate controls and sent its draft to the "new chat" key.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, surface: "editor" });
  h.post({ type: "busy", value: true });
  h.post({ type: "sessionReady", sessionId: "A", title: "A moved chat" });
  h.post({ type: "sessionStatuses", statuses: { A: "running" }, activeId: "A" });
  await h.settle(10);

  assert.strictEqual(h.document.getElementById("chat-title").textContent, "A moved chat", "the header names it");
  const detach = h.document.getElementById("detach-btn");
  assert.ok(!detach.classList.contains("hidden"), "the move back control is offered");
  detach.click();
  await h.settle(5);
  assert.ok(
    h.posted.some((m) => m.type === "attachSession" && m.id === "A"),
    "and it acts on the session that was handed over"
  );

  // Its draft is stored against the chat, not the new chat box.
  const input = h.document.getElementById("input");
  input.value = "typed after the move";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(600);
  assert.deepStrictEqual(h.posted.filter((m) => m.type === "draft").pop(), {
    type: "draft",
    id: "A",
    text: "typed after the move"
  });
  assert.strictEqual(h.errors().length, 0);
});

test("the drop marker only shows when the panel would actually move", async () => {
  const h = createHarness();
  const chat = h.document.getElementById("chat");
  Object.defineProperty(chat, "clientWidth", { value: 1000, configurable: true });
  chat.getBoundingClientRect = () => ({ left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800 });
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, panelSide: "right" });
  h.post({ type: "sessions", sessions: [{ id: "A", short_id: "A", title: "A", working_directory: "/w" }], activeId: "A", statuses: { A: "idle" } });
  await h.settle(10);
  h.document.getElementById("panel-toggle").click();
  await h.settle(10);
  const toolbar = h.document.querySelector("#sessions-panel .session-toolbar");
  const at = (type, x) => h.document.dispatchEvent(new h.window.MouseEvent(type, { clientX: x, clientY: 20, bubbles: true, button: 0 }));

  toolbar.dispatchEvent(new h.window.MouseEvent("mousedown", { clientX: 800, clientY: 20, bubbles: true, button: 0 }));
  at("mousemove", 700);
  // Still in the half it is already docked in: no marker at all, since an empty
  // attribute still matched the selector and drew it on the left edge.
  assert.strictEqual(chat.dataset.dropSide, undefined, "no marker while the drop is a no-op");
  at("mousemove", 200);
  assert.strictEqual(chat.dataset.dropSide, "left", "and the target edge once it would move");

  // Losing the pointer ends the drag without moving anything.
  h.window.dispatchEvent(new h.window.Event("blur"));
  await h.settle(5);
  assert.strictEqual(chat.dataset.dropSide, undefined, "the marker is cleared");
  assert.ok(!h.document.body.classList.contains("dv-panel-dragging"), "and the drag cursor with it");
  at("mouseup", 200);
  await h.settle(5);
  assert.strictEqual(chat.dataset.panelSide, "right", "an interrupted drag never moves the panel");
  assert.ok(!h.posted.some((m) => m.type === "setConfig"), "nor writes a side to settings");
  assert.strictEqual(h.errors().length, 0);
});

test("stepping a question carousel moves focus, so Enter keeps working", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "elicitation",
    requestId: "e1",
    mode: "form",
    message: "Three questions",
    schema: {
      type: "object",
      required: ["q0", "q1", "q2"],
      properties: {
        q0: { type: "string", title: "One", description: "One", oneOf: [{ const: "a", title: "A" }] },
        q1: { type: "string", title: "Two", description: "Two", oneOf: [{ const: "b", title: "B" }] },
        q2: { type: "string", title: "Three", description: "Three", oneOf: [{ const: "c", title: "C" }] }
      }
    }
  });
  await h.settle(20);
  const qc = h.document.querySelector(".qc");
  const fields = [...qc.querySelectorAll(".elicit-field")];
  const step = () => qc.querySelector(".qc-step").textContent;

  // A question arriving must not steal focus from the composer.
  assert.ok(!qc.contains(h.document.activeElement), "the widget does not grab focus on arrival");
  // Once the user is inside it (clicking an option focuses the native radio), Enter
  // is handled on the widget, so focus has to stay inside it as it steps.
  fields[0].querySelector(".elicit-native").focus();
  const enter = () => h.document.activeElement.dispatchEvent(
    new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })
  );
  enter();
  await h.settle(5);
  assert.strictEqual(step(), "2 / 3");
  assert.ok(fields[1].contains(h.document.activeElement), "focus follows to the question now shown");
  enter();
  await h.settle(5);
  assert.strictEqual(step(), "3 / 3", "so a second Enter still reaches the widget");
  assert.strictEqual(h.errors().length, 0);
});

test("a checkpoint restore keeps the notice that the agent stopped", async () => {
  const h = createHarness({ surface: "editor" });
  h.post({ type: "ready" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", surface: "editor" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userChunk", text: "carried over" });
  h.post({ type: "loaded" });
  h.post({ type: "sessionEnded" });
  await h.settle(10);
  assert.ok(h.thread().querySelector(".ended-row"), "the notice is there");

  h.post({ type: "reverted", head: 3 });
  await h.settle(10);
  assert.ok(h.thread().querySelector(".ended-row"), "and survives a restore, which has its own divider");
  assert.deepStrictEqual(h.reqTexts(), ["carried over"], "and so does the conversation");
  assert.strictEqual(h.errors().length, 0);
});

test("starting a new chat clears the previous chat's thread controls", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "capabilities", revert: true, surface: "view" });
  h.post({ type: "sessionStatuses", statuses: { A: "idle" }, activeId: "A" });
  await h.settle(10);
  assert.ok(!h.document.getElementById("detach-btn").classList.contains("hidden"));

  // A fresh chat has no session yet, so neither control can act.
  h.post({ type: "clear", reset: true });
  await h.settle(10);
  assert.ok(h.document.getElementById("detach-btn").classList.contains("hidden"), "move is withdrawn");
  assert.ok(h.document.getElementById("terminate-btn").classList.contains("hidden"), "terminate is withdrawn");
  assert.strictEqual(h.errors().length, 0);
});

test("sharing is offered only for a chat the agent can share", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "sessionStatuses", statuses: { A: "idle" }, activeId: "A" });
  // An older CLI has no share method, so the button must not be there to press.
  h.post({ type: "capabilities", surface: "view" });
  await h.settle(10);
  const btn = h.document.getElementById("share-btn");
  assert.ok(btn.classList.contains("hidden"), "nothing to press against an agent that cannot share");

  h.post({ type: "capabilities", surface: "view", sessionShare: true });
  await h.settle(10);
  assert.ok(!btn.classList.contains("hidden"), "the agent says it can, so the control appears");
  btn.click();
  await h.settle(5);
  assert.ok(h.posted.some((m) => m.type === "shareSession"), "and it asks the host for a link");

  // The list has no chat to share, so the control goes with the rest of them.
  h.post({ type: "clear", reset: true });
  await h.settle(10);
  assert.ok(btn.classList.contains("hidden"), "and is withdrawn with the other session controls");
  assert.strictEqual(h.errors().length, 0);
});

test("a permission prompt says what it would run", async () => {
  // Vtx asks about a command without a title, carrying the command in _meta,
  // so a prompt that only repeats "a tool" cannot be answered.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "permission",
    requestId: "p1",
    title: "Vtx wants to run a command",
    command: "pwd && whoami && date",
    toolCallId: "t1",
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  await h.settle(10);
  const box = h.document.querySelector("#permission-tray .cw");
  assert.match(box.querySelector(".cw-title").textContent, /run a command/);
  assert.strictEqual(box.querySelector(".tool-command code").textContent, "pwd && whoami && date",
    "the command it wants to run is shown");
  assert.strictEqual(h.errors().length, 0);
});

test("an edit looks the same however it arrives, and reads as one line", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "userChunk", text: "change two files" });
  // A run of tools, with an edit in the middle of it: the edit is its own row, not
  // a tool section inside the group with the file folded away.
  h.post({ type: "toolCall", id: "t1", title: "Read src/a.ts", kind: "read", status: "completed", locations: [{ path: "/w/src/a.ts" }] });
  h.post({ type: "toolCall", id: "t2", title: "Edit src/a.ts", kind: "edit", status: "in_progress", rawInput: { path: "/w/src/a.ts" } });
  h.post({
    type: "toolCall",
    id: "t2",
    status: "completed",
    content: [{ type: "diff", path: "/w/src/a.ts", added: 12, removed: 3 }]
  });
  await h.settle(20);

  const rows = [...h.thread().querySelectorAll(".edit-pill")];
  assert.strictEqual(rows.length, 1, "one row for the file, not one per update");
  assert.match(rows[0].textContent, /Edited/);
  assert.match(rows[0].querySelector(".file-pill-name").textContent, /^a\.ts$/, "named, not a full path");
  assert.match(rows[0].querySelector(".edit-pill-status").className, /codicon-edit/, "the same pencil everywhere");
  assert.strictEqual(rows[0].querySelector(".label-added").textContent, "+12");
  assert.strictEqual(
    [...h.thread().querySelectorAll(".tool .tool-verb")].map((v) => v.textContent).join(","),
    "Read",
    "the edit is not also a tool section"
  );

  // The same edit arriving as a live change event gains Keep and Undo, and stays
  // one row.
  h.post({ type: "fileChange", path: "/w/src/a.ts", added: 12, removed: 3 });
  await h.settle(10);
  const live = [...h.thread().querySelectorAll(".edit-pill")];
  assert.strictEqual(live.length, 1, "still one row");
  assert.strictEqual(live[0].querySelectorAll(".edit-pill-actions button").length, 2, "now it can be kept or undone");

  // A created file says so, with the same pencil.
  h.post({ type: "fileChange", path: "/w/src/new.ts", added: 40, removed: 0, created: true });
  await h.settle(10);
  const created = [...h.thread().querySelectorAll(".edit-pill")].pop();
  assert.match(created.textContent, /Created/);
  assert.match(created.querySelector(".edit-pill-status").className, /codicon-edit/);
  assert.strictEqual(h.errors().length, 0);
});

test("a file the agent read is one line that opens it", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({
    type: "toolCall",
    id: "r1",
    title: "Read /outside/the/workspace/notes.md",
    kind: "read",
    status: "completed",
    locations: [{ path: "/outside/the/workspace/notes.md", line: 12 }],
    content: [{ type: "text", text: "the whole file, which belongs in an editor" }]
  });
  await h.settle(20);

  const tool = h.thread().querySelector(".tool");
  assert.strictEqual(tool.querySelector(".tool-verb").textContent, "Read");
  assert.strictEqual(tool.querySelector(".tool-detail").textContent, " notes.md", "the file, not its path");
  assert.ok(tool.classList.contains("dv-nocollapse"), "there is nothing to expand");
  assert.strictEqual(tool.querySelector(".tool-body").childElementCount, 0, "the file is not dumped in the chat");
  const head = tool.querySelector(".dv-collapsible-header");
  assert.strictEqual(head.title, "/outside/the/workspace/notes.md");
  head.click();
  await h.settle(5);
  assert.deepStrictEqual(
    h.posted.filter((m) => m.type === "openFile"),
    [{ type: "openFile", path: "/outside/the/workspace/notes.md", line: 12 }],
    "clicking opens the real file, wherever it lives"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("a permission prompt with only a tool call names that tool", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "toolCall", id: "t9", status: "in_progress", title: "Edit src/auth/token.ts", kind: "edit" });
  h.post({
    type: "permission",
    requestId: "p2",
    title: "Vtx wants to run a tool",
    toolCallId: "t9",
    options: [{ optionId: "allow_once", name: "Allow", kind: "allow_once" }]
  });
  await h.settle(10);
  const box = h.document.querySelector("#permission-tray .cw");
  assert.strictEqual(box.querySelector(".cw-detail .cw-message").textContent, "Edit src/auth/token.ts",
    "it falls back to the tool line already in the transcript");

  // A file it names is shown as a pill.
  h.post({
    type: "permission",
    requestId: "p3",
    title: "Vtx wants to run a tool",
    locations: [{ path: "/w/src/auth/token.ts" }],
    options: [{ optionId: "allow_once", name: "Allow", kind: "allow_once" }]
  });
  await h.settle(10);
  const p3 = h.document.querySelector('#permission-tray [data-request-id="p3"]');
  const p3File = p3.querySelector(".file-change");
  assert.strictEqual(p3File.querySelector(".file-pill-name").textContent, "token.ts",
    "and names the file it would touch");
  assert.ok(p3File.classList.contains("file-pill"), "normal file references use the shared file pill class");
  assert.ok(p3File.querySelector(".file-pill-icon"), "normal file references use the shared file icon class");
  p3File.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.ok(h.posted.some((m) => m.type === "openFile" && m.path === "/w/src/auth/token.ts"),
    "normal file references open from the keyboard");
  assert.strictEqual(h.errors().length, 0);
});

test("waiting on a subagent reads as agent work, not a tool dump", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "toolCall",
    id: "r1",
    status: "in_progress",
    title: "Checked on subagent",
    kind: "other",
    meta: { inferenceToolName: "read_subagent" },
    rawInput: { agent_id: "78cc5558", block: true, timeout: 600 }
  });
  await h.settle(20);
  const card = [...h.document.querySelectorAll("#thread .tool")].find((t) => /Checked on subagent/.test(t.textContent));
  assert.match(card.querySelector(".tool-kind").className, /codicon-copilot-in-progress/,
    "it is drawn as an agent in progress");
  const pairs = [...card.querySelectorAll(".tool-summary")].map((r) => [
    r.querySelector(".tool-summary-label").textContent,
    r.querySelector(".tool-summary-value").textContent
  ]);
  assert.deepStrictEqual(pairs, [
    ["Agent", "78cc5558"],
    ["Waiting", "until it responds, up to 10 min"]
  ]);
  assert.strictEqual(card.querySelector(".tool-section-title"), null, "and never dumps its arguments");

  h.post({ type: "toolCallUpdate", id: "r1", status: "completed", meta: { inferenceToolName: "read_subagent" } });
  await h.settle(20);
  assert.match(card.querySelector(".tool-kind").className, /codicon-copilot-success/,
    "and becomes a tick when the agent reports back");
  assert.strictEqual(h.errors().length, 0);
});

test("a reasoning block leads with the thinking glyph", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "assistantStart" });
  h.post({ type: "thoughtChunk", text: "Weighing two options", mid: "m1" });
  await h.settle(10);
  const header = h.thread().querySelector(".thinking > .dv-collapsible-header");
  assert.ok(header.querySelector(".thinking-glyph.codicon-thinking"), "the glyph is there");
  assert.ok(
    [...header.children].indexOf(header.querySelector(".thinking-glyph")) <
      [...header.children].indexOf(header.querySelector(".thinking-label")),
    "before the label"
  );
  h.post({ type: "assistantEnd" });
  await h.settle(10);
  assert.strictEqual(h.errors().length, 0);
});

test("the header no longer narrates the running tool", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "toolCall", id: "t1", status: "in_progress", title: "Ran npm test", kind: "execute" });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("status"), null, "the widget is gone");
  assert.strictEqual(h.document.getElementById("chat-title").textContent, "Chat", "and the title is left alone");
  assert.strictEqual(h.errors().length, 0);
});

test("a chat never opens holding text typed somewhere else", async () => {
  // A surface fills its new chat box before it is handed a session, so the
  // composer could open a moved chat with the sessions list's unsent text in it.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "draft", id: null, text: "half typed in the new chat box" });
  await h.settle(10);
  const input = h.document.getElementById("input");
  assert.strictEqual(input.value, "half typed in the new chat box");

  h.post({ type: "body", body: "thread" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "draft", id: "A", text: "this chat's own prompt" });
  await h.settle(10);
  assert.strictEqual(input.value, "this chat's own prompt", "the chat's own draft wins");

  // Text the user has actually typed into this chat is never replaced.
  input.value = "mine, typed here";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(500);
  h.post({ type: "draft", id: "A", text: "a stale copy from the host" });
  await h.settle(10);
  assert.strictEqual(input.value, "mine, typed here");
  assert.strictEqual(h.errors().length, 0);
});

test("the header divider never stands on its own", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "capabilities", revert: true, panelSide: "right", surface: "view" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "sessionStatuses", statuses: { A: "idle" }, activeId: "A" });
  await h.settle(10);
  const divider = h.document.getElementById("header-divider");
  assert.ok(!divider.classList.contains("hidden"), "it separates the controls from the toggle");

  // A chat with nothing to terminate or move leaves nothing to separate.
  h.post({ type: "clear", reset: true });
  await h.settle(10);
  assert.ok(divider.classList.contains("hidden"), "so it goes");

  // On the left the toggle leads the header, so the divider always belongs.
  h.post({ type: "capabilities", revert: true, panelSide: "left", surface: "view" });
  await h.settle(10);
  assert.ok(!divider.classList.contains("hidden"));
  assert.strictEqual(h.errors().length, 0);
});

test("Enter answers the option it is on before moving to the next question", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "elicitation",
    requestId: "e2",
    mode: "form",
    message: "Two questions",
    schema: {
      type: "object",
      required: ["q0", "q1"],
      properties: {
        q0: { type: "string", title: "One", description: "One", oneOf: [{ const: "a", title: "A" }, { const: "b", title: "B" }] },
        q1: { type: "string", title: "Two", description: "Two", oneOf: [{ const: "c", title: "C" }] }
      }
    }
  });
  await h.settle(20);
  const qc = h.document.querySelector(".qc");
  const first = qc.querySelector(".elicit-field .elicit-native");
  first.focus();
  first.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await h.settle(10);
  assert.ok(first.checked, "the focused option is answered, not skipped");
  assert.strictEqual(qc.querySelector(".qc-step").textContent, "2 / 2", "and it still moves on");
  assert.strictEqual(h.errors().length, 0);
});



// --- Announcements ---------------------------------------------------------
// Everything here happens without the user looking at the panel, and the
// transcript fills in silently, so without an announcement a screen reader user
// gets no signal that Vtx started, finished, or is blocked waiting on them.

test("the panel says out loud when a turn starts, ends, and is stopped", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const said = () => h.document.querySelector("#announcer").textContent.replace(/\u200b/g, "");

  h.post({ type: "userChunk", text: "hi" });
  h.post({ type: "assistantStart" });
  await h.settle(10);
  assert.match(said(), /working/i);

  h.post({ type: "assistantEnd", stopReason: "end_turn" });
  await h.settle(10);
  assert.match(said(), /finished/i);

  h.post({ type: "assistantStart" });
  h.post({ type: "assistantEnd", stopReason: "cancelled" });
  await h.settle(10);
  assert.match(said(), /stopped/i, "a stop that landed is the one thing a user is waiting to hear");
  assert.strictEqual(h.errors().length, 0);
});

test("a request for a decision is announced and marked as a dialog", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "userChunk", text: "tidy up" });
  h.post({
    type: "permission",
    requestId: "p1",
    command: "rm -rf build",
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  await h.settle(20);
  const said = h.document.querySelector("#announcer").textContent;
  assert.match(said, /permission/i);
  assert.match(said, /rm -rf build/, "what it wants to run is the whole question");
  const widget = h.document.querySelector("#permission-tray [role='dialog']");
  assert.ok(widget, "a request for a decision is a dialog");
  // An unnamed dialog is announced as just "dialog", which says nothing about
  // what is being asked.
  assert.ok((widget.getAttribute("aria-label") || "").trim(), "and it is a named one");
  // Focus must stay where the user put it: a question arriving mid sentence must
  // not take the composer away.
  assert.notStrictEqual(h.document.activeElement, widget);
  assert.strictEqual(h.errors().length, 0);
});

test("the transcript is a log and the composer is labelled", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  await h.settle(10);
  assert.strictEqual(h.document.querySelector("#thread").getAttribute("role"), "log");
  assert.ok(h.document.querySelector("#thread").getAttribute("aria-label"));
  // A placeholder is not a label: it disappears the moment anything is typed.
  const input = h.document.querySelector("#input");
  assert.match(input.getAttribute("aria-label"), /ask vtx/i);
  // The same box starts a new chat in the list, and the label has to follow it:
  // it overrides the placeholder, so a stale one is what gets read out.
  h.post({ type: "body", body: "list" });
  await h.settle(10);
  assert.match(input.getAttribute("aria-label"), /new chat/i);
  const announcer = h.document.querySelector("#announcer");
  assert.strictEqual(announcer.getAttribute("aria-live"), "polite");
  assert.ok(!announcer.classList.contains("hidden"), "display:none would remove it from the accessibility tree");
});

test("a permission prompt keeps the broad grants behind a chevron, and records the answer", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "userChunk", text: "run something" });
  // The six options a real agent sends for a single shell command. Rendered flat,
  // "always allow in all projects" and "switch to bypass mode" look exactly as
  // ordinary as "Allow".
  h.post({
    type: "permission",
    requestId: "p1",
    title: "Vtx wants to run a command",
    command: "exit 42",
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "allow_session", name: "Yes, allow `exit` commands (this session)", kind: "allow_always" },
      { optionId: "allow_always", name: "Yes, always allow `exit` commands in `vtx-vscode`", kind: "allow_always" },
      { optionId: "allow_always_global", name: "Yes, always allow `exit` commands in all projects", kind: "allow_always" },
      { optionId: "switch_bypass", name: "Yes, switch to bypass mode", kind: "allow_always" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  await h.settle(20);

  const tray = h.document.querySelector("#permission-tray");
  const labels = [...tray.querySelectorAll(".cw-buttons button")].map((b) => b.textContent.trim() || b.getAttribute("aria-label"));
  assert.deepStrictEqual(labels, ["Allow", "More ways to allow this", "Reject"], "only the narrow yes and the no are buttons");

  const more = tray.querySelector(".cw-more");
  more.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(20);
  const items = [...h.document.querySelectorAll(".dv-menu .dv-menu-item")];
  assert.strictEqual(items.length, 4, "every grant that outlives the request is one level in");

  items[1].dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(20);
  assert.deepStrictEqual(h.posted.filter((m) => m.type === "permission").pop(), {
    type: "permission",
    requestId: "p1",
    optionId: "allow_always"
  });
  // A grant that keeps applying has to leave a trace, or there is no way to see
  // later what was allowed.
  const recap = h.document.querySelector(".perm-recap");
  assert.ok(recap, "the decision is recorded in the transcript");
  assert.match(recap.textContent, /always allow .*exit.* in .*vtx-vscode.*: exit 42/);
  assert.strictEqual(h.errors().length, 0);
});

test("a plain allow or reject still records what happened", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "userChunk", text: "run something" });
  h.post({
    type: "permission",
    requestId: "p2",
    command: "rm -rf build",
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  await h.settle(20);
  const tray = h.document.querySelector("#permission-tray");
  // With nothing broader on offer there is no chevron to show.
  assert.strictEqual(tray.querySelector(".cw-more"), null);
  [...tray.querySelectorAll(".cw-buttons button")].find((b) => b.textContent.trim() === "Reject").click();
  await h.settle(20);
  const recap = h.document.querySelector(".perm-recap");
  assert.ok(recap.classList.contains("rejected"));
  assert.strictEqual(recap.textContent.trim(), "Rejected: rm -rf build");
  assert.strictEqual(h.errors().length, 0);
});

test("a turn can be forked into a new chat without discarding anything", async () => {
  const h = createHarness();
  h.replay([{ role: "user", text: "first" }, { role: "assistant", text: "answer" }]);
  await h.settle(20);
  // A head arrives after each turn, and becomes the next turn's rewind point.
  h.post({ type: "turnHead", head: 31, reliable: true });
  h.post({ type: "userChunk", text: "second" });
  h.post({ type: "assistantChunk", text: "answer 2" });
  h.post({ type: "assistantEnd", stopReason: "end_turn" });
  await h.settle(20);

  const fork = h.document.querySelector(".checkpoint-fork");
  assert.ok(fork, "a turn with a rewind point can also be forked from");
  assert.ok(fork.getAttribute("aria-label"), "an icon with no text still needs a name");
  fork.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(10);

  const msg = h.posted.filter((m) => m.type === "revertFork").pop();
  // The same node a restore would rewind to, which is a valid fork point: the
  // agent branches there into a session of its own.
  assert.deepStrictEqual(msg, { type: "revertFork", target: 31 });
  // Forking discards nothing, so it must not ask about discarding edits.
  assert.strictEqual(h.posted.filter((m) => m.type === "revertPreview").length, 0);
  assert.strictEqual(h.errors().length, 0);
});

test("only the newest edit row for a file offers to keep or undo it", async () => {
  // Keeping and undoing are per file: the working set holds one original, from
  // before this chat first touched it. An older row's Undo therefore wound the file
  // back past every edit after it, from a button beside a diff showing only its own.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "capabilities", revert: true, showFileChanges: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "edit it twice" });
  h.post({ type: "fileChange", path: "/w/app.ts", added: 3, removed: 1, editId: "A#1#/w/app.ts" });
  await h.settle(20);
  const first = [...h.thread().querySelectorAll(".edit-pill")].pop();
  assert.ok(first && first.querySelector(".edit-pill-actions"), "the only row so far can be acted on");

  // The same file again, in a later turn.
  h.post({ type: "assistantEnd" });
  h.post({ type: "userMessage", text: "and again" });
  h.post({ type: "fileChange", path: "/w/app.ts", added: 2, removed: 0, editId: "A#2#/w/app.ts" });
  await h.settle(20);

  const rows = [...h.thread().querySelectorAll(".edit-pill")];
  const withActions = rows.filter((r) => r.querySelector(".edit-pill-actions"));
  assert.strictEqual(withActions.length, 1, "only one row still offers the decision");
  assert.strictEqual(withActions[0], rows[rows.length - 1], "and it is the newest one");
  assert.match(rows[0].title || "", /edited again later/i, "the older row says why it does not");
  assert.strictEqual(h.errors().length, 0);
});

test("a turn starting does not take the keyboard away from a control", async () => {
  // Every turn's controls are rebuilt when the busy state flips, which replaces the
  // elements. A keyboard user standing on one was dropped back to the top of the
  // panel each time a turn started or ended.
  const h = createHarness();
  h.replay([{ role: "user", text: "first" }, { role: "assistant", text: "answer" }]);
  await h.settle(20);
  h.post({ type: "turnHead", head: 31, reliable: true });
  h.post({ type: "userChunk", text: "second" });
  h.post({ type: "assistantChunk", text: "answer 2" });
  h.post({ type: "assistantEnd", stopReason: "end_turn" });
  await h.settle(20);

  const btn = h.document.querySelector("#thread .checkpoint-restore, #thread .footer-copy, #thread .msg-copy");
  assert.ok(btn, "there is a control in the transcript to stand on");
  btn.focus();
  assert.strictEqual(h.document.activeElement, btn, "focus is on it");

  h.post({ type: "busy", value: true });
  await h.settle(10);
  assert.strictEqual(h.document.activeElement, btn, "and a turn starting leaves it there");
  h.post({ type: "busy", value: false });
  await h.settle(10);
  assert.strictEqual(h.document.activeElement, btn, "and so does a turn ending");
  assert.strictEqual(h.errors().length, 0);
});

test("a failed MCP server is not blamed on the next chat", async () => {
  // The card sits above the composer rather than in the transcript, so switching
  // chats does not take it with the thread, and it was left saying the chat now on
  // screen was missing tools it actually had.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "mcpProblems", servers: [{ name: "godot-ai", message: "did not start" }] });
  await h.settle(10);
  assert.ok(h.document.getElementById("mcp-problems"), "the chat that has the problem says so");

  // Back to the list, which has no chat and so no servers of its own.
  h.document.getElementById("history-btn").click();
  await h.settle(20);
  assert.strictEqual(h.document.getElementById("mcp-problems"), null,
    "and the warning does not follow the user into the list");

  // A second chat, which the host reports as having nothing wrong.
  h.post({ type: "body", body: "thread" });
  h.post({ type: "sessionReady", sessionId: "B" });
  h.post({ type: "mcpProblems", servers: [] });
  await h.settle(10);
  assert.strictEqual(h.document.getElementById("mcp-problems"), null, "nor into the next chat");
  assert.strictEqual(h.errors().length, 0);
});

test("committing a word in an IME does not send the message", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  await h.settle(10);
  const input = h.document.getElementById("input");
  input.value = "\u3053\u3093\u306b\u3061";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(5);

  // The Enter that commits the candidate. It belongs to the IME, not to us.
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", {
    key: "Enter", bubbles: true, cancelable: true, isComposing: true, keyCode: 229
  }));
  await h.settle(10);
  assert.strictEqual(h.posted.filter((m) => m.type === "send").length, 0,
    "the reading must not be sent as the prompt");
  assert.strictEqual(input.value, "\u3053\u3093\u306b\u3061", "and the text is left alone");

  // Once the word is committed, Enter is ours again.
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await h.settle(10);
  assert.strictEqual(h.posted.filter((m) => m.type === "send").length, 1, "a real Enter still sends");
  assert.strictEqual(h.errors().length, 0);
});

test("a file suggestion that arrives too late does not steal the next Enter", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  await h.settle(10);
  const input = h.document.getElementById("input");
  const menu = h.document.getElementById("autocomplete");

  input.value = "@Chat";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  await h.settle(10);
  const asked = h.posted.filter((m) => m.type === "queryFiles").pop();
  assert.ok(asked, "typing a mention asks the host for matches");

  // The user gives up on the mention before the workspace answers.
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  await h.settle(5);
  assert.ok(menu.classList.contains("hidden"), "Escape closes it");

  // The reply lands now, for the query that was asked.
  h.post({ type: "fileSuggestions", query: asked.query, items: [{ path: "/w/chatViewProvider.ts", label: "chatViewProvider.ts", detail: "src/chat" }] });
  await h.settle(10);
  assert.ok(menu.classList.contains("hidden"), "and it stays closed rather than reopening over the composer");

  input.value = "what does this file do?";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  await h.settle(10);
  const sent = h.posted.filter((m) => m.type === "send").pop();
  assert.ok(sent && sent.text === "what does this file do?", "so Enter sends the message instead of picking a file");
  assert.strictEqual(h.errors().length, 0);
});

test("a prompt left half written comes back, and survives the panel settling", async () => {
  // The host restores the draft before it says `ready`, and says `ready` twice: on
  // open, and again once the CLI health check finishes. Handing the composer back
  // to the "new chat" box empties it, so doing that on every `ready` threw the
  // restored draft away, and with it anything typed while the check was running.
  const h = createHarness();
  const input = h.document.getElementById("input");

  h.post({ type: "draft", id: null, text: "half a prompt from last time" });
  await h.settle(10);
  assert.strictEqual(input.value, "half a prompt from last time", "the draft arrives");

  h.post({ type: "ready" });
  await h.settle(10);
  assert.strictEqual(input.value, "half a prompt from last time", "and is still there once the panel is ready");

  // Typing while the health check runs, then the second ready lands.
  input.value = "something I am typing now";
  input.dispatchEvent(new h.window.Event("input", { bubbles: true }));
  h.post({ type: "ready" });
  await h.settle(10);
  assert.strictEqual(input.value, "something I am typing now", "and a second ready does not wipe it either");
  assert.strictEqual(h.errors().length, 0);
});

test("a new chat does not offer the last chat's message", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear" });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "userMessage", text: "delete all the generated tests" });
  await h.settle(20);

  // What starting a new chat posts: a thread body, then a reset clear.
  h.post({ type: "body", body: "thread" });
  h.post({ type: "clear", reset: true });
  await h.settle(20);

  const input = h.document.getElementById("input");
  input.value = "";
  input.dispatchEvent(new h.window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
  await h.settle(10);
  assert.strictEqual(input.value, "",
    "Up recalls this chat's last message, and this chat has not had one");

  // And the same text must not be what a failed start offers to retry.
  h.post({ type: "error", text: "Couldn't start the agent." });
  await h.settle(10);
  const retry = h.thread().querySelector(".footer-retry, .error-retry");
  if (retry) {
    retry.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
    await h.settle(10);
    const sent = h.posted.filter((m) => m.type === "send").pop();
    assert.notStrictEqual(sent && sent.text, "delete all the generated tests",
      "Retry must not send the previous chat's message into this one");
  }
  assert.strictEqual(h.errors().length, 0);
});

test("a rewind says the file edits it cannot undo are staying", async () => {
  // Probed against a real agent: the revert plan is empty even for files it edited
  // through us, and its own rewind leaves the disk alone. So "Discard Edits" would
  // be a promise the rewind does not keep.
  const h = createHarness();
  h.replay([{ role: "user", text: "first" }, { role: "assistant", text: "answer" }]);
  await h.settle(20);
  h.post({ type: "turnHead", head: 31, reliable: true });
  h.post({ type: "userChunk", text: "second" });
  h.post({ type: "assistantChunk", text: "answer 2" });
  h.post({ type: "assistantEnd", stopReason: "end_turn" });
  await h.settle(20);

  const btn = h.document.querySelector(".checkpoint-restore");
  assert.ok(btn, "the turn offers a restore");
  btn.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(10);

  const asked = h.posted.filter((m) => m.type === "revertPreview").pop();
  assert.ok(asked, "it asks what the rewind would do before doing it");
  // What a real agent answers, plus the two files this chat changed itself.
  h.post({
    type: "revertPreview",
    token: asked.token,
    head: asked.head,
    result: { fileActions: [], irreversibleWarnings: [], conflicts: [] },
    pendingFiles: 2
  });
  await h.settle(10);

  assert.strictEqual(h.posted.filter((m) => m.type === "revertExecute").length, 0,
    "nothing is rewound until the second click");
  const label = btn.querySelector("span").textContent;
  assert.strictEqual(label, "Rewind Chat", "so it does not offer to discard what it will leave behind");
  assert.match(btn.title, /2 file edits stay on disk/, "and says where those edits went: " + btn.title);

  btn.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(10);
  assert.ok(h.posted.some((m) => m.type === "revertExecute" && m.head === 31), "confirming rewinds");
  assert.strictEqual(h.errors().length, 0);
});

test("the mode picker offers whatever modes the agent reports", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  // The five a real `vtx acp` reports. The extension used to compile in four of
  // them, so `smart` was unreachable from the panel however new the CLI was.
  h.post({
    type: "options",
    currentMode: "smart",
    currentModel: "adaptive",
    models: [],
    modes: [
      { value: "accept-edits", name: "Code", icon: "codicon-code" },
      { value: "smart", name: "Smart", icon: "codicon-sparkle" },
      { value: "ask", name: "Ask", icon: "codicon-comment-discussion" },
      { value: "plan", name: "Plan", icon: "codicon-checklist" },
      { value: "bypass", name: "Bypass Permissions", icon: "codicon-unlock" }
    ]
  });
  await h.settle(20);
  const dd = h.document.querySelector("#mode-dd");
  // A mode the extension does not know the name of would show as its raw id.
  assert.match(dd.textContent, /Smart/, "the picker names the mode the session is actually in");

  dd.querySelector("button").dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(20);
  const rows = [...dd.querySelectorAll(".dd-item")].map((r) => r.textContent.trim());
  assert.deepStrictEqual(rows, ["Code", "Smart", "Ask", "Plan", "Bypass"]);
  assert.strictEqual(dd.querySelector(".dd-item.selected").textContent.trim(), "Smart");
  // Vtx names its icons after its own set, so they arrive already mapped.
  assert.ok(dd.querySelector(".dd-item-icon.codicon-sparkle"), "the mapped icon is used");
  assert.strictEqual(h.errors().length, 0);
});

test("model picker follows the adaptive pinned and models layout", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "claude-medium",
    modes: [],
    models: [
      {
        id: "adaptive",
        name: "Adaptive",
        default: "adaptive",
        variants: [{ value: "adaptive", name: "Adaptive", description: "Automatically balances quality and cost" }]
      },
      {
        id: "fusion",
        name: "Fusion",
        default: "fusion-opus-sidekick-swe",
        variants: [{ value: "fusion-opus-sidekick-swe", name: "Opus + SWE" }]
      },
      {
        id: "claude",
        name: "Claude",
        default: "claude-medium",
        variants: [
          {
            value: "claude-medium",
            name: "Medium",
            costTier: "Med cost",
            costSummary: "$2 / MTok In · $10 / MTok Out · $0.2 / MTok Cache Read · $2.5 / MTok Cache Write",
            longContextCostSummary: "$4 / MTok In · $20 / MTok Out · $0.4 / MTok Cache Read · $5 / MTok Cache Write",
            description: "Powerful",
            maxContextTokens: 1048576,
            maxOutputTokens: 131072,
            isNew: true,
            promotion: "PROMO"
          },
          { value: "claude-low", name: "Low" }
        ]
      },
      {
        id: "terra",
        name: "Terra",
        default: "terra-medium",
        variants: [{ value: "terra-medium", name: "Medium", costTier: "Low cost", costSummary: "$1 / MTok In · $2 / MTok Out" }]
      }
    ]
  });
  await h.settle(20);

  const dd = h.document.querySelector("#model-dd");
  dd.querySelector(".dd-btn").click();
  const rows = [...dd.querySelectorAll(".dd-item")];
  assert.match(rows[0].textContent, /Adaptive/);
  assert.match(rows[1].textContent, /Fusion/, "Fusion sits beside Adaptive above the model list");
  assert.ok(rows[1].nextElementSibling.classList.contains("dd-sep"), "Fusion is separated from ordinary models");
  const adaptiveRow = rows[0];
  adaptiveRow.dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);
  const adaptiveHover = h.document.querySelector(".model-hover");
  assert.ok(adaptiveHover.classList.contains("compact"), "Adaptive uses the compact hover card");
  assert.match(adaptiveHover.textContent, /Automatically balances quality and cost/);
  assert.strictEqual(adaptiveHover.querySelector(".model-hover-configurable"), null, "Adaptive has no configurable controls");
  adaptiveRow.dispatchEvent(new h.window.MouseEvent("mouseleave", { bubbles: true }));
  await h.until(() => !h.document.querySelector(".model-hover"), 2000);
  const row = rows.find((item) => /Claude/.test(item.textContent));
  assert.ok(row, "the Claude family is listed");
  assert.doesNotMatch(row.textContent, /Med cost|MTok/, "cost stays out of the model row");
  assert.match(row.textContent, /NEW/);
  assert.match(row.textContent, /PROMO/);
  const pin = row.querySelector(".dd-item-action");
  assert.ok(pin, "the model row has a pin action");

  row.dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.settle(100);
  assert.strictEqual(h.document.querySelector(".model-hover"), null, "the model hover waits before opening");
  await h.until(() => h.document.querySelector(".model-hover"), 2000);
  const hover = h.document.querySelector(".model-hover");
  assert.ok(hover, "the model opens a hover card after the delay");
  assert.match(hover.textContent, /Powerful/);
  assert.match(hover.textContent, /Medium cost/);
  assert.match(hover.textContent, /Cost per 1M tokens/);
  assert.match(hover.textContent, /Default/);
  assert.match(hover.textContent, /Long Context/);
  assert.match(hover.textContent, /Input/);
  assert.match(hover.textContent, /Cache Read/);
  assert.match(hover.textContent, /\$2/);
  assert.match(hover.textContent, /\$4/);
  assert.match(hover.textContent, /1M tokens/);
  assert.match(hover.textContent, /131K tokens/);
  assert.match(hover.textContent, /Configurable/);
  assert.deepStrictEqual(
    [...hover.querySelectorAll(".model-hover-configurable-button")].map((button) => button.textContent),
    ["Thinking Level"]
  );
  assert.ok([...hover.querySelectorAll("button")].some((button) => button.textContent === "Thinking Level"));
  assert.match(hover.textContent, /PROMO/);

  row.dispatchEvent(new h.window.MouseEvent("mouseleave", { bubbles: true }));
  await h.settle(100);
  assert.ok(h.document.querySelector(".model-hover"), "the hover remains during the hide delay");
  await h.until(() => !h.document.querySelector(".model-hover"), 2000);
  assert.strictEqual(h.document.querySelector(".model-hover"), null, "the hover closes after the hide delay");

  const css = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  assert.match(css, /\.model-hover-header\.no-description\s*\{/, "the badge has a dedicated no-description header layout");
  assert.match(css, /\.dv-floater\.model-hover-floater\s*\{[\s\S]*width: min\(300px, calc\(100vw - 8px\)\);/, "the hover card uses the compact target width");
  assert.match(css, /\.dv-floater\.model-hover-floater\s*\{[\s\S]*max-width: min\(300px, calc\(100vw - 8px\)\);/, "the hover card fits the viewport width");
  assert.match(css, /\.dv-floater\.model-hover-floater\s*\{[\s\S]*max-height: calc\(100vh - 8px\);/, "the hover card fits the viewport height");
  assert.match(css, /#model-dd \.dd-menu\s*\{[\s\S]*width: min\(300px, calc\(100vw - 16px\)\);/, "the model picker uses the Copilot widget width");
  assert.match(css, /\.model-hover-configurable-controls\s*\{/, "configurable items use a right aligned control group");
  assert.match(css, /\.model-hover-configurable-button\s*\{[\s\S]*border: 1px solid var\(--vscode-widget-border/, "configurable items use the Copilot badge border");
  assert.match(css, /\.model-hover\.compact\s*\{/, "metadata only hovers use compact spacing");
  assert.match(css, /\.model-hover\.compact \.model-hover-header\s*\{/, "compact hovers remove the unused header gap");

  const terra = rows.find((item) => /Terra/.test(item.textContent));
  terra.dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);
  assert.ok(h.document.querySelector(".model-hover-header.no-description"), "a model without a description keeps the badge aligned");
  terra.dispatchEvent(new h.window.MouseEvent("mouseleave", { bubbles: true }));
  await h.until(() => !h.document.querySelector(".model-hover"), 2000);

  pin.click();
  await h.settle(20);
  dd.querySelector(".dd-btn").click();
  assert.deepStrictEqual(
    [...dd.querySelectorAll(".dd-group")].map((group) => group.textContent.trim()),
    ["Pinned", "Models"]
  );
  assert.strictEqual([...dd.querySelectorAll(".dd-item")].filter((item) => /Claude/.test(item.textContent)).length, 1);
  dd.querySelector(".dd-btn").click();

  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "other-medium",
    modes: [],
    models: [{
      id: "other",
      name: "Other",
      default: "other-medium",
      variants: [{ value: "other-medium", name: "Medium" }]
    }]
  });
  await h.settle(20);
  dd.querySelector(".dd-btn").click();
  assert.doesNotMatch(dd.textContent, /Claude/, "an unavailable pinned model is not shown");
  assert.strictEqual(h.errors().length, 0);
});

test("model hover draws a line from each cost label to its prices", async () => {
  const h = createHarness();
  h.document.documentElement.style.setProperty("--vscode-descriptionForeground", "#9d9d9d");
  const stylesheet = h.document.createElement("style");
  stylesheet.textContent = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  h.document.head.appendChild(stylesheet);
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "gpt-terra-medium",
    modes: [],
    models: [{
      id: "gpt-terra",
      name: "GPT-5.6 Terra",
      default: "gpt-terra-medium",
      variants: [{
        value: "gpt-terra-medium",
        name: "Medium",
        costSummary: "$2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output",
        longContextCostSummary: "$4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output"
      }]
    }]
  });
  await h.settle(20);

  const dd = h.document.querySelector("#model-dd");
  dd.querySelector(".dd-btn").click();
  dd.querySelector(".dd-item").dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);

  const hover = h.document.querySelector(".model-hover");
  assert.ok(hover, "the model hover is shown");
  const lines = [...hover.querySelectorAll(".model-hover-cost-line")];
  const rows = [...hover.querySelectorAll(".model-hover-cost-row")];
  assert.strictEqual(lines.length, 3, "each price row has one dotted line");
  assert.deepStrictEqual(
    rows.map((row) => row.querySelector(".model-hover-cost-label").textContent),
    ["Input", "Cache Read", "Output"],
    "each cost row keeps its label beside its first line"
  );
  assert.deepStrictEqual(
    [...hover.querySelectorAll(".model-hover-cost-value")].map((value) => value.textContent),
    ["$2", "$4", "$0.2", "$0.4", "$12", "$24"],
    "current CLI price values are shown"
  );
  assert.ok(
    lines.every((line) => h.window.getComputedStyle(line).borderBottomStyle === "dotted"),
    "cost lines use a dotted stroke"
  );
  assert.ok(
    lines.every((line) => {
      const style = h.window.getComputedStyle(line);
      return style.marginLeft === "0px" && style.marginRight === "0px";
    }),
    "each row uses one uninterrupted line"
  );
  assert.ok(
    [...hover.querySelectorAll(".model-hover-cost-label")].every((label) => h.window.getComputedStyle(label).paddingRight === "6px")
      && [...hover.querySelectorAll(".model-hover-cost-value")].every((value) => {
        const style = h.window.getComputedStyle(value);
        return style.paddingLeft === "6px" && style.paddingRight === "6px";
      }),
    "cost text leaves the dotted line a six pixel gap"
  );
  assert.strictEqual(
    h.window.getComputedStyle(hover.querySelector(".model-hover-cost-table")).columnGap,
    "0",
    "price columns do not add an extra gap"
  );
  assert.deepStrictEqual(
    lines.map((line) => line.parentElement.querySelector(".model-hover-cost-label").textContent),
    ["Input", "Cache Read", "Output"],
    "each dotted line belongs to its cost row"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("Fusion cost details separate lead and sidekick pricing", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "fusion-opus-sidekick-swe",
    modes: [],
    models: [{
      id: "fusion",
      name: "Fusion",
      default: "fusion-opus-sidekick-swe",
      variants: [{
        value: "fusion-opus-sidekick-swe",
        name: "Opus + SWE",
        costTier: "High cost",
        costSummary: "$5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output · $0.75 / 1M Sidekick input · $0.08 / 1M Sidekick cached input · $3.75 / 1M Sidekick output"
      }]
    }]
  });
  await h.settle(20);

  const dd = h.document.querySelector("#model-dd");
  dd.querySelector(".dd-btn").click();
  dd.querySelector(".dd-item").dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);

  const hover = h.document.querySelector(".model-hover");
  assert.deepStrictEqual(
    [...hover.querySelectorAll(".model-hover-section")].map((section) => section.textContent),
    ["Lead cost per 1M tokens", "Sidekick cost per 1M tokens"]
  );
  assert.deepStrictEqual(
    [...hover.querySelectorAll(".model-hover-cost-value")].map((value) => value.textContent),
    ["$5", "$0.5", "$25", "$0.75", "$0.08", "$3.75"]
  );
  assert.strictEqual(hover.querySelector(".model-hover-configurable-button").textContent, "Configure Fusion");
  assert.strictEqual(h.errors().length, 0);
});

test("model hover links context limits to their values", async () => {
  const h = createHarness();
  h.document.documentElement.style.setProperty("--vscode-descriptionForeground", "#9d9d9d");
  const stylesheet = h.document.createElement("style");
  stylesheet.textContent = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  h.document.head.appendChild(stylesheet);
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "context-model",
    modes: [],
    models: [{
      id: "context",
      name: "Context Model",
      default: "context-model",
      variants: [{
        value: "context-model",
        name: "Base",
        maxContextTokens: 1000000,
        maxOutputTokens: 128000
      }]
    }]
  });
  await h.settle(20);

  const dd = h.document.querySelector("#model-dd");
  dd.querySelector(".dd-btn").click();
  dd.querySelector(".dd-item").dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);

  const limits = h.document.querySelector(".model-hover-limits");
  assert.ok(limits, "the model hover shows its context limits");
  const rows = [...limits.querySelectorAll(".model-hover-cost-row")];
  const lines = [...limits.querySelectorAll(".model-hover-cost-line")];
  assert.strictEqual(rows.length, 2, "each context limit has a row");
  assert.strictEqual(lines.length, 2, "each context limit has one dotted line");
  assert.deepStrictEqual(
    rows.map((row) => row.querySelector(".model-hover-cost-label").textContent),
    ["Max context", "Max output"]
  );
  assert.deepStrictEqual(
    [...limits.querySelectorAll(".model-hover-cost-value")].map((value) => value.textContent),
    ["1M tokens", "128K tokens"]
  );
  assert.ok(
    lines.every((line) => h.window.getComputedStyle(line).borderBottomStyle === "dotted"),
    "context lines use the same dotted stroke"
  );
  assert.strictEqual(h.errors().length, 0);
});

test("model hover balances padding without configuration", async () => {
  const h = createHarness();
  const stylesheet = h.document.createElement("style");
  stylesheet.textContent = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  h.document.head.appendChild(stylesheet);
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "no-configuration",
    modes: [],
    models: [{
      id: "no-configuration",
      name: "No Configuration",
      default: "no-configuration",
      variants: [{
        value: "no-configuration",
        name: "Base",
        maxContextTokens: 1000000,
        maxOutputTokens: 128000
      }]
    }]
  });
  await h.settle(20);

  const dd = h.document.querySelector("#model-dd");
  dd.querySelector(".dd-btn").click();
  dd.querySelector(".dd-item").dispatchEvent(new h.window.MouseEvent("mouseenter", { bubbles: true }));
  await h.until(() => h.document.querySelector(".model-hover"), 2000);

  const hover = h.document.querySelector(".model-hover");
  assert.ok(hover, "the model hover is shown");
  assert.ok(!hover.classList.contains("compact"), "the context limits use the full tooltip layout");
  assert.ok(hover.classList.contains("no-configurable"), "the tooltip records that configuration is absent");
  const style = h.window.getComputedStyle(hover);
  assert.strictEqual(style.paddingTop, "10px");
  assert.strictEqual(style.paddingBottom, "13px");
  assert.strictEqual(h.errors().length, 0);
});

test("Fusion keeps lead effort and sidekick in one configuration picker", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low",
    thoughtLevels: [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }, { value: "xhigh", name: "XHigh" }],
    modelChoices: {
      fusion: [
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-medium", name: "Fusion (GPT-6 Astra High Thinking + SWE-2 Medium)" },
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-high", name: "Fusion (GPT-6 Astra High Thinking + SWE-2 High)" }
      ]
    },
    models: [{
      id: "fusion",
      name: "Fusion",
      default: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
      variants: [
        { value: "fusion-claude-opus-5-low-sidekick-swe-2-medium", name: "(Claude Opus 5 Low + SWE-2 Medium)" },
        { value: "fusion-claude-opus-5-medium-sidekick-swe-2-medium", name: "(Claude Opus 5 Medium + SWE-2 Medium)" },
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "(Claude Opus 5 High + SWE-2 Medium)" },
        { value: "fusion-claude-opus-5-low-sidekick-swe-2-high", name: "(Claude Opus 5 Low + SWE-2 High)" },
        { value: "fusion-claude-opus-5-medium-sidekick-swe-2-high", name: "(Claude Opus 5 Medium + SWE-2 High)" },
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "(Claude Opus 5 High + SWE-2 High)" },
        { value: "fusion-gpt-6-astra-low-sidekick-swe-2-medium", name: "(GPT-6 Astra Low + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-medium-sidekick-swe-2-medium", name: "(GPT-6 Astra Medium + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-medium", name: "(GPT-6 Astra High + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-low-sidekick-swe-2-high", name: "(GPT-6 Astra Low + SWE-2 High)" },
        { value: "fusion-gpt-6-astra-medium-sidekick-swe-2-high", name: "(GPT-6 Astra Medium + SWE-2 High)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-high", name: "(GPT-6 Astra High + SWE-2 High)" }
      ]
    }],
    modes: []
  });
  await h.settle(20);

  const picker = h.document.querySelector("#fusion-dd");
  assert.ok(!picker.classList.contains("hidden"), "Fusion has one configuration control");
  assert.strictEqual(picker.querySelector(".dd-btn").getAttribute("aria-label"), "Configure Fusion");
  assert.strictEqual(picker.querySelector(".fusion-config-lead-title").textContent, "Opus 5 · Low");
  assert.strictEqual(picker.querySelector(".fusion-config-sidekick-title").textContent, "SWE-2 · Medium");
  assert.ok(h.document.querySelector("#thinking-dd").classList.contains("hidden"), "thinking effort moves inside Fusion configuration");
  const css = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  assert.doesNotMatch(css, /#input-box\.cmp-xs #fusion-dd/, "compact Fusion keeps its configuration icon");

  picker.querySelector(".dd-btn").click();
  assert.deepStrictEqual(
    [...picker.querySelectorAll(".fusion-config-label")].map((label) => label.textContent),
    ["Lead model", "Lead effort", "Sidekick"]
  );
  const buttons = [...picker.querySelectorAll(".fusion-config-option")];
  // Selected items show a check icon, matching the effort selector for normal models.
  const selected = buttons.filter((b) => b.classList.contains("selected"));
  assert.ok(selected.length >= 1, "at least one option is selected by default");
  assert.ok(selected.every((b) => b.querySelector(".dd-check .codicon-check")), "selected Fusion options show a check icon");
  const unselected = buttons.filter((b) => !b.classList.contains("selected"));
  assert.ok(unselected.every((b) => !b.querySelector(".dd-check .codicon-check")), "unselected Fusion options have no check icon");
  buttons.find((button) => button.textContent === "GPT-6 Astra").click();
  assert.ok(!picker.querySelector(".dd-menu").classList.contains("hidden"), "the menu stays open while configuring");
  assert.ok(h.posted.some((message) => message.type === "setFusionModel" && message.model === "fusion-gpt-6-astra-low-sidekick-swe-2-medium" && message.thoughtLevel === "low"));
  // After selecting GPT-6 Astra, verify the check icon moved to the new lead.
  const option = (label) => [...picker.querySelectorAll(".fusion-config-option")].find((button) => button.textContent === label);
  const newLead = option("GPT-6 Astra");
  assert.ok(newLead.querySelector(".dd-check .codicon-check"), "the newly selected lead shows a check icon");
  option("High").click();
  assert.ok(h.posted.some((message) => message.type === "setConfigOption" && message.configId === "thought_level" && message.value === "high" && message.model === "fusion-gpt-6-astra-high-sidekick-swe-2-medium"));
  option("SWE-2 High").click();
  assert.ok(h.posted.some((message) => message.type === "setFusionModel" && message.model === "fusion-gpt-6-astra-high-sidekick-swe-2-high" && message.thoughtLevel === "high"));
  assert.strictEqual(h.errors().length, 0);
});

test("switching from a normal model to Fusion keeps the effort levels", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [{ value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion" }] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const fusionChoices = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];

  // Start on Fusion with thought levels and Fusion choices populated.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);
  assert.ok(!h.document.querySelector("#fusion-dd").classList.contains("hidden"), "Fusion picker visible initially");

  // Simulate user clicking GPT-5 in the model dropdown. This sets currentModelUid
  // synchronously, then the host responds with options for the normal model.
  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  const gptItem = [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("GPT-5"));
  gptItem.click();
  assert.ok(h.document.querySelector("#fusion-dd").classList.contains("hidden"), "Fusion picker hidden after clicking GPT-5");

  // Host responds with options for GPT-5 (no Fusion groups, no thought levels).
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "",
    thoughtLevels: [], modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  // Now the user clicks Fusion in the model dropdown.
  modelDD.querySelector(".dd-btn").click();
  const fusionItem = [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("Fusion"));
  fusionItem.click();
  const picker = h.document.querySelector("#fusion-dd");
  assert.ok(!picker.classList.contains("hidden"), "Fusion picker reappears after switching back");
  picker.querySelector(".dd-btn").click();
  const effortSection = [...picker.querySelectorAll(".fusion-config-label")].find((el) => el.textContent === "Lead effort");
  assert.ok(effortSection, "Lead effort section is present after switching from a normal model");
  const effortOptions = effortSection.parentElement.querySelectorAll(".fusion-config-option");
  assert.strictEqual(effortOptions.length, 2, "both effort levels are rendered");
  assert.strictEqual(h.errors().length, 0);
});

test("Fusion effort levels that arrive late fill the open configuration menu", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [{ value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion" }] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const fusionChoices = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];

  // A normal model is active and reports no thought level, but the Fusion
  // group is already known so the picker can populate.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "",
    thoughtLevels: [], modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);

  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("GPT-5")).click();
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("Fusion")).click();

  const picker = h.document.querySelector("#fusion-dd");
  assert.ok(!picker.classList.contains("hidden"), "Fusion picker is visible");
  picker.querySelector(".dd-btn").click();
  const labels = () => [...picker.querySelectorAll(".fusion-config-label")].map((el) => el.textContent);
  const effortValues = () => [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].map((el) => el.dataset.value);
  assert.deepStrictEqual(labels(), ["Lead model", "Lead effort", "Sidekick"], "the catalogue supplies an effort section before ACP does");
  assert.deepStrictEqual(effortValues(), ["high"], "only the effort the variants prove");

  // The options response for the Fusion model lands while the menu is still
  // open, so the effort section has to update without closing and reopening.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);

  assert.ok(!picker.querySelector(".dd-menu").classList.contains("hidden"), "the menu is still open");
  assert.deepStrictEqual(effortValues(), ["low", "high"], "the reported levels replace the catalogue guess");
  assert.strictEqual(h.errors().length, 0);
});

test("a normal model with no thought level keeps the ACP thinking picker hidden", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [{ value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion" }] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const fusionChoices = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];

  // Fusion is active, so its effort levels become the known list.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);

  // The user picks a normal model whose options report no thought level. The
  // remembered Fusion levels must not leak into the thinking picker, and the
  // GPT family has a single variant so there is nothing else to show.
  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("GPT-5")).click();
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "",
    thoughtLevels: [], modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  assert.ok(h.document.querySelector("#thinking-dd").classList.contains("hidden"), "the thinking picker stays hidden");
  assert.ok(h.document.querySelector("#fusion-dd").classList.contains("hidden"), "the Fusion picker is hidden");
  assert.strictEqual(h.errors().length, 0);
});

test("each model family keeps its own effort levels", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [{ value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion" }] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-medium", variants: [
      { value: "gpt-5-low", name: "GPT-5 Low" },
      { value: "gpt-5-medium", name: "GPT-5 Medium" },
      { value: "gpt-5-xhigh", name: "GPT-5 XHigh" }
    ] }
  ];
  const fusionChoices = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];

  // Start on Fusion so its effort list is the one already known.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);

  // Switching to a normal model must not leave Fusion's levels on screen. GPT
  // has no effort of its own yet, so until the host reports one the picker
  // lists the family's own variants instead.
  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("GPT-5")).click();

  const thinking = h.document.querySelector("#thinking-dd");
  const itemNames = () => [...thinking.querySelectorAll(".dd-item-name")].map((el) => el.textContent);
  assert.ok(!thinking.classList.contains("hidden"), "the thinking picker shows the family's variants");
  thinking.querySelector(".dd-btn").click();
  assert.deepStrictEqual(itemNames(), ["GPT-5 Low", "GPT-5 Medium", "GPT-5 XHigh"], "not Fusion's efforts");
  thinking.querySelector(".dd-btn").click();

  // The host then reports that GPT really does have a thought level.
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-medium", currentThoughtLevel: "medium",
    thoughtLevels: [{ value: "medium", name: "Medium" }, { value: "xhigh", name: "XHigh" }],
    modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  thinking.querySelector(".dd-btn").click();
  assert.deepStrictEqual(itemNames(), ["Medium", "XHigh"], "only GPT's own effort levels are listed");
  thinking.querySelector(".dd-btn").click();

  // Switching back to Fusion still shows Fusion's levels, not GPT's.
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("Fusion")).click();
  const picker = h.document.querySelector("#fusion-dd");
  picker.querySelector(".dd-btn").click();
  const effortSection = [...picker.querySelectorAll(".fusion-config-label")].find((el) => el.textContent === "Lead effort");
  assert.ok(effortSection, "Fusion keeps its effort section");
  const effortNames = [...effortSection.parentElement.querySelectorAll(".fusion-config-option")].map((el) => el.textContent);
  assert.deepStrictEqual(effortNames, ["Low", "High"], "Fusion's levels were not overwritten by GPT's");
  assert.strictEqual(h.errors().length, 0);
});

test("a model that reports no effort clears the previous model's effort", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-low-sidekick-swe-2-medium", variants: [
      { value: "fusion-claude-opus-5-low-sidekick-swe-2-medium", name: "(Claude Opus 5 Low + SWE-2 Medium)" },
      { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "(Claude Opus 5 High + SWE-2 Medium)" },
      { value: "fusion-claude-opus-5-low-sidekick-swe-2-high", name: "(Claude Opus 5 Low + SWE-2 High)" },
      { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "(Claude Opus 5 High + SWE-2 High)" }
    ] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const fusionChoices = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];

  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-low-sidekick-swe-2-medium",
    currentThoughtLevel: "high", thoughtLevels: levels,
    modelChoices: { fusion: fusionChoices }, models: allModels, modes: []
  });
  await h.settle(20);
  const picker = h.document.querySelector("#fusion-dd");
  assert.strictEqual(picker.querySelector(".fusion-config-lead-title").textContent, "Opus 5 · High", "the reported effort is on the button");

  // The user moves to a model with no thought level and the host answers with
  // an empty currentThoughtLevel, which is a deliberate clear, not a gap.
  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("GPT-5")).click();
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "",
    thoughtLevels: [], modelChoices: {}, models: allModels, modes: []
  });
  h.post({ type: "modelConfigApplied", requestId: 1, model: "gpt-5-xhigh", thoughtLevel: "" });
  await h.settle(20);
  assert.ok(h.document.querySelector("#thinking-dd").classList.contains("hidden"), "no effort and one variant means no picker");
  assert.ok(picker.classList.contains("hidden"), "the Fusion picker is hidden on a normal model");

  // Back on Fusion the cleared effort must not come back. If "high" had been
  // kept the button would read "Opus 5 · High", instead it falls back to the
  // first level.
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("Fusion")).click();
  assert.strictEqual(picker.querySelector(".fusion-config-lead-title").textContent, "Opus 5 · Low", "the cleared effort does not come back");
  assert.strictEqual(h.errors().length, 0);
});

test("Fusion is configurable before a session reports grouped choices", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  // The pre session options carry the family with the whole lead times
  // sidekick cross product in variants, named "(Lead + Sidekick)", and no
  // grouped choices yet.
  const fusionVariants = [];
  const leads = [["claude-opus-5-high", "Claude Opus 5 High"], ["gpt-6-astra-high", "GPT-6 Astra High"], ["claude-sonnet-6-medium", "Claude Sonnet 6 Medium"]];
  const sidekicks = [["swe-2-low", "SWE-2 Low"], ["swe-2-medium", "SWE-2 Medium"], ["swe-2-high", "SWE-2 High"], ["swe-2-xhigh", "SWE-2 XHigh"]];
  for (const [leadUid, leadName] of leads) {
    for (const [sidekickUid, sidekickName] of sidekicks) {
      fusionVariants.push({ value: `fusion-${leadUid}-sidekick-${sidekickUid}`, name: `(${leadName} + ${sidekickName})` });
    }
  }
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "", thoughtLevels: [],
    modelChoices: {},
    models: [{ id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: fusionVariants }],
    modes: []
  });
  await h.settle(20);

  // The cross product is configuration for the Fusion picker, not a thinking
  // list, so the twelve raw pair names must never land in the thinking
  // dropdown (which would also grow a filter box at that length).
  assert.ok(h.document.querySelector("#thinking-dd").classList.contains("hidden"), "the cross product is not dumped into the thinking picker");
  const picker = h.document.querySelector("#fusion-dd");
  assert.ok(!picker.classList.contains("hidden"), "the Fusion picker works off the family variants alone");
  picker.querySelector(".dd-btn").click();
  const leadSection = [...picker.querySelectorAll(".fusion-config-label")].find((el) => el.textContent === "Lead model");
  assert.ok(leadSection, "the Lead model section renders");
  const leadOptions = [...leadSection.parentElement.querySelectorAll(".fusion-config-option")].map((el) => el.textContent);
  assert.strictEqual(leadOptions.length, 3, "one option per lead, not per pair");
  assert.ok(!leadOptions[0].startsWith("("), "the bracketed variant name parses cleanly");
  assert.strictEqual(h.errors().length, 0);
});

test("Fusion derives its effort levels from the catalogue variants before ACP reports them", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const fusionVariants = [];
  for (const lead of ["claude-opus-5", "gpt-6-astra"]) {
    for (const effort of ["low", "medium", "high"]) {
      for (const sidekick of ["swe-2-medium", "swe-2-high"]) {
        fusionVariants.push({ value: `fusion-${lead}-${effort}-sidekick-${sidekick}`, name: `(${lead} ${effort} + ${sidekick})` });
      }
    }
  }
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: fusionVariants },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [
      { value: "gpt-5-medium", name: "GPT-5 Medium" },
      { value: "gpt-5-xhigh", name: "GPT-5 XHigh" }
    ] }
  ];
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "",
    thoughtLevels: [], modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  const modelDD = h.document.querySelector("#model-dd");
  modelDD.querySelector(".dd-btn").click();
  [...modelDD.querySelectorAll(".dd-item")].find((el) => el.textContent.includes("Fusion")).click();
  const picker = h.document.querySelector("#fusion-dd");
  assert.ok(!picker.classList.contains("hidden"), "the Fusion picker shows before ACP reports");
  assert.ok(h.document.querySelector("#thinking-dd").classList.contains("hidden"), "the normal thinking picker stays hidden on Fusion");
  picker.querySelector(".dd-btn").click();
  const effortValues = () => [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].map((el) => el.dataset.value);
  assert.deepStrictEqual(effortValues(), ["low", "medium", "high"], "only the lead's real efforts, never GPT's xhigh");

  const postedBefore = h.posted.length;
  [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].find((el) => el.dataset.value === "low").click();
  const effortPost = h.posted.slice(postedBefore).find((message) => message.type === "setConfigOption");
  assert.ok(effortPost, "an effort change is sent");
  assert.strictEqual(effortPost.configId, "thought_level");
  assert.strictEqual(effortPost.value, "low");
  assert.strictEqual(effortPost.model, "fusion-claude-opus-5-low-sidekick-swe-2-medium", "the uid carries the chosen effort");
  assert.strictEqual(typeof effortPost.requestId, "number", "the request is tagged for acknowledgement");

  const grouped = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-gpt-6-astra-high-sidekick-swe-2-high", name: "Fusion (GPT-6 Astra High Thinking + SWE-2 High)" }
  ];
  const noCatalogue = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [] },
    allModels[1]
  ];
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "", thoughtLevels: [],
    modelChoices: { fusion: grouped }, models: noCatalogue, modes: []
  });
  await h.settle(20);
  assert.ok(!picker.querySelector(".dd-menu").classList.contains("hidden"), "the menu is still open");
  const status = picker.querySelector(".fusion-config-loading");
  assert.ok(status, "a loading status stands in for the missing effort row");
  assert.strictEqual(status.getAttribute("role"), "status");
  assert.strictEqual(status.getAttribute("aria-busy"), "true");
  assert.strictEqual(status.textContent, "Loading effort options...");

  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "", thoughtLevels: [{ value: "low", name: "Low" }, { value: "high", name: "High" }],
    modelChoices: { fusion: grouped }, models: noCatalogue, modes: []
  });
  await h.settle(20);
  assert.ok(!picker.querySelector(".dd-menu").classList.contains("hidden"), "the menu stays open");
  assert.deepStrictEqual(effortValues(), ["low", "high"], "the reported levels fill the open menu");
  assert.strictEqual(h.errors().length, 0);
});

test("only the latest model change acknowledgement can reconcile a rapid series of picks", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const fusionVariants = [];
  for (const effort of ["low", "medium", "high"]) {
    for (const sidekick of ["swe-2-medium", "swe-2-high"]) {
      fusionVariants.push({ value: `fusion-claude-opus-5-${effort}-sidekick-${sidekick}`, name: "(Claude Opus 5 + SWE-2)" });
    }
  }
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-low-sidekick-swe-2-medium", variants: fusionVariants },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }];
  const staleOptions = (thoughtLevel, currentModel) => h.post({
    type: "options", currentMode: "accept-edits", currentModel, currentThoughtLevel: thoughtLevel,
    thoughtLevels: levels, modelChoices: {}, models: allModels, modes: []
  });
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-low-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  const picker = h.document.querySelector("#fusion-dd");
  picker.querySelector(".dd-btn").click();
  const title = () => picker.querySelector(".fusion-config-lead-title").textContent;
  const effortButton = (value) => [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].find((el) => el.dataset.value === value);
  const sentEfforts = () => h.posted.filter((message) => message.type === "setConfigOption" && message.configId === "thought_level");

  effortButton("low").click();
  effortButton("high").click();
  h.post({ type: "modelConfigApplied", requestId: 1, model: "fusion-claude-opus-5-low-sidekick-swe-2-medium", thoughtLevel: "low" });
  staleOptions("low", "fusion-claude-opus-5-low-sidekick-swe-2-medium");
  h.post({ type: "model", model: "fusion-claude-opus-5-low-sidekick-swe-2-medium" });
  assert.strictEqual(title(), "Opus 5 · High", "an old acknowledgement cannot roll back a newer pick");
  assert.strictEqual(effortButton("high").classList.contains("selected"), true);

  effortButton("medium").click();
  h.post({ type: "modelConfigApplied", requestId: 2, model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", thoughtLevel: "high" });
  staleOptions("high", "fusion-claude-opus-5-high-sidekick-swe-2-medium");
  assert.strictEqual(title(), "Opus 5 · Medium");

  effortButton("low").click();
  h.post({ type: "modelConfigApplied", requestId: 3, model: "fusion-claude-opus-5-medium-sidekick-swe-2-medium", thoughtLevel: "medium" });
  effortButton("high").click();
  h.post({ type: "modelConfigApplied", requestId: 4, model: "fusion-claude-opus-5-low-sidekick-swe-2-medium", thoughtLevel: "high" });
  staleOptions("medium", "fusion-claude-opus-5-medium-sidekick-swe-2-medium");
  assert.strictEqual(title(), "Opus 5 · High", "the guard is released only by the latest request id");

  assert.deepStrictEqual(sentEfforts().map((message) => [message.value, message.requestId]), [
    ["low", 1], ["high", 2], ["medium", 3], ["low", 4], ["high", 5]
  ], "every pick went out tagged, in order");

  h.post({ type: "modelConfigApplied", requestId: 5, model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", thoughtLevel: "high" });
  staleOptions("medium", "fusion-claude-opus-5-high-sidekick-swe-2-medium");
  await h.settle(20);
  assert.strictEqual(title(), "Opus 5 · Medium", "a post-ack authoritative update applies normally");
  assert.strictEqual(h.errors().length, 0);
});

test("unchanged Fusion options keep the open menu's nodes and focus", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants: [{ value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion" }] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const grouped = [
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "Fusion (Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-high", name: "Fusion (Claude Opus 5 High + SWE-2 High)" }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];
  const options = () => ({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: { fusion: grouped }, models: allModels, modes: []
  });
  h.post(options());
  await h.settle(20);

  const picker = h.document.querySelector("#fusion-dd");
  picker.querySelector(".dd-btn").click();
  const effortButton = (value) => [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].find((el) => el.dataset.value === value);
  const high = effortButton("high");
  high.focus();
  assert.strictEqual(h.document.activeElement, high, "the effort button holds focus");

  h.post(options());
  await h.settle(20);
  assert.strictEqual(effortButton("high"), high, "the button node survives an identical options reply");
  assert.strictEqual(h.document.activeElement, high, "focus survives it too");

  h.post({ ...options(), thoughtLevels: [...levels, { value: "xhigh", name: "XHigh" }] });
  await h.settle(20);
  const effortValues = () => [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].map((el) => el.dataset.value);
  assert.notStrictEqual(effortButton("high"), high, "new effort choices rebuild the menu");
  assert.deepStrictEqual(effortValues(), ["low", "high", "xhigh"], "the new choice is rendered");
  assert.strictEqual(h.errors().length, 0);
});

test("a stale model change acknowledgement cannot paint a new session", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const allModels = [
    { id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-low-sidekick-swe-2-medium", variants: [
      { value: "fusion-claude-opus-5-low-sidekick-swe-2-medium", name: "(Claude Opus 5 Low + SWE-2 Medium)" },
      { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "(Claude Opus 5 High + SWE-2 Medium)" }
    ] },
    { id: "gpt", name: "GPT-5", default: "gpt-5-xhigh", variants: [{ value: "gpt-5-xhigh", name: "GPT-5 XHigh" }] }
  ];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-claude-opus-5-low-sidekick-swe-2-medium",
    currentThoughtLevel: "low", thoughtLevels: levels,
    modelChoices: {}, models: allModels, modes: []
  });
  await h.settle(20);

  const picker = h.document.querySelector("#fusion-dd");
  picker.querySelector(".dd-btn").click();
  [...picker.querySelectorAll(".fusion-config-option[data-kind=effort]")].find((el) => el.dataset.value === "high").click();
  const effortPost = h.posted.slice().reverse().find((message) => message.type === "setConfigOption");
  assert.strictEqual(effortPost.requestId, 1, "the pick is tagged");
  assert.strictEqual(picker.querySelector(".fusion-config-lead-title").textContent, "Opus 5 · High", "the optimistic pick is on the button");

  h.post({ type: "clear", reset: true });
  h.post({ type: "sessionReady", sessionId: "session-two" });
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "gpt-5-xhigh", currentThoughtLevel: "", thoughtLevels: [],
    modelChoices: {}, models: allModels, modes: []
  });
  h.post({ type: "model", model: "gpt-5-xhigh" });
  await h.settle(20);
  assert.strictEqual(h.document.querySelector("#model-dd .dd-label").textContent, "GPT-5", "the new session's own model is on screen");
  assert.ok(picker.classList.contains("hidden"), "Fusion is not selected in the new session");

  h.post({ type: "modelConfigApplied", requestId: 1, model: "fusion-claude-opus-5-low-sidekick-swe-2-medium", thoughtLevel: "high" });
  await h.settle(20);
  assert.strictEqual(h.document.querySelector("#model-dd .dd-label").textContent, "GPT-5", "the stale acknowledgement cannot paint over it");
  assert.ok(picker.classList.contains("hidden"), "the stale ack does not drag Fusion back on screen");
  assert.strictEqual(h.errors().length, 0);
});

test("a session arriving keeps a pending pick guarded", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const options = (thoughtLevel) => ({
    type: "options", currentMode: "accept-edits",
    currentModel: "claude-medium", currentThoughtLevel: thoughtLevel,
    thoughtLevels: [{ value: "low", name: "Low" }, { value: "high", name: "High" }],
    modelChoices: {},
    models: [{ id: "claude", name: "Claude", default: "claude-medium", variants: [
      { value: "claude-medium", name: "Claude Medium" }, { value: "claude-high", name: "Claude High" }
    ] }],
    modes: []
  });
  h.post(options("low"));
  await h.settle(20);

  const thinking = h.document.querySelector("#thinking-dd");
  const label = () => thinking.querySelector(".dd-label").textContent;
  thinking.querySelector(".dd-btn").click();
  [...thinking.querySelectorAll(".dd-item")].find((el) => el.textContent === "High").click();
  assert.strictEqual(label(), "High", "the pick is optimistic");
  assert.strictEqual(h.posted.at(-1).requestId, 1, "the pick is tagged");

  h.post({ type: "sessionReady", sessionId: "s-new" });
  h.post(options("low"));
  await h.settle(20);
  assert.strictEqual(label(), "High", "a stale reply cannot undo the pick just because the session arrived");

  h.post({ type: "modelConfigApplied", requestId: 1, model: "claude-medium", thoughtLevel: "high" });
  await h.settle(20);
  assert.strictEqual(label(), "High", "the acknowledgement for the pending pick still applies");

  h.post(options("low"));
  await h.settle(20);
  assert.strictEqual(label(), "Low", "once acknowledged, authoritative replies apply again");
  assert.strictEqual(h.errors().length, 0);
});

test("catalogue lead names strip the effort suffix whatever level it carries", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options", currentMode: "accept-edits",
    currentModel: "fusion-gpt-6-astra-low-sidekick-swe-2-medium",
    currentThoughtLevel: "low",
    thoughtLevels: [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }],
    modelChoices: {},
    models: [{
      id: "fusion", name: "Fusion", default: "fusion-gpt-6-astra-low-sidekick-swe-2-medium",
      variants: [
        { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "(Claude Opus 5 High Thinking + SWE-2 Medium)" },
        { value: "fusion-claude-opus-5-low-sidekick-swe-2-medium", name: "(Claude Opus 5 Low Thinking + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-high-sidekick-swe-2-medium", name: "(GPT-6 Astra High Thinking + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-medium-sidekick-swe-2-medium", name: "(GPT-6 Astra Medium Thinking + SWE-2 Medium)" },
        { value: "fusion-gpt-6-astra-low-sidekick-swe-2-medium", name: "(GPT-6 Astra Low Thinking + SWE-2 Medium)" }
      ]
    }],
    modes: []
  });
  await h.settle(20);
  const picker = h.document.querySelector("#fusion-dd");
  picker.querySelector(".dd-btn").click();
  const leadNames = [...picker.querySelectorAll(".fusion-config-option[data-kind=lead]")].map((el) => el.textContent);
  assert.deepStrictEqual(leadNames, ["Claude Opus 5", "GPT-6 Astra"]);
  assert.strictEqual(h.errors().length, 0);
});

test("a reply about another Fusion lead cannot move the shown effort", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  const variants = [
    { value: "fusion-claude-opus-5-low-sidekick-swe-2-medium", name: "(Claude Opus 5 Low + SWE-2 Medium)" },
    { value: "fusion-claude-opus-5-high-sidekick-swe-2-medium", name: "(Claude Opus 5 High + SWE-2 Medium)" },
    { value: "fusion-gpt-6-astra-low-sidekick-swe-2-medium", name: "(GPT-6 Astra Low + SWE-2 Medium)" },
    { value: "fusion-gpt-6-astra-high-sidekick-swe-2-medium", name: "(GPT-6 Astra High + SWE-2 Medium)" }
  ];
  const allModels = [{ id: "fusion", name: "Fusion", default: "fusion-claude-opus-5-high-sidekick-swe-2-medium", variants }];
  const levels = [{ value: "low", name: "Low" }, { value: "high", name: "High" }];
  const options = (currentModel, currentThoughtLevel) => ({
    type: "options", currentMode: "accept-edits", currentModel, currentThoughtLevel,
    thoughtLevels: levels, modelChoices: {}, models: allModels, modes: []
  });
  h.post(options("fusion-claude-opus-5-high-sidekick-swe-2-medium", "high"));
  await h.settle(20);
  const picker = h.document.querySelector("#fusion-dd");
  const title = () => picker.querySelector(".fusion-config-lead-title").textContent;
  assert.strictEqual(title(), "Opus 5 · High");
  h.post(options("fusion-gpt-6-astra-low-sidekick-swe-2-medium", "low"));
  await h.settle(20);
  assert.strictEqual(title(), "Opus 5 · High", "a reply about GPT-6 Astra cannot change Opus 5's shown effort");
  assert.strictEqual(h.errors().length, 0);
});

test("thinking picker keeps a thought icon in compact mode", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({
    type: "options",
    currentMode: "accept-edits",
    currentModel: "claude-medium",
    models: [{
      id: "claude",
      name: "Claude",
      default: "claude-medium",
      variants: [
        { value: "claude-low", name: "Low" },
        { value: "claude-medium", name: "Medium" }
      ]
    }],
    modes: []
  });
  await h.settle(20);

  const thinking = h.document.querySelector("#thinking-dd");
  assert.ok(!thinking.classList.contains("hidden"), "the picker is available for multiple variants");
  assert.ok(thinking.querySelector(".dd-icon .codicon-thinking"), "the compact control has a thought icon");
  assert.strictEqual(thinking.querySelector(".dd-btn").getAttribute("aria-label"), "Thinking effort");

  const css = fs.readFileSync(path.join(ROOT, "media", "main.css"), "utf8");
  assert.match(css, /#thinking-dd \.dd-icon\s*\{\s*display: none;/, "the normal control hides the thought icon");
  assert.match(css, /#input-box\.cmp-sm #thinking-dd \.dd-icon\s*\{\s*display: inline-flex;/, "compact mode shows the thought icon");
  assert.doesNotMatch(css, /#input-box\.cmp-sm #thinking-dd \{/, "the small tier keeps the thinking control");
  assert.match(css, /#input-box\.cmp-xxs #mode-dd,\s*\n#input-box\.cmp-xxs #thinking-dd/, "the smallest tier drops the redundant controls, keeping attach and send");
  assert.strictEqual(h.errors().length, 0);
});

test("tool output carries a copy action, and JSON is pretty printed", async () => {
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  // A plain tool, not an MCP one: JSON is JSON whoever returned it.
  h.post({ type: "toolCall", id: "j1", title: "Read config", kind: "read", status: "pending" });
  h.post({ type: "toolCallUpdate", id: "j1", status: "completed",
    content: [{ type: "text", text: '{"name":"vtx","nested":{"on":true}}' }] });
  await h.settle(20);
  const card = [...h.document.querySelectorAll("#thread .tool")].find((t) => /Read config/.test(t.textContent));
  const pre = card.querySelector(".tool-block .tool-pre");
  assert.ok(pre.classList.contains("hljs"), "highlighted");
  assert.match(pre.textContent, /^\{\n  "name": "vtx",/, "and pretty printed");
  const copy = card.querySelector(".tool-block .tool-toolbar .dv-copy");
  assert.ok(copy, "with a copy action");
  copy.click();
  await h.settle(10);
  const posted = h.posted.filter((m) => m.type === "copyText").pop();
  assert.match(posted.text, /"nested": \{/, "which copies the formatted text");
  assert.strictEqual(h.errors().length, 0);
});

test("clearing the thread takes the permission menu with it", async () => {
  // The scope menu is anchored to a button in the permission tray but lives on the
  // body, so emptying the tray by hand left it floating over the new thread, still
  // offering to answer a request that had gone.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "userChunk", text: "run something" });
  h.post({
    type: "permission",
    requestId: "p9",
    command: "exit 42",
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "allow_always", name: "Yes, always allow `exit` commands here", kind: "allow_always" },
      { optionId: "allow_always_global", name: "Yes, always allow `exit` commands everywhere", kind: "allow_always" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  await h.settle(20);
  h.document.querySelector("#permission-tray .cw-more").dispatchEvent(new h.window.MouseEvent("click", { bubbles: true }));
  await h.settle(20);
  assert.ok(h.document.querySelector(".dv-menu"), "the menu is open");

  // A new chat, or opening another one, clears the thread underneath it.
  h.post({ type: "clear" });
  await h.settle(20);
  assert.strictEqual(h.document.querySelector(".dv-menu"), null, "and it goes with the thread");
  assert.strictEqual(h.errors().length, 0);
});

test("a file path with a space or an accent in it still opens", async () => {
  // The markdown renderer percent encodes every href, so the string on the anchor
  // is not a path any more. Sent as one, it opened nothing, and the only sign of
  // it was a line in the extension's output channel.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "assistantChunk", text: "see [docs/caf\u00e9.md](<docs/caf\u00e9 notes.md>)" });
  h.post({ type: "assistantEnd" });
  await h.settle(30);

  const link = [...h.thread().querySelectorAll("a[href]")].pop();
  assert.ok(link, "the link is rendered");
  link.dispatchEvent(new h.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await h.settle(10);
  const opened = h.posted.filter((m) => m.type === "openFile").pop();
  assert.ok(opened, "the click asks the host to open it");
  assert.strictEqual(opened.path, "docs/caf\u00e9 notes.md", "as a path, not as a percent encoded url");
  assert.strictEqual(h.errors().length, 0);
});

test("turning the changed files summary off turns it off", async () => {
  // The setting was read by the host, sent to the page and stored, and then never
  // looked at, so it did nothing whichever way it was set.
  const h = createHarness();
  h.post({ type: "ready" });
  h.post({ type: "body", body: "thread" });
  h.post({ type: "capabilities", revert: true, showFileChanges: true });
  h.post({ type: "sessionReady", sessionId: "A" });
  h.post({ type: "workingSet", files: [{ path: "/w/app.ts", added: 2, removed: 1 }] });
  await h.settle(20);
  const ws = h.document.querySelector("#working-set");
  assert.ok(!ws.classList.contains("hidden"), "on by default, the summary is there");

  h.post({ type: "capabilities", revert: true, showFileChanges: false });
  await h.settle(20);
  assert.ok(ws.classList.contains("hidden"), "and turning it off takes it away now, not next turn");
  assert.strictEqual(h.errors().length, 0);
});
});
