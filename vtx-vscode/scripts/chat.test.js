// Tests for the host side of a chat (src/chat/chatViewProvider.ts), through the
// harness in chat-harness.js: the real controller, driven by the same messages the
// page sends, talking to a real ACP agent over stdio.
//
// They all pin one property, because one shape of bug keeps being found here and it
// is always this: everything the controller does takes seconds (spawning an agent,
// opening a session, waking one), the user can open another chat while it happens,
// and the code after the await speaks for whatever is on screen by then rather than
// for the chat it was working on. Every fix for it used to be made by reading, which
// is why the same mistake kept being made one line away from where it was fixed.
//
// The agent's delays are the whole technique: holding session/new or session/load
// open is what makes the window between asking and arriving wide enough to act in.

const test = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { createChat, cleanup } = require("./chat-harness");

// The fake agent is a `/bin/sh` wrapper, so the CLI resolver cannot run it on Windows:
// the health check fails, no chat starts, and every test here would fail for that one
// reason. Skipped rather than left to fail, until the harness grows a `.cmd` shim (which
// would also exercise the Windows quoting path in cli/locate.ts, so it is worth doing).
const posixOnly = { skip: process.platform === "win32" };

const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

test("bypass mode answers command permission requests without showing them", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "" } });
  h.setAgentMode("bypass");
  const id = await h.startChat("bypass commands");
  const pending = h.controller.requestPermission({
    sessionId: id,
    toolCall: {
      toolCallId: "command",
      _meta: { "cognition.ai/editableCommand": "gh api graphql" }
    },
    options: [
      { optionId: "allow_once", name: "Allow", kind: "allow_once" },
      { optionId: "reject_once", name: "Reject", kind: "reject_once" }
    ]
  });
  try {
    const result = await Promise.race([
      pending,
      new Promise((resolve) => setTimeout(() => resolve(undefined), 100))
    ]);
    assert.deepStrictEqual(result, { outcome: { outcome: "selected", optionId: "allow_once" } });
    assert.strictEqual(h.postsOf("permission").length, 0);
  } finally {
    await h.dispose();
  }
});

test("ACP configuration exposes grouped models and applies thinking effort", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  try {
    await h.ready();
    await h.startChat("configure Fusion");

    const initialize = h.agentSaw("initialize")[0];
    assert.strictEqual(initialize.params.clientCapabilities._meta["cognition.ai/groupedSessionConfigOptions"], true);
    assert.strictEqual(initialize.params.clientCapabilities._meta["cognition.ai/multiRootWorkspace"], true);

    const options = h.last("options");
    assert.deepStrictEqual(options.modelChoices.fusion.map((choice) => choice.value), [
      "fusion-claude-opus-5-high-sidekick-swe-2-medium",
      "fusion-gpt-6-astra-high-sidekick-swe-2-high"
    ]);
    assert.strictEqual(options.currentThoughtLevel, "high");
    assert.deepStrictEqual(options.thoughtLevels.map((choice) => choice.value), ["low", "high"]);

    h.send({ type: "setConfigOption", configId: "thought_level", value: "low" });
    await h.until(() => h.agentSaw("session/set_config_option").some((request) => request.params.configId === "thought_level"));
    const request = h.agentSaw("session/set_config_option").find((item) => item.params.configId === "thought_level");
    assert.strictEqual(request.params.value, "low");
    await h.until(() => h.last("options")?.currentThoughtLevel === "low");

    const before = h.agentSaw("session/set_config_option").length;
    h.send({ type: "setFusionModel", model: "fusion-gpt-6-astra-high-sidekick-swe-2-high", thoughtLevel: "low" });
    await h.until(() => h.agentSaw("session/set_config_option").length === before + 2);
    assert.deepStrictEqual(
      h.agentSaw("session/set_config_option").slice(before).map((item) => [item.params.configId, item.params.value]),
      [["model", "fusion-gpt-6-astra-high-sidekick-swe-2-high"], ["thought_level", "low"]],
      "changing a Fusion pair restores the chosen lead effort after the model resets it"
    );
  } finally {
    await h.dispose();
  }
});

test("standard ACP tool names reach the webview", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "" } });
  try {
    await h.ready();
    const id = await h.startChat("name the tool");
    h.controller.runtimes.get(id).client.emit("update", {
      sessionId: id,
      update: { sessionUpdate: "tool_call", toolCallId: "search", name: "web_search", kind: "fetch" }
    });
    await h.until(() => h.postsOf("toolCall").some((message) => message.id === "search"));
    assert.strictEqual(h.postsOf("toolCall").find((message) => message.id === "search").meta.inferenceToolName, "web_search");
  } finally {
    await h.dispose();
  }
});

test("idle sessions do not hand diagnostics to the agent", posixOnly, async () => {
  const h = createChat({ promptDelay: 500, config: { "editorContext.diagnostics": true } });
  await h.ready();
  const uri = globalThis.__dvVscode.Uri.file(path.join(h.cwd, "src", "example.ts"));
  globalThis.__dvVscode.languages.diagnostics.set(uri.fsPath, [{
    message: "Lint errors detected.",
    severity: globalThis.__dvVscode.DiagnosticSeverity.Error,
    code: "lint",
    source: "eslint",
    range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }
  }]);

  const id = await h.startChat("check the file");
  await h.until(() => h.postsOf("busy").some((m) => m.value === true));
  globalThis.__dvFolders = [{ name: path.basename(h.cwd), uri: globalThis.__dvVscode.Uri.file(h.cwd), index: 0 }];
  assert.deepStrictEqual(
    h.controller.requestDiagnostics({ sessionId: id }).items,
    [],
    "unscoped requests must not return workspace diagnostics"
  );
  assert.strictEqual(
    h.controller.requestDiagnostics({ sessionId: id, path: uri.fsPath }).items.length,
    1,
    "active turns still receive editor diagnostics"
  );

  await h.until(() => h.postsOf("busy").some((m) => m.value === false), 3000);
  assert.deepStrictEqual(
    h.controller.requestDiagnostics({ sessionId: id, path: uri.fsPath }).items,
    [],
    "idle sessions must not trigger an unsolicited diagnostics turn"
  );
  await h.dispose();
});

test("unscoped diagnostics are withheld when multiple sessions exist", posixOnly, async () => {
  const h = createChat({ promptDelay: 60000, config: { "editorContext.diagnostics": true } });
  await h.ready();
  const first = await h.startChat("first chat");
  const uri = globalThis.__dvVscode.Uri.file(path.join(h.cwd, "src", "example.ts"));
  globalThis.__dvVscode.languages.diagnostics.set(uri.fsPath, [{
    message: "Lint errors detected.",
    severity: globalThis.__dvVscode.DiagnosticSeverity.Error,
    code: "lint",
    source: "eslint",
    range: { start: { line: 0, character: 0 }, end: { line: 0, character: 1 } }
  }]);
  globalThis.__dvFolders = [{ name: path.basename(h.cwd), uri: globalThis.__dvVscode.Uri.file(h.cwd), index: 0 }];

  h.setDelays({ promptDelay: 500 });
  h.send({ type: "send", text: "second chat", newSession: true });
  await h.until(() => {
    const active = h.controller.runtimes.get(h.activeId());
    return h.liveChats() === 2 && h.activeId() !== first && active?.busy;
  }, 6000);

  assert.deepStrictEqual(
    h.controller.requestDiagnostics({ path: uri.fsPath }).items,
    [],
    "an unscoped request must not use another session's active runtime"
  );
  await h.dispose();
});

// What each prompt actually carried, in the order the agent was asked.
function prompts(h) {
  return h.agentSaw("session/prompt").map((m) => {
    const blocks = m.params.prompt || [];
    return {
      text: blocks.filter((b) => b.type === "text").map((b) => b.text).join(""),
      images: blocks.filter((b) => b.type === "image").length
    };
  });
}

test("file URI links open the referenced file", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  globalThis.__dvOpened = [];
  const fileUri = globalThis.__dvVscode.Uri.file(path.join(h.cwd, "src", "client.ts")).toString();
  const upperFileUri = fileUri.replace(/^file:/, "FILE:");

  for (const href of [fileUri, upperFileUri]) {
    h.send({ type: "openFile", path: href });
    await h.until(() => globalThis.__dvOpened.at(-1) === fileUri);
  }

  assert.deepStrictEqual(globalThis.__dvOpened.slice(-2), [fileUri, fileUri]);
  await h.dispose();
});

