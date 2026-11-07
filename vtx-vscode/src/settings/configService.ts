import * as fs from "fs";
import * as os from "os";
import * as path from "path";

// Reads and writes the Vtx CLI config files that back the settings surface.
// Config files are JSON with comments (JSONC). We parse comment-tolerantly for
// display and, for direct edits, write back a normalised JSON file (comments in
// that file are lost, so the UI warns and offers an "open file" escape hatch).
// MCP writes go through the CLI verbs instead (see vtxConfigCli).

// Two scopes only: user (global) and project (this workspace, .vtx/config.json).
// We deliberately do not expose a separate gitignored "local" scope; the user
// gitignores whatever they want.
export type ConfigScope = "user" | "project";

export interface ConfigFile {
  scope: ConfigScope;
  path: string;
  exists: boolean;
  data: Record<string, unknown>;
}

// The user config directory, mirroring vtx's get_config_dir():
// $XDG_CONFIG_HOME/vtx when set and absolute, otherwise ~/.vtx on every
// platform (vtx has no %APPDATA% branch).
export function userConfigDir(): string {
  const xdg = process.env.XDG_CONFIG_HOME;
  if (xdg && path.isAbsolute(xdg)) {
    return path.join(xdg, "vtx");
  }
  return path.join(os.homedir(), ".vtx");
}

export function userConfigPath(): string {
  // vtx keeps its settings in config.yml (YAML), which this JSONC editor
  // cannot parse; config edits for the user scope go through `vtx config`
  // (see vtxConfigCli) until the panel speaks YAML. This path only backs the
  // project scope file, which vtx does not read either, so it is kept for
  // the panel's own bookkeeping.
  return path.join(userConfigDir(), "config.json");
}

// MCP servers live in mcp.json: ~/.vtx/mcp.json for the user and
// <project>/.vtx/mcp.json per project (src/vtx/mcp/config.py).
export function userMcpConfigPath(): string {
  return path.join(userConfigDir(), "mcp.json");
}

// Windsurf keeps its own MCP servers here. The Vtx CLI does not read this
// file; the settings surface lists them read only so the user can see what
// another editor configured, and edits go through `vtx mcp` where possible.
export function windsurfDir(): string {
  return path.join(os.homedir(), ".codeium", "windsurf");
}

export function windsurfMcpConfigPath(): string {
  return path.join(windsurfDir(), "mcp_config.json");
}

// Add, replace or (with a null definition) remove one server in an mcpServers
// file. The Vtx CLI owns its own files, so this is only for the ones it merely
// imports: `vtx mcp` will not write to another tool's config.
export function writeMcpServer(file: string, name: string, def: Record<string, unknown> | null): void {
  refuseIfUnparseable(file);
  const current = readConfig(file);
  const servers = (current.mcpServers && typeof current.mcpServers === "object"
    ? current.mcpServers
    : {}) as Record<string, unknown>;
  if (def === null) {
    delete servers[name];
  } else {
    servers[name] = def;
  }
  current.mcpServers = servers;
  writeFileAtomic(file, JSON.stringify(current, null, 2) + "\n");
}

