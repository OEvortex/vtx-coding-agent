import { motion } from "motion/react";

const PROVIDERS = ["OpenAI","Anthropic","Azure","DeepSeek","Copilot","Groq","Mistral","Together","Ollama","Zhipu","Gemini","Bedrock","vLLM","llama.cpp","Kimi","Qwen","Cerebras","Fireworks","Perplexity","xAI","Cohere","OpenRouter"];

export default function ProviderWall() {
  return (
    <section className="relative bg-[#070708] px-4 sm:px-8 py-20 overflow-hidden">
      <div className="mx-auto max-w-[1440px] text-center">
        <div className="mono-caption-bright">03 — 50+ PROVIDERS · BRING YOUR OWN KEY</div>
        <h2 className="insane-h2 mt-3">IF IT SPEAKS <span className="title-stroke-sm">OPENAI</span><br />VTX SPEAKS IT.</h2>
        <div className="provider-wall mt-10" aria-hidden="true">
          <div className="provider-track">
            {[...PROVIDERS, ...PROVIDERS].map((p, i) => (
              <span key={i} className="provider-chip">{p}</span>
            ))}
          </div>
          <div className="provider-track reverse">
            {[...PROVIDERS].reverse().concat(PROVIDERS).map((p, i) => (
              <span key={i} className="provider-chip alt">{p}</span>
            ))}
          </div>
        </div>
        <motion.div initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }}
          className="mx-auto mt-8 max-w-[720px] rounded-xl border border-white/10 bg-white/[0.02] p-4 text-left font-mono text-[12.5px] leading-relaxed text-zinc-400">
          <span className="text-zinc-600"># .vtx/providers/acme.yaml — no source edits</span><br />
          <span className="text-lime-300">slug:</span> acme <span className="text-zinc-600">·</span> <span className="text-lime-300">family:</span> openai_compat<br />
          <span className="text-lime-300">base_url:</span> https://ai.acme.internal/v1<br />
          <span className="text-cyan-300">$ vtx --provider acme -m acme-large</span>
        </motion.div>
      </div>
    </section>
  );
}