test("a message does not take the files of the chat opened while it was being sent", posixOnly, async () => {
  // Starting a chat takes seconds, and the composer belongs to whatever is on screen.
  // Opening another chat and staging a file in it during that window used to end with
  // the file sent to the agent of the chat being started, and then deleted, because
  // clearing the composer afterwards saves against whichever chat is being shown.
  const h = createChat();
  await h.ready();
  const existing = await h.startChat("an existing chat");
  h.send({ type: "leaveToList" });
  await h.settle(100);

  // A new chat, held open at session/new.
  h.setDelays({ newDelay: 900 });
  h.send({ type: "send", text: "a brand new chat", newSession: true });
  await h.settle(150);

  // While it starts, the user opens the chat they had and stages a file in it.
  h.send({ type: "loadSession", id: existing });
  await h.until(() => h.activeId() === existing, 4000);
  // Opening a chat hands the composer its own staged files, so the file is staged
  // after that has happened rather than into the middle of it.
  await h.settle(150);
  h.send({ type: "attachImage", name: "staged-here.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);
  await h.settle(1200);

  assert.strictEqual(h.activeId(), existing, "the panel is still on the chat the user opened");
  const staged = h.last("attachments");
  assert.strictEqual(staged.items.length, 1, "its staged file is still staged");
  assert.strictEqual(staged.items[0].label, "staged-here.png");
  assert.deepStrictEqual(
    prompts(h).map((p) => p.images),
    [0, 0],
    "and no prompt carried a file staged in another chat"
  );
  await h.dispose();
});

test("a file staged before a chat exists follows it into the chat", posixOnly, async () => {
  // The sessions list has a composer of its own, so a screenshot can be attached
  // before there is any chat to attach it to. Starting a chat carries what was waiting
  // there into it, and sending carries it with the message instead.
  const h = createChat();
  await h.ready();
  h.send({ type: "attachImage", name: "from-the-list.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);

  h.send({ type: "newSession" });
  await h.until(() => !!h.activeId(), 6000);
  await h.settle(200);
  const started = h.activeId();
  assert.strictEqual(
    ((h.last("attachments") || {}).items || []).length,
    1,
    "it is still staged in the chat the box became"
  );

  // Sent from there, it goes with the message and stops being staged, rather than
  // being sent again with the next one.
  h.send({ type: "send", text: "look at this" });
  await h.until(() => h.agentSaw("session/prompt").length === 1, 6000);
  await h.settle(200);
  assert.deepStrictEqual(prompts(h).map((p) => p.images), [1], "the message carried it");
  assert.strictEqual(((h.last("attachments") || {}).items || []).length, 0, "and nothing is left staged");
  assert.ok(started, "the chat started");
  await h.dispose();
});

test("a file sent with the first message is not left staged in the chat it started", posixOnly, async () => {
  // Sending from the sessions list starts a chat and sends in one go, so the file is
  // staged in the box, carried into the chat the box becomes, and sent. It has to stop
  // being staged in both places, or the next message sends it again.
  const h = createChat();
  await h.ready();
  h.send({ type: "attachImage", name: "sent-with-the-first.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);

  h.send({ type: "send", text: "start with this", newSession: true });
  await h.until(() => h.agentSaw("session/prompt").length === 1, 6000);
  await h.settle(250);

  assert.deepStrictEqual(prompts(h).map((p) => p.images), [1], "the first message carried it");
  assert.strictEqual(((h.last("attachments") || {}).items || []).length, 0, "and it is no longer staged");
  await h.dispose();
});

test("a file staged and not sent is still there after a reload", posixOnly, async () => {
  // A staged file belongs to a prompt that has not been sent, so it outlives the agent:
  // an image pasted into the composer has no source file to attach again, and nothing
  // else in the extension can put it back. Written down per chat, and read back for a
  // chat this window has not staged anything for yet.
  const first = createChat();
  await first.ready();
  const id = await first.startChat("before the reload");
  first.send({ type: "attachImage", name: "survives-a-reload.png", mime: "image/png", data: PNG });
  await first.until(() => ((first.last("attachments") || {}).items || []).length === 1);
  await first.settle(150);
  await first.dispose();

  // A new window over the same workspace: a fresh controller, the same storage.
  const next = createChat({ storage: first.storage, cwd: first.cwd });
  await next.ready();
  next.send({ type: "loadSession", id });
  await next.until(() => next.activeId() === id, 8000);
  await next.until(() => ((next.last("attachments") || {}).items || []).length === 1, 4000);

  const staged = next.last("attachments").items;
  assert.strictEqual(staged.length, 1, "it is staged again");
  assert.strictEqual(staged[0].label, "survives-a-reload.png");
  assert.ok(staged[0].thumb.startsWith("data:image/png;base64,"), "with the image itself, not just its name");
  await next.dispose();
});

test("a chat handed to another surface leaves its staged files readable", posixOnly, async () => {
  // Moving a chat to an editor tab hands the staged files over and deliberately leaves
  // the copy on disk, because whichever surface shows the chat next reads it back the
  // same way a reload does. So this surface must let go of it completely: an entry left
  // behind here, even an empty one, is what stops that file ever being read again.
  const h = createChat();
  await h.ready();
  const id = await h.startChat("moving out");
  h.send({ type: "attachImage", name: "goes-with-the-chat.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);
  await h.settle(150);

  const transfer = h.controller.exportRuntime(id);
  assert.ok(transfer, "the chat was handed over");
  assert.strictEqual(transfer.attachments.length, 1, "with what was staged for it");
  // Handing a chat over means giving up responsibility for its agent, so in production
  // the arriving surface stops it. Here nobody does, and an agent nothing can stop keeps
  // the test runner alive for ever, so this test owns it.
  await transfer.rt.client.shutdown().catch(() => {});

  // Later, the chat is opened here again: the tab was closed, or it was terminated.
  h.send({ type: "loadSession", id });
  await h.until(() => h.activeId() === id, 8000);
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1, 4000);
  assert.strictEqual(
    h.last("attachments").items[0].label,
    "goes-with-the-chat.png",
    "and the file it left on disk is read back"
  );
  await h.dispose();
});

test("a chat that finishes starting in the background does not take the panel", posixOnly, async () => {
  // No configured default mode, so each chat keeps the mode its own agent reports and
  // the two can be told apart.
  const h = createChat({ config: { defaultMode: "" } });
  await h.ready();
  const first = await h.startChat("first chat");

  // The chat started in the background is in a different mode, so its own settings
  // can be told from the visible chat's.
  h.setDelays({ newDelay: 700 });
  h.setAgentMode("plan");
  const newSessionRequests = h.agentSaw("session/new").length;
  h.send({ type: "send", text: "a new chat", newSession: true });
  assert.ok(
    await h.until(() => h.agentSaw("session/new").length > newSessionRequests, 60000),
    "The second session/new request was sent."
  );
  h.send({ type: "loadSession", id: first });
  await h.until(() => h.activeId() === first);
  // The background chat really did finish starting: without this every assertion below
  // is satisfied by it never having got there. Waiting on the request being logged is
  // not enough, since the agent logs it before it answers.
  // Generous, because the whole suite runs in parallel and this waits on a process being
  // spawned: the budget is not the thing under test, so it says why if it runs out.
  assert.ok(
    await h.until(() => h.liveChats() === 2, 60000),
    `the second chat finished opening. asked: ${h.agentSaw("session/new").length}, open: ${h.liveChats()},` +
      ` errors: ${JSON.stringify(h.postsOf("error"))}, log: ${h.logs.slice(-3).join(" | ")}`
  );
  await h.settle(300);

  assert.strictEqual(h.activeId(), first, "the panel stays where the user put it");
  // What a window reload resumes from has to name the chat being read, not whichever
  // one happened to finish opening last.
  assert.strictEqual(h.store.viewing(), first, "and that is what a reload would reopen");
  const announced = new Set(h.postsOf("sessionReady").map((m) => m.sessionId));
  assert.deepStrictEqual([...announced], [first], "only the visible chat announces itself");
  // The mode picker is the one control where being wrong matters, since it says
  // whether permission will be asked for before anything runs.
  assert.strictEqual(h.last("mode").mode, "default", "the picker still shows the visible chat's mode");
  assert.notStrictEqual(h.controller.currentMode, "plan", "and the panel did not take the background chat's");
  await h.dispose();
});

test("a chat still opening is reported as starting, not as idle", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("hello");

  // Terminated so it can be opened again, which is the path that replays history.
  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => (h.last("sessionStatuses") || { statuses: {} }).statuses[id] === undefined);

  h.setDelays({ loadDelay: 900 });
  h.send({ type: "loadSession", id });
  await h.until(() => ((h.last("sessionStatuses") || { statuses: {} }).statuses[id]) === "starting", 4000);
  assert.strictEqual(
    h.last("sessionStatuses").statuses[id],
    "starting",
    "the row says it is opening while it opens"
  );
  await h.until(() => (h.last("sessionStatuses") || { statuses: {} }).statuses[id] === "idle", 6000);
  await h.dispose();
});

test("messages queued behind a turn come back as a draft when the surface goes", posixOnly, async () => {
  // The queue only ever lived on the runtime. Closing a chat tab mid turn offers to
  // terminate it, and taking that offer went through dispose, which threw the queue
  // away, while terminating the same chat from the list handed it back.
  const h = createChat({ promptDelay: 60000 });
  await h.ready();
  const id = await h.startChat("the turn");
  await h.until(() => h.postsOf("busy").some((m) => m.value));

  h.send({ type: "send", text: "queued behind it" });
  await h.until(() => (h.last("queued") || { items: [] }).items.length === 1);
  assert.deepStrictEqual(prompts(h).map((p) => p.text), ["the turn"], "the second is not sent");

  h.controller.dispose();
  await h.until(() => /queued behind it/.test(h.store.draft(id) || ""));
  assert.match(h.store.draft(id), /queued behind it/, "the writing is back in that chat's box");
  await h.dispose();
});

test("a staged file goes with one message, not with both of them", posixOnly, async () => {
  // The message written first owns what was staged when it was written. A second one
  // written while the first is still waiting for its chat to open must not find the
  // same files still staged: they belong to a message that has already gone.
  const h = createChat({ promptDelay: 60000 });
  await h.ready();
  h.send({ type: "attachImage", name: "one-message-only.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);

  // Sent from the list, so the chat has to be created first, which is the window the
  // second message is written in.
  h.setDelays({ newDelay: 900 });
  h.send({ type: "send", text: "first message", newSession: true });
  await h.settle(200);
  h.send({ type: "send", text: "second message" });
  await h.settle(2500);

  // The first message went out with it. The second is waiting behind that turn, and
  // must not be holding the same file: it would be sent again when the queue drains.
  const sent = prompts(h).reduce((n, p) => n + p.images, 0);
  const waiting = (h.last("queued").items || []).reduce((n, q) => n + (q.attachments || []).length, 0);
  assert.strictEqual(sent, 1, "the message that was written with it carried it: " + JSON.stringify(prompts(h)));
  assert.strictEqual(waiting, 0, "and the one written after it is not holding it too");
  await h.dispose();
});

test("a later queued image stays on its own message", posixOnly, async () => {
  const h = createChat({ promptDelay: 60000 });
  await h.ready();
  await h.startChat("turn in progress");
  await h.until(() => h.postsOf("busy").some((m) => m.value));

  h.send({ type: "send", text: "text only" });
  await h.until(() => (h.last("queued") || { items: [] }).items.length === 1);

  h.send({ type: "attachImage", name: "later.png", mime: "image/png", data: PNG });
  await h.until(() => ((h.last("attachments") || {}).items || []).length === 1);
  h.send({ type: "send", text: "with image" });
  await h.until(() => (h.last("queued") || { items: [] }).items.length === 2);

  const items = h.last("queued").items;
  assert.deepStrictEqual(items.map((q) => q.text), ["text only", "with image"]);
  assert.deepStrictEqual(
    items.map((q) => (q.attachments || []).map((a) => a.label)),
    [[], ["later.png"]]
  );
  await h.dispose();
});

test("a second send never puts a second prompt on the same channel", posixOnly, async () => {
  // Opening a chat drains whatever was typed while it opened, so the turn that starts
  // can be in flight exactly when the send that asked for the wake resumes. ACP has no
  // way to hand a prompt to a live one: a second contends with the first, and they are
  // delivered out of order.
  const h = createChat({ promptDelay: 60000 });
  await h.ready();
  const id = await h.startChat("start");

  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => (h.last("sessionStatuses") || { statuses: {} }).statuses[id] === undefined);

  // The first send wakes it. The second is written while it is waking, so it waits,
  // and the wake drains the queue the moment it finishes.
  h.setDelays({ loadDelay: 900 });
  h.send({ type: "send", text: "first message" });
  await h.settle(200);
  h.send({ type: "send", text: "second message" });
  await h.settle(2500);

  const after = prompts(h).slice(1).map((p) => p.text);
  assert.strictEqual(after.length, 1, "one turn was started, not two: " + JSON.stringify(after));
  assert.strictEqual(h.last("queued").items.length, 1, "and the other is waiting its turn");
  await h.dispose();
});

test("a message written while a chat wakes is sent once it is awake", posixOnly, async () => {
  // A wake takes seconds and its channel is busy replaying, so a message written into
  // it waits in the queue. Opening a chat drains that queue on the way out and waking
  // one did not, so the message sat there until some later turn happened to end: sent
  // out of order, or never, from a chat whose composer had gone quiet.
  const h = createChat({ promptDelay: 150 });
  await h.ready();
  const id = await h.startChat("first");
  await h.until(() => prompts(h).length === 1, 6000);

  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => h.liveChats() === 0, 6000);
  h.answerWith(undefined);

  h.setDelays({ loadDelay: 900 });
  h.send({ type: "wakeSession", id });
  await h.settle(250);
  h.send({ type: "send", text: "typed while it woke" });
  const sent = await h.until(() => prompts(h).some((p) => p.text === "typed while it woke"), 8000);

  assert.ok(sent, "the message went out: " + JSON.stringify(prompts(h)));
  assert.deepStrictEqual((h.last("queued") || { items: [] }).items, [], "and nothing is left waiting");
  await h.dispose();
});

test("a message goes to the chat it was written in, not the one opened while it woke", posixOnly, async () => {
  // The same shape as every other bug here: the send waits for the wake, and the code
  // after the await asked what was on screen by then. Writing into a chat, then opening
  // another while it wakes, used to send the message to that other chat's agent.
  const h = createChat({ promptDelay: 150 });
  await h.ready();
  const woken = await h.startChat("the chat with the message");
  await h.until(() => prompts(h).length === 1, 6000);
  h.answerWith("Terminate");
  h.send({ type: "terminateSession", woken, id: woken });
  await h.until(() => h.liveChats() === 0, 6000);
  h.answerWith(undefined);

  const other = await h.startChat("the chat opened instead");
  await h.until(() => prompts(h).length === 2, 6000);
  h.send({ type: "leaveToList" });
  await h.settle(100);

  // Wake the first, write into it, then open the other one while it is still waking.
  h.setDelays({ loadDelay: 1200 });
  h.send({ type: "wakeSession", id: woken });
  await h.settle(250);
  h.send({ type: "send", text: "for the chat I wrote it in" });
  await h.settle(50);
  h.send({ type: "activateSession", id: other });
  await h.until(() => h.activeId() === other, 4000);
  await h.settle(2000);

  const carried = h.agentSaw("session/prompt").filter((m) =>
    (m.params.prompt || []).some((b) => b.text === "for the chat I wrote it in")
  );
  assert.strictEqual(carried.length, 1, "it was sent once");
  assert.strictEqual(carried[0].params.sessionId, woken, "to the chat it was written in");
  await h.dispose();
});

test("a chat announcing its mode while it starts does not set the panel's", posixOnly, async () => {
  // A session's first updates arrive before session/new returns, so its runtime is not
  // in the pool to be found by id. Answering those with whatever is on screen flipped
  // the mode picker of the chat being read to a background chat's, which is the one
  // control where being wrong matters: it says whether permission is asked for before
  // anything runs.
  const h = createChat({ config: { defaultMode: "" }, promptDelay: 150 });
  await h.ready();
  const first = await h.startChat("the chat on screen");
  await h.until(() => (h.last("mode") || {}).mode === "default", 6000);

  h.setAgentMode("plan");
  h.setDelays({ newDelay: 700 });
  h.send({ type: "send", text: "a chat started in the background", newSession: true });
  await h.settle(120);
  h.send({ type: "loadSession", id: first });
  await h.until(() => h.activeId() === first, 6000);
  assert.ok(await h.until(() => h.liveChats() === 2, 60000), "the background chat finished starting");
  await h.settle(300);

  assert.strictEqual(h.activeId(), first, "the panel stays where the user put it");
  assert.strictEqual(h.last("mode").mode, "default", "and still shows its own mode");
  assert.notStrictEqual(h.controller.currentMode, "plan", "not the one the background chat announced");
  await h.dispose();
});

test("stopping a turn does not let the next message contend with it", posixOnly, async () => {
  // Stop is a notification: the prompt stays open until the agent answers it, which can
  // take as long as the command it is waiting on. Freeing the composer is right, but
  // clearing the turn's own busy flag with it let the next message straight past the one
  // gate there is and onto a channel that already had a prompt on it.
  const h = createChat({ promptDelay: 60000 });
  await h.ready();
  await h.startChat("a turn that will not end");
  await h.until(() => prompts(h).length === 1, 6000);

  h.send({ type: "cancel" });
  await h.settle(100);
  h.send({ type: "send", text: "after the stop" });
  await h.settle(600);

  assert.strictEqual(prompts(h).length, 1, "still one prompt: " + JSON.stringify(prompts(h)));
  assert.strictEqual(h.last("queued").items.length, 1, "and the message is waiting, not lost");
  await h.dispose();
});

test("a closed surface does not start an ACP agent after delayed startup", posixOnly, async () => {
  const h = createChat();
  h.controller.autoNewSession = true;
  const runHealthCheck = h.controller.runHealthCheck.bind(h.controller);
  h.controller.runHealthCheck = async () => {
    await h.settle(400);
    await runHealthCheck();
  };

  h.send({ type: "ready" });
  await h.settle(50);
  h.controller.markClosed();
  h.controller.dispose();
  await h.settle(1200);

  assert.strictEqual(h.agentSaw("initialize").length, 0, "no ACP process starts after disposal");
  assert.strictEqual(h.liveChats(), 0, "no runtime is retained after disposal");
  await h.dispose();
});

test("a closed destination rejects pending readiness waiters", async () => {
  const h = createChat();
  const ready = h.controller.whenReady().then(() => false, () => true);

  h.controller.markClosed();
  const released = await Promise.race([ready, h.settle(100).then(() => false)]);

  assert.strictEqual(released, true, "a closed surface stops a move before it exports a runtime");
  await h.dispose();
});

test("a restored chat starts loading before a slow session list returns", posixOnly, async () => {
  const h = createChat();
  const id = "restored";
  h.store.add(id, h.cwd);
  h.store.setViewing(id);
  let releaseListing;
  h.controller.refreshSessions = async () => {
    await new Promise((resolve) => { releaseListing = resolve; });
  };

  h.send({ type: "ready" });
  const loaded = await h.until(() => h.agentSaw("session/load").length === 1, 4000);
  releaseListing?.();

  assert.ok(loaded, "session/load starts without waiting for session listing");
  await h.dispose();
});

test("a visible session list loads its initial rows", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.until(() => h.postsOf("ready").length > 0, 4000);
  h.send({ type: "listVisible", value: true });

  const listed = await h.until(() => h.postsOf("sessions").length === 1, 2000);

  assert.ok(listed, "the visible list receives its initial session rows");
  await h.dispose();
});

test("concurrent session refreshes share one ACP list request", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  const listSessions = rt.client.listSessions.bind(rt.client);
  rt.client.listSessions = async () => {
    await h.settle(300);
    return listSessions();
  };
  h.controller.sessionsCache = undefined;
  const before = h.agentSaw("session/list").length;

  await Promise.all(Array.from({ length: 5 }, () => h.controller.refreshSessions(true)));

  assert.strictEqual(h.agentSaw("session/list").length - before, 1, "all callers share one ACP list request");
  await h.dispose();
});

test("a completed turn does not list sessions while the list is hidden", posixOnly, async () => {
  const h = createChat({ promptDelay: 100 });
  await h.ready();
  await h.startChat("first turn");
  await h.until(() => h.postsOf("busy").filter((m) => m.value === false).length === 1, 5000);
  await h.settle(200);
  const before = h.agentSaw("session/list").length;

  h.send({ type: "send", text: "second turn" });
  await h.until(() => h.postsOf("busy").filter((m) => m.value === false).length === 2, 5000);
  await h.settle(300);

  assert.strictEqual(h.agentSaw("session/list").length - before, 0, "a hidden list does not use ACP");
  await h.dispose();
});

test("a stored session list paints before a cold revalidation", posixOnly, async () => {
  const cached = {
    id: "stored",
    short_id: "stored",
    working_directory: "/workspace",
    title: "Stored chat",
    tracked: true
  };
  const h = createChat({ state: { "devin.sessionList.v1": { at: 1, sessions: [cached] } } });
  h.controller.listOverProtocol = () => new Promise(() => {});
  await h.ready();
  h.send({ type: "listVisible", value: true });

  const painted = await h.until(() => h.postsOf("sessions").some((m) => m.sessions.some((s) => s.id === "stored")), 1000);

  assert.ok(painted, "cached rows appear before the cold request settles");
  await h.dispose();
});

test("a visible session list revalidates after its surface returns", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.startChat("keep the agent live");
  h.send({ type: "listVisible", value: true });
  await h.until(() => h.agentSaw("session/list").length === 1, 5000);
  await h.until(() => h.postsOf("sessions").length === 1, 5000);
  const before = h.agentSaw("session/list").length;

  h.controller.setSurfaceVisible(false);
  h.controller.setSurfaceVisible(true);
  const refreshed = await h.until(() => h.agentSaw("session/list").length === before + 1, 1000);

  assert.ok(refreshed, "returning to a visible list revalidates its rows");
  await h.dispose();
});

test("a direct send wakes a visible retained transcript without clearing it", posixOnly, async () => {
  const h = createChat({ promptDelay: 150 });
  await h.ready();
  const id = await h.startChat("first");
  await h.until(() => h.postsOf("busy").some((m) => m.value === false), 5000);
  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => h.liveChats() === 0, 5000);
  h.answerWith(undefined);
  h.controller.activeId = id;
  h.setDelays({ loadDelay: 900 });
  const clears = h.postsOf("clear").length;

  h.send({ type: "send", text: "continue", preserveTranscript: true });
  await h.settle(250);

  assert.strictEqual(h.postsOf("clear").slice(clears).filter((m) => m.loading).length, 0, "the retained thread stays on screen while waking");
  await h.dispose();
});

test("a queued send does not wait for the wake checkpoint query", posixOnly, async () => {
  const h = createChat({ promptDelay: 100, stepsDelay: 1000 });
  await h.ready();
  const id = await h.startChat("first");
  await h.until(() => h.postsOf("busy").some((m) => m.value === false), 5000);
  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => h.liveChats() === 0, 5000);
  h.answerWith(undefined);
  const promptsBefore = h.agentSaw("session/prompt").length;

  h.send({ type: "wakeSession", id });
  await h.settle(100);
  h.send({ type: "send", text: "after wake" });
  const sent = await h.until(() => h.agentSaw("session/prompt").length === promptsBefore + 1, 500);

  assert.ok(sent, "the prompt is sent before the delayed checkpoint query returns");
  await h.dispose();
});

