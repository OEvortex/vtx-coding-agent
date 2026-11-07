import { execFile } from "child_process";

// Best-effort cleanup of stranded `vtx acp` agents (and their MCP server
// children) left behind by a previously crashed or force-quit VS Code.
//
// Safety: we only ever touch ORPHANS, i.e. a `vtx acp` whose parent has died
// so it was reparented to init, or on a systemd desktop to the user manager
// that reaps in init's place. A live window's agent has a live extension-host
// parent, a terminal `vtx` TUI is not `vtx acp`, and this (or any) session's
// MCP servers hang off a live agent, so none of those are ever matched.
// macOS/Linux only (no-op on Windows).
export function reapOrphanedAgents(log: (line: string) => void): void {
  if (process.platform === "win32") {
    return;
  }
  execFile("ps", ["-eo", "pid=,ppid=,args="], { maxBuffer: 8 * 1024 * 1024 }, (err, stdout) => {
    if (err || !stdout) {
      return;
    }
    const rows: { pid: number; ppid: number; cmd: string }[] = [];
    for (const line of stdout.split("\n")) {
      const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/);
      if (m) {
        rows.push({ pid: Number(m[1]), ppid: Number(m[2]), cmd: m[3] });
      }
    }
    const childrenOf = new Map<number, number[]>();
    for (const r of rows) {
      const arr = childrenOf.get(r.ppid);
      if (arr) {
        arr.push(r.pid);
      } else {
        childrenOf.set(r.ppid, [r.pid]);
      }
    }
    // An orphaned agent: `.../vtx acp` that has been reparented. That is init
    // (pid 1), except on a systemd desktop, where VS Code runs inside the user
    // manager and the user manager is the subreaper that inherits it. Nothing
    // else is ever a parent of a live agent, so this cannot match one.
    const cmdOf = new Map(rows.map((r) => [r.pid, r.cmd]));
    const reaped = (ppid: number) => ppid === 1 || /systemd\s+--user/.test(cmdOf.get(ppid) || "");
    const orphans = rows.filter(
      (r) => reaped(r.ppid) && /(^|\/)vtx acp(\s|$)/.test(r.cmd)
    );
    if (orphans.length === 0) {
      return;
    }
    // Collect each orphan plus its whole descendant tree (its MCP servers).
    const toKill = new Set<number>();
    const collect = (pid: number): void => {
      if (toKill.has(pid)) {
        return;
      }
      toKill.add(pid);
      for (const c of childrenOf.get(pid) ?? []) {
        collect(c);
      }
    };
    for (const o of orphans) {
      collect(o.pid);
    }
    log(`[reaper] cleaning ${orphans.length} stranded vtx acp agent(s) (${toKill.size} process(es))`);
    for (const pid of toKill) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        // already gone
      }
    }
  });
}
