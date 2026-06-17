import { useState, useEffect } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { List, X, GithubLogo, Terminal } from "@phosphor-icons/react";

const navLinks = [
  { label: "Why Vtx", href: "#why" },
  { label: "Capabilities", href: "#capabilities" },
  { label: "Docs", href: "/docs/" },
  { label: "Features", href: "/features/" },
];

export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const reduce = useReducedMotion();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  return (
    <>
      <motion.header
        initial={reduce ? false : { y: -8, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className={`fixed top-0 left-0 right-0 z-40 transition-colors duration-200 ${
          scrolled
            ? "bg-[#0A0A0B]/85 backdrop-blur-md border-b border-hairline"
            : "bg-transparent border-b border-transparent"
        }`}
      >
        <div className="max-w-[1400px] mx-auto h-14 px-5 sm:px-7 flex items-center justify-between">
          {/* Logo */}
          <a href="#hero" className="flex items-center gap-2.5 group">
            <span className="grid place-items-center w-7 h-7 rounded-md bg-accent text-[#0A0A0B]">
              <Terminal size={14} weight="bold" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-ink">
              Vtx
            </span>
            <span className="hidden sm:inline-block font-mono text-[10.5px] text-ink-faint tracking-tight">
              v0.4
            </span>
          </a>

          {/* Desktop links */}
          <nav className="hidden lg:flex items-center gap-1">
            {navLinks.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="px-3 py-1.5 text-[12.5px] text-ink-muted hover:text-ink transition-colors font-medium tracking-tight"
              >
                {link.label}
              </a>
            ))}
          </nav>

          {/* Right side */}
          <div className="flex items-center gap-2">
            <a
              href="https://github.com/OEvortex/vtx-coding-agent"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden sm:flex btn-ghost"
              aria-label="GitHub"
            >
              <GithubLogo size={14} weight="regular" />
              <span>Star</span>
            </a>
            <a
              href="https://github.com/OEvortex/vtx-coding-agent"
              target="_blank"
              rel="noopener noreferrer"
              className="btn-primary"
            >
              <span>Install</span>
            </a>
            <button
              onClick={() => setMobileOpen(true)}
              className="lg:hidden p-2 -mr-1 text-ink-muted hover:text-ink"
              aria-label="Open menu"
            >
              <List size={20} weight="bold" />
            </button>
          </div>
        </div>
      </motion.header>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="lg:hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              initial={reduce ? false : { x: "100%" }}
              animate={{ x: 0 }}
              exit={reduce ? undefined : { x: "100%" }}
              transition={{ type: "spring", stiffness: 300, damping: 32 }}
              className="lg:hidden fixed top-0 right-0 bottom-0 z-50 w-[280px] bg-[#0A0A0B] border-l border-hairline flex flex-col"
            >
              <div className="flex items-center justify-between h-14 px-5 border-b border-hairline">
                <span className="font-mono text-[11px] tracking-[0.16em] text-ink-muted uppercase">
                  Menu
                </span>
                <button
                  onClick={() => setMobileOpen(false)}
                  className="p-1.5 -mr-1 text-ink-muted hover:text-ink"
                  aria-label="Close menu"
                >
                  <X size={18} weight="bold" />
                </button>
              </div>
              <nav className="flex-1 px-3 py-4 flex flex-col gap-0.5">
                {navLinks.map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    onClick={() => setMobileOpen(false)}
                    className="px-3 py-2.5 text-[14px] text-ink hover:bg-surface rounded-md font-medium"
                  >
                    {link.label}
                  </a>
                ))}
              </nav>
              <div className="p-4 border-t border-hairline space-y-2">
                <a
                  href="https://github.com/kuutsav/vtx"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-primary w-full justify-center"
                >
                  Install
                </a>
                <a
                  href="https://github.com/kuutsav/vtx"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-secondary w-full justify-center"
                >
                  <GithubLogo size={14} weight="regular" />
                  <span>View on GitHub</span>
                </a>
              </div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
