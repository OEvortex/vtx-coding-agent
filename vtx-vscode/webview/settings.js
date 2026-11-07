// Devin settings surface. Renders the Devin CLI config surface (general,
// instructions, skills, MCP, hooks, permissions, advanced) from data supplied by
// the extension host, and posts edits back.
//
// Two rules shape this file:
//   1. Scope (Global / a workspace folder) is picked once in the toolbar, not
//      repeated down the page. One page shows one scope. Rows carry "set here"
//      and "overridden" markers so choosing one scope hides no information.
//   2. This surface only ever edits Devin CLI config. Settings that control the
//      extension itself live in VS Code settings, and are linked to from here.
//
// All untrusted values are written via textContent, never innerHTML.
(function () {
  const vscode = acquireVsCodeApi();
  const $ = (id) => document.getElementById(id);
  const nav = $("settings-nav");
  const content = $("settings-content");
  const loading = $("settings-loading");
  const toolbar = $("settings-toolbar");
  const main = $("settings-main");

  // `global: true` marks a section the Devin CLI manages once for the machine, so
  // the scope picker is hidden there rather than offering a choice with no effect.
  const SECTIONS = [
    { id: "general", label: "General", icon: "settings-gear" },
    { id: "instructions", label: "Instructions", icon: "book" },
    { id: "skills", label: "Skills", icon: "lightbulb" },
    { id: "plugins", label: "Plugins", icon: "extensions", global: true },
    { id: "mcp", label: "MCP Servers", icon: "server-environment" },
    { id: "hooks", label: "Hooks", icon: "plug" },
    { id: "permissions", label: "Permissions", icon: "shield" },
    { id: "advanced", label: "Advanced", icon: "tools" }
  ];
  const sectionOf = (id) => SECTIONS.find((s) => s.id === id) || SECTIONS[0];

  let data = null;
  let active = "general";
  // The active scope: "user", or a workspace folder path.
  let scope = "user";
  let query = "";

  // --- Tiny DOM helpers ----------------------------------------------------
  function h(tag, props, children) {
    const e = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (k === "class") e.className = v;
        else if (k === "text") e.textContent = v;
        else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
        else if (v !== undefined && v !== null) e.setAttribute(k, v);
      }
    }
    for (const c of [].concat(children || [])) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  function icon(name) { return h("i", { class: "codicon codicon-" + name }); }

  // Messages that make the host do work and answer when it is finished, either
  // with fresh data or with `settings:idle` when nothing changed (a confirmation
  // was declined). Everything else is a read or a navigation.
  const MUTATING = new Set([
    "settings:setPath", "settings:resetSection", "settings:permission", "settings:addHook",
    "settings:removeHook", "settings:createSkill", "settings:createFile", "settings:deletePath",
    "settings:mcpAdd", "settings:mcpVerb", "settings:mcpLogin", "settings:pluginVerb",
    "settings:clearExtensionModel"
  ]);
  // Counts work handed to the host, so a control can tell whether the click it
  // just handled actually started something.
  let mutations = 0;

  function post(type, extra) {
    if (MUTATING.has(type)) mutations++;
    vscode.postMessage(Object.assign({ type }, extra || {}));
  }

  // --- Busy state ----------------------------------------------------------
  // Every write goes to the host and comes back as a fresh read, which can take
  // a moment (an MCP or plugin verb shells out to the CLI). The control that
  // started it shows that it is running and stops accepting input, so nothing
  // looks dead and the same write cannot be fired twice.
  const busyEls = new Map(); // element -> how to restore it
  let busyTimer = null;

  function markBusy(el) {
    if (!el || busyEls.has(el)) return;
    if (el.tagName === "BUTTON") {
      const ic = el.querySelector(".codicon");
      const before = ic ? ic.className : null;
      if (ic) {
        ic.className = "codicon codicon-loading codicon-modifier-spin";
      } else {
        el.prepend(icon("loading modifier-spin"));
      }
      busyEls.set(el, () => {
        if (before !== null && ic) ic.className = before;
        else el.querySelector(".codicon")?.remove();
      });
    } else {
      // A toggle's tick box is the input, but the visible control is its label.
      const visible = el.type === "checkbox" ? el.closest(".settings-toggle") || el : el;
      visible.classList.add("busy");
      busyEls.set(el, () => visible.classList.remove("busy"));
    }
    el.disabled = true;
    el.classList.add("busy");
    el.setAttribute("aria-busy", "true");
    // Insurance: never leave a control stuck if an answer somehow never lands.
    if (busyTimer) clearTimeout(busyTimer);
    busyTimer = setTimeout(clearBusy, 15000);
  }

  function clearBusy() {
    if (busyTimer) {
      clearTimeout(busyTimer);
      busyTimer = null;
    }
    for (const [el, restore] of busyEls) {
      restore();
      el.disabled = false;
      el.classList.remove("busy");
      el.removeAttribute("aria-busy");
    }
    busyEls.clear();
  }

  // Wraps a control's handler: if handling it actually gave the host work, the
  // control shows it running. The answer re-renders, which replaces these
  // elements outright, so nothing has to be undone by hand.
  function whileBusy(el, handler) {
    return (...args) => {
      const before = mutations;
      handler(...args);
      if (mutations > before) markBusy(el);
    };
  }

  function toggle(checked, onChange) {
    const input = h("input", { type: "checkbox" });
    input.checked = !!checked;
    input.addEventListener("change", whileBusy(input, () => onChange(input.checked)));
    return h("label", { class: "settings-toggle" }, [input, h("span", { class: "settings-toggle-track" })]);
  }
  // A dropdown built on a real <select>, the way VS Code's own Settings editor
  // does it: native keyboard handling, native option grouping, and the platform
  // popup, none of which a custom menu gets for free (and a custom menu would be
  // clipped by the scrolling page anyway). The wrapper exists only to draw the
  // chevron over it, since a <select> cannot carry pseudo-element content.
  //
  // An option may carry a `group`, and consecutive options sharing one are put in
  // an <optgroup>, which is what gives the workspace folders their heading and
  // divider.
  function select(options, value, onChange) {
    const sel = h("select", { class: "settings-select" });
    let group = null;
    for (const o of options) {
      const opt = h("option", { value: o.value, text: o.label });
      opt.selected = o.value === value;
      if (o.group) {
        if (!group || group.label !== o.group) {
          group = h("optgroup", { label: o.group });
          sel.appendChild(group);
        }
        group.appendChild(opt);
      } else {
        group = null;
        sel.appendChild(opt);
      }
    }
    sel.addEventListener("change", whileBusy(sel, () => onChange(sel.value)));
    return h("span", { class: "settings-select-wrap" }, [sel, icon("chevron-down")]);
  }
  function textInput(value, placeholder, onCommit) {
    const inp = h("input", { class: "settings-input", type: "text", placeholder: placeholder || "" });
    inp.value = value || "";
    // Commit only a real change: blur fires even when nothing was edited, and
    // writing the same value back would cost a pointless round trip and flash the
    // field as busy just for clicking through it.
    let committed = inp.value.trim();
    const commit = whileBusy(inp, () => {
      const next = inp.value.trim();
      if (next === committed) return;
      committed = next;
      if (onCommit) onCommit(next);
    });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    inp.addEventListener("blur", commit);
    return inp;
  }
  // For values where newlines are the point (a hook's prompt), so Enter inserts
  // one instead of submitting.
  function textArea(value, placeholder, rows) {
    const ta = h("textarea", { class: "settings-textarea", rows: String(rows || 4), placeholder: placeholder || "" });
    ta.value = value || "";
    return ta;
  }
  function btn(label, iconName, onClick, cls) {
    const b = h("button", { class: "settings-btn " + (cls || "") }, [iconName ? icon(iconName) : null, label]);
    b.addEventListener("click", whileBusy(b, onClick));
    return b;
  }
  // A text link that acts like a button, for an action inside a sentence.
  function linkBtn(label, onClick) {
    const b = h("button", { class: "settings-link" }, [label]);
    b.addEventListener("click", whileBusy(b, onClick));
    return b;
  }
  // Icon-only action button (edit / remove / etc.). The name is its accessible
  // label and its hover tooltip, since there is no visible text to go on.
  function iconBtn(iconName, tip, onClick, cls) {
    const b = h("button", {
      class: "settings-icon-btn " + (cls || ""), "data-tip": tip, "aria-label": tip
    }, [icon(iconName)]);
    b.addEventListener("click", whileBusy(b, onClick));
    return b;
  }
  // --- Tooltips ------------------------------------------------------------
  // VS Code shows its own hover widget rather than the platform tooltip, and only
  // after a beat, so anything carrying `data-tip` gets the same treatment: one
  // shared element, positioned against the target, escaping the scrolling page.
  // Keyboard focus shows it too, since an icon-only action has no visible label.
  const TIP_DELAY = 550;
  let tipEl = null;
  let tipTimer = null;
  let tipTarget = null;

  function hideTip() {
    if (tipTimer) {
      clearTimeout(tipTimer);
      tipTimer = null;
    }
    tipTarget = null;
    if (tipEl) {
      tipEl.remove();
      tipEl = null;
    }
  }

  function showTip(target) {
    const text = target.getAttribute("data-tip");
    if (!text || !target.isConnected) return;
    tipEl = h("div", { class: "settings-tip", role: "tooltip", text });
    document.body.appendChild(tipEl);
    const r = target.getBoundingClientRect();
    const t = tipEl.getBoundingClientRect();
    const margin = 6;
    // Below by default, above when there is no room, and clamped horizontally.
    let top = r.bottom + 4;
    if (top + t.height > window.innerHeight - margin) {
      top = Math.max(margin, r.top - t.height - 4);
    }
    let left = r.left + r.width / 2 - t.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - t.width - margin));
    tipEl.style.top = Math.round(top) + "px";
    tipEl.style.left = Math.round(left) + "px";
  }

  function armTip(target) {
    if (target === tipTarget) return;
    hideTip();
    if (!target) return;
    tipTarget = target;
    tipTimer = setTimeout(() => {
      tipTimer = null;
      showTip(target);
    }, TIP_DELAY);
  }

  const tipFor = (e) => (e.target && e.target.closest ? e.target.closest("[data-tip]") : null);
  document.addEventListener("mouseover", (e) => armTip(tipFor(e)));
  document.addEventListener("mouseleave", hideTip);
  document.addEventListener("focusin", (e) => armTip(tipFor(e)));
  document.addEventListener("focusout", hideTip);
  // A tooltip must never linger over what it was describing.
  document.addEventListener("click", hideTip, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); }, true);
  window.addEventListener("scroll", hideTip, true);

  function tag(text, cls) { return h("span", { class: "settings-tag " + (cls || ""), text }); }
  function empty(text) { return h("div", { class: "settings-empty", text }); }
  function splitList(v) { return (v || "").split(",").map((s) => s.trim()).filter(Boolean); }

  // A flat settings group: a small heading, an optional right-aligned action,
  // and rows separated by hairlines. Replaces the old bordered card so a single
  // toggle costs one row instead of a titled, described box.
  //
  // `opts.action` is an element for the heading (an Add button, say).
  // `opts.reset` adds a reset action covering exactly the config keys the rows
  // it contains carry, so the two can never drift apart.
  function group(title, rows, opts) {
    const o = opts || {};
    const body = h("div", { class: "settings-group-body" }, rows);
    let action = o.action || null;
    if (o.reset) {
      action = resetAction([...body.querySelectorAll("[data-key]")].map((e) => e.getAttribute("data-key")), title);
    }
    return h("div", { class: "settings-group" }, [
      title || action
        ? h("div", { class: "settings-group-head" }, [
            h("div", { class: "settings-group-title", text: title || "" }),
            action
          ])
        : null,
      body
    ]);
  }
  // One setting row. A control people type into is stacked under the label so it
  // gets the row's full width, the way VS Code's own Settings editor lays out a
  // text setting: a path, a domain list or a proxy URL is unreadable in the 180px
  // a right-aligned control leaves. Fixed width controls (a dropdown, a toggle)
  // stay on the right, where they line up into a column you can scan.
  function fieldRow(label, control, hint, extra) {
    const controls = [].concat(control).filter(Boolean);
    const stacked = controls.some((c) => c.matches && c.matches("input[type=text], textarea"));
    return h("div", { class: "settings-field" + (stacked ? " stacked" : "") }, [
      h("div", { class: "settings-field-main" }, [
        h("div", { class: "settings-field-label" }, [].concat(label)),
        hint ? h("div", { class: "settings-field-hint", text: hint }) : null,
        extra || null
      ]),
      h("div", { class: "settings-field-control" }, controls)
    ]);
  }
  // A list row: title (plus tags) and a subtitle on the left, actions on the right.
  // `cls` carries state such as `disabled`, which strikes the name and subtitle
  // through so a switched-off entry reads as switched off at a glance.
  function listRow(title, sub, actions, cls) {
    return h("div", { class: "settings-list-row " + (cls || "") }, [
      h("div", { class: "settings-list-main" }, [
        h("div", { class: "settings-list-title" }, [].concat(title)),
        sub ? h("div", { class: "settings-list-sub oneline", text: sub, "data-tip": sub }) : null
      ]),
      h("div", { class: "settings-row-actions" }, actions)
    ]);
  }

  // Every rule the agent has actually loaded. The toggles and the single
  // instructions file above only partly explain what is in force: a rule can come
  // from a plugin, or from another tool's file in a directory this panel never
  // looks at, and only the CLI knows the full set. Read only: the row opens the
  // file it names, and some of them are not ours to edit.
  function loadedRules() {
    const list = data.instructions && data.instructions.loaded;
    if (!list) {
      // The CLI could not be asked. Better to say nothing than to imply the files
      // above are the whole story.
      return null;
    }
    const rows = list.map((r) => listRow(
      [r.name || "(unnamed)", r.providerLabel ? tag(r.providerLabel, "muted") : null, r.scope === "global" ? tag("global", "muted") : null],
      r.path,
      r.path ? [iconBtn("go-to-file", "Open this file", () => post("settings:openFile", { path: r.path }))] : []
    ));
    return group("In force now", rows.length ? rows : [empty("No always-on rules are loaded.")]);
  }

  // The hooks the agent has really loaded, including ones from a plugin or written
  // in another tool's format, which the editable list below cannot see.
  function loadedHooks() {
    const list = data.hooks && data.hooks.loaded;
    if (!list) {
      return null;
    }
    const rows = list.map((hk) => listRow(
      [hk.name || hk.id || "hook", (hk.events || []).length ? tag((hk.events || []).join(", "), "muted") : null, hk.format ? tag(hk.format, "muted") : null],
      hk.sourcePath,
      hk.sourcePath ? [iconBtn("go-to-file", "Open the file it comes from", () => post("settings:openFile", { path: hk.sourcePath }))] : []
    ));
    return group("In force now", rows.length ? rows : [empty("No hooks are loaded.")]);
  }

  // A centered modal dialog. `build(close)` returns the body element and may call
  // close() after a successful submit.
  function openModal(title, build) {
    const overlay = h("div", { class: "settings-modal-overlay" });
    const dialog = h("div", { class: "settings-modal" });
    const close = () => { document.removeEventListener("keydown", onKey, true); overlay.remove(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const head = h("div", { class: "settings-modal-head" }, [
      h("div", { class: "settings-modal-title", text: title }),
      h("button", { class: "settings-icon-btn", "data-tip": "Close", "aria-label": "Close", onclick: close }, [icon("close")])
    ]);
    // `settings-form` stacks every row: in a form, one full width column reads
    // better than labels and controls fighting over a narrow dialog.
    dialog.append(head, h("div", { class: "settings-modal-body settings-form" }, build(close)));
    overlay.appendChild(dialog);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    setTimeout(() => document.addEventListener("keydown", onKey, true), 0);
    document.body.appendChild(overlay);
    return close;
  }
  function modalActions(children) {
    return h("div", { class: "settings-modal-actions" }, children);
  }
  // A submitted form stays open with its button spinning until the host answers,
  // rather than closing onto a panel that has not caught up yet.
  let pendingClose = null;
  function deferClose(close) {
    pendingClose = close;
  }
  function closePending() {
    if (!pendingClose) return;
    const close = pendingClose;
    pendingClose = null;
    close();
  }

  // --- Scope ---------------------------------------------------------------
  // The scopes on offer: Global, plus one per workspace folder. With one folder
  // open it is simply "Workspace"; with several, each is named.
  function scopeTabs() {
    const folders = (data && data.folders) || [];
    const single = folders.length === 1;
    return [{ key: "user", label: "Global" }].concat(
      folders.map((f) => ({ key: f.path, label: single ? "Workspace" : f.name }))
    );
  }
  function isActiveScope(g) {
    return scope === "user" ? g.scope === "user" : g.root === scope;
  }
  // The entry for the active scope out of a per-scope array from the host.
  function scoped(byScope) {
    return (byScope || []).find(isActiveScope) || null;
  }
  function activeValues() {
    return scoped(data && data.valuesByScope) || { values: {}, setKeys: [] };
  }
  function val(key) {
    return activeValues().values[key];
  }
  function setPath(key, value) {
    const g = activeValues();
    post("settings:setPath", { scope: g.scope || "user", root: g.root, path: key, value });
  }
  function scopeLabelOf(key) {
    const t = scopeTabs().find((x) => x.key === key);
    return t ? t.label : "Global";
  }
  // On the Global scope, flag a key a folder genuinely overrides, naming the folders.
  function overrideNote(key) {
    const fo = (data && data.folderOverrides) || {};
    const names = ((data && data.folders) || [])
      .filter((f) => (fo[f.path] || []).indexOf(key) >= 0)
      .map((f) => f.name);
    if (!names.length) return null;
    return h("div", { class: "settings-field-override" }, ["Overridden in " + names.join(", ")]);
  }

  // A value row bound to a CLI config key in the active scope. Adds the "set
  // here" marker and a clear action on a folder scope, and the "overridden in
  // <folder>" note on the User scope, so hiding the other scopes behind a tab
  // costs no information.
  function keyRow(key, label, control, hint) {
    const g = activeValues();
    const setHere = (g.setKeys || []).indexOf(key) >= 0;
    const marks = setHere ? [h("span", { class: "settings-dot", "data-tip": "Set in " + scopeLabelOf(scope) })] : [];
    const notes = [];
    if (scope === "user") {
      const o = overrideNote(key);
      if (o) notes.push(o);
    }
    const controls = [].concat(control);
    if (setHere && scope !== "user") {
      controls.push(iconBtn("discard", "Clear override, use the Global value", () => setPath(key, undefined)));
    }
    const row = fieldRow(
      marks.concat(h("span", { text: label })),
      controls,
      hint,
      notes.length ? h("div", null, notes) : null
    );
    // Lets a group derive exactly which keys its reset action covers.
    row.setAttribute("data-key", key);
    return row;
  }
  function keyToggle(key, label, hint) {
    return keyRow(key, label, toggle(val(key), (v) => setPath(key, v)), hint);
  }
  function keySelect(key, label, options, hint, emptyToUndefined) {
    const current = val(key) == null ? "" : String(val(key));
    // A value the list does not offer still has to be what the row shows. With no
    // option matching, a browser selects the first one, so a `notify` the CLI
    // wrote as something newer read as "Never", and a model set by /model read as
    // the CLI default, both looking like a deliberate setting and both wrong.
    const shown = current && !options.some((o) => String(o.value) === current)
      ? [...options, { value: current, label: current + " (set outside this list)" }]
      : options;
    const control = select(shown, current, (v) => setPath(key, emptyToUndefined && !v ? undefined : v));
    return keyRow(key, label, control, hint);
  }
  function keyText(key, label, placeholder, hint) {
    return keyRow(key, label, textInput(val(key) || "", placeholder, (v) => setPath(key, v || undefined)), hint);
  }
  function keyList(key, label, placeholder, hint) {
    // Whatever the file holds, the way every other control here takes it. A list
    // written as a bare string threw, and the throw came after the pane had been
    // emptied to be redrawn, so the section rendered nothing at all, and search,
    // which renders every section, blanked the whole page.
    const v = val(key);
    const current = Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v);
    return keyRow(key, label, textInput(current, placeholder, (v) => setPath(key, splitList(v))), hint);
  }
  // Reset the keys of one group, in the active scope only. Absent when that scope
  // sets none of them, since resetting would then be a no-op.
  function resetAction(keys, label) {
    const g = activeValues();
    if (!keys.some((k) => (g.setKeys || []).indexOf(k) >= 0)) return null;
    return iconBtn("discard", "Reset " + (label || "these settings") + " to defaults in " + scopeLabelOf(scope), () =>
      post("settings:resetSection", { scope: g.scope || "user", root: g.root, keys, label: label || "these settings" }));
  }
  // Header action for a list section, targeting the active scope.
  function addBtn(title, modalBody) {
    const g = activeValues();
    return btn("Add", "add", () => openModal(title + " in " + scopeLabelOf(scope), (close) => modalBody(close, g)), "primary");
  }

  // --- Chrome --------------------------------------------------------------
  function renderNav() {
    nav.innerHTML = "";
    for (const s of SECTIONS) {
      nav.appendChild(h("button", {
        class: "settings-nav-item" + (s.id === active && !query ? " active" : ""),
        // Render from what we have straight away, and ask the host to re-read the
        // files behind this section, so arriving at one never shows a stale view.
        onclick: () => { active = s.id; query = ""; render(); post("settings:reload"); }
      }, [icon(s.icon), h("span", { text: s.label })]));
    }
  }

  function setScope(key) {
    scope = key;
    // Tell the host, so project-scoped CLI verbs run in that folder. It answers
    // with freshly read data, so switching scope also picks up outside edits.
    post("settings:setRoot", { path: key === "user" ? "" : key });
    render();
  }

  // The scope picker. Two scopes (the common case of one folder open) are a pair
  // of tabs, which is one click. More than that would wrap into a wall of
  // buttons, so a workspace with several folders gets a dropdown instead.
  function renderScopePicker(tabs) {
    if (tabs.length > 2) {
      // The folders sit under a heading rather than each carrying a "Workspace:"
      // prefix, which keeps the control narrow and reads as one grouped list.
      const wrap = select(
        tabs.map((t) => ({ value: t.key, label: t.label, group: t.key === "user" ? undefined : "Workspace folder" })),
        scope,
        setScope
      );
      wrap.classList.add("settings-scope-picker");
      const sel = wrap.querySelector("select");
      sel.classList.add("settings-scope-select");
      sel.setAttribute("aria-label", "Settings scope");
      return wrap;
    }
    const group = h("div", { class: "settings-scope-tabs", role: "tablist", "aria-label": "Settings scope" });
    for (const t of tabs) {
      group.appendChild(h("button", {
        class: "settings-scope-btn" + (t.key === scope ? " active" : ""),
        role: "tab",
        "aria-selected": t.key === scope ? "true" : "false",
        "data-tip": t.key === "user" ? "Applies to every workspace" : t.key,
        onclick: () => setScope(t.key)
      }, [t.label]));
    }
    return group;
  }

  function renderToolbar() {
    toolbar.innerHTML = "";
    const tabs = scopeTabs();
    if (!tabs.some((t) => t.key === scope)) scope = "user";
    const search = h("input", {
      class: "settings-search", type: "search", placeholder: "Search settings",
      "aria-label": "Search settings"
    });
    search.value = query;
    search.addEventListener("input", () => { query = search.value.trim(); renderNav(); renderContent(); });
    // A section the CLI manages once for the machine has no scope to choose.
    const scoped = query ? true : !sectionOf(active).global;
    // Built as a list so an absent picker is skipped: append() would stringify a
    // null into the toolbar.
    const items = scoped ? [renderScopePicker(tabs)] : [];
    items.push(h("div", { class: "settings-toolbar-spacer" }), search);
    toolbar.append(...items);
    toolbar.classList.remove("hidden");
  }

  // The config file the active scope writes to, with an open or create action.
  // `inherits` adds the legend for unmarked rows, and is only true on a section
  // that actually shows a value inherited from the Global scope.
  function scopeFileLine(inherits) {
    const g = activeValues();
    if (!g.path || sectionOf(active).global) return null;
    return h("div", { class: "settings-scope-file" }, [
      h("span", { class: "settings-scope-file-path oneline", text: (g.exists ? "Editing " : "Not created yet: ") + g.path, "data-tip": g.path }),
      g.exists
        ? iconBtn("go-to-file", "Open this config file", () => post("settings:openFile", { path: g.path }))
        : iconBtn("add", "Create this config file", () => post("settings:createFile", { path: g.path, template: "{\n}\n" })),
      inherits ? h("span", { class: "settings-inherited", text: "Unmarked values are inherited from Global" }) : null
    ]);
  }

  function render() {
    renderNav();
    renderToolbar();
    renderContent();
  }

  function renderContent() {
    if (!data) return;
    const top = main ? main.scrollTop : 0;
    content.innerHTML = "";
    if (query) {
      content.appendChild(renderSearch(query.toLowerCase()));
    } else {
      const fn = renderers[active];
      const section = fn ? fn() : null;
      // Only claim values are inherited when this section really shows one.
      const inherits = !!section && scope !== "user" &&
        [...section.querySelectorAll("[data-key]")].some((r) => !r.querySelector(".settings-dot"));
      const file = scopeFileLine(inherits);
      if (file) content.appendChild(file);
      if (section) content.appendChild(section);
    }
    if (main) main.scrollTop = top;
  }

  // --- Search --------------------------------------------------------------
  // With a query, every section is rendered and then filtered down to matching
  // rows. Depth can live behind sections without being buried.
  function renderSearch(q) {
    const frag = document.createDocumentFragment();
    let hits = 0;
    for (const s of SECTIONS) {
      const el = renderers[s.id]();
      const kept = filterRows(el, q, s.label.toLowerCase().indexOf(q) >= 0);
      if (!kept) continue;
      hits += kept;
      frag.append(h("div", { class: "settings-result-head" }, [icon(s.icon), h("span", { text: s.label })]), el);
    }
    if (!hits) frag.appendChild(empty("No settings match \u201c" + query + "\u201d."));
    return frag;
  }
  const ROW_SEL = ".settings-field, .settings-list-row, .settings-perm-bucket";
  // A row matches on its own text, its config key, or its group's heading, since
  // the heading often carries the word being searched for ("Proxy", "Sandbox").
  function filterRows(root, q, keepAll) {
    let kept = 0;
    for (const g of root.querySelectorAll(".settings-group")) {
      const title = g.querySelector(".settings-group-title");
      const groupHit = keepAll || (title ? title.textContent.toLowerCase().indexOf(q) >= 0 : false);
      let inGroup = 0;
      for (const row of g.querySelectorAll(ROW_SEL)) {
        const hay = (row.textContent + " " + (row.getAttribute("data-key") || "")).toLowerCase();
        const hit = groupHit || hay.indexOf(q) >= 0;
        row.classList.toggle("settings-hidden", !hit);
        if (hit) inGroup++;
      }
      g.classList.toggle("settings-hidden", !inGroup);
      kept += inGroup;
    }
    return kept;
  }

  // --- Sections ------------------------------------------------------------
  const renderers = {
    general() {
      const wrap = h("div");
      const familyOpts = [{ value: "", label: "CLI default (Adaptive)" }].concat(
        ((data.models && data.models.families) || []).map((f) => ({ value: f.default || f.id, label: f.name || f.id }))
      );
      const modelRow = keySelect("agent.model", "Default model", familyOpts,
        "The model the Devin CLI uses when nothing overrides it.", true);
      const clash = extensionModelNotice();
      if (clash) modelRow.querySelector(".settings-field-main").appendChild(clash);
      wrap.append(
        group("Model", [modelRow], { reset: true }),
        group("Session", [
          keyToggle("agent.show_history_on_continue", "Show history on resume", "Show previous messages when resuming a session.")
        ], { reset: true }),
        group("Behaviour", [
          keyToggle("attribution", "Attribution on commits and PRs", "Add the Generated with Devin line and the Co-Authored-By trailer."),
          keyToggle("auto_update", "Auto update the Devin CLI"),
          keySelect("notify", "Notifications", [
            { value: "never", label: "Never" },
            { value: "smart", label: "Smart (when unfocused)" },
            { value: "always", label: "Always" }
          ]),
          keyToggle("respect_gitignore", "Respect .gitignore for tool access"),
          keyToggle("include_gitignored_files", "Include gitignored files in @ completions"),
          keyToggle("show_hints", "Show hints between turns")
        ], { reset: true }),
        extensionLinks()
      );
      return wrap;
    },

    instructions() {
      const g = scoped(data.instructions && data.instructions.byScope);
      const f = (g && g.file) || {};
      const wrap = h("div");
      wrap.append(
        group("Instructions file", [
          f.path
            ? listRow(
                ["Instructions", f.kind ? tag(f.kind, "muted") : null],
                f.path,
                f.exists
                  ? [
                      iconBtn("edit", "Edit instructions", () => post("settings:openFile", { path: f.path })),
                      iconBtn("trash", "Remove instructions", () => post("settings:deletePath", { path: f.path, isDir: false, label: "instructions" }), "danger")
                    ]
                  : [btn("Create AGENTS.md", "add", () => post("settings:createFile", { path: f.path, template: "# Instructions\n\n- \n" }))]
              )
            : empty("Open a folder to add workspace instructions.")
        ]),
        group("Import rules from other tools", [
          keyToggle("read_config_from.agents_standard", "Standard (AGENTS.md, .windsurfrules)"),
          keyToggle("read_config_from.cursor", "Cursor (.cursor/rules)"),
          keyToggle("read_config_from.windsurf", "Windsurf (.windsurf/rules)"),
          keyToggle("read_config_from.claude", "Claude Code (.claude)")
        ], { reset: true }),
        loadedRules()
      );
      return wrap;
    },

    skills() {
      const g = scoped(data.skills && data.skills.byScope);
      const rows = ((g && g.list) || []).map((it) => listRow(
        "/" + it.name,
        it.description,
        [
          it.path ? iconBtn("edit", "Edit SKILL.md", () => post("settings:openFile", { path: it.path })) : null,
          it.dir ? iconBtn("trash", "Remove skill", () => post("settings:deletePath", { path: it.dir, isDir: true, label: "skill /" + it.name }), "danger") : null
        ].filter(Boolean)
      ));
      return group("Skills", rows.length ? rows : [empty("No skills here. Add one, or use /skill-creator.")],
        { action: addBtn("New skill", skillModalBody) });
    },

    plugins() {
      const rows = ((data.plugins && data.plugins.list) || []).map((p) => listRow(
        p.name,
        p.description,
        [
          iconBtn("sync", "Update plugin", () => post("settings:pluginVerb", { verb: "update", arg: p.name })),
          iconBtn("trash", "Remove plugin", () => post("settings:pluginVerb", { verb: "remove", arg: p.name }), "danger")
        ]
      ));
      const wrap = h("div");
      wrap.append(
        group("Installed plugins", rows.length ? rows : [empty("No plugins installed.")],
          { action: btn("Install", "cloud-download", () => openModal("Install a plugin", pluginModalBody), "primary") }),
        h("div", { class: "settings-note", text: "A plugin bundles skills, hooks, and rules. The Devin CLI installs them once for your machine, so they apply to every workspace." })
      );
      return wrap;
    },

    mcp() {
      const g = scoped(data.mcp && data.mcp.byScope);
      const all = (g && g.servers) || [];
      const row = (sv) => {
        const meta = [tag(sv.transport, "muted")];
        if (sv.disabled) meta.push(tag("disabled", "muted"));
        if (sv.loggedIn) meta.push(tag("signed in", "scope"));
        if ((sv.envKeys || []).length) meta.push(tag(sv.envKeys.length + " env", "muted"));
        // Which file the server is written in decides who edits it: the CLI owns
        // its own, and another tool's is written directly.
        const src = sv.source === "windsurf" ? { source: "windsurf" } : {};
        const actions = [];
        if (sv.loggedIn) {
          actions.push(iconBtn("sign-out", "Log out", () => post("settings:mcpVerb", { verb: "logout", name: sv.name, root: g.root })));
        } else if (sv.oauthCapable) {
          actions.push(iconBtn("key", "Log in (OAuth)", () => post("settings:mcpLogin", { name: sv.name, root: g.root })));
        }
        actions.push(sv.disabled
          ? iconBtn("check", "Enable", () => post("settings:mcpVerb", Object.assign({ verb: "enable", name: sv.name, scope: sv.scope, root: g.root }, src)))
          : iconBtn("circle-slash", "Disable", () => post("settings:mcpVerb", Object.assign({ verb: "disable", name: sv.name, scope: sv.scope, root: g.root }, src))));
        if (sv.file) actions.push(iconBtn("edit", "Edit config", () => post("settings:openFile", { path: sv.file })));
        actions.push(iconBtn("trash", "Remove", () => post("settings:mcpVerb", Object.assign({ verb: "remove", name: sv.name, scope: sv.scope, root: g.root }, src)), "danger"));
        // The name is its own element so a disabled server can be struck through
        // without striking through its tags.
        const name = h("span", { class: "settings-list-name", text: sv.name });
        return listRow([name].concat(meta), sv.detail, actions, sv.disabled ? "disabled" : "");
      };
      const own = all.filter((sv) => sv.source !== "windsurf");
      const wind = all.filter((sv) => sv.source === "windsurf");
      const groups = [group("MCP servers", own.length ? own.map(row) : [empty("No MCP servers here.")],
        { action: addBtn("Add MCP server", mcpModalBody) })];
      // Windsurf's servers are loaded by the Devin CLI too, so they are part of
      // what this agent can do. They are listed apart because they live in
      // Windsurf's config and editing one changes it for Windsurf as well.
      if (wind.length || scope === "user") {
        groups.push(group("MCP servers from Windsurf",
          wind.length ? wind.map(row) : [empty("Windsurf has no MCP servers configured.")],
          { action: addBtn("Add MCP server to Windsurf", (close, gg) => mcpModalBody(close, gg, "windsurf")) }));
      }
      return h("div", {}, groups);
    },

    hooks() {
      const g = scoped(data.hooks && data.hooks.byScope);
      const entries = (g && g.entries) || [];
      const rows = [];
      const byEvent = {};
      for (const e of entries) (byEvent[e.event] = byEvent[e.event] || []).push(e);
      for (const ev of Object.keys(byEvent)) {
        rows.push(h("div", { class: "settings-subhead", text: ev }));
        for (const e of byEvent[ev]) {
          rows.push(listRow(
            [e.type || "command", e.matcher ? tag("match: " + e.matcher, "muted") : null],
            e.command || e.prompt || "",
            e.source
              ? [
                  iconBtn("edit", "Edit source", () => post("settings:openFile", { path: e.source })),
                  iconBtn("trash", "Remove hook", () => post("settings:removeHook", {
                    source: e.source, event: e.event, matcher: e.matcher, command: e.command, prompt: e.prompt
                  }), "danger")
                ]
              : []
          ));
        }
      }
      const wrap = h("div");
      wrap.append(
        group("Hooks", rows.length ? rows : [empty("No hooks here.")], { action: addBtn("Add hook", hookModalBody) }),
        loadedHooks()
      );
      return wrap;
    },

    permissions() {
      const g = scoped(data.permissions && data.permissions.byScope) || { scope: "user" };
      const rows = ["allow", "deny", "ask"].map((bucket) => {
        const chips = (g[bucket] || []).map((v) => {
          const x = h("button", {
            class: "settings-perm-x", "data-tip": "Remove " + v, "aria-label": "Remove " + v
          }, [icon("close")]);
          x.addEventListener("click", whileBusy(x, () =>
            post("settings:permission", { scope: g.scope, root: g.root, bucket, value: v, remove: true })));
          return h("span", { class: "settings-perm-chip" }, [h("span", { text: v }), x]);
        });
        return h("div", { class: "settings-perm-bucket" }, [
          h("div", { class: "settings-perm-bucket-label", text: bucket }),
          h("div", { class: "settings-perm-chips" }, chips.length ? chips : [h("span", { class: "settings-empty-inline", text: "none" })])
        ]);
      });
      // One adder for all three buckets, instead of an input per bucket.
      let addTo = "allow";
      const bucketSel = select(
        [{ value: "allow", label: "Allow" }, { value: "deny", label: "Deny" }, { value: "ask", label: "Ask" }],
        addTo, (v) => { addTo = v; }
      );
      const ruleInput = textInput("", "Exec(git status), Read(**), MCP(github)");
      const submit = () => {
        const v = (ruleInput.value || "").trim();
        if (!v) return;
        post("settings:permission", { scope: g.scope, root: g.root, bucket: addTo, value: v });
        ruleInput.value = "";
      };
      ruleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
      rows.push(fieldRow("Add a rule", [bucketSel, ruleInput, btn("Add", "add", submit, "primary")],
        "Allow runs a matching tool silently, deny blocks it, ask always prompts."));
      return group("Permissions", rows);
    },

    advanced() {
      const wrap = h("div");
      wrap.append(
        group("Proxy", [
          keySelect("proxy.mode", "Mode", [
            { value: "system", label: "System" },
            { value: "manual", label: "Manual" },
            { value: "off", label: "Off" }
          ], "How the Devin CLI routes its own outbound traffic."),
          keyText("proxy.url", "URL", "http://proxy.example.com:8080"),
          keyText("proxy.no_proxy", "No proxy", "localhost,127.0.0.1,.corp")
        ], { reset: true }),
        group("Sandbox", [
          keySelect("sandbox.network_mode", "Network mode", [
            { value: "full", label: "Full" },
            { value: "limited", label: "Limited" }
          ], "Domain filtering when running with --sandbox, which the CLI marks unstable. Limited allows only GET, HEAD and OPTIONS."),
          keyList("sandbox.allowed_domains", "Allowed domains", "github.com, **.npmjs.org"),
          keyList("sandbox.denied_domains", "Denied domains", "evil.example.com")
        ], { reset: true }),
        group("Terminal display", [
          keySelect("theme_mode", "Theme", [
            { value: "", label: "Auto" },
            { value: "light", label: "Light" },
            { value: "dark", label: "Dark" },
            { value: "terminal-dark", label: "Terminal dark" },
            { value: "terminal-light", label: "Terminal light" },
            { value: "nocolor", label: "No color" }
          ], "These affect the devin terminal interface, not this extension's chat panel.", true),
          keySelect("unicode_mode", "Unicode", [
            { value: "auto", label: "Auto" },
            { value: "unicode", label: "Unicode" },
            { value: "ascii", label: "ASCII" }
          ]),
          keyToggle("disable_osc", "Disable OSC escape sequences",
            "Turn off terminal title and hyperlink escape codes if your terminal mishandles them.")
        ], { reset: true }),
        group("Execution", [
          keyToggle("pty_for_noninteractive_exec", "Use a PTY for non-interactive exec",
            "Run non-interactive commands under a pseudo-terminal, which helps tools that need a TTY.")
        ], { reset: true })
      );
      return wrap;
    }
  };

  // --- The extension's own settings ----------------------------------------
  // Anything that controls this extension rather than the Devin CLI belongs in
  // VS Code settings, so this surface only links to it. The one exception is a
  // conflict: when the VS Code setting `devin.defaultModel` is set, it silently
  // overrides the CLI model above for chats started here, which is invisible
  // otherwise. That is reported as a warning on the affected row, not as a
  // setting of its own, and only while the conflict exists.
  function extensionModelNotice() {
    const override = ((data && data.extension) || {}).defaultModel || "";
    if (!override) return null;
    return h("div", { class: "settings-notice" }, [
      icon("info"),
      h("span", { text: "Chats here use " + (modelLabel(override) || override) + ", from the VS Code setting devin.defaultModel." }),
      linkBtn("Open it", () => post("settings:openExtensionSettings", { query: "defaultModel" })),
      linkBtn("Clear it", () => post("settings:clearExtensionModel"))
    ]);
  }
  function modelLabel(uid) {
    const fam = ((data.models && data.models.families) || []).find((f) => f.default === uid || f.id === uid);
    return fam ? fam.name || fam.id : "";
  }
  function extensionLinks() {
    const link = (label, hint, q) => fieldRow(
      label, [iconBtn("gear", "Open in VS Code settings", () => post("settings:openExtensionSettings", { query: q }))], hint
    );
    return group("VS Code extension settings", [
      link("All extension settings", "Everything that controls this extension rather than the Devin CLI.", ""),
      link("Session defaults", "The mode and model new chats start with.", "default"),
      link("Thinking display", "Whether and how the reasoning stream is shown.", "thinking"),
      link("Checkpoints and editing", "Restore points, edit requests, and file change summaries.", "checkpoints")
    ]);
  }

  // --- Create forms --------------------------------------------------------
  // Each form targets the scope it was opened from, so it never asks for a scope.
  function skillModalBody(close, g) {
    const name = textInput("", "release-notes");
    return h("div", null, [
      h("div", { class: "settings-modal-desc", text: "Scaffolds a SKILL.md in " + scopeLabelOf(scope) + ". The name becomes the slash command (/name)." }),
      fieldRow("Name", name, "Becomes the slash command, so /release-notes."),
      modalActions([
        btn("Cancel", null, close),
        btn("Create skill", "add", () => {
          const v = (name.value || "").trim();
          if (!v) return;
          post("settings:createSkill", { name: v, scope: g.scope, root: g.root });
          deferClose(close);
        }, "primary")
      ])
    ]);
  }

  function hookModalBody(close, g) {
    let event = "PreToolUse";
    let hookType = "command";
    const eventSel = select([
      { value: "PreToolUse", label: "PreToolUse" },
      { value: "PostToolUse", label: "PostToolUse" },
      { value: "PermissionRequest", label: "PermissionRequest" },
      { value: "UserPromptSubmit", label: "UserPromptSubmit" },
      { value: "Stop", label: "Stop" },
      { value: "SessionStart", label: "SessionStart" },
      { value: "SessionEnd", label: "SessionEnd" }
    ], event, (v) => { event = v; });
    const typeSel = select(
      [{ value: "command", label: "Command" }, { value: "prompt", label: "Prompt" }],
      hookType, (v) => { hookType = v; renderValue(); }
    );
    const matcher = textInput("", "exec");
    const timeout = textInput("", "30");
    // Both controls are built up front and swapped, so switching type back and
    // forth keeps whatever was typed in each.
    const command = textInput("", "npm run lint -- --fix");
    const prompt = textArea("", "Remember to run the tests before committing.");
    // The field is named for the type that is actually selected, rather than
    // offering one box labelled with both.
    const valueRow = h("div");
    const renderValue = () => {
      valueRow.innerHTML = "";
      valueRow.appendChild(hookType === "prompt"
        ? fieldRow("Prompt", prompt, "Injected into the conversation when the event fires.")
        : fieldRow("Command", command, "Run by your shell when the event fires."));
    };
    renderValue();
    return h("div", null, [
      h("div", { class: "settings-modal-desc", text: "Runs a command or injects a prompt on a lifecycle event, in " + scopeLabelOf(scope) + "." }),
      fieldRow("Event", eventSel),
      fieldRow("Type", typeSel),
      valueRow,
      fieldRow("Matcher", matcher, "Only run for tools whose name matches this. Leave it empty for every tool."),
      fieldRow("Timeout", timeout, "Seconds to allow before the hook is abandoned. Optional."),
      modalActions([
        btn("Cancel", null, close),
        btn("Add hook", "add", () => {
          const v = (hookType === "prompt" ? prompt.value : command.value).trim();
          if (!v) return;
          post("settings:addHook", {
            scope: g.scope, root: g.root, event, hookType,
            matcher: (matcher.value || "").trim(), value: v, timeout: (timeout.value || "").trim()
          });
          deferClose(close);
        }, "primary")
      ])
    ]);
  }

  function mcpModalBody(close, g, source) {
    const name = textInput("", "github");
    const cmd = textInput("", "https://mcp.example.com/mcp");
    const envIn = textInput("", "TOKEN=abc123, REGION=eu");
    return h("div", null, [
      h("div", { class: "settings-modal-desc", text: source === "windsurf"
        ? "Adds a server to Windsurf's config, which the Devin CLI reads too. Enter a URL for HTTP transport, or a command line for stdio."
        : "Adds a server to " + scopeLabelOf(scope) + ". Enter a URL for HTTP transport, or a command line for stdio." }),
      fieldRow("Name", name),
      fieldRow("URL or command", cmd, "A URL uses HTTP transport. A command line (npx -y @scope/server) uses stdio."),
      fieldRow("Env", envIn, "Comma separated KEY=value pairs. Optional."),
      modalActions([
        btn("Cancel", null, close),
        btn("Add server", "add", () => {
          const nameVal = (name.value || "").trim();
          const cmdVal = (cmd.value || "").trim();
          if (!nameVal || !cmdVal) return;
          const options = { name: nameVal, scope: g.scope };
          if (/^https?:\/\//i.test(cmdVal)) {
            options.transport = "http";
            options.url = cmdVal;
          } else {
            options.transport = "stdio";
            const parts = cmdVal.split(/\s+/);
            options.command = parts[0];
            options.args = parts.slice(1);
          }
          const env = {};
          for (const pair of (envIn.value || "").split(",")) {
            const eq = pair.indexOf("=");
            if (eq > 0) env[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
          }
          if (Object.keys(env).length) options.env = env;
          post("settings:mcpAdd", { options, root: g.root, source });
          deferClose(close);
        }, "primary")
      ])
    ]);
  }

  function pluginModalBody(close) {
    const src = textInput("", "github.com/org/repo");
    return h("div", null, [
      h("div", { class: "settings-modal-desc", text: "Installs from a Git source or a local path. Required plugins are installed too." }),
      fieldRow("Source", src, "A Git source or a local path."),
      modalActions([
        btn("Cancel", null, close),
        btn("Install", "cloud-download", () => {
          const v = (src.value || "").trim();
          if (!v) return;
          post("settings:pluginVerb", { verb: "install", arg: v });
          deferClose(close);
        }, "primary")
      ])
    ]);
  }

  // --- Messages ------------------------------------------------------------
  window.addEventListener("message", (event) => {
    const m = event.data;
    if (!m) return;
    if (m.type === "settings:data") {
      data = m.data;
      loading.classList.add("hidden");
      content.classList.remove("hidden");
      clearBusy();
      closePending();
      render();
    } else if (m.type === "settings:idle") {
      // The host finished without changing anything (a confirmation was declined),
      // so release the control that was waiting on it.
      clearBusy();
      closePending();
    } else if (m.type === "settings:error") {
      loading.classList.add("hidden");
      content.classList.remove("hidden");
      clearBusy();
      content.prepend(h("div", { class: "settings-error", text: m.text || "Something went wrong." }));
    }
  });

  renderNav();
  post("settings:load");
})();
