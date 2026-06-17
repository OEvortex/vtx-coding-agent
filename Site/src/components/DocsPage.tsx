import { useState, useEffect, useMemo, useRef, useCallback, memo } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  X,
  List,
  MagnifyingGlass,
  FileText,
  ArrowRight,
  Terminal,
  ArrowLeft,
  CaretRight,
  House,
} from "@phosphor-icons/react";
import docs, {
  categories,
  getDocsByCategory,
  getDocById,
} from "../content/docs";
import MarkdownRenderer from "./MarkdownRenderer";

function extractHeadings(markdown: string): { id: string; text: string; level: number }[] {
  const headings: { id: string; text: string; level: number }[] = [];
  const lines = markdown.split("\n");
  for (const line of lines) {
    const match = line.match(/^(#{1,3})\s+(.+)$/);
    if (match) {
      const level = match[1].length;
      const text = match[2].replace(/[*_`]/g, "").trim();
      const id = text
        .toLowerCase()
        .replace(/[^\w\s-]/g, "")
        .replace(/\s+/g, "-");
      headings.push({ id, text, level });
    }
  }
  return headings;
}

const searchIndex = docs.map((d) => ({
  doc: d,
  titleLow: d.title.toLowerCase(),
  descLow: d.description.toLowerCase(),
  catLow: d.category.toLowerCase(),
  contentLow: d.content.toLowerCase(),
}));

const SearchModal = memo(function SearchModal({
  open,
  onClose,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  const results = useMemo(() => {
    if (!query.trim()) return docs.slice(0, 6);
    const q = query.toLowerCase();

    const scored = searchIndex
      .map((entry) => {
        let score = 0;
        if (entry.titleLow === q) score += 100;
        else if (entry.titleLow.startsWith(q)) score += 80;
        else if (entry.titleLow.includes(q)) score += 50;
        if (entry.descLow.includes(q)) score += 20;
        if (entry.catLow.includes(q)) score += 10;
        if (entry.contentLow.includes(q)) score += 5;
        return { doc: entry.doc, score };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score);

    return scored.map((item) => item.doc);
  }, [query]);

  if (!open) return null;

  return (
    <motion.div
      className="fixed inset-0 z-[60] flex items-start justify-center pt-[15vh]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <motion.div
        className="relative w-full max-w-xl mx-4 bg-canvas border border-hairline rounded-lg shadow-2xl"
        initial={typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? {} : { opacity: 0, y: -20, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? {} : { opacity: 0, y: -20, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 400, damping: 30 }}
      >
        <div className="flex items-center gap-3 px-4 border-b border-hairline">
          <MagnifyingGlass size={16} className="text-ink-faint shrink-0" />
          <input
            ref={inputRef}
            type="text"
            placeholder="Search documentation..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 py-3.5 text-sm bg-transparent outline-none text-ink placeholder:text-ink-faint"
          />
          <kbd className="hidden sm:block text-[10px] font-mono text-ink-faint border border-hairline rounded px-1.5 py-0.5">
            ESC
          </kbd>
        </div>
        <div className="max-h-[50vh] overflow-y-auto py-2">
          {results.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-ink-faint">
              No results found.
            </div>
          ) : (
            results.map((doc) => (
              <button
                key={doc.id}
                onClick={() => {
                  onSelect(doc.id);
                  onClose();
                }}
                className="w-full text-left px-4 py-3 hover:bg-surface transition-colors flex items-start gap-3 group cursor-pointer"
              >
                <FileText size={14} className="text-ink-faint mt-0.5 shrink-0 group-hover:text-ink transition-colors" />
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-ink truncate">
                    {doc.title}
                  </div>
                  <div className="text-[11px] text-ink-faint mt-0.5 truncate">
                    {doc.category} &middot; {doc.description}
                  </div>
                </div>
                <ArrowRight size={14} className="text-ink-faint ml-auto mt-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
            ))
          )}
        </div>
        <div className="flex items-center justify-between px-4 py-2 border-t border-hairline text-[10px] font-mono text-ink-faint">
          <span>{results.length} result{results.length !== 1 ? "s" : ""}</span>
          <div className="flex items-center gap-2">
            <span>Navigate with</span>
            <kbd className="border border-hairline rounded px-1 py-0.5">&uarr;&darr;</kbd>
            <span>Select with</span>
            <kbd className="border border-hairline rounded px-1 py-0.5">&crarr;</kbd>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
});

const SidebarContent = memo(function SidebarContent({
  activeDocId,
  onSelect,
  onSearchOpen,
}: {
  activeDocId: string;
  onSelect: (id: string) => void;
  onSearchOpen: () => void;
}) {
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set(categories));

  const toggleCat = (cat: string) => {
    setExpandedCats((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  return (
    <div className="space-y-5">
      <a
        href="/"
        className="flex items-center gap-1.5 text-[11px] text-ink-muted hover:text-ink transition-colors"
      >
        <ArrowLeft size={12} weight="bold" />
        Back to home
      </a>

      <button
        onClick={onSearchOpen}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 border border-hairline text-ink-faint text-xs rounded-md hover:border-hairline-strong hover:text-ink transition-colors cursor-pointer group"
      >
        <MagnifyingGlass size={13} className="shrink-0" />
        <span className="flex-1 text-left">Search docs...</span>
        <kbd className="hidden sm:block text-[9px] font-mono border border-hairline rounded px-1 py-0.5 text-ink-faint group-hover:text-ink-muted">
          /
        </kbd>
      </button>

      <div className="border-b border-hairline pb-4">
        <div className="flex items-center gap-2">
          <span className="grid place-items-center w-7 h-7 rounded-md bg-accent text-[#0A0A0B]">
            <Terminal size={14} weight="bold" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold text-ink tracking-tight">
              Vtx docs
            </h2>
            <p className="font-mono text-[10.5px] text-ink-faint tracking-tight">
              v0.4.2
            </p>
          </div>
        </div>
      </div>

      {categories.map((cat) => {
        const catDocs = getDocsByCategory(cat);
        const isExpanded = expandedCats.has(cat);
        return (
          <div key={cat} className="space-y-0.5">
            <button
              onClick={() => toggleCat(cat)}
              className="w-full flex items-center justify-between px-2 py-1.5 text-[10.5px] font-mono text-ink-faint font-medium tracking-[0.18em] uppercase hover:text-ink transition-colors cursor-pointer"
            >
              <span>{cat}</span>
              <motion.span
                animate={{ rotate: isExpanded ? 90 : 0 }}
                transition={{ duration: 0.15 }}
              >
                <CaretRight size={10} weight="bold" />
              </motion.span>
            </button>
            <AnimatePresence initial={false}>
              {isExpanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2, ease: "easeInOut" }}
                  className="overflow-hidden"
                >
                  <div className="space-y-0.5 pt-1">
                    {catDocs.map((doc) => {
                      const isActive = doc.id === activeDocId;
                      return (
                        <button
                          key={doc.id}
                          onClick={() => onSelect(doc.id)}
                          className={`w-full text-left px-3 py-1.5 text-[12.5px] rounded-md transition-colors cursor-pointer flex items-center gap-2 relative ${
                            isActive
                              ? "bg-accent/10 text-accent font-medium"
                              : "text-ink-muted hover:text-ink hover:bg-surface font-normal"
                          }`}
                        >
                          {isActive && (
                            <motion.div
                              layoutId="sidebar-active"
                              className="absolute left-0 top-1 bottom-1 w-[2px] bg-accent rounded-r"
                              transition={{ type: "spring", stiffness: 300, damping: 30 }}
                            />
                          )}
                          <span className="truncate">{doc.title}</span>
                        </button>
                      );
                    })}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
});

const TableOfContents = memo(function TableOfContents({
  headings,
  activeId,
}: {
  headings: { id: string; text: string; level: number }[];
  activeId: string;
}) {
  if (headings.length < 2) return null;

  return (
    <div className="space-y-3">
      <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase block pb-2 border-b border-hairline">
        On this page
      </span>
      <nav className="space-y-1 relative">
        {headings.map((h) => {
          const isActive = activeId === h.id;
          return (
            <a
              key={h.id}
              href={`#${h.id}`}
              className={`relative block text-[12px] leading-snug py-1.5 transition-colors pl-3.5 ${
                h.level === 3 ? "pl-6" : ""
              } ${
                isActive
                  ? "text-ink font-medium"
                  : "text-ink-faint hover:text-ink-muted"
              }`}
            >
              {isActive && (
                <motion.div
                  layoutId="toc-indicator"
                  className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-accent rounded-r"
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                />
              )}
              {h.text}
            </a>
          );
        })}
      </nav>
    </div>
  );
});

