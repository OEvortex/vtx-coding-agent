import { useMemo } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { CodeBlock } from "@/components/CodeBlock";
import {
  FileReferenceChip,
  isFilePatternReference,
  isLikelyFilePath,
} from "@/components/FileReferenceChip";
import { cn } from "@/lib/utils";

import "katex/dist/katex.min.css";

interface MarkdownTextRendererProps {
  children: string;
  className?: string;
  highlightCode?: boolean;
  onOpenFilePreview?: (path: string) => void;
}

export default function MarkdownTextRenderer({
  children,
  className,
  highlightCode = true,
  onOpenFilePreview,
}: MarkdownTextRendererProps) {
  const components = useMemo<Components>(
    () => ({
      code({ className: cls, children: kids, ...props }) {
        const match = /language-(\w+)/.exec(cls || "");
        if (match) {
          const code = String(kids).replace(/\n$/, "");
          return (
            <CodeBlock
              language={match[1]}
              code={code}
              className="my-3"
              highlight={highlightCode}
            />
          );
        }
        const raw = String(kids).replace(/\n$/, "");
        if (isLikelyFilePath(raw)) {
          return <FileReferenceChip path={raw} onOpen={onOpenFilePreview} />;
        }
        return (
          <code
            className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]"
            {...props}
          >
            {kids}
          </code>
        );
      },
      pre({ children }) {
        return <>{children}</>;
      },
      a({ href, children: markdownChildren, ...props }) {
        const text = typeof markdownChildren === "string" ? markdownChildren : "";
        if (isLikelyFilePath(text)) {
          return <FileReferenceChip path={text} onOpen={onOpenFilePreview} />;
        }
        return (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-blue-500 underline underline-offset-2 hover:text-blue-600 dark:text-blue-300 dark:hover:text-blue-200"
            {...props}
          >
            {markdownChildren}
          </a>
        );
      },
    }),
    [highlightCode, onOpenFilePreview],
  );

  return (
    <div
      className={cn(
        "markdown-content prose max-w-none dark:prose-invert",
        "prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-semibold prose-headings:tracking-tight",
        "prose-h1:text-lg prose-h2:text-base prose-h3:text-sm",
        "prose-p:my-2",
        "prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5",
        "prose-blockquote:my-3 prose-blockquote:border-l-2",
        "prose-a:text-blue-500 prose-a:underline-offset-2",
        "prose-pre:my-0 prose-pre:bg-transparent prose-pre:p-0",
        "prose-code:before:content-none prose-code:after:content-none prose-code:font-normal",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