// Where the CLI keeps its own state: XDG on macOS/Linux, %LOCALAPPDATA% on
// Windows.
export function vtxDataDir(): string {
  const dataHome = process.platform === "win32"
    ? (process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"))
    : (process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"));
  return path.join(dataHome, "vtx");
}

// MCP OAuth tokens are stored per server under the data dir; their presence
// tells us a server is logged in.
export function mcpOauthDir(): string {
  return path.join(vtxDataDir(), "mcp", "oauth");
}

export function projectConfigPath(root: string): string {
  return path.join(root, ".vtx", "config.json");
}

// Strip // line comments and /* block */ comments and trailing commas so a
// JSONC config file parses with JSON.parse. String-aware so it does not touch
// `//` or `/*` inside string values.
export function stripJsonComments(input: string): string {
  let out = "";
  let inStr = false;
  let quote = "";
  let inLine = false;
  let inBlock = false;
  for (let i = 0; i < input.length; i++) {
    const c = input[i];
    const next = input[i + 1];
    if (inLine) {
      if (c === "\n") { inLine = false; out += c; }
      continue;
    }
    if (inBlock) {
      if (c === "*" && next === "/") { inBlock = false; i++; }
      continue;
    }
    if (inStr) {
      out += c;
      if (c === "\\") { out += next ?? ""; i++; continue; }
      if (c === quote) { inStr = false; }
      continue;
    }
    if (c === '"' || c === "'") { inStr = true; quote = c; out += c; continue; }
    if (c === "/" && next === "/") { inLine = true; i++; continue; }
    if (c === "/" && next === "*") { inBlock = true; i++; continue; }
    // A trailing comma is dropped here, where a string is still a string, rather
    // than by a pass over the finished text: a pass cannot tell a comma inside a
    // value from one before a close, so a hook command or a deny rule holding `,}`
    // was read back short a character and the next write saved that.
    if (c === "}" || c === "]") { out = out.replace(/,\s*$/, ""); }
    out += c;
  }
  return out;
}

// `readConfig` answers {} for a file it cannot parse, which is fine for showing
// one and ruinous for writing it: the write would replace someone's broken config
// with a document holding nothing but the value just edited, taking their model,
// permissions, hooks and MCP servers with it. Every write goes through here first.
// Write a config file without ever leaving a half written one behind. A plain
// write truncates in place, so a crash, a force quit or a full disk between the
// truncate and the last byte leaves the user with an empty or partial config, which
// is worse than the edit not happening. Writing beside it and renaming over the top
// is atomic on every filesystem we run on, and the rename is in the same directory
// so it cannot cross a device boundary.
export function writeFileAtomic(file: string, body: string): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // A rename replaces whatever is at the destination, including a symlink, so it
  // has to act on the file the link points at: these configs are often kept in a
  // dotfiles repository and linked into place, and replacing the link leaves that
  // copy behind as a file nothing reads any more. Resolving it also keeps the
  // scratch file beside the real target, so the rename cannot cross a device.
  let target = file;
  let mode: number | undefined;
  try {
    // The link is read rather than the whole path resolved, because resolving fails for
    // a link whose target is not there yet, which a dotfiles link often is: the file has
    // not been checked out, or was renamed. Treating that as "nothing there" replaced the
    // link with a regular file, which is the detachment this is here to prevent.
    const link = fs.lstatSync(file);
    target = link.isSymbolicLink() ? path.resolve(path.dirname(file), fs.readlinkSync(file)) : file;
    mode = fs.statSync(target).mode & 0o777;
  } catch {
    // Nothing there at all, or a link to a file that is not there: no permissions to
    // carry over, and `target` is the right thing to write either way.
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const tmp = path.join(path.dirname(target), `.${path.basename(target)}.${process.pid}.tmp`);
  try {
    // A new file is 0644 whatever it is replacing, and an MCP config holds tokens in its
    // `env`, so a 0600 config must not come back readable by everyone. Created with the
    // mode rather than only narrowed afterwards, or the secrets sit in a world readable
    // scratch file for the length of the write, and then chmodded to be exact, since what
    // the mode argument asks for is still cut down by the umask.
    fs.writeFileSync(tmp, body, mode === undefined ? "utf8" : { encoding: "utf8", mode });
    if (mode !== undefined) {
      fs.chmodSync(tmp, mode);
    }
    fs.renameSync(tmp, target);
  } catch (err) {
    try { fs.rmSync(tmp, { force: true }); } catch { /* nothing to clean up */ }
    throw err;
  }
}

export function refuseIfUnparseable(file: string): void {
  if (!fs.existsSync(file)) {
    return;
  }
  const raw = fs.readFileSync(file, "utf8");
  try {
    if (raw.trim()) JSON.parse(stripJsonComments(raw));
  } catch {
    throw new Error(`${file} is not valid JSON, so it was left alone. Fix the file and try again.`);
  }
}

export function readConfig(file: string): Record<string, unknown> {
  try {
    const raw = fs.readFileSync(file, "utf8");
    const parsed = JSON.parse(stripJsonComments(raw));
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function loadConfigFile(scope: ConfigScope, root?: string): ConfigFile {
  const p = scope === "user" ? userConfigPath() : projectConfigPath(root || process.cwd());
  return { scope, path: p, exists: fs.existsSync(p), data: readConfig(p) };
}

// Set a dotted path (e.g. "agent.model", "proxy.mode") in a config file. An
// undefined value removes the key, along with any parent object it leaves empty,
// so a removed setting does not linger as `"proxy": {}`.
export function setConfigPath(scope: ConfigScope, dotted: string, value: unknown, root?: string): string {
  const p = scope === "user" ? userConfigPath() : projectConfigPath(root || process.cwd());
  refuseIfUnparseable(p);
  const current = readConfig(p);
  const keys = dotted.split(".");
  const chain: Record<string, unknown>[] = [current];
  let node: Record<string, unknown> = current;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (!node[k] || typeof node[k] !== "object") node[k] = {};
    node = node[k] as Record<string, unknown>;
    chain.push(node);
  }
  if (value === undefined) {
    delete node[keys[keys.length - 1]];
    for (let i = chain.length - 1; i > 0; i--) {
      if (Object.keys(chain[i]).length) break;
      delete chain[i - 1][keys[i - 1]];
    }
  } else {
    node[keys[keys.length - 1]] = value;
  }
  writeFileAtomic(p, JSON.stringify(current, null, 2) + "\n");
  return p;
}
