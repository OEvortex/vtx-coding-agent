import { useState, memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import type { Components } from "react-markdown";
import { Copy, Check, Hash } from "@phosphor-icons/react";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeHighlight, rehypeSlug];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      className="ml-auto p-1.5 rounded-md text-ink-faint hover:text-lime-300 hover:bg-white/5 transition-all cursor-pointer"
      title="Copy code"
      aria-label="Copy code"
    >
      {copied ? <Check size={13} weight="bold" className="text-lime-300" /> : <Copy size={13} weight="regular" />}
    </button>
  );
}

function extractCodeText(node: React.ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractCodeText).join("");
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props: Record<string, unknown> }).props;
    if (props.children) return extractCodeText(props.children as React.ReactNode);
    if (typeof props.value === "string") return props.value;
  }
  return "";
}

function prettyLinkLabel(children: React.ReactNode, href?: string): React.ReactNode {
  if (typeof children === "string") {
    let t = children;
    // turn raw filenames like "configuration.md" / "sdk/README.md" into "Configuration"
    if (/\.md\/?$/i.test(t.trim()) || (href && /\.md(#|$)/i.test(href))) {
      t = t.replace(/^.*\//, "").replace(/\.md\/?$/i, "").replace(/[-_]+/g, " ").trim();
      if (/^readme$/i.test(t)) t = "overview";
      t = t.replace(/\b\w/g, (c) => c.toUpperCase());
      return t;
    }
    return children;
  }
  if (Array.isArray(children)) {
    const joined = children.filter((c) => typeof c === "string").join("");
    if (joined && /\.md\/?$/i.test(joined.trim())) return prettyLinkLabel(joined, href);
  }
  return children;
}
function HeadingAnchor({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <span className="group/heading inline-flex items-center gap-1.5">
      {children}
      {id && (
        <a
          href={`#${id}`}
          className="opacity-0 group-hover/heading:opacity-100 transition-opacity text-ink-faint hover:text-accent -mt-0.5"
          aria-label={`Link to ${id}`}
        >
          <Hash size={14} weight="bold" />
        </a>
      )}
    </span>
  );
}

const components: Components = {
  h1: ({ children, ...props }) => (
    <h1 className="text-[1.75rem] font-semibold text-ink mt-10 mb-4 tracking-tight scroll-mt-20" id={props.id}>
      <HeadingAnchor id={props.id}>{children}</HeadingAnchor>
    </h1>
  ),
  h2: ({ children, ...props }) => (
    <h2 className="text-[1.4rem] font-semibold text-ink mt-8 mb-3 tracking-tight pb-2 border-b border-hairline scroll-mt-20" id={props.id}>
      <HeadingAnchor id={props.id}>{children}</HeadingAnchor>
    </h2>
  ),
  h3: ({ children, ...props }) => (
    <h3 className="text-[1.15rem] font-semibold text-ink mt-6 mb-2 tracking-tight scroll-mt-20" id={props.id}>
      <HeadingAnchor id={props.id}>{children}</HeadingAnchor>
    </h3>
  ),
  p: ({ children, ...props }) => (
    <p className="mb-4 leading-[1.75] text-zinc-300" {...props}>
      {children}
    </p>
  ),
  a: ({ children, href, ...props }) => (
    <a
      href={href}
      className="font-medium text-lime-200/90 underline decoration-lime-400/25 underline-offset-[3px] hover:text-lime-200 hover:decoration-lime-300 transition-colors"
      target={href?.startsWith("http") ? "_blank" : undefined}
      rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
      {...props}
    >
      {prettyLinkLabel(children, href)}
    </a>
  ),
  ul: ({ children, ...props }) => (
    <ul className="mb-5 space-y-2.5 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 pl-10 list-disc marker:text-lime-400/60" {...props}>
      {children}
    </ul>
  ),
  ol: ({ children, ...props }) => (
    <ol className="mb-5 space-y-2.5 rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 pl-10 list-decimal marker:text-lime-400/60" {...props}>
      {children}
    </ol>
  ),
  li: ({ children, ...props }) => (
    <li className="leading-[1.7] text-zinc-300 pl-1" {...props}>
      {children}
    </li>
  ),
  blockquote: ({ children, ...props }) => {
    return (
      <blockquote
        className="docs-callout my-5 rounded-r-xl border border-white/10 border-l-2 border-l-lime-400 bg-lime-400/[0.04] px-4 py-3 text-ink-muted not-italic [&>p]:mb-0"
        {...props}
      >
        {children}
      </blockquote>
    );
  },
  pre: ({ children, ...props }) => {
    const codeText = extractCodeText(children);
    let lang = "code";
    const walk = (n: React.ReactNode): void => {
      if (lang !== "code") return;
      if (Array.isArray(n)) { n.forEach(walk); return; }
      if (n && typeof n === "object" && "props" in n) {
        const p = (n as { props: Record<string, unknown> }).props;
        const c = p.className;
        if (typeof c === "string") {
          const m = c.match(/language-([\w+-]+)/);
          if (m) lang = m[1];
        }
        if (p.children) walk(p.children as React.ReactNode);
      }
    };
    walk(children);
    return (
      <div className="relative group/code my-5 overflow-hidden rounded-xl border border-white/10 bg-[#0D0D10]">
        <div className="flex items-center gap-2 border-b border-white/[0.07] bg-white/[0.02] px-4 py-2">
          <span className="w-2 h-2 rounded-full bg-red-500/60" />
          <span className="w-2 h-2 rounded-full bg-amber-500/60" />
          <span className="w-2 h-2 rounded-full bg-green-500/60" />
          <span className="ml-2 font-mono text-[10.5px] tracking-widest text-zinc-600 uppercase">{lang}</span>
          <CopyButton text={codeText} />
        </div>
        <pre
          className="p-4 overflow-x-auto text-[13px] leading-[1.7] font-mono"
          {...props}
        >
          {children}
        </pre>
      </div>
    );
  },
  code: ({ inline, className, children, ...props }: any) => {
    if (inline) {
      return (
        <code
          className="bg-surface-2 border border-hairline rounded px-1.5 py-0.5 text-[0.875em] text-ink font-mono"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  table: ({ children, ...props }) => (
    <div className="my-5 overflow-x-auto rounded-xl border border-white/10">
      <table className="w-full text-sm border-collapse" {...props}>
        {children}
      </table>
    </div>
  ),
  th: ({ children, ...props }) => (
    <th
      className="text-left py-2 px-3 bg-surface-2 border-b border-hairline-strong font-semibold text-ink"
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ children, ...props }) => (
    <td
      className="py-2 px-3 border-b border-hairline text-ink-muted"
      {...props}
    >
      {children}
    </td>
  ),
};

function MarkdownRendererInner({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
      {content}
    </ReactMarkdown>
  );
}

const MarkdownRenderer = memo(MarkdownRendererInner);
export default MarkdownRenderer;
