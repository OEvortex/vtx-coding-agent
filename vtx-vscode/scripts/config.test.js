// Tests for src/settings/configService.ts, the only place the extension writes
// a config file itself. Everything else goes through the `devin` CLI, which owns
// its own files; this is what edits the ones it merely imports, so a mistake here
// corrupts another tool's configuration rather than ours.
//
// It touches nothing but fs/os/path, so it loads outside VS Code with no stub.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-config-"));

const outfile = path.join(TMP, "configService.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/settings/configService.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error"
});
const { writeMcpServer, windsurfMcpConfigPath, readConfig, setConfigPath, writeFileAtomic } = require(outfile);

const serversIn = (file) => readConfig(file).mcpServers || {};

test("Windsurf's config is found where Windsurf keeps it", () => {
  const p = windsurfMcpConfigPath();
  assert.strictEqual(path.basename(p), "mcp_config.json");
  assert.ok(p.endsWith(path.join(".codeium", "windsurf", "mcp_config.json")), p);
  assert.ok(p.startsWith(os.homedir()), "under the user's home, not the workspace");
});

test("a server can be added, turned off and removed in another tool's file", () => {
  const file = path.join(TMP, "windsurf", "mcp_config.json");

  // The file need not exist yet: Windsurf may never have been opened.
  writeMcpServer(file, "godot-ai", { serverUrl: "http://127.0.0.1:8000/mcp" });
  assert.deepStrictEqual(serversIn(file), { "godot-ai": { serverUrl: "http://127.0.0.1:8000/mcp" } });

  // Adding a second leaves the first alone.
  writeMcpServer(file, "fetch", { command: "docker", args: ["run", "-i", "--rm", "mcp/fetch"] });
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["godot-ai", "fetch"]);

  // Turning one off keeps its definition, so it can be turned back on.
  const off = { ...serversIn(file)["godot-ai"], disabled: true };
  writeMcpServer(file, "godot-ai", off);
  assert.deepStrictEqual(serversIn(file)["godot-ai"], { serverUrl: "http://127.0.0.1:8000/mcp", disabled: true });

  writeMcpServer(file, "godot-ai", null);
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["fetch"], "removing one leaves the rest");
});

test("a config is never left half written", () => {
  // A plain write truncates in place, so a crash or a full disk between the
  // truncate and the last byte leaves an empty or partial config, which is worse
  // than the edit not happening at all.
  const file = path.join(TMP, "atomic", "config.json");
  writeFileAtomic(file, '{"model":"first"}\n');
  assert.strictEqual(readConfig(file).model, "first");

  // The file is only ever replaced whole, and the scratch file it goes through does
  // not survive, in either direction.
  writeFileAtomic(file, '{"model":"second"}\n');
  assert.strictEqual(readConfig(file).model, "second");
  assert.deepStrictEqual(
    fs.readdirSync(path.dirname(file)),
    ["config.json"],
    "no temporary file is left behind"
  );

  // A failure leaves the previous contents intact rather than a truncated file.
  assert.throws(() => writeFileAtomic(path.join(file, "not-a-dir", "x.json"), "{}"));
  assert.strictEqual(readConfig(file).model, "second", "and the good file is untouched");
});

