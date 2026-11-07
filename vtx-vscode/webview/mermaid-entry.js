// Separate, lazily-loaded bundle: the main webview only injects this script the
// first time a mermaid diagram needs rendering, keeping mermaid (a multi-MB
// dependency) out of the main bundle. It just exposes the library on `window`.
import mermaid from "mermaid";

window.__mermaid = mermaid;
