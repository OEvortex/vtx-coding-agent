import { execFile } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

export interface CliAccount {
  name?: string;
  email?: string;
  tier?: string;
  plan?: string;
}

export interface CliHealth {
  path: string;
  found: boolean;
  version?: string;
  loggedIn?: boolean;
  account?: CliAccount;
  error?: string;
}

let cachedEnv: NodeJS.ProcessEnv | undefined;
let pendingEnv: Promise<NodeJS.ProcessEnv> | undefined;
const pendingHealth = new Map<string, Promise<CliHealth>>();

// GUI apps on macOS/Linux do not inherit the shell PATH, so resolve it from a
// login shell once and reuse it. The Vtx CLI also spawns MCP servers
// (npx, docker, uvx) that need this PATH.
export function loginShellEnv(): Promise<NodeJS.ProcessEnv> {
  if (cachedEnv) {
    return Promise.resolve(cachedEnv);
  }
  if (pendingEnv) {
    return pendingEnv;
  }
  if (process.platform === "win32") {
    cachedEnv = process.env;
    return Promise.resolve(cachedEnv);
  }
  const pending = new Promise<NodeJS.ProcessEnv>((resolve) => {
    const shell = process.env.SHELL || "/bin/sh";
    // In fish `$PATH` is a list, so quoting it yields space separated garbage.
    const script = /(^|\/)fish$/.test(shell)
      ? "printf '__PATH__=%s' (string join : $PATH)"
      : "printf '__PATH__=%s' \"$PATH\"";
    execFile(
      shell,
      ["-lic", script],
      { timeout: 5000, windowsHide: true },
      (_err, stdout) => {
        const match = /__PATH__=([^\n]*)/.exec(stdout || "");
        cachedEnv = match ? { ...process.env, PATH: match[1] } : { ...process.env };
        resolve(cachedEnv);
      }
    );
  });
  pendingEnv = pending;
  void pending.finally(() => {
    if (pendingEnv === pending) {
      pendingEnv = undefined;
    }
  });
  return pending;
}

