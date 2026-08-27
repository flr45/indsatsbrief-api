(() => {
  "use strict";

  const state = {
    incident: null,
    loading: false,
    result: null,
    stale: false,
    scannedDirection: null,
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

  function coordinates(data) {
    const short = data?.short_report_data || {};
    const source = short.coordinates || {};
    const latitude = toNumber(data?.latitude ?? source.latitude);
    const longitude = toNumber(data?.longitude ?? source.longitude);
    if (latitude === null || longitude === null) return null;
    return { latitude, longitude };
  }

  function weather(data) {
    return data?.weather || data?.short_report_data?.weather || {};
  }

  function windFromDegrees(data) {
    const source = weather(data);
    return toNumber(source.wind_direction_degrees ?? source.wind_direction_10m);
  }

  function windToDegrees(data) {
    const from = windFromDegrees(data);
    return from === null ? null : ((from + 180) % 360 + 360) % 360;
  }

  function cardinal(degrees) {
    const value = toNumber(degrees);
    if (value === null) return "–";
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    const normalized = ((value % 360) + 360) % 360;
    return labels[Math.round(normalized / 22.5) % 16];
  }

  function formatDistance(meters) {
    const number = toNumber(meters);
    if (number === null) return "–";
    if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)} km`;
    return `${Math.round(number / 10) * 10} m`;
  }

  function resetForIncident(data) {
    if (data === state.incident) return;
    state.incident = data;
    state.loading = false;
    state.result = null;
    state.stale = false;
    state.scannedDirection = null;
  }

  function ensurePanel() {
    let panel = document.getElementById("ib-smoke-context-panel");
    if (panel) return panel;
    const anchor = document.getElementById("ib-field-observations")
      || document.getElementById("ib-v4-scenario-helper")
      || document.getElementById("ib-smoke-controls");
    if (!anchor) return null;
    panel = document.createElement("section");
    panel.id = "ib-smoke-context-panel";
    panel.setAttribute("aria-label", "Sårbare steder i røgretning");
    anchor.insertAdjacentElement("afterend", panel);
    addNavigationLink();
    return panel;
  }

  function addNavigationLink() {
    const nav = document.querySelector(".ib-quick-nav");
    if (!nav || nav.querySelector('[data-target="ib-smoke-context-panel"]')) return;
    const help = nav.querySelector(".ib-v4-help-button");
    const link = document.createElement("a");
    link.href = "#ib-smoke-context-panel";
    link.dataset.target = "ib-smoke-context-panel";
    link.textContent = "Røgretning";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      document.getElementById("ib-smoke-context-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    if (help) nav.insertBefore(link, help);
    else nav.appendChild(link);
  }

  function createSelect(values, selected) {
    const select = document.createElement("select");
    values.forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = label;
      option.selected = Number(value) === Number(selected);
      select.appendChild(option);
    });
    return select;
  }

  function categoryIcon(category) {
    return ({
      hospital: "✚",
      healthcare: "+",
      childcare: "◉",
      school: "▣",
      education: "▤",
      care: "♥",
      social: "◆",
      institution: "■",
    })[category] || "●";
  }

  function osmUrl(place) {
    const lat = toNumber(place?.latitude);
    const lon = toNumber(place?.longitude);
    if (lat === null || lon === null) return null;
    return `https://www.openstreetmap.org/?mlat=${encodeURIComponent(lat)}&mlon=${encodeURIComponent(lon)}#map=17/${encodeURIComponent(lat)}/${encodeURIComponent(lon)}`;
  }

  function renderResults(container) {
    if (state.loading) {
      const loading = document.createElement("div");
      loading.className = "ib-context-loading";
      loading.textContent = "Scanner OpenStreetMap i vindsektoren …";
      container.appendChild(loading);
      return;
    }

    if (!state.result) {
      const empty = document.createElement("div");
      empty.className = "ib-context-empty";
      empty.textContent = "Tryk Scan røgretning for at finde udvalgte sårbare steder i vindsektoren.";
      container.appendChild(empty);
      return;
    }

    if (!state.result.ok) {
      const error = document.createElement("div");
      error.className = "ib-context-error";
      error.textContent = state.result.error || "OSM-screeningen kunne ikke hentes.";
      container.appendChild(error);
      return;
    }

    const summary = document.createElement("div");
    summary.className = "ib-context-result-summary";
    const strong = document.createElement("strong");
    strong.textContent = `${state.result.sector_count || 0} fund i sektoren`;
    const detail = document.createElement("span");
    detail.textContent = `${state.result.nearby_count || 0} relevante OSM-objekter undersøgt i ${formatDistance(state.result.radius_m)}`;
    summary.append(strong, detail);
    if (state.stale) {
      const stale = document.createElement("em");
      stale.textContent = "Vind/model er ændret – scan igen";
      summary.appendChild(stale);
    }
    container.appendChild(summary);

    const categories = state.result.categories || {};
    if (Object.keys(categories).length) {
      const chips = document.createElement("div");
      chips.className = "ib-context-category-chips";
      Object.entries(categories).forEach(([label, count]) => {
        const chip = document.createElement("span");
        chip.textContent = `${label}: ${count}`;
        chips.appendChild(chip);
      });
      container.appendChild(chips);
    }

    const places = Array.isArray(state.result.places) ? state.result.places : [];
    if (!places.length) {
      const none = document.createElement("div");
      none.className = "ib-context-none";
      none.textContent = "Ingen af de valgte stedtyper blev fundet i den aktuelle sektor.";
      container.appendChild(none);
      return;
    }

    const list = document.createElement("div");
    list.className = "ib-context-list";
    places.slice(0, 12).forEach((place) => {
      const item = document.createElement("article");
      item.className = "ib-context-place";
      const icon = document.createElement("span");
      icon.className = "ib-context-place-icon";
      icon.textContent = categoryIcon(place.category);
      const body = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = place.name || place.category_label || "OSM-fund";
      const meta = document.createElement("span");
      meta.textContent = `${place.category_label || "Sted"} · ${formatDistance(place.distance_m)} · ${cardinal(place.bearing_deg)} · ${Math.round(place.sector_offset_deg || 0)}° fra sektorens center`;
      body.append(name, meta);
      const linkUrl = osmUrl(place);
      if (linkUrl) {
        const link = document.createElement("a");
        link.href = linkUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "OSM";
        item.append(icon, body, link);
      } else {
        item.append(icon, body);
      }
      list.appendChild(item);
    });
    container.appendChild(list);
  }

  function renderPanel() {
    const data = currentIncidentData();
    if (!data) return;
    resetForIncident(data);
    const panel = ensurePanel();
    if (!panel) return;
    panel.replaceChildren();

    const direction = windToDegrees(data);
    const heading = document.createElement("div");
    heading.className = "ib-context-heading";
    const titleWrap = document.createElement("div");
    const kicker = document.createElement("span");
    kicker.textContent = "RØGKONTEKST";
    const title = document.createElement("strong");
    title.textContent = "Sårbare steder i forventet røgretning";
    const subtitle = document.createElement("span");
    subtitle.textContent = direction === null
      ? "Vindretning mangler – screening kan ikke beregnes endnu"
      : `Aktuel vindsektor mod ${cardinal(direction)} (${Math.round(direction)}°)`;
    titleWrap.append(kicker, title, subtitle);
    heading.appendChild(titleWrap);
    panel.appendChild(heading);

    const controls = document.createElement("div");
    controls.className = "ib-context-controls";
    const radiusLabel = document.createElement("label");
    radiusLabel.innerHTML = "<span>Afstand</span>";
    const radiusSelect = createSelect([[2000, "2 km"], [5000, "5 km"], [10000, "10 km"]], state.result?.radius_m || 5000);
    radiusLabel.appendChild(radiusSelect);

    const angleLabel = document.createElement("label");
    angleLabel.innerHTML = "<span>Sektorbredde</span>";
    const angleSelect = createSelect([[30, "±30°"], [45, "±45°"], [60, "±60°"]], state.result?.half_angle_deg || 45);
    angleLabel.appendChild(angleSelect);

    const scan = document.createElement("button");
    scan.type = "button";
    scan.className = "ib-context-scan";
    scan.textContent = state.loading ? "Scanner …" : "Scan røgretning";
    scan.disabled = state.loading || direction === null;
    scan.addEventListener("click", () => scanContext(Number(radiusSelect.value), Number(angleSelect.value)));
    controls.append(radiusLabel, angleLabel, scan);
    panel.appendChild(controls);

    const results = document.createElement("div");
    results.className = "ib-context-results";
    renderResults(results);
    panel.appendChild(results);

    const note = document.createElement("p");
    note.className = "ib-context-note";
    note.textContent = "Screeningen bruger en vindsektor – ikke den præcise røgpolygon. Fund betyder derfor ‘i forventet retning’, ikke dokumenteret røgpåvirkning. OSM kan være ufuldstændigt.";
    panel.appendChild(note);
  }

  async function scanContext(radiusM, halfAngle) {
    const data = currentIncidentData();
    const point = coordinates(data);
    const direction = windToDegrees(data);
    if (!data || !point || direction === null || state.loading) return;
    resetForIncident(data);
    state.loading = true;
    state.stale = false;
    renderPanel();
    try {
      const params = new URLSearchParams({
        lat: String(point.latitude),
        lon: String(point.longitude),
        direction: String(direction),
        radius_m: String(radiusM),
        half_angle: String(halfAngle),
      });
      const response = await fetch(`/api/smoke-context?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const payload = await response.json().catch(() => ({ ok: false, error: "Ugyldigt svar fra serveren." }));
      if (!response.ok && payload.ok !== false) payload.ok = false;
      state.result = payload;
      state.scannedDirection = direction;
    } catch (_) {
      state.result = { ok: false, error: "Røgkontekst kunne ikke hentes." };
    } finally {
      state.loading = false;
      renderPanel();
    }
  }

  function observeChanges() {
    const report = document.getElementById("report");
    if (report) {
      new MutationObserver(() => {
        const data = currentIncidentData();
        const changed = data && data !== state.incident;
        if (changed) {
          resetForIncident(data);
          renderPanel();
        }
      }).observe(report, { childList: true, subtree: true });
    }

    const frame = document.getElementById("map-frame");
    if (frame) {
      new MutationObserver(() => {
        if (!state.result || state.loading) return;
        const currentDirection = windToDegrees(currentIncidentData());
        if (currentDirection === null || state.scannedDirection === null) return;
        const diff = Math.abs((((currentDirection - state.scannedDirection) + 540) % 360) - 180);
        if (diff >= 5 || frame.getAttribute("src")) {
          state.stale = true;
          renderPanel();
        }
      }).observe(frame, { attributes: true, attributeFilter: ["src"] });
    }
  }

  function start() {
    const data = currentIncidentData();
    if (data) resetForIncident(data);
    renderPanel();
    addNavigationLink();
    observeChanges();
    window.setTimeout(() => {
      renderPanel();
      addNavigationLink();
    }, 900);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
