// Minimal stand-in for the `vscode` module, enough to load the host side of the
// extension outside VS Code: the working set (src/diff/changeTracker.ts), the ACP
// and terminal layers, and the chat controller itself (see chat-harness.js). Only
// the API those actually touch is implemented.
//
// Two things a test can drive from the outside, because the module under test is
// bundled with its own copy of this file and cannot be handed objects directly:
//   globalThis.__dvConfig  -> what workspace.getConfiguration().get() answers
//   globalThis.__dvFolders -> workspace.workspaceFolders

class EventEmitter {
  constructor() {
    this.listeners = [];
    this.event = (fn) => {
      this.listeners.push(fn);
      return { dispose: () => { this.listeners = this.listeners.filter((l) => l !== fn); } };
    };
  }
  fire(value) {
    for (const l of [...this.listeners]) l(value);
  }
  dispose() {
    this.listeners = [];
  }
}

const nodeFs = require("fs");
const nodePath = require("path");

const fileUri = (p) => ({
  scheme: "file",
  path: p,
  fsPath: p,
  query: "",
  toString: () => "file://" + p,
  // The real Uri is immutable and derives a new one. Used to name the scratch file
  // an atomic write goes through, so a test that lacks it silently saves nothing.
  with: (change) => fileUri(change && change.path !== undefined ? change.path : p)
});

const Uri = {
  file: fileUri,
  from: ({ scheme, path: p, query }) => ({ scheme, path: p, fsPath: p, query: query || "", toString: () => `${scheme}://${p}?${query}` }),
  joinPath: (base, ...parts) => Uri.file(nodePath.join(base.fsPath, ...parts)),
  parse: (s) => {
    const m = /^([a-z][a-z0-9+.-]*):\/\/(.*)$/i.exec(String(s));
    return m ? { scheme: m[1], path: m[2], fsPath: m[2], query: "", toString: () => String(s) } : fileUri(String(s));
  }
};

const Disposable = { from: (...items) => ({ dispose: () => items.forEach((i) => i.dispose && i.dispose()) }) };

const commands = {
  registered: new Map(),
  registerCommand(id, fn) {
    commands.registered.set(id, fn);
    return { dispose: () => commands.registered.delete(id) };
  },
  executeCommand: async () => undefined
};

// Enough of `workspace.fs` for the working set to be written and read back, which
// is how it survives a window reload.
const workspace = {
  registerTextDocumentContentProvider: () => ({ dispose: () => {} }),
  // Every resolution of a virtual document is recorded, so a test can prove the
  // original is only ever created once however many times it is asked for. The
  // record lives on the global because this stub is bundled into the module under
  // test, so the test file holds a different copy of it.
  openTextDocument: async (uri) => {
    (globalThis.__dvOpened = globalThis.__dvOpened || []).push(uri.toString());
    return { uri };
  },
  fs: {
    readFile: async (uri) => nodeFs.promises.readFile(uri.fsPath),
    writeFile: async (uri, body) => nodeFs.promises.writeFile(uri.fsPath, body),
    createDirectory: async (uri) => nodeFs.promises.mkdir(uri.fsPath, { recursive: true }),
    delete: async (uri) => nodeFs.promises.rm(uri.fsPath),
    rename: async (from, to) => nodeFs.promises.rename(from.fsPath, to.fsPath)
  }
};

const scm = {
  createSourceControl: () => ({
    quickDiffProvider: undefined,
    createResourceGroup: () => ({ resourceStates: [], dispose: () => {} }),
    dispose: () => {}
  })
};

// Enough of the terminal API to drive src/acp/vscodeTerminal.ts: a test builds a
// fake terminal, decides whether it reports shell integration, and pushes chunks
// into the execution's stream. `window.terminals` is what a test inspects.
const shellIntegrationChanged = new EventEmitter();
const shellExecutionEnded = new EventEmitter();
const terminalClosed = new EventEmitter();

const window = {
  terminals: [],
  createTerminal(options) {
    const terminal = {
      options,
      shown: 0,
      sent: [],
      shellIntegration: undefined,
      exitStatus: undefined,
      show: () => { terminal.shown++; },
      sendText: (t) => terminal.sent.push(t),
      dispose: () => { terminal.exitStatus = { code: 0 }; terminalClosed.fire(terminal); }
    };
    window.terminals.push(terminal);
    return terminal;
  },
  onDidChangeTerminalShellIntegration: shellIntegrationChanged.event,
  onDidEndTerminalShellExecution: shellExecutionEnded.event,
  onDidCloseTerminal: terminalClosed.event,
  // What the user was told. Recorded rather than shown, so a test can assert that
  // a failure was reported instead of passing in silence.
  shown: { error: [], warning: [], info: [] },
  showErrorMessage: (m) => { window.shown.error.push(String(m)); return Promise.resolve(undefined); },
  showWarningMessage: (m) => { window.shown.warning.push(String(m)); return Promise.resolve(undefined); },
  showInformationMessage: (m) => { window.shown.info.push(String(m)); return Promise.resolve(undefined); },
  // Test-side helpers, not part of the real API.
  __fire: { shellIntegrationChanged, shellExecutionEnded, terminalClosed }
};

