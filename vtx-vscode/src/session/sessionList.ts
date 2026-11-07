import { execFile } from "child_process";
import { cliCommand } from "../cli/locate";
import { AcpSessionRow } from "../acp/types";

export interface VtxSession {
  id: string;
  short_id: string;
  working_directory: string;
  last_activity_at?: number;
  last_activity_ago?: string;
  title?: string;
  tracked?: boolean;
}

interface ListOptions {
  cliPath: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  // Session ids this VS Code window owns (from workspaceState).
  trackedIds: string[];
  // Exact directory each tracked id was created in.
  cwdById: Record<string, string>;
  // Workspace folders, queried as a fallback for ids with no recorded cwd.
  folders: string[];
}

export interface ListResult {
  // Tracked sessions that still exist in Vtx, with fresh metadata.
  sessions: VtxSession[];
  // Tracked ids Vtx no longer knows about, safe to drop from the store.
  prunedIds: string[];
}

const LIST_CONCURRENCY = 4;

// Membership is workspaceState (tracked ids); the CLI is only the source of
// truth for existence and metadata. `vtx list --format json` is exact-match
// on cwd, so we query each distinct directory a tracked session was created in,
// then keep only the tracked ids the CLI still returns. Ids whose directory was
// queried successfully but no longer contains them are pruned; ids whose query
// failed are left untouched so a transient CLI error never wipes the list.
export async function listSessions(opts: ListOptions): Promise<ListResult> {
  if (!opts.trackedIds.length) {
    return { sessions: [], prunedIds: [] };
  }
  const dirs = new Set<string>();
  let missingCwd = false;
  for (const id of opts.trackedIds) {
    const cwd = opts.cwdById[id];
    if (cwd) {
      dirs.add(cwd);
    } else {
      missingCwd = true;
    }
  }
  if (missingCwd) {
    for (const f of opts.folders) {
      dirs.add(f);
    }
  }
  if (dirs.size === 0) {
    dirs.add(process.cwd());
  }

  const dirList = [...dirs];
  const timeoutMs = opts.timeoutMs ?? 15_000;
  const deadline = Date.now() + timeoutMs;
  const queried = await mapLimited(
    dirList,
    LIST_CONCURRENCY,
    (d) => runList(opts.cliPath, d, opts.env, Math.max(1, deadline - Date.now())),
    deadline
  );

  const byId = new Map<string, VtxSession>();
  const okDirs = new Set<string>();
  queried.forEach((res, i) => {
    const dir = dirList[i];
    if (!res?.ok) {
      return;
    }
    okDirs.add(dir);
    for (const s of res.sessions) {
      if (s && s.id && !byId.has(s.id)) {
        byId.set(s.id, s);
      }
    }
  });

  const sessions: VtxSession[] = [];
  const prunedIds: string[] = [];
  for (const id of opts.trackedIds) {
    const found = byId.get(id);
    if (found) {
      sessions.push({ ...found, tracked: true });
      continue;
    }
    // Absent: prune only if the directory we expected it in was queried
    // successfully (so we know it is genuinely gone, not a failed call).
    const cwd = opts.cwdById[id];
    const dirKnown = cwd ? okDirs.has(cwd) : okDirs.size === dirs.size;
    if (dirKnown) {
      prunedIds.push(id);
    }
  }

  sessions.sort((a, b) => (b.last_activity_at || 0) - (a.last_activity_at || 0));
  return { sessions, prunedIds };
}

// The same listing, taken from a live agent over ACP (`session/list`) instead of
// by spawning `vtx list` once per directory. Preferred whenever a session is
// open, which is most of the time the list is looked at, and it is better as well
// as cheaper: the protocol call is not scoped to a directory, so a tracked session
// created in a subdirectory of the workspace is found rather than silently missing.
//
// Membership is unchanged: the ids this window tracks, and only those. An untracked
// session (one started by `vtx` in a terminal) is deliberately still not listed.
export function fromAcpRows(rows: AcpSessionRow[], trackedIds: string[]): ListResult {
  const byId = new Map(rows.filter((r) => r && r.sessionId).map((r) => [r.sessionId, r]));
  const sessions: VtxSession[] = [];
  const prunedIds: string[] = [];
  // An agent that can answer this at all has at least its own session to report, so
  // no rows is a protocol answer that cannot be true, not a fact about the user's
  // sessions. Believing it pruned the whole workspace list, and with it every
  // session's recorded directory and unsent draft, which nothing can bring back.
  // The CLI path guards the same way, per directory.
  const usable = byId.size > 0;
  for (const id of trackedIds) {
    const row = byId.get(id);
    if (!row) {
      // One answer covering every directory, so an id that is absent here is gone,
      // with none of the "was that directory queried successfully" doubt the CLI
      // path has to allow for.
      if (usable) {
        prunedIds.push(id);
      }
      continue;
    }
    sessions.push({
      id,
      short_id: id,
      working_directory: row.cwd || "",
      last_activity_at: epochSeconds(row.updatedAt),
      title: row.title,
      tracked: true
    });
  }
  sessions.sort((a, b) => (b.last_activity_at || 0) - (a.last_activity_at || 0));
  return { sessions, prunedIds };
}

// `vtx list` reports epoch seconds and the panel sorts and groups on that, so an
// ISO timestamp from the protocol has to arrive in the same units.
function epochSeconds(iso?: string): number | undefined {
  const ms = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(ms) ? undefined : Math.floor(ms / 1000);
}

interface RunResult {
  ok: boolean;
  sessions: VtxSession[];
}

async function mapLimited<T, R>(items: T[], limit: number, run: (item: T) => Promise<R>, deadline: number): Promise<(R | undefined)[]> {
  const results = new Array<R | undefined>(items.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length && Date.now() < deadline) {
      const index = next++;
      results[index] = await run(items[index]);
    }
  });
  await Promise.all(workers);
  return results;
}

function runList(cliPath: string, cwd: string, env?: NodeJS.ProcessEnv, timeoutMs = 15_000): Promise<RunResult> {
  return new Promise((resolve) => {
    const cmd = cliCommand(cliPath, ["list", "--format", "json"]);
    execFile(
      cmd.file,
      cmd.args,
      { cwd, env, windowsHide: true, timeout: timeoutMs, maxBuffer: 8 * 1024 * 1024, shell: cmd.shell },
      (err, stdout) => {
        if (err && !stdout) {
          resolve({ ok: false, sessions: [] });
          return;
        }
        try {
          const parsed = JSON.parse(stdout) as VtxSession[];
          resolve({ ok: true, sessions: Array.isArray(parsed) ? parsed : [] });
        } catch {
          resolve({ ok: false, sessions: [] });
        }
      }
    );
  });
}
