import { execFile } from "child_process";
import { cliCommand } from "../cli/locate";

// Thin wrappers around the Vtx CLI surfaces used by the settings panel.
// `config list`/`set` own ~/.vtx/config.yml, `--dump-config` lists plugin rows,
// and `plugin enable|disable` rewrites the profile patch layer.

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

export interface NamedItem {
  name: string;
  description?: string;
  trigger?: string;
  path?: string;
  source?: string;
  disabled?: boolean;
  raw?: unknown;
}

// Vtx has no `skills list` verb: skills are discovered from .agents/skills,
// ~/.agents/skills and ~/.vtx/skills (src/vtx/agent/context/skills.py), which
// the settings panel scans directly.

// `vtx --dump-config` prints the composed plugin row list for the selected
// profile: `<name>\t[enabled|disabled]\t<module:PLUGIN>`. That is the surface
// `vtx plugin enable|disable` edits, so it is what the panel lists.
export async function listPlugins(ctx: CliContext): Promise<NamedItem[]> {
  const r = await run(ctx, ["--dump-config"]);
  if (!r.ok || !r.out.trim()) return [];
  const items: NamedItem[] = [];
  for (const line of r.out.split(/\r?\n/)) {
    const m = /^(\S+)\s+\[(enabled|disabled)\]\s+(\S+)/.exec(line);
    if (m) items.push({ name: m[1], description: m[3], disabled: m[2] === "disabled" });
  }
  return items;
}

// `vtx config list` prints the persisted ~/.vtx/config.yml as JSON, and
// `vtx config get <key>` / `set <key> <value>` read and write one dotted key.
// This is the only correct way for the panel to touch user settings: the file
// is YAML, which the JSONC editor cannot parse.
export async function configList(ctx: CliContext): Promise<Record<string, unknown> | undefined> {
  const r = await run(ctx, ["config", "list"]);
  if (!r.ok || !r.out.trim()) return undefined;
  try {
    const parsed = JSON.parse(r.out);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : undefined;
  } catch {
    return undefined;
  }
}

export async function configSet(ctx: CliContext, key: string, value: unknown): Promise<{ ok: boolean; err: string }> {
  const raw = typeof value === "string" ? value : JSON.stringify(value);
  const r = await run(ctx, ["config", "set", key, raw]);
  return { ok: r.ok, err: r.err || r.out };
}

export async function pluginVerb(
  ctx: CliContext,
  verb: "enable" | "disable",
  arg?: string
): Promise<{ ok: boolean; err: string }> {
  if (!arg) return { ok: false, err: "missing row name" };
  const r = await run(ctx, ["plugin", verb, arg]);
  return { ok: r.ok, err: r.err || r.out };
}
