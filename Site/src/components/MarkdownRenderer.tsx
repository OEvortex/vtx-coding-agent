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
      className="absolute top-2 right-2 p-1.5 rounded-md text-ink-faint hover:text-ink hover:bg-surface-2 transition-all cursor-pointer opacity-0 group-hover/code:opacity-100 z-10"
      title="Copy code"
      aria-label="Copy code"
    >
      {copied ? <Check size={13} weight="bold" /> : <Copy size={13} weight="regular" />}
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
    <p className="mb-4 leading-[1.7] text-ink-muted" {...props}>
      {children}
    </p>
  ),
  a: ({ children, href, ...props }) => (
    <a
      href={href}
      className="text-accent underline decoration-accent/30 underline-offset-[3px] hover:decoration-accent transition-colors"
      target={href?.startsWith("http") ? "_blank" : undefined}
      rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ children, ...props }) => (
    <ul className="list-disc pl-6 mb-4 space-y-1.5 marker:text-ink-faint" {...props}>
      {children}
    </ul>
  ),
  ol: ({ children, ...props }) => (
    <ol className="list-decimal pl-6 mb-4 space-y-1.5 marker:text-ink-faint" {...props}>
      {children}
    </ol>
  ),
  li: ({ children, ...props }) => (
    <li className="leading-[1.7] text-ink-muted" {...props}>
      {children}
    </li>
  ),
  blockquote: ({ children, ...props }) => (
    <blockquote
      className="border-l-2 border-accent pl-4 my-4 text-ink-muted italic"
      {...props}
    >
      {children}
    </blockquote>
  ),
  pre: ({ children, ...props }) => {
    const codeText = extractCodeText(children);
    return (
      <div className="relative group/code my-4">
        <CopyButton text={codeText} />
        <pre
          className="bg-[#111114] border border-hairline rounded-lg p-4 overflow-x-auto text-[13px] leading-[1.65] font-mono"
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
    <div className="my-4 overflow-x-auto">
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
