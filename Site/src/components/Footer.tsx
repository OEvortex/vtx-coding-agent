import { ArrowUp, GithubLogo } from "@phosphor-icons/react";

const sections = [
  {
    title: "Product",
    links: [
      { label: "Why Vtx", href: "#why" },
      { label: "Capabilities", href: "#capabilities" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Documentation", href: "/docs/" },
      { label: "Full feature list", href: "/features/" },
      { label: "GitHub", href: "https://github.com/OEvortex/vtx-coding-agent" },
      { label: "PyPI package", href: "https://pypi.org/project/vtx-coding-agent/" },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "MIT license", href: "https://github.com/OEvortex/vtx-coding-agent/blob/main/LICENSE" },
      { label: "Changelog", href: "https://github.com/OEvortex/vtx-coding-agent/blob/main/CHANGELOG.md" },
      { label: "Issues", href: "https://github.com/OEvortex/vtx-coding-agent/issues" },
      { label: "Discussions", href: "https://github.com/OEvortex/vtx-coding-agent/discussions" },
    ],
  },
];

export default function Footer() {
  const handleScrollTop = () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <footer className="bg-canvas border-t border-hairline pt-16 pb-8 px-5 sm:px-7 relative">
      <div
        className="absolute inset-0 pointer-events-none"
        aria-hidden="true"
        style={{
          background:
            "radial-gradient(ellipse at 50% 100%, rgba(163, 230, 53, 0.02) 0%, transparent 50%)",
        }}
      />
      <div className="relative max-w-[1400px] mx-auto space-y-12">
        {/* Top row: brand + columns */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10">
          <div className="lg:col-span-4 space-y-5">
            <div className="flex items-center gap-2.5">
              <span className="grid place-items-center w-8 h-8 rounded-lg bg-accent text-[#0A0A0B]">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <polyline points="4 17 10 11 4 5" />
                  <line x1="12" y1="19" x2="20" y2="19" />
                </svg>
              </span>
              <span className="text-[16px] font-semibold tracking-tight text-ink">
                Vtx
              </span>
              <span className="font-mono text-[10.5px] text-ink-faint tracking-tight">
                v0.4.2
              </span>
            </div>
            <p className="text-[14px] text-ink-muted leading-[1.65] max-w-[36ch]">
              A minimal, transparent agentic coding harness for the terminal.
              Open source under the MIT license.
            </p>
            <a
              href="https://github.com/OEvortex/vtx-coding-agent"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-[13px] text-ink-muted hover:text-ink transition-colors"
            >
              <GithubLogo size={14} weight="regular" />
              <span>github.com/OEvortex/vtx-coding-agent</span>
            </a>
          </div>

          <div className="lg:col-span-8 grid grid-cols-2 sm:grid-cols-3 gap-8">
            {sections.map((section) => (
              <div key={section.title} className="space-y-4">
                <span className="font-mono text-[10.5px] tracking-[0.18em] text-ink-faint uppercase block">
                  {section.title}
                </span>
                <ul className="space-y-3">
                  {section.links.map((link) => (
                    <li key={link.label}>
                      <a
                        href={link.href}
                        target={link.href.startsWith("http") ? "_blank" : undefined}
                        rel={link.href.startsWith("http") ? "noopener noreferrer" : undefined}
                        className="text-[13.5px] text-ink-muted hover:text-ink transition-colors inline-flex items-center gap-1.5 group/link"
                      >
                        {link.label}
                        {link.href.startsWith("http") && (
                          <ArrowUp size={10} weight="bold" className="opacity-0 group-hover/link:opacity-100 transition-opacity" />
                        )}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom row */}
        <div className="pt-8 border-t border-hairline flex flex-col sm:flex-row items-center justify-between gap-4 text-[12px] text-ink-faint font-mono tracking-tight">
          <p>© 2026 OEvortex. Released under the MIT license.</p>
          <button
            onClick={handleScrollTop}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-hairline hover:border-hairline-strong hover:bg-surface transition-colors text-ink-muted"
            aria-label="Back to top of page"
          >
            <span>Back to top</span>
            <ArrowUp size={12} weight="bold" />
          </button>
        </div>
      </div>
    </footer>
  );
}
