// Added/removed line counts for a diff, from an LCS over lines (so the edit
// pills can show +N/-M like VS Code). Capped to avoid O(n*m) blowups on huge
// files, where it falls back to the net line delta.
export function diffStat(oldText: string | null | undefined, newText: string | null | undefined): { added: number; removed: number } {
  // Split on either ending: a CRLF file rewritten with LF line endings is not a
  // file where every line changed.
  let a = oldText ? oldText.split(/\r?\n/) : [];
  let b = newText ? newText.split(/\r?\n/) : [];
  // A file whose last line ends in a newline splits with an empty element after
  // it. When both sides have one it cancels out below, but with one side empty
  // there is nothing to cancel it: a new two line file counted three added lines,
  // one more than git and than the editor's own diff say.
  if (!a.length) return { added: withoutFinalBlank(b), removed: 0 };
  if (!b.length) return { added: 0, removed: withoutFinalBlank(a) };
  // Trim what both sides share at each end first. An edit is nearly always a few
  // lines inside a file that is otherwise identical, and the O(n*m) table below
  // does not care how similar the two sides are: counting a session's worth of
  // replayed edits without this is seconds of work for nothing.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  a = a.slice(head, a.length - tail);
  b = b.slice(head, b.length - tail);
  if (!a.length) return { added: b.length, removed: 0 };
  if (!b.length) return { added: 0, removed: a.length };
  if (a.length > 4000 || b.length > 4000) {
    return { added: Math.max(0, b.length - a.length), removed: Math.max(0, a.length - b.length) };
  }
  const m = a.length;
  const n = b.length;
  let prev = new Array<number>(n + 1).fill(0);
  for (let i = 1; i <= m; i++) {
    const cur = new Array<number>(n + 1).fill(0);
    for (let j = 1; j <= n; j++) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  const lcs = prev[n];
  return { added: n - lcs, removed: m - lcs };
}

function withoutFinalBlank(lines: string[]): number {
  return lines.length && lines[lines.length - 1] === "" ? lines.length - 1 : lines.length;
}