test("a queued send does not wait for the load checkpoint query", posixOnly, async () => {
  const h = createChat({ promptDelay: 100, stepsDelay: 1000 });
  await h.ready();
  const id = await h.startChat("first");
  await h.until(() => h.postsOf("busy").some((m) => m.value === false), 5000);
  h.answerWith("Terminate");
  h.send({ type: "terminateSession", id });
  await h.until(() => h.liveChats() === 0, 5000);
  h.answerWith(undefined);
  const promptsBefore = h.agentSaw("session/prompt").length;

  h.send({ type: "loadSession", id });
  await h.settle(100);
  h.send({ type: "send", text: "after load" });
  const sent = await h.until(() => h.agentSaw("session/prompt").length === promptsBefore + 1, 500);

  assert.ok(sent, "the prompt is sent before the delayed checkpoint query returns");
  await h.dispose();
});

test("editor lifecycle events go only to a visible chat surface", posixOnly, async () => {
  const h = createChat({ documentLifecycle: true });
  await h.ready();
  await h.startChat("keep the agent live");
  const doc = {
    uri: globalThis.__dvVscode.Uri.file(path.join(h.cwd, "src", "active.ts")),
    languageId: "typescript",
    isDirty: false
  };
  globalThis.__dvVscode.window.activeTextEditor = { document: doc };
  const before = h.agentSaw("_cognition.ai/document/didFocus").length;

  h.controller.setSurfaceVisible(false);
  globalThis.__dvVscode.window.__fire.editorChanged.fire({ document: doc });
  await h.settle(100);
  assert.strictEqual(h.agentSaw("_cognition.ai/document/didFocus").length, before, "a hidden surface sends no editor context");

  h.controller.setSurfaceVisible(true);
  globalThis.__dvVscode.window.__fire.editorChanged.fire({ document: doc });
  const sent = await h.until(() => h.agentSaw("_cognition.ai/document/didFocus").length >= before + 1, 1000);
  assert.ok(sent, "the visible surface still sends editor context");
  globalThis.__dvVscode.window.activeTextEditor = undefined;
  await h.dispose();
});

