// Tests for src/settings/configService.ts, the only place the extension writes
// a Vtx file itself. User settings in ~/.vtx/config.yml are YAML and go through
// `vtx config` (vtxConfigCli), so what is left here is Vtx's JSON surface:
// mcp.json server edits and the atomic file writer they share.
//
// It touches nothing but fs/os/path, so it loads outside VS Code with no stub.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "vtx-config-"));

const outfile = path.join(TMP, "configService.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/settings/configService.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error"
});
const {
  writeMcpServer, userMcpConfigPath, projectMcpConfigPath,
  userHooksConfigPath, projectHooksConfigPath, readConfig, writeFileAtomic
} = require(outfile);

const serversIn = (file) => readConfig(file).mcpServers || {};

test("MCP paths are the ones Vtx reads", () => {
  assert.strictEqual(path.basename(userMcpConfigPath()), "mcp.json");
  assert.ok(userMcpConfigPath().endsWith(path.join(".vtx", "mcp.json")), userMcpConfigPath());
  assert.strictEqual(projectMcpConfigPath("/repo"), path.join("/repo", ".vtx", "mcp.json"));
  assert.strictEqual(userHooksConfigPath(), path.join(path.dirname(userMcpConfigPath()), "hooks.yml"));
  assert.strictEqual(projectHooksConfigPath("/repo"), path.join("/repo", ".vtx", "hooks.yml"));
});

test("a server can be added, turned off and removed in mcp.json", () => {
  const file = path.join(TMP, "ws", "mcp.json");

  // The file need not exist yet.
  writeMcpServer(file, "issue-tracker", { url: "https://example.com/mcp" });
  assert.deepStrictEqual(serversIn(file), { "issue-tracker": { url: "https://example.com/mcp" } });

  writeMcpServer(file, "fetch", { command: "docker", args: ["run", "-i", "mcp/fetch"] });
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["issue-tracker", "fetch"]);

  // Turning one off keeps its definition, so it can be turned back on. That is
  // the `enabled: false` field src/vtx/mcp/config.py validates.
  const off = { ...serversIn(file)["issue-tracker"], enabled: false };
  writeMcpServer(file, "issue-tracker", off);
  assert.deepStrictEqual(serversIn(file)["issue-tracker"], { url: "https://example.com/mcp", enabled: false });

  writeMcpServer(file, "issue-tracker", null);
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["fetch"], "removing one leaves the rest");
});

test("everything else in the file survives the edit", () => {
  const file = path.join(TMP, "other", "mcp.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({
    unrelated: { keep: true },
    mcpServers: { github: { url: "https://example.com/mcp", headers: { Authorization: "Bearer x" } } }
  }, null, 2));

  writeMcpServer(file, "github", { url: "https://example.com/mcp", headers: { Authorization: "Bearer x" }, timeout: 30 });
  const after = readConfig(file);
  assert.deepStrictEqual(after.unrelated, { keep: true }, "a key we do not understand is not dropped");
  assert.strictEqual((after.mcpServers.github.headers || {}).Authorization, "Bearer x", "nor a field of the server itself");

  writeMcpServer(file, "brand-new", { command: "npx", args: ["-y", "server"] });
  assert.deepStrictEqual(readConfig(file).unrelated, { keep: true });
});

test("a config is never left half written", () => {
  // A plain write truncates in place, so a crash or a full disk between the
  // truncate and the last byte leaves an empty or partial config, which is worse
  // than the edit not happening at all.
  const file = path.join(TMP, "atomic", "mcp.json");
  writeFileAtomic(file, '{"mcpServers":{}}\n');
  assert.deepStrictEqual(readConfig(file).mcpServers, {});

  writeFileAtomic(file, '{"mcpServers":{"a":{}}}\n');
  assert.deepStrictEqual(readConfig(file).mcpServers, { a: {} });
  assert.deepStrictEqual(
    fs.readdirSync(path.dirname(file)),
    ["mcp.json"],
    "no temporary file is left behind"
  );

  assert.throws(() => writeFileAtomic(path.join(file, "not-a-dir", "x.json"), "{}"));
  assert.deepStrictEqual(readConfig(file).mcpServers, { a: {} }, "and the good file is untouched");
});

