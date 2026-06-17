import { useState, useEffect, lazy, Suspense } from "react";
import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import LiveStats from "./components/LiveStats";
import Why from "./components/Why";
import Capabilities from "./components/Capabilities";
import CTASection from "./components/CTASection";
import Footer from "./components/Footer";
import ScrollProgress from "./components/ScrollProgress";
import NotFound from "./components/NotFound";
import { useRouteMeta, type RouteKey } from "./hooks/useRouteMeta";

// Lazy-load heavy subpages.
const DocsPage = lazy(() => import("./components/DocsPage"));
const FeaturesPage = lazy(() => import("./components/FeaturesPage"));

function LandingPage() {
  return (
    <div id="portfolio-app-root" className="bg-canvas text-ink min-h-screen font-sans antialiased">
      <a
        href="#hero"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-accent focus:text-accent-ink focus:rounded-md focus:font-medium focus:text-sm"
      >
        Skip to content
      </a>
      <div className="film-grain" aria-hidden="true" />
      <ScrollProgress />
      <Navbar />
      <main>
        <Hero />
        <LiveStats />
        <Why />
        <Capabilities />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}

function SubpageLoading() {
  return (
    <div className="bg-canvas min-h-screen flex flex-col items-center justify-center gap-4 text-ink-faint text-sm font-mono">
      <div className="flex items-center gap-2">
        <span className="block w-2 h-2 rounded-full bg-accent animate-pulse" />
        <span>Loading</span>
      </div>
      <div className="flex gap-1.5">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="w-1 h-1 rounded-full bg-ink-faint"
            style={{
              animation: `pulse 1.4s ease-in-out ${i * 0.2}s infinite`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

function isDocsRoute(): boolean {
  return window.location.pathname.startsWith("/docs");
}

function isFeaturesRoute(): boolean {
  return window.location.pathname.startsWith("/features");
}

function detectRoute(): RouteKey {
  if (isDocsRoute()) return "docs";
  if (isFeaturesRoute()) return "features";
  if (window.location.pathname === "/") return "landing";
  return "404";
}

export default function App() {
  const [route, setRoute] = useState<RouteKey>(detectRoute());

  useRouteMeta(route);

  useEffect(() => {
    const onPopState = () => setRoute(detectRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  if (route === "docs") {
    return (
      <Suspense fallback={<SubpageLoading />}>
        <DocsPage />
      </Suspense>
    );
  }

  if (route === "features") {
    return (
      <Suspense fallback={<SubpageLoading />}>
        <FeaturesPage />
      </Suspense>
    );
  }

  if (route === "404") {
    return <NotFound />;
  }

  return <LandingPage />;
}
