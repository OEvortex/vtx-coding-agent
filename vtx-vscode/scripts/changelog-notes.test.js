// Guards the release notes extractor used by the release workflow: a known
// version returns its CHANGELOG.md section, and a missing version falls back to
// a link without failing, so a release never breaks on a missing entry.

const test = require("node:test");
const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const path = require("path");

const SCRIPT = path.resolve(__dirname, "changelog-notes.js");

function run(version) {
  return execFileSync("node", [SCRIPT, version], { encoding: "utf8" });
}

test("extracts the section body for a known version", () => {
  const out = run("0.6.93");
  assert.match(out, /### Highlights/);
  assert.match(out, /### Under the hood/);
  // The version heading and the link reference block are not part of the body.
  assert.doesNotMatch(out, /^## /m);
  assert.doesNotMatch(out, /releases\/tag/);
});

test("accepts a leading v on the version", () => {
  assert.strictEqual(run("v0.6.93"), run("0.6.93"));
});

test("falls back to a changelog link for a missing version", () => {
  const out = run("99.99.99");
  assert.match(out, /CHANGELOG\.md/);
});