test("a file that does not parse is refused, not overwritten", () => {
  // readConfig answers {} for anything unparseable, which is right for showing
  // a file and ruinous for writing one: the write would replace the user's MCP
  // servers with a document holding nothing but the new one.
  const file = path.join(TMP, "broken", "mcp.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const broken = '{ "mcpServers": { "a": { "url": "x" } }   <- someone was editing this';
  fs.writeFileSync(file, broken);

  assert.deepStrictEqual(readConfig(file), {});
  assert.throws(() => writeMcpServer(file, "new", { url: "https://example.com" }), /not valid JSON/);
  assert.strictEqual(fs.readFileSync(file, "utf8"), broken, "their file is untouched");
});

test("a file with comments keeps parsing, and can still be written", () => {
  const file = path.join(TMP, "jsonc", "mcp.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, '{\n  // the fetch server\n  "mcpServers": { "fetch": { "command": "docker" } }\n}');
  writeMcpServer(file, "other", { url: "https://example.com/mcp" });
  assert.deepStrictEqual(Object.keys(serversIn(file)), ["fetch", "other"]);
});

test("a comma inside a value is part of the value, not a trailing comma", () => {
  // A trailing comma before } or ] is legal in the config and not in JSON, so it
  // has to go before parsing. Stripping it with a pass over the finished text
  // cannot tell a comma in a string from a real one, and a command that happens
  // to contain one was quietly edited: the read returned a different value than
  // the file held, and the next write saved it back.
  const file = path.join(TMP, "commas", "mcp.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const command = "jq '{name, }' out.json";
  fs.writeFileSync(file, JSON.stringify({ mcpServers: { tool: { command } } }, null, 2));

  assert.strictEqual(readConfig(file).mcpServers.tool.command, command, "read back as written");

  fs.writeFileSync(file, `{\n  "note": "ends with a comma, ]",\n  "list": [1, 2,],\n}`);
  const jsonc = readConfig(file);
  assert.strictEqual(jsonc.note, "ends with a comma, ]");
  assert.deepStrictEqual(jsonc.list, [1, 2]);
});

test("writing a config keeps the file it was, link and permissions and all", { skip: process.platform === "win32" }, () => {
  // These files are commonly a symlink into a dotfiles repository, and renaming
  // over a link replaces the link itself: the repository copy is left behind,
  // still there, no longer the file being read. A config can also hold MCP
  // secrets in its `env`, so 0600 is a deliberate choice a write must not undo.
  const store = path.join(TMP, "dotfiles");
  const home = path.join(TMP, "home", ".vtx");
  fs.mkdirSync(store, { recursive: true });
  fs.mkdirSync(home, { recursive: true });
  const real = path.join(store, "mcp.json");
  const link = path.join(home, "mcp.json");
  fs.writeFileSync(real, '{"mcpServers":{}}\n');
  fs.chmodSync(real, 0o600);
  fs.symlinkSync(real, link);

  writeFileAtomic(link, '{"mcpServers":{"a":{}}}\n');

  assert.ok(fs.lstatSync(link).isSymbolicLink(), "the link is still a link");
  assert.deepStrictEqual(readConfig(real).mcpServers, { a: {} }, "and the file it points at is what changed");
  assert.strictEqual(fs.statSync(real).mode & 0o777, 0o600, "with the permissions it had");
  assert.deepStrictEqual(fs.readdirSync(store), ["mcp.json"], "no scratch file left beside it");
});

test("a link whose target is not there yet is still followed, not replaced", { skip: process.platform === "win32" }, () => {
  // A dotfiles link can point at a file that is not checked out yet, and asking the
  // filesystem to resolve the whole path fails for that, which read as "nothing there"
  // and replaced the link with a regular file: the same silent detachment as before.
  const store = path.join(TMP, "dotfiles-pending");
  const home = path.join(TMP, "home-pending", ".vtx");
  fs.mkdirSync(store, { recursive: true });
  fs.mkdirSync(home, { recursive: true });
  const real = path.join(store, "mcp.json");
  const link = path.join(home, "mcp.json");
  fs.symlinkSync(real, link);
  assert.ok(!fs.existsSync(real), "the target is genuinely missing");

  writeFileAtomic(link, '{"mcpServers":{"a":{}}}\n');

  assert.ok(fs.lstatSync(link).isSymbolicLink(), "the link is still a link");
  assert.deepStrictEqual(readConfig(real).mcpServers, { a: {} }, "and the file it points at was created");
});

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));