test("a config we cannot parse is left alone, not replaced by the one value edited", () => {
  // Every write is a read, modify and write back, and a file that will not parse
  // reads as {}. Writing that would replace the user's model, permissions, hooks
  // and MCP servers with a document holding nothing but this one setting.
  const root = path.join(TMP, "broken-project");
  const file = path.join(root, ".vtx", "config.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const broken = '{\n  "model": "claude-sonnet-4",\n  "permissions": { "allow": ["Exec(ls)"] },\n';
  fs.writeFileSync(file, broken, "utf8");

  assert.throws(
    () => setConfigPath("project", "attribution", false, root),
    /not valid JSON/,
    "it has to refuse, and say why"
  );
  assert.strictEqual(fs.readFileSync(file, "utf8"), broken, "and the file is untouched, byte for byte");

  // Once it parses, the same edit goes through and keeps everything else.
  fs.writeFileSync(file, broken + "}\n", "utf8");
  setConfigPath("project", "attribution", false, root);
  const after = readConfig(file);
  assert.strictEqual(after.attribution, false, "the edit lands");
  assert.strictEqual(after.model, "claude-sonnet-4", "and the rest of the config is still there");
  assert.deepStrictEqual(after.permissions, { allow: ["Exec(ls)"] });
});

test("everything else in the file survives the edit", () => {
  const file = path.join(TMP, "other", "mcp_config.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // A real config carries keys we know nothing about, and a server we edit may
  // carry its own: Windsurf stamps a `registry` on the ones it installed.
  fs.writeFileSync(file, JSON.stringify({
    someWindsurfSetting: { keep: true },
    mcpServers: { "devin/fetch": { command: "docker", registry: "devin/fetch" } }
  }, null, 2));

  writeMcpServer(file, "devin/fetch", { command: "docker", registry: "devin/fetch", disabled: true });
  const after = readConfig(file);
  assert.deepStrictEqual(after.someWindsurfSetting, { keep: true }, "a setting we do not understand is not dropped");
  assert.strictEqual(after.mcpServers["devin/fetch"].registry, "devin/fetch", "nor a field of the server itself");

  writeMcpServer(file, "brand-new", { serverUrl: "https://example.com/mcp" });
  assert.deepStrictEqual(readConfig(file).someWindsurfSetting, { keep: true });
});

test("a file that does not parse is refused, not overwritten", () => {
  const file = path.join(TMP, "broken", "mcp_config.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const broken = '{ "mcpServers": { "a": { "url": "x" } }   <- someone was editing this';
  fs.writeFileSync(file, broken);

  // Reading answers {} for anything unparseable, which is right for showing a
  // file and ruinous for writing one: the write would replace another tool's
  // config with a document holding nothing but the new server.
  assert.deepStrictEqual(readConfig(file), {});
  assert.throws(() => writeMcpServer(file, "new", { serverUrl: "https://example.com" }), /not valid JSON/);
  assert.strictEqual(fs.readFileSync(file, "utf8"), broken, "their file is untouched");
});

test("a file with comments keeps parsing, and can still be written", () => {
  const file = path.join(TMP, "jsonc", "mcp_config.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{\n  // the fetch server\n  "mcpServers": { "fetch": { "command": "docker" } }\n}');
  writeMcpServer(file, "other", { serverUrl: "https://example.com/mcp" });
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["fetch", "other"]);
});

test("writing a config keeps the file it was, link and permissions and all", { skip: process.platform === "win32" }, () => {
  // These files are commonly a symlink into a dotfiles repository, and renaming
  // over a link replaces the link itself: the repository copy is left behind,
  // still there, no longer the file being read. A config can also hold MCP
  // secrets in its `env`, so 0600 is a deliberate choice a write must not undo.
  const store = path.join(TMP, "dotfiles");
  const home = path.join(TMP, "home", ".config", "devin");
  fs.mkdirSync(store, { recursive: true });
  fs.mkdirSync(home, { recursive: true });
  const real = path.join(store, "config.json");
  const link = path.join(home, "config.json");
  fs.writeFileSync(real, '{"model":"first"}\n');
  fs.chmodSync(real, 0o600);
  fs.symlinkSync(real, link);

  writeFileAtomic(link, '{"model":"second"}\n');

  assert.ok(fs.lstatSync(link).isSymbolicLink(), "the link is still a link");
  assert.strictEqual(readConfig(real).model, "second", "and the file it points at is what changed");
  assert.strictEqual(fs.statSync(real).mode & 0o777, 0o600, "with the permissions it had");
  assert.deepStrictEqual(fs.readdirSync(store), ["config.json"], "no scratch file left beside it");
});

test("a link whose target is not there yet is still followed, not replaced", { skip: process.platform === "win32" }, () => {
  // A dotfiles link can point at a file that is not checked out yet, and asking the
  // filesystem to resolve the whole path fails for that, which read as "nothing there"
  // and replaced the link with a regular file: the same silent detachment as before.
  const store = path.join(TMP, "dotfiles-pending");
  const home = path.join(TMP, "home-pending", ".config", "devin");
  fs.mkdirSync(store, { recursive: true });
  fs.mkdirSync(home, { recursive: true });
  const real = path.join(store, "config.json");
  const link = path.join(home, "config.json");
  fs.symlinkSync(real, link);
  assert.ok(!fs.existsSync(real), "the target is genuinely missing");

  writeFileAtomic(link, '{"model":"written-through"}\n');

  assert.ok(fs.lstatSync(link).isSymbolicLink(), "the link is still a link");
  assert.strictEqual(readConfig(real).model, "written-through", "and the file it points at was created");
});

test("a comma inside a value is part of the value, not a trailing comma", () => {
  // A trailing comma before } or ] is legal in the config and not in JSON, so it
  // has to go before parsing. Stripping it with a pass over the finished text
  // cannot tell a comma in a string from a real one, and a hook command or a deny
  // rule that happens to contain one was quietly edited: the read returned a
  // different value than the file held, and the next write saved it back.
  const file = path.join(TMP, "commas", "config.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const command = "jq '{name, }' out.json";
  fs.writeFileSync(file, JSON.stringify({ hooks: { PreToolUse: [{ command }] }, model: "sonnet" }, null, 2));

  assert.strictEqual(readConfig(file).hooks.PreToolUse[0].command, command, "read back as written");

  // And a real trailing comma is still removed, next to one that only looks like it.
  fs.writeFileSync(file, `{\n  "note": "ends with a comma, ]",\n  "list": [1, 2,],\n}`);
  const jsonc = readConfig(file);
  assert.strictEqual(jsonc.note, "ends with a comma, ]");
  assert.deepStrictEqual(jsonc.list, [1, 2]);
});

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));
