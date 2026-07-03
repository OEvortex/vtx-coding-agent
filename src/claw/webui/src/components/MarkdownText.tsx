import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import MarkdownTextRenderer from "@/components/MarkdownTextRenderer";

interface MarkdownTextProps {
  children: string;
  className?: string;
  streaming?: boolean;
  onOpenFilePreview?: (path: string) => void;
}

export function preloadMarkdownText(): void {
  // No-op: renderer is eagerly imported
}

const SHORT_STREAM_COMMIT_MS = 20;
const MEDIUM_STREAM_COMMIT_MS = 40;
const LONG_STREAM_COMMIT_MS = 80;

export function MarkdownText({
  children,
  className,
  streaming = false,
  onOpenFilePreview,
}: MarkdownTextProps) {
  const renderedSource = useStreamingMarkdownSource(children, streaming);
  const highlightCode = renderedSource === children;

  return (
    <MarkdownTextRenderer
      className={className}
      highlightCode={highlightCode}
      onOpenFilePreview={onOpenFilePreview}
    >
      {renderedSource}
    </MarkdownTextRenderer>
  );
}

function useStreamingMarkdownSource(source: string, streaming: boolean): string {
  const [renderedSource, setRenderedSource] = useState(source);
  const latestSourceRef = useRef(source);
  const renderedSourceRef = useRef(source);
  const timerRef = useRef<number | null>(null);

  const clearPendingCommit = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const commitSource = useCallback((next: string) => {
    if (renderedSourceRef.current === next) return;
    renderedSourceRef.current = next;
    setRenderedSource(next);
  }, []);

  const scheduleCommit = useCallback(() => {
    if (timerRef.current !== null) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      commitSource(latestSourceRef.current);
    }, streamingCommitDelay(latestSourceRef.current.length));
  }, [commitSource]);

  latestSourceRef.current = source;

  useLayoutEffect(() => {
    latestSourceRef.current = source;
    if (!streaming) {
      clearPendingCommit();
      commitSource(source);
    }
  }, [clearPendingCommit, commitSource, source, streaming]);

  useEffect(() => {
    latestSourceRef.current = source;
    if (!streaming) return;
    scheduleCommit();
  }, [scheduleCommit, source, streaming]);

  useEffect(() => clearPendingCommit, [clearPendingCommit]);

  return renderedSource;
}

function streamingCommitDelay(length: number): number {
  if (length > 24_000) return LONG_STREAM_COMMIT_MS;
  if (length > 8_000) return MEDIUM_STREAM_COMMIT_MS;
  return SHORT_STREAM_COMMIT_MS;
}
