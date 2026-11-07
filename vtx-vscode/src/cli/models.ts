import { execFile } from "child_process";
import { cliCommand } from "./locate";

export interface ModelVariant {
  value: string; // model uid the ACP `model` option accepts
  name: string; // effort label, e.g. "Medium", "High", "No Thinking"
  costTier?: string;
  costSummary?: string;
  longContextCostSummary?: string;
  description?: string;
  maxContextTokens?: number;
  maxOutputTokens?: number;
  promotion?: string;
  isNew?: boolean;
  isBeta?: boolean;
}

export interface ModelFamily {
  id: string; // stable family id (slug/uid)
  name: string; // family label, e.g. "Claude Sonnet 5"
  default: string; // default variant uid (the family's first/base option)
  variants: ModelVariant[];
  provider?: string; // provider slug this family's models belong to
}

export interface ModelProvider {
  slug: string;
  name?: string;
}

interface ModelsJson {
  provider_filter?: string;
  providers?: { slug?: string; name?: string; display_name?: string }[];
  families?: {
    family_label?: string;
    family_uid?: string;
    slug?: string;
    provider?: string;
    variants?: {
      model_uid?: string;
      label?: string;
      cost_tier?: string;
      cost_summary?: string;
      long_context_cost_summary?: string;
      description?: string;
      max_context_tokens?: number;
      max_output_tokens?: number;
      promotion?: string;
      promo?: string;
      promotion_label?: string;
      is_promo?: boolean;
      is_new?: boolean;
      is_beta?: boolean;
    }[];
  }[];
}

interface ModelsResult {
  families: ModelFamily[];
  providers: ModelProvider[];
  providerFilter: string;
}

let cache: { at: number; result: ModelsResult } | undefined;
let pendingListing: Promise<ModelFamily[]> | undefined;
// Bumped by invalidateModelFamilies so a fetch already in flight cannot write
// its pre-change answer back into the cache.
let cacheGen = 0;
// variant uid -> family, so a session's flat currentValue can be resolved.
const familyByValue = new Map<string, ModelFamily>();

// Lists the account's models as families (with effort variants) via
// `vtx models list --format json`. Needs no ACP session, and the uids match
// what the ACP `model` config option accepts. Cached in-memory for a few minutes.
export function listModelFamilies(cliPath: string, env?: NodeJS.ProcessEnv): Promise<ModelFamily[]> {
  if (cache && Date.now() - cache.at < 300000) {
    return Promise.resolve(cache.result.families);
  }
  if (pendingListing) {
    return pendingListing;
  }
  const pending = (async () => {
    const gen = cacheGen;
    const result = await run(cliPath, env);
    if (gen !== cacheGen) {
      // Invalidated while this was in flight: serve the answer but do not let
      // it replace whatever the newer fetch cached.
      return result.families;
    }
    if (result.families.length) {
      setCache(result);
      return result.families;
    }
    // The CLI call failed or returned nothing: keep serving the last successful
    // families (even if now stale) so the dropdowns don't empty out on a
    // transient error.
    return cache?.result.families ?? result.families;
  })();
  pendingListing = pending;
  void pending.finally(() => {
    if (pendingListing === pending) {
      pendingListing = undefined;
    }
  });
  return pending;
}

// Families from the last successful fetch, synchronously (may be empty).
export function cachedFamilies(): ModelFamily[] {
  return cache?.result.families || [];
}

// The provider slug `ui.model_provider_filter` scopes pickers to, "" when the
// model list shows every provider.
export function cachedProviderFilter(): string {
  return cache?.result.providerFilter || "";
}

// Every configured provider the CLI reported, for pickers that must offer
// slugs which currently have no cached models.
export function cachedProviders(): ModelProvider[] {
  return cache?.result.providers || [];
}

// Drop the cached listing so the next read refetches (e.g. after /provider
// changes ui.model_provider_filter).
export function invalidateModelFamilies(): void {
  cacheGen++;
  cache = undefined;
  pendingListing = undefined;
  familyByValue.clear();
}