class ThemeIcon {
  constructor(id) {
    this.id = id;
  }
}

// Same numbering as VS Code, which the diagnostics mapper compares against.
const DiagnosticSeverity = { Error: 0, Warning: 1, Information: 2, Hint: 3 };

const languages = {
  diagnostics: new Map(),
  getDiagnostics: (uri) => (uri
    ? languages.diagnostics.get(uri.fsPath) || []
    : [...languages.diagnostics].map(([p, list]) => [Uri.file(p), list]))
};

workspace.getConfiguration = () => ({
  get: (_key, fallback) => (globalThis.__dvConfig && _key in globalThis.__dvConfig ? globalThis.__dvConfig[_key] : fallback),
  update: async () => undefined
});

// --- Enough more of the API for the chat controller to be constructed and driven --
// It wires editor and document listeners in its constructor, so these have to exist
// or it cannot be built at all. Nothing here does any work: a test that needs an
// event fires it through `__fire`.

const editorChanged = new EventEmitter();
const selectionChanged = new EventEmitter();
const configChanged = new EventEmitter();
const foldersChanged = new EventEmitter();
const docOpened = new EventEmitter();
const docClosed = new EventEmitter();
const docChanged = new EventEmitter();
const docSaved = new EventEmitter();

Object.assign(window, {
  activeTextEditor: undefined,
  onDidChangeActiveTextEditor: editorChanged.event,
  onDidChangeTextEditorSelection: selectionChanged.event,
  showTextDocument: async () => undefined,
  // Answered rather than shown. A test that needs a particular choice sets
  // `window.answer` to it, which is how the terminate and discard prompts are driven.
  answer: undefined,
  showInputBox: async () => window.answer,
  showQuickPick: async () => window.answer,
  showOpenDialog: async () => undefined
});
window.showWarningMessage = (m) => { window.shown.warning.push(String(m)); return Promise.resolve(window.answer); };
window.showInformationMessage = (m) => { window.shown.info.push(String(m)); return Promise.resolve(window.answer); };
window.__fire.editorChanged = editorChanged;
window.__fire.selectionChanged = selectionChanged;
window.__fire.configChanged = configChanged;

Object.assign(workspace, {
  get workspaceFolders() {
    return globalThis.__dvFolders;
  },
  workspaceFile: undefined,
  textDocuments: [],
  asRelativePath: (p) => {
    const root = (globalThis.__dvFolders || [])[0];
    const full = typeof p === "string" ? p : p.fsPath;
    return root && full.startsWith(root.uri.fsPath) ? full.slice(root.uri.fsPath.length + 1) : full;
  },
  getWorkspaceFolder: () => (globalThis.__dvFolders || [])[0],
  findFiles: async () => [],
  onDidChangeConfiguration: configChanged.event,
  onDidChangeWorkspaceFolders: foldersChanged.event,
  onDidOpenTextDocument: docOpened.event,
  onDidCloseTextDocument: docClosed.event,
  onDidChangeTextDocument: docChanged.event,
  onDidSaveTextDocument: docSaved.event
});

const env = {
  clipboard: { writeText: async (t) => { env.clipboard.text = t; }, text: "" },
  openExternal: async () => true
};

const ConfigurationTarget = { Global: 1, Workspace: 2, WorkspaceFolder: 3 };

class Position {
  constructor(line, character) {
    this.line = line;
    this.character = character;
  }
}
class Range {
  constructor(start, end) {
    this.start = start;
    this.end = end;
  }
}

// Same numbering as VS Code, since the symbol picker maps these to its own labels.
const SymbolKind = {
  File: 0, Module: 1, Namespace: 2, Package: 3, Class: 4, Method: 5, Property: 6,
  Field: 7, Constructor: 8, Enum: 9, Interface: 10, Function: 11, Variable: 12,
  Constant: 13, String: 14, Number: 15, Boolean: 16, Array: 17, Object: 18,
  Key: 19, Null: 20, EnumMember: 21, Struct: 22, Event: 23, Operator: 24, TypeParameter: 25
};

module.exports = {
  EventEmitter, Uri, Disposable, commands, workspace, scm, window, ThemeIcon,
  DiagnosticSeverity, languages, env, ConfigurationTarget, Position, Range, SymbolKind
};

// The module under test is bundled with its own copy of this stub, so a test
// that requires it directly would be holding a different one. The copy the
// bundle loaded is published here, and that is the one to drive.
globalThis.__dvVscode = module.exports;