test("completed subagents release their runtime identifier mapping", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);

  h.controller.onSubagentUpdate({ _meta: { "cognition.ai/subagent_started": { agentId: "worker", title: "Worker" } } }, rt);
  h.controller.onSubagentUpdate({ _meta: { "cognition.ai/subagent_completed": { agentId: "worker", success: true } } }, rt);

  assert.strictEqual(rt.subagentIds.size, 0);
  await h.dispose();
});

test("a session evicted from history releases its staged attachments", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.until(() => h.postsOf("ready").length > 0, 5000);
  for (let i = 0; i < 200; i++) {
    h.store.add(`s${i}`, h.cwd);
  }
  h.controller.staged.set("s0", [{ id: "attachment", label: "image", type: "image", block: { type: "image", mimeType: "image/png", data: "a" } }]);

  await h.controller.createSession();
  await h.settle(100);

  assert.strictEqual(h.controller.staged.has("s0"), false);
  await h.dispose();
});

test("a session pruned from the list releases its staged attachments", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.startChat("keep the agent live");
  h.store.add("gone", h.cwd);
  h.controller.staged.set("gone", [{ id: "attachment", label: "image", type: "image", block: { type: "image", mimeType: "image/png", data: "a" } }]);

  await h.controller.refreshSessions(true);
  await h.settle(100);

  assert.strictEqual(h.store.has("gone"), false);
  assert.strictEqual(h.controller.staged.has("gone"), false);
  await h.dispose();
});

