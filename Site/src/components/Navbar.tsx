import { useState, useEffect } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { List, X, GithubLogo, Lightning } from "@phosphor-icons/react";

const navLinks = [
  { label: "ARSENAL", href: "#arsenal" },
  { label: "TOKEN WAR", href: "#install" },
  { label: "PROVIDERS", href: "#install" },
  { label: "DOCS", href: "/docs/" },
  { label: "FEATURES", href: "/features/" },
];

export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const reduce = useReducedMotion();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
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
      {/* top ticker */}
      <div className="nav-ticker" aria-hidden="true">
        <div className="nav-ticker-track">
          {Array.from({ length: 2 }).map((_, i) => (
            <span key={i}>
              ★ OPEN SOURCE · APACHE-2.0 · ~2.6K TOKENS · 50+ PROVIDERS · TUI + HEADLESS + SDK · NO BLOAT ·&nbsp;
            </span>
          ))}
        </div>
      </div>

      <motion.header
        initial={reduce ? false : { y: -16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className={`fixed top-6 left-0 right-0 z-40 transition-all duration-300 ${
          scrolled ? "top-2" : "top-6"
        }`}
      >
        <div className="mx-auto max-w-[1440px] px-3 sm:px-6">
          <div
            className={`nav-shell ${scrolled ? "nav-shell-scrolled" : ""}`}
          >
            <a href="#hero" className="flex items-center gap-2.5 group">
              <span className="nav-logo">
                <Lightning size={15} weight="fill" />
              </span>
              <span className="text-[17px] font-black tracking-tighter text-white">
                VTX
              </span>
              <span className="hidden sm:inline-flex rounded-full border border-lime-400/30 bg-lime-400/10 px-2 py-0.5 font-mono text-[10px] font-bold text-lime-300">
                v1.1.1
              </span>
            </a>

            <nav className="hidden lg:flex items-center gap-1">
              {navLinks.map((link) => (
                <a key={link.label} href={link.href} className="nav-link">
                  {link.label}
                </a>
              ))}
            </nav>

            <div className="flex items-center gap-2">
              <a
                href="https://github.com/OEvortex/vtx-coding-agent"
                target="_blank"
                rel="noopener noreferrer"
                className="hidden sm:inline-flex items-center gap-1.5 font-mono text-[12px] text-zinc-400 hover:text-lime-300 transition-colors"
                aria-label="GitHub"
              >
                <GithubLogo size={16} weight="fill" />
                <span className="font-bold">STAR</span>
              </a>
              <a
                href="#install"
                className="nav-cta"
              >
                <span>SEND IT →</span>
              </a>
              <button
                onClick={() => setMobileOpen(true)}
                className="lg:hidden grid place-items-center w-9 h-9 rounded-lg border border-white/10 text-zinc-300"
                aria-label="Open menu"
              >
                <List size={19} weight="bold" />
              </button>
            </div>
          </div>
        </div>
      </motion.header>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="lg:hidden fixed inset-0 z-50 rave-drawer"
          >
            <div className="flex items-center justify-between px-5 h-16 border-b border-white/10">
              <span className="font-black tracking-tighter text-xl">VTX<span className="text-lime-400">_</span></span>
              <button
                onClick={() => setMobileOpen(false)}
                className="grid place-items-center w-9 h-9 rounded-lg bg-lime-400 text-black"
                aria-label="Close menu"
              >
                <X size={18} weight="bold" />
              </button>
            </div>
            <nav className="flex flex-col px-5 pt-6 gap-1">
              {navLinks.map((link, i) => (
                <motion.a
                  key={link.label}
                  href={link.href}
                  onClick={() => setMobileOpen(false)}
                  initial={{ opacity: 0, x: 30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.05 * i }}
                  className="drawer-link"
                >
                  <span className="font-mono text-[11px] text-lime-400">0{i + 1}</span>
                  {link.label}
                </motion.a>
              ))}
            </nav>
            <div className="absolute bottom-0 left-0 right-0 p-5 space-y-2 border-t border-white/10 bg-black/60">
              <a href="#install" onClick={() => setMobileOpen(false)} className="btn-mega w-full justify-center">
                SEND IT →
              </a>
              <p className="text-center font-mono text-[10.5px] text-zinc-600">uv tool install vtx-coding-agent</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