export function expandHome(p: string): string {
  if (!p) {
    return p;
  }
  if (p === "~") {
    return os.homedir();
  }
  if (p.startsWith("~/") || p.startsWith("~\\")) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

function commonLocations(): string[] {
  const home = os.homedir();
  if (process.platform === "win32") {
    const local = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
    return [
      path.join(local, "Programs", "vtx", "vtx.exe"),
      path.join(home, ".local", "bin", "vtx.exe")
    ];
  }
  return [
    path.join(home, ".local", "bin", "vtx"),
    "/usr/local/bin/vtx",
    "/opt/homebrew/bin/vtx",
    "/usr/bin/vtx"
  ];
}

// Resolve a binary by scanning the (already login-shell-resolved) PATH
// directly, instead of spawning another interactive login shell, which on a
// heavy shell profile can add several seconds to startup.
function which(binary: string, env: NodeJS.ProcessEnv): Promise<string | undefined> {
  const isWin = process.platform === "win32";
  const exts = isWin ? (env.PATHEXT || ".COM;.EXE;.CMD;.BAT").split(";") : [""];
  const dirs = String(env.PATH || process.env.PATH || "").split(isWin ? ";" : ":");
  for (const dir of dirs) {
    if (!dir) {
      continue;
    }
    // Windows PATH entries are often quoted, and the quotes are not part of the
    // directory name.
    for (const ext of exts) {
      const candidate = path.join(isWin ? dir.replace(/^"|"$/g, "") : dir, binary + ext);
      try {
        if (fs.existsSync(candidate)) {
          return Promise.resolve(candidate);
        }
      } catch {
        // ignore unreadable dirs
      }
    }
  }
  return Promise.resolve(undefined);
}

// Resolves the vtx binary using: explicit setting, login-shell PATH lookup,
// then common install locations. Returns an absolute path when possible.
export async function resolveCliPath(setting: string): Promise<string | undefined> {
  const env = await loginShellEnv();
  const configured = expandHome((setting || "").trim());
  if (configured && configured !== "vtx") {
    if (path.isAbsolute(configured)) {
      if (fs.existsSync(configured)) {
        return configured;
      }
    } else {
      // A bare/relative custom name (e.g. "vtx-beta"): resolve it on PATH by
      // its basename rather than silently falling back to "vtx".
      const viaCustom = await which(path.basename(configured), env);
      if (viaCustom && fs.existsSync(viaCustom)) {
        return viaCustom;
      }
    }
  }
  const viaWhich = await which("vtx", env);
  if (viaWhich && fs.existsSync(viaWhich)) {
    return viaWhich;
  }
  for (const candidate of commonLocations()) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return configured && configured !== "vtx" ? configured : undefined;
}

// npm installs a CLI on Windows as a `.cmd` shim, and since Node 18.20 a shim
// cannot be spawned directly (CVE-2024-27980): it has to go through the command
// interpreter. Once a shell is in the way the quoting is ours to do, so every
// spawn of the CLI is built here and the quirk lives in one place.
export function cliCommand(bin: string, args: string[]): { file: string; args: string[]; shell: boolean } {
  if (process.platform !== "win32" || !/\.(cmd|bat)$/i.test(bin)) {
    return { file: bin, args, shell: false };
  }
  // `cmd.exe` expands `%VAR%` and, with delayed expansion, `!VAR!`, and it does so
  // inside double quotes as well. There is no escape for either on a command line
  // (`%%` only works inside a batch file), so a value carrying one cannot be passed
  // through a shim safely. Refusing says so, where quoting it would silently run
  // something else.
  const unsafe = [bin, ...args].find((s) => /[%!]/.test(s));
  if (unsafe) {
    throw new Error(`Cannot pass "${unsafe}" to the Vtx CLI on Windows: % and ! have no escape through a .cmd shim.`);
  }
  const quote = (s: string) => (/[\s"^&|<>()]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  return { file: quote(bin), args: args.map(quote), shell: true };
}

function run(bin: string, args: string[], env: NodeJS.ProcessEnv): Promise<{ ok: boolean; out: string }> {
  return new Promise((resolve) => {
    const cmd = cliCommand(bin, args);
    execFile(cmd.file, cmd.args, { timeout: 8000, env, windowsHide: true, shell: cmd.shell }, (err, stdout, stderr) => {
      resolve({ ok: !err, out: String(stdout || stderr || "").trim() });
    });
  });
}

export function checkHealth(setting: string): Promise<CliHealth> {
  const key = setting || "vtx";
  const existing = pendingHealth.get(key);
  if (existing) {
    return existing;
  }
  const pending = checkHealthNow(setting);
  pendingHealth.set(key, pending);
  void pending.finally(() => {
    if (pendingHealth.get(key) === pending) {
      pendingHealth.delete(key);
    }
  });
  return pending;
}

async function checkHealthNow(setting: string): Promise<CliHealth> {
  const env = await loginShellEnv();
  const resolved = await resolveCliPath(setting);
  if (!resolved) {
    return { path: setting || "vtx", found: false, error: "Vtx CLI not found" };
  }
  // Version and auth status are independent; run them together to save a
  // whole CLI round-trip on startup.
  const [version, auth] = await Promise.all([
    run(resolved, ["--version"], env),
    run(resolved, ["auth", "status"], env)
  ]);
  if (!version.ok) {
    return { path: resolved, found: false, error: version.out || "Failed to run vtx --version" };
  }
  // `vtx auth status` prints "Logged in (via Vtx)." when authed and
  // "Not logged in." when not, so a bare /logged in/ match would treat a
  // signed-out user as signed in. Require a positive match and reject the
  // negative phrasings explicitly.
  const loggedIn = /logged in/i.test(auth.out) && !/not logged in|not authenticated|logged out/i.test(auth.out);
  const account = loggedIn ? parseAccount(auth.out) : undefined;
  return { path: resolved, found: true, version: cleanVersion(version.out), loggedIn, account };
}

function cleanVersion(out: string): string {
  const line = (out || "").split(/\r?\n/)[0].trim();
  return line.replace(/^vtx\s+/i, "");
}

// Parses the human-readable `vtx auth status` output.
function parseAccount(out: string): CliAccount {
  const pick = (label: string) => {
    const m = new RegExp(`^\\s*${label}:\\s*(.+)$`, "mi").exec(out);
    return m ? m[1].trim() : undefined;
  };
  return { name: pick("Name"), email: pick("Email"), tier: pick("Tier"), plan: pick("Plan") };
}