test("an idle runtime cap releases the oldest background session", posixOnly, async () => {
  const h = createChat({ promptDelay: 100, config: { maxIdleSessions: 1 } });
  await h.ready();
  const first = await h.startChat("first");
  await h.until(() => h.postsOf("busy").some((m) => m.value === false), 5000);
  h.send({ type: "newSession" });
  await h.until(() => h.liveChats() === 2 && h.activeId() !== first, 5000);

  h.controller.reapIdleRuntimes();
  await h.settle(100);

  assert.strictEqual(h.controller.runtimes.has(first), false);
  assert.strictEqual(h.liveChats(), 1);
  await h.dispose();
});

test("transcript replay is sent in bounded batches", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  rt.log = Array.from({ length: 201 }, (_, i) => ({ type: "toolCall", id: `t${i}` }));
  h.posted.length = 0;

  h.controller.replayLog(rt);

  const batches = h.postsOf("replay");
  assert.deepStrictEqual(batches.map((batch) => batch.items.length), [100, 100, 2]);
  assert.strictEqual(h.posted.at(-1).type, "loaded");
  await h.dispose();
});

test("an idle handover replays a complete local transcript without loading", posixOnly, async () => {
  const from = createChat({ promptDelay: 100 });
  await from.ready();
  const id = await from.startChat("first");
  await from.until(() => from.postsOf("busy").some((m) => m.value === false), 5000);
  const transfer = from.controller.exportRuntime(id);
  const to = createChat();
  await to.ready();
  const before = from.agentSaw("session/load").length;

  await to.controller.importRuntime(transfer);

  assert.strictEqual(from.agentSaw("session/load").length, before);
  assert.ok(to.postsOf("replay").length > 0, "the local transcript is replayed into the destination");
  await to.dispose();
  await from.dispose();
});

test("an idle handover replays retained terminal output", posixOnly, async () => {
  const from = createChat({ promptDelay: 100 });
  await from.ready();
  const id = await from.startChat("first");
  await from.until(() => from.postsOf("busy").some((m) => m.value === false), 5000);
  const rt = from.controller.runtimes.get(id);
  const terminalId = rt.terminals.create({ sessionId: id, command: "printf saved" }).terminalId;
  await rt.terminals.waitForExit(terminalId);
  const transfer = from.controller.exportRuntime(id);
  const to = createChat();
  await to.ready();

  await to.controller.importRuntime(transfer);

  assert.ok(to.postsOf("terminalOutput").some((message) => message.terminalId === terminalId && /saved/.test(message.output)));
  await to.dispose();
  await from.dispose();
});

test("a cold surface lists sessions through another surface's live ACP client", posixOnly, async () => {
  const source = createChat();
  await source.ready();
  await source.startChat("keep the agent live");
  const target = createChat({
    surfaceHost: {
      sessionListClient: () => source.controller.sessionListClient(),
      elsewhere: () => [],
      statuses: () => ({}),
      titlesChanged: () => {}
    }
  });
  await target.ready();
  target.store.add("tracked", target.cwd);
  const before = source.agentSaw("session/list").length;

  await target.controller.refreshSessions(true);

  assert.strictEqual(source.agentSaw("session/list").length, before + 1);
  await target.dispose();
  await source.dispose();
});

test("a session running in an editor tab reports its status to the sidebar", posixOnly, async () => {
  const chats = [];
  const surfaceHost = {
    elsewhere: (except) => chats.filter((chat) => chat.controller !== except).flatMap((chat) => chat.controller.liveSessions()),
    statuses: (except) => Object.assign({}, ...chats.filter((chat) => chat.controller !== except).map((chat) => chat.controller.liveSessionStatuses())),
    sessionsChanged: (except) => chats.filter((chat) => chat.controller !== except).forEach((chat) => chat.controller.surfacesChanged()),
    sessionListClient: () => undefined,
    titlesChanged: () => {}
  };
  const editor = createChat({ kind: "editor", promptDelay: 2000, surfaceHost });
  chats.push(editor);
  await editor.ready();
  const id = await editor.startChat("keep working");
  const sidebar = createChat({ surfaceHost });
  chats.push(sidebar);

  sidebar.controller.surfacesChanged();

  assert.ok(await sidebar.until(() => sidebar.last("sessionStatuses")?.statuses[id] === "running"));
  assert.ok(await sidebar.until(() => sidebar.last("sessionStatuses")?.statuses[id] === "idle"));
  await sidebar.dispose();
  await editor.dispose();
});

test("a background runtime does not receive the visible editor documents", posixOnly, async () => {
  const h = createChat({ documentLifecycle: true });
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  const doc = {
    uri: globalThis.__dvVscode.Uri.file(path.join(h.cwd, "src", "active.ts")),
    languageId: "typescript",
    isDirty: false
  };
  globalThis.__dvVscode.workspace.textDocuments = [doc];
  globalThis.__dvVscode.window.activeTextEditor = { document: doc };
  h.controller.activeId = undefined;
  const before = h.agentSaw("_cognition.ai/document/didOpen").length;

  h.controller.sendOpenDocuments(rt);
  await h.settle(100);

  assert.strictEqual(h.agentSaw("_cognition.ai/document/didOpen").length, before);
  globalThis.__dvVscode.workspace.textDocuments = [];
  globalThis.__dvVscode.window.activeTextEditor = undefined;
  await h.dispose();
});

test("a closed destination refuses a live runtime handover", posixOnly, async () => {
  const from = createChat({ promptDelay: 100 });
  await from.ready();
  const id = await from.startChat("first");
  await from.until(() => from.postsOf("busy").some((m) => m.value === false), 5000);
  const transfer = from.controller.exportRuntime(id);
  const to = createChat();
  to.controller.markClosed();

  await assert.rejects(() => to.controller.importRuntime(transfer), /closed/);

  assert.strictEqual(to.liveChats(), 0);
  await transfer.rt.client.shutdown();
  await to.dispose();
  await from.dispose();
});

test("resolving a takeover clears its timeout", async () => {
  const h = createChat();
  const setTimeout = global.setTimeout;
  const clearTimeout = global.clearTimeout;
  const timer = { unref() {} };
  let cleared = false;
  global.setTimeout = () => timer;
  global.clearTimeout = (value) => { if (value === timer) cleared = true; };
  try {
    const pending = h.controller.askTakeover("session");
    h.controller.resolveTakeover("lock-1", "cancel");
    await pending;
    assert.strictEqual(cleared, true);
  } finally {
    global.setTimeout = setTimeout;
    global.clearTimeout = clearTimeout;
    await h.dispose();
  }
});

test("a pushed revert step list avoids a follow-up ACP query", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  rt.steps = [{ stepNumber: 1, revertTargetNodeId: 1, forkTargetNodeId: 2 }];
  rt.stepsKnown = true;
  const before = h.agentSaw("_cognition.ai/revert/listSteps").length;

  await h.controller.postTurnHead(false);

  assert.strictEqual(h.agentSaw("_cognition.ai/revert/listSteps").length, before);
  assert.ok(h.postsOf("turnHead").some((message) => message.head === 2));
  await h.dispose();
});

test("session loading starts ACP while staged attachments are restored", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.until(() => h.postsOf("ready").length > 0, 5000);
  h.store.add("stored", h.cwd);
  const loadStaged = h.controller.loadStaged.bind(h.controller);
  let spawned = false;
  const spawnRuntime = h.controller.spawnRuntime.bind(h.controller);
  h.controller.loadStaged = async (...args) => {
    await h.settle(500);
    return loadStaged(...args);
  };
  h.controller.spawnRuntime = (...args) => {
    spawned = true;
    return spawnRuntime(...args);
  };

  h.send({ type: "loadSession", id: "stored" });
  await h.settle(100);

  assert.strictEqual(spawned, true);
  await h.dispose();
});

