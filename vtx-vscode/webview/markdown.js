import MarkdownIt from "markdown-it";
import hljs from "highlight.js/lib/common";

// Real markdown rendering for assistant messages. HTML is disabled so model
// output cannot inject markup; only the tags markdown-it emits are produced.
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, lang) {
    // Mermaid fences render as a live diagram once the assistant turn settles;
    // until then (and on parse failure) the source shows as a normal code block.
    if ((lang || "").toLowerCase() === "mermaid") {
      return `<pre class="code-block mermaid-src" data-lang="mermaid"><code>${md.utils.escapeHtml(code)}</code></pre>`;
    }
    const language = lang && hljs.getLanguage(lang) ? lang : "";
    let body;
    if (language) {
      try {
        body = hljs.highlight(code, { language, ignoreIllegals: true }).value;
      } catch {
        body = md.utils.escapeHtml(code);
      }
    } else {
      body = md.utils.escapeHtml(code);
    }
    const attr = language ? ` data-lang="${language}"` : "";
    return `<pre class="code-block hljs"${attr}><code>${body}</code></pre>`;
  }
});
const defaultLinkValidator = md.validateLink.bind(md);
md.validateLink = (url) => /^file:/i.test(url) || defaultLinkValidator(url);

export function renderMarkdown(src) {
  return md.render(src || "");
}

// Syntax-highlighted shell command (inline HTML), for the terminal tool card.
export function renderShell(src) {
  const code = String(src || "");
  try {
    return hljs.highlight(code, { language: "bash", ignoreIllegals: true }).value;
  } catch {
    return md.utils.escapeHtml(code);
  }
}

export function renderMarkdownInline(src) {
  return md.renderInline(src || "");
}

// Syntax-highlighted code (inline HTML) in an explicit language, for tool result
// bodies (e.g. pretty-printed JSON from an MCP tool).
export function renderCode(src, lang) {
  const code = String(src || "");
  const language = lang && hljs.getLanguage(lang) ? lang : "";
  if (!language) {
    return md.utils.escapeHtml(code);
  }
  try {
    return hljs.highlight(code, { language, ignoreIllegals: true }).value;
  } catch {
    return md.utils.escapeHtml(code);
  }
}
