import { Marquee } from "./Marquee";

/**
 * One marquee band of provider names (the real 50+ catalog, abbreviated
 * to the recognizable ones). Pauses on hover; static under reduced motion.
 */
export default function ProviderMarquee() {
  const providers = [
    "OpenAI",
    "Anthropic",
    "Azure",
    "GitHub Copilot",
    "DeepSeek",
    "Groq",
    "Mistral",
    "Together",
    "Ollama",
    "llama.cpp",
    "vLLM",
    "Zhipu",
    "OpenRouter",
    "Google",
    "Meta AI",
    "xAI",
    "Qwen",
    "Kimi",
  ];

  return (
    <div className="relative py-5 border-y border-hairline bg-canvas">
      <p className="mono-caption text-center mb-4">
        50+ built-in providers · OpenAI- &amp; Anthropic-compatible · local models
      </p>
      <Marquee speed={44}>
        <div className="flex items-center gap-10 pr-10">
          {providers.map((name) => (
            <span
              key={name}
              className="flex items-center gap-2.5 whitespace-nowrap text-[13px] font-medium text-zinc-500"
            >
              <span className="block w-1 h-1 rounded-full bg-accent/50" />
              {name}
            </span>
          ))}
        </div>
      </Marquee>
    </div>
  );
}