test("a session list refresh records one timing summary", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.startChat("keep the agent live");
  h.logs.length = 0;

  await h.controller.refreshSessions(true);

  assert.strictEqual(h.logs.filter((line) => /^\[perf\] session-list /.test(line)).length, 1);
  await h.dispose();
});

test("a session load records one timing summary", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  await h.until(() => h.postsOf("ready").length > 0, 5000);
  h.store.add("stored", h.cwd);
  h.logs.length = 0;

  h.send({ type: "loadSession", id: "stored" });
  await h.until(() => h.postsOf("loaded").length > 0, 5000);

  assert.strictEqual(h.logs.filter((line) => /^\[perf\] session-load /.test(line)).length, 1);
  await h.dispose();
});

test("an optimistic ready hint refreshes a list after health succeeds", posixOnly, async () => {
  const h = createChat({ state: { "devin.readyHint.v1": true } });
  const healthCheck = h.controller.runHealthCheck.bind(h.controller);
  let release;
  h.controller.runHealthCheck = async () => new Promise((resolve) => { release = resolve; }).then(healthCheck);

  h.send({ type: "ready" });
  assert.strictEqual(await h.until(() => h.postsOf("ready").length === 1, 1000), true);
  h.send({ type: "listVisible", value: true });
  await h.settle(25);
  assert.strictEqual(h.postsOf("sessions").length, 0);

  release();
  assert.strictEqual(await h.until(() => h.postsOf("sessions").length === 1, 5000), true);
  await h.dispose();
});

test("a successful recheck resumes the last viewed session", posixOnly, async () => {
  const h = createChat({ state: { "devin.viewingSession.v1": "stored" } });
  const healthCheck = h.controller.runHealthCheck.bind(h.controller);
  h.controller.runHealthCheck = async () => {
    h.controller.health = { found: false, loggedIn: false, path: "devin" };
  };

  h.send({ type: "ready" });
  assert.strictEqual(await h.until(() => h.postsOf("setup").length > 0, 1000), true);
  h.controller.runHealthCheck = healthCheck;
  h.send({ type: "recheck" });

  assert.strictEqual(await h.until(() => h.agentSaw("session/load").some((message) => message.params.sessionId === "stored"), 5000), true);
  await h.dispose();
});

test("the idle runtime cap keeps a session that is still loading", posixOnly, async () => {
  const h = createChat({ promptDelay: 100, loadDelay: 1000, config: { maxIdleSessions: 1 } });
  await h.ready();
  const first = await h.startChat("keep the first session idle");
  assert.strictEqual(await h.until(() => h.postsOf("busy").some((message) => message.value === false), 5000), true);
  h.store.add("loading", h.cwd);
  h.send({ type: "loadSession", id: "loading" });
  assert.strictEqual(await h.until(() => h.controller.runtimes.get("loading")?.replaying === true, 1000), true);
  h.controller.activeId = first;

  h.controller.reapIdleRuntimes();

  assert.strictEqual(h.controller.runtimes.has("loading"), true);
  await h.dispose();
});

test("revealing a chat reconciles documents changed while hidden", posixOnly, async () => {
  const h = createChat({ documentLifecycle: true });
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  const first = { uri: globalThis.__dvVscode.Uri.file(path.join(h.cwd, "first.ts")), languageId: "typescript", isDirty: false };
  const second = { uri: globalThis.__dvVscode.Uri.file(path.join(h.cwd, "second.ts")), languageId: "typescript", isDirty: true };
  globalThis.__dvVscode.workspace.textDocuments = [first];
  globalThis.__dvVscode.window.activeTextEditor = { document: first };
  h.controller.sendOpenDocuments(rt);
  await h.settle(50);
  h.controller.setSurfaceVisible(false);
  globalThis.__dvVscode.workspace.textDocuments = [second];
  globalThis.__dvVscode.window.activeTextEditor = { document: second };
  const beforeClose = h.agentSaw("_cognition.ai/document/didClose").length;
  const beforeOpen = h.agentSaw("_cognition.ai/document/didOpen").length;

  h.controller.setSurfaceVisible(true);

  assert.strictEqual(await h.until(() => h.agentSaw("_cognition.ai/document/didClose").length === beforeClose + 1, 1000), true);
  assert.strictEqual(await h.until(() => h.agentSaw("_cognition.ai/document/didOpen").length === beforeOpen + 1, 1000), true);
  globalThis.__dvVscode.workspace.textDocuments = [];
  globalThis.__dvVscode.window.activeTextEditor = undefined;
  await h.dispose();
});

test("a replaying runtime is not exported before loading settles", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep the agent live");
  const rt = h.controller.runtimes.get(id);
  rt.replaying = true;
  let transfer;
  try {
    transfer = h.controller.exportRuntime(id);
    assert.strictEqual(transfer, undefined);
  } finally {
    if (transfer) {
      rt.replaying = false;
      await h.controller.importRuntime(transfer);
    }
  }
  await h.dispose();
});

test("history eviction retains attachments for a live session", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  const id = await h.startChat("keep this session live");
  h.controller.staged.set(id, [{ id: "attachment", label: "image", type: "image", block: { type: "image", mimeType: "image/png", data: "a" } }]);
  for (let i = 0; i < 199; i++) {
    h.store.add(`older-${i}`, h.cwd);
  }

  h.controller.rememberSession("new", h.cwd);
  await h.settle(50);

  assert.strictEqual(h.controller.staged.has(id), true);
  await h.dispose();
});

test("a forced refresh follows a stale list request after session removal", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  assert.strictEqual(await h.until(() => h.postsOf("ready").length > 0, 5000), true);
  h.store.add("gone", h.cwd);
  let resolve;
  let calls = 0;
  h.controller.listOverProtocol = () => {
    calls++;
    if (calls === 1) {
      return new Promise((done) => { resolve = done; });
    }
    return Promise.resolve({ sessions: [], prunedIds: [] });
  };

  const initial = h.controller.refreshSessions(true);
  await h.settle(25);
  h.store.remove("gone");
  const forced = h.controller.refreshSessions(true);
  resolve({ sessions: [{ id: "gone", short_id: "gone", working_directory: h.cwd }], prunedIds: [] });
  await Promise.all([initial, forced]);

  assert.strictEqual(calls, 2);
  assert.strictEqual(h.last("sessions").sessions.some((session) => session.id === "gone"), false);
  await h.dispose();
});

test("a new prompt invalidates its previous revert step list", posixOnly, async () => {
  const h = createChat({ promptDelay: 100 });
  await h.ready();
  const id = await h.startChat("first");
  assert.strictEqual(await h.until(() => h.postsOf("busy").some((message) => message.value === false), 5000), true);
  await h.settle(100);
  const rt = h.controller.runtimes.get(id);
  rt.steps = [{ stepNumber: 1, revertTargetNodeId: 1, forkTargetNodeId: 2 }];
  rt.stepsKnown = true;
  const before = h.agentSaw("_cognition.ai/revert/listSteps").length;

  h.send({ type: "send", text: "second" });

  assert.strictEqual(await h.until(() => h.agentSaw("_cognition.ai/revert/listSteps").length > before, 5000), true);
  await h.dispose();
});

test("a busy handover replays retained terminal output", posixOnly, async () => {
  const from = createChat({ promptDelay: 60000 });
  await from.ready();
  const id = await from.startChat("keep the turn running");
  assert.strictEqual(await from.until(() => from.postsOf("busy").some((message) => message.value), 5000), true);
  const rt = from.controller.runtimes.get(id);
  const terminalId = rt.terminals.create({ sessionId: id, command: "printf saved" }).terminalId;
  await rt.terminals.waitForExit(terminalId);
  const transfer = from.controller.exportRuntime(id);
  const to = createChat();
  await to.ready();

  await to.controller.importRuntime(transfer);

  assert.ok(to.postsOf("terminalOutput").some((message) => message.terminalId === terminalId && /saved/.test(message.output)));
  await to.dispose();
  await from.dispose();
});

test("a watched list update follows an in-flight session refresh", posixOnly, async () => {
  const h = createChat();
  await h.ready();
  assert.strictEqual(await h.until(() => h.postsOf("ready").length > 0, 5000), true);
  h.controller.listVisible = true;
  let resolve;
  let calls = 0;
  h.controller.listOverProtocol = () => {
    calls++;
    if (calls === 1) {
      return new Promise((done) => { resolve = done; });
    }
    return Promise.resolve({ sessions: [], prunedIds: [] });
  };

  const initial = h.controller.refreshSessions(true);
  await h.settle(25);
  h.controller.relistIfWatched();
  resolve({ sessions: [], prunedIds: [] });
  await initial;

  assert.strictEqual(await h.until(() => calls === 2, 1000), true);
  await h.dispose();
});

