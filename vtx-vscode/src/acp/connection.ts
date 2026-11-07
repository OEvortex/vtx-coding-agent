import { ChildProcessWithoutNullStreams } from "child_process";

// A minimal JSON-RPC 2.0 connection over a child process' stdio, using
// newline-delimited JSON framing (as spoken by `vtx acp`).

type RpcId = number;
const MAX_FRAME_CHARS = 8 * 1024 * 1024;
const MAX_STDERR_CHARS = 64 * 1024;

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

export type RequestHandler = (method: string, params: unknown) => Promise<unknown>;
export type NotificationHandler = (method: string, params: unknown) => void;

export class JsonRpcConnection {
  private nextId: RpcId = 1;
  private readonly pending = new Map<RpcId, PendingCall>();
  private buffer = "";
  private discardingFrame = false;
  private stderr = "";
  private closed = false;

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly onRequest: RequestHandler,
    private readonly onNotification: NotificationHandler,
    private readonly onLog?: (line: string) => void
  ) {
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk: string) => this.onData(chunk));
    this.child.stderr.setEncoding("utf8");
    this.child.stderr.on("data", (chunk: string) => this.onStderr(chunk));
    this.child.on("close", () => this.handleClose());
    // `close` waits for the pipes as well as the process, and the agent starts
    // its MCP servers as children that inherit them: one of those outliving a
    // crashed agent held `close` back, and a turn waiting on a prompt (the one
    // call with no timeout) never heard that its agent had gone.
    this.child.on("exit", () => this.handleClose());
    // Without listeners, a stream `error` (e.g. EPIPE on stdin after the agent
    // dies) is thrown as an unhandled error and can crash the extension host.
    this.child.stdin.on("error", (err) => this.onLog?.(`[stdin-error] ${err.message}`));
    this.child.stdout.on("error", (err) => {
      this.onLog?.(`[stdout-error] ${err.message}`);
      this.handleClose();
    });
    this.child.stderr.on("error", (err) => this.onLog?.(`[stderr-error] ${err.message}`));
  }

  private onData(chunk: string): void {
    if (this.discardingFrame) {
      const index = chunk.indexOf("\n");
      if (index < 0) {
        return;
      }
      this.discardingFrame = false;
      chunk = chunk.slice(index + 1);
    }
    this.buffer += chunk;
    let index: number;
    while ((index = this.buffer.indexOf("\n")) >= 0) {
      const frame = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 1);
      if (frame.length > MAX_FRAME_CHARS) {
        this.onLog?.("[protocol-error] ACP frame exceeded 8 MiB");
        continue;
      }
      const line = frame.trim();
      if (line.length === 0) {
        continue;
      }
      this.dispatch(line);
    }
    if (this.buffer.length > MAX_FRAME_CHARS) {
      this.buffer = "";
      this.discardingFrame = true;
      this.onLog?.("[protocol-error] ACP frame exceeded 8 MiB");
    }
  }

  private onStderr(chunk: string): void {
    this.stderr += chunk;
    let index: number;
    while ((index = this.stderr.indexOf("\n")) >= 0) {
      const output = this.stderr.slice(0, index);
      this.stderr = this.stderr.slice(index + 1);
      if (output.length > MAX_STDERR_CHARS) {
        this.onLog?.("[protocol-error] ACP stderr line exceeded 64 KiB");
        continue;
      }
      const line = output.trim();
      if (isActionableLog(line)) {
        this.onLog?.(redactLog(line));
      }
    }
    if (this.stderr.length > MAX_STDERR_CHARS) {
      this.stderr = "";
      this.onLog?.("[protocol-error] ACP stderr line exceeded 64 KiB");
    }
  }

  private dispatch(line: string): void {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      this.onLog?.(`[non-json] ${line}`);
      return;
    }

    if (msg.id !== undefined && msg.method !== undefined) {
      // Request from the agent to us.
      this.handleIncomingRequest(msg);
      return;
    }
    if (msg.id !== undefined) {
      // Response to a call we made.
      const pending = this.pending.get(msg.id);
      if (!pending) {
        return;
      }
      this.pending.delete(msg.id);
      if (msg.error) {
        const err = new Error(msg.error.message || "RPC error") as Error & { code?: number; data?: unknown };
        err.code = msg.error.code;
        err.data = msg.error.data;
        pending.reject(err);
      } else {
        pending.resolve(msg.result);
      }
      return;
    }
    if (msg.method !== undefined) {
      // Notification from the agent.
      this.onNotification(msg.method, msg.params);
    }
  }

  private async handleIncomingRequest(msg: any): Promise<void> {
    try {
      const result = await this.onRequest(msg.method, msg.params);
      this.send({ jsonrpc: "2.0", id: msg.id, result: result ?? null });
    } catch (err) {
      this.send({
        jsonrpc: "2.0",
        id: msg.id,
        error: {
          // A handler that names a JSON-RPC code (a method we do not serve) keeps it,
          // since that is how the agent tells "not supported" from "it went wrong".
          code: typeof (err as { code?: unknown })?.code === "number" ? (err as { code: number }).code : -32000,
          message: err instanceof Error ? err.message : String(err)
        }
      });
    }
  }

  // `timeoutMs` bounds the wait. Every call the client makes passes one except a prompt,
  // and a test reads that rule off its source. A reply is otherwise only settled by the
  // agent answering or its process closing, so an agent that is alive and silent (a
  // blocking MCP server, a token refresh) leaves the caller waiting for the rest of the
  // window. It is not the default here because a prompt is a whole turn and can
  // legitimately run for many minutes.
  request<T = unknown>(method: string, params?: unknown, timeoutMs?: number): Promise<T> {
    if (this.closed) {
      return Promise.reject(new Error("ACP connection is closed"));
    }
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      let timer: NodeJS.Timeout | undefined;
      const clear = (): void => {
        if (timer) {
          clearTimeout(timer);
        }
        this.pending.delete(id);
      };
      this.pending.set(id, {
        resolve: (v: unknown) => { clear(); resolve(v as T); },
        reject: (e: unknown) => { clear(); reject(e); }
      });
      if (timeoutMs && timeoutMs > 0) {
        timer = setTimeout(() => {
          this.onLog?.(`[rpc-timeout] ${method} after ${timeoutMs}ms`);
          clear();
          reject(new Error(`${method} did not answer within ${Math.round(timeoutMs / 1000)}s`));
        }, timeoutMs);
        timer.unref?.();
      }
      // If the write fails the response can never arrive, so settle the call
      // now rather than leaving it pending forever.
      if (!this.send({ jsonrpc: "2.0", id, method, params })) {
        clear();
        reject(new Error("Failed to write to ACP process"));
      }
    });
  }

  notify(method: string, params?: unknown): void {
    if (this.closed) {
      return;
    }
    this.send({ jsonrpc: "2.0", method, params });
  }

  private send(message: unknown): boolean {
    try {
      this.child.stdin.write(JSON.stringify(message) + "\n");
      return true;
    } catch (err) {
      this.onLog?.(`[send-failed] ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
  }

  private handleClose(): void {
    this.closed = true;
    const stderr = this.stderr.trim();
    this.stderr = "";
    if (isActionableLog(stderr)) {
      this.onLog?.(redactLog(stderr));
    }
    for (const [, pending] of this.pending) {
      pending.reject(new Error("ACP process exited"));
    }
    this.pending.clear();
  }

  dispose(): void {
    try {
      this.child.stdin.end();
    } catch {
      // ignore
    }
    // Nothing is going to answer these now. Left pending, a call made just before
    // the agent was disposed hung for ever, and its caller with it.
    this.handleClose();
  }
}

function isActionableLog(line: string): boolean {
  return /\b(?:warn|error|failed|failure|panic|exception|denied)\b/i.test(line);
}

function redactLog(line: string): string {
  return line
    .replace(/(authorization\s*:\s*(?:bearer\s+|basic\s+)?)[^\s,;"']+/gi, "$1[redacted]")
    .replace(/((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s,;"']+/gi, "$1[redacted]");
}
