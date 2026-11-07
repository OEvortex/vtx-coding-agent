#!/usr/bin/env node
// Print the CHANGELOG.md section for a given version, for use as a GitHub
// Release body. The release workflow appends its own "Full Changelog" link, so
// this prints only the section body: the summary and the Highlights and Under
// the hood lists, without the version heading or the link reference block.
//
//   node scripts/changelog-notes.js 0.6.94
//
// If the version has no entry, it prints a short fallback and exits 0 so a
// release never breaks on a missing entry, with a warning on stderr.

const fs = require("fs");
const path = require("path");

const version = (process.argv[2] || "").replace(/^v/, "").trim();
if (!version) {
  console.error("usage: node scripts/changelog-notes.js <version>");
  process.exit(2);
}

const file = path.join(__dirname, "..", "CHANGELOG.md");
const lines = fs.readFileSync(file, "utf8").split("\n");

const heads = [];
lines.forEach((line, i) => {
  if (/^## /.test(line)) heads.push(i);
});

let body = null;
for (let k = 0; k < heads.length; k++) {
  const head = lines[heads[k]];
  if (head.startsWith(`## [${version}]`) || head.startsWith(`## ${version} `)) {
    const start = heads[k] + 1;
    const end = k + 1 < heads.length ? heads[k + 1] : lines.length;
    body = lines
      .slice(start, end)
      .filter((l) => !/^\[[^\]]+\]:\s*https?:/.test(l)) // drop the link reference block
      .join("\n")
      .replace(/^\s+/, "")
      .replace(/\s+$/, "");
    break;
  }
}

if (!body) {
  console.error(`::warning::No CHANGELOG.md entry found for ${version}. Add one before releasing.`);
  process.stdout.write(`See [CHANGELOG.md](https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md).\n`);
  process.exit(0);
}

process.stdout.write(body + "\n");