test("a model pick waits for a stored session to finish loading", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  let release;
  try {
    await h.ready();
    const baseline = await h.startChat("baseline config");
    const configOptions = JSON.parse(JSON.stringify(h.controller.runtimes.get(baseline).configOptions));
    h.store.add("stored", h.cwd);

    let loadedRuntime;
    const blocked = new Promise((resolve) => { release = resolve; });
    h.controller.loadWithTakeover = async (rt) => {
      loadedRuntime = rt;
      await blocked;
      return { configOptions };
    };

    h.send({ type: "loadSession", id: "stored" });
    assert.ok(await h.until(() => !!loadedRuntime), "the full session load entered");
    assert.strictEqual(loadedRuntime.replaying, true, "the stored session is still replaying");

    const calls = [];
    const original = loadedRuntime.client.setConfigOption.bind(loadedRuntime.client);
    loadedRuntime.client.setConfigOption = async (sessionId, configId, value) => {
      calls.push([configId, value]);
      return original(sessionId, configId, value);
    };
    h.send({
      type: "setConfigOption",
      configId: "thought_level",
      value: "low",
      model: "fusion-claude-opus-5-high-sidekick-swe-2-medium",
      requestId: 51
    });
    await Promise.resolve();
    await Promise.resolve();
    assert.deepStrictEqual(calls, [], "the config RPC waits for the full session load");

    release();
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").some((message) => message.requestId === 51)), "the request is acknowledged after loading");
    const ack = h.postsOf("modelConfigApplied").find((message) => message.requestId === 51);
    assert.strictEqual(loadedRuntime.configOptions.find((option) => option.id === "thought_level").currentValue, "low");
    assert.strictEqual(ack.thoughtLevel, "low", "the acknowledgement keeps the user's effort instead of the loaded high effort");
  } finally {
    release?.();
    await h.dispose();
  }
});

test("model picks serialize and coalesce to the latest full selection", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => (globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback),
    update: async (key, value) => { writes.push([key, value]); }
  });
  let release;
  try {
    await h.ready();
    const id = await h.startChat("serialize picks");
    const rt = h.controller.runtimes.get(id);
    const original = rt.client.setConfigOption.bind(rt.client);
    const calls = [];
    let inFlight = 0;
    let maxInFlight = 0;
    const blocked = new Promise((resolve) => { release = resolve; });
    rt.client.setConfigOption = async (sessionId, configId, value) => {
      calls.push([configId, value]);
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      try {
        if (calls.length === 1) await blocked;
        return await original(sessionId, configId, value);
      } finally {
        inFlight -= 1;
      }
    };

    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 1 });
    await h.until(() => calls.length === 1);
    h.send({ type: "setConfigOption", configId: "thought_level", value: "high", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 2 });
    h.send({ type: "setFusionModel", model: "fusion-gpt-6-astra-high-sidekick-swe-2-high", thoughtLevel: "low", requestId: 3 });
    h.send({ type: "setConfigOption", configId: "thought_level", value: "high", model: "fusion-gpt-6-astra-high-sidekick-swe-2-high", requestId: 4 });

    const intermediate = JSON.parse(JSON.stringify(rt.configOptions));
    intermediate.find((option) => option.id === "thought_level").currentValue = "low";
    rt.client.emit("update", { sessionId: id, update: { sessionUpdate: "config_option_update", configOptions: intermediate } });
    await h.settle(50);
    assert.notStrictEqual(h.last("options")?.currentThoughtLevel, "low", "an intermediate notification cannot paint over a pending pick");

    release();
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "exactly one acknowledgement arrives");

    assert.strictEqual(maxInFlight, 1, "the agent never saw two config calls at once");
    assert.deepStrictEqual(calls, [
      ["thought_level", "low"],
      ["model", "fusion-gpt-6-astra-high-sidekick-swe-2-high"],
      ["thought_level", "high"]
    ], "queued intermediate states are skipped and the final pair and effort land once");
    const ack = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(ack.requestId, 4, "only the latest request is acknowledged");
    assert.strictEqual(ack.model, "fusion-gpt-6-astra-high-sidekick-swe-2-high");
    assert.strictEqual(ack.thoughtLevel, "high");
    assert.strictEqual(h.last("options").currentModel, "fusion-gpt-6-astra-high-sidekick-swe-2-high");
    assert.strictEqual(h.last("options").currentThoughtLevel, "high");
    assert.ok(writes.some(([key, value]) => key === "defaultThoughtLevel" && value === "high"), "the applied effort is persisted");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    release?.();
    await h.dispose();
  }
});

test("a superseded model change still leaves its confirmed state recorded", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => (globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback),
    update: async (key, value) => { writes.push([key, value]); }
  });
  let release;
  const uidA = "fusion-claude-opus-5-high-sidekick-swe-2-medium";
  const uidB = "fusion-gpt-6-astra-high-sidekick-swe-2-high";
  try {
    await h.ready();
    const id = await h.startChat("record the superseded reply");
    const rt = h.controller.runtimes.get(id);
    const original = rt.client.setConfigOption.bind(rt.client);
    const calls = [];
    const blocked = new Promise((resolve) => { release = resolve; });
    rt.client.setConfigOption = async (sessionId, configId, value) => {
      calls.push([configId, value]);
      if (calls.length === 1) await blocked;
      return original(sessionId, configId, value);
    };

    h.send({ type: "setFusionModel", model: uidB, thoughtLevel: "low", requestId: 21 });
    assert.ok(await h.until(() => calls.length === 1), "the model change is in flight");
    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: uidA, requestId: 22 });
    release();
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "only the latest request is acknowledged");

    assert.deepStrictEqual(calls, [
      ["model", uidB],
      ["model", uidA],
      ["thought_level", "low"]
    ], "the queued pick still sends its own model, because B was recorded when its call finished");
    const ack = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(ack.requestId, 22);
    assert.strictEqual(ack.model, uidA);
    assert.strictEqual(ack.thoughtLevel, "low");
    assert.strictEqual(rt.configOptions.find((option) => option.id === "model").currentValue, uidA, "the runtime knows its real model");
    assert.strictEqual(rt.model, uidA, "the runtime model agrees with the confirmed option");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    release?.();
    await h.dispose();
  }
});

test("a failed effort still acknowledges the model that did apply", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => (globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback),
    update: async (key, value) => { writes.push([key, value]); }
  });
  const uidA = "fusion-claude-opus-5-high-sidekick-swe-2-medium";
  const uidB = "fusion-gpt-6-astra-high-sidekick-swe-2-high";
  try {
    await h.ready();
    const id = await h.startChat("fail the effort only");
    const rt = h.controller.runtimes.get(id);
    const original = rt.client.setConfigOption.bind(rt.client);
    let failEffort = true;
    rt.client.setConfigOption = async (sessionId, configId, value) => {
      if (configId === "thought_level" && failEffort) throw new Error("effort refused");
      return original(sessionId, configId, value);
    };

    h.send({ type: "setFusionModel", model: uidB, thoughtLevel: "low", requestId: 31 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "the failed request is still acknowledged");
    const ack = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(ack.model, uidB, "the model change that did land is reported");
    assert.strictEqual(ack.thoughtLevel, "high", "the effort the agent actually kept is reported");
    assert.strictEqual(rt.model, uidB, "the runtime records the applied model");
    assert.ok(vscode.window.shown.warning.length >= 1, "the failure is surfaced as a warning");
    assert.ok(!writes.some(([key]) => key === "defaultModel" || key === "defaultThoughtLevel"), "nothing about the failed pick is persisted");

    failEffort = false;
    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: uidA, requestId: 32 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 2), "a retry after the failure works");
    const retry = h.postsOf("modelConfigApplied")[1];
    assert.strictEqual(retry.model, uidA);
    assert.strictEqual(retry.thoughtLevel, "low");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    await h.dispose();
  }
});

test("an effort the agent does not offer is neither sent nor persisted", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => (globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback),
    update: async (key, value) => { writes.push([key, value]); }
  });
  try {
    await h.ready();
    await h.startChat("reject unknown effort");
    h.send({ type: "setConfigOption", configId: "thought_level", value: "xhigh", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 41 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "the request is still acknowledged");
    assert.ok(!h.agentSaw("session/set_config_option").some((request) => request.params.configId === "thought_level"), "no call goes out for a value the agent does not list");
    const ack = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(ack.thoughtLevel, "high", "the agent's real effort is acknowledged");
    assert.ok(writes.some(([key, value]) => key === "defaultThoughtLevel" && value === "high"), "the confirmed effort is persisted");
    assert.ok(!writes.some(([key, value]) => value === "xhigh"), "the unsupported value is never persisted");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    await h.dispose();
  }
});