function QuickNav({
  onSelect,
  currentId,
}: {
  onSelect: (id: string) => void;
  currentId: string;
}) {
  const currentIndex = docs.findIndex((d) => d.id === currentId);
  const prev = currentIndex > 0 ? docs[currentIndex - 1] : null;
  const next = currentIndex < docs.length - 1 ? docs[currentIndex + 1] : null;

  if (!prev && !next) return null;

  return (
    <div className="flex items-stretch gap-3 mt-16 pt-8 border-t border-hairline">
      {prev ? (
        <button
          onClick={() => onSelect(prev.id)}
          className="flex-1 text-left px-4 py-4 border border-hairline rounded-lg hover:border-hairline-strong hover:bg-surface transition-colors cursor-pointer group"
        >
          <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase block mb-1.5">
            Previous
          </span>
          <span className="text-sm font-medium text-ink group-hover:text-accent transition-colors">
            {prev.title}
          </span>
        </button>
      ) : (
        <div className="flex-1" />
      )}
      {next ? (
        <button
          onClick={() => onSelect(next.id)}
          className="flex-1 text-right px-4 py-4 border border-hairline rounded-lg hover:border-hairline-strong hover:bg-surface transition-colors cursor-pointer group"
        >
          <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase block mb-1.5">
            Next
          </span>
          <span className="text-sm font-medium text-ink group-hover:text-accent transition-colors">
            {next.title}
          </span>
        </button>
      ) : (
        <div className="flex-1" />
      )}
    </div>
  );
}

