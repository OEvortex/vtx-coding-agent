// Tests for the CLI list parsers (src/settings/vtxConfigCli.ts), which turn the
// `devin` command's printed output into the rows the settings panel renders, with
// Update and Remove buttons beside them.
//
// The output is text meant for a human, so the risk is the parser being too
// generous: anything it accepts becomes a row the user can act on, and a row that
// is really an error message offers to uninstall something that does not exist.
// A fake `devin` on disk stands in for the real one, printing exactly what it
// prints (captured from `devin plugins list`).

const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-clilist-"));

const outfile = path.join(TMP, "vtxConfigCli.js");
esbuild.buildSync({
  entryPoints: [path.join(ROOT, "src/settings/vtxConfigCli.ts")],
  outfile,
  bundle: true,
  platform: "node",
  format: "cjs",
  logLevel: "error",
  alias: { vscode: path.join(__dirname, "vscode-stub.js") }
});
const { listPlugins, listSkills, isPlainCliName } = require(outfile);

// A stand-in `devin` that prints what it is told and exits how it is told.
function fakeCli(name, stdout, exitCode = 0) {
  const js = path.join(TMP, name + ".js");
  fs.writeFileSync(js, `process.stdout.write(${JSON.stringify(stdout)});process.exit(${exitCode});`);
  const shim = path.join(TMP, name + (process.platform === "win32" ? ".cmd" : ".sh"));
  if (process.platform === "win32") {
    fs.writeFileSync(shim, `@echo off\r\n"${process.execPath}" "${js}"\r\n`);
  } else {
    fs.writeFileSync(shim, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
    fs.chmodSync(shim, 0o755);
  }
  return { cliPath: shim };
}

test("the plugin list is what the CLI listed, not whatever it printed", async () => {
  // Real output, captured from `devin plugins list`.
  const good = await listPlugins(fakeCli("plugins-ok",
    "Installed plugins\n\n  \u2022 story-skills v0.3.1\n  \u2022 superpowers v6.3.0\n"));
  assert.deepStrictEqual(good.map((p) => p.name), ["story-skills", "superpowers"]);
  assert.strictEqual(good[0].description, "v0.3.1", "the version rides along as the detail");

  // A CLI that failed. Every line here would have become a plugin with an Update
  // and a Remove button, and removing "Error:" is not something to offer.
  const failed = await listPlugins(fakeCli("plugins-err",
    "Error: not authenticated. Run devin auth login.\n", 1));
  assert.deepStrictEqual(failed, [], "a failure lists nothing");

  // Exit code 0 but nothing that is a plugin row: a hint, a reworded header, a
  // wrapped line. None of them are plugins either.
  const noise = await listPlugins(fakeCli("plugins-noise",
    "Plugins (2 installed)\nRun `devin plugins add <source>` to install one.\nSee https://docs.devin.ai for more.\n"));
  assert.deepStrictEqual(noise, [], "and nor is prose");

  const none = await listPlugins(fakeCli("plugins-none", "No plugins installed\n"));
  assert.deepStrictEqual(none, [], "and an empty list is empty");
});

test("the skill list keeps its strict shape", async () => {
  // Same risk, and this parser was already strict. Kept honest here so it stays so.
  const skills = await listSkills(fakeCli("skills-ok",
    "Available Skills\n\n  /docx [user,model] (~/.config/devin/skills/docx) - Word documents\n"
    + "Error: something went wrong\n"));
  assert.deepStrictEqual(skills.map((s) => s.name), ["docx"], "only the row that is a skill");
  assert.strictEqual(skills[0].description, "Word documents");
});

test("a server name that is really a command is not typed into a shell", () => {
  // The name comes from a config file, which may belong to a cloned repository, and
  // the OAuth login types it into whichever shell the user has. Quoting for all of
  // them is not possible, so anything that is not a name is refused.
  for (const ok of ["godot-ai", "fetch", "my_server.v2", "@acme/tools", "a-b_c.1"]) {
    assert.strictEqual(isPlainCliName(ok), true, ok + " is an ordinary name");
  }
  for (const bad of [
    'a" & whoami & "b',
    "a; rm -rf ~",
    "a`whoami`",
    "a$(whoami)",
    "a|b",
    "a b",
    "-leading-dash",
    "",
    "a\nb"
  ]) {
    assert.strictEqual(isPlainCliName(bad), false, JSON.stringify(bad) + " must be refused");
  }
});

test.after(() => {
  fs.rmSync(TMP, { recursive: true, force: true });
});
