(() => {
  "use strict";

  const state = {
    active: false,
    loading: false,
    launcher: null,
    advanced: null,
    advancedBody: null,
    modelScriptPromise: null,
    uiSyncQueued: false,
  };

  function assetVersion() {
    const own = document.querySelector('script[src*="/static/smoke-optin-v1.js"]');
    if (!own) return String(Date.now());
    try {
      return new URL(own.src, window.location.href).searchParams.get("v") || String(Date.now());
    } catch (_) {
      return String(Date.now());
    }
  }

  function ensureStylesheet() {
    if (document.querySelector('link[data-ib-smoke-v3="true"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `/static/smoke-v3.css?v=${encodeURIComponent(assetVersion())}`;
    link.dataset.ibSmokeV3 = "true";
    document.head.appendChild(link);
  }

  function currentFrame() {
    return document.getElementById("map-frame");
  }

  function makeButton(label, className, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  }

  function launcherNeedsUpdate(launcher) {
    const desiredState = state.active ? "active" : state.loading ? "loading" : "idle";
    return launcher?.dataset?.state !== desiredState;
  }

  function renderLauncher() {
    const frame = currentFrame();
    if (!frame) return null;
    let launcher = document.getElementById("ib-smoke-optin");
    if (!launcher) {
      launcher = document.createElement("section");
      launcher.id = "ib-smoke-optin";
      launcher.setAttribute("aria-label", "Røganalyse");
      frame.insertAdjacentElement("beforebegin", launcher);
    }
    state.launcher = launcher;
    if (!launcherNeedsUpdate(launcher)) return launcher;

    launcher.dataset.state = state.active ? "active" : state.loading ? "loading" : "idle";
    launcher.replaceChildren();

    const text = document.createElement("div");
    text.className = "ib-smoke-optin-copy";
    const kicker = document.createElement("span");
    kicker.textContent = "RØGANALYSE";
    const title = document.createElement("strong");
    const detail = document.createElement("small");

    if (!state.active) {
      title.textContent = state.loading ? "Starter røgmodel …" : "Valgfri analyse";
      detail.textContent = "Kortet vises normalt. Røgmodel, plume-rise og røgkontekst beregnes kun, når du vælger det.";
    } else {
      title.textContent = "Røganalyse aktiv";
      detail.textContent = "Modellen er startet for denne side-session. Avancerede indstillinger er samlet og skjult som standard.";
    }
    text.append(kicker, title, detail);

    const actions = document.createElement("div");
    actions.className = "ib-smoke-optin-actions";
    if (!state.active) {
      const start = makeButton(state.loading ? "Starter …" : "Start røganalyse", "ib-smoke-optin-start", activate);
      start.disabled = state.loading;
      actions.appendChild(start);
    } else {
      actions.appendChild(makeButton("Indstillinger", "ib-smoke-optin-settings", () => {
        const details = ensureAdvancedWorkspace();
        if (!details) return;
        details.open = !details.open;
        if (details.open) details.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }));
    }
    launcher.append(text, actions);
    return launcher;
  }

  function loadModelScript() {
    if (state.modelScriptPromise) return state.modelScriptPromise;
    ensureStylesheet();
    state.modelScriptPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-ib-smoke-v3="true"]');
      if (existing) {
        if (existing.dataset.loaded === "true") resolve();
        else {
          existing.addEventListener("load", resolve, { once: true });
          existing.addEventListener("error", reject, { once: true });
        }
        return;
      }
      const script = document.createElement("script");
      script.src = `/static/smoke-v3.js?v=${encodeURIComponent(assetVersion())}`;
      script.dataset.ibSmokeV3 = "true";
      script.addEventListener("load", () => {
        script.dataset.loaded = "true";
        resolve();
      }, { once: true });
      script.addEventListener("error", () => reject(new Error("Røgmodel kunne ikke indlæses")), { once: true });
      document.body.appendChild(script);
    });
    return state.modelScriptPromise;
  }

  async function activate() {
    if (state.active || state.loading) return;
    state.loading = true;
    renderLauncher();
    try {
      await loadModelScript();
      state.active = true;
      document.body.classList.add("ib-smoke-analysis-active");
      window.dispatchEvent(new CustomEvent("indsatsbrief:smoke-analysis-state", { detail: { active: true } }));
      renderLauncher();
      queueUiSync();
      window.setTimeout(queueUiSync, 180);
      window.setTimeout(queueUiSync, 900);
    } catch (error) {
      console.warn("[IndsatsBrief] Røganalyse kunne ikke startes", error);
      state.modelScriptPromise = null;
      const launcher = document.getElementById("ib-smoke-optin");
      if (launcher) launcher.dataset.state = "";
      const rendered = renderLauncher();
      const detail = rendered?.querySelector("small");
      if (detail) detail.textContent = "Røgmodellen kunne ikke indlæses. Det almindelige kort er stadig tilgængeligt.";
    } finally {
      state.loading = false;
      const launcher = document.getElementById("ib-smoke-optin");
      if (launcher) launcher.dataset.state = "";
      renderLauncher();
    }
  }

  function ensureAdvancedWorkspace() {
    if (!state.active) return null;
    const summary = document.getElementById("ib-smoke-summary");
    const controls = document.getElementById("ib-smoke-controls");
    if (!summary || !controls) return null;

    let details = document.getElementById("ib-smoke-advanced");
    if (!details) {
      details = document.createElement("details");
      details.id = "ib-smoke-advanced";
      const heading = document.createElement("summary");
      const title = document.createElement("span");
      title.innerHTML = "<strong>Modelindstillinger og røgkontekst</strong><small>Brandscenarie · lokal vind · sårbare steder</small>";
      const hint = document.createElement("em");
      hint.textContent = "Vis";
      heading.append(title, hint);
      details.appendChild(heading);
      const body = document.createElement("div");
      body.className = "ib-smoke-advanced-body";
      details.appendChild(body);
      summary.insertAdjacentElement("afterend", details);
      details.addEventListener("toggle", () => {
        const em = details.querySelector("summary em");
        if (em) em.textContent = details.open ? "Skjul" : "Vis";
        window.setTimeout(() => window.dispatchEvent(new Event("resize")), 30);
      });
    }

    state.advanced = details;
    state.advancedBody = details.querySelector(".ib-smoke-advanced-body");
    moveAdvancedPanels();
    return details;
  }

  function moveAdvancedPanels() {
    const body = state.advancedBody;
    if (!body) return;
    [
      "ib-smoke-controls",
      "ib-v4-scenario-helper",
      "ib-field-observations",
      "ib-smoke-context-panel",
    ].forEach((id) => {
      const element = document.getElementById(id);
      if (element && element.parentElement !== body) body.appendChild(element);
    });
  }

  function metricLabel(card) {
    return card?.querySelector("span")?.textContent?.trim().toLowerCase() || "";
  }

  function replaceSmokeAction(host) {
    const actions = host?.querySelector(".ib-v4-glance-actions");
    if (!actions) return;
    const candidates = Array.from(actions.querySelectorAll("button"));
    const original = candidates.find((button) => /røgmodel|røganalyse/i.test(button.textContent || ""));
    if (!original) return;

    if (!state.active) {
      if (original.dataset.ibSmokeOptin === "true") return;
      const replacement = original.cloneNode(true);
      replacement.dataset.ibSmokeOptin = "true";
      replacement.textContent = "Start røganalyse";
      replacement.addEventListener("click", activate);
      original.replaceWith(replacement);
    } else if (/opdater røgmodel/i.test(original.textContent || "")) {
      original.textContent = "Opdater røganalyse";
    }
  }

  function compactOperationalGlance() {
    const host = document.getElementById("ib-operational-glance");
    if (!host) return;
    replaceSmokeAction(host);
    host.classList.toggle("ib-smoke-not-started", !state.active);
    Array.from(host.querySelectorAll(".ib-v4-glance-metric")).forEach((card) => {
      const label = metricLabel(card);
      const smokeMetric = label === "fortynding" || label === "røghøjde";
      card.hidden = smokeMetric && !state.active;
    });
  }

  function compactNavigation() {
    if (state.active) return;
    document.querySelectorAll('.ib-quick-nav [data-target="ib-smoke-context-panel"]').forEach((node) => node.remove());
  }

  function queueUiSync() {
    if (state.uiSyncQueued) return;
    state.uiSyncQueued = true;
    window.requestAnimationFrame(() => {
      state.uiSyncQueued = false;
      compactOperationalGlance();
      compactNavigation();
      if (state.active) {
        ensureAdvancedWorkspace();
        moveAdvancedPanels();
      }
    });
  }

  function mutationIsRelevant(mutation) {
    const target = mutation.target instanceof Element ? mutation.target : mutation.target?.parentElement;
    if (target?.closest?.("#ib-smoke-optin, #ib-smoke-advanced")) return false;
    if (target?.closest?.("#ib-operational-glance, .ib-quick-nav")) return true;
    for (const node of mutation.addedNodes || []) {
      if (!(node instanceof Element)) continue;
      if (
        node.matches?.("#ib-operational-glance, .ib-quick-nav, #ib-smoke-summary, #ib-smoke-controls, #ib-v4-scenario-helper, #ib-field-observations, #ib-smoke-context-panel") ||
        node.querySelector?.("#ib-operational-glance, .ib-quick-nav, #ib-smoke-summary, #ib-smoke-controls, #ib-v4-scenario-helper, #ib-field-observations, #ib-smoke-context-panel")
      ) return true;
    }
    return false;
  }

  function observeUi() {
    const observer = new MutationObserver((mutations) => {
      if (mutations.some(mutationIsRelevant)) queueUiSync();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function interceptSmokeShortcut() {
    document.addEventListener("keydown", (event) => {
      if (state.active || event.defaultPrevented) return;
      if (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key.toLowerCase() !== "r") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      activate();
    }, true);
  }

  function start() {
    renderLauncher();
    compactOperationalGlance();
    compactNavigation();
    observeUi();
    interceptSmokeShortcut();
    window.setTimeout(queueUiSync, 850);
  }

  window.IndsatsBriefSmokeOptIn = {
    activate,
    isActive: () => state.active,
    openSettings() {
      if (!state.active) return activate();
      const details = ensureAdvancedWorkspace();
      if (details) details.open = true;
    },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