const reduce =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default memo(function DocsPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [activeDocId, setActiveDocId] = useState("readme");
  const [activeHeading, setActiveHeading] = useState("");
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const target = e.target as HTMLElement;
        if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
        e.preventDefault();
        setSearchOpen(true);
      }
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSearchOpen(true);
      }
      if (e.key === "Escape") {
        setSearchOpen(false);
        setSidebarOpen(false);
        setTocOpen(false);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  useEffect(() => {
    const parseHash = () => {
      const hash = window.location.hash;
      const match = hash.match(/^#(.+)$/);
      if (match) {
        const id = match[1];
        if (getDocById(id)) setActiveDocId(id);
      } else {
        setActiveDocId("readme");
      }
    };
    parseHash();
    window.addEventListener("hashchange", parseHash);
    return () => window.removeEventListener("hashchange", parseHash);
  }, []);

  const activeDoc = useMemo(() => getDocById(activeDocId), [activeDocId]);
  const docContent = activeDoc?.content || "";

  useEffect(() => {
    if (activeDoc) {
      document.title = `${activeDoc.title} · Vtx docs`;
    } else {
      document.title = "Vtx documentation";
    }
  }, [activeDoc]);

  const headings = useMemo(
    () => (docContent ? extractHeadings(docContent) : []),
    [docContent]
  );

  const handleSelectDoc = useCallback((id: string) => {
    setActiveDocId(id);
    window.location.hash = `#${id}`;
    setSidebarOpen(false);
    contentRef.current?.scrollTo({ top: 0, behavior: "instant" });
  }, []);

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;

    let rafId: number;
    let lastHeading = "";

    const handleScroll = () => {
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const headingEls = container.querySelectorAll("h1[id], h2[id], h3[id]");
        let closest = "";
        let closestDist = Infinity;
        for (const el of headingEls) {
          const rect = el.getBoundingClientRect();
          const containerRect = container.getBoundingClientRect();
          const dist = Math.abs(rect.top - containerRect.top - 80);
          if (dist < closestDist) {
            closestDist = dist;
            closest = el.id;
          }
        }
        if (closest !== lastHeading) {
          lastHeading = closest;
          setActiveHeading(closest);
        }
      });
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", handleScroll);
      cancelAnimationFrame(rafId);
    };
  }, [activeDocId]);

  return (
    <div className="bg-canvas min-h-screen text-ink font-sans antialiased">
      {/* Top bar */}
      <header className="fixed top-0 left-0 right-0 z-40 bg-canvas/85 backdrop-blur-md border-b border-hairline h-14 flex items-center px-4 sm:px-6">
        <div className="flex items-center justify-between w-full max-w-[1600px] mx-auto">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="lg:hidden p-1.5 text-ink-muted hover:text-ink transition-colors cursor-pointer"
              aria-label="Toggle sidebar"
            >
              {sidebarOpen ? <X size={18} weight="bold" /> : <List size={18} weight="bold" />}
            </button>
            <div className="flex items-center gap-2.5 text-[12px] text-ink-muted">
              <a
                href="/"
                className="flex items-center gap-2 hover:text-ink transition-colors"
              >
                <span className="grid place-items-center w-6 h-6 rounded-md bg-accent text-[#0A0A0B]">
                  <Terminal size={12} weight="bold" />
                </span>
                <span className="font-semibold tracking-tight">Vtx</span>
              </a>
              <CaretRight size={10} className="text-ink-faint/40" />
              <a href="/docs/" className="hover:text-ink transition-colors text-ink-muted">
                Docs
              </a>
              {activeDoc && (
                <>
                  <CaretRight size={10} className="text-ink-faint/40" />
                  <span className="text-ink font-medium">{activeDoc.title}</span>
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <a
              href="/"
              className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-ink-muted hover:text-ink transition-colors"
            >
              <House size={13} weight="regular" />
              Home
            </a>
            <button
              onClick={() => setSearchOpen(true)}
              className="hidden sm:flex items-center gap-2 px-3 py-1.5 border border-hairline text-ink-faint text-[11.5px] font-mono rounded-md hover:border-hairline-strong hover:text-ink transition-colors cursor-pointer"
            >
              <MagnifyingGlass size={12} />
              Search
              <kbd className="text-[9px] border border-hairline rounded px-1 py-0.5 ml-1">/</kbd>
            </button>
            <button
              onClick={() => setTocOpen(!tocOpen)}
              className="xl:hidden p-1.5 text-ink-muted hover:text-ink transition-colors cursor-pointer"
              aria-label="Toggle table of contents"
            >
              <List size={18} weight="bold" />
            </button>
          </div>
        </div>
      </header>

      <AnimatePresence>
        {searchOpen && (
          <SearchModal
            open={searchOpen}
            onClose={() => setSearchOpen(false)}
            onSelect={handleSelectDoc}
          />
        )}
      </AnimatePresence>

      <div className="flex pt-14">
        <aside className="hidden lg:block fixed left-0 top-14 bottom-0 w-[280px] border-r border-hairline overflow-y-auto p-5 bg-canvas">
          <SidebarContent
            activeDocId={activeDocId}
            onSelect={handleSelectDoc}
            onSearchOpen={() => setSearchOpen(true)}
          />
        </aside>

        <AnimatePresence>
          {sidebarOpen && (
            <>
              <motion.div
                className="lg:hidden fixed inset-0 bg-black/60 z-40 top-14"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setSidebarOpen(false)}
              />
              <motion.aside
                className="lg:hidden fixed left-0 top-14 bottom-0 w-[280px] z-50 border-r border-hairline overflow-y-auto p-5 bg-canvas shadow-2xl"
                initial={reduce ? {} : { x: -280 }}
                animate={{ x: 0 }}
                exit={reduce ? {} : { x: -280 }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}
              >
                <SidebarContent
                  activeDocId={activeDocId}
                  onSelect={handleSelectDoc}
                  onSearchOpen={() => {
                    setSidebarOpen(false);
                    setSearchOpen(true);
                  }}
                />
              </motion.aside>
            </>
          )}
        </AnimatePresence>

        <main
          ref={contentRef}
          className="flex-1 lg:ml-[280px] xl:mr-[220px] overflow-y-auto h-[calc(100vh-56px)] scroll-smooth"
        >
          {activeDoc ? (
            <div className="max-w-3xl mx-auto px-6 sm:px-8 lg:px-12 py-10 overflow-hidden">
              <AnimatePresence mode="wait">
                <motion.div
                  key={activeDoc.id}
                  initial={reduce ? {} : { opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={reduce ? {} : { opacity: 0, y: -12 }}
                  transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                >
                  <div className="mb-10">
                    <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase block mb-3">
                      {activeDoc.category}
                    </span>
                    <h1 className="text-[32px] sm:text-[40px] font-semibold text-ink tracking-tight leading-[1.1]">
                      {activeDoc.title}
                    </h1>
                    <p className="text-[15px] text-ink-muted mt-3 leading-[1.6] max-w-[60ch]">
                      {activeDoc.description}
                    </p>
                    <div className="h-px bg-hairline mt-8" />
                  </div>

                  <div className="doc-markdown">
                    <MarkdownRenderer content={docContent} />
                  </div>
                </motion.div>
              </AnimatePresence>

              <QuickNav onSelect={handleSelectDoc} currentId={activeDocId} />

              <div className="mt-12 pt-6 border-t border-hairline flex items-center justify-between text-[10.5px] font-mono text-ink-faint">
                <span>Last updated: Vtx v0.4.2</span>
                <span>Open source under the MIT license</span>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-ink-faint text-sm">
              Select a document from the sidebar.
            </div>
          )}
        </main>

        <aside className="hidden xl:block fixed right-0 top-14 bottom-0 w-[220px] border-l border-hairline overflow-y-auto p-5 bg-canvas">
          <TableOfContents headings={headings} activeId={activeHeading} />
        </aside>

        <AnimatePresence>
          {tocOpen && (
            <>
              <motion.div
                className="xl:hidden fixed inset-0 bg-black/60 z-40 top-14"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setTocOpen(false)}
              />
              <motion.aside
                className="xl:hidden fixed right-0 top-14 bottom-0 w-[220px] z-50 border-l border-hairline overflow-y-auto p-5 bg-canvas shadow-2xl"
                initial={reduce ? {} : { x: 220 }}
                animate={{ x: 0 }}
                exit={reduce ? {} : { x: 220 }}
                transition={{ type: "spring", stiffness: 300, damping: 30 }}
              >
                <TableOfContents headings={headings} activeId={activeHeading} />
              </motion.aside>
            </>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
});
