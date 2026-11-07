// What a session has painted into its transcript, kept so a chat can be rebuilt
// on another surface.
//
// A chat can change surface (side panel to editor tab and back) while a turn is
// running, and the new surface starts with an empty page. The agent cannot supply
// the history at that moment: issuing `session/load` over a live channel kills the
// running prompt ("Agent communication channel closed"). The rendered DOM cannot
// travel either, since it carries live element handles. So the messages that
// produced the transcript are recorded as they go out, and replayed into the new
// surface, which rebuilds it through the same path that drew it the first time.

export type Painted = Record<string, unknown>;

// Entries kept per session. Streamed text is merged as it arrives, so this counts
// parts of a conversation (a message, a tool call, a plan) rather than stream
// chunks: a long working session sits in the hundreds, well under this. Past it
// the oldest go, and the agent is asked for the rest once the turn ends.
export const LOG_MAX = 4000;

const STREAMED = ["assistantChunk", "thoughtChunk", "userChunk"];

// Add one painted message. Consecutive chunks of the same streamed message are
// merged, so a long turn costs a handful of entries rather than thousands.
// Returns true when the oldest entries had to be dropped, which is what makes a
// rebuilt transcript partial.
export function recordPainted(log: Painted[], payload: Painted, max = LOG_MAX): boolean {
  const last = log[log.length - 1];
  if (
    last &&
    STREAMED.includes(String(payload.type)) &&
    last.type === payload.type &&
    last.messageId === payload.messageId &&
    typeof last.text === "string" &&
    typeof payload.text === "string"
  ) {
    last.text += payload.text;
    return false;
  }
  log.push({ ...payload });
  if (log.length > max) {
    log.splice(0, log.length - max);
    return true;
  }
  return false;
}

// The messages that rebuild the transcript, in order. Reasoning that has already
// happened is marked replayed so it is not timed again, and the last block is
// settled the way a reload settles it: turn markers are posted per surface, so
// they are never recorded.
export function paintedReplay(log: Painted[]): Painted[] {
  const out = log.map((p) => (p.type === "thoughtChunk" ? { ...p, replayed: true } : p));
  out.push({ type: "assistantEnd" });
  return out;
}
