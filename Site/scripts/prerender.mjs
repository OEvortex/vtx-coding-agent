/**
 * Prerender per-route meta tags into static HTML files.
 *
 * After `vite build`, we copy dist/index.html into:
 *   - dist/index.html         (home, already has landing meta)
 *   - dist/features/index.html (with /features/ meta pre-baked)
 *   - dist/docs/index.html    (with /docs/ meta pre-baked)
 *
 * The React app's useRouteMeta hook overwrites these on hydration with
 * the same values, so this is a no-op for users. For crawlers without JS
 * (Bing, archive.org, social card unfurls in some clients), the right
 * <title>, <meta>, OG tags, and JSON-LD are already in the static HTML.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST = join(__dirname, "..", "dist");

const SITE_URL = "https://agentjarvis-v2.netlify.app";

const ROUTES = [
  {
    path: "",
    schemaType: "WebPage",
    title: "Vtx: Agentic coding harness for your terminal",
    description:
      "Vtx is an open-source agentic coding harness for developers, builders, and founders. 120+ tools, MCP, ACP, hybrid memory, extensions, watchers, sandboxed execution. Request access to the paid private beta.",
    canonical: `${SITE_URL}/`,
    ogTitle: "Vtx: Agentic coding harness",
    ogDescription:
      "5 core tools, 11+ LLM providers, TUI, headless CLI, MCP, ACP, hybrid memory, extensions, watchers, sandboxed execution.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "WebPage",
      "@id": `${SITE_URL}/#webpage`,
      "url": `${SITE_URL}/`,
      "name": "Vtx: Agentic coding harness",
      "isPartOf": { "@id": `${SITE_URL}/#website` },
      "about": { "@id": `${SITE_URL}/#software` },
      "inLanguage": "en-US",
    },
  },
  {
    path: "features",
    schemaType: "ItemList",
    title: "Features - Vtx | 120+ tools, 116 skills, MCP, extensions",
    description:
      "Complete feature reference for Vtx: four interfaces, 120+ tools, 116 skills, six agent classes, 5-layer memory, MCP client, ACP server, plugin compatibility bridge, and sandboxed execution.",
    canonical: `${SITE_URL}/features/`,
    ogTitle: "Vtx: Full feature reference",
    ogDescription:
      "120+ tools, 116 skills across 26 categories, 6 agent classes, MCP, ACP, plugin compatibility. The complete Vtx feature surface.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "ItemList",
      "name": "Vtx Features",
      "url": `${SITE_URL}/features/`,
      itemListElement: [
        { "@type": "ListItem", "position": 1, "name": "Four User Interfaces (TUI, WebUI, RPC, ACP)" },
        { "@type": "ListItem", "position": 2, "name": "120+ Tools (39 core + 84 extension)" },
        { "@type": "ListItem", "position": 3, "name": "5-Layer Memory Architecture" },
        { "@type": "ListItem", "position": 4, "name": "116 Skills across 26 Categories" },
        { "@type": "ListItem", "position": 5, "name": "MCP & ACP Protocol Support" },
        { "@type": "ListItem", "position": 6, "name": "Claude Code & OpenAI Codex Plugin Compatibility" },
      ],
    },
  },
  {
    path: "docs",
    schemaType: "TechArticle",
    title: "Documentation - Vtx",
    description:
      "Official Vtx documentation: setup, extension system, custom tools, agents, memory, hooks, RPC, ACP, sandbox, skills, and architecture reference.",
    canonical: `${SITE_URL}/docs/`,
    ogTitle: "Vtx: Documentation",
    ogDescription:
      "Setup, extension system, custom tools, memory, hooks, RPC, ACP, sandbox - every Vtx subsystem documented.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "TechArticle",
      "headline": "Vtx Documentation",
      "name": "Vtx Documentation",
      "description":
        "Official documentation for Vtx - the open-source agentic coding harness with 120+ tools, MCP, ACP, hybrid memory, and sandboxed execution.",
      proficiencyLevel: "Expert",
      author: { "@type": "Organization", "name": "OEvortex" },
      publisher: {
        "@type": "Organization",
        "name": "OEvortex",
        logo: { "@type": "ImageObject", "url": `${SITE_URL}/favicon-512x512.png` },
      },
      url: `${SITE_URL}/docs/`,
    },
  },
];

function escapeAttr(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function rewriteHtml(html, meta) {
  html = html.replace(/<title>[^<]*<\/title>/, `<title>${meta.title}</title>`);

  html = html.replace(
    /<meta\s+name="description"\s+content="[^"]*"\s*\/?>/,
    `<meta name="description" content="${escapeAttr(meta.description)}" />`
  );
  html = html.replace(
    /<meta\s+property="og:title"\s+content="[^"]*"\s*\/?>/,
    `<meta property="og:title" content="${escapeAttr(meta.ogTitle)}" />`
  );
  html = html.replace(
    /<meta\s+property="og:description"\s+content="[^"]*"\s*\/?>/,
    `<meta property="og:description" content="${escapeAttr(meta.ogDescription)}" />`
  );
  html = html.replace(
    /<meta\s+property="og:url"\s+content="[^"]*"\s*\/?>/,
    `<meta property="og:url" content="${meta.canonical}" />`
  );
  html = html.replace(
    /<meta\s+name="twitter:title"\s+content="[^"]*"\s*\/?>/,
    `<meta name="twitter:title" content="${escapeAttr(meta.ogTitle)}" />`
  );
  html = html.replace(
    /<meta\s+name="twitter:description"\s+content="[^"]*"\s*\/?>/,
    `<meta name="twitter:description" content="${escapeAttr(meta.ogDescription)}" />`
  );
  html = html.replace(
    /<link\s+rel="canonical"\s+href="[^"]*"\s*\/?>/,
    `<link rel="canonical" href="${meta.canonical}" />`
  );

  const jsonLdScript = `<script type="application/ld+json" data-route="prerender">\n${JSON.stringify(meta.jsonLd, null, 2)}\n</script>`;
  html = html.replace("</head>", `    ${jsonLdScript}\n  </head>`);

  return html;
}

function main() {
  const baseHtml = readFileSync(join(DIST, "index.html"), "utf8");
  console.log(`Read base dist/index.html (${baseHtml.length} bytes)`);

  for (const route of ROUTES) {
    if (!route.path) {
      console.log(`  ✓ /             (landing, already correct)`);
      continue;
    }
    const outDir = join(DIST, route.path);
    mkdirSync(outDir, { recursive: true });
    const outPath = join(outDir, "index.html");
    const html = rewriteHtml(baseHtml, route);
    writeFileSync(outPath, html, "utf8");
    console.log(`  ✓ /${route.path}/ (${html.length} bytes) - ${route.schemaType}`);
  }

  console.log("\nPrerender complete.");
}

main();
