(() => {
  "use strict";

  const UI_VERSION = "Operational Intelligence v4";
  const state = {
    lastModelUpdate: null,
    focusMode: false,
    shortcutPanelOpen: false,
    lastAddressKey: null,
  };

  const toNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  const firstNumber = (...values) => {
    for (const value of values) {
      const number = toNumber(value);
      if (number !== null) return number;
    }
    return null;
  };

  function currentIncidentData() {
    try {
      return typeof latestIncidentData !== "undefined" && latestIncidentData ? latestIncidentData : null;
    } catch (_) {
      return null;
    }
  }

  function currentStructuredReport() {
    try {
      return typeof latestReportStructured !== "undefined" && latestReportStructured ? latestReportStructured : null;
    } catch (_) {
      return null;
    }
  }

  function currentReportText() {
    try {
      return typeof latestReportText !== "undefined" && latestReportText ? String(latestReportText) : "";
    } catch (_) {
      return document.getElementById("report")?.innerText || "";
    }
  }

  function buildingData(data) {
    return data?.building || data?.short_report_data?.building || {};
  }

  function weatherData(data) {
    return data?.weather || data?.short_report_data?.weather || {};
  }

  function addressLabel(data) {
    const structured = currentStructuredReport();
    const input = document.getElementById("address")?.value?.trim();
    const candidates = [
      structured?.title,
      data?.address?.formatted,
      data?.address?.address,
      data?.requested_address,
      data?.matched_address,
      input,
    ];
    return candidates.find((value) => typeof value === "string" && value.trim())?.trim() || "Aktuel adresse";
  }

  function formatDistance(meters) {
    if (!Number.isFinite(meters)) return "–";
    if (meters >= 1000) return `${(meters / 1000).toFixed(meters >= 10000 ? 0 : 1)} km`;
    return `${Math.round(meters / 10) * 10} m`;
  }

  function formatClock(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "–";
    return new Intl.DateTimeFormat("da-DK", { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function compass(degrees) {
    const number = toNumber(degrees);
    if (number === null) return null;
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    return labels[Math.round((((number % 360) + 360) % 360) / 22.5) % 16];
  }

  function extractBuildingSummary(data) {
    const building = buildingData(data);
    const area = firstNumber(
      building.area_m2,
      building.built_area_m2,
      building.samlet_bygningsareal,
      building.bebygget_areal
    );
    const floors = firstNumber(building.floors_count, building.antal_etager);
    const year = firstNumber(building.construction_year, building.opfoerelsesaar);
    const roof = building.roof_material_text || building.tagdaeknings_materiale_tekst || null;
    const facade = building.outer_wall_material_text || building.ydervaegs_materiale_tekst || null;
    return { building, area, floors, year, roof, facade };
  }

  function extractWeatherSummary(data) {
    const weather = weatherData(data);
    const speed = firstNumber(weather.wind_speed_ms, weather.wind_speed_10m);
    const gust = firstNumber(weather.wind_gust_ms, weather.wind_gusts_10m);
    const directionDegrees = firstNumber(weather.wind_direction_degrees, weather.wind_direction_10m);
    const directionText = weather.wind_direction_text || weather.wind_direction || null;
    const temperature = firstNumber(weather.temperature_c, weather.temperature, weather.temperature_2m);
    return { weather, speed, gust, directionDegrees, directionText, temperature };
  }

  function asbestosState(building) {
    const check = building?.asbestos_check || {};
    const fallback = building?.asbestos_fallback || {};
    const status = String(fallback.status || check.status || "").toLowerCase();
    if (status === "yes") return { level: "critical", label: "Asbest registreret" };
    if (["unknown", "not_returned", "partial_no"].includes(status)) return { level: "caution", label: "Asbeststatus uklar" };
    if (status === "no") return { level: "good", label: "BBR angiver ingen asbest" };
    return null;
  }

  function reportContains(pattern) {
    return pattern.test(currentReportText());
  }

  function collectRiskSignals(data) {
    const { building, floors, roof } = extractBuildingSummary(data);
    const signals = [];
    const asbestos = asbestosState(building);
    if (asbestos) signals.push(asbestos);

    if (roof && /eternit|fibercement|cement/i.test(String(roof)) && asbestos?.level !== "critical") {
      signals.push({ level: "caution", label: `Tag: ${roof}` });
    }

    const basementArea = firstNumber(building.basement_area_m2, building.kaelder_areal);
    if (building.basement_present === true || (basementArea !== null && basementArea > 0)) {
      signals.push({ level: "info", label: basementArea ? `Kælder ${Math.round(basementArea)} m²` : "Kælder registreret" });
    }

    if (floors !== null && floors >= 4) {
      signals.push({ level: "info", label: `${Math.round(floors)} etager` });
    }

    const secondary = Array.isArray(building.secondary_buildings)
      ? building.secondary_buildings.length
      : Array.isArray(data?.secondary_buildings)
        ? data.secondary_buildings.length
        : 0;
    if (secondary > 0) signals.push({ level: "info", label: `${secondary} sekundærbygning${secondary === 1 ? "" : "er"}` });

    const textSignals = [
      [/solcelle|solceller|photovoltaic|pv-anlæg/i, "Solceller registreret", "caution"],
      [/gasflaske|gastank|gasinstallation|naturgas/i, "Gasrelateret fund", "caution"],
      [/tank|oplag|kemikal|farlige stoffer|hazmat|cbrn/i, "Tank/oplag/kemi fund", "caution"],
      [/jernbane|banespor/i, "Jernbane i nærheden", "info"],
      [/adgangsbegræns|port|låge|bom/i, "Adgangsforhold markeret", "info"],
    ];
    textSignals.forEach(([pattern, label, level]) => {
      if (reportContains(pattern)) signals.push({ level, label });
    });

    const seen = new Set();
    return signals.filter((signal) => {
      const key = signal.label.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 8);
  }

  function readSmokeMetric(title) {
    const cards = Array.from(document.querySelectorAll("#ib-smoke-summary .ib-v3-metric"));
    const card = cards.find((item) => item.querySelector(".ib-v3-metric-title")?.textContent?.trim() === title);
    if (!card) return null;
    return {
      value: card.querySelector("strong")?.textContent?.trim() || "–",
      detail: card.querySelector("small")?.textContent?.trim() || "",
    };
  }

  function readSmokeConfidence() {
    const badge = document.querySelector("#ib-smoke-summary .ib-v3-confidence");
    return badge?.textContent?.trim() || null;
  }

  function ensureGlanceHost() {
    let host = document.getElementById("ib-operational-glance");
    if (host) return host;
    const status = document.getElementById("status");
    const grid = document.querySelector(".main-grid");
    if (!grid) return null;
    host = document.createElement("section");
    host.id = "ib-operational-glance";
    host.hidden = true;
    host.setAttribute("aria-label", "Indsatsblik");
    if (status) status.insertAdjacentElement("afterend", host);
    else grid.insertAdjacentElement("beforebegin", host);
    return host;
  }

  function createMetric(label, value, detail, tone = "") {
    const card = document.createElement("div");
    card.className = `ib-v4-glance-metric ${tone}`.trim();
    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    const valueEl = document.createElement("strong");
    valueEl.textContent = value || "–";
    const detailEl = document.createElement("small");
    detailEl.textContent = detail || "";
    card.append(labelEl, valueEl, detailEl);
    return card;
  }

  function makeAction(label, action, extraClass = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `ib-v4-action ${extraClass}`.trim();
    button.textContent = label;
    button.addEventListener("click", action);
    return button;
  }

  function scrollToId(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function refreshSmokeModel() {
    const frame = document.getElementById("map-frame");
    if (!frame) return;
    const src = frame.getAttribute("src");
    if (!src) return;
    try {
      const url = new URL(src, window.location.href);
      url.searchParams.set("ib_refresh", String(Date.now()));
      frame.setAttribute("src", url.toString());
    } catch (_) {
      frame.setAttribute("src", src);
    }
    state.lastModelUpdate = new Date();
    renderGlance();
  }

  function toggleFocusMode(force) {
    state.focusMode = typeof force === "boolean" ? force : !state.focusMode;
    document.body.classList.toggle("ib-v4-focus", state.focusMode);
    const button = document.querySelector("[data-ib-v4-focus]");
    if (button) {
      button.textContent = state.focusMode ? "Vis alle paneler" : "Fokusvisning";
      button.setAttribute("aria-pressed", state.focusMode ? "true" : "false");
    }
  }

  async function copyBrief() {
    const text = currentReportText().trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      flashToast("Brief kopieret");
    } catch (_) {
      document.getElementById("copy")?.click();
    }
  }

  function flashToast(message) {
    let toast = document.getElementById("ib-v4-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "ib-v4-toast";
      toast.setAttribute("role", "status");
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toast._ibTimer);
    toast._ibTimer = window.setTimeout(() => toast.classList.remove("show"), 1800);
  }

  function renderGlance() {
    const host = ensureGlanceHost();
    if (!host) return;
    const data = currentIncidentData();
    if (!data) {
      host.hidden = true;
      return;
    }

    host.hidden = false;
    host.replaceChildren();
    const { building, area, floors, year, roof } = extractBuildingSummary(data);
    const weather = extractWeatherSummary(data);
    const risks = collectRiskSignals(data);
    const diluted = readSmokeMetric("Markant fortyndet");
    const lifted = readSmokeMetric("Røg løftet");
    const smokeConfidence = readSmokeConfidence();

    const header = document.createElement("div");
    header.className = "ib-v4-glance-header";
    const titleWrap = document.createElement("div");
    const kicker = document.createElement("span");
    kicker.className = "ib-v4-kicker";
    kicker.textContent = "INDSATSBLIK";
    const title = document.createElement("h2");
    title.textContent = addressLabel(data);
    const sub = document.createElement("p");
    sub.textContent = `${UI_VERSION}${state.lastModelUpdate ? ` · røgmodel opdateret ${formatClock(state.lastModelUpdate)}` : ""}`;
    titleWrap.append(kicker, title, sub);

    const actions = document.createElement("div");
    actions.className = "ib-v4-glance-actions";
    actions.append(
      makeAction("Kort", () => scrollToId("map-section")),
      makeAction("Opdater røgmodel", refreshSmokeModel),
      makeAction("Kopiér brief", copyBrief),
      makeAction(state.focusMode ? "Vis alle paneler" : "Fokusvisning", () => toggleFocusMode(), "secondary")
    );
    actions.lastElementChild.dataset.ibV4Focus = "true";
    actions.lastElementChild.setAttribute("aria-pressed", state.focusMode ? "true" : "false");
    header.append(titleWrap, actions);
    host.appendChild(header);

    const metrics = document.createElement("div");
    metrics.className = "ib-v4-glance-grid";
    const buildingValue = [
      area !== null ? `${Math.round(area)} m²` : null,
      floors !== null ? `${Math.round(floors)} et.` : null,
      year !== null ? `${Math.round(year)}` : null,
    ].filter(Boolean).join(" · ") || "BBR-data";
    metrics.appendChild(createMetric("Bygning", buildingValue, roof ? `Tag: ${roof}` : ""));

    const windDirection = weather.directionDegrees !== null
      ? `fra ${compass(weather.directionDegrees)}`
      : weather.directionText || "retning ukendt";
    const windValue = weather.speed !== null ? `${weather.speed.toFixed(1)} m/s` : "–";
    const windDetailParts = [windDirection];
    if (weather.gust !== null) windDetailParts.push(`stød ${weather.gust.toFixed(1)} m/s`);
    if (weather.temperature !== null) windDetailParts.push(`${weather.temperature.toFixed(1)} °C`);
    metrics.appendChild(createMetric("Vind", windValue, windDetailParts.join(" · ")));

    metrics.appendChild(createMetric(
      "Fortynding",
      diluted?.value || "afventer model",
      diluted?.detail || smokeConfidence || "Røgmodel indlæses",
      diluted ? "good" : ""
    ));

    metrics.appendChild(createMetric(
      "Røghøjde",
      lifted?.value || "afventer model",
      lifted?.detail || smokeConfidence || "Røgmodel indlæses",
      lifted?.value && !/ikke entydigt/i.test(lifted.value) ? "good" : "warn"
    ));
    host.appendChild(metrics);

    const footer = document.createElement("div");
    footer.className = "ib-v4-glance-footer";
    const riskWrap = document.createElement("div");
    riskWrap.className = "ib-v4-risk-strip";
    if (risks.length) {
      risks.forEach((risk) => {
        const chip = document.createElement("span");
        chip.className = `ib-v4-risk ${risk.level}`;
        chip.textContent = risk.label;
        riskWrap.appendChild(chip);
      });
    } else {
      const chip = document.createElement("span");
      chip.className = "ib-v4-risk good";
      chip.textContent = "Ingen særlige registrerede fund løftet frem";
      riskWrap.appendChild(chip);
    }

    const quality = document.createElement("div");
    quality.className = "ib-v4-quality";
    const source = building.source || "BBR";
    quality.textContent = `${source}${smokeConfidence ? ` · ${smokeConfidence}` : ""}`;
    footer.append(riskWrap, quality);
    host.appendChild(footer);
  }

  function buildingAreaFromIncident() {
    const data = currentIncidentData();
    if (!data) return null;
    return extractBuildingSummary(data).area;
  }

  function setSmokeArea(area) {
    const controls = document.getElementById("ib-smoke-controls");
    const input = controls?.querySelector('input[type="number"]');
    if (!input || !Number.isFinite(area)) return false;
    const bounded = Math.max(1, Math.min(10000, Math.round(area)));
    input.value = String(bounded);
    input.dispatchEvent(new Event("change", { bubbles: true }));
    window.setTimeout(() => enhanceSmokeControls(), 60);
    return true;
  }

  function setSelectByText(partialText, optionMatch) {
    const controls = document.getElementById("ib-smoke-controls");
    if (!controls) return false;
    const labels = Array.from(controls.querySelectorAll("label"));
    const label = labels.find((item) => item.textContent?.toLowerCase().includes(partialText.toLowerCase()));
    const select = label?.querySelector("select");
    if (!select) return false;
    const option = Array.from(select.options).find((item) => optionMatch(item));
    if (!option) return false;
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  function quickScenarioButton(label, area, title) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ib-v4-scenario";
    button.textContent = label;
    button.title = title;
    button.addEventListener("click", () => {
      if (setSmokeArea(area)) {
        flashToast(`Brændende areal sat til ${Math.round(area)} m²`);
      }
    });
    return button;
  }

  function enhanceSmokeControls() {
    const controls = document.getElementById("ib-smoke-controls");
    if (!controls) return;
    let helper = document.getElementById("ib-v4-scenario-helper");
    if (!helper) {
      helper = document.createElement("div");
      helper.id = "ib-v4-scenario-helper";
      controls.insertAdjacentElement("afterend", helper);
    }
    helper.replaceChildren();

    const buildingArea = buildingAreaFromIncident();
    const areaInput = controls.querySelector('input[type="number"]');
    const currentArea = toNumber(areaInput?.value);

    const header = document.createElement("div");
    header.className = "ib-v4-scenario-header";
    const headerText = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = "Scenariehjælp";
    const small = document.createElement("span");
    if (buildingArea !== null && currentArea !== null) {
      const percent = Math.max(0, Math.round((currentArea / buildingArea) * 100));
      small.textContent = `Modelantagelse: ${Math.round(currentArea)} af ${Math.round(buildingArea)} m² (${percent} % af registreret bygningsareal)`;
    } else {
      small.textContent = "Hurtige antagelser til røgmodellen – ikke automatisk vurdering af brandens faktiske udbredelse";
    }
    headerText.append(strong, small);
    header.appendChild(headerText);
    helper.appendChild(header);

    const presets = document.createElement("div");
    presets.className = "ib-v4-scenario-presets";
    presets.append(
      quickScenarioButton("20 m²", 20, "Mindre lokal brand"),
      quickScenarioButton("50 m²", 50, "Større rum/del af bygning"),
      quickScenarioButton("100 m²", 100, "Vejledende 100 m² scenarie")
    );
    if (buildingArea !== null && buildingArea > 0) {
      [0.25, 0.5, 1].forEach((fraction) => {
        const area = Math.max(1, Math.min(10000, buildingArea * fraction));
        presets.appendChild(quickScenarioButton(
          `${Math.round(fraction * 100)} % bygning`,
          area,
          `${Math.round(area)} m² ud fra BBR-arealet`
        ));
      });
    }
    helper.appendChild(presets);

    const profileRow = document.createElement("div");
    profileRow.className = "ib-v4-scenario-secondary";
    const profileLabel = document.createElement("span");
    profileLabel.textContent = "Hurtigvalg:";
    profileRow.appendChild(profileLabel);
    ["Lille brand", "Mellem brand", "Stor brand", "Meget stor brand"].forEach((profileName) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ib-v4-mini-action";
      button.textContent = profileName.replace(" brand", "");
      button.addEventListener("click", () => {
        if (setSelectByText("brandstørrelse", (option) => option.textContent.includes(profileName))) {
          window.setTimeout(() => enhanceSmokeControls(), 90);
        }
      });
      profileRow.appendChild(button);
    });
    ["Lav intensitet", "Normal intensitet", "Høj intensitet"].forEach((intensityName) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ib-v4-mini-action intensity";
      button.textContent = intensityName.replace(" intensitet", "");
      button.addEventListener("click", () => setSelectByText("brandintensitet", (option) => option.textContent.includes(intensityName)));
      profileRow.appendChild(button);
    });
    helper.appendChild(profileRow);

    const note = document.createElement("p");
    note.className = "ib-v4-scenario-note";
    note.textContent = "Scenarieknapper ændrer kun modelantagelser. Brug observeret brandudbredelse og indsatsoplysninger, når de er kendt.";
    helper.appendChild(note);
  }

  function ensureShortcutPanel() {
    let panel = document.getElementById("ib-v4-shortcuts");
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "ib-v4-shortcuts";
    panel.hidden = true;
    panel.innerHTML = `
      <div class="ib-v4-shortcuts-card" role="dialog" aria-modal="true" aria-label="Tastaturgenveje">
        <div class="ib-v4-shortcuts-heading"><strong>Tastaturgenveje</strong><button type="button" aria-label="Luk">×</button></div>
        <div class="ib-v4-shortcut-grid">
          <kbd>/</kbd><span>Fokusér adressesøgning</span>
          <kbd>B</kbd><span>Gå til brief</span>
          <kbd>M</kbd><span>Gå til kort</span>
          <kbd>F</kbd><span>Forstør/luk kort</span>
          <kbd>R</kbd><span>Opdater røgmodel</span>
          <kbd>V</kbd><span>Skift fokusvisning</span>
          <kbd>?</kbd><span>Vis/luk denne hjælp</span>
        </div>
      </div>`;
    panel.querySelector("button")?.addEventListener("click", () => toggleShortcutPanel(false));
    panel.addEventListener("click", (event) => {
      if (event.target === panel) toggleShortcutPanel(false);
    });
    document.body.appendChild(panel);
    return panel;
  }

  function toggleShortcutPanel(force) {
    const panel = ensureShortcutPanel();
    state.shortcutPanelOpen = typeof force === "boolean" ? force : !state.shortcutPanelOpen;
    panel.hidden = !state.shortcutPanelOpen;
  }

  function typingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  }

  function installKeyboardShortcuts() {
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.shortcutPanelOpen) {
        toggleShortcutPanel(false);
        return;
      }
      if (typingTarget(event.target)) {
        if (event.key === "Escape") event.target.blur?.();
        return;
      }
      const key = event.key.toLowerCase();
      if (event.key === "/") {
        event.preventDefault();
        document.getElementById("address")?.focus();
      } else if (key === "b") {
        scrollToId("result");
      } else if (key === "m") {
        scrollToId("map-section");
      } else if (key === "f") {
        const button = document.querySelector(".ib-map-action");
        if (button) button.click();
      } else if (key === "r") {
        refreshSmokeModel();
      } else if (key === "v") {
        toggleFocusMode();
      } else if (event.key === "?") {
        toggleShortcutPanel();
      }
    });
  }

  function addShortcutButton() {
    const nav = document.querySelector(".ib-quick-nav");
    if (!nav || nav.querySelector(".ib-v4-help-button")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ib-v4-help-button";
    button.textContent = "?";
    button.title = "Tastaturgenveje";
    button.addEventListener("click", () => toggleShortcutPanel());
    nav.appendChild(button);
  }

  function observeApplication() {
    const report = document.getElementById("report");
    if (report) {
      new MutationObserver(() => {
        window.requestAnimationFrame(() => {
          renderGlance();
          enhanceSmokeControls();
        });
      }).observe(report, { childList: true, subtree: true, characterData: true });
    }

    const bodyObserver = new MutationObserver((mutations) => {
      let smokeChanged = false;
      let navChanged = false;
      for (const mutation of mutations) {
        if (!(mutation.target instanceof Element)) continue;
        if (mutation.target.closest?.("#ib-smoke-summary") || mutation.target.id === "ib-smoke-summary") smokeChanged = true;
        if (mutation.target.closest?.(".ib-quick-nav") || mutation.target.classList?.contains("ib-quick-nav")) navChanged = true;
      }
      if (smokeChanged) {
        state.lastModelUpdate = new Date();
        window.requestAnimationFrame(() => {
          renderGlance();
          enhanceSmokeControls();
        });
      }
      if (navChanged) addShortcutButton();
      if (!document.getElementById("ib-v4-scenario-helper") && document.getElementById("ib-smoke-controls")) {
        enhanceSmokeControls();
      }
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function resetForNewAddressIfNeeded() {
    const input = document.getElementById("address");
    if (!input) return;
    const update = () => {
      const key = input.value.trim().toLowerCase();
      if (state.lastAddressKey !== null && key !== state.lastAddressKey && !currentIncidentData()) {
        state.lastModelUpdate = null;
        const host = document.getElementById("ib-operational-glance");
        if (host) host.hidden = true;
      }
      state.lastAddressKey = key;
    };
    input.addEventListener("input", update);
    update();
  }

  function start() {
    document.documentElement.dataset.ibOperationalIntelligence = "v4";
    ensureGlanceHost();
    ensureShortcutPanel();
    installKeyboardShortcuts();
    observeApplication();
    resetForNewAddressIfNeeded();
    addShortcutButton();
    renderGlance();
    enhanceSmokeControls();
    window.setTimeout(() => {
      addShortcutButton();
      renderGlance();
      enhanceSmokeControls();
    }, 700);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
