const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const esbuild = require("esbuild");

const ROOT = path.resolve(__dirname, "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "devin-models-"));
function loadModels(name) {
  const outfile = path.join(TMP, name + ".js");
  esbuild.buildSync({
    entryPoints: [path.join(ROOT, "src/cli/models.ts")],
    outfile,
    bundle: true,
    platform: "node",
    format: "cjs",
    logLevel: "error"
  });
  return require(outfile);
}

const { listModelFamilies } = loadModels("models");

function fakeCli(stdout) {
  const js = path.join(TMP, "devin.js");
  const shim = path.join(TMP, process.platform === "win32" ? "devin.cmd" : "devin.sh");
  fs.writeFileSync(js, `process.stdout.write(${JSON.stringify(stdout)});`);
  if (process.platform === "win32") {
    fs.writeFileSync(shim, `@echo off\r\n"${process.execPath}" "${js}"\r\n`);
  } else {
    fs.writeFileSync(shim, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
    fs.chmodSync(shim, 0o755);
  }
  return shim;
}

test("model listing preserves cost, limits, promotion, and status metadata", async () => {
  const families = await listModelFamilies(fakeCli(JSON.stringify({
    families: [{
      family_label: "Claude",
      slug: "claude",
      variants: [{
        model_uid: "claude-medium",
        label: "Claude Medium",
        cost_tier: "Med cost",
        cost_summary: "$2 / MTok In · $10 / MTok Out",
        long_context_cost_summary: "$4 / MTok In · $20 / MTok Out",
        description: "Powerful",
        max_context_tokens: 1000000,
        max_output_tokens: 128000,
        is_promo: true,
        is_new: true,
        is_beta: false
      }]
    }]
  })));

  assert.deepStrictEqual(families[0].variants[0], {
    value: "claude-medium",
    name: "Medium",
    costTier: "Med cost",
    costSummary: "$2 / MTok In · $10 / MTok Out",
    longContextCostSummary: "$4 / MTok In · $20 / MTok Out",
    description: "Powerful",
    maxContextTokens: 1000000,
    maxOutputTokens: 128000,
    promotion: "PROMO",
    isNew: true,
    isBeta: false
  });
});

test("concurrent model listings share one CLI process", { skip: process.platform === "win32" }, async () => {
  const { listModelFamilies: list } = loadModels("models-concurrent");
  const log = path.join(TMP, "models.log");
  const js = path.join(TMP, "slow-models.js");
  const cli = path.join(TMP, "slow-models.sh");
  fs.writeFileSync(js, `require("fs").appendFileSync(process.env.MODELS_LOG, "x"); setTimeout(() => process.stdout.write('{"families":[]}'), 100);`);
  fs.writeFileSync(cli, `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} ${JSON.stringify(js)}\n`);
  fs.chmodSync(cli, 0o755);

  await Promise.all([
    list(cli, { ...process.env, MODELS_LOG: log }),
    list(cli, { ...process.env, MODELS_LOG: log })
  ]);

  assert.strictEqual(fs.readFileSync(log, "utf8"), "x");
});

test.after(() => fs.rmSync(TMP, { recursive: true, force: true }));
