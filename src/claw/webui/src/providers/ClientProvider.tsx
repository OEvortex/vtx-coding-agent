import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";

import type { VtxClawClient } from "@/lib/vtx-claw-client";

type AgentMode = "vtx" | "claw";

const STORAGE_KEY = "vtx_agent_mode";

function getStoredMode(): AgentMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "vtx" || v === "claw") return v;
  } catch {}
  return "claw";
}

function storeMode(mode: AgentMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {}
}

// External store for agent mode — lets components subscribe without re-rendering
// the whole tree on every toggle.
const listeners = new Set<() => void>();
let currentMode: AgentMode = getStoredMode();

function notifyListeners(): void {
  for (const cb of listeners) {
    cb();
  }
}

function subscribeToMode(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getCurrentMode(): AgentMode {
  return currentMode;
}

function setMode(mode: AgentMode): void {
  if (mode === currentMode) return;
  currentMode = mode;
  storeMode(mode);
  notifyListeners();
}

export function useAgentMode(): AgentMode {
  return useSyncExternalStore(subscribeToMode, getCurrentMode);
}

export function useSetAgentMode(): (mode: AgentMode) => void {
  return setMode;
}

interface ClientContextValue {
  client: VtxClawClient;
  token: string;
  modelName: string | null;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  children,
}: {
  client: VtxClawClient;
  token: string;
  modelName?: string | null;
  children: ReactNode;
}) {
  return (
    <ClientContext.Provider value={{ client, token, modelName }}>
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
