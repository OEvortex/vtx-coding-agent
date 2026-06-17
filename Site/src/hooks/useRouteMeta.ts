import { useEffect } from "react";

export type RouteKey = "landing" | "features" | "docs" | "404";

interface RouteMeta {
  title: string;
  description: string;
  canonical: string;
  ogTitle: string;
  ogDescription: string;
  jsonLd?: object;
  ogImagePath?: string;
}

const SITE_URL = "https://agentjarvis-v2.netlify.app";
const DEFAULT_OG = `${SITE_URL}/og-image.jpg`;

const ROUTE_META: Record<RouteKey, RouteMeta> = {
  landing: {
    title: "Vtx: Agentic coding harness for your terminal",
    description:
      "Open-source agentic coding harness for developers. 5 core tools, 11+ LLM providers, TUI, headless CLI, session persistence, compaction, skills, and a full extension system.",
    canonical: `${SITE_URL}/`,
    ogTitle: "Vtx: Agentic coding harness",
    ogDescription:
      "5 core tools, 11+ LLM providers, TUI, headless CLI, sessions, compaction, skills, extensions. Open source under the MIT license.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "WebPage",
      "@id": `${SITE_URL}/#webpage`,
      "url": `${SITE_URL}/`,
      "name": "Vtx: Agentic coding harness",
      "isPartOf": { "@id": `${SITE_URL}/#website` },
      "about": { "@id": `${SITE_URL}/#software` },
    },
  },
  features: {
    title: "Features - Vtx | Interactive TUI, Headless CLI, Skills & Extensions",
    description:
      "Complete feature reference for Vtx: interactive Textual TUI, headless CLI mode, 9 default tools, custom skills system, Python extension API, and configurable permissions.",
    canonical: `${SITE_URL}/features/`,
    ogTitle: "Vtx: Full feature reference",
    ogDescription:
      "Textual TUI, headless CLI, 9 default tools, custom skills, Python extensions, and prompt/auto permission modes.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "ItemList",
      "name": "Vtx features",
      "itemListElement": [
        { "@type": "ListItem", "position": 1, "name": "Interactive Terminal UI (TUI) and Headless CLI" },
        { "@type": "ListItem", "position": 2, "name": "9 default tools (read, edit, write, bash, find, skill, web...)" },
        { "@type": "ListItem", "position": 3, "name": "Custom Skills system with command registration (/deploy)" },
        { "@type": "ListItem", "position": 4, "name": "Python Extension API for custom tools and lifecycle event hooks" },
        { "@type": "ListItem", "position": 5, "name": "Flexible LLM Support (hosted APIs & local endpoints)" },
        { "@type": "ListItem", "position": 6, "name": "Granular permissions (prompt & auto modes)" },
      ],
    },
  },
  docs: {
    title: "Documentation - Vtx",
    description:
      "Official Vtx documentation: setup, extension system, custom tools, agents, memory, hooks, RPC, ACP, sandbox, skills, and architecture reference.",
    canonical: `${SITE_URL}/docs/`,
    ogTitle: "Vtx: Documentation",
    ogDescription:
      "Setup, extension system, custom tools, memory, hooks, RPC, ACP, sandbox. Every Vtx subsystem documented.",
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "TechArticle",
      "headline": "Vtx documentation",
      "description":
        "Official documentation for Vtx, the open-source agentic coding harness.",
      "proficiencyLevel": "Expert",
      "author": { "@type": "Organization", "name": "OEvortex" },
    },
  },
  "404": {
    title: "Page not found - Vtx",
    description: "The page you're looking for doesn't exist.",
    canonical: `${SITE_URL}/`,
    ogTitle: "Vtx: Page not found",
    ogDescription: "The page you're looking for doesn't exist.",
  },
};

function setMeta(name: string, content: string, attr: "name" | "property" = "name") {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, name);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel: string, href: string) {
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

function removeJsonLd(id: string) {
  document.querySelectorAll(`script[data-route-jsonld="${id}"]`).forEach((n) => n.remove());
}

function addJsonLd(id: string, data: object) {
  const script = document.createElement("script");
  script.type = "application/ld+json";
  script.setAttribute("data-route-jsonld", id);
  script.textContent = JSON.stringify(data);
  document.head.appendChild(script);
}

function pushUrlState(route: RouteKey) {
  const path = route === "landing" ? "/" : `/${route}/`;
  if (window.location.pathname !== path) {
    window.history.pushState({ route }, "", path);
  }
}

export function useRouteMeta(route: RouteKey) {
  useEffect(() => {
    const meta = ROUTE_META[route];
    if (!meta) return;

    document.title = meta.title;
    setMeta("description", meta.description);
    setMeta("og:title", meta.ogTitle, "property");
    setMeta("og:description", meta.ogDescription, "property");
    setMeta("og:url", meta.canonical, "property");
    setMeta("og:image", meta.ogImagePath ?? DEFAULT_OG, "property");
    setMeta("twitter:title", meta.ogTitle);
    setMeta("twitter:description", meta.ogDescription);
    setMeta("twitter:image", meta.ogImagePath ?? DEFAULT_OG);
    setLink("canonical", meta.canonical);

    removeJsonLd("route");
    if (meta.jsonLd) addJsonLd("route", meta.jsonLd);

    pushUrlState(route);

    return () => {
      removeJsonLd("route");
    };
  }, [route]);
}
