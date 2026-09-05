import { ArrowUp, GithubLogo, Lightning, Copy, Check } from "@phosphor-icons/react";
import { useState } from "react";

const cols = [
  {
    title: "HARNESS",
    links: [
      { label: "The Arsenal", href: "#arsenal" },
      { label: "Token War", href: "#install" },
      { label: "Install", href: "#install" },
    ],
  },
  {
    title: "RESOURCES",
    links: [
      { label: "Documentation", href: "/docs/" },
      { label: "Full feature list", href: "/features/" },
      { label: "PyPI package", href: "https://pypi.org/project/vtx-coding-agent/" },
    ],
  },
  {
    title: "PROJECT",
    links: [
      { label: "GitHub", href: "https://github.com/OEvortex/vtx-coding-agent" },
      { label: "Issues", href: "https://github.com/OEvortex/vtx-coding-agent/issues" },
      { label: "Changelog", href: "https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md" },
    ],
  },
];

export default function Footer() {
  const [copied, setCopied] = useState(false);
  const cmd = "uv tool install vtx-coding-agent";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
    } catch { /* noop */ }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <footer className="footer-rave">
      {/* giant CTA */}
      <div className="mx-auto max-w-[1440px] px-4 sm:px-8 pt-16 sm:pt-24">
        <div className="footer-cta">
          <div className="flex items-center gap-2 font-mono text-[11px] tracking-[0.2em] text-lime-300">
            <Lightning size={13} weight="fill" /> LAST CALL — JOIN THE LEAN SIDE
          </div>
          <h2 className="footer-h2">STOP FEEDING<br />THE BLOAT<span className="text-lime-400">.</span></h2>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button onClick={copy} className="footer-copy">
              <span className="text-lime-300">$</span>
              <code>{cmd}</code>
              {copied ? <Check size={15} className="text-lime-300" /> : <Copy size={15} className="text-zinc-500" />}
            </button>
            <a className="btn-mega" href="https://github.com/OEvortex/vtx-coding-agent" target="_blank" rel="noreferrer">
              <GithubLogo size={16} weight="fill" /> STAR ON GITHUB
            </a>
          </div>
        </div>

        {/* link grid */}
        <div className="mt-14 grid grid-cols-2 md:grid-cols-5 gap-8 border-t border-white/10 pt-10">
          <div className="col-span-2">
            <div className="flex items-center gap-2">
              <span className="nav-logo"><Lightning size={15} weight="fill" /></span>
              <span className="font-black text-lg tracking-tighter">VTX</span>
              <span className="font-mono text-[10px] text-zinc-600">v1.1.1 · apache-2.0</span>
            </div>
            <p className="mt-3 max-w-[32ch] text-[13.5px] leading-relaxed text-zinc-500">
              Minimalist coding-agent harness. ~2.6k-token loop. TUI + headless CLI + Python SDK. Your context stays yours.
            </p>
            <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-lime-400/25 bg-lime-400/5 px-3 py-1.5 font-mono text-[11px] text-lime-300">
              <span className="dot-pulse" /> all systems lean
            </div>
          </div>
          {cols.map((c) => (
            <div key={c.title}>
              <div className="font-mono text-[10px] tracking-[0.22em] text-zinc-600">{c.title}</div>
              <ul className="mt-3 space-y-2.5">
                {c.links.map((l) => (
                  <li key={l.label}>
                    <a href={l.href} className="footer-link">{l.label}</a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {/* giant outline */}
      <div className="footer-giant" aria-hidden="true">VTX—VTX—VTX</div>

      <div className="relative border-t border-white/10">
        <div className="mx-auto max-w-[1440px] px-4 sm:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3 font-mono text-[11px] text-zinc-600">
          <span>© 2026 OEVORTEX · BUILT LEAN · SHIPPED MEAN</span>
          <div className="flex items-center gap-3">
            <span className="hidden sm:inline">python 3.12+ · linux/mac/win</span>
            <button
              onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-zinc-400 hover:text-black hover:bg-lime-400 hover:border-lime-400 transition-all"
              aria-label="Back to top"
            >
              TOP <ArrowUp size={12} weight="bold" />
            </button>
          </div>
        </div>
      </div>
    </footer>
  );
}
