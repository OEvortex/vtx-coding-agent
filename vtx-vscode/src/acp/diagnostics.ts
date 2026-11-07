import * as path from "path";
import * as vscode from "vscode";
import { DiagnosticItem } from "./types";

// Turning VS Code's diagnostics into the list the agent asks for.
//
// This is the one thing a native editor client can tell Vtx that the CLI in a
// terminal cannot: what the editor already knows is wrong, without spawning a
// compiler or a linter to find out. The agent pulls it on its own schedule.
//
// It is a filter, not a dump. A big workspace reports thousands of diagnostics and
// they would crowd everything else out of the agent's context, so:
//  - errors and warnings only (hints and information are editor UI, not defects)
//  - inside the workspace, since a diagnostic in a dependency is not actionable
//  - files the agent has touched this session first, because those are the ones it
//    is working on and the ones its own edits may have broken
//  - capped, with errors ahead of warnings inside each group

// How many diagnostics the agent is handed at once.
export const MAX_DIAGNOSTICS = 200;

export interface DiagnosticsOptions {
  // Absolute paths the agent has edited this session, which sort first.
  touched?: Set<string>;
  // Workspace roots. Empty means no workspace is open, so nothing is filtered out.
  roots?: string[];
  max?: number;
}

export function diagnosticItems(
  entries: readonly [vscode.Uri, readonly vscode.Diagnostic[]][],
  opts: DiagnosticsOptions = {}
): DiagnosticItem[] {
  const { touched = new Set<string>(), roots = [], max = MAX_DIAGNOSTICS } = opts;
  const items: { item: DiagnosticItem; touched: boolean; error: boolean }[] = [];
  for (const [uri, list] of entries) {
    if (uri.scheme !== "file" || !inside(uri.fsPath, roots)) {
      continue;
    }
    for (const d of list) {
      const severity = severityName(d.severity);
      if (!severity) {
        continue;
      }
      items.push({
        touched: touched.has(uri.fsPath),
        error: severity === "error",
        item: {
          // A uri, not a path: the agent rejects the whole reply without it.
          uri: uri.toString(),
          id: diagnosticCode(d),
          message: d.message,
          severity,
          source: d.source,
          // Left zero based, as VS Code reports it. The agent renders it one based.
          range: {
            start: { line: d.range.start.line, character: d.range.start.character },
            end: { line: d.range.end.line, character: d.range.end.character }
          }
        }
      });
    }
  }
  items.sort((a, b) => {
    if (a.touched !== b.touched) return a.touched ? -1 : 1;
    if (a.error !== b.error) return a.error ? -1 : 1;
    return 0;
  });
  return items.slice(0, max).map((e) => e.item);
}

// Only the two that describe a defect. Returns undefined for the rest, which is
// how they are filtered out.
function severityName(s: vscode.DiagnosticSeverity): string | undefined {
  if (s === vscode.DiagnosticSeverity.Error) return "error";
  if (s === vscode.DiagnosticSeverity.Warning) return "warning";
  return undefined;
}

// The rule or code behind a diagnostic, which the agent wants as a plain string.
// VS Code allows a string, a number, or an object carrying a link to the rule's
// documentation, and an unlabelled diagnostic still needs something stable.
function diagnosticCode(d: vscode.Diagnostic): string {
  const code = typeof d.code === "object" && d.code ? d.code.value : d.code;
  return code != null ? String(code) : (d.source || "diagnostic");
}

function inside(p: string, roots: string[]): boolean {
  // A root can already end in a separator, since "/" and "C:\\" are folders a
  // user can open. Comparing against "//" then matched nothing at all, so the
  // agent was told every time that the code had no problems.
  const norm = (r: string) => (r.length > 1 && r.endsWith(path.sep) ? r.slice(0, -1) : r);
  return roots.length === 0 || roots.some((raw) => {
    const r = norm(raw);
    return p === r || p.startsWith(r.endsWith(path.sep) ? r : r + path.sep);
  });
}
