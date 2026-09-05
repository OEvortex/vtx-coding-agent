import { useState } from "react";
import { Copy, Check, Terminal, Rocket } from "@phosphor-icons/react";

const TABS = [
  { id: "uv", label: "uv", cmd: "uv tool install vtx-coding-agent" },
  { id: "curl", label: "curl", cmd: "curl -fsSL https://raw.githubusercontent.com/OEvortex/vtx-coding-agent/main/scripts/install.sh | bash" },
  { id: "run", label: "run", cmd: 'vtx -p "Write unit tests for src/ai/agent/tools/task.py"' },
];

export default function InstallRave() {
  const [tab, setTab] = useState(TABS[0]);
  const [ok, setOk] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(tab.cmd); } catch { /* noop */ }
    setOk(true);
    setTimeout(() => setOk(false), 1400);
  };
  return (
    <section id="install" className="rave-section">
      <div className="rave-beam" aria-hidden="true" />
      <div className="relative mx-auto max-w-[1100px] text-center">
        <div className="mono-caption-bright">04 — 30 SECONDS TO LIFTOFF</div>
        <h2 className="rave-title mt-4">COPY. PASTE.<br />SHIP.</h2>
        <div className="mx-auto mt-8 max-w-[760px] overflow-hidden rounded-2xl border border-lime-400/25 bg-black/70 text-left shadow-[0_0_80px_rgba(163,230,53,0.15)] backdrop-blur">
          <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2.5">
            {TABS.map((t) => (
              <button key={t.id} onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1.5 font-mono text-[12px] transition ${tab.id === t.id ? "bg-lime-400 text-black font-bold" : "text-zinc-400 hover:text-white hover:bg-white/5"}`}>
                {t.label}
              </button>
            ))}
            <span className="ml-auto hidden sm:flex items-center gap-1.5 font-mono text-[11px] text-zinc-600"><Terminal size={13} /> zsh</span>
          </div>
          <div className="flex items-center gap-3 px-5 py-5 font-mono text-[13px] sm:text-[14px]">
            <span className="text-lime-300">$</span>
            <code className="flex-1 break-all text-zinc-100">{tab.cmd}</code>
            <button onClick={copy} className="rave-copy" aria-label="copy">
              {ok ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <a className="btn-mega" href="https://github.com/OEvortex/vtx-coding-agent" target="_blank" rel="noreferrer"><Rocket size={16} weight="fill" /> GITHUB — STAR IT</a>
          <a className="btn-ghostmega" href="/features/">ALL FEATURES →</a>
        </div>
        <p className="mt-5 font-mono text-[11px] text-zinc-600">python 3.12+ · linux / mac / windows · apache-2.0 · no telemetry bullshit</p>
      </div>
    </section>
  );
}
