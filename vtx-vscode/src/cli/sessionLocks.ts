import * as fs from "fs";
import * as path from "path";
import { vtxDataDir } from "../settings/configService";

// The Vtx CLI guards each session with an advisory lock file at
// `<data dir>/vtx/cli/session_locks/<id>.lock`, whose contents are the PID of
// the process that owns it. Resuming a session held by a live process fails
// with "... cannot be resumed because it is currently running.". The CLI never
// cleans these files, so thousands of stale ones accumulate; the mere presence
// of a `.lock` file therefore means nothing, only whether its PID is alive and
// holding it.

// Where the CLI keeps its own state, including `sessions.db`, the store every
// session lives in. Writing to it is the only outward sign the CLI gives that the
// session list has changed, from any process.
export function cliDataDir(): string {
  return path.join(vtxDataDir(), "cli");
}

export function sessionLocksDir(): string {
  return path.join(cliDataDir(), "session_locks");
}

export function lockPath(sessionId: string): string {
  return path.join(sessionLocksDir(), `${sessionId}.lock`);
}

// A PID is "alive" if signal 0 succeeds, or fails with EPERM (the process
// exists but is owned by another user). ESRCH means it is gone.
export function isPidAlive(pid: number): boolean {
  if (!pid || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}

function readPid(file: string): number | null {
  try {
    const pid = parseInt(fs.readFileSync(file, "utf8").trim(), 10);
    return Number.isFinite(pid) ? pid : null;
  } catch {
    return null;
  }
}

export interface LockOwner {
  locked: boolean; // a live process currently owns the lock
  pid?: number; // the owning PID when locked
}

// Whether a session is currently held by a live process (any process, this one
// or another). Used to decide whether resuming needs a take-over.
export function lockOwner(sessionId: string): LockOwner {
  const pid = readPid(lockPath(sessionId));
  if (pid != null && isPidAlive(pid)) {
    return { locked: true, pid };
  }
  return { locked: false };
}

// Remove a session's lock file (take-over / stale reclaim). Best effort.
export function removeLock(sessionId: string): void {
  try {
    fs.rmSync(lockPath(sessionId), { force: true });
  } catch {
    // already gone / not writable
  }
}

// Remove lock files whose owning PID is no longer alive. Returns how many were
// swept. Safe: a dead-PID lock is garbage the CLI would overwrite anyway, and
// a live owner (including EPERM) is left untouched.
export function sweepStaleLocks(log?: (line: string) => void): number {
  const dir = sessionLocksDir();
  let entries: string[];
  try {
    entries = fs.readdirSync(dir);
  } catch {
    return 0; // no locks dir yet
  }
  let removed = 0;
  for (const name of entries) {
    if (!name.endsWith(".lock")) {
      continue;
    }
    const file = path.join(dir, name);
    const pid = readPid(file);
    // Only remove when we can positively read a dead PID; skip unreadable or
    // still-owned locks.
    if (pid != null && !isPidAlive(pid)) {
      try {
        fs.rmSync(file, { force: true });
        removed++;
      } catch {
        // not writable; leave it
      }
    }
  }
  if (removed && log) {
    log(`[locks] swept ${removed} stale session lock(s)`);
  }
  return removed;
}
