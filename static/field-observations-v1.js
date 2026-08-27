(() => {
  "use strict";

  const state = {
    incident: null,
    originalWeather: null,
    hadOwnWeather: false,
    active: false,
    appliedAt: null,
    speed: null,
    direction: null,
    gust: null,
  };

  const toNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };

  function currentIncidentData() {
    try {
      return typeof latestIncidentData !== "undefined" && latestIncidentData ? latestIncidentData : null;
    } catch (_) {
      return null;
    }
  }

  function sourceWeather(data) {
    return data?.weather || data?.short_report_data?.weather || {};
  }

  function weatherValue(weather, ...keys) {
    for (const key of keys) {
      const value = toNumber(weather?.[key]);
      if (value !== null) return value;
    }
    return null;
  }

  function formatClock(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "–";
    return new Intl.DateTimeFormat("da-DK", { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function directionName(degrees) {
    const value = toNumber(degrees);
    if (value === null) return "–";
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    const normalized = ((value % 360) + 360) % 360;
    return labels[Math.round(normalized / 22.5) % 16];
  }

  function refreshSmokeModel() {
    const frame = document.getElementById("map-frame");
    const src = frame?.getAttribute("src");
    if (!frame || !src) return;
    try {
      const url = new URL(src, window.location.href);
      url.searchParams.set("ib_field_refresh", String(Date.now()));
      frame.setAttribute("src", url.toString());
    } catch (_) {
      frame.setAttribute("src", src);
    }
  }

  function ensureIncidentState(data) {
    if (!data) return;
    if (state.incident === data) return;
    state.incident = data;
    state.originalWeather = null;
    state.hadOwnWeather = false;
    state.active = false;
    state.appliedAt = null;
    state.speed = null;
    state.direction = null;
    state.gust = null;
  }

  function prepareBackup(data) {
    if (state.originalWeather !== null) return;
    state.hadOwnWeather = Object.prototype.hasOwnProperty.call(data, "weather") && data.weather && typeof data.weather === "object";
    const existing = sourceWeather(data);
    state.originalWeather = { ...(existing || {}) };
  }

  function ensureMutableWeather(data) {
    if (!data.weather || typeof data.weather !== "object") {
      data.weather = { ...(data?.short_report_data?.weather || {}) };
    }
    return data.weather;
  }

  function applyOverride(speed, direction, gust) {
    const data = currentIncidentData();
    if (!data) return false;
    ensureIncidentState(data);
    prepareBackup(data);
    const weather = ensureMutableWeather(data);
    weather.wind_speed_ms = speed;
    weather.wind_direction_degrees = direction;
    if (gust !== null) weather.wind_gust_ms = Math.max(gust, speed);
    else delete weather.wind_gust_ms;
    weather.__ib_field_observation = {
      speed_ms: speed,
      direction_from_degrees: direction,
      gust_ms: gust,
      applied_at: new Date().toISOString(),
    };
    state.active = true;
    state.appliedAt = new Date();
    state.speed = speed;
    state.direction = direction;
    state.gust = gust;
    renderStatus();
    renderPanel();
    refreshSmokeModel();
    return true;
  }

  function clearOverride() {
    const data = currentIncidentData();
    if (!data || state.incident !== data || state.originalWeather === null) {
      state.active = false;
      renderStatus();
      renderPanel();
      return;
    }
    if (state.hadOwnWeather) data.weather = { ...state.originalWeather };
    else delete data.weather;
    state.active = false;
    state.appliedAt = null;
    state.speed = null;
    state.direction = null;
    state.gust = null;
    renderStatus();
    renderPanel();
    refreshSmokeModel();
  }

  function ensurePanelHost() {
    let panel = document.getElementById("ib-field-observations");
    if (panel) return panel;
    const anchor = document.getElementById("ib-v4-scenario-helper") || document.getElementById("ib-smoke-controls");
    if (!anchor) return null;
    panel = document.createElement("details");
    panel.id = "ib-field-observations";
    panel.className = "ib-field-observations";
    const summary = document.createElement("summary");
    summary.textContent = "Lokale observationer · vindoverride";
    panel.appendChild(summary);
    anchor.insertAdjacentElement("afterend", panel);
    return panel;
  }

  function labeledNumber(labelText, unitText, min, max, step) {
    const label = document.createElement("label");
    label.className = "ib-field-input";
    const span = document.createElement("span");
    span.textContent = labelText;
    const wrap = document.createElement("div");
    wrap.className = "ib-field-input-wrap";
    const input = document.createElement("input");
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    const unit = document.createElement("small");
    unit.textContent = unitText;
    wrap.append(input, unit);
    label.append(span, wrap);
    return { label, input };
  }

  function renderPanel() {
    const data = currentIncidentData();
    if (!data) return;
    ensureIncidentState(data);
    const panel = ensurePanelHost();
    if (!panel) return;
    const open = panel.open;
    const summary = panel.querySelector("summary") || document.createElement("summary");
    summary.textContent = state.active
      ? `Lokale observationer · AKTIV ${state.speed?.toFixed(1)} m/s fra ${directionName(state.direction)}`
      : "Lokale observationer · vindoverride";
    panel.replaceChildren(summary);
    panel.open = open || state.active;

    const content = document.createElement("div");
    content.className = "ib-field-content";
    const intro = document.createElement("p");
    intro.className = "ib-field-intro";
    intro.textContent = "Brug dette, hvis I har en bedre lokal observation end prognosen. Retningen angives som den retning vinden kommer FRA.";
    content.appendChild(intro);

    const apiWeather = state.active && state.originalWeather ? state.originalWeather : sourceWeather(data);
    const apiSpeed = weatherValue(apiWeather, "wind_speed_ms", "wind_speed_10m");
    const apiDirection = weatherValue(apiWeather, "wind_direction_degrees", "wind_direction_10m");
    const apiGust = weatherValue(apiWeather, "wind_gust_ms", "wind_gusts_10m");

    const form = document.createElement("div");
    form.className = "ib-field-grid";
    const speedField = labeledNumber("Vindhastighed", "m/s", 0, 60, 0.1);
    const directionField = labeledNumber("Vind fra", "°", 0, 359, 1);
    const gustField = labeledNumber("Vindstød (valgfri)", "m/s", 0, 80, 0.1);
    speedField.input.value = String(state.active ? state.speed : (apiSpeed ?? ""));
    directionField.input.value = String(state.active ? state.direction : (apiDirection ?? ""));
    gustField.input.value = String(state.active && state.gust !== null ? state.gust : (apiGust ?? ""));
    form.append(speedField.label, directionField.label, gustField.label);
    content.appendChild(form);

    const compass = document.createElement("div");
    compass.className = "ib-field-compass";
    const compassTitle = document.createElement("span");
    compassTitle.textContent = "Hurtig retning fra:";
    compass.appendChild(compassTitle);
    [
      ["N", 0], ["NØ", 45], ["Ø", 90], ["SØ", 135],
      ["S", 180], ["SV", 225], ["V", 270], ["NV", 315],
    ].forEach(([label, degrees]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => { directionField.input.value = String(degrees); });
      compass.appendChild(button);
    });
    content.appendChild(compass);

    const comparison = document.createElement("div");
    comparison.className = "ib-field-api-reference";
    const apiDirectionText = apiDirection !== null ? `${directionName(apiDirection)} (${Math.round(apiDirection)}°)` : "–";
    comparison.textContent = `API-reference: ${apiSpeed !== null ? `${apiSpeed.toFixed(1)} m/s` : "–"} fra ${apiDirectionText}${apiGust !== null ? ` · stød ${apiGust.toFixed(1)} m/s` : ""}`;
    content.appendChild(comparison);

    const actions = document.createElement("div");
    actions.className = "ib-field-actions";
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "ib-field-apply";
    apply.textContent = "Brug lokal vind i røgmodel";
    apply.addEventListener("click", () => {
      const speed = toNumber(speedField.input.value);
      const direction = toNumber(directionField.input.value);
      const gust = toNumber(gustField.input.value);
      if (speed === null || speed < 0 || speed > 60 || direction === null || direction < 0 || direction >= 360) {
        comparison.textContent = "Indtast gyldig vindhastighed og retning 0–359°.";
        comparison.classList.add("error");
        return;
      }
      comparison.classList.remove("error");
      applyOverride(speed, direction, gust !== null ? gust : null);
    });
    actions.appendChild(apply);
    if (state.active) {
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "ib-field-clear";
      clear.textContent = "Brug API-vind igen";
      clear.addEventListener("click", clearOverride);
      actions.appendChild(clear);
    }
    content.appendChild(actions);

    const warning = document.createElement("p");
    warning.className = "ib-field-warning";
    warning.textContent = "Override gælder kun den lokale browser-session og røgmodellen. Den ændrer ikke de oprindelige vejrlinjer i den genererede brief.";
    content.appendChild(warning);
    panel.appendChild(content);
  }

  function ensureStatusHost() {
    let status = document.getElementById("ib-field-wind-status");
    if (status) return status;
    const summary = document.getElementById("ib-smoke-summary");
    const controls = document.getElementById("ib-smoke-controls");
    if (!summary && !controls) return null;
    status = document.createElement("div");
    status.id = "ib-field-wind-status";
    status.hidden = true;
    if (summary) summary.insertAdjacentElement("beforebegin", status);
    else controls.insertAdjacentElement("beforebegin", status);
    return status;
  }

  function renderStatus() {
    const status = ensureStatusHost();
    if (!status) return;
    if (!state.active) {
      status.hidden = true;
      status.replaceChildren();
      return;
    }
    status.hidden = false;
    status.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = "LOKAL VIND AKTIV";
    const text = document.createElement("span");
    text.textContent = `${state.speed.toFixed(1)} m/s fra ${directionName(state.direction)} (${Math.round(state.direction)}°)${state.gust !== null ? ` · stød ${state.gust.toFixed(1)} m/s` : ""} · sat ${formatClock(state.appliedAt)}`;
    const clear = document.createElement("button");
    clear.type = "button";
    clear.textContent = "Nulstil";
    clear.addEventListener("click", clearOverride);
    status.append(strong, text, clear);
  }

  function syncGlanceChip() {
    const glance = document.getElementById("ib-operational-glance");
    if (!glance) return;
    let chip = glance.querySelector(".ib-field-glance-chip");
    if (!state.active) {
      if (chip) chip.remove();
      return;
    }
    const strip = glance.querySelector(".ib-v4-risk-strip");
    if (!strip) return;
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "ib-v4-risk info ib-field-glance-chip";
      strip.prepend(chip);
    }
    const desired = `Lokal vind ${state.speed.toFixed(1)} m/s fra ${directionName(state.direction)}`;
    if (chip.textContent !== desired) chip.textContent = desired;
  }

  function observePage() {
    let scheduled = false;
    const observer = new MutationObserver((mutations) => {
      const hasExternalMutation = mutations.some((mutation) => {
        const target = mutation.target instanceof Element
          ? mutation.target
          : mutation.target?.parentElement;
        if (!target) return true;
        return !target.closest("#ib-field-observations, #ib-field-wind-status, .ib-field-glance-chip");
      });
      if (!hasExternalMutation || scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        const data = currentIncidentData();
        const incidentChanged = Boolean(data && data !== state.incident);
        if (incidentChanged) {
          ensureIncidentState(data);
          renderPanel();
          renderStatus();
        } else if (
          data &&
          !document.getElementById("ib-field-observations") &&
          document.getElementById("ib-smoke-controls")
        ) {
          renderPanel();
        }
        if (state.active && !document.getElementById("ib-field-wind-status")) renderStatus();
        syncGlanceChip();
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function start() {
    const data = currentIncidentData();
    if (data) ensureIncidentState(data);
    renderPanel();
    renderStatus();
    syncGlanceChip();
    observePage();
    window.setTimeout(() => {
      renderPanel();
      renderStatus();
      syncGlanceChip();
    }, 800);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
