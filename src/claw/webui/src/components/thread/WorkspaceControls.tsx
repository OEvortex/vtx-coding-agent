import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  Hand,
  Home,
  Loader2,
  Search,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type {
  WorkspaceAccessMode,
  WorkspaceScopePayload,
  WorkspacesPayload,
} from "@/lib/types";
import { getHostApi } from "@/lib/runtime";
import { cn } from "@/lib/utils";
import {
  isAbsoluteWorkspacePath,
  projectNameFromPath,
  scopeWithAccessMode,
  selectedProjectScope,
  shortWorkspacePath,
} from "@/lib/workspace";
import { browseFolders, type FsBrowseResult } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";

// ---------------------------------------------------------------------------
// FolderBrowser — inline Codex-style folder picker
// ---------------------------------------------------------------------------

function FolderBrowser({
  onSelect,
  disabled,
}: {
  onSelect: (path: string) => void;
  disabled?: boolean;
}) {
  const { token } = useClient();
  const [browse, setBrowse] = useState<FsBrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const navigate = useCallback(
    async (path?: string) => {
      setLoading(true);
      setSearchQuery("");
      try {
        const result = await browseFolders(token, path);
        setBrowse(result);
      } catch {
        // silently ignore — keep current view
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  // Load home dir on first render
  useEffect(() => {
    void navigate();
  }, [navigate]);

  // Focus search when browser mounts
  useEffect(() => {
    const t = setTimeout(() => searchRef.current?.focus(), 60);
    return () => clearTimeout(t);
  }, []);

  if (!browse && loading) {
    return (
      <div className="flex h-32 items-center justify-center text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  if (!browse) return null;

  // Build breadcrumb segments from current path
  const sep = browse.separator || "/";
  const parts = browse.path.replace(/\\/g, "/").split("/").filter(Boolean);
  // Reconstruct segment paths
  const segments = parts.map((part, i) => ({
    label: part,
    path:
      sep === "\\"
        ? parts.slice(0, i + 1).join("\\")
        : "/" + parts.slice(0, i + 1).join("/"),
  }));

  // Filtered entries
  const q = searchQuery.trim().toLowerCase();
  const filtered = q
    ? browse.entries.filter((e) => e.name.toLowerCase().includes(q))
    : browse.entries;

  return (
    <div className="flex flex-col gap-1.5">
      {/* Breadcrumb */}
      <div className="flex items-center gap-0.5 overflow-x-auto rounded-[10px] bg-muted/50 px-2 py-1 text-[11px] text-muted-foreground scrollbar-none">
        <button
          type="button"
          disabled={disabled}
          onClick={() => void navigate(browse.home)}
          className="flex shrink-0 items-center gap-1 rounded-md px-1 py-0.5 hover:bg-background/70 hover:text-foreground disabled:pointer-events-none"
          title={browse.home}
        >
          <Home className="h-3 w-3" />
        </button>
        {segments.map((seg, i) => (
          <span key={seg.path} className="flex shrink-0 items-center gap-0.5">
            <ChevronRight className="h-2.5 w-2.5 shrink-0 text-muted-foreground/50" />
            <button
              type="button"
              disabled={disabled || i === segments.length - 1}
              onClick={() => void navigate(seg.path)}
              className={cn(
                "max-w-[8rem] truncate rounded-md px-1 py-0.5",
                i === segments.length - 1
                  ? "font-semibold text-foreground"
                  : "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none",
              )}
            >
              {seg.label}
            </button>
          </span>
        ))}
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60" />
        <Input
          ref={searchRef}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Filter folders…"
          disabled={disabled}
          className="h-8 rounded-[10px] border-border/55 bg-background/80 pl-8 pr-3 text-[12px] focus-visible:ring-1 focus-visible:ring-foreground/10 focus-visible:ring-offset-0"
        />
        {loading && (
          <Loader2 className="absolute right-2.5 top-1/2 h-3 w-3 -translate-y-1/2 animate-spin text-muted-foreground/60" />
        )}
      </div>

      {/* Directory list */}
      <div className="max-h-48 overflow-y-auto rounded-[10px] border border-border/40 bg-background/60">
        {/* Use current dir as selection target */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => onSelect(browse.path)}
          className="flex w-full items-center gap-2.5 border-b border-border/30 px-3 py-2 text-left text-[12.5px] font-medium text-primary hover:bg-primary/5 disabled:pointer-events-none"
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate">
            Use &ldquo;{parts.at(-1) || browse.path}&rdquo;
          </span>
          <Check className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </button>

        {/* Go up */}
        {browse.parent && !q && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => void navigate(browse.parent!)}
            className="flex w-full items-center gap-2.5 border-b border-border/20 px-3 py-1.5 text-left text-[12px] text-muted-foreground hover:bg-muted/40 hover:text-foreground disabled:pointer-events-none"
          >
            <Folder className="h-3.5 w-3.5 shrink-0 opacity-50" />
            <span className="italic opacity-70">..</span>
          </button>
        )}

        {filtered.length === 0 ? (
          <p className="px-3 py-4 text-center text-[11.5px] text-muted-foreground/60">
            {q ? "No matching folders" : "No subdirectories"}
          </p>
        ) : (
          filtered.map((entry) => (
            <div
              key={entry.path}
              className="flex items-center border-b border-border/15 last:border-0"
            >
              <button
                type="button"
                disabled={disabled}
                onClick={() => void navigate(entry.path)}
                className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-1.5 text-left text-[12.5px] hover:bg-muted/50 disabled:pointer-events-none"
              >
                <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" />
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              </button>
              <button
                type="button"
                disabled={disabled}
                title={`Select "${entry.name}"`}
                onClick={() => onSelect(entry.path)}
                className="flex h-full items-center gap-1 border-l border-border/20 px-2.5 py-1.5 text-[11px] text-muted-foreground hover:bg-primary/8 hover:text-primary disabled:pointer-events-none"
              >
                <Check className="h-3 w-3" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkspaceProjectPicker
// ---------------------------------------------------------------------------

export function WorkspaceProjectPicker({
  isHero,
  disabled,
  scope,
  defaultScope,
  controls,
  error,
  onChange,
}: {
  isHero: boolean;
  disabled?: boolean;
  scope: WorkspaceScopePayload | null;
  defaultScope: WorkspaceScopePayload | null;
  controls: WorkspacesPayload["controls"] | null;
  error?: string | null;
  onChange?: (scope: WorkspaceScopePayload) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pathDraft, setPathDraft] = useState("");
  const [pathError, setPathError] = useState<string | null>(null);
  const [pickingFolder, setPickingFolder] = useState(false);
  const [mode, setMode] = useState<"browse" | "manual">("browse");
  const currentProjectScope = selectedProjectScope(scope, defaultScope);
  const projectLabel = currentProjectScope
    ? currentProjectScope.project_name ||
      projectNameFromPath(currentProjectScope.project_path)
    : t("thread.composer.workspace.projectPlaceholder");
  const visible =
    isHero &&
    !!defaultScope &&
    !!onChange &&
    controls?.can_change_project !== false;
  const hostApi = getHostApi();
  const nativeProjectPicker = !!hostApi;

  const prevOpenRef = useRef(false);
  useEffect(() => {
    const wasOpen = prevOpenRef.current;
    prevOpenRef.current = open;
    // Only initialize pathDraft when the dropdown transitions from closed to open.
    // Avoid resetting while open so external scope updates don't overwrite user input.
    if (open && !wasOpen) {
      setPathDraft(currentProjectScope?.project_path ?? "");
      setPathError(null);
      setMode("browse");
    }
  }, [currentProjectScope?.project_path, open]);

  useEffect(() => {
    if (error && visible) setOpen(true);
  }, [error, visible]);

  const applyProjectPath = useCallback(
    (projectPath: string, projectName?: string) => {
      const base = scope ?? defaultScope;
      const trimmed = projectPath.trim();
      if (!base || !onChange) return;
      if (!trimmed || !isAbsoluteWorkspacePath(trimmed)) {
        setPathError(t("workspace.dialog.absolutePathRequired"));
        return;
      }
      onChange({
        ...base,
        project_path: trimmed,
        project_name: projectName || projectNameFromPath(trimmed),
        restrict_to_workspace: base.access_mode === "restricted",
      });
      setPathError(null);
      setOpen(false);
    },
    [defaultScope, onChange, scope, t],
  );

  const pickNativeFolder = useCallback(async () => {
    if (!hostApi || disabled) return;
    setPickingFolder(true);
    try {
      const picked = await hostApi.pickFolder();
      if (picked) applyProjectPath(picked);
    } catch (err) {
      setPathError((err as Error).message);
    } finally {
      setPickingFolder(false);
    }
  }, [applyProjectPath, disabled, hostApi]);

  if (!visible || !defaultScope || !onChange) return null;

  if (nativeProjectPicker) {
    return (
      <div className="flex min-w-0 items-center rounded-b-[28px] border-t border-border/25 bg-muted/60 px-3 py-1.5 dark:bg-white/[0.055] sm:px-4">
        <button
          type="button"
          disabled={disabled || pickingFolder}
          aria-label={t("thread.composer.workspace.projectAria")}
          title={currentProjectScope?.project_path}
          onClick={() => void pickNativeFolder()}
          className={cn(
            "inline-flex h-7 max-w-full items-center gap-2 rounded-full px-2.5 sm:max-w-[18rem]",
            "text-[12px] font-medium text-muted-foreground/90 transition-colors",
            "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
            currentProjectScope && "text-foreground/82",
          )}
        >
          <Folder
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              currentProjectScope && "text-primary",
            )}
          />
          <span className="truncate">{projectLabel}</span>
        </button>
        {pathError || error ? (
          <span
            role="alert"
            className="ml-2 min-w-0 truncate text-[11.5px] font-medium text-destructive"
          >
            {pathError ?? error}
          </span>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center rounded-b-[28px] border-t border-border/25 bg-muted/60 px-3 py-1.5 dark:bg-white/[0.055] sm:px-4">
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            aria-label={t("thread.composer.workspace.projectAria")}
            className={cn(
              "inline-flex h-7 max-w-full items-center gap-2 rounded-full px-2.5 sm:max-w-[18rem]",
              "text-[12px] font-medium text-muted-foreground/90 transition-colors",
              "hover:bg-background/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-55",
              currentProjectScope && "text-foreground/82",
            )}
          >
            <Folder
              className={cn(
                "h-3.5 w-3.5 shrink-0",
                currentProjectScope && "text-primary",
              )}
            />
            <span className="truncate">{projectLabel}</span>
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          side="bottom"
          sideOffset={8}
          className="w-[min(26rem,calc(100vw-2rem))] rounded-[22px]"
        >
          {/* Default workspace option */}
          <DropdownMenuItem
            onSelect={() =>
              applyProjectPath(
                defaultScope.project_path,
                defaultScope.project_name,
              )
            }
            className="flex min-h-[48px] cursor-default gap-3 rounded-[16px] px-3 py-2.5 focus:bg-muted/55"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[12px] bg-muted text-foreground/80">
              <Folder className="h-4 w-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-semibold text-foreground">
                {t("workspace.dialog.defaultProject")}
              </span>
              <span className="block truncate text-[11.5px] text-muted-foreground">
                {shortWorkspacePath(defaultScope.project_path)}
              </span>
            </span>
            {!currentProjectScope ? (
              <Check className="h-4 w-4 text-foreground/80" />
            ) : null}
          </DropdownMenuItem>

          <div className="my-1 h-px bg-border/45" />

          {/* Mode toggle */}
          <div
            className="space-y-2 px-1.5 pb-2 pt-1"
            onKeyDown={(event) => {
              if (event.key !== "Escape") event.stopPropagation();
            }}
          >
            {/* Tab switcher */}
            <div className="flex items-center gap-1 rounded-full bg-muted/60 p-0.5">
              <button
                type="button"
                onClick={() => setMode("browse")}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-1 text-[11.5px] font-medium transition-all",
                  mode === "browse"
                    ? "bg-background shadow-sm text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <FolderOpen className="h-3 w-3" />
                Browse
              </button>
              <button
                type="button"
                onClick={() => setMode("manual")}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-1 text-[11.5px] font-medium transition-all",
                  mode === "manual"
                    ? "bg-background shadow-sm text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Search className="h-3 w-3" />
                Paste Path
              </button>
            </div>

            {/* Browse mode */}
            {mode === "browse" && (
              <FolderBrowser
                disabled={disabled}
                onSelect={(path) => applyProjectPath(path)}
              />
            )}

            {/* Manual mode */}
            {mode === "manual" && (
              <form
                className="flex items-center gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  applyProjectPath(pathDraft);
                }}
              >
                <Input
                  value={pathDraft}
                  disabled={disabled}
                  onChange={(event) => {
                    setPathDraft(event.target.value);
                    setPathError(null);
                  }}
                  placeholder={t("workspace.dialog.manualPlaceholder")}
                  aria-label={t("workspace.dialog.manual")}
                  className={cn(
                    "h-9 rounded-full border-border/55 bg-background/80 px-3 text-[12.5px]",
                    "focus-visible:ring-1 focus-visible:ring-foreground/10 focus-visible:ring-offset-0",
                  )}
                />
                <Button
                  type="submit"
                  disabled={disabled || !pathDraft.trim()}
                  className="h-9 shrink-0 rounded-full px-3 text-[12px]"
                >
                  {t("workspace.dialog.usePath")}
                </Button>
              </form>
            )}

            {pathError || error ? (
              <p
                role="alert"
                className="px-1 text-[11.5px] font-medium text-destructive"
              >
                {pathError ?? error}
              </p>
            ) : null}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WorkspaceAccessMenu
// ---------------------------------------------------------------------------

export function WorkspaceAccessMenu({
  scope,
  disabled,
  canUseFullAccess,
  isHero,
  onChange,
}: {
  scope: WorkspaceScopePayload;
  disabled?: boolean;
  canUseFullAccess: boolean;
  isHero: boolean;
  onChange?: (scope: WorkspaceScopePayload) => void;
}) {
  const { t } = useTranslation();
  const mode = scope.access_mode;
  const isFull = mode === "full";

  const setMode = (value: WorkspaceAccessMode) => {
    if (value === "full" && !canUseFullAccess) return;
    if (value === mode) return;
    onChange?.(scopeWithAccessMode(scope, value));
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled || !onChange}>
        <Button
          type="button"
          variant="ghost"
          aria-label={t("thread.composer.workspace.accessAria")}
          className={cn(
            "max-w-[min(12.5rem,42vw)] rounded-[10px] border border-transparent font-semibold shadow-none",
            isHero ? "h-8 px-2.5 text-[12px]" : "h-9 px-3 text-[12.5px]",
            isFull
              ? "bg-transparent text-orange-600 hover:bg-orange-500/8 dark:text-orange-300 dark:hover:bg-orange-400/10"
              : "bg-transparent text-muted-foreground hover:bg-foreground/[0.045] hover:text-foreground dark:hover:bg-white/[0.06]",
          )}
        >
          {isFull ? (
            <AlertTriangle
              className={cn(
                "mr-1.5 shrink-0",
                isHero ? "h-3.5 w-3.5" : "h-3.5 w-3.5",
              )}
            />
          ) : (
            <Hand
              className={cn(
                "mr-1.5 shrink-0",
                isHero ? "h-3.5 w-3.5" : "h-3.5 w-3.5",
              )}
            />
          )}
          <span className="truncate">
            {t(
              isFull
                ? "thread.composer.workspace.full"
                : "thread.composer.workspace.default",
            )}
          </span>
          <ChevronDown
            className={cn("ml-1.5 shrink-0", isHero ? "h-3 w-3" : "h-3 w-3")}
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <AccessMenuItem
          icon={<Hand className="h-4 w-4" />}
          label={t("thread.composer.workspace.default")}
          selected={mode === "restricted"}
          onSelect={() => setMode("restricted")}
        />
        <AccessMenuItem
          icon={<AlertTriangle className="h-4 w-4" />}
          label={t("thread.composer.workspace.full")}
          selected={mode === "full"}
          disabled={!canUseFullAccess}
          warning
          onSelect={() => setMode("full")}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AccessMenuItem({
  icon,
  label,
  selected,
  disabled,
  warning,
  onSelect,
}: {
  icon: ReactNode;
  label: string;
  selected: boolean;
  disabled?: boolean;
  warning?: boolean;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex h-10 items-center gap-3 rounded-xl px-3 text-[13.5px] font-semibold",
        warning &&
          "text-orange-600 focus:text-orange-600 dark:text-orange-300 dark:focus:text-orange-300",
      )}
    >
      <span
        className="grid h-5 w-5 shrink-0 place-items-center text-current"
        aria-hidden
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {selected ? <Check className="h-4 w-4 shrink-0" aria-hidden /> : null}
    </DropdownMenuItem>
  );
}