export function familyOf(value: string): ModelFamily | undefined {
  return familyByValue.get(value);
}

function setCache(result: ModelsResult): void {
  cache = { at: Date.now(), result };
  familyByValue.clear();
  for (const fam of result.families) {
    for (const v of fam.variants) {
      familyByValue.set(v.value, fam);
    }
  }
}

// "Claude Sonnet 5 Medium" + family "Claude Sonnet 5" -> "Medium".
// When the variant label is exactly the family name (the base, no-thinking
// variant), there is nothing left after stripping, so call it "Base".
function effortLabel(familyLabel: string, variantLabel: string): string {
  let e = variantLabel;
  if (familyLabel && variantLabel.toLowerCase().startsWith(familyLabel.toLowerCase())) {
    e = variantLabel.slice(familyLabel.length).trim();
  }
  return e || "Base";
}

// Best-effort ordering of thinking effort, lowest to highest.
function effortRank(name: string): number {
  const s = name.toLowerCase();
  if (/^base$|no\s*thinking|none/.test(s)) return 0;
  if (/x-?high|xhigh|extra/.test(s)) return 4;
  if (/\bmax\b/.test(s)) return 5;
  if (/high/.test(s)) return 3;
  if (/medium|\bmed\b/.test(s)) return 2;
  if (/low/.test(s)) return 1;
  return 2.5; // unknown efforts sit in the middle, keeping their original order
}

function run(cliPath: string, env?: NodeJS.ProcessEnv): Promise<ModelsResult> {
  return new Promise((resolve) => {
    const cmd = cliCommand(cliPath, ["models", "list", "--format", "json"]);
    execFile(
      cmd.file,
      cmd.args,
      { env, windowsHide: true, timeout: 15000, maxBuffer: 8 * 1024 * 1024, shell: cmd.shell },
      (err, stdout) => {
        if (err && !stdout) {
          resolve({ families: [], providers: [], providerFilter: "" });
          return;
        }
        try {
          const parsed = JSON.parse(stdout) as ModelsJson;
          const out: ModelFamily[] = [];
          for (const fam of parsed.families || []) {
            const label = fam.family_label || fam.slug || "";
            const variants: ModelVariant[] = [];
            for (const v of fam.variants || []) {
              if (v.model_uid) {
                variants.push({
                  value: v.model_uid,
                  name: effortLabel(label, v.label || v.model_uid),
                  costTier: v.cost_tier,
                  costSummary: v.cost_summary,
                  longContextCostSummary: v.long_context_cost_summary,
                  description: v.description,
                  maxContextTokens: v.max_context_tokens,
                  maxOutputTokens: v.max_output_tokens,
                  promotion: v.promotion || v.promo || v.promotion_label || (v.is_promo ? "PROMO" : undefined),
                  isNew: v.is_new,
                  isBeta: v.is_beta
                });
              }
            }
            if (!variants.length) {
              continue;
            }
            // Sort effort variants low -> high; the lowest becomes the default.
            variants.sort((a, b) => effortRank(a.name) - effortRank(b.name));
            out.push({
              // family_uid first: vtx emits one family per model with slug set
              // to the provider, so slug alone would collide across a provider's
              // models and every row would select the first one.
              id: fam.family_uid || fam.slug || variants[0].value,
              name: label || variants[0].value,
              default: variants[0].value,
              variants,
              provider: fam.provider
            });
          }
          resolve({
            families: out,
            providers: (parsed.providers || [])
              .filter((p) => p.slug)
              .map((p) => ({ slug: p.slug as string, name: p.name || p.display_name })),
            providerFilter: typeof parsed.provider_filter === "string" ? parsed.provider_filter : ""
          });
        } catch {
          resolve({ families: [], providers: [], providerFilter: "" });
        }
      }
    );
  });
}