test("a rejected model change restores the confirmed effort and still acknowledges", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => (globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback),
    update: async (key, value) => { writes.push([key, value]); }
  });
  try {
    await h.ready();
    const id = await h.startChat("reject the pick");
    const rt = h.controller.runtimes.get(id);
    const original = rt.client.setConfigOption.bind(rt.client);
    rt.client.setConfigOption = async (sessionId, configId, value) => {
      if (configId === "thought_level") throw new Error("thought_level refused");
      return original(sessionId, configId, value);
    };

    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 7 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "the failed request is still acknowledged");
    const ack = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(ack.requestId, 7);
    assert.strictEqual(ack.thoughtLevel, "high", "the acknowledgement reports the confirmed effort, not the rejected pick");
    assert.ok(vscode.window.shown.warning.length >= 1, "the failure is surfaced as a warning");
    assert.strictEqual(h.last("options").currentThoughtLevel, "high", "the pickers are repainted with confirmed state");
    assert.ok(!writes.some(([key, value]) => key === "defaultThoughtLevel" && value === "low"), "the rejected effort is not persisted");

    rt.client.setConfigOption = original;
    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 8 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 2), "a retry after the failure works");
    assert.strictEqual(h.last("options").currentThoughtLevel, "low");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    await h.dispose();
  }
});

test("a model change cannot paint the session that replaced the one it was for", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  let release;
  try {
    await h.ready();
    const first = await h.startChat("first chat");
    const rtA = h.controller.runtimes.get(first);
    const original = rtA.client.setConfigOption.bind(rtA.client);
    let entered = false;
    const blocked = new Promise((resolve) => { release = resolve; });
    rtA.client.setConfigOption = async (sessionId, configId, value) => {
      entered = true;
      await blocked;
      return original(sessionId, configId, value);
    };
    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 9 });
    assert.ok(await h.until(() => entered), "the first session's change is in flight");

    const second = await h.startChat("second chat");
    h.send({ type: "activateSession", id: second });
    assert.ok(await h.until(() => h.activeId() === second), "the second session is on screen");

    const rtB = h.controller.runtimes.get(second);
    const bOptions = JSON.parse(JSON.stringify(rtB.configOptions));
    bOptions.find((option) => option.id === "model").currentValue = "fusion-gpt-6-astra-high-sidekick-swe-2-high";
    bOptions.find((option) => option.id === "thought_level").currentValue = "low";
    rtB.client.emit("update", { sessionId: second, update: { sessionUpdate: "config_option_update", configOptions: bOptions } });
    assert.ok(await h.until(() => h.last("options")?.currentModel === "fusion-gpt-6-astra-high-sidekick-swe-2-high" && h.last("options")?.currentThoughtLevel === "low"), "the active session's own update is not held back by another session's pending change");

    const posts = (type) => h.postsOf(type).length;
    const beforeRelease = { options: posts("options"), model: posts("model"), ack: posts("modelConfigApplied") };
    release();
    await h.controller.modelConfigTail;
    await h.settle(50);
    assert.strictEqual(posts("options"), beforeRelease.options, "the released request repaints nothing");
    assert.strictEqual(posts("model"), beforeRelease.model, "no model message lands either");
    assert.strictEqual(posts("modelConfigApplied"), beforeRelease.ack, "a request whose session went away is not acknowledged");
    assert.strictEqual(h.last("options").currentModel, "fusion-gpt-6-astra-high-sidekick-swe-2-high", "the new session's own state stays on screen");
  } finally {
    release?.();
    await h.dispose();
  }
});

test("a pick made before any session persists its defaults and applies once one starts", posixOnly, async () => {
  const vscode = globalThis.__dvVscode;
  const writes = [];
  const realGetConfiguration = vscode.workspace.getConfiguration;
  vscode.workspace.getConfiguration = () => ({
    get: (key, fallback) => {
      const written = [...writes].reverse().find(([k]) => k === key);
      if (written) return written[1];
      return globalThis.__dvConfig && key in globalThis.__dvConfig ? globalThis.__dvConfig[key] : fallback;
    },
    update: async (key, value) => { writes.push([key, value]); }
  });
  const h = createChat({ newDelay: 300, config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  try {
    await h.ready();

    h.send({ type: "setFusionModel", model: "fusion-gpt-6-astra-high-sidekick-swe-2-high", thoughtLevel: "low", requestId: 11 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "the pre session pick is acknowledged");
    const first = h.postsOf("modelConfigApplied")[0];
    assert.strictEqual(first.requestId, 11);
    assert.strictEqual(first.model, "fusion-gpt-6-astra-high-sidekick-swe-2-high");
    assert.strictEqual(first.thoughtLevel, "low");
    assert.ok(writes.some(([key, value]) => key === "defaultModel" && value === "fusion-gpt-6-astra-high-sidekick-swe-2-high"), "the model default is persisted");
    assert.ok(writes.some(([key, value]) => key === "defaultThoughtLevel" && value === "low"), "the effort default is persisted");
    assert.strictEqual(h.agentSaw("session/set_config_option").length, 0, "no agent existed to call");

    h.send({ type: "send", text: "start a chat", newSession: true });
    await h.until(() => h.agentSaw("session/new").length === 1);
    h.send({ type: "setConfigOption", configId: "thought_level", value: "low", model: "fusion-claude-opus-5-high-sidekick-swe-2-medium", requestId: 12 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 2), "the queued pick waits for the starting session");
    const second = h.postsOf("modelConfigApplied")[1];
    assert.strictEqual(second.requestId, 12);
    assert.strictEqual(second.model, "fusion-claude-opus-5-high-sidekick-swe-2-medium");
    assert.strictEqual(second.thoughtLevel, "low");
    assert.ok(h.agentSaw("session/set_config_option").some((request) => request.params.configId === "thought_level" && request.params.value === "low"), "the new session's agent gets the effort");
  } finally {
    vscode.workspace.getConfiguration = realGetConfiguration;
    await h.dispose();
  }
});

test("catalogue uids are translated to the values the session's model option accepts", posixOnly, async () => {
  const h = createChat({ config: { defaultMode: "", defaultModel: "", defaultThoughtLevel: "" } });
  try {
    await h.ready();
    const id = await h.startChat("translate the pick");
    const rt = h.controller.runtimes.get(id);
    // The real session offers one uid per family, not the per effort uids the
    // catalogue (and so the picker) is built from. Every reply has to report it:
    // publishOptions replaces the snapshot each time.
    const widen = (options) => {
      options.find((option) => option.id === "model").options.push({
        group: "gpt-5.6-sol", name: "GPT-5.6 Sol", options: [{ value: "gpt-5-6-sol-medium", name: "GPT-5.6 Sol" }]
      });
      options.find((option) => option.id === "thought_level").options.push({ value: "none", name: "None" }, { value: "medium", name: "Medium" });
      return options;
    };
    widen(rt.configOptions);
    const original = rt.client.setConfigOption.bind(rt.client);
    rt.client.setConfigOption = async (sessionId, configId, value) => {
      const res = await original(sessionId, configId, value);
      if (res?.configOptions) widen(res.configOptions);
      return res;
    };

    h.send({ type: "setModel", model: "gpt-5-6-sol-none", requestId: 51 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 1), "the normal model pick is acknowledged");
    assert.deepStrictEqual(
      h.agentSaw("session/set_config_option").map((item) => [item.params.configId, item.params.value]),
      [["model", "gpt-5-6-sol-medium"], ["thought_level", "none"]],
      "the effort variant uid becomes the accepted family uid plus a thought_level call"
    );

    const before = h.agentSaw("session/set_config_option").length;
    h.send({ type: "setFusionModel", model: "fusion-claude-opus-5-medium-sidekick-swe-2-medium", thoughtLevel: "medium", requestId: 52 });
    assert.ok(await h.until(() => h.postsOf("modelConfigApplied").length === 2), "the Fusion pick is acknowledged");
    assert.deepStrictEqual(
      h.agentSaw("session/set_config_option").slice(before).map((item) => [item.params.configId, item.params.value]),
      [["model", "fusion-claude-opus-5-high-sidekick-swe-2-medium"], ["thought_level", "medium"]],
      "a Fusion pair the session does not list maps to its accepted uid, with the lead effort going to thought_level"
    );
  } finally {
    await h.dispose();
  }
});

test.after(() => cleanup());
