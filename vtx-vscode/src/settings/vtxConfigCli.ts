import { execFile } from "child_process";
import { cliCommand } from "../cli/locate";

// Thin wrappers around the Vtx CLI customization verbs used by the settings
// surface. Reads use `<cmd> list` (JSON when supported, text otherwise); MCP
// mutations always go through the CLI so OAuth and scope handling are correct.

export interface CliContext {
  cliPath: string;
  env?: NodeJS.ProcessEnv;
  cwd?: string;
}

function run(ctx: CliContext, args: string[]): Promise<{ ok: boolean; out: string; err: string }> {
  return new Promise((resolve) => {
    const cmd = cliCommand(ctx.cliPath, args);
    execFile(
      cmd.file,
      cmd.args,
      { env: ctx.env, cwd: ctx.cwd, windowsHide: true, timeout: 20000, maxBuffer: 8 * 1024 * 1024, shell: cmd.shell },
      (error, stdout, stderr) => {
        resolve({ ok: !error, out: String(stdout || ""), err: String(stderr || (error ? error.message : "")) });
      }
    );
  });
}

// A name from a config file that is about to be typed into the user's own shell.
// Which shell that is is unknown (PowerShell by default on Windows, and quoting
// differs between all of them), so rather than try to quote for every one, anything
// that is not a plain name is refused: a server called `a" & whoami & "b` in a
// cloned repo's own .vtx/mcp_config.json is not a name, it is a command.
export function isPlainCliName(name: string): boolean {
  // `@` and `/` are in, so an npm style `@scope/name` still works: neither means
  // anything to a shell. A leading dash is out, since that reads as a flag.
  return /^[A-Za-z0-9@][A-Za-z0-9._@/-]*$/.test(name);
}

export interface NamedItem {
  name: string;
  description?: string;
  trigger?: string;
  path?: string;
  source?: string;
  raw?: unknown;
}

// `vtx skills list` prints, per skill:
//   /name [user,model] (./path/to/skill) - description...
// (the description may wrap across following lines). Capture name, scopes, the
// skill directory, and the first line of the description.
export async function listSkills(ctx: CliContext): Promise<NamedItem[]> {
  const r = await run(ctx, ["skills", "list"]);
  if (!r.out.trim()) return [];
  const items: NamedItem[] = [];
  const re = /^\s*\/(\S+)\s+\[([^\]]*)\]\s+\(([^)]+)\)\s*(?:-\s*(.*))?$/;
  for (const line of r.out.split(/\r?\n/)) {
    const m = line.match(re);
    if (m) {
      items.push({ name: m[1], source: m[2], path: m[3].trim(), description: (m[4] || "").trim() });
    }
  }
  return items;
}

// `vtx plugins list` prints installed plugins (name, version, blocked status).
export async function listPlugins(ctx: CliContext): Promise<NamedItem[]> {
  const r = await run(ctx, ["plugins", "list"]);
  if (!r.ok || !r.out.trim() || /no plugins/i.test(r.out)) return [];
  const items: NamedItem[] = [];
  // Only the bullet rows the CLI prints (`  • story-skills v0.3.1`). Taking any
  // line meant an error, a hint, or a reworded header became a plugin, complete
  // with Update and Remove buttons: `Error: not authenticated` turned into a
  // plugin named "Error:" that could be uninstalled.
  for (const line of r.out.split(/\r?\n/)) {
    const m = /^\s*[•*-]\s+(\S+)\s*(.*)$/.exec(line);
    if (m) items.push({ name: m[1], description: m[2].trim() });
  }
  return items;
}

export async function pluginVerb(
  ctx: CliContext,
  verb: "install" | "update" | "remove",
  arg?: string
): Promise<{ ok: boolean; err: string }> {
  const args = ["plugins", verb];
  if (arg) args.push(arg);
  const r = await run(ctx, args);
  return { ok: r.ok, err: r.err || r.out };
}

export interface McpAddOptions {
  name: string;
  scope: "user" | "project";
  transport?: "stdio" | "http";
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
}

export async function mcpAdd(ctx: CliContext, o: McpAddOptions): Promise<{ ok: boolean; err: string }> {
  // `-s` defaults to `local`, so pass the scope explicitly (user = global
  // ~/.vtx/mcp_config.json, project = .vtx/mcp_config.json).
  const args: string[] = ["mcp", "add", "-s", o.scope];
  if (o.transport) args.push("-t", o.transport);
  for (const [k, v] of Object.entries(o.env || {})) args.push("-e", `${k}=${v}`);
  for (const [k, v] of Object.entries(o.headers || {})) args.push("-H", `${k}: ${v}`);
  if (o.url) {
    args.push("--url", o.url, o.name);
  } else if (o.command) {
    args.push(o.name, "--", o.command, ...(o.args || []));
  } else {
    args.push(o.name);
  }
  const r = await run(ctx, args);
  return { ok: r.ok, err: r.err };
}

export async function mcpVerb(
  ctx: CliContext,
  verb: "remove" | "enable" | "disable" | "login" | "logout",
  name: string,
  scope?: "user" | "project"
): Promise<{ ok: boolean; err: string }> {
  const args = ["mcp", verb, name];
  // `-s` defaults to `local`, so pass the scope explicitly for config-editing
  // verbs (login/logout are not scoped).
  if (scope && verb !== "login" && verb !== "logout") {
    args.push("-s", scope);
  }
  const r = await run(ctx, args);
  return { ok: r.ok, err: r.err };
}
