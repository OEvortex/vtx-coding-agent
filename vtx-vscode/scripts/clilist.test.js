// Tests for the Vtx CLI wrappers (src/settings/vtxConfigCli.ts): `vtx config
// list|set` for ~/.vtx/config.yml, `vtx --dump-config` for plugin rows, and
// `vtx plugin enable|disable` for the profile patch layer.
//
// The risk is the same as any parser fed by another program's output: anything
// accepted becomes a row the user can act on, and a row that is really an error
// message offers to toggle something that does not exist.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "vtx-clilist-"));

const outfile = path.join(TMP, "vtxConfigCli.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/settings/vtxConfigCli.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error",
  alias: { vscode: path.join(__dirname, "vtx-stub.js") }
});
const { listPlugins, configList, configSet, pluginVerb } = require(outfile);

// A stand-in `vtx` that prints what it is told and exits how it is told.
function fakeCli(name, stdout, exitCode = 0, stderr = "") {
  const js = path.join(TMP, name + ".js");
  fs.writeFileSync(js, `process.stdout.write(${JSON.stringify(stdout)});process.stderr.write(${JSON.stringify(stderr)});process.exit(${exitCode});`);
  const shim = path.join(TMP, name + (process.platform === "win32" ? ".cmd" : ".sh"));
  if (process.platform === "win32") {
    fs.writeFileSync(shim, `@echo off\r\n"${process.execPath}" "${js}"\r\n`);
  } else {
    fs.writeFileSync(shim, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
    fs.chmodSync(shim, 0o755);
  }
  return { cliPath: shim };
}

test("the plugin list is what --dump-config listed, not whatever it printed", async () => {
  // `vtx --dump-config` prints `<id>\t[enabled|disabled]\t<name>` per row.
  const good = await listPlugins(fakeCli("plugins-ok",
    "hooks\t[enabled]\tvtx.plugins.builtins.hooks:PLUGIN\n"
    + "tool-grep\t[disabled]\tvtx.plugins.builtins.tool_grep:PLUGIN\n"));
  assert.deepStrictEqual(good.map((p) => p.name), ["hooks", "tool-grep"]);
  assert.strictEqual(good[0].disabled, false);
  assert.strictEqual(good[1].disabled, true);
  assert.strictEqual(good[0].description, "vtx.plugins.builtins.hooks:PLUGIN");

  // A CLI that failed. Every line here would have become a row with an enable
  // and a disable button, and toggling "Error:" is not something to offer.
  const failed = await listPlugins(fakeCli("plugins-err",
    "vtx plugin: unknown row 'x'; run `vtx --dump-config` to list them\n", 1));
  assert.deepStrictEqual(failed, [], "a failure lists nothing");

  const noise = await listPlugins(fakeCli("plugins-noise",
    "No extensions loaded.\nRun `vtx --help` for more.\n"));
  assert.deepStrictEqual(noise, [], "and nor is prose");
});

test("config list parses the JSON `vtx config list` prints", async () => {
  const listed = await configList(fakeCli("config-ok",
    JSON.stringify({ llm: { default_model: "auto" }, permissions: { mode: "prompt" } }, null, 2)));
  assert.deepStrictEqual(listed, { llm: { default_model: "auto" }, permissions: { mode: "prompt" } });

  assert.strictEqual(await configList(fakeCli("config-bad", "not json\n")), undefined);
  assert.strictEqual(await configList(fakeCli("config-fail", "boom\n", 1)), undefined);
});

test("config set and plugin verbs report the CLI result", async () => {
  const ok = fakeCli("verbs-ok", "set llm.default_model\n");
  assert.deepStrictEqual(await configSet(ok, "llm.default_model", "auto"), { ok: true, err: "set llm.default_model\n" });
  assert.deepStrictEqual(await pluginVerb(ok, "disable", "tool-grep"), { ok: true, err: "set llm.default_model\n" });
  assert.deepStrictEqual(await pluginVerb(ok, "enable"), { ok: false, err: "missing row name" });

  const bad = fakeCli("verbs-err", "", 1, "vtx plugin: unknown row\n");
  const failed = await pluginVerb(bad, "disable", "nope");
  assert.strictEqual(failed.ok, false);
  assert.match(failed.err, /unknown row/);
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
