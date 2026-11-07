import { renderMarkdown, renderShell, renderCode } from "./markdown.js";

(function () {
  const vscode = acquireVsCodeApi();
  const $ = (id) => document.getElementById(id);

  // Power/shutdown glyph for the "terminate session" (kill) controls. Codicons
  // (@vscode/codicons) has no power symbol, so this ships as an inline SVG like
  // the send button, keeping the classic shutdown look independent of the font.
  const KILL_GLYPH =
    '<svg class="kill-glyph" viewBox="0 0 16 16" fill="none" stroke="currentColor" ' +
    'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M8 2.2v5"/><path d="M5.1 4.5a4.3 4.3 0 1 0 5.8 0"/></svg>';

  const el = {
    boot: $("boot"),
    setup: $("setup"),
    announcer: $("announcer"),
    chat: $("chat"),
    chatTitle: $("chat-title"),
    historyBtn: $("history-btn"),
    titleBtn: $("title-btn"),
    titleCode: $("title-code"),
    panelToggle: $("panel-toggle"),
    headerDivider: $("header-divider"),
    newSessionDd: $("new-session-dd"),
    sessionsResizer: $("sessions-resizer"),
    listSearchBtn: $("list-search-btn"),
    listFilterBtn: $("list-filter-btn"),
    settingsBtn: $("settings-btn"),
    terminateBtn: $("terminate-btn"),
    shareBtn: $("share-btn"),
    detachBtn: $("detach-btn"),
    status: $("status"),
    usage: $("usage"),
    sessionsList: $("sessions-list"),
    sessionsPanel: $("sessions-panel"),
    chatMain: $("chat-main"),
    bodyEl: $("body"),
    thread: $("thread"),
    input: $("input"),
    send: $("send"),
    sendGroup: $("send-group"),
    sendMore: $("send-more"),
    stop: $("stop"),
    attach: $("attach"),
    modeDD: $("mode-dd"),
    modelDD: $("model-dd"),
    fusionDD: $("fusion-dd"),
    thinkingDD: $("thinking-dd"),
    inputBox: $("input-box"),
    composer: $("composer"),
    permissionTray: $("permission-tray"),
    elicitationTray: $("elicitation-tray"),
    workingSet: $("working-set"),
    todoWidget: $("todo-widget"),
    attachments: $("attachments"),
    autocomplete: $("autocomplete"),
    scrollDown: $("scroll-down")
  };

  let body = "list"; // "list" | "thread"
  let busy = false; // whether a turn is in flight; gates edit/restore chrome
  // The thread is a flat sequence of blocks rendered in stream order. `block`
  // is the currently open block; a new block starts on a role change, a
  // messageId change, or after a tool/plan/error interrupts the flow.
  let block = null; // { kind: "user"|"assistant"|"thinking", mid, bubble|body, buffer, start?, label? }
  const toolEls = new Map();
  const terminalCache = new Map();
  const TERMINAL_CACHE_MAX = 50;
  const collapsedGroups = new Set();
  let lastSessions = [];
  let lastActiveId = null;
  let lastFolders = [];
  // The nine most recent chats answer to Ctrl/Cmd+1..9 and wear that number in
  // the list. Recency, not row position, so a number means the same chat
  // whichever way the list is sorted, grouped or filtered.
  let numberedIds = [];
  // Per-session liveness for the status dots: id -> "running" | "idle" |
  // "starting". Absent means dead (gray). Sent by the host.
  let sessionStatuses = {};
  // Sessions another chat surface is running (an editor tab, or the side panel).
  let elsewhereIds = [];
  // Retained transcripts so switching back to an idle session is instant (no
  // reload): id -> { frag, turns, ... }. A dirty id changed in the background
  // and must be reloaded instead of restored. curSessionId is the session whose
  // transcript is currently mounted in the thread.
  const views = new Map();
  const dirtyViews = new Set();
  let curSessionId = null;
  let listCtrl = null; // full sessions-list controller
  let menuCtrl = null; // title-dropdown controller
  let panelCtrl = null; // embedded side-panel controller (feature 2)
  let sessionsPanelOpen = false; // whether the embedded side panel is shown

  let commands = [];
  let ac = null;
  // The `@` query a reply is expected for, or null when nothing is expected.
  let fileQueryToken = null;
  let currentTitle = "Chat";
  let lastUserText = "";

  const modeDropdown = createDropdown(el.modeDD, (v) => vscode.postMessage({ type: "setMode", mode: v }));
  // Model picker lists families; a separate thinking picker holds the effort
  // variants of the selected family (Copilot-style).
  let modelFamilies = [];
  let modelChoices = {};
  // thought_level is reported for the active model only, so each family's
  // effort list is filed under that family rather than applied to whichever
  // model is on screen. A missing key means not known yet, an empty array
  // means the family really has no efforts.
  let thoughtLevelsByFamily = {};
  // Whether the thinking picker is currently listing efforts rather than the
  // family's own variants, since the two need different messages on select.
  let thinkingShowsEfforts = false;
  let currentThoughtLevel = "";
  let currentModelUid = "";
  let modelChangeSeq = 0;
  let pendingModelChange = 0;
  function postModelChange(message) {
    pendingModelChange = ++modelChangeSeq;
    vscode.postMessage({ ...message, requestId: pendingModelChange });
  }
  const savedState = vscode.getState() || {};
  const pinnedModelIds = new Set(Array.isArray(savedState.pinnedModels) ? savedState.pinnedModels : []);
  // The active model family's display name, stamped onto each turn when it is
  // sent so the response footer can show "{model} · {time}" (VS Code's footer
  // detail). ACP does not report a per-turn model, so this is the model
  // selected at send time.
  let currentModelLabel = "";
  let modelHoverFloater = null;
  const modelDropdown = createDropdown(el.modelDD, onModelSelect, {
    buttonIcon: modelButtonIcon,
    itemAction: toggleModelPin,
    itemHover: showModelHover,
    itemLeave: scheduleModelHoverClose,
    onClose: closeModelHover
  });
  const fusionPicker = createFusionPicker(
    el.fusionDD,
    (model) => {
      currentModelUid = model;
      updateModelConfig(familyOfUid(model), model);
      postModelChange({ type: "setFusionModel", model, thoughtLevel: currentThoughtLevel });
    },
    (value, model) => {
      if (model) currentModelUid = model;
      currentThoughtLevel = value;
      postModelChange({ type: "setConfigOption", configId: "thought_level", value, model: currentModelUid });
    }
  );
  const thinkingDropdown = createDropdown(el.thinkingDD, onThinkingSelect, {
    staticIcon: "codicon-thinking",
    ariaLabel: "Thinking effort"
  });

  // Icon shown on the model button: sparkle for Adaptive, the brand codicon for
  // Claude / OpenAI, and a generic chip otherwise. Grok has no codicon, so it
  // still uses the bundled SVG mask (via brandIconOf).
  function brandIconOf(fam) {
    let icons = {};
    try { icons = JSON.parse(document.body.dataset.modelIcons || "{}"); } catch { icons = {}; }
    const s = ((fam.name || "") + " " + (fam.id || "")).toLowerCase();
    if (/grok/.test(s) && icons.grok) return { key: "grok", url: icons.grok };
    return null;
  }
  function modelButtonIcon(familyId) {
    const fam = familyById(familyId);
    if (!fam || isAdaptive(fam)) return "codicon-sparkle";
    const s = ((fam.name || "") + " " + (fam.id || "")).toLowerCase();
    if (/claude/.test(s)) return "codicon-claude";
    if (/gpt|openai/.test(s)) return "codicon-openai";
    const b = brandIconOf(fam);
    return b ? `img:${b.key} ${b.url}` : "codicon-chip";
  }

  function familyById(id) { return modelFamilies.find((f) => f.id === id); }
  function familyOfUid(uid) {
    return modelFamilies.find((f) => (f.variants || []).some((v) => v.value === uid))
      || (String(uid || "").startsWith("fusion-") ? familyById("fusion") : undefined);
  }
  function effortKey(fam, uid) {
    return isFusion(fam) ? `fusion:${fusionIdentity(uid).lead}` : fam?.id;
  }
  const EFFORT_ORDER = ["none", "low", "medium", "high", "xhigh"];
  const EFFORT_NAMES = { none: "No Thinking", low: "Low", medium: "Medium", high: "High", xhigh: "XHigh" };
  function effortsFor(fam, uid) {
    const known = fam ? thoughtLevelsByFamily[effortKey(fam, uid)] : undefined;
    if (known?.length) return known;
    if (!isFusion(fam)) return [];
    const wanted = fusionIdentity(uid || fam.default);
    const seen = new Set();
    for (const variant of fam.variants || []) {
      const identity = fusionIdentity(variant.value);
      if (identity.lead === wanted.lead && identity.sidekick === wanted.sidekick && identity.effort) seen.add(identity.effort);
    }
    return EFFORT_ORDER.filter((value) => seen.has(value)).map((value) => ({ value, name: EFFORT_NAMES[value] }));
  }
  function variantFor(fam, uid) {
    return (fam?.variants || []).find((v) => v.value === uid)
      || (fam?.variants || []).find((v) => v.value === fam.default)
      || fam?.variants?.[0];
  }
  function promotionLabel(v) {
    return typeof v?.promotion === "string" && v.promotion.trim() ? v.promotion.trim() : "";
  }
  function variantBadges(v) {
    return [v?.isNew ? "NEW" : "", v?.isBeta ? "BETA" : "", promotionLabel(v)].filter(Boolean);
  }
  function costTierLabel(tier) {
    return String(tier || "").replace(/^med\b/i, "Medium").trim();
  }
  function costTierClass(tier) {
    const value = String(tier || "").toLowerCase();
    if (value.includes("very")) return "cost-very-high";
    if (value.includes("high")) return "cost-high";
    if (value.includes("med")) return "cost-medium";
    return "cost-low";
  }
  function costRows(summary, sidekick = false) {
    return String(summary || "").split("·").map((part) => {
      const match = /^(.+?)\s*\/\s*(?:MTok|1M)\s+(Sidekick )?(In|Input|Out|Output|Cache Read|Cached input|Cache Write)$/i.exec(part.trim());
      if (!match || !!match[2] !== sidekick) return null;
      const labels = { in: "Input", input: "Input", out: "Output", output: "Output", "cache read": "Cache Read", "cached input": "Cache Read", "cache write": "Cache Write" };
      return { label: labels[match[3].toLowerCase()], value: match[1].trim() };
    }).filter(Boolean);
  }
  function formatTokenCount(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "";
    if (n >= 900000) return `${Math.round(n / 1000000)}M tokens`;
    if (n >= 1000) return `${Math.round(n / 1000)}K tokens`;
    return `${n} tokens`;
  }
  function modelHoverContent(item) {
    if (!item) return null;
    const defaultRows = costRows(item.costSummary);
    const longRows = costRows(item.longContextCostSummary);
    const sidekickDefaultRows = costRows(item.costSummary, true);
    const sidekickLongRows = costRows(item.longContextCostSummary, true);
    const hasSidekickCosts = sidekickDefaultRows.length > 0 || sidekickLongRows.length > 0;
    const costSections = [
      { title: hasSidekickCosts ? "Lead cost per 1M tokens" : "Cost per 1M tokens", defaultRows, longRows },
      ...(hasSidekickCosts ? [{ title: "Sidekick cost per 1M tokens", defaultRows: sidekickDefaultRows, longRows: sidekickLongRows }] : [])
    ];
    const hasCosts = costSections.some((section) => section.defaultRows.length || section.longRows.length);
    const promotion = promotionLabel(item);
    const contextRows = [
      ["Max context", formatTokenCount(item.maxContextTokens)],
      ["Max output", formatTokenCount(item.maxOutputTokens)]
    ].filter(([, value]) => value);
    const hasConfigurable = !!item.thinkingLevels || !!item.fusion;
    if (!item.description && !item.costTier && !hasCosts && !promotion && !contextRows.length && !hasConfigurable) return null;
    const compact = !hasCosts && !promotion && !contextRows.length && !hasConfigurable;

    const card = document.createElement("div");
    card.className = "model-hover" + (compact ? " compact" : "") + (!hasConfigurable ? " no-configurable" : "");
    const header = document.createElement("div");
    header.className = "model-hover-header" + (item.description ? "" : " no-description");
    header.appendChild(Object.assign(document.createElement("strong"), { className: "model-hover-name", textContent: item.name || "Model" }));
    if (item.description) header.appendChild(Object.assign(document.createElement("span"), { className: "model-hover-description", textContent: item.description }));
    if (item.costTier) header.appendChild(Object.assign(document.createElement("span"), { className: `model-hover-tier ${costTierClass(item.costTier)}`, textContent: costTierLabel(item.costTier) }));
    card.appendChild(header);

    costSections.forEach((section) => {
      const rowLabels = [...new Set([...section.defaultRows, ...section.longRows].map((row) => row.label))];
      if (!rowLabels.length) return;
      card.appendChild(Object.assign(document.createElement("div"), { className: "model-hover-section", textContent: section.title }));
      const cost = document.createElement("div");
      cost.className = "model-hover-cost";
      const headings = document.createElement("div");
      headings.className = "model-hover-cost-heading-row" + (section.longRows.length ? " has-long-context" : "");
      headings.appendChild(Object.assign(document.createElement("span"), { className: "model-hover-cost-heading", textContent: "Default" }));
      if (section.longRows.length) headings.appendChild(Object.assign(document.createElement("span"), { className: "model-hover-cost-heading", textContent: "Long Context" }));
      cost.appendChild(headings);
      const table = document.createElement("div");
      table.className = "model-hover-cost-table" + (section.longRows.length ? " has-long-context" : "");
      const defaultByLabel = new Map(section.defaultRows.map((row) => [row.label, row.value]));
      const longByLabel = new Map(section.longRows.map((row) => [row.label, row.value]));
      rowLabels.forEach((label) => {
        const row = document.createElement("div");
        row.className = "model-hover-cost-row" + (section.longRows.length ? " has-long-context" : "");
        row.append(
          Object.assign(document.createElement("span"), { className: "model-hover-cost-line" }),
          Object.assign(document.createElement("span"), { className: "model-hover-cost-label", textContent: label }),
          Object.assign(document.createElement("strong"), { className: "model-hover-cost-value", textContent: defaultByLabel.get(label) || "" })
        );
        if (section.longRows.length) {
          row.append(Object.assign(document.createElement("strong"), { className: "model-hover-cost-value", textContent: longByLabel.get(label) || "" }));
        }
        table.appendChild(row);
      });
      cost.appendChild(table);
      card.appendChild(cost);
    });

    if (promotion) {
      const promo = document.createElement("div");
      promo.className = "model-hover-promotion";
      promo.appendChild(Object.assign(document.createElement("span"), { textContent: "Promotion" }));
      promo.appendChild(Object.assign(document.createElement("strong"), { textContent: promotion }));
      card.appendChild(promo);
    }

    if (contextRows.length) {
      const limits = document.createElement("div");
      limits.className = "model-hover-limits";
      contextRows.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "model-hover-cost-row";
        row.append(
          Object.assign(document.createElement("span"), { className: "model-hover-cost-line" }),
          Object.assign(document.createElement("span"), { className: "model-hover-cost-label", textContent: label }),
          Object.assign(document.createElement("strong"), { className: "model-hover-cost-value", textContent: value })
        );
        limits.appendChild(row);
      });
      card.appendChild(limits);
    }

    if (hasConfigurable) {
      const configurable = document.createElement("div");
      configurable.className = "model-hover-configurable";
      configurable.appendChild(Object.assign(document.createElement("span"), { className: "model-hover-configurable-label", textContent: "Configurable" }));
      const controls = document.createElement("div");
      controls.className = "model-hover-configurable-controls";
      const effort = document.createElement("button");
      effort.type = "button";
      effort.className = "model-hover-configurable-button";
      effort.textContent = item.fusion ? "Configure Fusion" : "Thinking Level";
      effort.addEventListener("click", (event) => {
        event.stopPropagation();
        modelDropdown.close();
        (item.fusion ? el.fusionDD : el.thinkingDD).querySelector(".dd-btn")?.click();
      });
      controls.appendChild(effort);
      configurable.appendChild(controls);
      card.appendChild(configurable);
    }
    return card;
  }
  let modelHoverShowTimer = null;
  let modelHoverCloseTimer = null;
  function cancelModelHoverShow() {
    if (modelHoverShowTimer) clearTimeout(modelHoverShowTimer);
    modelHoverShowTimer = null;
  }
  function cancelModelHoverClose() {
    if (modelHoverCloseTimer) clearTimeout(modelHoverCloseTimer);
    modelHoverCloseTimer = null;
  }
  function closeModelHover() {
    cancelModelHoverShow();
    cancelModelHoverClose();
    if (modelHoverFloater) modelHoverFloater.close();
    modelHoverFloater = null;
  }
  function scheduleModelHoverClose() {
    cancelModelHoverShow();
    cancelModelHoverClose();
    modelHoverCloseTimer = setTimeout(closeModelHover, 300);
  }
  function showModelHover(anchor, item) {
    cancelModelHoverClose();
    closeModelHover();
    modelHoverShowTimer = setTimeout(() => {
      modelHoverShowTimer = null;
      const card = modelHoverContent(item.hover);
      if (!card) return;
      card.addEventListener("mouseenter", cancelModelHoverClose);
      card.addEventListener("mouseleave", scheduleModelHoverClose);
      modelHoverFloater = makeFloater(anchor, card, "outside-right", () => { modelHoverFloater = null; });
    }, 500);
  }
  function savePinnedModels() {
    vscode.setState({ ...(vscode.getState() || {}), pinnedModels: [...pinnedModelIds] });
  }
  function toggleModelPin(item) {
    if (!item?.value) return;
    if (pinnedModelIds.has(item.value)) pinnedModelIds.delete(item.value);
    else pinnedModelIds.add(item.value);
    savePinnedModels();
    applyModelOptions(modelFamilies, currentModelUid);
    modelDropdown.close();
  }

  function onModelSelect(familyId) {
    const fam = familyById(familyId);
    if (!fam) return;
    currentModelUid = fam.default;
    currentModelLabel = fam.name || "";
    postModelChange({ type: "setModel", model: fam.default });
    updateModelConfig(fam, fam.default);
  }
  function onThinkingSelect(value) {
    if (thinkingShowsEfforts) {
      currentThoughtLevel = value;
      postModelChange({ type: "setConfigOption", configId: "thought_level", value, model: currentModelUid });
      return;
    }
    currentModelUid = value;
    postModelChange({ type: "setModel", model: value });
  }
  function fusionIdentity(uid) {
    const [leadUid = "", sidekick = ""] = String(uid || "").replace(/^fusion-/, "").split("-sidekick-");
    const effortMatch = leadUid.match(/-(none|low|medium|high|xhigh)$/);
    return { lead: leadUid.replace(/-(?:none|low|medium|high|xhigh)$/, ""), sidekick, effort: effortMatch ? effortMatch[1] : "" };
  }
  function fusionChoice(choice) {
    const identity = fusionIdentity(choice.value);
    const [lead = choice.name || choice.value, sidekick = ""] = String(choice.name || choice.value).replace(/^Fusion\s*/i, "").replace(/^\(/, "").replace(/\)$/, "").split(" + ");
    return {
      ...choice,
      ...identity,
      leadName: lead.replace(/ (?:No Thinking|Low|Medium|High|XHigh)(?: Thinking)?$/, "") || identity.lead,
      sidekickName: sidekick || identity.sidekick
    };
  }
  function compactFusionLead(name) {
    return String(name || "").replace(/^Claude /, "");
  }
  function compactFusionSidekick(name) {
    return String(name || "").replace(/ (Low|Medium|High|XHigh)$/, " · $1");
  }
  function createFusionPicker(container, onModel, onEffort) {
    const button = document.createElement("button");
    button.className = "dd-btn";
    button.setAttribute("aria-haspopup", "true");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Configure Fusion");
    button.innerHTML = '<span class="dd-icon"><i class="codicon codicon-git-compare"></i></span><span class="dd-label fusion-config-selection"><span class="fusion-config-lead-title"></span><span class="fusion-config-separator"> + </span><span class="fusion-config-sidekick-title"></span></span><i class="codicon codicon-chevron-down"></i>';
    const leadTitle = button.querySelector(".fusion-config-lead-title");
    const sidekickTitle = button.querySelector(".fusion-config-sidekick-title");
    const menu = document.createElement("div");
    menu.className = "dd-menu fusion-config-menu hidden";
    container.append(button, menu);

    let pairs = [];
    let levels = [];
    let lead = "";
    let sidekick = "";
    let effort = "";
    let lastSignature = "";
    let menuBuilt = false;

    function pair() {
      return pairs.find((item) => item.lead === lead && item.sidekick === sidekick && item.effort === effort)
        || pairs.find((item) => item.lead === lead && item.sidekick === sidekick)
        || pairs.find((item) => item.lead === lead)
        || pairs[0];
    }
    function sync() {
      const selected = pair();
      const effortName = levels.find((item) => item.value === effort)?.name || effort;
      if (selected) {
        leadTitle.textContent = `${compactFusionLead(selected.leadName)} · ${effortName}`;
        sidekickTitle.textContent = compactFusionSidekick(selected.sidekickName);
        button.title = `${selected.leadName} · ${effortName} + ${selected.sidekickName}`;
      }
      menu.querySelectorAll(".fusion-config-option").forEach((item) => {
        const value = item.dataset.value;
        const selectedValue = item.dataset.kind === "lead" ? lead : item.dataset.kind === "effort" ? effort : sidekick;
        const isSelected = value === selectedValue;
        item.classList.toggle("selected", isSelected);
        item.setAttribute("aria-checked", isSelected ? "true" : "false");
        const checkEl = item.querySelector(".dd-check");
        if (checkEl) checkEl.innerHTML = isSelected ? '<i class="codicon codicon-check"></i>' : "";
      });
    }
    function choose(kind, value) {
      if (kind === "effort") {
        effort = value;
        onEffort(value, pair()?.value);
      } else {
        if (kind === "lead") lead = value;
        else sidekick = value;
        const selected = pair();
        if (selected) onModel(selected.value);
      }
      sync();
    }
    function section(title, kind, options) {
      const root = document.createElement("div");
      root.className = "fusion-config-section";
      root.appendChild(Object.assign(document.createElement("div"), { className: "fusion-config-label", textContent: title }));
      const rows = document.createElement("div");
      rows.className = "fusion-config-options" + (kind === "effort" ? " effort" : "");
      rows.setAttribute("role", "radiogroup");
      rows.setAttribute("aria-label", title);
      options.forEach((option) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "fusion-config-option";
        item.setAttribute("role", "radio");
        item.dataset.kind = kind;
        item.dataset.value = option.value;
        const check = document.createElement("span");
        check.className = "dd-check";
        item.appendChild(check);
        item.appendChild(Object.assign(document.createElement("span"), { className: "fusion-config-text", textContent: option.name }));
        item.addEventListener("click", (event) => {
          event.stopPropagation();
          choose(kind, option.value);
        });
        rows.appendChild(item);
      });
      root.appendChild(rows);
      return root;
    }
    function effortLoading() {
      const root = document.createElement("div");
      root.className = "fusion-config-section";
      root.appendChild(Object.assign(document.createElement("div"), { className: "fusion-config-label", textContent: "Lead effort" }));
      const status = document.createElement("div");
      status.className = "fusion-config-loading";
      status.setAttribute("role", "status");
      status.setAttribute("aria-busy", "true");
      status.textContent = "Loading effort options...";
      root.appendChild(status);
      return root;
    }
    function renderMenu() {
      menu.innerHTML = "";
      const leads = [...new Map(pairs.map((item) => [item.lead, { value: item.lead, name: item.leadName }])).values()];
      const sidekicks = [...new Map(pairs.map((item) => [item.sidekick, { value: item.sidekick, name: item.sidekickName }])).values()];
      menu.append(section("Lead model", "lead", leads));
      // An effort section with no options is just an orphan label, so without
      // real choices it reports that the levels are still loading instead.
      if (levels.length) menu.append(section("Lead effort", "effort", levels));
      else menu.append(effortLoading());
      menu.append(section("Sidekick", "sidekick", sidekicks));
      menuBuilt = true;
      sync();
    }
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      document.querySelectorAll(".dd-menu").forEach((item) => { if (item !== menu) item.classList.add("hidden"); });
      document.querySelectorAll(".dd-btn.open").forEach((item) => { if (item !== button) item.classList.remove("open"); });
      renderMenu();
      menu.classList.toggle("hidden");
      const open = !menu.classList.contains("hidden");
      button.classList.toggle("open", open);
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
    return {
      set(choices, currentModel, effortOptions, currentEffort) {
        const signature = JSON.stringify([choices, effortOptions]);
        const changed = signature !== lastSignature;
        lastSignature = signature;
        pairs = (choices || []).map(fusionChoice);
        levels = effortOptions || [];
        const current = fusionIdentity(currentModel);
        lead = current.lead || pairs[0]?.lead || "";
        sidekick = current.sidekick || pairs[0]?.sidekick || "";
        effort = (levels.some((item) => item.value === currentEffort) ? currentEffort
          : levels.some((item) => item.value === current.effort) ? current.effort
          : levels[0]?.value) || "";
        if (effort) currentThoughtLevel = effort;
        // Choices can arrive after the user has already opened the menu, so
        // rebuild it in place when they really changed.
        if (!menu.classList.contains("hidden")) {
          if (changed || !menuBuilt) renderMenu(); else sync();
        } else {
          sync();
        }
        container.classList.toggle("hidden", !pairs.length);
      }
    };
  }
  function updateModelConfig(fam, currentUid) {
    const levels = effortsFor(fam, currentUid);
    if (isFusion(fam)) {
      // Fusion's own variants are the whole lead times sidekick cross product,
      // so they are never a thinking list. The picker waits for real pairs.
      fusionPicker.set(fusionPairs(fam), currentUid, levels, currentThoughtLevel);
      el.thinkingDD.classList.add("hidden");
      thinkingShowsEfforts = false;
      return;
    }
    el.fusionDD.classList.add("hidden");
    if (levels.length) {
      thinkingDropdown.set(levels, currentThoughtLevel);
      el.thinkingDD.classList.remove("hidden");
      thinkingShowsEfforts = true;
    } else if (fam && (fam.variants || []).length > 1) {
      thinkingDropdown.set(fam.variants.map((v) => ({ ...v, badges: variantBadges(v) })), currentUid);
      el.thinkingDD.classList.remove("hidden");
      thinkingShowsEfforts = false;
    } else {
      el.thinkingDD.classList.add("hidden");
      thinkingShowsEfforts = false;
    }
  }
  // The grouped choices only exist once a session has reported its options, so
  // they merge over the family's own variants, which carry the same pair uids.
  function fusionPairs(fam) {
    const byValue = new Map();
    for (const variant of fam?.variants || []) byValue.set(variant.value, variant);
    for (const choice of modelChoices.fusion || []) byValue.set(choice.value, choice);
    return [...byValue.values()];
  }
  function isAdaptive(f) { return f.id === "adaptive" || /adaptive/i.test(f.name || ""); }
  function isFusion(f) { return f?.id === "fusion" || /^fusion$/i.test(f?.name || ""); }

  function applyModelOptions(families, currentModel) {
    const list = Array.isArray(families) ? families.slice() : [];
    const adaptive = list.filter(isAdaptive);
    const fusion = list.filter(isFusion);
    const rest = list.filter((f) => !isAdaptive(f) && !isFusion(f)).sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    modelFamilies = [...adaptive, ...fusion, ...rest];
    const fam = familyOfUid(currentModel) || modelFamilies[0];
    currentModelUid = currentModel || fam?.default || "";
    currentModelLabel = fam ? fam.name || "" : currentModelLabel;
    const item = (family, group) => {
      const variant = variantFor(family, currentModelUid);
      return {
        value: family.id,
        name: family.name,
        badges: variantBadges(variant),
        pinned: pinnedModelIds.has(family.id),
        hover: { ...variant, name: family.name, thinkingLevels: (family.variants || []).length > 1, fusion: isFusion(family) },
        group
      };
    };
    const adaptiveFamily = adaptive[0];
    const fusionFamily = fusion[0];
    const pinned = modelFamilies.filter((f) => !isAdaptive(f) && !isFusion(f) && pinnedModelIds.has(f.id));
    const models = modelFamilies.filter((f) => !isAdaptive(f) && !isFusion(f) && !pinnedModelIds.has(f.id));
    const items = [];
    if (adaptiveFamily) items.push(item(adaptiveFamily));
    if (fusionFamily) items.push(item(fusionFamily));
    if ((adaptiveFamily || fusionFamily) && (pinned.length || models.length)) items.push({ sep: true });
    if (pinned.length) {
      items.push(...pinned.map((f) => item(f, "Pinned")));
      if (models.length) items.push({ sep: true });
    }
    items.push(...models.map((f) => item(f, "Models")));
    modelDropdown.set(items, fam ? fam.id : "");
    updateModelConfig(fam, currentModelUid);
  }
  function selectModelUid(uid) {
    const fam = familyOfUid(uid);
    if (!fam) return;
    currentModelUid = uid;
    currentModelLabel = fam.name || "";
    modelDropdown.setCurrent(fam.id);
    updateModelConfig(fam, uid);
  }

  // --- View state ----------------------------------------------------------

  function setView(v) {
    el.setup.classList.toggle("hidden", v !== "setup");
    el.chat.classList.toggle("hidden", v !== "chat");
  }

  // Dismiss the boot overlay once there is real content to show (setup, the
  // session list, or an error). Idempotent.
  let booted = false;
  function hideBoot() {
    if (booted) return;
    booted = true;
    if (el.boot) el.boot.classList.add("hidden");
  }
  // Safety net: never let the overlay stick if the host is slow or silent.
  setTimeout(hideBoot, 15000);

  function setBody(b) {
    clearElsewhere();
    const was = body;
    // A tab is its one chat, so there is nowhere else for it to go.
    body = inEditor() ? "thread" : b;
    const list = body === "list";
    el.sessionsList.classList.toggle("hidden", !list);
    el.thread.classList.toggle("hidden", list);
    // The composer lives outside the body panels, so mark the list view so its
    // session-scoped widgets (working set, context ring) hide while browsing.
    el.composer.classList.toggle("list-mode", list);
    el.input.placeholder = list ? "Start a new chat\u2026" : "Ask Devin, or type @ to add a file";
    // An explicit label wins over the placeholder, so it has to say the same thing:
    // in the list the box starts a chat, in a chat it continues one.
    el.input.setAttribute("aria-label", list ? "Start a new chat" : "Ask Devin");
    // Only on the way in. Handing the composer back to the "new chat" box empties
    // it, and the panel is told it is a list more than once: on open, and again
    // once the CLI health check finishes. Doing it every time wiped the draft that
    // had just been restored, and anything typed while that check was running.
    if (list && was !== "list") { closeTitleMenu(); detachComposerFromSession(); closeSessionsPanel(); stopThreadLoading(); }
    renderHeader();
    updateComposerDock();
    updateTerminateBtn();
    updateScrollDownButton();
    reportListVisible();
  }

  // Whether a session list is on screen anywhere here: the full list, the docked
  // panel, or the title switcher. The host keeps the list live only while someone
  // is looking at one, since every re-listing runs `devin list`.
  let listShown = null;
  function reportListVisible() {
    const shown = body === "list" || sessionsPanelOpen || !!menuCtrl;
    if (shown === listShown) return;
    listShown = shown;
    vscode.postMessage({ type: "listVisible", value: shown });
  }



  // --- Custom webview title bar --------------------------------------------
  // We drive all controls from the webview header (VS Code's own view/title
  // buttons are removed): the Sessions list header (New/Search/Filter/Refresh/
  // Settings) and the in-session header (panel toggle, back, title + subtitle,
  // rename, terminate).
  function mkIcon(name) {
    const i = document.createElement("i");
    i.className = "codicon codicon-" + name;
    return i;
  }
  function setBtnIcon(btn, name) {
    btn.textContent = "";
    btn.appendChild(mkIcon(name));
  }

  function currentSessionMeta() {
    return curSessionId ? lastSessions.find((s) => s.id === curSessionId) : null;
  }

  function renderHeader() {
    const list = body === "list";
    // List-mode cluster, all on the right of the "Sessions" title. The list keeps
    // itself up to date, so there is nothing to refresh by hand.
    el.newSessionDd.classList.toggle("hidden", !list);
    el.listSearchBtn.classList.toggle("hidden", !list);
    el.listFilterBtn.classList.toggle("hidden", !list);
    el.settingsBtn.classList.toggle("hidden", !list);
    // Thread-mode clusters. A tab has no list to go back to.
    el.historyBtn.classList.toggle("hidden", list || inEditor());
    updateHeaderDivider();
    // In a tab the title is the tab's own name, not a control, so it reads as a
    // heading there too.
    el.titleBtn.classList.toggle("as-heading", list || inEditor());
    // In a session, the session code shows as a badge to the left of the title.
    const meta = list ? null : currentSessionMeta();
    // Follow a name the host has since learned (the CLI's own title, or a rename).
    if (meta && meta.title) currentTitle = meta.title;
    el.chatTitle.textContent = list ? "Sessions" : currentTitle;
    if (!list && meta && meta.short_id) {
      el.titleCode.textContent = meta.short_id;
      el.titleCode.classList.remove("hidden");
    } else {
      el.titleCode.textContent = "";
      el.titleCode.classList.add("hidden");
    }
    if (list) {
      el.panelToggle.classList.add("hidden");
    } else {
      updatePanelToggle();
    }
  }

  // In-session: whether there is room for the docked sessions panel beside the
  // thread. Wide -> a sidebar toggle for the docked panel; narrow -> a
  // list-tree button that opens the session switcher dropdown instead.
  const SIDE_BY_SIDE_MIN = 600;
  function hasRoomForPanel() {
    return el.chat.clientWidth >= SIDE_BY_SIDE_MIN;
  }

  // Which side the docked panel sits on (devin.sessionsPanel.side, default
  // right). The whole layout is driven off the attribute, in CSS, so the DOM
  // order never changes: the panel and its resizer are reordered, and the toggle
  // moves to the far end of the header, past the terminate button.
  function panelSide() {
    return caps.panelSide === "left" ? "left" : "right";
  }
  function applyPanelSide() {
    el.chat.dataset.panelSide = panelSide();
    if (body === "thread") { updatePanelToggle(); updateDetachBtn(); }
  }
  // Persist a side chosen by dragging, and apply it now rather than waiting for
  // the host to echo the setting back.
  function setPanelSide(side) {
    if (side !== "left" && side !== "right") return;
    if (panelSide() === side) return;
    caps.panelSide = side;
    applyPanelSide();
    vscode.postMessage({ type: "setConfig", key: "sessionsPanel.side", value: side });
  }
  function updatePanelToggle() {
    if (body !== "thread") return;
    // A tab has no sessions panel: it is one chat.
    if (inEditor()) {
      el.panelToggle.classList.add("hidden");
      return;
    }
    el.panelToggle.classList.remove("hidden");
    if (hasRoomForPanel()) {
      const side = panelSide();
      const icon = "layout-sidebar-" + side + (sessionsPanelOpen ? "-off" : "");
      setBtnIcon(el.panelToggle, icon);
      el.panelToggle.title = sessionsPanelOpen ? "Hide sessions" : "Show sessions";
      el.panelToggle.classList.toggle("active", sessionsPanelOpen);
    } else {
      // No room for a docked panel: the list-tree button opens the switcher.
      if (sessionsPanelOpen) closeSessionsPanel();
      setBtnIcon(el.panelToggle, "list-tree");
      el.panelToggle.title = "Switch session";
      el.panelToggle.classList.remove("active");
    }
  }

  function openSessionsPanel() {
    if (!hasRoomForPanel() || inEditor()) { toggleTitleMenu(); return; }
    sessionsPanelOpen = true;
    el.sessionsPanel.classList.remove("hidden");
    el.sessionsResizer.classList.remove("hidden");
    el.chat.classList.add("panel-open");
    if (!panelCtrl) {
      panelCtrl = mountSessionList(el.sessionsPanel, { controls: "panel", movable: true });
    } else {
      panelCtrl.refresh();
    }
    updatePanelToggle();
    reportListVisible();
  }
  function closeSessionsPanel() {
    sessionsPanelOpen = false;
    el.sessionsPanel.classList.add("hidden");
    el.sessionsResizer.classList.add("hidden");
    el.chat.classList.remove("panel-open");
    if (body === "thread") updatePanelToggle();
    reportListVisible();
  }
  function toggleSessionsPanel() {
    if (sessionsPanelOpen) closeSessionsPanel();
    else openSessionsPanel();
  }

  // The test harness (jsdom) has no ResizeObserver; guard it.
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => { if (body === "thread") updatePanelToggle(); });
    ro.observe(el.chat);
  }

  // A small floating popover/menu anchored to a header button. `align` is
  // "center" | "left" | "right" | "outside-right" relative to the anchor; the box is clamped to
  // the viewport so it never renders off-screen, and flips above the anchor if
  // it would overflow the bottom.
  function makeFloater(anchor, content, align, onClose) {
    const boxEl = document.createElement("div");
    boxEl.className = "dv-floater" + (content.classList.contains("model-hover") ? " model-hover-floater" : "");
    boxEl.appendChild(content);
    document.body.appendChild(boxEl);
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const bw = boxEl.offsetWidth;
    const bh = boxEl.offsetHeight;
    let left;
    if (align === "center") left = r.left + r.width / 2 - bw / 2;
    else if (align === "left") left = r.left;
    else if (align === "outside-right") left = r.right + 4;
    else left = r.right - bw;
    if (align === "outside-right" && left + bw > vw - 4 && r.left - bw - 4 >= 0) left = r.left - bw - 4;
    left = Math.max(4, Math.min(left, vw - bw - 4));
    let top = r.bottom + 4;
    if (top + bh > vh - 4 && r.top - bh - 4 >= 0) top = r.top - bh - 4;
    top = Math.max(4, top);
    boxEl.style.left = left + "px";
    boxEl.style.top = top + "px";
    boxEl.addEventListener("mousedown", (e) => e.stopPropagation());
    // And the click, not only the press: the document handler closes every menu,
    // including the session switcher a floater can be anchored inside, so clicking
    // into a floater's own search box removed the menu it belonged to.
    boxEl.addEventListener("click", (e) => e.stopPropagation());
    const onDown = (e) => { if (!boxEl.contains(e.target) && !anchor.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    setTimeout(() => {
      document.addEventListener("mousedown", onDown, true);
      document.addEventListener("keydown", onKey, true);
    }, 0);
    function close() {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      boxEl.remove();
      if (onClose) onClose();
    }
    return { el: boxEl, close };
  }

  // The New Session dropdown, shared by the labelled header button and the
  // icon-only button in the sessions panel / switcher.
  let newSessionFloater = null;
  function openNewSessionMenu(anchor) {
    if (newSessionFloater) { newSessionFloater.close(); return; }
    const menu = document.createElement("div");
    menu.className = "dv-menu";
    // The button itself starts one here, so the menu only offers the other places.
    const items = [
      { icon: "split-horizontal", label: "New Session (Editor)", target: "editor" },
      { icon: "multiple-windows", label: "New Session (Window)", target: "window" },
      { icon: "terminal", label: "New Devin CLI Session (Terminal)", target: "terminal" }
    ];
    for (const it of items) {
      const row = document.createElement("button");
      row.className = "dv-menu-item";
      row.appendChild(mkIcon(it.icon));
      row.appendChild(Object.assign(document.createElement("span"), { textContent: it.label }));
      row.addEventListener("click", () => { vscode.postMessage({ type: "newSessionAt", target: it.target }); if (newSessionFloater) newSessionFloater.close(); });
      menu.appendChild(row);
    }
    newSessionFloater = makeFloater(anchor, menu, "center", () => { newSessionFloater = null; });
  }
  // A split button, like VS Code's own: the labelled half starts a session right
  // here, the chevron beside it offers the other places to open one. `compact`
  // drops the label for the narrow toolbar in the sessions panel and switcher,
  // where the same split behaviour still applies.
  function buildNewSessionButton(opts) {
    const compact = !!(opts && opts.compact);
    const wrap = document.createElement("div");
    wrap.className = "new-session-split" + (compact ? " new-session-compact" : "");
    const main = document.createElement("button");
    main.className = "new-session-btn";
    main.title = "New session";
    main.setAttribute("aria-label", "New session");
    main.appendChild(mkIcon("new-session"));
    if (!compact) main.appendChild(Object.assign(document.createElement("span"), { textContent: "New Session" }));
    main.addEventListener("click", (e) => { e.stopPropagation(); vscode.postMessage({ type: "newSessionAt", target: "view" }); });
    const more = document.createElement("button");
    more.className = "new-session-more";
    more.title = "New session in\u2026";
    more.setAttribute("aria-haspopup", "true");
    more.appendChild(mkIcon("chevron-down"));
    more.addEventListener("click", (e) => { e.stopPropagation(); openNewSessionMenu(wrap); });
    wrap.append(main, more);
    return wrap;
  }
  el.newSessionDd.appendChild(buildNewSessionButton());

  // Drag the divider between the sessions sidebar and the chat content to
  // resize the panel freely. On the right the drag direction is mirrored: moving
  // left grows the panel.
  el.sessionsResizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = el.sessionsPanel.getBoundingClientRect().width;
    const dir = panelSide() === "right" ? -1 : 1;
    document.body.style.cursor = "col-resize";
    document.body.classList.add("dv-resizing");
    const onMove = (ev) => {
      let w = startW + dir * (ev.clientX - startX);
      const maxW = Math.max(240, el.chat.clientWidth - 320);
      w = Math.max(220, Math.min(w, maxW));
      el.sessionsPanel.style.width = w + "px";
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.classList.remove("dv-resizing");
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  // Drag the panel by the empty space in its own header to move it to the other
  // side, the way VS Code lets you drag a view between sidebars. A drop past the
  // midpoint of the chat area moves it; anything shorter is treated as a click
  // and leaves it be. Live preview while dragging would fight the pointer, so
  // the side is only committed on release, with the target edge highlighted.
  function startPanelDrag(e) {
    if (e.button !== 0) return;
    const startX = e.clientX;
    let armed = false;
    let target = panelSide();
    const chatBox = () => el.chat.getBoundingClientRect();
    const onMove = (ev) => {
      if (!armed && Math.abs(ev.clientX - startX) < 4) return;
      if (!armed) {
        armed = true;
        document.body.classList.add("dv-panel-dragging");
      }
      const box = chatBox();
      target = ev.clientX < box.left + box.width / 2 ? "left" : "right";
      if (target === panelSide()) delete el.chat.dataset.dropSide;
      else el.chat.dataset.dropSide = target;
    };
    const stop = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      window.removeEventListener("blur", cancel);
      document.body.classList.remove("dv-panel-dragging");
      delete el.chat.dataset.dropSide;
    };
    const onUp = () => {
      const move = armed;
      stop();
      if (move) setPanelSide(target);
    };
    // Losing the pointer (released outside the window, or focus taken away) ends
    // the drag without moving anything, rather than leaving it armed for the next
    // click anywhere in the panel.
    const cancel = () => stop();
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    window.addEventListener("blur", cancel);
  }

  // Header button wiring.
  el.panelToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    if (hasRoomForPanel()) toggleSessionsPanel();
    else toggleTitleMenu();
  });
  el.listSearchBtn.addEventListener("click", (e) => { e.stopPropagation(); if (listCtrl) listCtrl.toggleSearch(el.listSearchBtn); });
  el.listFilterBtn.addEventListener("click", (e) => { e.stopPropagation(); if (listCtrl) listCtrl.toggleFilter(el.listFilterBtn); });
  el.settingsBtn.addEventListener("click", () => vscode.postMessage({ type: "openSettings" }));

  // Indeterminate loading bar along the top edge of the body, using the same
  // travelling accent as the composer's working border. Replaces the spinners
  // that used to sit inside the thread and the session list.
  const loadingBar = document.createElement("div");
  loadingBar.className = "dv-top-loading hidden";
  el.bodyEl.appendChild(loadingBar);
  function showLoadingBar() { loadingBar.classList.remove("hidden"); }
  function hideLoadingBar() { loadingBar.classList.add("hidden"); }

  // Entering the sessions list turns the composer into a clean "new chat" box:
  // the draft, attachments, request-edit binding, docked plan and busy state
  // all belonged to the session we just left. The in-flight turn is cancelled
  // host-side (leaveToList / showSessions). The working set and context ring
  // are only hidden by list-mode CSS, not cleared, so pending edits stay
  // reviewable and reappear when a thread is reopened.
  function detachComposerFromSession() {
    cancelInputEditing();
    // Which servers failed belongs to the chat that was open. The card sits above
    // the composer, not in the thread, so nothing else takes it away, and it was
    // left warning about the last chat while the user browsed the list.
    renderMcpProblems([]);
    mcpDismissed.clear();
    el.input.value = "";
    closeAutocomplete();
    autosize();
    updateSendState();
    renderAttachments([]);
    // Drop any open question/permission widget: a still-pending one is re-posted
    // by the host when the session is reopened, so keeping it here would leave a
    // stale copy and double up on return.
    cancelPrompts();
    planCollapsePref = null;
    wsCollapsePref = null;
    hideDockedPlan();
    closeUsagePopup();
    if (busy) setBusy(false);
  }

  function isAliveStatus(st) { return st === "running" || st === "idle" || st === "starting" || st === "attention"; }

  // The header terminate control is shown only inside a live session's thread, and
  // never in a tab: there, closing the tab is what stops the chat.
  function updateTerminateBtn() {
    const show = body === "thread" && !inEditor() && !!curSessionId && isAliveStatus(sessionStatuses[curSessionId]);
    el.terminateBtn.classList.toggle("hidden", !show);
    updateDetachBtn();
  }

  // With the panel on the right the divider sits between the thread controls and
  // the panel toggle, so it is only a separator while there is something to
  // separate: on its own it reads as a stray rule.
  function updateHeaderDivider() {
    const controls = !el.detachBtn.classList.contains("hidden") || !el.terminateBtn.classList.contains("hidden");
    // A tab has no panel toggle, so there is never a second cluster to divide.
    const needed = body === "thread" && !inEditor() && (panelSide() === "left" || controls);
    el.headerDivider.classList.toggle("hidden", !needed);
  }
  el.terminateBtn.innerHTML = KILL_GLYPH;
  el.terminateBtn.addEventListener("click", () => {
    // Terminating from inside a session returns to the list once confirmed.
    if (curSessionId) vscode.postMessage({ type: "terminateSession", id: curSessionId, title: currentTitle, returnToList: true });
  });

  // Move this chat between the side panel and an editor tab. The live agent goes
  // with it, so the direction is the only thing that changes: the side panel
  // offers "open in an editor tab", an editor tab offers "move to the side panel".
  function updateDetachBtn() {
    const show = body === "thread" && !!curSessionId;
    // Sharing publishes the conversation, so it only appears for a chat that exists
    // and only when the agent supports it.
    el.shareBtn.classList.toggle("hidden", !(show && caps.sessionShare));
    el.detachBtn.classList.toggle("hidden", !show);
    updateHeaderDivider();
    if (!show) return;
    setBtnIcon(el.detachBtn, inEditor() ? "layout-sidebar-right-dock" : "link-external");
    el.detachBtn.title = inEditor() ? "Move this chat to the side panel" : "Open this chat in an editor tab";
    el.detachBtn.setAttribute("aria-label", el.detachBtn.title);
  }
  el.detachBtn.addEventListener("click", () => {
    if (!curSessionId) return;
    vscode.postMessage({ type: inEditor() ? "attachSession" : "detachSession", id: curSessionId });
  });
  el.shareBtn.addEventListener("click", () => {
    if (curSessionId) vscode.postMessage({ type: "shareSession" });
  });

  // A chat runs in one place at a time, so when the one being opened is already
  // open on the other surface this one says where it is instead of showing a stale
  // copy with a live looking composer. `elsewhereId` is that chat: it is
  // deliberately not `curSessionId`, since nothing here owns or shows it.
  let elsewhereId = null;
  function renderElsewhere(m) {
    snapshotCurrent();
    curSessionId = null;
    pendingModelChange = 0;
    setBody("thread");
    stopThreadLoading();
    elsewhereId = m.id || null;
    currentTitle = m.title || "Chat";
    el.thread.innerHTML = "";
    el.composer.classList.add("hidden");
    const where = m.where || "another chat surface";
    const box = document.createElement("div");
    box.className = "welcome";
    const icon = document.createElement("i");
    icon.className = "codicon codicon-link-external welcome-icon";
    const title = document.createElement("div");
    title.className = "welcome-title";
    title.textContent = "This chat is open in " + where;
    const sub = document.createElement("div");
    sub.className = "welcome-sub muted";
    sub.textContent = "A chat runs in one place at a time. Bring it here to carry on where it left off, agent and all.";
    const actions = document.createElement("div");
    actions.className = "welcome-actions";
    actions.appendChild(btn("Continue in " + (m.here || "this surface"), "", () =>
      vscode.postMessage({ type: "moveHere", id: elsewhereId })));
    actions.appendChild(btn("Show it in " + where, "secondary", () =>
      vscode.postMessage({ type: "revealSession", id: elsewhereId })));
    [icon, title, sub, actions].forEach((n) => box.appendChild(n));
    el.thread.appendChild(box);
    renderHeader();
    updateTerminateBtn();
  }
  function clearElsewhere() {
    if (!elsewhereId) return;
    elsewhereId = null;
    el.thread.innerHTML = "";
    el.composer.classList.remove("hidden");
  }

  el.historyBtn.addEventListener("click", () => {
    // Keep the session alive in the background and retain its transcript so
    // returning to it is instant, then show the list.
    snapshotCurrent();
    curSessionId = null;
    pendingModelChange = 0;
    vscode.postMessage({ type: "leaveToList" });
    setBody("list");
  });
  el.titleBtn.addEventListener("click", (e) => {
    if (body === "list") return;
    e.stopPropagation();
    // Clicking the session title renames it. In a tab the title is the tab's own,
    // renamed from its context menu, so it is not a control here.
    if (curSessionId && !inEditor()) vscode.postMessage({ type: "renameSession", id: curSessionId, title: currentTitle });
  });

  // --- Composer ------------------------------------------------------------

  function send() {
    const text = el.input.value.trim();
    if (!text) return;
    // Editing a queued message updates it in place (keeps its position in the
    // queue) rather than dropping it and appending a new one at the end.
    if (editingQueuedId && body !== "list") {
      vscode.postMessage({ type: "editQueued", id: editingQueuedId, text });
      finishQueuedEdit();
      return;
    }
    // In editRequests:input mode, the composer is editing a past request:
    // submitting rewinds to it and resends instead of appending a new turn.
    // Never in the list view, where the composer is a fresh new-chat box.
    if (editingTurn && body !== "list") {
      submitInputEdit(editingTurn, text);
      return;
    }
    const startNew = body === "list";
    if (startNew) {
      currentTitle = "Chat";
      setBody("thread");
    }
    vscode.postMessage({ type: "send", text, newSession: startNew, preserveTranscript: !startNew && threadHasContent() });
    el.input.value = "";
    savedDraft = "";
    closeAutocomplete();
    autosize();
    updateSendState();
  }

  // While Devin is working, a message can either wait its turn or take over. The
  // protocol cannot hand a message to a running prompt (Copilot calls that
  // steering, and its agents support it), so the second option ends the turn and
  // sends straight after it. Same split button as Copilot's: the primary half is
  // the default action, Alt flips it, and the chevron offers the other.
  // Both are a send, so both keep the send glyph: the queue is the plain one, and
  // taking over the turn carries the restart arrow. A plus would read as the attach
  // button sitting at the other end of the same toolbar.
  const SEND_ACTIONS = {
    queue: {
      icon: "codicon-newline",
      label: "Send to Queue",
      detail: "Send this after the current request finishes",
      post: (text) => ({ type: "send", text })
    },
    stopAndSend: {
      icon: "codicon-debug-restart",
      label: "Stop and Send",
      detail: "Stop what Devin is doing, then send this",
      post: (text) => ({ type: "stopAndSend", text })
    }
  };
  let altHeld = false;
  function defaultSendAction() {
    return caps.sendWhileWorking === "stopAndSend" ? "stopAndSend" : "queue";
  }
  function otherSendAction() {
    return defaultSendAction() === "queue" ? "stopAndSend" : "queue";
  }
  // What the primary half does right now: the default, or the other one while Alt
  // is held (VS Code flips the icon with the modifier, so it never lies).
  function primarySendAction() {
    return altHeld ? otherSendAction() : defaultSendAction();
  }
  function runSendAction(name) {
    const text = el.input.value.trim();
    if (!text) return;
    // Nothing is running, or the composer is borrowed for an edit: an ordinary send.
    if (!busy || editingQueuedId || editingTurn || body === "list") { send(); return; }
    vscode.postMessage(SEND_ACTIONS[name].post(text));
    el.input.value = "";
    savedDraft = "";
    closeAutocomplete();
    autosize();
    updateSendState();
  }
  el.send.addEventListener("click", () => runSendAction(primarySendAction()));
  el.sendMore.addEventListener("click", (e) => {
    e.stopPropagation();
    openSendMenu();
  });
  let sendFloater = null;
  // The chevron menu on a permission prompt, holding the grants that outlive it.
  let permissionFloater = null;
  function openSendMenu() {
    if (sendFloater) { sendFloater.close(); return; }
    const menu = document.createElement("div");
    menu.className = "dv-menu";
    [defaultSendAction(), otherSendAction()].forEach((name, i) => {
      const a = SEND_ACTIONS[name];
      const row = document.createElement("button");
      row.className = "dv-menu-item with-detail";
      row.appendChild(mkIcon(a.icon.replace("codicon-", "")));
      const text = document.createElement("span");
      text.className = "dv-menu-text";
      text.appendChild(Object.assign(document.createElement("span"), { textContent: a.label }));
      text.appendChild(Object.assign(document.createElement("span"), { className: "dv-menu-detail", textContent: a.detail }));
      const keys = document.createElement("span");
      keys.className = "dv-menu-keys";
      keys.textContent = i === 0 ? "Enter" : "Alt+Enter";
      row.append(text, keys);
      row.addEventListener("click", () => { if (sendFloater) sendFloater.close(); runSendAction(name); });
      menu.appendChild(row);
    });
    sendFloater = makeFloater(el.sendGroup, menu, "right", () => { sendFloater = null; });
  }
  // Holding Alt swaps the primary half, so what it will do is always what it shows.
  function trackAlt(e) {
    if (e.altKey === altHeld) return;
    altHeld = e.altKey;
    updateComposerButtons();
  }
  document.addEventListener("keydown", trackAlt);
  document.addEventListener("keyup", trackAlt);
  window.addEventListener("blur", () => { if (altHeld) { altHeld = false; updateComposerButtons(); } });

  // Whether this chat holds the keyboard. A keybinding's `when` cannot see into
  // a webview, so the panel has to say so for the host to publish it: without it
  // Ctrl/Cmd+1..9 keep meaning what they mean everywhere else in VS Code, even
  // with the cursor in the composer.
  const sayFocus = (value) => vscode.postMessage({ type: "chatFocus", value });
  window.addEventListener("focus", () => sayFocus(true));
  window.addEventListener("blur", () => sayFocus(false));
  if (document.hasFocus()) sayFocus(true);

  function stopTurn() {
    vscode.postMessage({ type: "cancel" });
    cancelPrompts();
  }
  el.stop.addEventListener("click", stopTurn);
  el.stop.title = "Stop (Esc)";

  // Close any open question/permission widgets (on Stop, or when the host says
  // the request was cancelled). They must not linger or be submittable after
  // the turn is stopped.
  function cancelPrompts() {
    // Let an open question hand its half given answers to the host before it goes,
    // so reopening the session brings them back.
    el.elicitationTray.querySelectorAll(".qc").forEach((w) => w.dispatchEvent(new Event("dv-teardown")));
    el.elicitationTray.innerHTML = "";
    // The scope menu hangs off the body, anchored to a button in the tray below,
    // so wiping the tray would otherwise leave it floating over nothing.
    if (permissionFloater) permissionFloater.close();
    el.permissionTray.innerHTML = "";
  }
  el.attach.addEventListener("click", () => vscode.postMessage({ type: "addContext" }));

  // While an IME is composing, the keys belong to the IME: Enter commits the
  // candidate and the arrows walk its list. Acting on them here sent the reading
  // instead of the word, and `preventDefault` took the composition down with it,
  // which made the composer unusable in Japanese, Chinese and Korean.
  function composing(e) {
    return e.isComposing || e.keyCode === 229;
  }
  el.input.addEventListener("keydown", (e) => {
    if (composing(e)) return;
    if (ac) {
      if (e.key === "ArrowDown") { e.preventDefault(); ac.index = (ac.index + 1) % ac.items.length; renderAutocomplete(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); ac.index = (ac.index - 1 + ac.items.length) % ac.items.length; renderAutocomplete(); return; }
      if ((e.key === "Enter" || e.key === "Tab") && ac.items.length) { e.preventDefault(); acceptAutocomplete(ac.items[ac.index]); return; }
      if (e.key === "Escape") { e.preventDefault(); closeAutocomplete(); return; }
    }
    // Escape before the menu has even opened still abandons the mention: its reply
    // is in flight, and finding files and symbols is slow enough that it usually
    // is. Without this the menu appeared after the user had given up on it.
    if (e.key === "Escape" && !ac && fileQueryToken !== null) {
      fileQueryToken = null;
    }
    if (e.key === "Escape" && editingQueuedId) { e.preventDefault(); cancelQueuedEdit(); return; }
    if (e.key === "Escape" && editingTurn) { e.preventDefault(); cancelInputEditing(); return; }
    // With nothing open to close, Escape from the composer stops the turn. VS Code
    // binds only Ctrl/Cmd+Esc (still handled below, from anywhere in the panel),
    // but a bare Escape is the reflex when the composer has focus.
    if (e.key === "Escape" && busy) { e.preventDefault(); stopTurn(); return; }
    // ArrowUp on an empty composer recalls your last message (VS Code's input
    // history), so it is quick to resend or tweak it.
    if (e.key === "ArrowUp" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !editingTurn && !editingQueuedId && el.input.value === "" && lastUserText) {
      e.preventDefault();
      el.input.value = lastUserText;
      el.input.setSelectionRange(lastUserText.length, lastUserText.length);
      autosize();
      updateSendState();
      return;
    }
    // Enter runs the default action, Alt+Enter the other one, the way VS Code
    // binds the queue and steer pair. Idle, both are an ordinary send.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      runSendAction(e.altKey ? otherSendAction() : defaultSendAction());
    }
  });

  el.input.addEventListener("input", () => { autosize(); updateAutocomplete(); updateSendState(); scheduleDraftSave(); });
  // Clicking away is a natural point to stop debouncing and store it.
  el.input.addEventListener("blur", () => saveDraft());

  // Unsent text is remembered by the host, per session (and for the "new chat"
  // box in the list), so leaving a chat, reloading it or closing the window keeps
  // the prompt you were part way through. Editing a queued or an already sent
  // message borrows the same box, so those keep out of the draft.
  let draftTimer = null;
  let savedDraft = null;
  // Which chat the text in the composer belongs to, so text typed in one is not
  // left sitting in another.
  let savedDraftKey = null;
  function draftKey() { return curSessionId || null; }
  function saveDraft() {
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    if (editingQueuedId || editingTurn) return;
    const text = el.input.value;
    if (text === savedDraft) return;
    savedDraft = text;
    savedDraftKey = draftKey();
    vscode.postMessage({ type: "draft", id: draftKey(), text });
  }
  function scheduleDraftSave() {
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, 400);
  }
  // This chat is about to be handed to the other surface. Everything only this
  // page knows goes back to the host first, and it waits for the reply: a draft
  // saves on a timer, and a question's answers are half given, so without this the
  // chat would arrive on the new surface with an empty composer and a blank
  // question.
  function flushState() {
    saveDraft();
    el.elicitationTray.querySelectorAll(".qc").forEach((w) => w.dispatchEvent(new Event("dv-teardown")));
    vscode.postMessage({ type: "stateFlushed" });
  }
  // Put the stored draft back after something else has borrowed the composer
  // (editing a queued or an already sent message).
  function restoreDraft() {
    if (!savedDraft || el.input.value) return;
    el.input.value = savedDraft;
  }
  // The stored draft fills the composer only when it is empty: a transcript
  // restored in the panel already carries the fresher text, and a message for a
  // session that has since been left is ignored.
  function applyDraft(m) {
    const id = m.id || null;
    if (id !== draftKey() || editingQueuedId || editingTurn) return;
    // A surface fills its new chat box before it is handed a session, so the
    // composer can still hold text meant for another chat. Untouched, it goes.
    if (savedDraftKey !== id && el.input.value && el.input.value === savedDraft) {
      el.input.value = "";
    }
    savedDraft = m.text || "";
    savedDraftKey = id;
    if (el.input.value || !m.text) return;
    el.input.value = m.text;
    autosize();
    updateSendState();
  }

  // VS Code cancels an in-progress request edit when you click another row or
  // outside the editor (finishedEditing on onDidFocusOutside). Mirror that:
  // a pointer down anywhere outside the open inline editor closes it. Capture
  // phase so it runs before the row's own click-to-edit handler.
  document.addEventListener("mousedown", (e) => {
    if (!inlineEditTurn) return;
    const ed = inlineEditTurn.editorEl;
    if (ed && !ed.contains(e.target)) finishEditing(inlineEditTurn);
  }, true);
  // Escape cancels the inline edit even when focus has left the textarea.
  document.addEventListener("keydown", (e) => {
    // Not while composing: dismissing an IME candidate list is an Escape too, and
    // taking it as "cancel this edit" threw away what had just been written.
    if (composing(e)) return;
    if (e.key === "Escape" && inlineEditTurn) finishEditing(inlineEditTurn);
  });

  // Copilot-style chat shortcuts (active whenever the panel is focused):
  //  - Ctrl/Cmd+Esc stops the current turn,
  //  - Ctrl/Cmd+.       opens the mode picker,
  //  - Ctrl/Cmd+Alt+.   opens the model picker.
  document.addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    if (e.key === "Escape") {
      if (busy) { e.preventDefault(); stopTurn(); }
      return;
    }
    if (e.code === "Period" && body === "thread") {
      e.preventDefault();
      openComposerDropdown(e.altKey ? el.modelDD : el.modeDD);
    }
  });

  // Open one of the composer's dropdown pickers (mode / model) by keyboard, by
  // triggering its button when its menu is not already open.
  function openComposerDropdown(container) {
    if (!container || container.classList.contains("hidden")) return;
    const btn = container.querySelector(".dd-btn");
    const menu = container.querySelector(".dd-menu");
    if (btn && (!menu || menu.classList.contains("hidden"))) btn.click();
  }

  function updateSendState() {
    updateComposerButtons();
  }

  // Show Send / Stop / Queue like Copilot: idle shows Send (disabled when empty);
  // while a turn runs, Stop is always available AND, as soon as you type, the
  // Send button turns into a Queue button (a click queues, same as Enter) so you
  // are not limited to the keyboard.
  function updateComposerButtons() {
    const hasText = !!el.input.value.trim();
    // The split only applies to a message sent into a running turn: an edit of a
    // queued or an already sent message is an ordinary submit.
    const split = busy && hasText && !editingQueuedId && !editingTurn && body !== "list";
    el.stop.classList.toggle("hidden", !busy);
    // Keep Send visible unless a turn is running with nothing typed (Stop only).
    el.send.classList.toggle("hidden", busy && !hasText);
    el.send.disabled = !hasText;
    el.sendGroup.classList.toggle("split", split);
    el.sendMore.classList.toggle("hidden", !split);
    const action = split ? SEND_ACTIONS[primarySendAction()] : null;
    el.send.title = action ? action.label + " (" + (altHeld ? "Alt+Enter" : "Enter") + ")" : "Send (Enter)";
    const icon = el.send.querySelector("i");
    if (icon) icon.className = "codicon " + (action ? action.icon : "codicon-newline");
    if (!split && sendFloater) sendFloater.close();
  }

  // The image types the agent can decode. One it cannot (svg, heic, avif) has to
  // be attached as a file: as an image block the whole request is rejected, and
  // the block stays in the session, so every later message in that chat is
  // rejected too.
  const IMAGE_TYPES = [
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "image/bmp", "image/tiff", "image/x-icon", "image/vnd.microsoft.icon"
  ];

  // A file handed over as bytes with no path (a paste, or an OS drop): an image
  // goes inline as base64 so the model can see it, anything else as its text.
  function attachRawFile(file, fallbackName) {
    const name = file.name || fallbackName;
    const reader = new FileReader();
    if (IMAGE_TYPES.indexOf(file.type) !== -1) {
      reader.onload = () => {
        const result = String(reader.result || "");
        vscode.postMessage({ type: "attachImage", name, mime: file.type, data: result.slice(result.indexOf(",") + 1) });
      };
      reader.readAsDataURL(file);
      return;
    }
    reader.onload = () => vscode.postMessage({ type: "attachDroppedText", name, text: String(reader.result || "") });
    reader.readAsText(file);
  }

  el.input.addEventListener("paste", (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.type && it.type.indexOf("image/") === 0) {
        const file = it.getAsFile();
        if (!file) continue;
        e.preventDefault();
        attachRawFile(file, "pasted-image");
      }
    }
  });

  function autosize() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(Math.max(el.input.scrollHeight, 52), 240) + "px";
  }

  // --- Scroll management ---------------------------------------------------
  // The transcript follows new content only while the user is pinned to the
  // bottom. Height changes from streaming text, expanding tool cards, loading
  // images, mermaid swap-in and collapse animations are followed via a
  // ResizeObserver, so the view never lags behind and does not yank the user
  // back when they have scrolled up to read.
  let stickToBottom = true;
  let pinning = false;
  let pinScheduled = false;
  // True while a session/load replay is streaming in. The transcript is hidden
  // behind the loading spinner and auto-scroll is frozen, so the user sees a
  // clean spinner instead of the whole history rendering and scroll-thrashing;
  // it is revealed and jumped to the bottom once, on `loaded`.
  let loadingSession = false;

  function distanceFromBottom() {
    return el.thread.scrollHeight - el.thread.scrollTop - el.thread.clientHeight;
  }
  function pinNow() {
    if (loadingSession) return;
    pinning = true;
    el.thread.scrollTop = el.thread.scrollHeight;
    updateScrollDownButton();
    // Release the guard next frame; by then we are at the bottom so a genuine
    // user scroll re-evaluates stick correctly.
    requestAnimationFrame(() => { pinning = false; });
  }
  // The jump-to-bottom button shows only when the user is scrolled up in a
  // thread (VS Code's .chat-scroll-down / .show-scroll-down), and clicking it
  // snaps to the bottom and re-sticks.
  function updateScrollDownButton() {
    const show = body === "thread" && distanceFromBottom() > 60;
    el.scrollDown.classList.toggle("visible", show);
  }
  el.scrollDown.addEventListener("click", () => forceScrollToBottom());
  // Pin after layout so scrollHeight reflects the just-added content.
  function schedulePin() {
    if (pinScheduled) return;
    pinScheduled = true;
    requestAnimationFrame(() => {
      pinScheduled = false;
      if (stickToBottom) pinNow();
    });
  }
  // Content changed: follow the bottom only if the user is pinned there.
  function scrollToBottom() { schedulePin(); }

  // Say something once, out loud, for anyone not watching the panel.
  //
  // Deliberately not the transcript itself: making that live would re-read a reply
  // on every streamed chunk, which is worse than silence. So this announces the
  // things that change what the user can do (a turn started or finished, a tool ran,
  // something is waiting on them) and leaves the reading of replies to the
  // role=log transcript.
  //
  // Repeating the same string is a no-op in most screen readers, so a marker is
  // toggled to force it to speak twice when it genuinely happened twice.
  let lastAnnouncement = "";
  function announce(text) {
    if (!el.announcer || !text) return;
    el.announcer.textContent = text === lastAnnouncement ? text + "\u200b" : text;
    lastAnnouncement = text;
  }

  // A user action (send, open/restore a session) must land at the bottom even
  // though the content keeps reflowing for a moment afterwards (the busy chrome
  // refresh, the "Working…" indicator, then the first tokens). Pinning once is
  // racy: a transient scroll during that reflow can flip stickToBottom off and
  // leave the new message just above the fold. So pin every frame for a short
  // window and refuse to un-stick during it.
  const now = () => (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now());
  let forcePinUntil = 0;
  function forcePinLoop() {
    pinNow();
    if (now() < forcePinUntil) requestAnimationFrame(forcePinLoop);
  }
  function forceScrollToBottom() {
    stickToBottom = true;
    // The test harness (jsdom) has no layout, so a timed loop adds nothing and
    // would just leave rAFs pending: a single pin is enough there.
    if (typeof ResizeObserver === "undefined") { schedulePin(); return; }
    const wasForcing = now() < forcePinUntil;
    forcePinUntil = now() + 450;
    if (!wasForcing) forcePinLoop();
  }

  // When the user expands/collapses a section in the transcript, keep that
  // section pinned where it was rather than following the stream to the bottom
  // (VS Code's getAnchoredScrollTop). Following is disabled so the growing
  // response can't fight what the user is reading; scrolling back to the bottom
  // (or the Stop/scroll-down button) re-engages it.
  let anchoring = false;
  function anchorAfterToggle(target, anchorTop) {
    stickToBottom = false;
    forcePinUntil = 0; // cancel any post-send force-pin so the expand wins
    if (anchorTop == null || typeof requestAnimationFrame === "undefined") return;
    anchoring = true;
    const until = now() + 340; // spans the collapse height animation
    const step = () => {
      if (!anchoring || !target.isConnected) { anchoring = false; return; }
      const cur = target.getBoundingClientRect().top;
      if (cur !== anchorTop) el.thread.scrollTop += cur - anchorTop;
      if (now() < until) requestAnimationFrame(step);
      else anchoring = false;
    };
    requestAnimationFrame(step);
  }
  // A genuine user scroll (wheel/touch) hands control back immediately.
  el.thread.addEventListener("wheel", () => { anchoring = false; }, { passive: true });
  el.thread.addEventListener("touchstart", () => { anchoring = false; }, { passive: true });

  // Detach only when the USER scrolls up (scrollTop decreases). Appended content
  // never moves scrollTop, so streaming can never accidentally detach us; a pin
  // only increases scrollTop. Re-attach whenever we are back at the bottom. This
  // fixes the response sometimes not following to the very end mid-stream.
  let lastScrollTop = 0;
  el.thread.addEventListener("scroll", () => {
    const st = el.thread.scrollTop;
    const movedUp = st < lastScrollTop - 2;
    lastScrollTop = st;
    updateScrollDownButton();
    // Ignore our own pins, the forced window, and the post-toggle anchor loop:
    // none of these are the user deciding to follow or leave the bottom.
    if (pinning || anchoring || now() < forcePinUntil) return;
    if (movedUp && distanceFromBottom() > 40) stickToBottom = false;
    else if (distanceFromBottom() <= 40) stickToBottom = true;
  }, { passive: true });

  // Follow height changes that fire no DOM mutation at scroll time (images
  // loading, mermaid swap-in, collapsible animations, font reflow). jsdom (the
  // test harness) has no ResizeObserver, so fall back to a no-op there.
  const contentRO = typeof ResizeObserver !== "undefined"
    ? new ResizeObserver(() => { if (stickToBottom) pinNow(); else updateScrollDownButton(); })
    : { observe() {}, unobserve() {} };
  // Auto-observe transcript children (turns, welcome, dividers) as they mount,
  // so their inner growth keeps us pinned.
  const contentMO = new MutationObserver((muts) => {
    for (const m of muts) {
      m.addedNodes.forEach((n) => { if (n.nodeType === 1) contentRO.observe(n); });
      m.removedNodes.forEach((n) => { if (n.nodeType === 1) { try { contentRO.unobserve(n); } catch { /* gone */ } } });
    }
    if (stickToBottom) schedulePin();
    else updateScrollDownButton();
  });
  contentMO.observe(el.thread, { childList: true });
  Array.prototype.forEach.call(el.thread.children, (n) => contentRO.observe(n));

  // Match VS Code's dynamic working-border speed: the "comet" duration scales
  // with input width (clamped 1.4s-2.5s) so it travels at a consistent visual
  // pace at any panel width. Mirrors chatInputPart._updateWorkingProgressAnimationDuration.
  let workingDuration = 0;
  function updateWorkingDuration(width) {
    const safe = Math.max(50, width || el.inputBox.clientWidth || 300);
    const d = Math.min(2.5, Math.max(1.4, 0.55 + 0.075 * Math.sqrt(safe)));
    if (Math.abs(d - workingDuration) < 0.05) return;
    workingDuration = d;
    el.inputBox.style.setProperty("--dv-input-working-duration", d.toFixed(2) + "s");
    // Force a one-frame restart so a new duration takes effect mid-flight
    // (browsers otherwise keep the old duration until the current cycle ends).
    if (el.inputBox.classList.contains("busy")) {
      el.inputBox.classList.add("anim-restart");
      void el.inputBox.offsetWidth;
      requestAnimationFrame(() => el.inputBox.classList.remove("anim-restart"));
    }
  }

  // Responsive composer: progressively drop labels, then whole controls, as the
  // panel narrows, so the toolbar never overlaps. Worst case keeps just Send.
  if (window.ResizeObserver) {
    const ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width;
      el.inputBox.classList.toggle("cmp-sm", w < 380); // labels -> icons only
      el.inputBox.classList.toggle("cmp-xs", w < 280); // hide mode/model/context
      el.inputBox.classList.toggle("cmp-xxs", w < 190); // only Send remains
      updateWorkingDuration(w);
    });
    ro.observe(el.inputBox);
  }

  function isFileReference(href) {
    const target = href.split(/[?#]/, 1)[0];
    if (!target || target === "./" || target === "../") return false;
    return /^[a-z]:[\\/]/i.test(href) || /^file:/i.test(href) ||
      (!/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith("//"));
  }
  function fileReferenceTarget(href) {
    if (/^file:/i.test(href)) return href;
    const path = href.split(/[?#]/, 1)[0];
    try { return decodeURIComponent(path); } catch { return path; }
  }
  function fileReferenceIconTarget(href) {
    return fileReferenceTarget(href).split(/[?#]/, 1)[0];
  }

  // Clickable anchors in assistant/user text. External links are opened by VS Code's
  // own webview link handling, so we only open file and relative links in the editor.
  el.thread.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (!href || href.startsWith("#") || !isFileReference(href)) return;
    e.preventDefault();
    e.stopPropagation();
    vscode.postMessage({ type: "openFile", path: fileReferenceTarget(href) });
  });

  // --- Drag and drop context (files, images), VS Code chat style -----------
  // Drop files/images onto the chat to attach them as context. Internal drags
  // (Explorer, editor tabs) arrive as a text/uri-list of file URIs; OS/app drops
  // arrive as real files (images inline as base64, other files as text). A full
  // cover overlay with a paperclip pill mirrors VS Code's .chat-dnd-overlay.
  const dndOverlay = document.createElement("div");
  dndOverlay.className = "chat-dnd-overlay";
  dndOverlay.innerHTML = '<span class="attach-context-overlay-text"><i class="codicon codicon-attach"></i><span class="overlay-text">Attach as Context</span></span>';
  el.chat.appendChild(dndOverlay);

  // The drag payloads we can turn into context. VS Code's internal drags carry
  // every dragged resource in `application/vnd.code.uri-list` (or ResourceURLs /
  // CodeFiles) and truncate the standard `text/uri-list` to the FIRST resource,
  // so all of them have to be read or a multi file drag attaches only one.
  const DND_URI_TYPES = ["application/vnd.code.uri-list", "ResourceURLs", "CodeFiles", "text/uri-list"];

  function dndSupported(e) {
    if (el.chat.classList.contains("hidden")) return false; // boot / setup screen
    const t = (e.dataTransfer && e.dataTransfer.types) || [];
    const has = (x) => Array.prototype.indexOf.call(t, x) !== -1;
    return has("Files") || DND_URI_TYPES.some(has);
  }

  // Collect filesystem paths from whichever drag type is present, in the order
  // that preserves the full selection.
  function dropPaths(dt) {
    const out = [];
    const get = (t) => { try { return (dt.getData && dt.getData(t)) || ""; } catch { return ""; } };
    // A file URI travels as it is: only the host can turn one into a path, since
    // a Windows URI (file:///c%3A/...) is not a path with the scheme cut off.
    const addUri = (line) => {
      const s = String(line || "").trim();
      if (!s || s.charAt(0) === "#" || /^https?:/i.test(s)) return;
      out.push(s);
    };
    const internal = get("application/vnd.code.uri-list");
    if (internal) internal.split(/\r?\n/).forEach(addUri);
    if (!out.length) {
      const res = get("ResourceURLs"); // JSON array of URI strings
      if (res) { try { JSON.parse(res).forEach(addUri); } catch { /* not JSON */ } }
    }
    if (!out.length) {
      const files = get("CodeFiles"); // JSON array of plain fs paths
      if (files) { try { JSON.parse(files).forEach((p) => { if (p) out.push(String(p)); }); } catch { /* not JSON */ } }
    }
    if (!out.length) {
      const std = get("text/uri-list");
      if (std) std.split(/\r?\n/).forEach(addUri);
    }
    return out;
  }
  // VS Code hands any drag that reaches a webview back to the workbench, so the
  // editor can own the drop: its host script watches the webview window for drag
  // events and drops pointer-events on our iframe as soon as it sees one
  // (webviewWindowDragMonitor). That is what made the overlay flash and vanish,
  // and the file open in an editor instead of attaching. So every drag event is
  // claimed in the capture phase and stopped there, before it reaches the window
  // listeners the host installed, which keeps the drag ours. Claiming them on the
  // window rather than the chat also covers the sessions panel and the edges,
  // where a stray dragenter would otherwise hand the whole drag away.
  //
  // A drag that starts inside VS Code (Explorer, editor tabs) is blocked from its
  // very first dragstart, before any event reaches us, so those only arrive while
  // Shift is held: that is VS Code's own gesture for dropping into a webview.
  let dndDepth = 0;
  function onDnd(type, handler) {
    window.addEventListener(type, (e) => {
      if (!dndSupported(e)) return;
      e.preventDefault();
      e.stopPropagation();
      handler(e);
    }, true);
  }
  onDnd("dragenter", () => {
    dndDepth++;
    dndOverlay.classList.add("visible");
  });
  onDnd("dragover", (e) => {
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  });
  onDnd("dragleave", () => {
    dndDepth = Math.max(0, dndDepth - 1);
    if (dndDepth === 0) dndOverlay.classList.remove("visible");
  });
  onDnd("drop", (e) => {
    hideDndOverlay();
    handleDrop(e.dataTransfer);
  });
  function hideDndOverlay() {
    dndDepth = 0;
    dndOverlay.classList.remove("visible");
  }
  // A drag that ends or is cancelled outside the panel never fires dragleave on
  // the container, which would otherwise leave the overlay stuck on screen.
  window.addEventListener("dragend", hideDndOverlay);
  window.addEventListener("drop", hideDndOverlay);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideDndOverlay(); });

  function handleDrop(dt) {
    // Prefer paths (internal Explorer / editor-tab drags, and OS drops that
    // expose file URLs): attach each by path, letting the host read it.
    const paths = dropPaths(dt);
    if (paths.length) {
      paths.forEach((p) => vscode.postMessage({ type: "addMention", path: p }));
      return;
    }
    // Otherwise we have raw files (an OS/app drop with no path): images inline,
    // everything else as its text content. A folder cannot be read as either, so
    // it is picked out through the entries API, which is also the only way to see
    // that one was dropped at all. Both have to be taken off the items list while
    // the drop event is still on the stack, before any of the reading below.
    const dropped = [];
    Array.prototype.forEach.call(dt.items || [], (it) => {
      if (it.kind !== "file") return;
      dropped.push({ dir: it.webkitGetAsEntry ? it.webkitGetAsEntry() : null, file: it.getAsFile() });
    });
    if (!dropped.length) {
      Array.prototype.forEach.call(dt.files || [], (f) => dropped.push({ dir: null, file: f }));
    }
    dropped.forEach(({ dir, file }) => {
      if (dir && dir.isDirectory) {
        attachDroppedFolder(dir);
        return;
      }
      if (file) attachRawFile(file, "file");
    });
  }

  // A folder dropped from outside VS Code. The OS drag carries no path, so the
  // host cannot list it the way it lists a folder dragged from the Explorer: read
  // its top level here instead and attach that as the listing. readEntries hands
  // back a batch at a time and an empty batch means the end.
  function attachDroppedFolder(dir) {
    const reader = dir.createReader();
    const names = [];
    const done = () => vscode.postMessage({ type: "attachDroppedFolder", name: dir.name, entries: names.sort() });
    const read = () => reader.readEntries((batch) => {
      if (!batch.length || names.length >= 500) {
        done();
        return;
      }
      batch.forEach((en) => {
        if (!en.name.startsWith(".")) names.push(en.isDirectory ? en.name + "/" : en.name);
      });
      read();
    }, done);
    read();
  }

  // --- Dropdown component (VS Code style) ----------------------------------

  function createDropdown(container, onSelect, opts) {
    opts = opts || {};
    const btn = document.createElement("button");
    btn.className = "dd-btn";
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");
    if (opts.ariaLabel) btn.setAttribute("aria-label", opts.ariaLabel);
    const btnIcon = document.createElement("span");
    btnIcon.className = "dd-icon";
    const label = document.createElement("span");
    label.className = "dd-label";
    const chev = document.createElement("i");
    chev.className = "codicon codicon-chevron-down";
    btn.appendChild(btnIcon);
    btn.appendChild(label);
    btn.appendChild(chev);

    function iconFor(v) {
      if (opts.buttonIcon) return opts.buttonIcon(v) || "";
      if (opts.staticIcon) return opts.staticIcon;
      const it = items.find((x) => x.value === v);
      return it && it.icon ? it.icon : "";
    }
    function updateBtnIcon() {
      const ic = iconFor(current);
      btnIcon.classList.toggle("hidden", !ic);
      if (!ic) { btnIcon.innerHTML = ""; return; }
      if (ic.indexOf("img:") === 0) {
        // Render brand SVGs as a currentColor mask so they are monochrome and
        // adapt to light/dark automatically (no separate variants needed).
        const rest = ic.slice(4);
        const sp = rest.indexOf(" ");
        const key = rest.slice(0, sp);
        const url = rest.slice(sp + 1).replace(/"/g, "%22");
        btnIcon.innerHTML = `<span class="dd-brand dd-brand--${key}" style="-webkit-mask-image:url('${url}');mask-image:url('${url}')"></span>`;
      } else {
        btnIcon.innerHTML = `<i class="codicon ${ic}"></i>`;
      }
    }
    const menu = document.createElement("div");
    menu.className = "dd-menu hidden";
    container.appendChild(btn);
    container.appendChild(menu);

    let items = [];
    let current = "";
    let filterText = "";

    function close() {
      menu.classList.add("hidden");
      btn.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
      opts.onClose?.();
    }
    function labelFor(v) {
      const it = items.find((x) => x.value === v);
      return it ? it.name : v || "";
    }
    function makeRow(it) {
      const row = document.createElement("div");
      row.className = "dd-item" + (it.value === current ? " selected" : "");
      const check = document.createElement("span");
      check.className = "dd-check";
      if (it.value === current) check.innerHTML = '<i class="codicon codicon-check"></i>';
      row.appendChild(check);
      if (it.icon) {
        const ic = document.createElement("i");
        ic.className = "codicon dd-item-icon " + it.icon;
        row.appendChild(ic);
      }
      const content = document.createElement("span");
      content.className = "dd-item-content";
      const txt = document.createElement("span");
      txt.className = "dd-item-name";
      txt.textContent = it.name;
      content.appendChild(txt);
      if (it.detail) {
        const detail = document.createElement("span");
        detail.className = "dd-item-detail";
        detail.textContent = it.detail;
        content.appendChild(detail);
      }
      for (const badge of it.badges || []) {
        const badgeEl = document.createElement("span");
        badgeEl.className = "dd-item-badge";
        badgeEl.textContent = badge;
        content.appendChild(badgeEl);
      }
      row.appendChild(content);
      if (opts.itemAction && it.value) {
        const action = document.createElement("button");
        const label = it.pinned ? `Unpin ${it.name}` : `Pin ${it.name}`;
        action.type = "button";
        action.className = "dd-item-action";
        action.title = label;
        action.setAttribute("aria-label", label);
        action.appendChild(mkIcon(it.pinned ? "pinned" : "pin"));
        action.addEventListener("click", (ev) => {
          ev.stopPropagation();
          opts.itemAction(it);
        });
        row.appendChild(action);
      }
      if (opts.itemHover) row.addEventListener("mouseenter", () => opts.itemHover(row, it));
      if (opts.itemLeave) row.addEventListener("mouseleave", () => opts.itemLeave(row, it));
      row.addEventListener("click", (ev) => {
        ev.stopPropagation();
        current = it.value;
        label.textContent = labelFor(current);
        updateBtnIcon();
        close();
        onSelect(it.value);
      });
      return row;
    }
    function renderMenu() {
      menu.innerHTML = "";
      const showFilter = items.length > 10;
      const rows = document.createElement("div");
      rows.className = "dd-rows";
      const renderRows = () => {
        rows.innerHTML = "";
        const q = filterText.trim().toLowerCase();
        let lastGroup = null;
        let shown = 0;
        items.forEach((it) => {
          if (it.sep) {
            if (!q) rows.appendChild(Object.assign(document.createElement("div"), { className: "dd-sep" }));
            return;
          }
          if (q && !it.name.toLowerCase().includes(q) && !(it.group || "").toLowerCase().includes(q)) return;
          if (it.group && it.group !== lastGroup) {
            const h = document.createElement("div");
            h.className = "dd-group";
            h.textContent = it.group;
            rows.appendChild(h);
          }
          lastGroup = it.group || null;
          rows.appendChild(makeRow(it));
          shown++;
        });
        if (!shown) {
          const empty = document.createElement("div");
          empty.className = "dd-empty";
          empty.textContent = "No matches";
          rows.appendChild(empty);
        }
      };
      if (showFilter) {
        const f = document.createElement("input");
        f.className = "dd-filter";
        f.type = "text";
        f.placeholder = "Filter\u2026";
        f.value = filterText;
        f.addEventListener("click", (e) => e.stopPropagation());
        f.addEventListener("keydown", (e) => e.stopPropagation());
        f.addEventListener("input", () => { filterText = f.value; renderRows(); });
        menu.appendChild(f);
        setTimeout(() => f.focus(), 0);
      }
      menu.appendChild(rows);
      renderRows();
    }
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      document.querySelectorAll(".dd-menu").forEach((m) => { if (m !== menu) m.classList.add("hidden"); });
      document.querySelectorAll(".dd-btn.open").forEach((b) => {
        if (b !== btn) { b.classList.remove("open"); b.setAttribute("aria-expanded", "false"); }
      });
      filterText = "";
      renderMenu();
      menu.classList.toggle("hidden");
      const open = !menu.classList.contains("hidden");
      btn.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });

    return {
      set(newItems, newCurrent) {
        items = newItems || [];
        current = newCurrent || (items[0] && items[0].value) || "";
        label.textContent = labelFor(current);
        updateBtnIcon();
        container.classList.toggle("hidden", items.length === 0);
      },
      setCurrent(v) { current = v; label.textContent = labelFor(v); updateBtnIcon(); },
      close
    };
  }

  document.addEventListener("click", () => {
    document.querySelectorAll(".dd-menu").forEach((m) => m.classList.add("hidden"));
    document.querySelectorAll(".dd-btn.open").forEach((b) => { b.classList.remove("open"); b.setAttribute("aria-expanded", "false"); });
    closeUsagePopup();
    closeTitleMenu();
  });

  // --- Autocomplete --------------------------------------------------------

  function updateAutocomplete() {
    const value = el.input.value;
    const caret = el.input.selectionStart ?? value.length;
    if (value.startsWith("/") && value.indexOf(" ") === -1) {
      const q = value.slice(1).toLowerCase();
      const items = commands.filter((c) => c.name.toLowerCase().includes(q)).slice(0, 40)
        .map((c) => ({ kind: "slash", name: c.name, description: c.description || "" }));
      openAutocomplete(items);
      return;
    }
    const before = value.slice(0, caret);
    const at = before.match(/@([^\s@]*)$/);
    if (at) {
      fileQueryToken = at[1];
      vscode.postMessage({ type: "queryFiles", query: fileQueryToken });
      return;
    }
    closeAutocomplete();
  }

  function openAutocomplete(items) {
    if (!items.length) { closeAutocomplete(); return; }
    ac = { items, index: 0 };
    renderAutocomplete();
  }

  function renderAutocomplete() {
    if (!ac) return;
    el.autocomplete.innerHTML = "";
    el.autocomplete.classList.remove("hidden");
    let activeRow = null;
    ac.items.forEach((it, i) => {
      const row = document.createElement("div");
      row.className = "ac-item" + (i === ac.index ? " active" : "");
      const icon = document.createElement("i");
      icon.className = "codicon " + (it.kind === "slash" ? "codicon-terminal" : "codicon-file");
      const primary = document.createElement("span");
      primary.className = "ac-primary";
      primary.textContent = it.kind === "slash" ? "/" + it.name : it.label;
      const secondary = document.createElement("span");
      secondary.className = "ac-secondary";
      secondary.textContent = it.description || it.detail || "";
      row.appendChild(icon);
      row.appendChild(primary);
      row.appendChild(secondary);
      row.addEventListener("mousedown", (ev) => { ev.preventDefault(); acceptAutocomplete(it); });
      el.autocomplete.appendChild(row);
      if (i === ac.index) activeRow = row;
    });
    // Keep the keyboard-highlighted item visible as the selection moves past the
    // top or bottom edge of the scrollable menu.
    if (activeRow) activeRow.scrollIntoView({ block: "nearest" });
  }

  function closeAutocomplete() {
    ac = null;
    // Forget which query is expected, or a reply still in flight would reopen the
    // menu over a composer the user has already moved on from. Finding files and
    // symbols takes a round trip through the workspace and the language servers,
    // so Escape, a Backspace out of the mention, or a space all beat it back, and
    // the menu then ate the Enter meant to send the message.
    fileQueryToken = null;
    el.autocomplete.classList.add("hidden");
    el.autocomplete.innerHTML = "";
  }

  function acceptAutocomplete(item) {
    if (item.kind === "slash") {
      el.input.value = "/" + item.name + " ";
      el.input.focus();
      closeAutocomplete();
      autosize();
      updateSendState();
      return;
    }
    const value = el.input.value;
    const caret = el.input.selectionStart ?? value.length;
    const before = value.slice(0, caret).replace(/@([^\s@]*)$/, "");
    el.input.value = before + value.slice(caret);
    vscode.postMessage({ type: "addMention", path: item.path });
    el.input.focus();
    closeAutocomplete();
    autosize();
    // The @query has left the box, and setting `value` fires no input event, so
    // without this Send stays drawn as though there were still something to send.
    updateSendState();
  }

  // --- Turns & thread ------------------------------------------------------
  // The thread is a list of turns. Each turn pairs a user request with the
  // assistant response that follows it (assistant text, thinking, tool cards,
  // plan). This request/response model mirrors VS Code's chat and is what makes
  // edit-in-place, checkpoints (restore), and undo possible.

  let turns = [];
  let currentTurn = null;
  let turnSeq = 0;
  // The head node id after the most recently completed turn. A turn's revert
  // target ("checkpoint") is the head captured before it ran (headBefore).
  let lastHead = null;
  // Whether `lastHead` is a valid revert target on the current conversation
  // expansion. False right after a reload (the next prompt re-expands and
  // orphans it); true after a live turn completion or an instant restore.
  let lastHeadReliable = false;
  // Feature gates from the host (revert capability + settings).
  // The surface comes from the page itself as well as from the host, so the chrome
  // is right from the first paint instead of being drawn as a side panel and then
  // corrected once a session loads.
  let caps = { revert: false, subagentControl: false, editRequests: "inline", checkpoints: true, showFileChanges: true, confirmRemoval: true, verbose: true, progressBorder: true, contextUsage: true, inlineReferencesStyle: "box", thinkingStyle: "fixedScrolling", streamAnim: "rise", panelSide: "right", sendWhileWorking: "queue", surface: document.body.dataset.surface === "editor" ? "editor" : "view" };
  // An editor tab holds exactly one chat: no session list, no back button and no
  // terminate control (closing the tab is how a chat in a tab is stopped).
  // Browsing sessions is the side panel's job.
  function inEditor() { return caps.surface === "editor"; }
  // Pending revert-preview requests keyed by token.
  const previewWaiters = new Map();
  let previewSeq = 0;
  function cancelPreviewWaiters() {
    for (const waiter of [...previewWaiters.values()]) waiter(null);
  }
  // A revert we asked the host to perform but has not yet confirmed. We only
  // trim the transcript once the host replies "reverted", so a failed revert
  // (which leaves the conversation unchanged) does not desync the UI.
  let pendingRevert = null; // { head, showRestored } | null

  function respTarget() {
    return currentTurn ? currentTurn.resp : el.thread;
  }

  function actionBtn(icon, title, onClick) {
    const b = document.createElement("button");
    b.className = "msg-action";
    b.title = title;
    b.innerHTML = `<i class="codicon ${icon}"></i>`;
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(b); });
    return b;
  }

  // Copy button with VS Code's two-icon cross-fade (copy <-> check), mirroring
  // chat.css's .chat-copy-action-icon-copy/-copied layered swap. `cls` is the
  // base button class (msg-action in toolbars, code-btn in code blocks).
  function copyButton(title, cls, getText) {
    const b = document.createElement("button");
    b.className = cls + " dv-copy";
    b.title = title;
    b.innerHTML =
      '<span class="dv-copy-icons">' +
      '<i class="codicon codicon-copy dv-copy-i"></i>' +
      '<i class="codicon codicon-check dv-copy-i2"></i>' +
      "</span>";
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      vscode.postMessage({ type: "copyText", text: getText() });
      b.classList.add("copied");
      setTimeout(() => b.classList.remove("copied"), 1200);
    });
    return b;
  }

  // Shared collapsible, built div-first (not <details>) so the grid
  // 1fr<->0fr height + opacity collapse can animate, mirroring VS Code's
  // chatCollapsibleContentPart. Native <details> accessibility is preserved via
  // role=button + aria-expanded + Enter/Space toggling on the header. Returns
  // { root, header (fill with the summary row), body (fill with content),
  // setCollapsed, isCollapsed }. Add `dv-nocollapse` to the root to freeze it.
  function makeCollapsible(rootClass, opts) {
    opts = opts || {};
    const root = document.createElement("div");
    root.className = rootClass + (opts.startCollapsed === false ? "" : " dv-collapsed");
    const header = document.createElement("div");
    header.className = "dv-collapsible-header";
    header.setAttribute("role", "button");
    header.tabIndex = 0;
    const anim = document.createElement("div");
    anim.className = "dv-collapsible-anim";
    const inner = document.createElement("div");
    inner.className = "dv-collapsible-anim-inner";
    anim.appendChild(inner);
    root.appendChild(header);
    root.appendChild(anim);
    const sync = () => {
      const collapsed = root.classList.contains("dv-collapsed");
      header.setAttribute("aria-expanded", collapsed ? "false" : "true");
      // The disclosure names what it would do, which for a command row is
      // VS Code's Show Output / Hide Output.
      const chevron = header.querySelector(".tool-chevron");
      if (chevron) chevron.title = collapsed ? "Show Output" : "Hide Output";
    };
    const setCollapsed = (v) => {
      root.classList.toggle("dv-collapsed", !!v);
      // Hidden means hidden: without this, Tab walks into a folded section and
      // find in page matches text nobody can see. VS Code marks it inert too.
      inner.inert = !!v;
      sync();
    };
    // `userToggled` records that the user opened/closed this section by hand, so
    // callers never auto-collapse or auto-expand it out from under them (VS
    // Code persists the manual state and gates auto-collapse on it).
    let userToggled = false;
    const userToggle = () => {
      if (root.classList.contains("dv-nocollapse")) return;
      userToggled = true;
      const willExpand = root.classList.contains("dv-collapsed");
      // Expanding a section in the transcript means the user wants to read it:
      // capture where it sits, stop following the stream, and pin it there
      // afterwards (VS Code's getAnchoredScrollTop) so the growing response can't
      // yank them to the bottom. Collapsing keeps the normal follow behaviour.
      const anchor = willExpand && el.thread.contains(root);
      const anchorTop = anchor ? root.getBoundingClientRect().top : null;
      setCollapsed(!willExpand);
      if (anchor) anchorAfterToggle(root, anchorTop);
      if (opts.onUserToggle) opts.onUserToggle();
    };
    header.addEventListener("click", (e) => {
      if (e.target.closest && e.target.closest(".file-pill")) return;
      e.stopPropagation();
      userToggle();
    });
    header.addEventListener("keydown", (e) => {
      if (e.target.closest && e.target.closest(".file-pill")) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); userToggle(); }
    });
    sync();
    const ctrl = {
      root, header, body: inner, setCollapsed,
      isCollapsed: () => root.classList.contains("dv-collapsed"),
      userToggled: () => userToggled
    };
    // So anything holding the element alone can still fold it (a replay does).
    root._dvCollapse = ctrl;
    inner.inert = root.classList.contains("dv-collapsed");
    return ctrl;
  }

  // Create a new turn shell (request container + checkpoint row + response
  // container) and make it current. `text` is the user's message.
  function newTurn(mid, text) {
    finalizeBlock();
    hideWelcome();
    const container = document.createElement("div");
    container.className = "turn";
    const req = document.createElement("div");
    req.className = "turn-request";
    const reqBody = document.createElement("div");
    reqBody.className = "req-body";
    const reqText = document.createElement("div");
    reqText.className = "req-text bubble";
    reqBody.appendChild(reqText);
    req.appendChild(reqBody);
    const resp = document.createElement("div");
    resp.className = "turn-response";
    const checkpoint = document.createElement("div");
    checkpoint.className = "checkpoint-row hidden";
    container.appendChild(req);
    container.appendChild(checkpoint);
    container.appendChild(resp);
    el.thread.appendChild(container);
    const turn = {
      id: "t" + (++turnSeq), mid, container, req, reqBody, reqText, resp, checkpoint,
      text: text || "", headBefore: lastHead, headBeforeReliable: lastHeadReliable,
      headAfter: null, editing: false,
      createdAt: Date.now(), completedAt: null, model: currentModelLabel
    };
    turns.push(turn);
    currentTurn = turn;
    if (text !== undefined) setTurnText(turn, text);
    buildTurnChrome(turn);
    // The previous last turn is no longer last: move Retry to this one.
    refreshRetryButtons();
    scrollToBottom();
    return turn;
  }

  function setTurnText(turn, text) {
    turn.text = text;
    if (text) turn.reqText.innerHTML = renderMarkdown(text);
    lastUserText = text;
  }

  // What was attached to a request, above the message and aligned with it, the way
  // VS Code's chat keeps a request's context attached to it (chat-attached-context
  // in an interactive-request). A picture is its own thumbnail rather than a
  // generic file glyph, so a screenshot is recognisable at a glance.
  function setTurnAttachments(turn, items) {
    if (turn.attachRow) turn.attachRow.remove();
    turn.attachRow = attachedContextRow(items);
    if (!turn.attachRow) return;
    // Above the bubble rather than inside it, which is where VS Code puts it: the
    // context is what the message came with, not part of what was typed.
    turn.req.insertBefore(turn.attachRow, turn.reqBody);
  }

  // The row of pills itself, shared by a sent request and a queued one.
  function attachedContextRow(items) {
    const list = (items || []).filter((a) => a && a.label);
    if (!list.length) return null;
    const row = document.createElement("div");
    row.className = "chat-attached-context";
    list.forEach((a) => {
      const pill = document.createElement("span");
      pill.className = "chat-attached-context-attachment";
      pill.title = a.label;
      if (a.thumb) {
        const img = document.createElement("img");
        img.className = "chat-attached-context-pill-image";
        img.src = a.thumb;
        img.alt = "";
        pill.appendChild(img);
      } else {
        const icon = document.createElement("i");
        icon.className = "codicon " + fileIconFor(a.label) + " attachment-icon";
        icon.setAttribute("aria-hidden", "true");
        pill.appendChild(icon);
      }
      const name = document.createElement("span");
      name.className = "attachment-name";
      name.textContent = a.label;
      pill.appendChild(name);
      row.appendChild(pill);
    });
    return row;
  }

  // Request hover toolbar (Copy, Edit) + a persistent response footer toolbar
  // (Copy, Retry) + a hover timestamp + the checkpoint row (Restore). Rebuilt
  // whenever caps or busy state change.
  function buildTurnChrome(turn) {
    // A turn is complete once it is no longer the one actively streaming.
    turn.container.classList.toggle("complete", turn !== currentTurn || !busy);

    // Request toolbar (top-right, on hover): Copy + Edit.
    let reqActions = turn.req.querySelector(".msg-actions");
    if (reqActions) reqActions.remove();
    reqActions = document.createElement("div");
    reqActions.className = "msg-actions req-actions";
    reqActions.appendChild(copyButton("Copy", "msg-action", () => turn.text));
    // VS Code shows an explicit Edit pencil only in hover/input modes; in inline
    // mode you click the request text to edit (no pencil).
    if (canEditTurn(turn) && caps.editRequests !== "inline") {
      reqActions.appendChild(actionBtn("codicon-edit", "Edit Request", () => startEditing(turn)));
    }
    turn.req.appendChild(reqActions);

    // Request timestamp under the bubble, revealed with the turn hover chrome
    // (live turns only; replayed history has no known original time).
    let ts = turn.req.querySelector(".turn-ts");
    if (ts) ts.remove();
    if (caps.verbose && !turn.replayed && turn.createdAt) {
      ts = document.createElement("div");
      ts.className = "turn-ts";
      ts.appendChild(timeFlip("Sent ", turn.createdAt));
      turn.req.appendChild(ts);
    }

    // Click-to-edit on the whole bubble (inline and input modes both start on a
    // click; input mode routes the text into the composer).
    turn.reqBody.onclick = null;
    turn.reqBody.removeAttribute("title");
    if ((caps.editRequests === "inline" || caps.editRequests === "input") && canEditTurn(turn)) {
      turn.req.classList.add("editable-inline");
      turn.reqBody.onclick = () => startEditing(turn);
      // VS Code's inline mode shows a "Click to Edit" hover hint on the request.
      if (caps.editRequests === "inline") turn.reqBody.title = "Click to Edit";
    } else {
      turn.req.classList.remove("editable-inline");
    }

    // Keyboard affordances on the request bubble (VS Code makes request rows
    // focusable): Enter/Space edits, Delete/Backspace restores to this turn.
    turn.reqBody.tabIndex = 0;
    turn.reqBody.setAttribute("role", "button");
    turn.reqBody.onkeydown = (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && canRestoreTurn(turn)) {
        e.preventDefault();
        doRestore(turn);
      } else if ((e.key === "Enter" || e.key === " ") && canEditTurn(turn)) {
        e.preventDefault();
        startEditing(turn);
      }
    };

    // Checkpoint row (Restore Checkpoint) between request and response.
    renderCheckpointRow(turn);

    // Response footer (Copy, Retry) + completion time, persistent under a
    // completed response, mirroring VS Code's ChatMessageFooter.
    buildTurnFooter(turn);
  }

  // True when the turn's response actually rendered something (text, tool card,
  // image, plan, ...), so we don't show a Copy button on an empty answer. The
  // working row is the panel talking, not the answer, so it does not count.
  function turnHasResponse(turn) {
    if (!turn.resp) return false;
    const real = [...turn.resp.children].filter((c) => !c.classList.contains("working"));
    return real.length > 0 || real.map((c) => c.textContent).join("").trim().length > 0;
  }

  function buildTurnFooter(turn) {
    let footer = turn.footer;
    if (footer) footer.remove();
    footer = document.createElement("div");
    footer.className = "chat-footer";
    // VS Code's ChatMessageFooter order: Copy first (order 1), then the rest.
    // Copy only when there is actually a response to copy (hidden on an empty
    // answer). The footer is rebuilt on completion, so the content is present.
    if (turnHasResponse(turn)) {
      footer.appendChild(copyButton("Copy", "msg-action", () => turn.resp.innerText.trim()));
    }
    // Retry regenerates the response IN PLACE (rewind + re-run the same request);
    // it never appends a duplicate. Only offer it when we have a revert target on
    // the current expansion (turnRevertable) and only on the LAST response (the
    // is-last class gates visibility, kept accurate by refreshRetryButtons).
    if (caps.revert && turnRevertable(turn)) {
      const retry = actionBtn("codicon-refresh", "Retry", () => {
        if (!turn.text || busy) return;
        revertAndResend(turn, turn.text);
      });
      retry.classList.add("footer-retry");
      footer.appendChild(retry);
    }
    footer.classList.toggle("is-last", turns[turns.length - 1] === turn);
    // Right-aligned "time • model" detail, matching VS Code's chat-footer-details.
    // What the turn actually cost hangs off it: the CLI reports its own figures
    // (how long, how many ACUs, how many messages), so they are shown on hover
    // rather than guessed at or crammed into the row.
    if (caps.verbose && !turn.replayed && turn.completedAt) {
      const det = document.createElement("span");
      det.className = "chat-footer-details";
      det.appendChild(timeFlip("", turn.completedAt));
      if (turn.tookMs) det.appendChild(document.createTextNode("  \u2022  " + fmtDuration(turn.tookMs)));
      if (turn.model) det.appendChild(document.createTextNode("  \u2022  " + turn.model));
      if (turn.stats && turn.stats.length) {
        det.title = turn.stats.map((d) => d.label + ": " + d.value).join("\n");
      }
      footer.appendChild(det);
    }
    turn.footer = footer;
    turn.container.appendChild(footer);
  }

  // Keep Retry only on the last turn's footer as turns are added or trimmed.
  function refreshRetryButtons() {
    const last = turns[turns.length - 1];
    for (const t of turns) {
      if (t.footer) t.footer.classList.toggle("is-last", t === last);
    }
  }

  function refreshTurnChrome() {
    // Rebuilding a turn's controls replaces the elements, so a keyboard user standing
    // on a Copy, a Retry or a Restore Checkpoint would be dropped back to the top of
    // the panel every time a turn started or finished. The turn holding focus keeps
    // its controls, and gets them rebuilt when focus moves on.
    turns.forEach((t) => {
      if (t.editing) return;
      if (t.container && t.container.contains(document.activeElement)) {
        t.chromeStale = true;
        return;
      }
      t.chromeStale = false;
      buildTurnChrome(t);
    });
  }
  // The deferred half of the above: whatever was left alone is brought up to date
  // once the user is no longer inside it.
  document.addEventListener("focusout", () => {
    setTimeout(() => {
      const pending = turns.filter((t) => t.chromeStale && !t.editing
        && !(t.container && t.container.contains(document.activeElement)));
      if (!pending.length) return;
      pending.forEach((t) => { t.chromeStale = false; buildTurnChrome(t); });
    }, 0);
  }, true);

  // A turn is revertable only when we hold a node id that is valid on the CURRENT
  // conversation expansion. The agent re-expands the conversation on session
  // load and assigns fresh node ids, orphaning any id captured before the load
  // (verified: reverting to a pre-load head fails with "Invalid params"). So:
  //  - the first live turn of a fresh session (headBefore null) reverts by
  //    starting a new session;
  //  - any later turn is revertable only if its head-before was captured live
  //    from a completed turn on this expansion (headBeforeReliable);
  //  - replayed/historical turns and the first turn after a reload are NOT
  //    revertable, because their "before" node is orphaned by re-expansion.
  function turnRevertable(turn) {
    if (turn.headBefore == null) {
      return !turn.replayed;
    }
    return !!turn.headBeforeReliable;
  }
  // After a wake/reload the agent re-expands the conversation with fresh node
  // ids, so every revert target captured for the (restored) turns is orphaned.
  // Downgrade them to non-revertable, exactly like a freshly replayed reload.
  function invalidateRevertHeads() {
    lastHeadReliable = false;
    turns.forEach((t) => { t.headBeforeReliable = false; t.headAfterReliable = false; });
    refreshTurnChrome();
  }
  function canEditTurn(turn) {
    return caps.revert && caps.editRequests !== "none" && !busy && turnRevertable(turn);
  }
  function canRestoreTurn(turn) {
    return caps.revert && caps.checkpoints && !busy && turnRevertable(turn);
  }

  function renderCheckpointRow(turn) {
    const row = turn.checkpoint;
    row.innerHTML = "";
    if (!canRestoreTurn(turn)) { row.classList.add("hidden"); return; }
    row.classList.remove("hidden");
    const left = document.createElement("span");
    left.className = "checkpoint-line-left";
    const btn = document.createElement("button");
    btn.className = "checkpoint-restore";
    // VS Code renders this as a plain text label (no leading icon).
    btn.innerHTML = '<span>Restore Checkpoint</span>';
    btn.title = "Restores workspace and chat to this point";
    const right = document.createElement("span");
    right.className = "checkpoint-line-right";
    // Inline two-state confirm: the label morphs to a shimmering "Discard Edits"
    // and an X cancel appears, exactly like VS Code's ChatRestoreCheckpoint item.
    let confirming = false;
    const cancel = document.createElement("button");
    cancel.className = "checkpoint-cancel hidden";
    cancel.innerHTML = '<i class="codicon codicon-close"></i>';
    cancel.title = "Cancel restoring this checkpoint";
    // How many of this chat's file edits the rewind will leave on disk, so the
    // confirmation can say what really happens instead of promising a workspace
    // that goes back with the conversation.
    let staying = 0;
    const setConfirming = (v) => {
      confirming = v;
      row.classList.toggle("confirming", v);
      cancel.classList.toggle("hidden", !v);
      const span = btn.querySelector("span");
      const files = staying === 1 ? "1 file edit" : staying + " file edits";
      span.textContent = v ? (staying ? "Rewind Chat" : "Discard Edits") : "Restore Checkpoint";
      span.classList.toggle("dv-shimmer", v);
      btn.title = v
        ? (staying
          ? `Rewinds the chat to this point. ${files} stay on disk, listed under changed files to keep or undo.`
          : "Confirm restoring this checkpoint and discarding later edits")
        : "Restores this chat to this point";
    };
    cancel.addEventListener("click", (e) => { e.stopPropagation(); setConfirming(false); });
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (confirming) { setConfirming(false); doRestore(turn); return; }
      const needs = await revertNeedsConfirm(turn);
      if (needs === null) return;
      if (needs) { staying = needs.staying || 0; setConfirming(true); }
      else doRestore(turn);
    });
    row.appendChild(left);
    row.appendChild(btn);
    // Fork: carry on from here in a new chat. The agent copies the conversation up
    // to this point into a session of its own, so unlike a restore this discards
    // nothing and touches no file, and needs no confirmation.
    if (caps.revert && turn.headBefore != null) {
      const fork = document.createElement("button");
      fork.className = "checkpoint-fork";
      fork.innerHTML = '<i class="codicon codicon-git-branch"></i>';
      fork.title = "Continue from here in a new chat, leaving this one as it is";
      fork.setAttribute("aria-label", "Fork this chat from here");
      fork.addEventListener("click", (e) => {
        e.stopPropagation();
        setConfirming(false);
        vscode.postMessage({ type: "revertFork", target: turn.headBefore });
      });
      row.appendChild(fork);
    }
    row.appendChild(cancel);
    row.appendChild(right);
  }

  // Restore: rewind to before this turn and drop the prompt text back into the
  // composer (do not auto-run), matching VS Code.
  function doRestore(turn) {
    // A rewind during a running turn fights the prompt for the channel, and the
    // keyboard path already refuses it. The mouse path can still reach a stale
    // button: the controls of a turn holding focus are left alone until focus moves,
    // so its row can outlive the state it was drawn for.
    if (busy || !turnRevertable(turn)) {
      return;
    }
    if (turn.headBefore == null) {
      // No prior node: the host starts a fresh session and posts "clear",
      // which resets the transcript for us (no trim needed here).
      vscode.postMessage({ type: "revertExecute", newSession: true });
    } else {
      // Defer trimming + the "restored" divider until the host confirms.
      pendingRevert = { head: turn.headBefore, showRestored: true };
      vscode.postMessage({ type: "revertExecute", head: turn.headBefore });
    }
    el.input.value = turn.text;
    el.input.focus();
    autosize();
    updateSendState();
  }

  // A "Checkpoint restored" divider left at the rewind point, matching VS Code's
  // restored-checkpoint row (fading lines + label). There is no redo: re-running
  // from a rewind cannot be relied on to land in the same place. Forking a turn
  // into a new chat is the way to keep both.
  function renderRestoredRow() {
    const prev = el.thread.querySelector(".restored-row:not(.ended-row)");
    if (prev) prev.remove();
    const row = document.createElement("div");
    row.className = "restored-row";
    const left = document.createElement("span");
    left.className = "restored-line";
    const label = document.createElement("span");
    label.className = "restored-label";
    label.textContent = "Checkpoint restored";
    const right = document.createElement("span");
    right.className = "restored-line";
    row.appendChild(left);
    row.appendChild(label);
    row.appendChild(right);
    el.thread.appendChild(row);
    scrollToBottom();
  }

  // Servers whose warning has been read and sent away, for this chat.
  const mcpDismissed = new Set();

  // MCP servers the agent could not reach. The tool calls they would have offered
  // simply never happen, so without saying this the chat looks capable of things
  // it is not. One row per chat, above the composer, rebuilt as more turn up.
  function renderMcpProblems(servers) {
    // Once it has been read it is noise, so it can be sent away. A server that
    // fails later is new news and says so again, and a session change starts over.
    const list = (servers || []).filter((s) => !mcpDismissed.has(s.name));
    let box = document.getElementById("mcp-problems");
    if (!list.length) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement("div");
      box.id = "mcp-problems";
      box.className = "tray-card mcp-card";
      el.permissionTray.parentElement.insertBefore(box, el.permissionTray);
    }
    box.innerHTML = "";
    const head = document.createElement("div");
    head.className = "error-head";
    const icon = document.createElement("i");
    icon.className = "codicon codicon-warning";
    const msg = document.createElement("span");
    msg.textContent = list.length === 1
      ? "The MCP server " + list[0].name + " did not start, so its tools are not available."
      : list.length + " MCP servers did not start, so their tools are not available: " +
        list.map((s) => s.name).join(", ");
    const close = actionBtn("codicon-close", "Dismiss", () => {
      list.forEach((s) => mcpDismissed.add(s.name));
      box.remove();
    });
    close.classList.add("mcp-dismiss");
    head.append(icon, msg, close);
    box.appendChild(head);
    const detail = document.createElement("div");
    detail.className = "mcp-detail muted";
    detail.textContent = list.map((s) => s.message).join("\n");
    box.appendChild(detail);
    // Dismiss sends this one away. These two say how much longer than that: the
    // rest of this window, or for good (the devin.showMcpWarnings setting).
    const mute = document.createElement("div");
    mute.className = "mcp-mute";
    mute.append(
      linkBtn("Don't show again in this window", () => {
        vscode.postMessage({ type: "muteMcpWarnings", scope: "window" });
        box.remove();
      }),
      linkBtn("Don't show again", () => {
        vscode.postMessage({ type: "muteMcpWarnings", scope: "always" });
        box.remove();
      })
    );
    box.appendChild(mute);
  }

  // A plain text action, VS Code's inline link button.
  function linkBtn(text, onClick) {
    const b = document.createElement("button");
    b.className = "dv-link-btn";
    b.textContent = text;
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    return b;
  }

  // What the turn cost, from the CLI's own figures. It supplies the labels, so the
  // footer shows what it was given rather than a wording of our own.
  function applyTurnStats(m) {
    const turn = currentTurn || turns[turns.length - 1];
    if (!turn) return;
    turn.stats = (m.dimensions || []).filter((d) => d.label && d.value);
    if (m.model) turn.model = m.model;
    if (m.totalTimeMs) turn.tookMs = m.totalTimeMs;
    if (turn.footer) buildTurnFooter(turn);
  }

  // The agent behind this chat has stopped: it was terminated, it exited after
  // sitting idle, or it crashed. Only an editor tab is told, since the side panel
  // says the same thing with the gray dot in its list. The conversation stays on
  // screen and the next message starts it up again.
  function renderSessionEnded() {
    setBusy(false);
    hideWorking();
    finalizeBlock();
    hideWelcome();
    const prev = el.thread.querySelector(".ended-row");
    if (prev) prev.remove();
    const row = document.createElement("div");
    row.className = "restored-row ended-row";
    const label = document.createElement("span");
    label.className = "restored-label";
    label.textContent = "The agent has stopped. Send a message to start it again.";
    row.appendChild(Object.assign(document.createElement("span"), { className: "restored-line" }));
    row.appendChild(label);
    row.appendChild(Object.assign(document.createElement("span"), { className: "restored-line" }));
    el.thread.appendChild(row);
    forceScrollToBottom();
  }

  // Ask the host to preview the revert; returns true if it would discard edits
  // or has irreversible actions and confirmation is enabled.
  function revertNeedsConfirm(turn) {
    if (!caps.confirmRemoval || turn.headBefore == null) return Promise.resolve(false);
    const token = "pv" + (++previewSeq);
    const sessionId = curSessionId;
    return new Promise((resolve) => {
      let timer;
      const finish = (value) => {
        if (!previewWaiters.has(token)) return;
        previewWaiters.delete(token);
        clearTimeout(timer);
        resolve(sessionId === curSessionId ? value : null);
      };
      previewWaiters.set(token, (msg) => {
        if (!msg) { finish(null); return; }
        // Files this chat has changed that the agent's plan does not cover. They
        // stay on disk through the rewind, so this is the case most worth stopping
        // for, and it is the one the agent reports nothing about.
        const staying = msg.pendingFiles || 0;
        if (msg.error || !msg.result) { finish(staying > 0 ? { staying } : false); return; }
        const r = msg.result;
        const planned = (r.fileActions && r.fileActions.length) || 0;
        const warnings = (r.irreversibleWarnings && r.irreversibleWarnings.length) || 0;
        if (!planned && !warnings && !staying) { finish(false); return; }
        finish({ staying: planned ? 0 : staying });
      });
      timer = setTimeout(() => finish(false), 4000);
      vscode.postMessage({ type: "revertPreview", head: turn.headBefore, token });
    });
  }

  // Remove this turn and every turn after it from the DOM/model.
  function trimTurnsFrom(turn) {
    const idx = turns.indexOf(turn);
    if (idx < 0) return;
    for (let i = turns.length - 1; i >= idx; i--) {
      turns[i].container.remove();
      turns.splice(i, 1);
    }
    currentTurn = turns[turns.length - 1] || null;
    if (currentTurn) {
      lastHead = currentTurn.headAfter;
      lastHeadReliable = !!currentTurn.headAfterReliable;
    } else {
      lastHead = null;
      lastHeadReliable = false;
    }
    refreshRetryButtons();
  }

  // --- Edit a request in place --------------------------------------------

  // The turn currently being edited in the bottom composer (editRequests:input).
  let editingTurn = null;

  // The turn whose request is being edited inline (VS Code allows only one at a
  // time). Kept module-level so a new edit, a click elsewhere or Escape can
  // close it.
  let inlineEditTurn = null;

  function startEditing(turn) {
    if (!canEditTurn(turn)) return;
    if (caps.editRequests === "input") { startInputEditing(turn); return; }
    if (turn.editing) return;
    // Only one request editable at a time: close any other open editor first.
    if (inlineEditTurn && inlineEditTurn !== turn) finishEditing(inlineEditTurn);
    inlineEditTurn = turn;
    document.body.classList.add("editing-request");
    turn.editing = true;
    turn.req.classList.add("editing");
    turn.reqText.classList.add("hidden");
    const box = document.createElement("div");
    box.className = "req-editor";
    // Clicks inside the editor must not bubble to reqBody.onclick (which would
    // immediately restart editing and make Cancel/Send look like no-ops).
    box.addEventListener("click", (e) => e.stopPropagation());
    const ta = document.createElement("textarea");
    ta.className = "req-editor-input";
    ta.value = turn.text;
    const row = document.createElement("div");
    row.className = "req-editor-actions";
    const cancelBtn = btn("Cancel", "secondary", () => finishEditing(turn));
    const sendBtn = btn("Send", "primary", () => submitEdit(turn, ta.value));
    row.appendChild(cancelBtn);
    row.appendChild(sendBtn);
    box.appendChild(ta);
    box.appendChild(row);
    turn.reqBody.appendChild(box);
    turn.editorEl = box;
    const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(Math.max(ta.scrollHeight, 32), 240) + "px"; };
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => {
      // Submitting an edit rewinds the conversation, so a candidate-confirm taken
      // for a submit would discard turns as well as mangling the text.
      if (composing(e)) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finishEditing(turn); }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitEdit(turn, ta.value); }
    });
    grow();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    // Dim the turns that a submit would discard.
    markDiscardable(turn, true);
  }

  function finishEditing(turn) {
    turn.editing = false;
    turn.req.classList.remove("editing");
    turn.reqText.classList.remove("hidden");
    if (turn.editorEl) { turn.editorEl.remove(); turn.editorEl = null; }
    markDiscardable(turn, false);
    if (inlineEditTurn === turn) inlineEditTurn = null;
    if (!inlineEditTurn) document.body.classList.remove("editing-request");
  }

  // Rewind to before `turn` and resend `text` (shared by inline + input edits).
  // Trimming is deferred to the host's "reverted" confirmation so a failed
  // revert does not remove turns that are, in fact, still there.
  function revertAndResend(turn, text) {
    if (turn.headBefore == null) {
      // Host starts a fresh session (posts "clear") then resends the text.
      vscode.postMessage({ type: "revertExecute", newSession: true, resendText: text });
    } else {
      pendingRevert = { head: turn.headBefore, showRestored: false };
      vscode.postMessage({ type: "revertExecute", head: turn.headBefore, resendText: text });
    }
  }

  // Nothing about the editor changes while the host is asked what the rewind would
  // discard, and that answer is a round trip away, so a user who saw nothing happen
  // presses Enter again. Both submits then rewound, which sends the edited prompt
  // twice and lands the second rewind under the resend of the first. The turn is
  // held for the duration, and the state is re-checked afterwards, since a turn can
  // start while the question is still open.
  async function submitEdit(turn, text) {
    text = (text || "").trim();
    if (!text || turn.submitting) return;
    turn.submitting = true;
    try {
      const needs = await revertNeedsConfirm(turn);
      if (needs === null) return;
      if (needs && !(await confirmDiscard())) return;
      if (!canEditTurn(turn)) return;
      finishEditing(turn);
      revertAndResend(turn, text);
    } finally {
      turn.submitting = false;
    }
  }

  // --- Edit a request from the bottom composer (editRequests:input) ---------

  function startInputEditing(turn) {
    // Only one input edit at a time; re-target if already editing another.
    if (editingTurn && editingTurn !== turn) cancelInputEditing();
    if (editingQueuedId) cancelQueuedEdit();
    editingTurn = turn;
    el.input.value = turn.text;
    el.inputBox.classList.add("editing-request");
    showEditingBanner("Editing message", cancelInputEditing);
    markDiscardable(turn, true);
    el.input.focus();
    el.input.setSelectionRange(el.input.value.length, el.input.value.length);
    autosize();
    updateSendState();
  }

  function cancelInputEditing() {
    if (!editingTurn) return;
    markDiscardable(editingTurn, false);
    editingTurn = null;
    el.inputBox.classList.remove("editing-request");
    removeEditingBanner();
    el.input.value = "";
    restoreDraft();
    autosize();
    updateSendState();
  }

  async function submitInputEdit(turn, text) {
    if (turn.submitting) return;
    turn.submitting = true;
    try {
      const needs = await revertNeedsConfirm(turn);
      if (needs === null) return;
      if (needs && !(await confirmDiscard())) return;
      if (!canEditTurn(turn)) return;
      cancelInputEditing();
      revertAndResend(turn, text);
    } finally {
      turn.submitting = false;
    }
  }

  function showEditingBanner(text, onCancel) {
    removeEditingBanner();
    const bar = document.createElement("div");
    bar.className = "input-editing-banner";
    bar.id = "input-editing-banner";
    const label = document.createElement("span");
    label.className = "input-editing-label";
    label.innerHTML = '<i class="codicon codicon-edit"></i><span></span>';
    label.querySelector("span").textContent = text || "Editing message";
    const cancel = document.createElement("button");
    cancel.className = "chip-x";
    cancel.title = "Cancel edit (Esc)";
    cancel.innerHTML = '<i class="codicon codicon-close"></i>';
    cancel.addEventListener("click", (e) => { e.stopPropagation(); (onCancel || cancelInputEditing)(); });
    bar.appendChild(label);
    bar.appendChild(cancel);
    el.inputBox.insertBefore(bar, el.inputBox.firstChild);
  }

  function removeEditingBanner() {
    const b = document.getElementById("input-editing-banner");
    if (b) b.remove();
  }

  function markDiscardable(fromTurn, on) {
    const idx = turns.indexOf(fromTurn);
    if (idx < 0) return;
    for (let i = idx; i < turns.length; i++) {
      turns[i].container.classList.toggle("discardable", on && i !== idx);
    }
  }

  // In-thread confirmation shown before an edit/restore discards later turns'
  // file edits, replacing the native confirm() dialog. Resolves true to proceed.
  // A "Don't ask again" checkbox persists the preference to the host setting.
  function confirmDiscard() {
    return new Promise((resolve) => {
      const box = cwShell();
      cwTitle(box, "Discard later edits?");
      const body = cwBody(box);
      const m = document.createElement("div");
      m.className = "cw-message";
      m.textContent = "This removes this request and everything after it, and undoes the file edits those turns made.";
      body.appendChild(m);
      const dont = document.createElement("label");
      dont.className = "cw-dontask";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      const sp = document.createElement("span");
      sp.textContent = "Don't ask again";
      dont.appendChild(cb);
      dont.appendChild(sp);
      body.appendChild(dont);
      const row = cwButtons(box);
      const done = (ok) => {
        if (ok && cb.checked) {
          caps.confirmRemoval = false;
          vscode.postMessage({ type: "setConfig", key: "editing.confirmEditRequestRemoval", value: false });
        }
        box.remove();
        resolve(ok);
      };
      row.appendChild(btn("Discard and resend", "primary", () => done(true)));
      row.appendChild(btn("Cancel", "secondary", () => done(false)));
      el.permissionTray.appendChild(box);
    });
  }

  const SHELL_LANGS = new Set(["bash", "sh", "shell", "zsh", "console", "powershell", "ps", "ps1", "bat", "cmd"]);

  function codeBtn(icon, title, onClick) {
    const b = document.createElement("button");
    b.className = "code-btn";
    b.title = title;
    b.innerHTML = `<i class="codicon ${icon}"></i>`;
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); onClick(b); });
    return b;
  }

  function enhanceCodeBlocks(container) {
    if (!container) return;
    container.querySelectorAll("pre.code-block:not(.mermaid-src)").forEach((pre) => {
      if (pre.dataset.enhanced) return;
      pre.dataset.enhanced = "1";
      const code = pre.querySelector("code");
      const getText = () => (code ? code.textContent : pre.textContent) || "";
      const lang = (pre.getAttribute("data-lang") || "").toLowerCase();
      const bar = document.createElement("div");
      bar.className = "code-toolbar";
      bar.appendChild(copyButton("Copy", "code-btn", getText));
      bar.appendChild(codeBtn("codicon-insert", "Insert at cursor", () => vscode.postMessage({ type: "insertAtCursor", text: getText() })));
      bar.appendChild(codeBtn("codicon-go-to-file", "Apply to file", () => vscode.postMessage({ type: "applyToFile", text: getText() })));
      if (SHELL_LANGS.has(lang)) {
        bar.appendChild(codeBtn("codicon-terminal", "Run in terminal", () => vscode.postMessage({ type: "runInTerminal", text: getText() })));
      }
      pre.appendChild(bar);
    });
  }

  // Mermaid is heavy (~MBs), so it ships as its own bundle and is fetched only
  // the first time a diagram actually appears. The promise is cached (even on
  // failure) so we never inject the script twice.
  let mermaidPromise = null;
  function loadMermaid() {
    if (mermaidPromise) return mermaidPromise;
    const src = document.body.dataset.mermaidSrc;
    if (!src) return (mermaidPromise = Promise.reject(new Error("mermaid unavailable")));
    mermaidPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      const nonce = document.body.dataset.nonce;
      if (nonce) s.setAttribute("nonce", nonce);
      s.onload = () => {
        const m = window.__mermaid;
        if (!m) { reject(new Error("mermaid failed to load")); return; }
        const dark = document.body.classList.contains("vscode-dark") ||
          document.body.classList.contains("vscode-high-contrast");
        try {
          m.initialize({
            startOnLoad: false,
            securityLevel: "strict",
            theme: dark ? "dark" : "default",
            // A diagram it cannot parse must throw and clean up after itself.
            // Left to draw its own error, it paints a bomb and "Syntax error in
            // text" into the temporary node it hung off document.body and then
            // leaves it there, which lands under the whole panel, outside the
            // transcript entirely. We keep the source block instead.
            suppressErrorRendering: true
          });
        } catch (e) { /* keep going */ }
        resolve(m);
      };
      s.onerror = () => reject(new Error("mermaid script error"));
      document.head.appendChild(s);
    });
    return mermaidPromise;
  }
  // Upgrade any completed mermaid source blocks in `container` to rendered SVG.
  // Called on turn finalisation, so the fence is closed and the source stable.
  let mermaidSeq = 0;
  function renderMermaid(container) {
    if (!container) return;
    const blocks = [...container.querySelectorAll("pre.mermaid-src:not([data-mermaid-done])")];
    if (!blocks.length) return;
    loadMermaid().then((m) => {
      blocks.forEach((pre) => {
        if (pre.dataset.mermaidDone) return;
        pre.dataset.mermaidDone = "1";
        const codeEl = pre.querySelector("code");
        const src = (codeEl ? codeEl.textContent : pre.textContent) || "";
        const id = "mmd-" + (++mermaidSeq);
        Promise.resolve()
          .then(() => m.render(id, src))
          .then(({ svg }) => {
            const wrap = document.createElement("div");
            wrap.className = "mermaid-diagram";
            wrap.innerHTML = svg;
            pre.replaceWith(wrap);
          })
          .catch(() => { pre.classList.add("mermaid-error"); });
      });
    }).catch(() => { /* mermaid unavailable: leave the source blocks in place */ });
  }

  // File/symbol references in assistant text render as VS Code style inline anchor
  // chips: a bordered pill with a file-type icon. External links stay plain. With
  // inlineReferences.style === "link" they stay as plain links. Clicks are handled
  // by the delegated thread listener.
  function unenhanceAnchor(a) {
    const label = a.querySelector(".file-pill-name");
    if (label) {
      while (label.firstChild) a.insertBefore(label.firstChild, label);
      label.remove();
    }
    const icon = a.querySelector(".file-pill-icon");
    if (icon) icon.remove();
    a.classList.remove("file-change", "file-pill");
    a.removeAttribute("tabindex");
    a.removeAttribute("role");
    delete a.dataset.anchored;
  }
  function enhanceCodeFileReferences(container) {
    if (!container) return;
    container.querySelectorAll("code").forEach((code) => {
      if (code.closest("pre")) return;
      const match = /^\[([^\]]+)\]\((.+)\)$/i.exec((code.textContent || "").trim());
      if (!match) return;
      const rawTarget = match[2].trim().replace(/\s+(?:"[^"]*"|'[^']*'|\([^)]*\))$/, "");
      const target = rawTarget.startsWith("<") && rawTarget.endsWith(">")
        ? rawTarget.slice(1, -1)
        : rawTarget;
      if (!isFileReference(target)) return;
      const link = document.createElement("a");
      link.setAttribute("href", target);
      link.textContent = match[1];
      code.replaceWith(link);
    });
  }
  function enhanceAnchors(container) {
    if (!container) return;
    enhanceCodeFileReferences(container);
    container.querySelectorAll("a[href]").forEach((a) => {
      const href = a.getAttribute("href") || "";
      if (caps.inlineReferencesStyle === "link") {
        if (a.dataset.anchored) unenhanceAnchor(a);
        return;
      }
      if (!href || href.startsWith("#") || !isFileReference(href) || a.dataset.anchored) return;
      a.dataset.anchored = "1";
      a.classList.add("file-change", "file-pill");
      a.tabIndex = 0;
      a.setAttribute("role", "button");
      if (!a.dataset.filePillKeys) {
        a.dataset.filePillKeys = "1";
        a.addEventListener("keydown", (e) => {
          if (!a.classList.contains("file-pill")) return;
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); a.click(); }
        });
      }
      const icon = document.createElement("i");
      icon.className = "codicon " + fileIconFor(fileReferenceIconTarget(href)) + " file-pill-icon";
      icon.setAttribute("aria-hidden", "true");
      a.insertBefore(icon, a.firstChild);
      const label = document.createElement("span");
      label.className = "file-pill-name";
      while (a.childNodes.length > 1) label.appendChild(a.childNodes[1]);
      a.appendChild(label);
    });
  }
  function sameMid(a, b) { return (a || null) === (b || null); }

  // Rendering is throttled so a fast stream doesn't re-parse the whole buffer
  // on every chunk (which is O(n^2) for long turns). Normal turns render on the
  // next animation frame (snappy); once the open buffer is large, re-parsing it
  // 60x/second is wasteful, so it falls back to a coarser ~120ms cadence to cap
  // CPU. `finalizeBlock` always renders directly, so the final state is exact.
  let renderScheduled = false;
  let lastRenderAt = 0;
  function scheduleRender() {
    if (renderScheduled) return;
    renderScheduled = true;
    const run = () => { renderScheduled = false; lastRenderAt = now(); renderOpenBlock(); };
    if (block && block.buffer && block.buffer.length > 20000) {
      setTimeout(run, Math.max(0, 120 - (now() - lastRenderAt)));
    } else {
      requestAnimationFrame(run);
    }
  }
  // Split the reasoning buffer into a chain of steps, one per blank-line
  // separated block, and render each as a node on the connector timeline. A
  // fenced code block can legally contain blank lines, so keep the whole
  // buffer as a single step when one is present rather than tearing it apart.
  function thinkingSteps(text) {
    const trimmed = text.replace(/\s+$/, "");
    if (!trimmed) return [];
    if (trimmed.includes("```")) return [trimmed];
    return trimmed.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean);
  }
  function renderThinkingItems(b) {
    const steps = thinkingSteps(b.buffer);
    b.body.innerHTML = steps
      .map(
        (step) =>
          '<div class="thinking-item"><i class="codicon codicon-circle-filled thinking-icon"></i>' +
          '<div class="thinking-item-content">' + renderMarkdown(step) + "</div></div>"
      )
      .join("");
    b.body.querySelectorAll(".thinking-item-content").forEach(enhanceAnchors);
  }
  function renderOpenBlock() {
    if (!block) return;
    if (block.kind === "thinking") {
      // Keep the fixed height peek pinned to the latest reasoning, unless the user
      // has expanded it to read back through the chain of thought, or has scrolled
      // up inside it, which is the same intention and was not being honoured: every
      // thought that arrived took them back to the bottom of a section they were
      // reading. Measured before the items are redrawn, like the box a command's
      // output goes into.
      const box = block.scrollEl;
      const at = box ? box.scrollTop : 0;
      const pinned = !box || box.scrollHeight - at - box.clientHeight < 24;
      renderThinkingItems(block);
      if (box && block.peek && !block.collapse.userToggled()) {
        box.scrollTop = pinned ? box.scrollHeight : at;
      }
    } else if (block.kind === "user") {
      if (block.turn) { block.turn.text = block.buffer; block.turn.reqText.innerHTML = renderMarkdown(block.buffer); }
    } else {
      block.bubble.innerHTML = renderMarkdown(block.buffer);
      if (block.kind === "assistant") { enhanceCodeBlocks(block.bubble); enhanceAnchors(block.bubble); }
    }
    // Follow the stream if the user is pinned to the bottom (scrollToBottom is a
    // no-op otherwise); the ResizeObserver also keeps us pinned as height grows.
    scrollToBottom();
  }

  // Assistant content (text, thinking, tools, plan) always belongs to a turn.
  // If none is open (e.g. an assistant-first history replay), start a headless
  // one with no request row.
  function ensureTurn() {
    if (!currentTurn) {
      newTurn(undefined, undefined);
      currentTurn.container.classList.add("headless");
    }
    return currentTurn;
  }

  // Close the current block, running any finalisation it needs.
  function finalizeBlock() {
    if (!block) return;
    renderOpenBlock();
    // The fence is now closed, so any mermaid source in a settled assistant
    // message can be upgraded to a rendered diagram.
    if (block.kind === "assistant" && block.bubble) renderMermaid(block.bubble);
    if (block.kind === "thinking") {
      if (block.timer) clearInterval(block.timer);
      if (block.details) {
        block.details.classList.remove("thinking-active");
        block.details.classList.remove("thinking-peek");
      }
      // A thought that turned out to say nothing is not a step: left in, it is a
      // node on the chain with nothing beside it, and a gap in the run where the
      // reader looks for something that was never there.
      if (block.details && !block.body.textContent.trim()) {
        dropStep(block.details);
        block = null;
        return;
      }
      // A thought of its own folds to its header once it is done, as VS Code
      // folds a finished thinking part. Reasoning inside a run does not: it has
      // no header to fold to, and the run folding takes it with it.
      if (block.peek && block.collapse && block.details && !inRun(block.details) && !block.collapse.userToggled()) {
        block.collapse.setCollapsed(true);
      }
      if (block.label) {
        block.label.textContent = block.replayed
          ? "Thought"
          : `Thought for ${Math.max(1, Math.round((Date.now() - block.start) / 1000))}s`;
      }
    }
    block = null;
  }

  // A subtle "Working…" placeholder shown whenever a turn is running with
  // nothing of its own to show: after send until the first token, and again in
  // every gap between one action finishing and the next starting, which is where
  // a working agent used to look like a stopped one (VS Code's
  // ChatWorkingProgressContentPart).
  let workingEl = null;
  // What the agent says it is up to. VS Code's pool, and its wording: one verb,
  // no ellipsis, and no spinner beside it, since the shimmer is the thing that
  // says it is alive.
  const WORKING_DWELL_MS = 1200;
  // Streamed text pauses constantly between tokens, so the row only appears once
  // the text has actually stopped (VS Code's WORKING_CAUGHT_UP_DEBOUNCE_MS).
  const WORKING_DEBOUNCE_MS = 750;
  const workingBag = {};
  let workingWord = null;
  let workingWordAt = 0;
  let lastProseAt = 0;
  let workingTimer = null;
  function pickWorkingWord() {
    // The row is rebuilt constantly while a turn streams. Without a dwell the
    // word would flicker through the list instead of reading as one thought.
    if (workingWord && now() - workingWordAt < WORKING_DWELL_MS) return workingWord;
    workingWord = nextWord(workingBag, "think");
    workingWordAt = now();
    return workingWord;
  }

  // Whether the panel should be saying the agent is working, and what it should
  // say. Nothing is added while something else already speaks for the turn: a
  // running tool has its own state, a thought its own shimmering header, a
  // subagent its own working row, and a question is waiting on the user, which
  // is worth saying rather than calling it work.
  function workingLabel() {
    if (!busy || body !== "thread" || !currentTurn) return null;
    const pending = el.permissionTray.children.length + el.elicitationTray.children.length;
    if (pending) return pending === 1 ? "1 confirmation pending" : pending + " confirmations pending";
    if (currentTurn.resp.querySelector(".tool.in_progress, .tool.pending, .subagent-active")) return null;
    if (block && block.kind === "thinking") return null;
    // Mid sentence: the text itself is the sign of life until it stops.
    if (proseSettlesIn() > 0) return null;
    return pickWorkingWord();
  }

  // How long the streamed text still has to be quiet before a pause in it counts
  // as one, or 0 when nothing is streaming.
  function proseSettlesIn() {
    if (!busy || !block || block.kind !== "assistant") return 0;
    return Math.max(0, WORKING_DEBOUNCE_MS - (now() - lastProseAt));
  }

  // Re-decide after anything the host says, and again once a pause in the text
  // has lasted long enough to count as one.
  function refreshWorking() {
    if (workingTimer) { clearTimeout(workingTimer); workingTimer = null; }
    const label = workingLabel();
    if (label) showWorking(label); else hideWorking();
    const wait = label ? 0 : proseSettlesIn();
    if (wait) {
      workingTimer = setTimeout(() => { workingTimer = null; refreshWorking(); }, wait);
    }
  }

  function showWorking(label) {
    // Already saying it, and still at the end of the turn: leave the row alone so
    // its sweep runs on rather than restarting.
    if (workingEl && workingEl.parentElement === respTarget() && workingEl.nextSibling === null) {
      const span = workingEl.firstChild;
      if (label && span.textContent !== label) span.textContent = label;
      return;
    }
    hideWorking();
    ensureTurn();
    const w = document.createElement("div");
    w.className = "working";
    const span = document.createElement("span");
    span.className = "dv-shimmer";
    span.textContent = label || pickWorkingWord();
    w.appendChild(span);
    respTarget().appendChild(w);
    syncShimmer(span);
    workingEl = w;
    scrollToBottom();
  }
  // Every shimmer runs off one clock. The row is recreated as the turn streams,
  // and a fresh element restarts the sweep at 0%, which reads as frozen rather
  // than as moving. A negative delay drops it back into the phase of the rest.
  const SHIMMER_MS = 2000;
  const shimmerEpoch = now();
  function syncShimmer(node) {
    if (node) node.style.animationDelay = "-" + Math.round((now() - shimmerEpoch) % SHIMMER_MS) + "ms";
  }

  function hideWorking() {
    if (workingEl) { workingEl.remove(); workingEl = null; }
    // A retained transcript can bring back a "Working…" line this module no
    // longer tracks, so sweep any stray one: two must never show at once.
    el.thread.querySelectorAll(".working").forEach((n) => n.remove());
  }

  function appendAssistant(text, mid) {
    hideWorking();
    lastProseAt = now();
    let opened = false;
    if (!(block && block.kind === "assistant" && sameMid(block.mid, mid))) {
      finalizeBlock();
      hideWelcome();
      ensureTurn();
      breakToolGroup();
      const bubble = document.createElement("div");
      bubble.className = "resp-text bubble";
      respTarget().appendChild(bubble);
      block = { kind: "assistant", mid, bubble, buffer: "" };
      opened = true;
    }
    block.buffer += text;
    // A block's first chunk renders straight away: on a history replay a whole
    // message arrives as one chunk, and deferring it to the next frame leaves an
    // empty bubble between the tool cards until then. Later chunks keep the
    // throttle, which is what stops a long stream re-parsing itself per token.
    if (opened) renderOpenBlock();
    else scheduleRender();
  }

  // An image produced in an assistant response (e.g. a browser screenshot or a
  // chart). Rendered as its own block between any surrounding text.
  function appendAssistantImage(mime, data) {
    if (!data) return;
    hideWorking();
    finalizeBlock();
    hideWelcome();
    ensureTurn();
    breakToolGroup();
    respTarget().appendChild(imageThumb(mime, data));
    scrollToBottom();
  }

  // A picture in the transcript: a thumbnail, opening to full size on a click. VS
  // Code shows a tool's images at this size, beside the row rather than buried in
  // it, because the picture is the result and not a detail of it.
  function imageThumb(mime, data) {
    const img = document.createElement("img");
    img.className = "dv-thumb";
    img.src = "data:" + (mime || "image/png") + ";base64," + data;
    img.alt = "";
    img.title = "Click to enlarge";
    img.addEventListener("click", (e) => {
      e.stopPropagation();
      img.title = img.classList.toggle("expanded") ? "Click to shrink" : "Click to enlarge";
      scrollToBottom();
    });
    return img;
  }

  // `replayed` marks reasoning that is being re-rendered rather than streamed
  // (a session load, or a background turn catching up). Its real duration is not
  // recorded anywhere, so the block is labelled "Thought" with no time: a "1s"
  // measured from the replay would be made up. `at` is when it originally
  // happened, which the CLI does record, shown on hover.
  function appendThought(text, mid, replayed, at) {
    hideWorking();
    let opened = false;
    if (!(block && block.kind === "thinking" && sameMid(block.mid, mid))) {
      finalizeBlock();
      hideWelcome();
      ensureTurn();
      // fixedScrolling shows a live, fixed-height peek while streaming (VS
      // Code's chat.agent.thinkingStyle); collapsed starts folded.
      const peek = caps.thinkingStyle === "fixedScrolling";
      // Reasoning that lands in a run is one of its steps: it reads as the text
      // it is, on the same chain as the work it led to, with no header and
      // nothing to open, which is what VS Code does with a thinking item. On its
      // own it is a section of its own and keeps its header, so the styling is
      // left to the stylesheet, which can see whether it ended up in a run.
      // "collapsed" is the one style that asks for a folded section either way.
      const plain = caps.thinkingStyle !== "collapsed";
      const c = makeCollapsible(
        "thinking thinking-active" + (peek ? " thinking-peek" : "") + (plain ? " thinking-plain" : ""),
        { startCollapsed: !peek }
      );
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right thinking-chevron";
      const glyph = document.createElement("i");
      glyph.className = "codicon codicon-thinking thinking-glyph";
      const label = document.createElement("span");
      label.className = "thinking-label";
      label.textContent = replayed ? "Thought" : "Thinking\u2026";
      if (replayed && at && caps.verbose) label.title = "Thought at " + fmtTime(at);
      c.header.appendChild(chev);
      c.header.appendChild(glyph);
      c.header.appendChild(label);
      const bodyEl = document.createElement("div");
      bodyEl.className = "thinking-body";
      c.body.appendChild(bodyEl);
      // Reasoning is part of the run it belongs to, not a break in it.
      placeInRun(c.root);
      block = { kind: "thinking", mid, details: c.root, body: bodyEl, label, buffer: "", start: Date.now(), timer: null, peek, collapse: c, scrollEl: c.body, replayed: !!replayed };
      const tb = block;
      if (!replayed) {
        tb.timer = setInterval(() => {
          if (!tb.label) return;
          const secs = Math.max(1, Math.round((Date.now() - tb.start) / 1000));
          tb.label.textContent = `Thinking\u2026 ${secs}s`;
        }, 1000);
      }
      opened = true;
    }
    block.buffer += text;
    if (opened) renderOpenBlock();
    else scheduleRender();
  }

  // A user turn streamed during history replay (user_message_chunk): starts a
  // new turn and streams the request text into it.
  function appendUserChunk(text, mid, attachments) {
    if (!(block && block.kind === "user" && sameMid(block.mid, mid))) {
      finalizeBlock();
      hideWelcome();
      const turn = newTurn(mid, "");
      turn.replayed = true; // from a loaded session; node id unknown
      buildTurnChrome(turn); // hide edit/restore until (if) a head is known
      block = { kind: "user", mid, turn, buffer: "" };
    }
    if (attachments && attachments.length) setTurnAttachments(block.turn, attachments);
    block.buffer += text;
    lastUserText = block.buffer;
    // Render the request text synchronously so a finalize between replayed
    // chunks can never leave the bubble as an empty placeholder.
    if (block.turn) {
      block.turn.text = block.buffer;
      block.turn.reqText.innerHTML = renderMarkdown(block.buffer);
    }
    scrollToBottom();
  }

  // A user turn we already have in full (live echo from the host).
  function addUserMessage(text, attachments) {
    finalizeBlock();
    hideWelcome();
    // A response is imminent, so enter the busy state BEFORE building the turn:
    // otherwise the new turn is briefly treated as "complete" and flashes its
    // Copy/Retry footer until the host's busy=true arrives. The host confirms
    // busy shortly, and clears it on completion/error.
    setBusy(true);
    const turn = newTurn(undefined, text);
    setTurnAttachments(turn, attachments);
    // A send is an explicit action: snap back to the bottom even if the user
    // had scrolled up while reading.
    forceScrollToBottom();
  }
  // The plan/todo list shows live in a docked widget above the composer (VS
  // Code's chat-todo-list-widget), then snapshots into the transcript when the
  // turn completes so history keeps it. `planCollapsePref` remembers a manual
  // expand/collapse (null = no preference, auto) so auto-collapse never fights
  // the user, and the choice is kept for the next plan raised this session (like
  // VS Code's per-widget userManuallyExpanded). Reset only on a session change.
  let planCollapsePref = null;
  // Same idea for the working-set (file changes) widget: remember a manual
  // collapse so it survives the widget hiding and reappearing within a session.
  let wsCollapsePref = null;

  function planRow(entry) {
    const raw = entry.status;
    const st = raw === "completed" ? "done"
      : raw === "in_progress" ? "active"
      : raw === "skipped" || raw === "cancelled" ? "skipped"
      : "pending";
    const row = document.createElement("div");
    row.className = "plan-entry plan-" + st;
    const mark = document.createElement("i");
    mark.className = "codicon plan-mark " + (
      st === "done" ? "codicon-pass-filled"
        : st === "active" ? "codicon-loading codicon-modifier-spin"
        : st === "skipped" ? "codicon-circle-slash"
        : "codicon-circle-large-outline");
    if (st === "skipped") mark.title = "Skipped";
    const txt = document.createElement("span");
    txt.className = "plan-entry-text";
    txt.textContent = entry.content;
    row.appendChild(mark);
    row.appendChild(txt);
    return row;
  }

  // A static plan card, used for the inline history snapshot left in a turn.
  function planCard(entries) {
    const box = document.createElement("div");
    box.className = "plan";
    const title = document.createElement("div");
    title.className = "plan-title";
    title.textContent = "Plan";
    box.appendChild(title);
    (entries || []).forEach((e) => box.appendChild(planRow(e)));
    return box;
  }

  // What the docked plan is currently showing, retained per session so switching
  // away and back keeps it (the host does not re-send a plan on reopen).
  let dockedPlan = [];

  function renderPlan(entries) {
    hideWorking();
    hideWelcome();
    ensureTurn();
    if (currentTurn) currentTurn.planEntries = entries || [];
    renderDockedPlan(entries || []);
    scrollToBottom();
  }

  // Builds/updates the docked todo widget above the composer. Auto-collapses
  // once work is under way (a task is active or done) unless the user toggled.
  function renderDockedPlan(entries) {
    if (!entries.length) { hideDockedPlan(); return; }
    dockedPlan = entries;
    const done = entries.filter((e) => e.status === "completed").length;
    let ctrl = el.todoWidget._ctrl;
    if (!ctrl) {
      el.todoWidget.innerHTML = "";
      ctrl = makeCollapsible("plan plan-docked", {
        startCollapsed: false,
        onUserToggle: () => { planCollapsePref = el.todoWidget._ctrl.isCollapsed(); }
      });
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right plan-chevron";
      const title = document.createElement("span");
      title.className = "plan-title plan-docked-title";
      title.textContent = "Plan";
      const count = document.createElement("span");
      count.className = "plan-count";
      // Folded, the plan is a header and nothing else, so it has to say where the
      // agent is: a bare "2/13" for the rest of a long turn reads as stuck.
      const at = document.createElement("span");
      at.className = "plan-at";
      ctrl.header.appendChild(chev);
      ctrl.header.appendChild(title);
      ctrl.header.appendChild(count);
      ctrl.header.appendChild(at);
      el.todoWidget.appendChild(ctrl.root);
      el.todoWidget._ctrl = ctrl;
      el.todoWidget._count = count;
      el.todoWidget._at = at;
    }
    el.todoWidget._count.textContent = done + "/" + entries.length;
    // The item being worked on, or the next one waiting, so the folded header says
    // where the plan has got to rather than only how far.
    const here = entries.find((e) => e.status === "in_progress")
      || entries.find((e) => !e.status || e.status === "pending");
    el.todoWidget._at.textContent = here ? here.content : "";
    el.todoWidget._at.title = here ? here.content : "";
    el.todoWidget._at.classList.toggle("plan-at-next", !!here && here.status !== "in_progress");
    ctrl.body.innerHTML = "";
    entries.forEach((e) => ctrl.body.appendChild(planRow(e)));
    // Honour a remembered manual choice; otherwise auto-collapse once work is
    // under way (a task is active, done, or skipped).
    if (planCollapsePref !== null) {
      ctrl.setCollapsed(planCollapsePref);
    } else {
      ctrl.setCollapsed(entries.some((e) => e.status === "in_progress" || e.status === "completed" || e.status === "skipped"));
    }
    el.todoWidget.classList.remove("hidden");
    updateComposerDock();
  }

  function hideDockedPlan() {
    dockedPlan = [];
    el.todoWidget.classList.add("hidden");
    el.todoWidget.innerHTML = "";
    el.todoWidget._ctrl = null;
    updateComposerDock();
  }

  // On turn completion, leave the final plan in the transcript as history and
  // clear the docked widget.
  function commitPlanSnapshot() {
    if (currentTurn && currentTurn.planEntries && currentTurn.planEntries.length && !currentTurn.planSnapped) {
      currentTurn.planSnapped = true;
      currentTurn.resp.appendChild(planCard(currentTurn.planEntries));
    }
    // Keep the user's collapse preference for the next plan this session; only a
    // session change clears it.
    hideDockedPlan();
  }

  // Square the input's top corners when a widget (plan / working set) is docked
  // flush on top of it, mirroring VS Code.
  function updateComposerDock() {
    // In the list view the docked widgets are hidden, so never square the input.
    const docked = body !== "list" &&
      (!el.todoWidget.classList.contains("hidden") || !el.workingSet.classList.contains("hidden"));
    el.inputBox.classList.toggle("docked-above", docked);
  }
  // Mirrors VS Code's getToolInvocationIcon() keyword map (chatThinkingContentPart):
  // read->book, edit->pencil, search->search, terminal->terminal, comment->comment,
  // everything else->tools. (VS Code uses `book` for reads, not a file icon.)
  const TOOL_KIND_ICONS = {
    read: "codicon-book",
    edit: "codicon-edit",
    delete: "codicon-trash",
    move: "codicon-arrow-right",
    search: "codicon-search",
    execute: "codicon-terminal",
    think: "codicon-thinking",
    fetch: "codicon-globe",
    other: "codicon-tools"
  };

  // Icons keyed by the resolved tool type (see toolInfo), which is derived from
  // Devin's `_meta` and is more specific than the coarse ACP `kind`. MCP tools use
  // VS Code's MCP glyph (codicon-mcp), the same icon VS Code brands MCP with.
  const TOOL_TYPE_ICONS = {
    // Waiting on a subagent is agent work, not tool work. It becomes a tick once
    // the agent reports back (ACP tells us the call completed).
    subagent_check: "codicon-copilot-in-progress",
    web_search: "codicon-globe",
    webfetch: "codicon-globe",
    mcp: "codicon-mcp",
    mcp_list: "codicon-mcp"
  };

  // Resolve a tool's real identity from the `_meta` Devin attaches. Returns a
  // descriptor { type, server?, tool? } for web search / fetch / MCP tools, or
  // null when the coarse `kind` is all we have.
  function toolInfo(d) {
    const meta = d.meta || {};
    const name = meta.toolName || meta.inferenceToolName || "";
    if (meta.eventType === "mcp_tool_call" || /^mcp__/.test(name)) {
      const parts = name.split("__");
      const server = parts.length > 1 ? parts[1] : "";
      const tool = parts.length > 2 ? parts.slice(2).join("__") : "";
      return { type: "mcp", server, tool };
    }
    if (meta.inferenceToolName === "read_subagent") return { type: "subagent_check" };
    if (meta.inferenceToolName === "mcp_list_tools") return { type: "mcp_list" };
    if (meta.inferenceToolName === "web_search") return { type: "web_search" };
    if (meta.inferenceToolName === "webfetch") return { type: "webfetch" };
    return null;
  }

  // The skill a call invoked, if that is what it was. The CLI's own tool, so it
  // names itself; the argument carries the skill.
  function skillName(d) {
    const meta = d.meta || {};
    const raw = d.rawInput;
    if (meta.inferenceToolName !== "skill" && meta.toolName !== "skill") return null;
    const name = raw && typeof raw === "object" ? raw.skill : null;
    return typeof name === "string" && name ? name : null;
  }

  // A reference to something, inline in a row: VS Code's chat inline anchor, an
  // icon and a name in a bordered pill that opens what it names.
  function anchorPill(icon, text, onClick) {
    const a = document.createElement("a");
    a.className = "chat-inline-anchor";
    const i = document.createElement("i");
    i.className = "codicon " + icon;
    const label = document.createElement("span");
    label.className = "chat-inline-anchor-label";
    label.textContent = text;
    a.append(i, label);
    a.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    return a;
  }

  function statusIcon(status) {
    switch (status) {
      case "in_progress": return "codicon-loading codicon-modifier-spin";
      case "completed": return "codicon-check";
      case "failed": return "codicon-error";
      case "cancelled": return "codicon-circle-slash";
      default: return "codicon-circle-large-outline";
    }
  }

  // VS Code shows a tool line as a normal-weight verb followed by a dimmed
  // detail (e.g. "Read" + " src/auth/token.ts"). Devin gives one title string,
  // so split on the first space.
  function setToolLabel(labelEl, title) {
    const t = String(title || "Tool");
    const sp = t.indexOf(" ");
    const verb = sp === -1 ? t : t.slice(0, sp);
    const rest = sp === -1 ? "" : t.slice(sp);
    labelEl.textContent = "";
    const v = document.createElement("span");
    v.className = "tool-verb";
    v.textContent = verb;
    labelEl.appendChild(v);
    if (rest) {
      const r = document.createElement("span");
      r.className = "tool-detail";
      r.textContent = rest;
      labelEl.appendChild(r);
    }
  }

  // A run of work (tool calls, edits, and the reasoning between them) collapses
  // under one header that says what the run did, mirroring VS Code's chat. Only
  // the reply itself ends a run: an answer, an image, or handing off to a subagent.
  // A run is over: it folds to its summary, the way VS Code's chat finalizes a
  // thinking part (finalizeCurrentThinkingPart -> collapseContent). The work it
  // did is said on the header, and what it did it with is a click away, which is
  // the whole point of grouping it. Unlike VS Code we leave alone a run the user
  // opened by hand: closing something somebody deliberately opened is not tidying.
  function endToolRun() {
    const g = currentTurn && currentTurn.toolRun && currentTurn.toolRun.group;
    if (g && !g.collapse.userToggled() && !holdsFocus(g.root)) g.collapse.setCollapsed(true);
  }


  // Everything a finished turn did, behind one line, leaving the answer itself in
  // the open: VS Code's completed response disclosure. The work is a click away,
  // which is what a transcript you can scan needs.
  function foldCompletedWork(turn) {
    if (!turn || !turn.resp || turn.folded) return;
    // The answer has to exist and has to come last: without one there is nothing
    // left in the open, and folding the whole turn would hide all of it.
    const kids = [...turn.resp.children];
    const lastAnswer = kids.filter((n) => n.classList.contains("resp-text")).pop();
    if (!lastAnswer) return;
    // Everything before it goes in, prose the agent said along the way included,
    // as VS Code folds every node before the final response. Taking only the tool
    // rows would lift them out from around that prose and reorder the turn.
    const work = kids.slice(0, kids.indexOf(lastAnswer)).filter((n) => !n.classList.contains("hidden") && n.offsetParent !== null || n.textContent.trim());
    // One step reads better as itself than behind a line saying there is one step.
    if (work.length < 2) return;
    // Never fold away what somebody is reading or has tabbed into.
    if (work.some((n) => holdsFocus(n) || n === document.activeElement)) return;

    const box = document.createElement("details");
    box.className = "completed-work";
    const head = document.createElement("summary");
    head.className = "completed-work-summary";
    const chev = document.createElement("i");
    chev.className = "codicon codicon-chevron-right completed-work-chevron";
    const label = document.createElement("span");
    label.textContent = completedWorkLabel(work.length, turn.tookMs);
    head.append(label, chev);
    box.appendChild(head);
    turn.resp.insertBefore(box, work[0]);
    work.forEach((n) => box.appendChild(n));
    const sync = () => head.setAttribute("aria-expanded", String(box.open));
    sync();
    box.addEventListener("toggle", sync);
    turn.folded = box;
  }

  // "Completed 6 steps in 1m 23s". VS Code drops anything under a second rather
  // than reporting "in 0s".
  function completedWorkLabel(steps, ms) {
    const n = steps === 1 ? "Completed 1 step" : "Completed " + steps + " steps";
    if (!ms || ms < 1000) return n;
    const secs = Math.floor(ms / 1000);
    return n + " in " + (secs < 60 ? secs + "s" : Math.floor(secs / 60) + "m " + (secs % 60) + "s");
  }

  // A section being read is not tidied away underneath the reader. VS Code checks
  // the same thing before it folds a finished turn (keepOpenForFocus).
  function holdsFocus(node) {
    const active = document.activeElement;
    return !!(active && node && node !== active && node.contains(active));
  }

  // Everything a replay drew is finished work, so it comes back folded rather
  // than as the whole session laid out end to end.
  function foldReplayedSections() {
    el.thread.querySelectorAll(".tool-group, .thinking, .subagent").forEach((node) => {
      // Everything except the reasoning inside a run, which has no header to
      // fold to: folding it puts its text out of reach for good and leaves an
      // empty row on the run's chain, which then draws a line to nothing. The
      // run folding takes it with it anyway.
      if (inRun(node)) return;
      const ctrl = node._dvCollapse;
      if (ctrl && !ctrl.userToggled() && !holdsFocus(node)) ctrl.setCollapsed(true);
    });
  }

  // A reasoning block that is a row of a run rather than a section of its own.
  function inRun(node) {
    return node.classList.contains("thinking-plain") && !!node.closest(".tool-group-body");
  }

  // Joining a run costs a thought its header, which is the only thing that could
  // have unfolded it, so it is opened on the way in. A replayed one arrives
  // folded, and would otherwise be a row of nothing with a line drawn to it.
  function openInRun(node) {
    if (inRun(node) && node._dvCollapse) node._dvCollapse.setCollapsed(false);
  }

  // Take a step back out of the transcript, and out of the run's bookkeeping: a
  // run still waiting to see a second step must forget this one, or it would
  // later try to group around a node that is no longer there.
  function dropStep(node) {
    const run = currentTurn && currentTurn.toolRun;
    if (run && run.first && run.first.node === node) run.first = null;
    node.remove();
    const group = run && run.group;
    if (group) {
      if (group.body.children.length) updateToolGroup(group);
      else group.root.remove();
    }
  }

  function breakToolGroup() {
    endToolRun();
    if (currentTurn) currentTurn.toolRun = null;
  }
  function createToolGroup() {
    const c = makeCollapsible("tool-group", { startCollapsed: false });
    const chev = document.createElement("i");
    chev.className = "codicon codicon-chevron-right tool-group-chevron";
    const label = document.createElement("span");
    label.className = "tool-group-label";
    const statEl = document.createElement("i");
    statEl.className = "codicon tool-group-status";
    c.header.appendChild(chev);
    c.header.appendChild(label);
    c.header.appendChild(statEl);
    const body = document.createElement("div");
    body.className = "tool-group-body";
    c.body.appendChild(body);
    return { root: c.root, body, label, statEl, collapse: c, ids: new Set() };
  }

  // "a, b and c", for a summary made of several clauses or several files.
  function listPhrase(parts) {
    if (parts.length < 2) return parts[0] || "";
    return parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
  }
  // Name them while there are few and every one of them is named, and count them
  // otherwise: "a.ts and b.ts", "3 files", "a file".
  function thingsPhrase(names, count, noun) {
    const uniq = [...new Set(names.filter(Boolean))];
    if (count <= 2 && uniq.length === count) return listPhrase(uniq);
    return count === 1 ? "a " + noun : count + " " + noun + "s";
  }
  function short(s, max) {
    const t = String(s).replace(/\s+/g, " ").trim();
    return t.length > max ? t.slice(0, max - 1) + "\u2026" : t;
  }

  // What a run of tools actually did, the way VS Code's chat says it: a leading
  // verb and then what it was done to ("Read 3 files and ran npm test"), rather
  // than a bare count of tools. Files and commands are named while there are few.
  function groupSummary(g) {
    const of = { read: [], delete: [], move: [], search: [], execute: [], fetch: [], web: [], mcp: [], agent: [], other: [] };
    for (const id of g.ids) {
      const entry = toolEls.get(id);
      if (!entry) continue;
      const d = entry.data;
      const info = toolInfo(d);
      const name = toolTargetName(d);
      if (info && info.type === "subagent_check") of.agent.push("");
      else if (info && (info.type === "mcp" || info.type === "mcp_list")) of.mcp.push(info.tool || info.server || "");
      else if (info && info.type === "web_search") of.web.push("");
      else if (info && info.type === "webfetch") of.fetch.push(toolField(d.rawInput, ["url", "uri", "href"]) || "");
      else if (of[d.kind]) {
        of[d.kind].push(
          d.kind === "search" ? searchLine(d) || "" :
          d.kind === "execute" ? toolCommandStr(d.rawInput) || "" :
          d.kind === "fetch" ? toolField(d.rawInput, ["url", "uri", "href"]) || "" :
          name
        );
      } else of.other.push("");
    }
    const one = (list, single, many) => (list.length === 1 ? single(list[0]) : many(list.length));
    const clauses = [];
    // What the run did to files leads the summary, as VS Code's does: the rows say
    // "Created" and "Edited", the summary says "Created ... and updated ...".
    const editRows = [...g.body.querySelectorAll(".edit-pill")];
    const named = (rows) => rows.map((n) => {
      const name = n.querySelector(".file-pill-name");
      return name ? name.textContent : "";
    });
    const created = named(editRows.filter((n) => n.dataset.created));
    const edited = named(editRows.filter((n) => !n.dataset.created));
    if (created.length) clauses.push(["Created", thingsPhrase(created, created.length, "file")]);
    if (edited.length) clauses.push(["Updated", thingsPhrase(edited, edited.length, "file")]);
    if (of.read.length) clauses.push(["Read", thingsPhrase(of.read, of.read.length, "file")]);
    if (of.search.length) {
      clauses.push(["Searched", one(of.search,
        (s) => (s ? "for " + short(s, 40) : "the workspace"),
        (n) => "the workspace " + n + " times")]);
    }
    if (of.execute.length) {
      // Kept short: a summary carrying every flag of a long command line would
      // crowd out everything else the run did.
      const named = (c) => (c ? short(c, 28) : "a command");
      clauses.push(["Ran", one(of.execute, named, (n) => n + " commands")]);
    }
    if (of.web.length) {
      clauses.push(["Searched", of.web.length === 1 ? "the web" : "the web " + of.web.length + " times"]);
    }
    if (of.fetch.length) {
      clauses.push(["Fetched", one(of.fetch, (u) => (u ? short(u, 40) : "a page"), (n) => n + " pages")]);
    }
    if (of.mcp.length) {
      clauses.push(one(of.mcp, (t) => (t ? ["Called", t] : ["Used", "an MCP tool"]), (n) => ["Used", n + " MCP tools"]));
    }
    if (of.agent.length) {
      clauses.push(["Waited", of.agent.length === 1 ? "on a subagent" : "on " + of.agent.length + " subagents"]);
    }
    if (of.delete.length) clauses.push(["Deleted", thingsPhrase(of.delete, of.delete.length, "file")]);
    if (of.move.length) clauses.push(["Moved", thingsPhrase(of.move, of.move.length, "file")]);
    if (of.other.length) clauses.push(["Used", thingsPhrase([], of.other.length, "tool")]);
    // Two clauses at most, joined with "and", which is what VS Code's chat does:
    // "Updated 2 files and ran commands in terminal". Chaining every one of them
    // made a header that ran past the width of the panel, taking its own chevron
    // with it, and a summary that has to be read word by word is not a summary.
    // The rest of what the run did is a click away, which is what the group is for.
    const parts = clauses.slice(0, 2).map(([verb, rest], i) => (i ? verb.toLowerCase() : verb) + (rest ? " " + rest : ""));
    // A clause that already names two things carries its own "and", so the pair is
    // separated by a comma instead: "Read a.ts and b.ts, ran npm test".
    const phrase = parts.some((p) => / and /.test(p)) ? parts.join(", ") : listPhrase(parts);
    if (phrase) return phrase;
    // A run of nothing but reasoning: say that rather than counting tools.
    return g.ids.size ? "Used " + g.ids.size + (g.ids.size === 1 ? " tool" : " tools") : "Thought it through";
  }

  function updateToolGroup(g) {
    setToolLabel(g.label, groupSummary(g));
    const running = !!g.body.querySelector(".tool.in_progress, .tool.pending");
    g.statEl.className = "codicon tool-group-status " + (running ? "codicon-loading codicon-modifier-spin" : "codicon-check");
    g.root.classList.toggle("running", running);
  }
  // Place a node that belongs to a run of work: a tool call, an edit, or the
  // reasoning in between. The first one mounts inline, the second wraps both into a
  // group, and the rest join it. Only the reply itself ends a run, so a burst of
  // work stays under one summary even when the agent thinks out loud part way
  // through it, which is how VS Code's chat holds a run together.
  function placeInRun(node, id) {
    const turn = currentTurn;
    const run = turn.toolRun || (turn.toolRun = { first: null, group: null });
    if (run.group) {
      run.group.body.appendChild(node);
      openInRun(node);
      if (id) run.group.ids.add(id);
      updateToolGroup(run.group);
      return run.group;
    }
    if (!run.first) {
      respTarget().appendChild(node);
      run.first = { id, node };
      return null;
    }
    const g = createToolGroup();
    respTarget().insertBefore(g.root, run.first.node);
    g.body.appendChild(run.first.node);
    g.body.appendChild(node);
    openInRun(run.first.node);
    openInRun(node);
    for (const each of [run.first.id, id]) {
      if (each) g.ids.add(each);
    }
    const firstEntry = run.first.id && toolEls.get(run.first.id);
    if (firstEntry) firstEntry.group = g;
    run.group = g;
    updateToolGroup(g);
    return g;
  }

  // A row already in the open run changed (an edit reported again with its diff),
  // so the summary above it is rebuilt.
  function refreshRunGroup(node) {
    const run = currentTurn && currentTurn.toolRun;
    if (run && run.group && run.group.body.contains(node)) {
      updateToolGroup(run.group);
    }
  }

  // --- Subagents -----------------------------------------------------------
  // Work the agent delegates renders as one block per subagent, mirroring VS
  // Code's chatSubagentContentPart: a collapsed row whose title shimmers while
  // the subagent runs ("Explore: List the webview files \u2014 Read file", the
  // suffix being the tool it is on), opening onto a timeline of its prompt, tool
  // calls, streamed output and closing report. Parallel subagents are siblings,
  // not a group. Keyed by the agentId the ACP `subagent_started` tag carries.
  const subagentEls = new Map();

  const SUBAGENT_MAX_TITLE = 100;

  // What the panel says an agent is doing when it has nothing else to show yet.
  // VS Code's own pools, one per kind of work (chatThinkingContentPart.ts:
  // defaultThinkingMessages, toolMessages, terminalMessages, and
  // chatSubagentContentPart's subagentWorkingMessages), drawn without
  // replacement so the same word never comes round twice in a row.
  const WORK_WORDS = {
    think: ["Thinking", "Reasoning", "Considering", "Analyzing", "Evaluating", "Working"],
    tool: ["Processing", "Preparing", "Loading", "Analyzing", "Evaluating"],
    terminal: ["Executing", "Running", "Processing"]
  };

  function nextWord(bag, kind) {
    const pool = bag[kind] && bag[kind].length ? bag[kind] : (bag[kind] = WORK_WORDS[kind].slice());
    return pool.splice(Math.floor(Math.random() * pool.length), 1)[0];
  }

  function capitalise(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  // Truncate on a word boundary, like VS Code's rcut for subagent titles.
  function cutWords(text, max) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    if (t.length <= max) return t;
    const cut = t.slice(0, max);
    const sp = cut.lastIndexOf(" ");
    return (sp > max / 2 ? cut.slice(0, sp) : cut) + "\u2026";
  }

  // Add a row to a subagent's timeline. Rows carry the rail and its icon; the
  // prompt pins to the top and new work lands above the spinner and the report.
  function insertSubagentItem(sub, contentNode, iconClass, extraClass, first) {
    const row = document.createElement("div");
    row.className = "subagent-item" + (extraClass ? " " + extraClass : "");
    const icon = document.createElement("i");
    icon.className = "codicon subagent-icon " + (iconClass || "");
    row.appendChild(icon);
    row.appendChild(contentNode);
    const before = first ? sub.bodyEl.firstChild : sub.spinnerRow || sub.resultRow;
    if (before) sub.bodyEl.insertBefore(row, before);
    else sub.bodyEl.appendChild(row);
    return { row, icon };
  }

  // The prompt and the report are collapsibles titled with their first line,
  // opening onto the rest (VS Code's ChatCollapsibleMarkdownContentPart).
  function subagentSection(text, fallbackTitle) {
    const lines = String(text || "").split("\n");
    let i = 0;
    while (i < lines.length && !lines[i].trim()) i++;
    const head = lines[i] ? lines[i].trim() : "";
    const rest = lines.slice(i + 1).join("\n").trim();
    const c = makeCollapsible("subagent-section", { startCollapsed: true });
    const label = document.createElement("span");
    label.className = "subagent-section-label";
    label.textContent = cutWords(head || fallbackTitle, SUBAGENT_MAX_TITLE);
    const chev = document.createElement("i");
    chev.className = "codicon codicon-chevron-right subagent-section-chevron";
    c.header.appendChild(label);
    c.header.appendChild(chev);
    if (rest) {
      const body = document.createElement("div");
      body.className = "subagent-section-body";
      body.innerHTML = renderMarkdown(rest);
      enhanceCodeBlocks(body);
      enhanceAnchors(body);
      c.body.appendChild(body);
    } else {
      c.root.classList.add("dv-nocollapse");
    }
    return c.root;
  }

  function updateSubagentTitle(sub) {
    const d = sub.data;
    const prefix = capitalise(d.profile || "Subagent");
    sub.titleEl.textContent = prefix + ": " + d.title;
    const doing = d.lastTool || d.working;
    sub.detailEl.textContent = d.active && doing ? " \u2014 " + doing : "";
    sub.header.setAttribute("aria-label", sub.titleEl.textContent + sub.detailEl.textContent);
  }

  // Foreground and background are switchable while the subagent runs (the CLI's
  // Ctrl+B). The agent acknowledges nothing, so the button flips optimistically
  // and only a failure is sent back.
  function renderSubagentActions(sub, id) {
    sub.actionsEl.innerHTML = "";
    if (!caps.subagentControl || !sub.data.active) return;
    const toBackground = !sub.data.background;
    const b = document.createElement("button");
    b.className = "subagent-action";
    b.title = toBackground ? "Run in background" : "Bring to foreground";
    b.setAttribute("aria-label", b.title);
    const i = document.createElement("i");
    i.className = "codicon " + (toBackground ? "codicon-clock" : "codicon-device-desktop");
    b.appendChild(i);
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      sub.data.background = toBackground;
      vscode.postMessage({ type: "subagentMode", id, background: toBackground });
      renderSubagentActions(sub, id);
    });
    sub.actionsEl.appendChild(b);
  }

  function ensureSubagentSpinner(sub) {
    if (sub.spinnerRow || !sub.data.active) return;
    sub.spinnerLabel = document.createElement("span");
    sub.spinnerLabel.className = "subagent-spinner-label dv-shimmer";
    sub.spinnerRow = insertSubagentItem(sub, sub.spinnerLabel, "codicon-circle-filled", "subagent-spinner").row;
    subagentWorkingOn(sub, "tool");
  }

  // What the subagent is doing now, in its header and on its spinner. A tool
  // names itself while it runs; between tools the word rotates as each piece of
  // work lands, which is how VS Code keeps a delegated task from looking stuck
  // on the last thing it ran.
  function subagentWorkingOn(sub, kind) {
    if (!sub.data.active) return;
    sub.data.lastTool = "";
    sub.data.working = nextWord(sub.words || (sub.words = {}), kind);
    if (sub.spinnerLabel) sub.spinnerLabel.textContent = sub.data.working;
    updateSubagentTitle(sub);
  }

  function startSubagent(m) {
    let sub = subagentEls.get(m.id);
    if (!sub) {
      finalizeBlock();
      hideWorking();
      hideWelcome();
      ensureTurn();
      // A subagent is its own part, so it ends the surrounding run of tools.
      breakToolGroup();
      const c = makeCollapsible("subagent subagent-active", { startCollapsed: true });
      // VS Code leaves the header glyph out, which reads fine there because the
      // Agents window frames it. Standing alone in a transcript the row needs to
      // say what it is, so it leads with the agent glyph the way a grouped tool
      // run leads with codicon-tools.
      const glyph = document.createElement("i");
      glyph.className = "codicon codicon-hubot subagent-glyph";
      const title = document.createElement("span");
      title.className = "subagent-title dv-shimmer";
      const detail = document.createElement("span");
      detail.className = "subagent-detail";
      const actions = document.createElement("span");
      actions.className = "subagent-actions";
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right subagent-chevron";
      c.header.appendChild(glyph);
      c.header.appendChild(title);
      c.header.appendChild(detail);
      c.header.appendChild(actions);
      c.header.appendChild(chev);
      const bodyEl = document.createElement("div");
      bodyEl.className = "subagent-body";
      c.body.appendChild(bodyEl);
      respTarget().appendChild(c.root);
      sub = {
        node: c.root, header: c.header, titleEl: title, detailEl: detail, actionsEl: actions,
        bodyEl, collapse: c, spinnerRow: null, resultRow: null, prose: null, data: {}
      };
      subagentEls.set(m.id, sub);
    }
    Object.assign(sub.data, {
      title: cutWords(m.title || "Running subagent", SUBAGENT_MAX_TITLE),
      titleFromAgent: !!m.title,
      profile: m.profile || "",
      background: m.background === true,
      active: true,
      lastTool: ""
    });
    if (m.task && !sub.promptRow) {
      sub.promptRow = insertSubagentItem(sub, subagentSection(m.task, "Prompt"), "codicon-comment", "subagent-prompt", true).row;
    }
    ensureSubagentSpinner(sub);
    renderSubagentActions(sub, m.id);
    updateSubagentTitle(sub);
    scrollToBottom();
  }

  // The subagent's own narration and reasoning, streamed into the timeline. Each
  // run of chunks is one row; a tool call closes it, so the order of what it
  // said and what it did is preserved.
  function appendSubagentChunk(m) {
    const sub = subagentEls.get(m.parentId);
    if (!sub || !m.text) return;
    if (!sub.prose || sub.prose.stream !== m.stream) {
      const content = document.createElement("div");
      content.className = "subagent-prose-content";
      const thought = m.stream === "thought";
      insertSubagentItem(sub, content, thought ? "codicon-thinking" : "codicon-comment",
        "subagent-prose" + (thought ? " subagent-thought" : ""));
      sub.prose = { stream: m.stream, content, buffer: "" };
      // It has moved off whatever tool it last ran, so say what it is doing now.
      subagentWorkingOn(sub, thought ? "think" : "tool");
    }
    sub.prose.buffer += m.text;
    sub.prose.content.innerHTML = renderMarkdown(sub.prose.buffer);
    enhanceAnchors(sub.prose.content);
    scrollToBottom();
  }

  // Also accepts a report for an already settled block: a background subagent
  // outlives the turn that spawned it, so its report can arrive after the block
  // has been folded away.
  function endSubagent(m) {
    const sub = subagentEls.get(m.id);
    if (!sub || (!sub.data.active && !m.summary)) return;
    sub.data.active = false;
    sub.data.lastTool = "";
    sub.data.working = "";
    // With nothing of its own to show for it, say so the way the CLI does.
    if (!sub.data.titleFromAgent) sub.data.title = "Ran subagent";
    sub.node.classList.remove("subagent-active");
    sub.node.classList.toggle("subagent-failed", m.success === false);
    sub.prose = null;
    if (sub.spinnerRow) {
      sub.spinnerRow.remove();
      sub.spinnerRow = null;
    }
    if (m.summary && !sub.resultRow) {
      sub.resultRow = insertSubagentItem(
        sub, subagentSection(m.summary, "Result"),
        m.success === false ? "codicon-error" : "codicon-check", "subagent-result"
      ).row;
    }
    renderSubagentActions(sub, m.id);
    updateSubagentTitle(sub);
    // A finished subagent folds away, unless the user opened it to watch.
    if (!sub.collapse.userToggled()) sub.collapse.setCollapsed(true);
  }

  // The host only sends this to put the button back when a mode switch failed.
  function setSubagentBackground(m) {
    const sub = subagentEls.get(m.id);
    if (!sub) return;
    sub.data.background = m.background === true;
    renderSubagentActions(sub, m.id);
  }

  // A turn can end without a report (interrupted, or the parent moved on), so
  // no block is left shimmering. Mirrors VS Code finalising on response end.
  // A background subagent is left alone: it keeps working after the turn that
  // spawned it and reports later. `all` settles those too, for a replayed
  // transcript where no report is ever coming.
  function finalizeSubagents(all) {
    subagentEls.forEach((sub, id) => {
      if (sub.data.active && (all || !sub.data.background)) endSubagent({ id });
    });
  }

  // Tool calls that are really "a thing happened to this file": they are shown as
  // one line naming the file, never as a section with the file buried inside it.
  // An edit becomes the edit row itself, wherever it came from.
  const editTools = new Map(); // tool call id -> what it has told us about the edit
  // Enough for the calls still on screen in a long turn, and bounded so a day in one
  // panel does not retain every edit payload it has ever seen.
  const MAX_EDIT_TOOLS = 200;
  const FILE_LINE_KINDS = ["read", "delete", "move"];

  // What a file tool is acting on: the diff it produced, the location it reported,
  // or the argument it was given, whichever it has.
  function toolFileTarget(d) {
    const diff = (d.content || []).find((c) => c.type === "diff" && c.path);
    if (diff) return { path: diff.path, added: diff.added, removed: diff.removed, created: diff.created, editId: diff.editId };
    const loc = (d.locations || []).find((l) => l && l.path);
    if (loc) return { path: loc.path, line: loc.line };
    const raw = d.rawInput;
    const p = raw && typeof raw === "object" ? toolFilePath(raw) : null;
    return p ? { path: p } : null;
  }

  // The verb Devin already used for this call ("Read src/a.ts" -> "Read"), so the
  // row reads the way the agent described it, with just the file name after it.
  function toolVerb(d, fallback) {
    const first = String(d.title || "").trim().split(/\s+/)[0];
    return first || fallback;
  }

  // The file a row is about, for a summary: from the diff, the location or the
  // argument, and failing all of those from the title the agent gave it, which
  // reads "Read src/a.ts". Only when that tail actually looks like a path.
  function toolTargetName(d) {
    const target = toolFileTarget(d);
    if (target && target.path) return baseName(target.path);
    const rest = String(d.title || "").trim().split(/\s+/).slice(1).join(" ");
    return rest && !/\s/.test(rest) && /[\\/.]/.test(rest) ? baseName(rest) : "";
  }

  // A search says what was looked for and where, on the row itself: the term and
  // the directory are the whole story, so there is nothing to unfold.
  // A command as one line of label, following VS Code's buildCommandDisplayText in
  // runInTerminalHelpers.ts: undo the escaping artefacts, put a multi line command
  // on one line, and stop at 80 characters. A hard limit rather than letting the
  // panel decide, so a run of chained commands cannot take the whole row and the
  // rows stay a scannable column.
  const COMMAND_DISPLAY_MAX = 80;
  function commandDisplayText(cmd) {
    const flat = String(cmd).replace(/\\(["'/])/g, "$1").replace(/\r\n|\r|\n/g, " ");
    // Drop a leading "cd <dir> &&", as VS Code's extractCdPrefix does: the row is
    // for the command, and the path it was run in is the least interesting half of
    // it, while being long enough to push the command itself off the end.
    const cd = /^cd ([^\s]+) &&\s+(.+)$/.exec(flat);
    const shown = cd ? cd[2] : flat;
    return shown.length > COMMAND_DISPLAY_MAX ? shown.slice(0, COMMAND_DISPLAY_MAX - 3) + "..." : shown;
  }

  function searchLine(d) {
    const raw = d.rawInput;
    if (!raw || typeof raw !== "object") return null;
    const term = toolField(raw, ["query", "pattern", "search", "regex", "q", "text"]);
    if (term == null) return null;
    const dir = toolField(raw, ["path", "dir", "directory", "cwd"]);
    return String(term) + (dir ? " in " + shorten(String(dir)) : "");
  }

  // An edit, from a tool call rather than a change event. Claims every update for
  // that call so it is never also drawn as a tool section.
  function editFromTool(m) {
    const known = editTools.get(m.id);
    if (!known && m.kind !== "edit") return false;
    const d = known || {};
    if (m.title) d.title = m.title;
    if (m.rawInput !== undefined) d.rawInput = m.rawInput;
    if (Array.isArray(m.content) && m.content.length) d.content = m.content;
    if (Array.isArray(m.locations) && m.locations.length) d.locations = m.locations;
    editTools.set(m.id, d);
    // `rawInput` carries the file's text for an edit tool, so this map is two copies
    // of a file per call. It is only read to name the file and open the diff of the
    // call being reported, so the oldest are dropped rather than kept for the life
    // of the panel across every chat it shows.
    while (editTools.size > MAX_EDIT_TOOLS) {
      editTools.delete(editTools.keys().next().value);
    }
    const target = toolFileTarget(d);
    // Nothing names the file yet (a pending call): the row appears with the diff.
    if (!target) return true;
    hideWorking();
    renderEdit({ ...target, name: target.path || toolVerb(d, "Edit") });
    return true;
  }

  function upsertTool(m) {
    // A subagent's work stays on its own rail, so only the turn's own edits become
    // edit rows in the transcript.
    if (!m.parentId && editFromTool(m)) return;
    hideWorking();
    let entry = toolEls.get(m.id);
    if (!entry) {
      finalizeBlock();
      hideWelcome();
      ensureTurn();
      const c = makeCollapsible("tool", { startCollapsed: true });
      const node = c.root;
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right tool-chevron";
      // VS Code's third terminal action, which for us is the disclosure itself.
      chev.title = "Show Output";
      const kindIcon = document.createElement("i");
      kindIcon.className = "codicon tool-kind";
      const label = document.createElement("span");
      label.className = "label";
      const statEl = document.createElement("i");
      statEl.className = "codicon tool-status";
      c.header.appendChild(chev);
      c.header.appendChild(kindIcon);
      c.header.appendChild(label);
      c.header.appendChild(statEl);
      // What can be done with the command this row is running, in the row's own
      // title, as VS Code puts it (chatTerminalToolProgressPart's action bar).
      const actions = document.createElement("div");
      actions.className = "tool-actions";
      c.header.appendChild(actions);
      const bodyEl = document.createElement("div");
      bodyEl.className = "tool-body";
      c.body.appendChild(bodyEl);
      // A subagent's tool joins that subagent's timeline instead of the turn's
      // tool run, and shows its icon on the rail rather than in its own header.
      const sub = m.parentId ? subagentEls.get(m.parentId) : null;
      let railIcon = null;
      let group = null;
      if (sub) {
        sub.prose = null;
        railIcon = insertSubagentItem(sub, node, "", "subagent-tool").icon;
      } else {
        group = placeInRun(node, m.id);
      }
      entry = { node, kindIcon, label, statEl, bodyEl, actions, data: {}, collapse: c, group, sub, railIcon };
      toolEls.set(m.id, entry);
    }
    // Merge incrementally: updates may carry only some fields.
    const d = entry.data;
    if (m.title) d.title = m.title;
    if (m.kind) d.kind = m.kind;
    if (m.meta) d.meta = Object.assign(d.meta || {}, m.meta);
    if (m.status) d.status = m.status;
    // An update that carries no arguments sends null rather than leaving them out,
    // so taking it at face value would erase the command the row is named after.
    if (m.rawInput !== undefined && m.rawInput !== null) d.rawInput = m.rawInput;
    if (m.terminalId) d.terminalId = m.terminalId;
    if (Array.isArray(m.content) && m.content.length) d.content = m.content;
    if (Array.isArray(m.locations) && m.locations.length) d.locations = m.locations;

    // Update only the status class (className overwrite would wipe the
    // dv-collapsed / tool-empty state the collapsible controller manages).
    ["pending", "in_progress", "completed", "failed", "cancelled"].forEach((s) => entry.node.classList.remove(s));
    entry.node.classList.add(d.status || "pending");
    if (entry.group) updateToolGroup(entry.group);
    const info = toolInfo(d);
    let typeIcon = info && TOOL_TYPE_ICONS[info.type];
    if (info && info.type === "subagent_check") {
      if (d.status === "completed") typeIcon = "codicon-copilot-success";
      else if (d.status === "failed" || d.status === "cancelled") typeIcon = "codicon-copilot-error";
    }
    const kindIconClass = typeIcon || TOOL_KIND_ICONS[d.kind] || TOOL_KIND_ICONS.other;
    entry.kindIcon.className = "codicon tool-kind " + kindIconClass;
    if (entry.railIcon) entry.railIcon.className = "codicon subagent-icon " + kindIconClass;
    entry.statEl.className = "codicon tool-status " + statusIcon(d.status);
    // Pictures a tool produced stay beside the row whether or not it is expanded,
    // the way VS Code lifts them out of the collapsible: a screenshot is the
    // result, not a detail of it.
    renderToolMedia(entry);
    // A file the agent read, deleted or moved is one line naming that file, with
    // nothing to expand: the file itself is one click away, in a real editor. A
    // search is one line too, saying what it looked for and where. A tool that
    // came back with a picture or a terminal keeps its body: there is more to it.
    const plain = !(d.content || []).some((c) => c.type === "image" || c.type === "terminal");
    // Invoking a skill is a fact, not a step to open: the argument is the skill's
    // own name, which the row already says. VS Code puts the name in an inline
    // anchor that opens what it names, and nothing else, so this does too.
    const skill = plain && skillName(d);
    if (skill) {
      renderSkillRow(entry, d, skill);
      return;
    }
    const fileLine = plain && FILE_LINE_KINDS.includes(d.kind) ? toolFileTarget(d) : null;
    const search = plain && !fileLine && d.kind === "search" ? searchLine(d) : null;
    // A search that found files has something to show, so it keeps its body. Only
    // one that came back with nothing to list stays a single line: emptying the
    // body regardless threw away every result a glob had just gone and found.
    const hits = (d.content || []).filter((c) => c.type === "link" && c.path).length;
    if (fileLine || (search && !hits)) {
      const head = entry.node.querySelector(".dv-collapsible-header");
      setToolLabel(entry.label, toolVerb(d, fileLine ? "Read" : "Search") + " " +
        (fileLine ? baseName(fileLine.path) : search));
      entry.node.classList.add("tool-empty", "dv-nocollapse");
      entry.bodyEl.innerHTML = "";
      head.title = fileLine ? fileLine.path : d.title || "";
      if (fileLine && !entry.openFile) {
        entry.openFile = () => {
          const f = toolFileTarget(entry.data);
          if (f) vscode.postMessage({ type: "openFile", path: f.path, line: f.line });
        };
        head.addEventListener("click", entry.openFile);
      }
    } else {
      // It is a row with a body again (a later update brought an image or a
      // terminal), so its header is a collapse toggle once more. Left attached,
      // the handler from when it was a single line meant expanding it also jumped
      // the editor to the file.
      if (entry.openFile) {
        entry.node.querySelector(".dv-collapsible-header").removeEventListener("click", entry.openFile);
        entry.openFile = null;
      }
      // A command shows the command itself in the row, syntax highlighted, the way
      // VS Code's terminal part titles it: the row is the command, not a sentence
      // about it. The status icon beside it already says how it went.
      const cmd = d.kind === "execute" ? toolCommandStr(d.rawInput) : null;
      if (cmd) {
        entry.label.textContent = "";
        const verb = document.createElement("span");
        verb.className = "tool-verb";
        const running = d.status === "in_progress" || d.status === "pending";
        verb.textContent = running ? "Running" : "Ran";
        const code = document.createElement("code");
        // Plain text, not highlighted: on the row the command is a label, and VS
        // Code only colours it in the block you get by expanding it.
        code.className = "tool-label-code";
        code.textContent = commandDisplayText(cmd);
        entry.label.append(verb, code);
        // Left running while the agent went on: "Running X in background", as
        // VS Code titles it, so the row does not read as still being waited on.
        const term = d.terminalId && terminalCache.get(d.terminalId);
        if (running && term && term.skipped) {
          const suffix = document.createElement("span");
          suffix.className = "tool-detail";
          suffix.textContent = " in background";
          entry.label.append(suffix);
        }
        entry.node.querySelector(".dv-collapsible-header").title = cmd;
      } else if (search) {
        // What it looked for, and how many it found, the way VS Code's chat titles
        // a search. The results themselves are in the body.
        setToolLabel(entry.label, toolVerb(d, "Search") + " " + search +
          ", " + hits + (hits === 1 ? " result" : " results"));
      } else {
        setToolLabel(entry.label, d.title);
      }
      renderToolBody(entry);
    }
    // Track files this turn looked at for a "Used N references" summary.
    if (currentTurn && Array.isArray(d.locations) && ["read", "search", "fetch"].includes(d.kind)) {
      currentTurn.refs = currentTurn.refs || new Map();
      d.locations.forEach((l) => {
        if (l && l.path && !currentTurn.refs.has(l.path)) currentTurn.refs.set(l.path, { path: l.path, line: l.line });
      });
      renderUsedRefs(currentTurn);
    }
    // A subagent's work is reported in its own header, not the window's, so a
    // background subagent cannot talk over the main agent's progress.
    if (entry.sub) {
      if (d.status === "in_progress" && d.title) {
        entry.sub.data.lastTool = String(d.title).replace(/\s+/g, " ").trim();
        updateSubagentTitle(entry.sub);
      } else if (d.status && d.status !== "in_progress" && d.status !== "pending") {
        // The tool it was naming is over, so the header stops naming it and goes
        // back to saying it is working, rather than reading as stuck on it.
        subagentWorkingOn(entry.sub, d.kind === "execute" ? "terminal" : "tool");
      }
      scrollToBottom();
      return;
    }
    scrollToBottom();
  }

  // A collapsed "Used N references" summary at the top of a turn's response,
  // aggregating the files the agent read or searched (VS Code's used-context).
  function renderUsedRefs(turn) {
    if (!turn.refs || !turn.refs.size) return;
    let box = turn.usedRefsEl;
    if (!box) {
      const c = makeCollapsible("used-refs", { startCollapsed: true });
      box = c.root;
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right used-refs-chevron";
      const label = document.createElement("span");
      label.className = "used-refs-label";
      c.header.appendChild(chev);
      c.header.appendChild(label);
      const body = document.createElement("div");
      body.className = "used-refs-body";
      c.body.appendChild(body);
      turn.resp.insertBefore(box, turn.resp.firstChild);
      turn.usedRefsEl = box;
      turn.usedRefsBody = body;
      turn.usedRefsLabel = label;
    }
    const n = turn.refs.size;
    turn.usedRefsLabel.textContent = "Used " + n + " reference" + (n === 1 ? "" : "s");
    turn.usedRefsBody.innerHTML = "";
    turn.refs.forEach((f) => turn.usedRefsBody.appendChild(filePill({ path: f.path, line: f.line, diff: false })));
  }

  // First non-empty value among candidate keys of a rawInput object.
  function toolField(raw, keys) {
    if (!raw || typeof raw !== "object") return null;
    for (const k of keys) { if (raw[k] != null && raw[k] !== "") return raw[k]; }
    return null;
  }
  // Extract a runnable command string from a tool's rawInput (handles the
  // common argument shapes; ACP does not standardise the key).
  function toolCommandStr(raw) {
    if (typeof raw === "string") return raw.trim() || null;
    let c = toolField(raw, ["command", "cmd", "script", "commandLine", "shellCommand"]);
    if (c == null && raw && Array.isArray(raw.args)) c = raw.args.join(" ");
    else if (c == null && raw && typeof raw.args === "string") c = raw.args;
    return c != null ? String(c) : null;
  }
  function toolFilePath(raw) {
    const p = toolField(raw, ["path", "file", "filename", "filePath", "file_path", "target"]);
    return p != null ? String(p) : null;
  }

  // A shell command block (VS Code's terminal command style): a dim $ prompt,
  // the syntax-highlighted command, and a "Run in terminal" affordance, instead
  // of dumping the argument JSON. Captioned "Input", so a command and what it
  // printed read as the Input / Output pair VS Code's chat shows.
  function toolCommandBlock(cmd, captioned = true) {
    const sec = document.createElement("div");
    sec.className = "tool-section";
    if (captioned) {
      const title = document.createElement("div");
      title.className = "tool-section-title";
      title.textContent = "Input";
      sec.appendChild(title);
    }
    const box = document.createElement("div");
    box.className = "tool-command";
    const prompt = document.createElement("span");
    prompt.className = "tool-command-prompt";
    prompt.textContent = "$";
    const code = document.createElement("code");
    code.className = "hljs tool-command-code";
    code.innerHTML = renderShell(cmd);
    const run = codeBtn("codicon-terminal", "Run in terminal", () => vscode.postMessage({ type: "runInTerminal", text: cmd }));
    run.classList.add("tool-command-run");
    box.appendChild(prompt);
    box.appendChild(code);
    box.appendChild(run);
    sec.appendChild(box);
    return sec;
  }
  // A one-line "Label value" summary (e.g. Search / Fetch). When `href` is a URL
  // the value renders as a link that opens in the browser.
  function toolSummaryLine(label, value, href) {
    const sec = document.createElement("div");
    sec.className = "tool-section";
    const row = document.createElement("div");
    row.className = "tool-summary";
    const l = document.createElement("span");
    l.className = "tool-summary-label";
    l.textContent = label;
    let v;
    if (href && /^https?:\/\//i.test(href)) {
      v = document.createElement("a");
      v.href = href;
      // The click is handled by the delegated #thread listener; a second handler
      // here would post openExternal twice and open the link in two tabs.
    } else {
      v = document.createElement("span");
    }
    v.className = "tool-summary-value";
    v.textContent = value;
    row.appendChild(l);
    row.appendChild(v);
    sec.appendChild(row);
    return sec;
  }
  // Pretty-print JSON text (objects/arrays only); returns null when the text is
  // not JSON, so callers can fall back to showing it verbatim.
  function tryPrettyJson(text) {
    const t = String(text || "").trim();
    if (!(t.startsWith("{") || t.startsWith("["))) return null;
    try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return null; }
  }
  // MCP / custom tool arguments, shown as a labelled, highlighted JSON block.
  // Pretty printed JSON for a value that is one: an object, or text that parses
  // as one. Returns null for anything else, which is then shown as it came.
  function jsonText(v) {
    if (v && typeof v === "object") return safeJson(v);
    return typeof v === "string" ? tryPrettyJson(v) : null;
  }

  // A code block for tool input and output. JSON is pretty printed and
  // highlighted, and every block carries the copy action a markdown code block
  // has. The toolbar is a sibling of the <pre> so a live terminal write, which
  // replaces the pre's text, cannot wipe it.
  function toolBlock(value, opts) {
    const o = opts || {};
    const wrap = document.createElement("div");
    wrap.className = "tool-block";
    const pre = document.createElement("pre");
    pre.className = "tool-pre" + (o.cls ? " " + o.cls : "");
    const json = o.json === false ? null : jsonText(value);
    if (json != null) {
      pre.classList.add("hljs");
      pre.innerHTML = renderCode(json, "json");
    } else if (o.lang) {
      pre.classList.add("hljs");
      pre.innerHTML = renderCode(String(value == null ? "" : value), o.lang);
    } else {
      pre.textContent = typeof value === "string" ? value : safeJson(value);
    }
    const bar = document.createElement("div");
    bar.className = "tool-toolbar";
    bar.appendChild(copyButton("Copy", "code-btn", () => pre.textContent || ""));
    wrap.appendChild(pre);
    wrap.appendChild(bar);
    return { wrap, pre };
  }

  function toolSection(title, value, opts) {
    const sec = document.createElement("div");
    sec.className = "tool-section";
    if (title) {
      const h = document.createElement("div");
      h.className = "tool-section-title";
      h.textContent = title;
      sec.appendChild(h);
    }
    const { wrap, pre } = toolBlock(value, opts);
    sec.appendChild(wrap);
    return { sec, pre };
  }

  function toolArgsSection(raw) {
    return toolSection("Arguments", raw).sec;
  }
  // Raw argument JSON, kept only as a last-resort fallback for tools we cannot
  // represent more nicely (mostly MCP / custom tools).
  function toolRawInputSection(raw) {
    return toolSection("Input", raw).sec;
  }

  // Kinds whose input is fully conveyed by the title + file pills / diff, so we
  // never dump their argument JSON.
  const FILE_TOOL_KINDS = ["read", "edit", "delete", "move"];
  const NO_RAW_KINDS = ["read", "edit", "delete", "move", "think"];

  function renderToolBody(entry) {
    const d = entry.data;
    const body = entry.bodyEl;
    body.innerHTML = "";
    let hasContent = false;
    const raw = d.rawInput;
    const isObj = raw && typeof raw === "object" && !Array.isArray(raw);

    // Prefer the resolved tool identity (from _meta) over the coarse ACP kind:
    // web search and web fetch both report kind "fetch", and MCP tools report no
    // kind at all. Fall back to kind-aware rendering for the built-in tools.
    const info = toolInfo(d);
    let inputShown = false;
    if (info && info.type === "web_search") {
      const q = toolField(isObj ? raw : null, ["query", "q", "search", "text"]);
      if (q != null) { body.appendChild(toolSummaryLine("Search", String(q))); inputShown = true; hasContent = true; }
    } else if (info && info.type === "webfetch") {
      const u = toolField(isObj ? raw : null, ["url", "uri", "href"]);
      if (u != null) { body.appendChild(toolSummaryLine("Fetch", String(u), String(u))); inputShown = true; hasContent = true; }
    } else if (info && info.type === "subagent_check") {
      // The raw arguments are plumbing (agent id, block, timeout), so read them
      // out as what the agent is actually doing.
      const agent = toolField(isObj ? raw : null, ["agent_id", "agentId", "id"]);
      if (agent != null) { body.appendChild(toolSummaryLine("Agent", String(agent))); hasContent = true; }
      const blocking = isObj && (raw.block === true || raw.block === "true");
      const secs = isObj ? Number(raw.timeout) : NaN;
      if (blocking) {
        const cap = secs > 0 ? (secs >= 120 ? `${Math.round(secs / 60)} min` : `${secs}s`) : "";
        body.appendChild(toolSummaryLine("Waiting", cap ? `until it responds, up to ${cap}` : "until it responds"));
        hasContent = true;
      }
      inputShown = true;
    } else if (info && (info.type === "mcp" || info.type === "mcp_list")) {
      if (isObj && Object.keys(raw).length) { body.appendChild(toolArgsSection(raw)); inputShown = true; hasContent = true; }
      else if (typeof raw === "string" && raw.trim()) { body.appendChild(toolArgsSection(raw)); inputShown = true; hasContent = true; }
    } else if (d.kind === "execute") {
      const cmd = toolCommandStr(raw);
      // A command and what it printed need no captions: VS Code's chat shows the
      // command, then its output under it, and nothing else. "Input" and "Output"
      // are for a tool whose arguments and result are not self evident.
      if (cmd) { body.appendChild(toolCommandBlock(cmd, false)); inputShown = true; hasContent = true; }
    } else if (d.kind === "search") {
      const q = toolField(isObj ? raw : null, ["query", "pattern", "search", "regex", "q", "text"]);
      // Unless the row already says what was searched for, which it does whenever
      // the search found something to list.
      const onRow = searchLine(d) && (d.content || []).some((c) => c.type === "link" && c.path);
      if (q != null && !onRow) { body.appendChild(toolSummaryLine("Search", String(q))); hasContent = true; }
      // Either way the input has been shown, on the row if not in the body, so the
      // raw argument fallback must not dump the query back out as JSON.
      if (q != null) inputShown = true;
    } else if (d.kind === "fetch") {
      const u = toolField(isObj ? raw : null, ["url", "uri", "href"]);
      if (u != null) { body.appendChild(toolSummaryLine("Fetch", String(u), String(u))); inputShown = true; hasContent = true; }
    }

    // A replayed command hands back the command itself, as the shell script it
    // was sent as, and that is not output: rendering it made every reloaded row
    // show its own input twice, once as the command and once as the result.
    const ran = d.kind === "execute" ? String(toolCommandStr(d.rawInput) || "").trim() : "";
    const textItems = (d.content || []).filter((c) => c.type === "text" && c.text && !(ran && c.text.trim() === ran));
    if (textItems.length) {
      hasContent = true;
      const text = textItems.map((c) => c.text).join("\n");
      // A command whose terminal we hold reports nothing but its exit code, and the
      // output below already ends with it. Two Output blocks saying the same thing
      // is one too many.
      if (d.terminalId && /^Exited with code /.test(text.trim())) {
        // nothing: the terminal section carries it
      } else if (info && (info.type === "web_search" || info.type === "webfetch")) {
        // The result is a short summary ("Found 5 results", "Fetched N chars"),
        // so a dim caption reads better than a heavyweight Result block.
        const note = document.createElement("div");
        note.className = "tool-result-note";
        note.textContent = text;
        body.appendChild(note);
      } else {
        // A tool that returns JSON gets it pretty printed and highlighted,
        // whichever tool it is: MCP servers are the common case, not the only one.
        // "Output" pairs with the "Input" above it, as VS Code's chat labels them,
        // except under a command, where both are self evident.
        body.appendChild(toolSection(d.kind === "execute" ? "" : "Output", text).sec);
      }
    }

    // Devin's tool call never mentions the terminal it asked the client to open,
    // and once the command exits it reports only "Exited with code 0". The host
    // matches the terminal to the call, so the output the command actually
    // produced can be shown here, live, instead of nothing at all.
    const termItems = (d.content || []).filter((c) => c.type === "terminal" && c.terminalId);
    if (!termItems.length && d.terminalId) termItems.push({ terminalId: d.terminalId });
    if (termItems.length) {
      hasContent = true;
      termItems.forEach((c) => {
        const cached = terminalCache.get(c.terminalId);
        // Uncaptioned under a command, for the same reason the command is: the
        // output of a command is obviously the output of a command.
        const title = d.kind === "execute" ? "" : "Output";
        const { sec, pre } = toolSection(title, (cached && cached.output) || "\u2026", { cls: "terminal-pre", json: false });
        pre.setAttribute("data-terminal", c.terminalId);
        body.appendChild(sec);
      });
      // A command that finishes in a moment should not have flashed its output on
      // the way past, and one that is still going is worth watching. So the output
      // opens itself only once the command has been running for a while, and closes
      // again when it succeeds. VS Code's terminal part behaves the same way. A
      // failure stays open (below), and a section opened by hand is left alone.
      watchTerminalOutput(entry);
    }
    renderTerminalActions(entry);

    const diffItems = (d.content || []).filter((c) => c.type === "diff" && c.path);
    const locs = (d.locations || []).slice();
    const fileRows = [
      ...diffItems.map((c) => ({ path: c.path, diff: true, added: c.added, removed: c.removed })),
      ...locs.map((l) => ({ path: l.path, line: l.line, diff: false })),
      // Files the tool pointed at rather than quoted (a search hit, a listing):
      // each one opens.
      ...(d.content || []).filter((c) => c.type === "link" && c.path).map((c) => ({ path: c.path, diff: false }))
    ];
    // For a file tool with no location/diff, surface the path from rawInput as a
    // pill rather than dumping the argument JSON.
    if (!fileRows.length && FILE_TOOL_KINDS.includes(d.kind) && isObj) {
      const p = toolFilePath(raw);
      if (p) fileRows.push({ path: p, diff: d.kind === "edit" });
    }
    if (fileRows.length) {
      hasContent = true;
      // A handful of files reads well as pills. A listing does not: forty of them
      // wrap into a wall, and a pill only carries the file name, so half of them
      // say "index.ts" and none of them say where. Past a handful they become rows
      // grouped under the folder they are in.
      body.appendChild(fileRows.length > 6 ? fileGroups(fileRows) : filePills(fileRows));
    }

    // Raw argument JSON, only as a fallback: we did not show a friendly input
    // view, and it is not a file/think tool (whose input the title + pills
    // already convey). Covers MCP / custom tools where the args are the point.
    if (!inputShown && !NO_RAW_KINDS.includes(d.kind)) {
      if (isObj && Object.keys(raw).length) { body.insertBefore(toolRawInputSection(raw), body.firstChild); hasContent = true; }
      else if (typeof raw === "string" && raw.trim()) { body.insertBefore(toolRawInputSection(raw), body.firstChild); hasContent = true; }
    }

    // An empty tool has nothing to reveal, so it must not collapse/expand.
    entry.node.classList.toggle("tool-empty", !hasContent);
    entry.node.classList.toggle("dv-nocollapse", !hasContent);

    // Auto-expand a failed tool so the error is visible without a click
    // (VS Code's chat.tools.autoExpandFailures).
    if (d.status === "failed" && hasContent && entry.collapse && !entry.node.dataset.autoOpened) {
      entry.collapse.setCollapsed(false);
      entry.node.dataset.autoOpened = "1";
    }
  }

  // Pictures a tool produced, kept between the row and its body so they are there
  // whether or not it is open.
  function renderToolMedia(entry) {
    const shots = (entry.data.content || []).filter((c) => c.type === "image" && c.data);
    if (!shots.length) {
      if (entry.mediaEl) { entry.mediaEl.remove(); entry.mediaEl = null; }
      return;
    }
    if (!entry.mediaEl) {
      entry.mediaEl = document.createElement("div");
      entry.mediaEl.className = "tool-media";
      entry.node.insertBefore(entry.mediaEl, entry.node.children[1] || null);
    }
    entry.mediaEl.innerHTML = "";
    shots.forEach((c) => entry.mediaEl.appendChild(imageThumb(c.mime, c.data)));
  }

  // How long a command has to run before its output is worth opening on its own.
  const TERMINAL_WATCH_MS = 2000;

  function watchTerminalOutput(entry) {
    const running = entry.data.status === "in_progress" || entry.data.status === "pending";
    if (running) {
      if (!entry.watchTimer) {
        entry.watchTimer = setTimeout(() => {
          entry.watchTimer = null;
          const still = entry.data.status === "in_progress" || entry.data.status === "pending";
          if (still && entry.collapse && entry.collapse.isCollapsed() && !entry.collapse.userToggled()) {
            entry.collapse.setCollapsed(false);
            entry.node.dataset.autoOpened = "1";
          }
        }, TERMINAL_WATCH_MS);
      }
      return;
    }
    if (entry.watchTimer) {
      clearTimeout(entry.watchTimer);
      entry.watchTimer = null;
    }
    // Done and well: put back what was only opened to watch it.
    if (entry.data.status === "completed" && entry.node.dataset.autoOpened && !entry.collapse.userToggled()) {
      entry.collapse.setCollapsed(true);
      delete entry.node.dataset.autoOpened;
    }
  }

  // A file reference rendered as a VS Code style pill: a file-type icon, the
  // name, and (for edits) +added / -removed line counts. Clicking opens a diff
  // for edited files or the file at a line otherwise.
  // One line: what the agent did, then the skill it did it with, in a pill that
  // opens the skill's own SKILL.md. Nothing to expand, since the only thing the
  // body ever held was the name repeated back as JSON.
  function renderSkillRow(entry, d, skill) {
    // The agent's own verb, minus the name it ends with, which the pill carries.
    const title = String(d.title || "Invoked skill").trim();
    const verb = title.endsWith(skill) ? title.slice(0, -skill.length).trim() : title;
    setToolLabel(entry.label, verb || "Invoked skill");
    entry.label.append(" ");
    // VS Code's own icon for a skill (aiCustomizationIcons registers the skill
    // icon as the lightbulb). The pill in its chat carries the icon theme's file
    // glyph, which a webview cannot ask for.
    entry.label.appendChild(anchorPill("codicon-lightbulb", skill, () =>
      vscode.postMessage({ type: "openSkill", name: skill })
    ));
    entry.node.classList.add("tool-empty", "dv-nocollapse");
    entry.bodyEl.innerHTML = "";
    entry.node.querySelector(".dv-collapsible-header").title = title;
  }

  // What a command's row offers, in the order and the wording VS Code uses
  // (chatTerminalToolProgressPart._updateToolbarActions): let it carry on in the
  // background, and open the terminal it is really running in. The third of its
  // actions, showing the output, is this row's own chevron.
  function renderTerminalActions(entry) {
    const bar = entry.actions;
    const id = entry.data.terminalId;
    if (!bar) return;
    bar.innerHTML = "";
    if (!id) return;
    const state = terminalCache.get(id);
    // Nothing said about it yet means it has only just started.
    const running = !state || (!state.exitStatus && !state.skipped);
    if (running && state && state.integrated) {
      bar.appendChild(iconBtn("codicon-debug-continue-small", "Continue in Background", (e) => {
        e.stopPropagation();
        vscode.postMessage({ type: "continueInBackground", terminalId: id });
      }));
    }
    if (state && state.integrated) {
      // Hidden until it is shown once, which is the difference between the two
      // labels VS Code uses for the same action.
      const label = state.revealed ? "Focus Terminal" : "Show and Focus Terminal";
      bar.appendChild(iconBtn("codicon-open-in-product", label, (e) => {
        e.stopPropagation();
        vscode.postMessage({ type: "showTerminal", terminalId: id });
      }));
    }
  }

  function filePills(rows) {
    const sec = document.createElement("div");
    sec.className = "tool-section tool-files";
    rows.forEach((f) => sec.appendChild(filePill(f)));
    return sec;
  }

  // The folder a result is in, relative to the workspace, which is what makes one
  // "index.ts" among forty of them tell you anything.
  function folderOf(p) {
    const parts = String(p).split(/[\\/]/);
    parts.pop();
    const dir = parts.join("/");
    // The workspace root comes from the host with its native separators, and on
    // Windows its drive letter may be cased differently from the agent's path,
    // so both sides are levelled before one is cut off the other.
    const root = caps.root ? String(caps.root).replace(/\\/g, "/").replace(/\/+$/, "") : "";
    const under = root && dir.slice(0, root.length).toLowerCase() === root.toLowerCase();
    return (under ? dir.slice(root.length).replace(/^\/+/, "") : dir) || ".";
  }

  // A listing, as rows grouped by folder. The folder is named once, each file is
  // one row under it, and the whole thing scrolls rather than pushing the rest of
  // the turn off the screen.
  function fileGroups(rows) {
    const sec = document.createElement("div");
    sec.className = "tool-section file-groups";
    const byDir = new Map();
    rows.forEach((f) => {
      const dir = folderOf(f.path);
      if (!byDir.has(dir)) byDir.set(dir, []);
      byDir.get(dir).push(f);
    });
    [...byDir].forEach(([dir, files]) => {
      const head = document.createElement("div");
      head.className = "file-group-head";
      const icon = document.createElement("i");
      icon.className = "codicon codicon-folder file-group-icon";
      const name = document.createElement("span");
      name.className = "file-group-name";
      name.textContent = dir;
      name.title = dir;
      const count = document.createElement("span");
      count.className = "file-group-count";
      count.textContent = files.length;
      head.append(icon, name, count);
      sec.appendChild(head);
      files.forEach((f) => {
        const row = filePill(f);
        row.classList.add("file-group-row");
        sec.appendChild(row);
      });
    });
    return sec;
  }

  function filePill(f) {
    const link = document.createElement("a");
    link.className = "file-change file-pill";
    link.tabIndex = 0;
    link.setAttribute("role", "button");
    link.title = f.path;
    const icon = document.createElement("i");
    icon.className = "codicon " + fileIconFor(f.path) + " file-pill-icon";
    icon.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "file-pill-name";
    name.textContent = baseName(f.path) + (f.line ? ":" + f.line : "");
    link.appendChild(icon);
    link.appendChild(name);
    if (f.added) {
      const a = document.createElement("span");
      a.className = "label-added";
      a.textContent = "+" + f.added;
      link.appendChild(a);
    }
    if (f.removed) {
      const r = document.createElement("span");
      r.className = "label-removed";
      r.textContent = "-" + f.removed;
      link.appendChild(r);
    }
    const open = () => {
      if (f.diff) vscode.postMessage({ type: "openDiff", path: f.path, editId: f.editId });
      else vscode.postMessage({ type: "openFile", path: f.path, line: f.line });
    };
    link.addEventListener("click", open);
    link.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
    return link;
  }

  function safeJson(v) {
    try {
      const s = JSON.stringify(v, null, 2);
      return s && s.length > 4000 ? s.slice(0, 4000) + "\n…" : s;
    } catch {
      return String(v);
    }
  }
  function addFileChange(m) {
    const path = typeof m === "string" ? m : m.path;
    const added = typeof m === "object" ? m.added : undefined;
    const removed = typeof m === "object" ? m.removed : undefined;
    const created = typeof m === "object" && m.created;
    if (path) wsCounts.set(path, { added: added || 0, removed: removed || 0 });
    // A live edit is part of the working set, so it can be kept or undone here.
    renderEdit({ path, added, removed, created, editId: typeof m === "object" ? m.editId : undefined, actionable: true });
  }

  // The one way an edit is ever shown: the pencil, what happened to the file, and
  // the file itself with its line counts. A file can report changes several times
  // in a turn (a tool call, its updates, and the change event all carry the same
  // diff), so there is one row per file per turn, rewritten in place.
  function renderEdit(e) {
    const path = e.path;
    finalizeBlock();
    hideWelcome();
    ensureTurn();
    const turn = currentTurn;
    turn.editPills = turn.editPills || new Map();
    const key = path || e.name;
    let node = key ? turn.editPills.get(key) : null;
    const isNew = !node;
    if (isNew) {
      node = document.createElement("div");
      node.className = "edit-pill";
      if (key) turn.editPills.set(key, node);
    } else if (node.classList.contains("resolved")) {
      // Kept or undone by hand: leave that alone, a later update must not undo it.
      return;
    }
    // An edit reported by the agent alone (a reloaded transcript) has no working
    // set behind it, so it cannot offer Keep and Undo. Once a row has them it
    // keeps them: the same edit arrives again as the tool call completes.
    const actionable = e.actionable || node.dataset.actionable === "1";
    if (actionable && path) node.dataset.actionable = "1";
    if (e.created) node.dataset.created = "1";
    // The row stands for this turn's work on the file, and opens exactly that.
    if (e.editId) node.dataset.editId = e.editId;
    node.innerHTML = "";
    const icon = document.createElement("i");
    // The same pencil whether the file was created or changed: it is the same
    // action to review, and a tick here would read as "already dealt with".
    icon.className = "codicon codicon-edit edit-pill-status";
    const label = document.createElement("span");
    label.className = "edit-pill-label";
    label.textContent = node.dataset.created ? "Created" : "Edited";
    node.appendChild(icon);
    node.appendChild(label);
    node.appendChild(filePill({ path: path || e.name, diff: !!path, added: e.added, removed: e.removed, editId: node.dataset.editId }));
    // Inline Keep / Undo for this edit (VS Code shows accept/reject per edit),
    // in addition to the Keep all / Undo all in the docked working set.
    if (actionable && path) {
      const actions = document.createElement("div");
      actions.className = "edit-pill-actions";
      // Keeping and undoing are per file, not per row: the working set holds one
      // original per file, from before this chat first touched it. So these say the
      // file, rather than "this change", which read as though the row's own edit
      // could be undone on its own.
      // Neither says so itself: an undo can fail (a read only file, a lock, a
      // directory that has gone), and the host then keeps the file in the working
      // set so it can be undone again. A row that had already written "Undone" over
      // itself would be the last thing still claiming the file had been put back.
      // The host answers with what it really resolved, the way the tray already
      // waits for it.
      actions.appendChild(iconBtn("codicon-check", "Keep this file's changes", (ev) => {
        ev.stopPropagation();
        vscode.postMessage({ type: "acceptFile", path });
      }));
      actions.appendChild(iconBtn("codicon-discard", "Undo this file's changes", (ev) => {
        ev.stopPropagation();
        vscode.postMessage({ type: "rejectFile", path });
      }));
      node.appendChild(actions);
      // And only the newest row for a file carries them. An older row's Undo wound
      // the file back past every edit that came after it, from a button sitting
      // beside a diff showing only its own, so the later work went without a word.
      turns.forEach((t) => {
        if (!t.editPills) return;
        t.editPills.forEach((other, key) => {
          if (key !== path || other === node) return;
          const stale = other.querySelector(".edit-pill-actions");
          if (stale) stale.remove();
          other.title = "This file was edited again later. Keep or undo it from the newest change, or the changed files list.";
        });
      });
    }
    // An edit is part of the run that made it, so it joins the group rather than
    // splitting it: that is how a run comes to read "Created a.ts, updated b.ts".
    if (isNew) placeInRun(node);
    else refreshRunGroup(node);
    scrollToBottom();
  }

  // The host answers with what was actually resolved, so an edit row says so
  // however it happened: "Keep all" in the tray, a single Keep, the Source
  // Control view, or a command. Only the row clicked used to know.
  function applyResolved(paths, action) {
    const want = new Set(paths || []);
    if (!want.size) return;
    const label = action === "reject" ? "Undone" : "Kept";
    turns.forEach((t) => {
      if (!t.editPills) return;
      t.editPills.forEach((node, key) => {
        if (want.has(key)) markEditResolved(node, label);
      });
    });
  }

  // After keeping/undoing a single edit, reflect the resolved state on its pill.
  function markEditResolved(node, label) {
    node.classList.add("resolved");
    const actions = node.querySelector(".edit-pill-actions");
    if (actions) actions.remove();
    const l = node.querySelector(".edit-pill-label");
    if (l) l.textContent = label;
    const st = node.querySelector(".edit-pill-status");
    if (st) st.className = "codicon " + (label === "Undone" ? "codicon-discard" : "codicon-check") + " edit-pill-status";
  }

  // --- Permissions & elicitation -------------------------------------------

  // VS Code chat-confirmation-widget2 shell: a bordered card with an optional
  // bold title row (border-bottom), a message/body section, and a right-aligned
  // primary/secondary button row. Used by the permission, elicitation, and
  // edit-discard confirmations so they all read as one widget.
  function cwShell() {
    const box = document.createElement("div");
    box.className = "cw";
    return box;
  }
  function cwTitle(box, text) {
    const t = document.createElement("div");
    t.className = "cw-title";
    t.textContent = text;
    box.appendChild(t);
    return t;
  }
  function cwBody(box) {
    const b = document.createElement("div");
    b.className = "cw-body";
    box.appendChild(b);
    return b;
  }
  function cwButtons(box) {
    const r = document.createElement("div");
    r.className = "cw-buttons";
    box.appendChild(r);
    return r;
  }

  // The host re-posts every still-outstanding request when a session is
  // reopened, so a widget is keyed by its request id and replaces the one
  // already on screen. Without this, switching away and back stacks a fresh
  // copy of the same question each time until they cover the transcript.
  function trayMount(tray, widget, requestId) {
    if (requestId) {
      widget.dataset.requestId = requestId;
      const open = tray.querySelector('[data-request-id="' + cssEscape(requestId) + '"]');
      if (open) open.remove();
    }
    // A tray widget is a request for a decision, so it is a dialog. Focus is left
    // where it is on purpose (a question arriving must never steal the composer
    // mid sentence), so the role and the announcement are what make it findable.
    widget.setAttribute("role", "dialog");
    widget.setAttribute("aria-live", "off");
    // A dialog with no name is announced as just "dialog", so it takes the name of
    // whatever it is asking about, and says what it is when it has no title.
    const title = widget.querySelector(".cw-title, .qc-title");
    widget.setAttribute("aria-label", (title && title.textContent.trim()) || "Devin needs an answer");
    tray.appendChild(widget);
  }
  function cssEscape(v) {
    return window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/["\\\]]/g, "\\$&");
  }

  function showPermission(data) {
    const box = cwShell();
    cwTitle(box, data.title || "Devin wants to run a tool");
    // Say what is being asked about. Devin sends the command in `command` and no
    // title, so without this the prompt is unanswerable.
    const detail = permissionDetail(data);
    if (detail) cwBody(box).appendChild(detail);
    const row = cwButtons(box);
    const options = data.options || [];
    const answer = (opt) => {
      vscode.postMessage({ type: "permission", requestId: data.requestId, optionId: opt.optionId });
      box.remove();
      renderPermissionRecap(data, opt);
    };
    // Devin offers up to six options for one command: allow once, allow for the
    // session, always in this project, always everywhere, switch to bypass, and
    // reject. As a flat row of identical buttons, "always allow in all projects"
    // and "switch to bypass mode" look exactly as ordinary as "Allow", which is
    // how a permission prompt teaches people to stop reading it. So: the narrow
    // yes and the no are the buttons, and every broader grant is one level in.
    const rejects = options.filter((o) => /reject/.test(o.kind || ""));
    const allows = options.filter((o) => !/reject/.test(o.kind || ""));
    const primary = allows.find((o) => o.kind === "allow_once") || allows[0];
    const broader = allows.filter((o) => o !== primary);

    if (primary) {
      if (broader.length) {
        // A split button, like the composer's Send: the safe answer, and a chevron
        // holding the ones that outlive this request.
        const group = document.createElement("span");
        group.className = "cw-split";
        group.appendChild(btn(primary.name || primary.optionId, "primary", () => answer(primary)));
        const more = btn("", "primary cw-more", () => openPermissionScopeMenu(more, broader, answer));
        more.innerHTML = '<i class="codicon codicon-chevron-down"></i>';
        more.title = "Allow more broadly";
        more.setAttribute("aria-label", "More ways to allow this");
        more.setAttribute("aria-haspopup", "true");
        group.appendChild(more);
        row.appendChild(group);
      } else {
        row.appendChild(btn(primary.name || primary.optionId, "primary", () => answer(primary)));
      }
    }
    rejects.forEach((opt) => row.appendChild(btn(opt.name || opt.optionId, "secondary", () => answer(opt))));
    trayMount(el.permissionTray, box, data.requestId);
    // Devin is blocked until this is answered, so it must not be a silent change.
    announce(data.command ? `Devin needs permission to run: ${data.command}` : "Devin needs permission to use a tool.");
  }

  // The grants that outlive this one request (this session, this project, every
  // project, or switching mode outright), kept behind the chevron so choosing one
  // is deliberate.
  function openPermissionScopeMenu(anchor, options, answer) {
    if (permissionFloater) { permissionFloater.close(); return; }
    const menu = document.createElement("div");
    menu.className = "dv-menu";
    options.forEach((opt) => {
      const row = document.createElement("button");
      row.className = "dv-menu-item";
      // Switching mode is not a scope for this command, it turns approvals off
      // wholesale, so it is marked as the outlier it is.
      const bypass = /bypass/.test(opt.optionId || "");
      row.appendChild(mkIcon(bypass ? "unlock" : "check"));
      const text = document.createElement("span");
      text.className = "dv-menu-text";
      text.textContent = opt.name || opt.optionId;
      row.appendChild(text);
      row.addEventListener("click", () => {
        if (permissionFloater) permissionFloater.close();
        answer(opt);
      });
      menu.appendChild(row);
    });
    permissionFloater = makeFloater(anchor, menu, "right", () => { permissionFloater = null; });
  }

  // What was decided, left in the transcript. Without this a prompt answered and
  // gone leaves no trace, so there is no way to see afterwards what was allowed,
  // which matters most for the grants that keep applying.
  function renderPermissionRecap(data, opt) {
    ensureTurn();
    const box = document.createElement("div");
    const rejected = /reject/.test(opt.kind || "");
    box.className = "perm-recap" + (rejected ? " rejected" : "");
    box.appendChild(mkIcon(rejected ? "circle-slash" : "check"));
    const text = document.createElement("span");
    // The option names are already written from the user's side ("Yes, always allow
    // `exit` commands in `devin-vscode`"), so they are quoted rather than reworded.
    const scope = opt.kind === "allow_once" ? "Allowed" : rejected ? "Rejected" : opt.name || "Allowed";
    text.textContent = data.command ? `${scope}: ${data.command}` : scope;
    box.appendChild(text);
    respTarget().appendChild(box);
    scrollToBottom();
  }

  // What the agent is asking permission for: the command it wants to run, the
  // files it names, or failing both the tool line already in the transcript.
  function permissionDetail(data) {
    const wrap = document.createElement("div");
    wrap.className = "cw-detail";
    if (data.command) {
      // The question already says what this is, so the command needs no caption.
      wrap.appendChild(toolCommandBlock(data.command, false));
    }
    const paths = [
      ...(data.content || []).filter((c) => c && c.type === "diff" && c.path).map((c) => c.path),
      ...(data.locations || []).filter((l) => l && l.path).map((l) => l.path)
    ];
    [...new Set(paths)].slice(0, 6).forEach((path) => wrap.appendChild(filePill({ path })));
    const texts = (data.content || []).filter((c) => c && c.type === "text" && c.text).map((c) => c.text);
    if (!wrap.childElementCount && texts.length) {
      wrap.appendChild(toolBlock(texts.join("\n")).wrap);
    }
    // Nothing came with the request: name the tool call it belongs to, which is
    // already rendered in the transcript just above.
    if (!wrap.childElementCount && data.toolCallId) {
      const entry = toolEls.get(data.toolCallId);
      const edit = editTools.get(data.toolCallId);
      const label = (entry && entry.label && entry.label.textContent) || (edit && edit.title);
      if (label) {
        const line = document.createElement("div");
        line.className = "cw-message muted";
        line.textContent = label;
        wrap.appendChild(line);
      }
    }
    return wrap.childElementCount ? wrap : null;
  }

  function showElicitation(data) {
    let widget;
    // Post the response, drop the widget, and leave a Q/A recap in the
    // transcript (like VS Code), so the exchange stays visible.
    const finish = (action, content, recap) => {
      vscode.postMessage({ type: "elicitationResponse", requestId: data.requestId, action, content });
      if (widget) widget.remove();
      if (recap && recap.length) renderQaRecap(recap);
    };

    // URL prompts are a simple confirmation, not a question carousel.
    if (data.mode === "url" && data.url) {
      const box = cwShell();
      widget = box;
      cwTitle(box, data.message || "Devin has a question");
      const body = cwBody(box);
      // A real link, so it can be read, hovered and opened before agreeing to it:
      // being asked to approve a URL that is only inert text is not much of a
      // question. The webview's own link handling opens it.
      const url = document.createElement("a");
      url.className = "cw-message";
      url.href = data.url;
      url.textContent = data.url;
      body.appendChild(url);
      const row = cwButtons(box);
      row.appendChild(btn("Open", "primary", () => {
        // Answer the agent AND actually open it: the extension asked to handle
        // this, so leaving the opening to whoever asked would open nothing.
        vscode.postMessage({ type: "openExternal", url: data.url });
        finish("accept");
      }));
      row.appendChild(btn("Decline", "secondary", () => finish("decline")));
      trayMount(el.elicitationTray, box, data.requestId);
      announce("Devin is asking to open a link: " + data.url);
      return;
    }

    const props = (data.schema && data.schema.properties) || {};
    const names = Object.keys(props);
    const required = (data.schema && data.schema.required) || [];
    const controls = names.map((key) => buildElicitQuestion(key, props[key], {
      allowOther: data.allowOther,
      required: required.includes(key),
      // The carousel header shows the current question's text (and updates as you
      // navigate), so the per-card label would just duplicate it.
      hideTitle: true
    }));

    // A one-card-at-a-time carousel (VS Code's chat-question-carousel): the
    // header keeps the prompt + step, the body shows the current question, and
    // the footer navigates and submits all answers at once.
    const qc = document.createElement("div");
    qc.className = "qc";
    if (controls.length <= 1) qc.classList.add("qc-single");
    widget = qc;

    const header = document.createElement("div");
    header.className = "qc-header";
    const title = document.createElement("div");
    title.className = "qc-title";
    title.textContent = data.message || "Devin has a question";
    const close = actionBtn("codicon-close", "Cancel", () =>
      finish("cancel", undefined, controls.map((c) => ({ title: c.prompt || c.title, answer: "" })))
    );
    header.appendChild(title);
    header.appendChild(close);
    qc.appendChild(header);

    const body = document.createElement("div");
    body.className = "qc-body";
    controls.forEach((c) => body.appendChild(c.el));
    qc.appendChild(body);

    const validation = document.createElement("div");
    validation.className = "qc-validation hidden";
    validation.textContent = "Please answer this question.";
    qc.appendChild(validation);

    const footer = document.createElement("div");
    footer.className = "qc-footer";
    const prev = actionBtn("codicon-chevron-left", "Previous", () => show(idx - 1));
    const next = actionBtn("codicon-chevron-right", "Next", () => show(idx + 1));
    const nav = document.createElement("div");
    nav.className = "qc-nav";
    nav.appendChild(prev);
    nav.appendChild(next);
    const step = document.createElement("span");
    step.className = "qc-step";
    const spacer = document.createElement("span");
    spacer.className = "qc-spacer";
    function submitAll() {
      controls.forEach((c) => c.el.classList.remove("elicit-invalid"));
      const bad = controls.findIndex((c) => !c.valid());
      if (bad >= 0) {
        show(bad);
        controls[bad].el.classList.add("elicit-invalid");
        validation.classList.remove("hidden");
        return;
      }
      const content = {};
      const recap = controls.map((c) => {
        content[c.key] = c.value();
        return { title: c.prompt || c.title, answer: c.answerText() };
      });
      finish("accept", content, recap);
    }
    const submit = btn("Submit", "primary", submitAll);
    footer.appendChild(nav);
    footer.appendChild(step);
    footer.appendChild(spacer);
    footer.appendChild(submit);
    qc.appendChild(footer);

    // Submit stays disabled until EVERY question has an answer (no option is
    // selected by default, so the user must visit each question and pick one).
    // A tooltip explains why, so the disabled state does not feel broken.
    function updateSubmitState() {
      const unanswered = controls.filter((c) => !c.answered()).length;
      submit.disabled = unanswered > 0;
      submit.title = unanswered > 0
        ? (controls.length > 1 ? "Answer all questions to submit" : "Answer the question to submit")
        : "";
    }
    qc.addEventListener("change", updateSubmitState);
    qc.addEventListener("input", updateSubmitState);

    // Answers in progress are remembered against the request, so leaving the
    // session and coming back does not lose the ones already given (or the free
    // text half typed into "Other"). The host holds them on the pending request
    // and hands them back when it re-shows the question.
    let answerTimer = null;
    function saveAnswers() {
      if (answerTimer) { clearTimeout(answerTimer); answerTimer = null; }
      const state = { at: idx, by: {} };
      controls.forEach((c) => { state.by[c.key] = c.exportState(); });
      vscode.postMessage({ type: "answerDraft", requestId: data.requestId, state });
    }
    function scheduleSaveAnswers() {
      if (answerTimer) clearTimeout(answerTimer);
      answerTimer = setTimeout(saveAnswers, 300);
    }
    qc.addEventListener("change", scheduleSaveAnswers);
    qc.addEventListener("input", scheduleSaveAnswers);
    // Leaving the session tears the widget down, so flush what is in it now.
    qc.addEventListener("dv-teardown", saveAnswers);

    // Keyboard: Left/Right step between questions (captured so a radio group does
    // not consume them), except while typing in a text field. Enter answers and
    // moves on, so a carousel can be filled in without reaching for the mouse:
    // it goes to the next question, and submits on the last one. A newline in a
    // free text answer is Shift+Enter, or Ctrl/Cmd+Enter. A focused button keeps
    // its own Enter, so Cancel still cancels.
    qc.addEventListener("keydown", (e) => {
      // Committing a candidate in a free text answer is not answering the question.
      if (composing(e)) return;
      const t = e.target;
      const inButton = t && t.tagName === "BUTTON";
      const inTextarea = t && t.tagName === "TEXTAREA";
      const typing = inTextarea || (t && t.tagName === "INPUT" && (t.type === "text" || t.type === "number"));
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        if (typing) return;
        e.preventDefault();
        e.stopPropagation();
        show(e.key === "ArrowLeft" ? idx - 1 : idx + 1);
        return;
      }
      if (e.key !== "Enter" || inButton) return;
      if (e.shiftKey || e.altKey) return; // Shift+Enter is the newline
      e.preventDefault();
      e.stopPropagation();
      if (inTextarea && (e.ctrlKey || e.metaKey)) {
        const at = t.selectionStart;
        t.value = t.value.slice(0, at) + "\n" + t.value.slice(t.selectionEnd);
        t.selectionStart = t.selectionEnd = at + 1;
        // Lets the answer's own input handler grow the box and re-check Submit.
        t.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
      if (e.ctrlKey || e.metaKey) return;
      // Enter answers the option it is sitting on before it moves, otherwise a
      // keyboard user steps past a question and only finds out at Submit.
      if (t && t.tagName === "INPUT" && (t.type === "radio" || t.type === "checkbox") && !t.checked) {
        t.checked = true;
        t.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (idx < controls.length - 1) show(idx + 1);
      else submitAll();
    }, true);

    let idx = 0;
    function show(i) {
      idx = Math.max(0, Math.min(controls.length - 1, i));
      controls.forEach((c, j) => c.el.classList.toggle("hidden", j !== idx));
      // Track the current question's text in the header so it changes with the
      // options, not just the choices below it.
      const cur = controls[idx];
      title.textContent = (cur && cur.prompt) || data.message || "Devin has a question";
      const label = controls.length + " question" + (controls.length === 1 ? "" : "s");
      step.textContent = controls.length > 1 ? (idx + 1) + " / " + controls.length : label;
      prev.disabled = idx === 0;
      next.disabled = idx === controls.length - 1;
      validation.classList.add("hidden");
      updateSubmitState();
      // Hiding the previous question takes the focused control out of the page, so
      // move focus into this one: otherwise Enter and the arrows stop reaching the
      // widget after the first step. Only when the user is already inside the
      // widget, so a question arriving never steals the composer's focus.
      const focusable = cur && cur.el.querySelector("input, textarea");
      if (focusable && qc.contains(document.activeElement)) focusable.focus({ preventScroll: true });
    }
    // Put back any answers given before the session was left, and reopen on the
    // question that was on screen.
    const saved = data.answers;
    if (saved && saved.by) {
      controls.forEach((c) => c.importState(saved.by[c.key]));
    }
    show(saved && typeof saved.at === "number" ? saved.at : 0);
    updateSubmitState();
    trayMount(el.elicitationTray, qc, data.requestId);
    // Focus stays in the composer by design, so this is the only signal that Devin
    // is now waiting on an answer.
    announce(
      names.length > 1
        ? `Devin is asking ${names.length} questions. ${data.message || ""}`.trim()
        : `Devin is asking a question. ${data.message || ""}`.trim()
    );
  }

  // Builds one question block for an elicitation form. Returns the element plus
  // value()/valid() for submission and title/answerText() for the recap.
  // Handles single-select (oneOf), multi-select (array, items.anyOf), an
  // "Other" free-text choice, and a plain text/number/boolean fallback.
  function buildElicitQuestion(key, spec, opts) {
    const title = spec.title || spec.description || key;
    // The full question text (prefer the longer `description`), surfaced in the
    // carousel header so it updates as you step between questions.
    const prompt = spec.description || spec.title || "";
    const field = document.createElement("div");
    field.className = "elicit-field";
    if (!opts.hideTitle && (spec.title || spec.description)) {
      const lab = document.createElement("div");
      lab.className = "elicit-q";
      lab.textContent = title;
      field.appendChild(lab);
    }

    const isMulti = spec.type === "array";
    const optionDefs = isMulti ? (spec.items && spec.items.anyOf) || [] : spec.oneOf || [];

    // Free text / number / boolean when there are no discrete options. Free
    // text uses an auto-growing textarea (VS Code's chat-question-freeform).
    if (!optionDefs.length && !isMulti) {
      let input;
      if (spec.type === "boolean") { input = document.createElement("input"); input.type = "checkbox"; }
      else if (spec.type === "number" || spec.type === "integer") { input = document.createElement("input"); input.type = "number"; }
      else { input = document.createElement("textarea"); input.rows = 1; }
      const isCheckbox = input.type === "checkbox";
      const isNumber = input.type === "number";
      const isFreeform = input.tagName === "TEXTAREA";
      input.className = "elicit-input" + (isFreeform ? " elicit-freeform" : "");
      if (spec.default !== undefined && !isCheckbox) input.value = String(spec.default);
      if (isFreeform) {
        const grow = () => { input.style.height = "auto"; input.style.height = Math.min(Math.max(input.scrollHeight, 28), 160) + "px"; };
        input.addEventListener("input", grow);
        setTimeout(grow, 0);
      }
      field.appendChild(input);
      const val = () => (isCheckbox ? input.checked : isNumber ? Number(input.value) : input.value);
      return {
        key, el: field, title, prompt,
        value: val,
        valid: () => (!opts.required || isCheckbox || String(input.value).trim() !== ""),
        // "Answered" gates the Submit button: a boolean always has a state; text
        // and number need a value.
        answered: () => (isCheckbox ? true : String(input.value).trim() !== ""),
        answerText: () => (isCheckbox ? (input.checked ? "Yes" : "No") : String(input.value)),
        // Enough to put a half finished answer back after a session switch.
        exportState: () => (isCheckbox ? { checked: input.checked } : { text: input.value }),
        importState: (st) => {
          if (!st) return;
          if (isCheckbox) input.checked = !!st.checked;
          else if (typeof st.text === "string") input.value = st.text;
        }
      };
    }

    const name = "elicit-" + key + "-" + Math.random().toString(36).slice(2, 7);
    const choices = []; // { input, label, val } for the fixed options
    let otherRadio = null;
    let otherText = null;

    // VS Code's question-list row: a leading 1-based number, the label, and a
    // trailing check that appears only when the row is selected. The native
    // radio/checkbox is kept (visually hidden) for state + a11y.
    let optNum = 0;
    const addOption = (label, val, isOther) => {
      const opt = document.createElement("label");
      opt.className = "elicit-option";
      const input = document.createElement("input");
      input.type = isMulti ? "checkbox" : "radio";
      input.name = name;
      input.className = "elicit-native";
      if (!isOther) input.value = val;
      const num = document.createElement("span");
      num.className = "elicit-number";
      num.textContent = String(++optNum);
      const span = document.createElement("span");
      span.className = "elicit-option-label";
      span.textContent = label;
      const indicator = document.createElement("i");
      indicator.className = "codicon codicon-check elicit-indicator";
      opt.appendChild(input);
      opt.appendChild(num);
      opt.appendChild(span);
      if (isOther) {
        otherRadio = input;
        // A textarea (not a single-line input) so a long answer wraps onto new
        // lines and grows in height, while sitting between the label and check.
        otherText = document.createElement("textarea");
        otherText.rows = 1;
        otherText.className = "elicit-input elicit-other";
        otherText.placeholder = "Type your answer";
        const grow = () => { otherText.style.height = "auto"; otherText.style.height = Math.min(Math.max(otherText.scrollHeight, 26), 160) + "px"; };
        otherText.addEventListener("input", () => { if (otherText.value && !input.checked) input.checked = true; grow(); });
        setTimeout(grow, 0);
        opt.appendChild(otherText);
      } else {
        choices.push({ input, label, val });
      }
      // Trailing check (VS Code's .chat-question-list-indicator, margin-left auto).
      opt.appendChild(indicator);
      field.appendChild(opt);
    };

    optionDefs.forEach((o) => addOption(o.title || String(o.const), o.const, false));
    if (opts.allowOther) addOption("Other", null, true);

    const otherValue = () => (otherRadio && otherRadio.checked && otherText.value.trim() ? otherText.value.trim() : null);
    const selectedValues = () => {
      const vals = choices.filter((c) => c.input.checked).map((c) => c.val);
      const o = otherValue();
      if (o) vals.push(o);
      return vals;
    };
    const selectedLabels = () => {
      const labels = choices.filter((c) => c.input.checked).map((c) => c.label);
      const o = otherValue();
      if (o) labels.push(o);
      return labels;
    };
    const minItems = spec.minItems || (opts.required ? 1 : 0);
    return {
      key, el: field, title, prompt,
      value: () => (isMulti ? selectedValues() : selectedValues()[0]),
      valid: () => (isMulti ? selectedValues().length >= minItems : (!opts.required || selectedValues().length >= 1)),
      // Submit is gated on every question having at least one option selected.
      answered: () => selectedValues().length >= 1,
      answerText: () => selectedLabels().join(", "),
      exportState: () => ({
        picked: choices.filter((c) => c.input.checked).map((c) => c.val),
        other: otherText ? otherText.value : "",
        otherPicked: !!(otherRadio && otherRadio.checked)
      }),
      importState: (st) => {
        if (!st) return;
        const picked = Array.isArray(st.picked) ? st.picked : [];
        choices.forEach((c) => { c.input.checked = picked.includes(c.val); });
        if (otherText && typeof st.other === "string") otherText.value = st.other;
        if (otherRadio) otherRadio.checked = !!st.otherPicked;
      }
    };
  }

  // Persistent Q/A block left in the transcript after a question is answered,
  // one card per question (VS Code style): dimmed "Q:" and bold "A:", or an
  // italic "Skipped" when nothing was chosen.
  function renderQaRecap(items) {
    ensureTurn();
    items.forEach((it) => {
      const box = document.createElement("div");
      box.className = "qa-recap";
      const q = document.createElement("div");
      q.className = "qa-q";
      q.textContent = "Q: " + it.title;
      box.appendChild(q);
      const a = document.createElement("div");
      if (it.answer && String(it.answer).trim()) {
        a.className = "qa-a";
        a.textContent = "A: " + it.answer;
      } else {
        a.className = "qa-skipped";
        a.textContent = "Skipped";
      }
      box.appendChild(a);
      respTarget().appendChild(box);
    });
    scrollToBottom();
  }

  // --- Working set ---------------------------------------------------------

  // Latest per-file +added/-removed counts, accumulated from fileChange events,
  // so the working set can show per-file and total line deltas like VS Code.
  const wsCounts = new Map();
  // The files the working set is currently listing, retained per session so a
  // switch restores this session's own pending edits.
  let wsFiles = [];
  // What the host last said this chat had changed, kept even while the summary is
  // hidden, so turning the setting back on has something to draw again.
  let wsLast = [];

  function countBadges(target, added, removed) {
    if (added) {
      const a = document.createElement("span");
      a.className = "label-added";
      a.textContent = "+" + added;
      target.appendChild(a);
    }
    if (removed) {
      const r = document.createElement("span");
      r.className = "label-removed";
      r.textContent = "-" + removed;
      target.appendChild(r);
    }
  }

  // Docked working set above the composer. Collapsible (like the plan) with a
  // chevron, and the file list scrolls once it gets long. The collapsible is
  // cached so its collapsed state survives the frequent re-renders during a turn.
  function renderWorkingSet(files) {
    wsLast = files || [];
    files = wsLast;
    // The setting that says "show a summary of files changed at the end of each
    // turn" is this summary, and it was read, sent and stored without ever being
    // looked at, so turning it off did nothing at all.
    if (!caps.showFileChanges || files.length === 0) { hideWorkingSet(); return; }
    // Counts sent with the files fill the tally, so a working set restored after a
    // window reload shows its line counts rather than bare names.
    files.forEach((f) => {
      if (f.path && !wsCounts.has(f.path) && (f.added || f.removed)) {
        wsCounts.set(f.path, { added: f.added || 0, removed: f.removed || 0 });
      }
    });
    wsFiles = files;
    el.workingSet.classList.remove("hidden");
    let ctrl = el.workingSet._ctrl;
    if (!ctrl) {
      el.workingSet.innerHTML = "";
      ctrl = makeCollapsible("ws-collapsible", {
        startCollapsed: false,
        onUserToggle: () => { wsCollapsePref = el.workingSet._ctrl.isCollapsed(); }
      });
      ctrl.header.classList.add("ws-header");
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-right ws-chevron";
      const label = document.createElement("span");
      label.className = "ws-label";
      const main = document.createElement("div");
      main.className = "ws-header-main";
      main.appendChild(chev);
      main.appendChild(label);
      const actions = document.createElement("div");
      actions.className = "ws-actions";
      actions.appendChild(btn("Open all", "secondary", () => vscode.postMessage({ type: "openAllDiffs" })));
      actions.appendChild(btn("Keep all", "primary", () => vscode.postMessage({ type: "acceptAll" })));
      actions.appendChild(btn("Undo all", "secondary", () => vscode.postMessage({ type: "rejectAll" })));
      // Header toggles the collapse, so the action buttons must not bubble.
      actions.addEventListener("click", (e) => e.stopPropagation());
      ctrl.header.appendChild(main);
      ctrl.header.appendChild(actions);
      const list = document.createElement("div");
      list.className = "ws-list";
      ctrl.body.appendChild(list);
      el.workingSet.appendChild(ctrl.root);
      el.workingSet._ctrl = ctrl;
      el.workingSet._label = label;
      el.workingSet._list = list;
    }
    const label = el.workingSet._label;
    label.textContent = `${files.length} changed file${files.length > 1 ? "s" : ""}`;
    // Total +added / -removed across the working set (VS Code's line counts).
    let totAdded = 0, totRemoved = 0;
    files.forEach((f) => { const c = wsCounts.get(f.path); if (c) { totAdded += c.added || 0; totRemoved += c.removed || 0; } });
    const counts = document.createElement("span");
    counts.className = "ws-counts";
    countBadges(counts, totAdded, totRemoved);
    label.appendChild(counts);
    const list = el.workingSet._list;
    list.innerHTML = "";
    files.forEach((f) => {
      const row = document.createElement("div");
      row.className = "ws-file";
      const link = document.createElement("a");
      link.className = "file-change file-pill";
      link.tabIndex = 0;
      link.setAttribute("role", "button");
      const icon = document.createElement("i");
      icon.className = "codicon " + fileIconFor(f.name) + " file-pill-icon";
      icon.setAttribute("aria-hidden", "true");
      const nm = document.createElement("span");
      nm.className = "file-pill-name";
      nm.textContent = f.name;
      link.appendChild(icon);
      link.appendChild(nm);
      const c = wsCounts.get(f.path);
      if (c) countBadges(link, c.added, c.removed);
      link.title = f.path;
      const open = () => vscode.postMessage({ type: "openDiff", path: f.path });
      link.addEventListener("click", open);
      link.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
      const grp = document.createElement("div");
      grp.className = "ws-file-actions";
      grp.appendChild(iconBtn("codicon-check", "Keep", () => vscode.postMessage({ type: "acceptFile", path: f.path })));
      grp.appendChild(iconBtn("codicon-discard", "Undo", () => vscode.postMessage({ type: "rejectFile", path: f.path })));
      row.appendChild(link);
      row.appendChild(grp);
      list.appendChild(row);
    });
    // Honour a remembered manual collapse (the widget is otherwise always shown
    // expanded), so it survives the working set hiding and reappearing.
    if (wsCollapsePref !== null) ctrl.setCollapsed(wsCollapsePref);
    updateComposerDock();
  }

  function hideWorkingSet() {
    wsFiles = [];
    el.workingSet.classList.add("hidden");
    el.workingSet.innerHTML = "";
    el.workingSet._ctrl = null;
    el.workingSet._label = null;
    el.workingSet._list = null;
    updateComposerDock();
  }

  // --- Attachments + implicit context -------------------------------------

  let lastAttachments = [];
  // The active-editor "current file" that VS Code shows as an implicit context
  // pill: { path, name, line1?, line2?, enabled } or null.
  let implicit = null;

  // Best-effort file-type codicon by extension. A webview cannot reach VS Code's
  // file icon theme, so this maps the common cases; unknown falls back to file.
  const FILE_ICONS = {
    js: "codicon-file-code", jsx: "codicon-file-code", ts: "codicon-file-code", tsx: "codicon-file-code",
    py: "codicon-file-code", rb: "codicon-file-code", go: "codicon-file-code", rs: "codicon-file-code",
    java: "codicon-file-code", c: "codicon-file-code", h: "codicon-file-code", cpp: "codicon-file-code",
    cs: "codicon-file-code", php: "codicon-file-code", sh: "codicon-terminal", bash: "codicon-terminal",
    zsh: "codicon-terminal", html: "codicon-file-code", css: "codicon-file-code", scss: "codicon-file-code",
    json: "codicon-json", md: "codicon-markdown", markdown: "codicon-markdown",
    png: "codicon-file-media", jpg: "codicon-file-media", jpeg: "codicon-file-media", gif: "codicon-file-media",
    svg: "codicon-file-media", webp: "codicon-file-media", pdf: "codicon-file-pdf",
    zip: "codicon-file-zip", tar: "codicon-file-zip", gz: "codicon-file-zip"
  };
  function fileIconFor(name) {
    const ext = (String(name || "").split(".").pop() || "").toLowerCase();
    return FILE_ICONS[ext] || "codicon-file";
  }

  function renderAttachments(items) {
    lastAttachments = Array.isArray(items) ? items : [];
    renderComposerContext();
  }

  // --- Queued messages (sent after the current turn finishes) --------------

  // Messages submitted while a turn is in flight are queued by the host rather
  // than dropped (VS Code's chat queue). They render at the very bottom of the
  // transcript under a "Queued" divider, as dimmed request bubbles like VS Code's
  // pending rows, each editable in place (keeps its position) and removable, and
  // the host auto-sends them in order. A CSS `order` keeps the block pinned below
  // every turn even when a flush appends a new one.
  let queuedItems = [];
  // The queued message being edited in the composer (null = not editing one).
  let editingQueuedId = null;

  function renderQueued(items) {
    queuedItems = Array.isArray(items) ? items : [];
    // The item being edited may have flushed (its turn started) while editing;
    // drop the stale editing state so the banner does not dangle.
    if (editingQueuedId && !queuedItems.some((q) => q.id === editingQueuedId)) {
      clearQueuedEditState();
    }
    const existing = el.thread.querySelector(".queued-inline");
    if (existing) existing.remove();
    if (queuedItems.length === 0) return;
    const box = document.createElement("div");
    box.className = "queued-inline";
    const head = document.createElement("div");
    head.className = "queued-divider";
    // Show the depth so it is obvious the queue is draining as each one goes out.
    head.textContent = queuedItems.length > 1 ? "Queued (" + queuedItems.length + ")" : "Queued";
    head.title = "Sent in order once the current response finishes";
    box.appendChild(head);
    queuedItems.forEach((q) => box.appendChild(queuedRow(q)));
    el.thread.appendChild(box);
    scrollToBottom();
  }

  // One queued message, styled as a dimmed user request bubble (VS Code's pending
  // request row) with edit/remove actions that appear on hover.
  function queuedRow(q) {
    const item = document.createElement("div");
    item.className = "queued-item" + (q.id === editingQueuedId ? " editing" : "");
    item.dataset.id = q.id;
    // What it is queued with, above it, as on a request that has been sent.
    const context = attachedContextRow(q.attachments);
    if (context) item.appendChild(context);
    const bubble = document.createElement("div");
    bubble.className = "queued-bubble bubble";
    bubble.textContent = q.text;
    const actions = document.createElement("div");
    actions.className = "queued-actions";
    actions.appendChild(iconBtn("codicon-edit", "Edit queued message", (e) => { e.stopPropagation(); startQueuedEdit(q); }));
    // VS Code's "Send Immediately" (Codicon.newLine), and its own words for what
    // that costs: the turn in flight is ended so this one can go now.
    actions.appendChild(iconBtn("codicon-newline", "Cancel the current request and send this message immediately", (e) => {
      e.stopPropagation();
      // Commit an in-progress edit of this same message first, so it is sent
      // with what is currently typed rather than the stale text.
      if (editingQueuedId === q.id) {
        const text = el.input.value.trim();
        if (text) vscode.postMessage({ type: "editQueued", id: q.id, text });
        finishQueuedEdit();
      }
      vscode.postMessage({ type: "sendQueuedNow", id: q.id });
    }));
    actions.appendChild(iconBtn("codicon-close", "Remove from queue", (e) => {
      e.stopPropagation();
      if (editingQueuedId === q.id) finishQueuedEdit();
      vscode.postMessage({ type: "removeQueued", id: q.id });
    }));
    item.appendChild(bubble);
    item.appendChild(actions);
    return item;
  }

  // Load a queued message into the composer to edit it in place. It stays in the
  // queue (its slot is reserved) and is updated on submit, so it never jumps to
  // the end.
  function startQueuedEdit(q) {
    if (editingTurn) cancelInputEditing();
    editingQueuedId = q.id;
    // Tell the host which message we are editing: the queue keeps draining past
    // it, but holds when this one reaches the head until we commit or cancel.
    vscode.postMessage({ type: "queueEditing", id: q.id });
    el.input.value = q.text;
    el.inputBox.classList.add("editing-request");
    showEditingBanner("Editing queued message", cancelQueuedEdit);
    el.input.focus();
    el.input.setSelectionRange(el.input.value.length, el.input.value.length);
    autosize();
    updateSendState();
    markQueuedEditing(); // highlight the row being edited (no full re-render)
  }

  // Toggle the "editing" highlight on the queued bubble that matches the id being
  // edited, without rebuilding the list (which would detach the rows).
  function markQueuedEditing() {
    el.thread.querySelectorAll(".queued-item").forEach((it) => {
      it.classList.toggle("editing", !!editingQueuedId && it.dataset.id === editingQueuedId);
    });
  }

  // Reset the composer chrome after a queued edit ends (submit or cancel), and
  // release the host's hold on that message so it can be sent.
  function clearQueuedEditState() {
    if (!editingQueuedId) return;
    editingQueuedId = null;
    el.inputBox.classList.remove("editing-request");
    removeEditingBanner();
    markQueuedEditing();
    vscode.postMessage({ type: "queueEditing", id: null });
  }

  function finishQueuedEdit() {
    clearQueuedEditState();
    el.input.value = "";
    // Editing borrowed the composer, so give the draft it was holding back.
    restoreDraft();
    closeAutocomplete();
    autosize();
    updateSendState();
  }

  function cancelQueuedEdit() {
    if (!editingQueuedId) return;
    finishQueuedEdit();
  }

  // Renders the implicit "current file" pill (first) followed by the explicit
  // attachment pills, into the shared #attachments row.
  function renderComposerContext() {
    el.attachments.innerHTML = "";
    const hasImplicit = !!(implicit && implicit.path);
    if (!hasImplicit && lastAttachments.length === 0) { el.attachments.classList.add("hidden"); return; }
    el.attachments.classList.remove("hidden");
    if (hasImplicit) el.attachments.appendChild(implicitChip(implicit));
    lastAttachments.forEach((a) => el.attachments.appendChild(attachmentChip(a)));
  }

  function attachmentChip(a) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.tabIndex = 0;
    chip.setAttribute("role", "button");
    chip.dataset.id = a.id;
    if (a.type === "image" && a.thumb) {
      const img = document.createElement("img");
      img.className = "chip-thumb";
      img.src = a.thumb;
      img.alt = "";
      chip.appendChild(img);
    } else {
      const icon = document.createElement("i");
      icon.className = "codicon " + (
        a.type === "image" ? "codicon-file-media"
          : a.type === "selection" ? "codicon-selection"
          : a.type === "directory" ? "codicon-folder"
          : fileIconFor(a.label));
      icon.setAttribute("aria-hidden", "true");
      chip.appendChild(icon);
    }
    const label = document.createElement("span");
    label.className = "chip-label";
    label.textContent = a.label;
    chip.appendChild(label);
    const x = document.createElement("button");
    x.className = "chip-x";
    x.tabIndex = -1;
    x.title = "Remove from context";
    x.innerHTML = '<i class="codicon codicon-close"></i>';
    x.addEventListener("click", (e) => { e.stopPropagation(); vscode.postMessage({ type: "removeAttachment", id: a.id }); });
    chip.appendChild(x);
    chip.addEventListener("keydown", (e) => onChipKey(e, chip));
    return chip;
  }

  function implicitChip(ic) {
    const chip = document.createElement("span");
    chip.className = "chip implicit" + (ic.enabled ? "" : " disabled");
    chip.tabIndex = 0;
    chip.setAttribute("role", "button");
    chip.title = ic.path;
    // VS Code renders the toggle (x when enabled, + when disabled) before the
    // label. Clicking it includes/excludes the current file.
    const toggle = document.createElement("button");
    toggle.className = "chip-x";
    toggle.tabIndex = -1;
    toggle.title = ic.enabled ? "Don't include the current file" : "Include the current file";
    toggle.innerHTML = '<i class="codicon ' + (ic.enabled ? "codicon-close" : "codicon-add") + '"></i>';
    toggle.addEventListener("click", (e) => { e.stopPropagation(); vscode.postMessage({ type: "setImplicit", enabled: !ic.enabled }); });
    const icon = document.createElement("i");
    icon.className = "codicon " + fileIconFor(ic.name);
    icon.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.className = "chip-label";
    const range = ic.line1 ? ":" + ic.line1 + (ic.line2 && ic.line2 !== ic.line1 ? "-" + ic.line2 : "") : "";
    label.textContent = ic.name + range;
    chip.appendChild(toggle);
    chip.appendChild(icon);
    chip.appendChild(label);
    chip.addEventListener("keydown", (e) => onChipKey(e, chip));
    return chip;
  }

  // Arrow keys move focus between pills; Delete/Backspace removes the focused
  // one (or disables the implicit pill).
  function onChipKey(e, chip) {
    const chips = [...el.attachments.querySelectorAll(".chip")];
    const i = chips.indexOf(chip);
    if (e.key === "ArrowRight") { e.preventDefault(); (chips[i + 1] || chips[0]).focus(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); (chips[i - 1] || chips[chips.length - 1]).focus(); }
    else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      const next = chips[i + 1] || chips[i - 1];
      if (chip.classList.contains("implicit")) vscode.postMessage({ type: "setImplicit", enabled: false });
      else if (chip.dataset.id) vscode.postMessage({ type: "removeAttachment", id: chip.dataset.id });
      if (next) next.focus();
    }
  }

  // --- Reusable session list (shared by the full list and the title menu) --

  // `status` is a Set of the selected states; empty means no state filter, so
  // several can be combined (Running + Terminated, say).
  function filterSessions(sessions, q, status) {
    q = (q || "").trim().toLowerCase();
    const sel = status && status.size ? status : null;
    return sessions.filter((s) => {
      if (sel) {
        const st = sessionStatuses[s.id];
        // A chat open on another surface has no status here (the host reports only
        // this surface's runtimes), and its row is drawn as idle. Filtering it as
        // terminated contradicted the dot beside it.
        const away = elsewhereIds.includes(s.id);
        const working = st === "running" || st === "starting" || st === "attention";
        const alive = working || st === "idle" || away;
        const match =
          (sel.has("running") && working) ||
          (sel.has("idle") && (st === "idle" || away)) ||
          (sel.has("terminated") && !alive);
        if (!match) return false;
      }
      if (!q) return true;
      return (s.title || "").toLowerCase().includes(q) || (s.short_id || s.id || "").toLowerCase().includes(q);
    });
  }

  // Rank for the "State" sort: working sessions first, then idle, then ended.
  function sessionStateRank(s) {
    const st = sessionStatuses[s.id];
    if (st === "running" || st === "starting" || st === "attention") return 0;
    // Alive elsewhere is alive, the same as the dot says.
    if (st === "idle" || elsewhereIds.includes(s.id)) return 1;
    return 2;
  }

  function sortSessions(sessions, sort) {
    const at = (s) => s.last_activity_at || 0;
    const out = sessions.slice();
    if (sort === "state") {
      out.sort((a, b) => sessionStateRank(a) - sessionStateRank(b) || at(b) - at(a));
    } else if (sort === "name") {
      out.sort((a, b) => (a.title || a.short_id || a.id || "").localeCompare(b.title || b.short_id || b.id || ""));
    } else {
      out.sort((a, b) => at(b) - at(a));
    }
    return out;
  }

  // Groups sessions by workspace (workspace folders first) with collapsible
  // headers, appending session rows into `container`.
  function renderSessionGroups(container, sessions, activeId, folders) {
    const folderNames = new Map((folders || []).map((f) => [f.path, f.name]));
    const groups = new Map();
    const orderedKeys = [];
    // The CLI reports a session's directory as it was given, and VS Code reports
    // the folder's own way (a lower cased drive letter on Windows), so the two
    // are compared without case rather than character by character.
    const keyFor = (s) => {
      const wd = (s.working_directory || "").toLowerCase();
      for (const f of folders || []) {
        const dir = f.path.toLowerCase();
        if (wd === dir || wd.startsWith(dir + "/") || wd.startsWith(dir + "\\")) return f.path;
      }
      return s.working_directory || "__workspace__";
    };
    sessions.forEach((s) => {
      const key = keyFor(s);
      if (!groups.has(key)) { groups.set(key, []); orderedKeys.push(key); }
      groups.get(key).push(s);
    });
    const folderOrder = (folders || []).map((f) => f.path);
    const rank = (k) => { const i = folderOrder.indexOf(k); return i === -1 ? folderOrder.length + 1 : i; };
    orderedKeys.sort((a, b) => rank(a) - rank(b));

    const showGroups = (folders || []).length > 1 || orderedKeys.length > 1;
    orderedKeys.forEach((key) => {
      const rows = groups.get(key);
      if (showGroups) {
        const collapsed = collapsedGroups.has(key);
        const header = document.createElement("div");
        header.className = "group-header" + (collapsed ? " collapsed" : "");
        const chev = document.createElement("i");
        chev.className = "codicon codicon-chevron-down group-chevron";
        const txt = document.createElement("span");
        txt.className = "group-label";
        txt.textContent = folderNames.get(key) || (key === "__workspace__" ? "This workspace" : baseName(key));
        const count = document.createElement("span");
        count.className = "group-count";
        count.textContent = String(rows.length);
        header.appendChild(chev);
        header.appendChild(txt);
        header.appendChild(count);
        const box = document.createElement("div");
        box.className = "group-items" + (collapsed ? " hidden" : "");
        rows.forEach((s) => box.appendChild(sessionRow(s, activeId)));
        header.addEventListener("click", () => {
          const nowCollapsed = !collapsedGroups.has(key);
          if (nowCollapsed) collapsedGroups.add(key); else collapsedGroups.delete(key);
          box.classList.toggle("hidden", nowCollapsed);
          header.classList.toggle("collapsed", nowCollapsed);
        });
        container.appendChild(header);
        container.appendChild(box);
      } else {
        rows.forEach((s) => container.appendChild(sessionRow(s, activeId)));
      }
    });
  }

  // Buckets sessions by last-activity date, with collapsible headers.
  function renderDateGroups(container, sessions, activeId) {
    const now = Date.now() / 1000;
    const bucket = (ts) => {
      if (!ts) return "Older";
      const d = now - ts;
      if (d < 86400) return "Today";
      if (d < 172800) return "Yesterday";
      if (d < 604800) return "This week";
      if (d < 2592000) return "This month";
      return "Older";
    };
    const order = ["Today", "Yesterday", "This week", "This month", "Older"];
    const groups = new Map();
    sessions.forEach((s) => { const k = bucket(s.last_activity_at); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(s); });
    order.forEach((label) => {
      const rows = groups.get(label);
      if (!rows) return;
      const key = "date:" + label;
      const collapsed = collapsedGroups.has(key);
      const header = document.createElement("div");
      header.className = "group-header" + (collapsed ? " collapsed" : "");
      const chev = document.createElement("i");
      chev.className = "codicon codicon-chevron-down group-chevron";
      const txt = document.createElement("span");
      txt.className = "group-label";
      txt.textContent = label;
      const count = document.createElement("span");
      count.className = "group-count";
      count.textContent = String(rows.length);
      header.append(chev, txt, count);
      const box = document.createElement("div");
      box.className = "group-items" + (collapsed ? " hidden" : "");
      rows.forEach((s) => box.appendChild(sessionRow(s, activeId)));
      header.addEventListener("click", () => {
        const nowCollapsed = !collapsedGroups.has(key);
        if (nowCollapsed) collapsedGroups.add(key); else collapsedGroups.delete(key);
        box.classList.toggle("hidden", nowCollapsed);
        header.classList.toggle("collapsed", nowCollapsed);
      });
      container.appendChild(header);
      container.appendChild(box);
    });
  }

  function mountSessionList(container, opts) {
    opts = opts || {};
    container.innerHTML = "";
    const state = { q: "", status: new Set(), grouping: "workspace", sort: "activity" };
    let searchFloater = null;
    let filterFloater = null;

    // Optional in-list toolbar, used by the side panel and the title switcher.
    // The full-screen list is driven from the header instead (controls omitted).
    if (opts.controls === "panel") {
      const mkTool = (iconName, title, onClick) => {
        const b = document.createElement("button");
        b.className = "session-tool-btn";
        b.title = title;
        b.setAttribute("aria-label", title);
        b.appendChild(mkIcon(iconName));
        b.addEventListener("click", (e) => { e.stopPropagation(); onClick(b); });
        return b;
      };
      const toolbar = document.createElement("div");
      toolbar.className = "session-toolbar";
      // The docked panel can be dragged to the other side by its own header. Only
      // the empty space does it, so the tool buttons keep their clicks.
      if (opts.movable) {
        toolbar.classList.add("session-toolbar-movable");
        toolbar.title = "Drag to move the panel to the other side";
        toolbar.addEventListener("mousedown", (e) => {
          if (e.target.closest("button")) return;
          startPanelDrag(e);
        });
      }
      const titleLabel = document.createElement("span");
      titleLabel.className = "session-panel-title";
      titleLabel.textContent = "Sessions";
      const spacer = document.createElement("span");
      spacer.className = "session-tool-spacer";
      const newBtn = buildNewSessionButton({ compact: true });
      const searchBtn = mkTool("search", "Search sessions", (b) => api.toggleSearch(b));
      const filterBtn = mkTool("list-filter", "Filter sessions", (b) => api.toggleFilter(b));
      toolbar.append(titleLabel, spacer, newBtn, searchBtn, filterBtn);
      container.appendChild(toolbar);
    }

    const bodyEl = document.createElement("div");
    bodyEl.className = "session-list-body";
    container.appendChild(bodyEl);

    function renderBody() {
      bodyEl.innerHTML = "";
      if (!lastSessions.length) {
        bodyEl.innerHTML = '<div class="sessions-empty"><i class="codicon codicon-comment-discussion"></i><div>No chats yet.</div></div>';
        return;
      }
      const filtered = sortSessions(filterSessions(lastSessions, state.q, state.status), state.sort);
      if (!filtered.length) { bodyEl.innerHTML = '<div class="sessions-empty-sm">No matching sessions</div>'; return; }
      if (state.grouping === "none") {
        filtered.forEach((s) => bodyEl.appendChild(sessionRow(s, lastActiveId)));
      } else if (state.grouping === "date") {
        renderDateGroups(bodyEl, filtered, lastActiveId);
      } else {
        renderSessionGroups(bodyEl, filtered, lastActiveId, lastFolders);
      }
    }

    function toggleSearch(anchor) {
      if (searchFloater) { searchFloater.close(); return; }
      const inp = document.createElement("input");
      inp.className = "session-search-pop";
      inp.type = "text";
      inp.placeholder = "Search sessions\u2026";
      inp.value = state.q;
      inp.addEventListener("input", () => { state.q = inp.value; renderBody(); });
      inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape" && searchFloater) searchFloater.close(); });
      searchFloater = makeFloater(anchor, inp, "right", () => { searchFloater = null; });
      inp.focus();
    }

    function toggleFilter(anchor) {
      if (filterFloater) { filterFloater.close(); return; }
      const menu = document.createElement("div");
      menu.className = "dv-menu session-filter-menu";
      const build = () => {
        menu.innerHTML = "";
        const group = (title, options, get, set) => {
          const label = document.createElement("div");
          label.className = "dv-menu-label";
          label.textContent = title;
          menu.appendChild(label);
          for (const [val, lab] of options) {
            const row = document.createElement("button");
            row.className = "dv-menu-item radio" + (get() === val ? " checked" : "");
            const chk = mkIcon(get() === val ? "check" : "blank");
            chk.classList.add("dv-menu-check");
            row.appendChild(chk);
            row.appendChild(Object.assign(document.createElement("span"), { textContent: lab }));
            row.addEventListener("click", (e) => { e.stopPropagation(); set(val); build(); renderBody(); });
            menu.appendChild(row);
          }
        };
        // Status is multi select: tick any combination, or All to clear them.
        const multi = (title, options) => {
          const label = document.createElement("div");
          label.className = "dv-menu-label";
          label.textContent = title;
          menu.appendChild(label);
          const none = state.status.size === 0;
          for (const [val, lab] of options) {
            const on = val === "all" ? none : state.status.has(val);
            const row = document.createElement("button");
            row.className = "dv-menu-item radio" + (on ? " checked" : "");
            const chk = mkIcon(on ? "check" : "blank");
            chk.classList.add("dv-menu-check");
            row.appendChild(chk);
            row.appendChild(Object.assign(document.createElement("span"), { textContent: lab }));
            row.addEventListener("click", (e) => {
              e.stopPropagation();
              if (val === "all") state.status.clear();
              else if (state.status.has(val)) state.status.delete(val);
              else state.status.add(val);
              build();
              renderBody();
            });
            menu.appendChild(row);
          }
        };
        const sep = () => {
          const s = document.createElement("div");
          s.className = "dv-menu-sep";
          menu.appendChild(s);
        };
        multi("Status", [["all", "All"], ["running", "Running"], ["idle", "Idle"], ["terminated", "Terminated"]]);
        sep();
        group("Sort by", [["activity", "Last activity"], ["state", "State"], ["name", "Name"]], () => state.sort, (v) => { state.sort = v; });
        sep();
        group("Group by", [["workspace", "Workspace"], ["date", "Date"], ["none", "None"]], () => state.grouping, (v) => { state.grouping = v; });
      };
      build();
      filterFloater = makeFloater(anchor, menu, "right", () => { filterFloater = null; });
    }

    const api = {
      refresh: renderBody,
      toggleSearch,
      toggleFilter,
      setQuery: (q) => { state.q = q; renderBody(); },
      setStatus: (s) => { state.status = s instanceof Set ? s : new Set(s && s !== "all" ? [s] : []); renderBody(); },
      setSort: (s) => { state.sort = s; renderBody(); },
      setGrouping: (g) => { state.grouping = g; renderBody(); }
    };
    renderBody();
    return api;
  }

  // --- Title session switcher (dropdown from the header title) -------------

  function toggleTitleMenu() {
    if (inEditor()) return;
    if (document.getElementById("title-menu")) { closeTitleMenu(); return; }
    const menu = document.createElement("div");
    menu.id = "title-menu";
    menu.className = "title-menu";
    menu.addEventListener("click", (e) => e.stopPropagation());
    el.titleBtn.parentElement.appendChild(menu);
    menuCtrl = mountSessionList(menu, { controls: "panel" });
    reportListVisible();
  }

  function closeTitleMenu() {
    const m = document.getElementById("title-menu");
    if (m) m.remove();
    menuCtrl = null;
    reportListVisible();
  }

  // --- Sessions list -------------------------------------------------------

  function renderSessions(sessions, activeId, folders) {
    lastSessions = sessions || [];
    lastActiveId = activeId;
    lastFolders = folders || [];
    numberedIds = sortSessions(lastSessions, "activity").slice(0, 9).map((s) => s.id);
    if (!listCtrl) {
      listCtrl = mountSessionList(el.sessionsList, {});
    } else {
      listCtrl.refresh();
    }
    if (menuCtrl) {
      menuCtrl.refresh();
    }
    if (panelCtrl && sessionsPanelOpen) {
      panelCtrl.refresh();
    }
    // Refresh the in-session header so the subtitle (session code) fills in once
    // this session's metadata arrives.
    if (body === "thread") renderHeader();
  }

  // Detach the currently mounted transcript into `views` so we can restore it
  // instantly later, and reset the live singletons for whatever renders next.
  // `opening` is the session about to be shown, when there is one: it must survive
  // the eviction below, or it would be restored from a cache entry that no longer
  // exists and come up blank.
  function snapshotCurrent(opening) {
    cancelPreviewWaiters();
    if (!curSessionId) return;
    // Abandon any in-progress queued-message edit (releases the host's queue
    // hold) so it does not carry over to the next session.
    cancelQueuedEdit();
    // Close the open block before snapshotting so a half-streamed thinking or
    // message settles ("Thinking…" -> "Thought for Xs") instead of being frozen
    // mid stream. The turn keeps running in the background; its continuation
    // replays into a fresh block when we return.
    finalizeBlock();
    // The "Working…" line is part of the DOM we are about to retain, so drop it
    // now (while it is still in the thread) rather than only untracking it.
    // Otherwise a frozen one comes back on return and the next turn adds a
    // second one beside it.
    hideWorking();
    // The open question/permission widgets belong to the session we are leaving,
    // and the host re-posts the ones still outstanding when it is reopened. They
    // live in the composer, not the thread, so this snapshot does not carry them:
    // leaving them up would show one session's question over another's
    // transcript, and double up on return.
    cancelPrompts();
    // An edit in progress belongs to a turn of the session being left, and `turns`
    // is about to be replaced. Left open, the dimming it puts on the turns it would
    // discard could never be lifted (the code that lifts it looks the turn up in
    // `turns`), leaving that chat at 40% opacity and refusing clicks for good. In
    // composer mode it was worse: the next message typed anywhere was taken as a
    // submit of this edit, and rewound whichever chat was open by a node id from
    // this one.
    if (inlineEditTurn) finishEditing(inlineEditTurn);
    cancelInputEditing();
    // Which servers failed, and which warnings were waved away, belong to the chat
    // being left. The host re-derives them for the one being opened, and keeping the
    // dismissals silenced a real warning in the next chat, which is usually about
    // the same server since they are configured per project.
    mcpDismissed.clear();
    // Persist the unsent text against the session being left, before the composer
    // is handed to the next one.
    saveDraft();
    const frag = document.createDocumentFragment();
    while (el.thread.firstChild) frag.appendChild(el.thread.firstChild);
    views.set(curSessionId, {
      frag, turns, currentTurn, turnSeq, lastHead, lastHeadReliable,
      toolEls: new Map(toolEls), subagentEls: new Map(subagentEls), terminalCache: new Map(terminalCache),
      commands: commands.slice(), title: currentTitle, lastUserText, draft: el.input.value,
      // The composer widgets live outside the thread, so they are kept here by
      // value and put back by restoreView: the plan Devin is working through, the
      // files it has changed and their line counts, and the context ring.
      plan: dockedPlan.slice(), planCollapsePref,
      wsFiles: wsFiles.slice(), wsCounts: new Map(wsCounts), wsCollapsePref,
      usage: lastUsage
    });
    // Per session: the next session must not inherit this one's last message
    // (it backs ArrowUp recall and Retry) nor its unsent draft. Both are put
    // back by restoreView when this session is reopened.
    lastUserText = "";
    el.input.value = "";
    autosize();
    updateSendState();
    // A session that was mid-run when we left it will keep changing, so its
    // snapshot is stale: force a reload when we come back. So is one that was still
    // opening, and worse: what is cached is however much of the history had arrived,
    // with the loading row still in it, and the rest is dropped, since the host stops
    // painting a replay for a chat that is no longer on screen. Restoring that showed
    // a chat missing most of itself under a spinner that had nothing left to clear it.
    const leaving = sessionStatuses[curSessionId];
    if (leaving === "running" || leaving === "starting") dirtyViews.add(curSessionId);
    // Cap retained transcripts to bound DOM retention. Never the one being left, and
    // never the one being opened: evicting that left the chat it was about to
    // restore with an empty thread and no reload, so it came up blank.
    if (views.size > 8) {
      for (const oldest of views.keys()) {
        if (oldest !== curSessionId && oldest !== opening) {
          views.delete(oldest);
          dirtyViews.delete(oldest);
          break;
        }
      }
    }
    turns = [];
    currentTurn = null;
    toolEls.clear();
    subagentEls.clear();
    terminalCache.clear();
    // Reset the transient per-session UI that is NOT part of the moved DOM, so
    // the previous session's working-set deltas, context-usage ring and docked
    // plan do not bleed into the next view. Each was retained above and comes
    // back with the session, and the host posts the working set for whichever
    // session is opened next.
    wsCounts.clear();
    renderWorkingSet([]);
    lastUsage = null;
    el.usage.classList.add("hidden");
    el.usage.innerHTML = "";
    closeUsagePopup();
    planCollapsePref = null;
    wsCollapsePref = null;
    hideDockedPlan();
    // The busy chrome (Stop button, working border) belongs to the session we
    // just left. Clear it so it never bleeds into the next one: the host posts
    // the real state for whichever session we open.
    if (busy) setBusy(false);
  }

  // Re-mount a retained transcript (real nodes, listeners intact) without a
  // reload.
  function restoreView(id) {
    const v = views.get(id);
    if (!v) return;
    el.thread.innerHTML = "";
    el.thread.appendChild(v.frag);
    el.thread.querySelectorAll(".resp-text, .subagent-prose-content, .subagent-section-body, .thinking-item-content")
      .forEach(enhanceAnchors);
    turns = v.turns;
    currentTurn = v.currentTurn;
    turnSeq = v.turnSeq;
    lastHead = v.lastHead;
    lastHeadReliable = !!v.lastHeadReliable;
    toolEls.clear();
    v.toolEls.forEach((val, k) => toolEls.set(k, val));
    subagentEls.clear();
    (v.subagentEls || new Map()).forEach((val, k) => subagentEls.set(k, val));
    terminalCache.clear();
    v.terminalCache.forEach((val, k) => terminalCache.set(k, val));
    commands = v.commands || [];
    lastUserText = v.lastUserText || "";
    // Put the composer widgets back: this session's plan, its changed files with
    // their line counts, and its context usage.
    planCollapsePref = v.planCollapsePref === undefined ? null : v.planCollapsePref;
    wsCollapsePref = v.wsCollapsePref === undefined ? null : v.wsCollapsePref;
    renderDockedPlan(v.plan || []);
    wsCounts.clear();
    (v.wsCounts || new Map()).forEach((val, k) => wsCounts.set(k, val));
    renderWorkingSet(v.wsFiles || []);
    if (v.usage) renderUsage(v.usage);
    // Put this session's own unsent draft back in the composer.
    el.input.value = v.draft || "";
    autosize();
    updateSendState();
    currentTitle = v.title || currentTitle;
    el.chatTitle.textContent = currentTitle;
    views.delete(id);
    dirtyViews.delete(id);
    forceScrollToBottom();
  }

  // Open a session from the list: restore its transcript instantly when it is
  // idle and unchanged, otherwise ask the host to wake/reload it.
  function switchToSession(id, title) {
    if (id === curSessionId) { setBody("thread"); return; }
    pendingModelChange = 0;
    // Held by the other surface: say so rather than restoring a copy of it here.
    // The host answers with the same state, and corrects this if it disagrees.
    if (elsewhereIds.includes(id)) {
      renderElsewhere({
        id,
        title,
        where: inEditor() ? "the side panel" : "an editor tab",
        here: inEditor() ? "this tab" : "the side panel"
      });
      vscode.postMessage({ type: "loadSession", id });
      return;
    }
    currentTitle = title || "Chat";
    el.chatTitle.textContent = currentTitle;
    setBody("thread");
    // Restore the cached transcript instantly whenever we have a clean copy,
    // even for a dead (terminated / idle-exited) session. A live session just
    // needs re-pointing (activate); a dead one is spawned in the background
    // (wake) while its transcript stays on screen, so there is no "Waking…"
    // spinner. Only a session we have never displayed here needs a full load.
    const status = sessionStatuses[id];
    // "attention" means the session is alive with a turn in flight, blocked on a
    // permission/question, so treat it like running (re-attach, never wake).
    const running = status === "running" || status === "attention";
    const alive = running || status === "idle" || status === "starting";
    snapshotCurrent(id);
    // Read after the snapshot, not before: snapshotting evicts the least recently
    // visited transcript, and deciding beforehand meant acting on a cache entry that
    // the snapshot had just thrown away, which left the chat blank with no reload.
    const haveView = views.has(id) && !dirtyViews.has(id);
    curSessionId = id;
    if (running && views.has(id)) {
      // A turn is in flight. Reloading its transcript over the live channel
      // aborts the prompt ("channel closed"), so re-attach to the running
      // session instead and let it keep streaming into the restored thread.
      restoreView(id);
      dirtyViews.delete(id);
      vscode.postMessage({ type: "activateSession", id });
    } else if (haveView) {
      restoreView(id);
      if (alive) {
        vscode.postMessage({ type: "activateSession", id });
      } else {
        // A wake spawns a fresh acp, which re-expands the conversation and
        // orphans the cached revert node ids, so the restored turns are no
        // longer safe revert targets (same as a reload).
        invalidateRevertHeads();
        vscode.postMessage({ type: "wakeSession", id });
      }
    } else {
      views.delete(id);
      dirtyViews.delete(id);
      // Show the spinner immediately: a full load round-trips through the host
      // (health check, spawning a fresh acp, replaying history), which can take a
      // few seconds, and the host's own `clear{loading}` only lands after that.
      showThreadLoading(!alive);
      vscode.postMessage({ type: "loadSession", id });
    }
  }

  // A status-only update (no list change): refresh the dots. Status ticks can
  // be frequent and often carry no change, so skip the (full) list rebuild when
  // the statuses and active id are identical to the last render, to avoid
  // churning the DOM and dropping row focus. A genuine change always yields a
  // different signature, so no update is ever missed.
  let lastStatusSig = "";
  function applyStatuses(statuses, activeId, elsewhere) {
    const next = statuses || {};
    sessionStatuses = next;
    if (activeId !== undefined) lastActiveId = activeId;
    if (Array.isArray(elsewhere)) elsewhereIds = elsewhere;
    const sig = Object.keys(next).sort().map((k) => k + ":" + next[k]).join(",") + "|" + (lastActiveId || "") + "|" + elsewhereIds.join(",");
    if (sig === lastStatusSig) return;
    lastStatusSig = sig;
    if (listCtrl) listCtrl.refresh();
    if (menuCtrl) menuCtrl.refresh();
    if (panelCtrl && sessionsPanelOpen) panelCtrl.refresh();
    updateTerminateBtn();
  }

  // A session is held by another live Devin process (item 5): offer take-over.
  function showLockConflict(m) {
    const box = cwShell();
    cwTitle(box, "Session is open elsewhere");
    const body = cwBody(box);
    const msg = document.createElement("div");
    msg.className = "cw-message";
    msg.textContent = "This session is currently running in another Devin process" +
      (m.pid ? " (PID " + m.pid + ")" : "") +
      ". You can take it over here, which may disrupt the other process, or cancel and close it there first.";
    body.appendChild(msg);
    const row = cwButtons(box);
    const done = (decision) => { box.remove(); vscode.postMessage({ type: "takeoverDecision", requestId: m.requestId, decision }); };
    row.appendChild(btn("Take over", "primary", () => done("takeover")));
    row.appendChild(btn("Cancel", "secondary", () => done("cancel")));
    el.permissionTray.appendChild(box);
  }

  function sessionRow(s, activeId) {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === activeId ? " active" : "");
    const main = document.createElement("div");
    main.className = "session-main";
    const title = document.createElement("div");
    title.className = "session-title";
    title.textContent = s.title || s.short_id || s.id;
    // Liveness dot: green = running, amber = waiting for you, gray = not running.
    // A chat on another surface keeps the status reported by the controller that
    // owns it, so a running editor chat is green here too.
    const away = elsewhereIds.includes(s.id);
    const st = sessionStatuses[s.id];
    const dot = document.createElement("span");
    dot.className = "session-dot " +
      (st === "running" ? "dot-running"
        : st === "attention" ? "dot-attention"
        : st === "starting" ? "dot-starting"
        : st === "idle" || away ? "dot-idle"
        : "dot-dead");
    dot.title = st === "running" ? "Running"
      : st === "attention" ? "Needs your input"
      : st === "starting" ? "Waking\u2026"
      : away ? "Running in " + (inEditor() ? "the side panel" : "an editor tab")
      : st === "idle" ? "Alive, waiting for you"
      : "Not running";
    title.insertBefore(dot, title.firstChild);
    if (st === "attention") item.classList.add("needs-attention");
    // Running on another surface: say so before it is clicked, since a chat runs
    // in one place at a time.
    if (away) {
      const badge = document.createElement("i");
      badge.className = "codicon codicon-link-external session-elsewhere";
      badge.title = "Open in " + (inEditor() ? "the side panel" : "an editor tab");
      title.appendChild(badge);
    }
    const meta = document.createElement("div");
    meta.className = "session-meta";
    const time = document.createElement("span");
    time.className = "session-time";
    // Timed from the activity stamp rather than the CLI's own wording, so every
    // re-listing brings the label up to date, including for a chat only this
    // window knows about yet.
    time.textContent = agoFrom(s.last_activity_at) || s.last_activity_ago || "";
    const code = document.createElement("span");
    code.className = "session-code";
    code.textContent = s.short_id || s.id;
    meta.appendChild(time);
    meta.appendChild(code);
    main.appendChild(title);
    main.appendChild(meta);
    main.addEventListener("click", () => {
      closeTitleMenu();
      switchToSession(s.id, s.title);
    });
    const actions = document.createElement("div");
    actions.className = "session-actions";
    // Terminate is only offered for a live session (kills its process, keeps
    // the conversation). Delete removes the conversation entirely.
    if (!away && isAliveStatus(sessionStatuses[s.id])) {
      const term = iconBtn(KILL_GLYPH, "Terminate (stop this session's process)", (e) => {
        e.stopPropagation();
        vscode.postMessage({ type: "terminateSession", id: s.id, title: s.title || s.id });
      });
      actions.appendChild(term);
    }
    const rename = iconBtn("codicon-edit", "Rename", (e) => { e.stopPropagation(); vscode.postMessage({ type: "renameSession", id: s.id, title: s.title || "" }); });
    const del = iconBtn("codicon-trash", "Delete", (e) => { e.stopPropagation(); vscode.postMessage({ type: "deleteSession", id: s.id, title: s.title || s.id }); });
    actions.appendChild(rename);
    actions.appendChild(del);
    item.appendChild(main);
    // The shortcut that opens this chat, in the slot the row's actions take over
    // on hover.
    const n = numberedIds.indexOf(s.id) + 1;
    if (n) {
      const shortcut = document.createElement("span");
      shortcut.className = "session-key";
      shortcut.textContent = sessionShortcut(n);
      shortcut.title = "Open with " + sessionShortcut(n);
      item.appendChild(shortcut);
    }
    item.appendChild(actions);
    return item;
  }

  function sessionShortcut(n) {
    return (navigator.platform || "").startsWith("Mac") ? "\u2318" + n : "Ctrl+" + n;
  }

  // --- Setup panel ---------------------------------------------------------

  function renderSetup(health) {
    setView("setup");
    el.setup.innerHTML = "";
    const h = document.createElement("h2");
    h.textContent = "Set up Devin";
    el.setup.appendChild(h);
    const cliOk = !!health.found;
    el.setup.appendChild(stepBlock(
      cliOk ? "Devin CLI found" : "Devin CLI not found",
      cliOk ? `${health.path || ""}${health.version ? " (" + health.version + ")" : ""}` : "Set the path to the devin executable, or install the Devin CLI first.",
      cliOk,
      [btn("Browse...", "secondary", () => vscode.postMessage({ type: "browseCli" })), btn("Re-check", "secondary", () => vscode.postMessage({ type: "recheck" }))]
    ));
    if (cliOk) {
      const authed = health.loggedIn !== false;
      el.setup.appendChild(stepBlock(
        authed ? "Authenticated" : "Not logged in",
        authed ? "You are logged in to Devin." : "Log in to Devin, then re-check.",
        authed,
        authed ? [] : [btn("Log in", "primary", () => vscode.postMessage({ type: "authenticate" })), btn("Re-check", "secondary", () => vscode.postMessage({ type: "recheck" }))]
      ));
    }
    if (cliOk && health.loggedIn !== false) {
      el.setup.appendChild(btn("Start chatting", "primary", () => vscode.postMessage({ type: "finishSetup" })));
    }
    if (health.error && !cliOk) {
      const p = document.createElement("p");
      p.className = "setup-error";
      p.textContent = health.error;
      el.setup.appendChild(p);
    }
  }

  function stepBlock(title, desc, ok, controls) {
    const box = document.createElement("div");
    box.className = "setup-step";
    const t = document.createElement("div");
    t.className = "setup-title";
    const icon = document.createElement("i");
    icon.className = "codicon " + (ok ? "codicon-pass-filled" : "codicon-error");
    const tt = document.createElement("span");
    tt.textContent = title;
    t.appendChild(icon);
    t.appendChild(tt);
    box.appendChild(t);
    if (desc) { const d = document.createElement("div"); d.className = "setup-desc"; d.textContent = desc; box.appendChild(d); }
    if (controls && controls.length) { const row = document.createElement("div"); row.className = "setup-controls"; controls.forEach((c) => row.appendChild(c)); box.appendChild(row); }
    return box;
  }

  // --- Helpers -------------------------------------------------------------

  function btn(label, cls, onClick) {
    const b = document.createElement("button");
    b.className = "btn " + (cls || "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }
  // `icon` is a codicon class (e.g. "codicon-edit") or, when it starts with "<",
  // raw inline SVG/HTML (e.g. KILL_GLYPH).
  function iconBtn(icon, title, onClick) {
    const b = document.createElement("button");
    b.className = "icon-btn small";
    b.title = title;
    b.innerHTML = icon.charAt(0) === "<" ? icon : `<i class="codicon ${icon}"></i>`;
    b.addEventListener("click", onClick);
    return b;
  }
  function setBusy(value) {
    const wasBusy = busy;
    busy = value;
    updateComposerButtons();
    // Copilot-style animated indicator on the input (gated by progressBorder).
    el.inputBox.classList.toggle("busy", value && caps.progressBorder);
    if (!value) {
      // Stamp the just-finished turn's completion time (live turns only).
      if (wasBusy && currentTurn && !currentTurn.replayed && !currentTurn.completedAt) {
        currentTurn.completedAt = Date.now();
      }
      // A turn that ends on its work, with no closing word, still ends its run:
      // otherwise the last one of every turn is the only one left standing open.
      if (wasBusy) endToolRun();
      if (wasBusy) foldCompletedWork(currentTurn);
      // Move the live plan into the transcript as history and undock it.
      if (wasBusy) commitPlanSnapshot();
      finalizeSubagents();
    }
  }

  // Apply UI preference gates (progress border, context usage) when the host
  // sends capabilities. Timestamps (verbose) are gated in buildTurnChrome.
  function applyCapPrefs() {
    el.inputBox.classList.toggle("busy", busy && caps.progressBorder);
    if (!caps.contextUsage) {
      el.usage.classList.add("hidden");
      closeUsagePopup();
    }
    // Drive the streaming entrance animation from the setting.
    el.thread.dataset.anim = caps.streamAnim || "rise";
    applyPanelSide();
    updateDetachBtn();
    // The split button's primary half follows the default action setting.
    updateComposerButtons();
    // Turning the changed files summary off has to take it off the screen now,
    // not at the end of the next turn.
    renderWorkingSet(wsLast);
  }

  // --- Welcome / empty state ----------------------------------------------

  const STARTER_PROMPTS = [
    "Explain how this codebase is structured",
    "Find and fix a bug in the current file",
    "Write tests for the file I have open",
    "Review my recent changes"
  ];

  function renderWelcome() {
    if (el.thread.querySelector(".welcome")) return;
    if (el.thread.querySelector(".turn")) return;
    const box = document.createElement("div");
    box.className = "welcome";
    const logoSrc = document.body.dataset.logo;
    let icon;
    if (logoSrc) {
      icon = document.createElement("img");
      icon.className = "welcome-logo";
      icon.src = logoSrc;
      icon.alt = "Devin";
    } else {
      icon = document.createElement("i");
      icon.className = "codicon codicon-comment-discussion welcome-icon";
    }
    const title = document.createElement("div");
    title.className = "welcome-title";
    title.textContent = "Ask Devin anything";
    const sub = document.createElement("div");
    sub.className = "welcome-sub muted";
    sub.textContent = "Devin can read your workspace, run tools, and edit files. Try one of these:";
    box.appendChild(icon);
    box.appendChild(title);
    box.appendChild(sub);
    const chips = document.createElement("div");
    chips.className = "welcome-prompts";
    STARTER_PROMPTS.forEach((p) => {
      const chip = document.createElement("button");
      chip.className = "welcome-chip";
      chip.textContent = p;
      chip.addEventListener("click", () => { el.input.value = p; el.input.focus(); autosize(); send(); });
      chips.appendChild(chip);
    });
    box.appendChild(chips);
    // The only place the Shift gesture can be taught. VS Code makes a webview
    // inert for the whole of a drag that starts inside it, so a drag from the
    // Explorer reaches us only while Shift is held, and until it does we get no
    // event to hint from.
    const hint = document.createElement("div");
    hint.className = "welcome-hint muted";
    hint.innerHTML =
      '<i class="codicon codicon-attach"></i> Drag files and folders in to attach them, ' +
      "holding <kbd>Shift</kbd> when the drag starts inside VS Code.";
    box.appendChild(hint);
    el.thread.appendChild(box);
  }

  function hideWelcome() {
    const w = el.thread.querySelector(".welcome");
    if (w) w.remove();
  }

  function showThreadLoading(waking) {
    el.thread.innerHTML = "";
    // Freeze auto-scroll and hide the streaming transcript behind the spinner
    // until the replay settles (see `loadingSession`).
    loadingSession = true;
    el.thread.classList.add("loading-replay");
    showLoadingBar();
    const d = document.createElement("div");
    d.className = "thread-loading";
    d.innerHTML = "<span></span>";
    d.querySelector("span").textContent = waking ? "Waking session\u2026" : "Loading session\u2026";
    el.thread.appendChild(d);
  }

  // Reveal the transcript and re-enable auto-scroll after a load settles (or is
  // abandoned). Safe to call when no load is in progress.
  function stopThreadLoading() {
    loadingSession = false;
    el.thread.classList.remove("loading-replay");
    // The spinner row goes when the load ends, and not before. Every renderer
    // calls hideWelcome, so removing it there took the spinner away with the
    // first replayed chunk while the transcript behind it was still hidden,
    // leaving the panel blank for the rest of the replay.
    const l = el.thread.querySelector(".thread-loading");
    if (l) l.remove();
    hideLoadingBar();
  }

  function threadHasContent() {
    return !!el.thread.querySelector(".turn, .tool, .edit-pill, .plan, .thinking");
  }

  // --- Error rendering -----------------------------------------------------

  function renderError(text) {
    // An error ends the in-flight turn. Clear busy defensively so a failed
    // send/new-session (where the host may not post busy=false) never leaves the
    // composer stuck on Stop.
    setBusy(false);
    hideWorking();
    finalizeBlock();
    hideWelcome();
    announce("Devin reported an error: " + text);
    const box = document.createElement("div");
    box.className = "tray-card error-card";
    const head = document.createElement("div");
    head.className = "error-head";
    const icon = document.createElement("i");
    icon.className = "codicon codicon-error";
    const msg = document.createElement("span");
    const low = (text || "").toLowerCase();
    const loggedOut = /not logged in|log ?in|authenticat|unauthori[sz]ed|401/.test(low);
    const rateLimited = /rate limit|429|too many requests|quota/.test(low);
    msg.textContent = loggedOut
      ? "You're not logged in to Devin."
      : rateLimited
        ? "Devin is rate limited right now."
        : (text || "Something went wrong.");
    head.appendChild(icon);
    head.appendChild(msg);
    box.appendChild(head);
    const row = document.createElement("div");
    row.className = "options";
    if (loggedOut) {
      row.appendChild(btn("Log in", "primary", () => vscode.postMessage({ type: "authenticate" })));
      row.appendChild(btn("Re-check", "secondary", () => vscode.postMessage({ type: "recheck" })));
    } else {
      row.appendChild(btn("Retry", "primary", () => { if (lastUserText) vscode.postMessage({ type: "send", text: lastUserText, newSession: false }); }));
    }
    box.appendChild(row);
    respTarget().appendChild(box);
    scrollToBottom();
  }

  // The window reloaded (or the extension restarted) while this session had a
  // turn in flight. The agent runs its commands and file edits through the
  // extension host, so it cannot survive that: say so plainly, and offer to send
  // the same message again.
  function renderInterrupted() {
    setBusy(false);
    hideWorking();
    finalizeBlock();
    hideWelcome();
    const box = document.createElement("div");
    box.className = "tray-card interrupted-card";
    const head = document.createElement("div");
    head.className = "error-head";
    const icon = document.createElement("i");
    icon.className = "codicon codicon-debug-disconnect";
    const msg = document.createElement("span");
    msg.textContent = "The last turn stopped when the window reloaded. Your files and the rest of this conversation are untouched.";
    head.appendChild(icon);
    head.appendChild(msg);
    box.appendChild(head);
    if (lastUserText) {
      const row = document.createElement("div");
      row.className = "options";
      row.appendChild(btn("Send it again", "primary", () => {
        box.remove();
        vscode.postMessage({ type: "send", text: lastUserText, newSession: false });
      }));
      box.appendChild(row);
    }
    respTarget().appendChild(box);
    scrollToBottom();
  }

  // Short local time (HH:MM) for turn timestamps.
  // How long a turn took, as the CLI measured it.
  function fmtDuration(ms) {
    const s = ms / 1000;
    if (s < 60) return (s < 10 ? s.toFixed(1) : Math.round(s)) + "s";
    const m = Math.floor(s / 60);
    return m + "m " + Math.round(s - m * 60) + "s";
  }

  function fmtTime(ts) {
    try { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch { return ""; }
  }

  // Relative age of a millisecond timestamp ("2m ago"), for the timestamp flip.
  function agoMs(ms) {
    const d = Math.max(0, (Date.now() - ms) / 1000);
    if (d < 60) return "just now";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }

  // A timestamp that flips relative <-> absolute on hover, mirroring VS Code's
  // chat-response/request-timing micro-interaction. `prefix` e.g. "Sent ".
  function timeFlip(prefix, ts) {
    const wrap = document.createElement("span");
    wrap.className = "time-flip";
    const primary = document.createElement("span");
    primary.className = "time-primary";
    primary.textContent = prefix + agoMs(ts);
    const alt = document.createElement("span");
    alt.className = "time-alt";
    alt.textContent = prefix + fmtTime(ts);
    wrap.appendChild(primary);
    wrap.appendChild(alt);
    return wrap;
  }

  // --- Usage / cost --------------------------------------------------------

  function fmtTokens(n) {
    if (!n && n !== 0) return "";
    if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k";
    return String(n);
  }

  let lastUsage = null;

  function fmtCost(cost) {
    if (!cost || typeof cost.amount !== "number") return "";
    return (cost.amount < 1 ? "$" + cost.amount.toFixed(3) : "$" + cost.amount.toFixed(2));
  }

  function ringSvg(pct) {
    const r = 7, c = 2 * Math.PI * r;
    const off = c * (1 - Math.min(100, Math.max(0, pct)) / 100);
    // Arc colour matches VS Code's context-usage widget: normal icon colour,
    // amber past 75%, red past 90%; track is the dimmed disabled foreground.
    const arc = pct >= 90
      ? "var(--vscode-editorError-foreground, #f14c4c)"
      : pct >= 75
        ? "var(--vscode-editorWarning-foreground, #cca700)"
        : "var(--vscode-icon-foreground, var(--vscode-foreground))";
    return (
      `<svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">` +
      `<circle cx="9" cy="9" r="7" fill="none" stroke="var(--vscode-disabledForeground, var(--vscode-panel-border))" stroke-width="2.5" opacity="0.5"/>` +
      `<circle cx="9" cy="9" r="7" fill="none" stroke="${arc}" stroke-width="2.5" stroke-linecap="round"` +
      ` stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}" transform="rotate(-90 9 9)"/></svg>`
    );
  }

  function renderUsage(m) {
    if (!caps.contextUsage) { el.usage.classList.add("hidden"); lastUsage = null; return; }
    if (!m.used || !m.size) { el.usage.classList.add("hidden"); lastUsage = null; closeUsagePopup(); return; }
    lastUsage = m;
    const pct = Math.round((m.used / m.size) * 100);
    el.usage.innerHTML = ringSvg(pct) + `<span class="usage-pct">${pct}%</span>`;
    el.usage.title = "Context used, click for details";
    el.usage.classList.remove("hidden");
    refreshUsagePopup();
  }

  function closeUsagePopup() {
    const p = document.getElementById("usage-popup");
    if (p) p.remove();
  }

  function refreshUsagePopup() {
    const pop = document.getElementById("usage-popup");
    if (pop && lastUsage) pop.innerHTML = usagePopupHtml(lastUsage);
  }

  function usagePopupHtml(m) {
    const pct = Math.round((m.used / m.size) * 100);
    const cost = fmtCost(m.cost);
    return (
      `<div class="usage-row"><span>Context window</span><span>${pct}%</span></div>` +
      `<div class="usage-bar"><div style="width:${Math.min(100, pct)}%"></div></div>` +
      `<div class="usage-row muted"><span>Tokens</span><span>${fmtTokens(m.used)} / ${fmtTokens(m.size)}</span></div>` +
      (cost ? `<div class="usage-row muted"><span>Cost this turn</span><span>${cost}</span></div>` : "")
    );
  }

  el.usage.addEventListener("click", (e) => {
    e.stopPropagation();
    if (document.getElementById("usage-popup")) { closeUsagePopup(); return; }
    if (!lastUsage) return;
    const pop = document.createElement("div");
    pop.id = "usage-popup";
    pop.className = "usage-popup";
    pop.innerHTML = usagePopupHtml(lastUsage);
    pop.addEventListener("click", (ev) => ev.stopPropagation());
    el.usage.parentElement.appendChild(pop);
  });

  // Live terminal output streamed from the extension host.
  function updateTerminal(m) {
    if (!m.terminalId) return;
    let text = m.output || "";
    if (m.exitStatus) {
      const code = m.exitStatus.exitCode;
      const sig = m.exitStatus.signal;
      text += `\n[exited ${sig ? "signal " + sig : "code " + (code == null ? "?" : code)}]`;
    }
    const state = {
      output: text,
      exitStatus: m.exitStatus,
      integrated: !!m.integrated,
      skipped: !!m.skipped,
      revealed: !!m.revealed
    };
    const before = terminalCache.get(m.terminalId);
    if (before) terminalCache.delete(m.terminalId);
    terminalCache.set(m.terminalId, state);
    if (terminalCache.size > TERMINAL_CACHE_MAX) {
      for (const [terminalId, cached] of terminalCache) {
        if (cached.exitStatus) {
          terminalCache.delete(terminalId);
          break;
        }
      }
    }
    let wrote = false;
    el.thread.querySelectorAll(`pre[data-terminal="${cssEscape(m.terminalId)}"]`).forEach((pre) => {
      // Write into the box that is already there, and keep it pinned to the newest
      // line unless the user has scrolled up to read something. Rebuilding it reset
      // the scroll, so a long command only ever showed its first few lines.
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
      pre.textContent = text || "\u2026";
      if (atBottom) pre.scrollTop = pre.scrollHeight;
      wrote = true;
      scrollToBottom();
    });
    // The row is titled and actioned by the state of its command, so redraw the
    // rows bound to this terminal: what it says ("in background") and what it
    // offers (backgrounding it, showing it) both follow from this. Only when one of
    // those actually changed, though: doing it for every chunk of output rebuilt the
    // whole card, re-highlighted the command and re-decoded any image in it, which
    // is what made the panel stall during a build.
    const chrome = (s) => s && [!!s.exitStatus, s.integrated, s.skipped, s.revealed].join("|");
    if (wrote && chrome(before) === chrome(state)) {
      return;
    }
    const bound = [];
    toolEls.forEach((entry, id) => {
      if (entry.data && entry.data.terminalId === m.terminalId) bound.push(id);
    });
    bound.forEach((id) => upsertTool({ id }));
  }
  function shorten(p) { return p.split(/[\\/]/).slice(-2).join("/"); }
  function baseName(p) { return p.split(/[\\/]/).filter(Boolean).pop() || p; }
  function agoFrom(ts) {
    if (!ts) return "";
    const d = Math.max(0, Date.now() / 1000 - ts);
    if (d < 60) return "just now";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) return Math.floor(d / 3600) + "h ago";
    return Math.floor(d / 86400) + "d ago";
  }

  // --- Inbound messages ----------------------------------------------------

  window.addEventListener("message", (event) => {
    const m = event.data;
    try {
      handleMessage(m);
      // Whatever just happened may have left the turn with nothing to show, which
      // is exactly when the panel has to say the agent is still working.
      refreshWorking();
    } catch (err) {
      // A bug in one handler must never wedge the whole UI (e.g. leaving the
      // sessions list stuck on its loading spinner). Surface it and recover.
      try { console.error("[devin] message handler error", m && m.type, err); } catch {}
      try { vscode.postMessage({ type: "webviewError", where: m && m.type, message: String(err && err.stack || err) }); } catch {}
      // If the sessions list was mid-load, clear the spinner so it is usable.
      if (m && (m.type === "sessions" || m.type === "sessionsLoading") && el.sessionsList) {
        try { renderSessions(lastSessions, lastActiveId, lastFolders); } catch { el.sessionsList.innerHTML = '<div class="sessions-empty-sm">Could not load sessions.</div>'; }
      }
    }
  });

  function handleMessage(m) {
    switch (m.type) {
      case "setup": hideBoot(); renderSetup(m.health || {}); break;
      // Readiness can be re-announced (a health recheck, a surface being handed a
      // session), so it must not throw away a thread that is already on screen.
      // Readiness is announced more than once (the cached fast path, then the real
      // health check) and can land while a chat is being painted into this
      // surface, so it must never send a thread back to the list.
      case "ready": hideBoot(); setView("chat"); if (body !== "thread") setBody("list"); break;
      case "replay":
        for (const item of m.items || []) handleMessage(item);
        break;
      case "body":
        setView("chat");
        if (m.body === "list") {
          // Host-driven return to the list (e.g. after terminate). Cache the
          // current transcript and drop curSessionId so reselecting the same
          // session is not short-circuited and can restore + wake seamlessly.
          snapshotCurrent();
          curSessionId = null;
          pendingModelChange = 0;
          setBody("list");
        } else {
          setBody("thread");
        }
        break;
      case "workspace": break;
      case "options": {
        modeDropdown.set((m.modes || []).map((mode) => mode.value === "bypass" ? { ...mode, name: "Bypass" } : mode), m.currentMode);
        // Model groups are merged rather than replaced: the server reports the
        // options of the active model, so a normal model's response can omit the
        // Fusion group entirely, and losing it would empty the Fusion picker.
        const incoming = m.modelChoices || {};
        for (const key of Object.keys(incoming)) modelChoices[key] = incoming[key];
        // Families come from this response, because modelFamilies is still
        // empty on the first one.
        const famOf = (uid) => (m.models || []).find((f) => (f.variants || []).some((v) => v.value === uid))
          || (String(uid || "").startsWith("fusion-") ? (m.models || []).find(isFusion) : undefined);
        const responseFamily = famOf(m.currentModel);
        const shownFamily = famOf(currentModelUid) || responseFamily;
        if (responseFamily) {
          const key = effortKey(responseFamily, m.currentModel);
          if (key) thoughtLevelsByFamily[key] = Array.isArray(m.thoughtLevels) ? m.thoughtLevels : [];
          // Only the family the response is about can report its effort, and an
          // empty string is the host clearing a model that has none.
          if (!pendingModelChange && effortKey(responseFamily, m.currentModel) === effortKey(shownFamily, currentModelUid || m.currentModel) && typeof m.currentThoughtLevel === "string") currentThoughtLevel = m.currentThoughtLevel;
        }
        // The user may have already picked a different model before this async
        // options response arrived. Keep their local selection.
        applyModelOptions(m.models, currentModelUid || m.currentModel);
        break;
      }
      case "commands": commands = Array.isArray(m.commands) ? m.commands : []; break;
      case "fileSuggestions":
        if (m.query === fileQueryToken) {
          openAutocomplete((m.items || []).map((f) => ({ kind: "file", path: f.path, label: f.label, detail: f.detail })));
        }
        break;
      case "sessions":
        hideBoot();
        hideLoadingBar();
        if (m.statuses) sessionStatuses = m.statuses;
        if (Array.isArray(m.elsewhere)) elsewhereIds = m.elsewhere;
        renderSessions(m.sessions, m.activeId, m.folders);
        updateTerminateBtn();
        break;
      case "sessionStatuses": applyStatuses(m.statuses, m.activeId, m.elsewhere); break;
      case "sessionActivity": if (m.id && views.has(m.id)) dirtyViews.add(m.id); break;
      case "openSession":
        // Host-initiated open (e.g. the "needs your input" notification). Reuse
        // the same path as a click so view restore / wake / load all apply.
        if (m.id) {
          setView("chat");
          const s = (lastSessions || []).find((x) => x.id === m.id);
          switchToSession(m.id, s && s.title);
        }
        break;
      case "pickSession": {
        // Ctrl/Cmd+1..9: the number is the one shown on the row, so it resolves
        // against the same recency order the badges come from.
        const id = numberedIds[(m.index || 0) - 1];
        const s = id && (lastSessions || []).find((x) => x.id === id);
        if (s) { setView("chat"); switchToSession(s.id, s.title); }
        break;
      }
      case "lockConflict": showLockConflict(m); break;
      case "sessionReady":
        if (m.title) { currentTitle = m.title; el.chatTitle.textContent = currentTitle; }
        // The thread now shows this session; retire any retained snapshot for it.
        if (m.sessionId && curSessionId !== m.sessionId) { cancelPreviewWaiters(); if (curSessionId) pendingModelChange = 0; }
        if (m.sessionId) { curSessionId = m.sessionId; views.delete(m.sessionId); dirtyViews.delete(m.sessionId); }
        // Remembered so an editor tab restored after a window reload comes back to
        // the chat it was holding instead of an empty tab.
        vscode.setState({ ...(vscode.getState() || {}), sessionId: curSessionId, pinnedModels: [...pinnedModelIds] });
        // Refresh the header so the title and code badge reflect the session now
        // shown (e.g. after starting a new session).
        renderHeader();
        updateTerminateBtn();
        break;
      case "clear":
        clearElsewhere();
        if (m.reset || m.loading) pendingModelChange = 0;
        // The warning belongs to the agent behind this chat, so it does not carry
        // over to the next one, dismissed or not.
        renderMcpProblems([]);
        mcpDismissed.clear();
        workingEl = null;
        // Any prior load is over: drop the replay freeze before this clear
        // rebuilds the thread (a new clear{loading} re-arms it just below).
        stopThreadLoading();
        // A fresh session resets the header title and code badge instead of
        // keeping the previous session's.
        if (m.reset) {
          currentTitle = "Chat";
          curSessionId = null;
          // Nothing has been said in this chat yet, so there is nothing for Up to
          // recall and nothing for Retry to resend. Keeping the last chat's message
          // meant a brand new chat offered it, and Retry would have sent it here.
          lastUserText = "";
          closeTitleMenu();
          renderHeader();
          updateTerminateBtn();
        }
        // A freshly cleared thread starts pinned at the bottom.
        stickToBottom = true;
        // Drop any in-progress request edit so its banner/target don't dangle
        // over the freshly cleared thread.
        cancelInputEditing();
        el.thread.innerHTML = "";
        turns = [];
        currentTurn = null;
        lastHead = null;
        lastHeadReliable = false;
        pendingRevert = null;
        cancelPreviewWaiters();
        // Not the two trays by hand: the permission scope menu is anchored to a
        // button in one of them but lives on the body, so wiping the tray left it
        // floating over the new thread, still answering for a request that had
        // gone, and left the next prompt's chevron dead on its first click.
        cancelPrompts();
        planCollapsePref = null;
    wsCollapsePref = null;
        hideDockedPlan();
        wsCounts.clear();
        renderWorkingSet([]);
        renderQueued([]);
        renderAttachments([]);
        toolEls.clear();
        subagentEls.clear();
        if (block && block.timer) clearInterval(block.timer);
        block = null;
        el.usage.classList.add("hidden");
        el.usage.innerHTML = "";
        lastUsage = null;
        closeUsagePopup();
        // `pendingSend` means a user message is about to render (new chat from
        // the list), so do not flash the welcome screen in the gap.
        if (m.loading) showThreadLoading(m.waking);
        else if (body === "thread" && !m.pendingSend) renderWelcome();
        break;
      case "loaded":
        // Settle the last replayed block so its mermaid diagrams render and no
        // block stays open to catch a later stray chunk. A replayed background
        // subagent has no report to close it (the CLI does not keep one), so it
        // has to be settled here too.
        finalizeBlock();
        finalizeSubagents(true);
        // What was replayed is history, and history arrives folded: every run of
        // it was finished long before the window was reopened.
        foldReplayedSections();
        { const l = el.thread.querySelector(".thread-loading"); if (l) l.remove(); }
        // Reveal the transcript now the replay has settled, before we scroll.
        stopThreadLoading();
        if (body === "thread" && !threadHasContent()) renderWelcome();
        // A freshly loaded transcript starts pinned at the bottom.
        forceScrollToBottom();
        break;
      case "sessionsLoading":
        // Keep whatever is already listed on screen and just run the top loading
        // bar, so returning to the list never blanks it while it revalidates. The
        // list keeps itself up to date in the background, and that must not flash a
        // loading bar over a chat someone is reading.
        if (listShown) showLoadingBar();
        if (!lastSessions.length) {
          el.sessionsList.innerHTML = "";
          listCtrl = null;
        }
        break;
      case "userMessage":
        if (currentTitle === "Chat") { currentTitle = m.text.slice(0, 40); el.chatTitle.textContent = currentTitle; }
        addUserMessage(m.text, m.attachments);
        break;
      case "userChunk": appendUserChunk(m.text, m.messageId, m.attachments); break;
      case "assistantStart": finalizeBlock(); announce("Devin is working."); break;
      case "assistantChunk": appendAssistant(m.text, m.messageId); break;
      case "assistantImage": appendAssistantImage(m.mime, m.data); break;
      case "thoughtChunk": appendThought(m.text, m.messageId, m.replayed, m.at); break;
      case "assistantEnd":
        hideWorking();
        finalizeBlock();
        // "cancelled" is the one a user needs to hear: it means their stop landed.
        announce(m.stopReason === "cancelled" ? "Devin stopped." : "Devin finished replying.");
        break;
      case "plan": renderPlan(m.entries); break;
      case "toolCall":
      case "toolCallUpdate": upsertTool(m); break;
      case "subagentStart": startSubagent(m); break;
      case "subagentChunk": appendSubagentChunk(m); break;
      case "subagentEnd": endSubagent(m); break;
      case "subagentMode": setSubagentBackground(m); break;
      case "fileChange": addFileChange(m); break;
      case "workingSet": renderWorkingSet(m.files); break;
      case "queued": renderQueued(m.items); break;
      case "attachments": renderAttachments(m.items); break;
      case "draft": applyDraft(m); break;
      // Something outside the panel (an editor command) set the composer up and
      // wants the user in it, ready to send.
      case "focusInput": el.input.focus(); break;
      case "changesResolved": applyResolved(m.paths, m.action); break;
      case "mcpProblems": renderMcpProblems(m.servers); break;
      case "turnStats": applyTurnStats(m); break;
      case "elsewhere": hideBoot(); renderElsewhere(m); break;
      case "sessionEnded": renderSessionEnded(); break;
      case "flushState": flushState(); break;
      case "implicitContext":
        implicit = m.file ? { path: m.file.path, name: m.file.name, line1: m.file.line1, line2: m.file.line2, enabled: m.enabled !== false } : null;
        renderComposerContext();
        break;
      case "permission": showPermission(m); break;
      case "elicitation": showElicitation(m); break;
      // Every turn's edit/restore chrome depends on the busy state
      // (canEditTurn/canRestoreTurn gate on !busy), so rebuild them all.
      case "busy": setBusy(m.value); refreshTurnChrome(); break;
      case "cancelPrompts": cancelPrompts(); break;
      case "mode": if (m.mode) modeDropdown.setCurrent(m.mode); break;
      case "model": if (m.model && !pendingModelChange) selectModelUid(m.model); break;
      case "modelConfigApplied":
        if (m.requestId !== pendingModelChange || !pendingModelChange) break;
        pendingModelChange = 0;
        if (typeof m.thoughtLevel === "string") currentThoughtLevel = m.thoughtLevel;
        if (m.model) selectModelUid(m.model);
        break;
      case "terminalOutput": updateTerminal(m); break;
      case "usage": renderUsage(m); break;
      // A failed revert reports `error` rather than `reverted`, so abandon any
      // pending revert here to avoid a stale head trimming the wrong turn later.
      case "error": hideBoot(); pendingRevert = null; renderError(m.text); break;
      case "interrupted": hideBoot(); renderInterrupted(); break;
      case "capabilities":
        caps = Object.assign(caps, {
          root: m.root || caps.root,
          revert: !!m.revert,
          subagentControl: !!m.subagentControl,
          sessionShare: !!m.sessionShare,
          editRequests: m.editRequests || caps.editRequests,
          checkpoints: m.checkpoints !== undefined ? !!m.checkpoints : caps.checkpoints,
          showFileChanges: m.showFileChanges !== undefined ? !!m.showFileChanges : caps.showFileChanges,
          confirmRemoval: m.confirmRemoval !== undefined ? !!m.confirmRemoval : caps.confirmRemoval,
          verbose: m.verbose !== undefined ? !!m.verbose : caps.verbose,
          progressBorder: m.progressBorder !== undefined ? !!m.progressBorder : caps.progressBorder,
          contextUsage: m.contextUsage !== undefined ? !!m.contextUsage : caps.contextUsage,
          inlineReferencesStyle: m.inlineReferencesStyle || caps.inlineReferencesStyle,
          sendWhileWorking: m.sendWhileWorking || caps.sendWhileWorking,
          thinkingStyle: m.thinkingStyle || caps.thinkingStyle,
          streamAnim: m.streamAnim || caps.streamAnim,
          panelSide: m.panelSide || caps.panelSide,
          surface: m.surface || caps.surface
        });
        applyCapPrefs();
        document.querySelectorAll(".resp-text, .subagent-prose-content, .subagent-section-body, .thinking-item-content")
          .forEach(enhanceAnchors);
        refreshTurnChrome();
        break;
      case "turnHead":
        if (typeof m.head === "number") {
          lastHead = m.head;
          // `reliable` heads are captured on the current expansion (a live turn
          // completion or an instant restore) and are safe revert targets. The
          // head read right after a reload is NOT reliable: the next prompt
          // re-expands the conversation and orphans it.
          lastHeadReliable = !!m.reliable;
          if (currentTurn) {
            currentTurn.headAfter = m.head;
            currentTurn.headAfterReliable = lastHeadReliable;
          }
        }
        break;

      case "reverted": {
        // The host has performed the rewind; now it is safe to trim the turns
        // from the reverted point onward (and drop the "restored" divider for a
        // checkpoint restore). A resend, if any, streams in as new turns after.
        const head = typeof m.head === "number" ? m.head : (pendingRevert && pendingRevert.head);
        const turn = turns.find((t) => t.headBefore === head);
        if (turn) trimTurnsFrom(turn);
        if (pendingRevert && pendingRevert.showRestored) renderRestoredRow();
        pendingRevert = null;
        break;
      }
      case "revertPreview": {
        const w = previewWaiters.get(m.token);
        if (w) w(m);
        break;
      }
      default: break;
    }
  }

  vscode.postMessage({ type: "ready" });
})();
