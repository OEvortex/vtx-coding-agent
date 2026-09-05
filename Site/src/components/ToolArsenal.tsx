import { motion } from "motion/react";
import { Knife, ShieldCheck, Brain, Plug, TerminalWindow, UsersThree } from "@phosphor-icons/react";

const TOOLS = [
  { icon: Knife, name: "read / edit / write", desc: "Surgical file ops. Paginate, patch precisely, never nuke.", tag: "core", hot: true },
  { icon: TerminalWindow, name: "bash", desc: "Gated shell. Destructive cmds blocked unless you say so.", tag: "gated" },
  { icon: Brain, name: "task + goal", desc: "Sub-agents stream back. Goals persist with audit + checkpoints.", tag: "agentic", hot: true },
  { icon: ShieldCheck, name: "prompt / auto", desc: "Alt+Ctrl+P flips permission mode live in the TUI.", tag: "safe" },
  { icon: Plug, name: "skill + web + mcp", desc: "Modular skills, Exa neural search, full MCP extension bus.", tag: "extend" },
  { icon: UsersThree, name: "ask_user + handoff", desc: "Clarify when stuck. Shift+Tab cycles review / audit agents.", tag: "human" },
];

export default function ToolArsenal() {
  return (
    <section id="arsenal" className="relative bg-[#070708] px-4 sm:px-8 py-20 sm:py-28 overflow-hidden">
      <div className="mx-auto max-w-[1440px]">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mono-caption-bright">01 — THE ARSENAL</div>
            <h2 className="insane-h2 mt-3">10 TOOLS.<br /><span className="text-lime-300">ZERO FAT.</span></h2>
          </div>
          <p className="max-w-[36ch] text-sm leading-relaxed text-zinc-500">
            No 47-tool junk drawer. Every tool earns its tokens. This is the entire attack surface.
          </p>
        </div>

        <div className="mt-10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {TOOLS.map((t, i) => (
            <motion.div
              key={t.name}
              initial={{ opacity: 0, y: 18 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ delay: i * 0.06 }}
              className="arsenal-card group"
            >
              <div className="flex items-center justify-between">
                <t.icon size={22} weight="duotone" className="text-lime-300" />
                <span className="font-mono text-[10px] tracking-[0.2em] text-zinc-600">/{t.tag}</span>
              </div>
              <div className="mt-5 font-mono text-[15px] font-bold text-zinc-100">{t.name}</div>
              <p className="mt-2 text-[13.5px] leading-relaxed text-zinc-500">{t.desc}</p>
              {t.hot && <div className="hot-bar" aria-hidden="true" />}
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
