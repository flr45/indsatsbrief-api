(() => {
  "use strict";

  const state = {
    map: null,
    L: null,
    layer: null,
    control: null,
    controlButton: null,
    controlCount: null,
    places: [],
    payload: null,
    visible: true,
    loading: false,
  };

  const nativeFetch = typeof window.fetch === "function" ? window.fetch.bind(window) : null;

  function toNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function safeCategory(value) {
    const category = String(value || "other").toLowerCase();
    return [
      "hospital", "healthcare", "childcare", "school", "education",
      "care", "social", "institution", "other",
    ].includes(category) ? category : "other";
  }

  function categoryGlyph(category) {
    return ({
      hospital: "✚",
      healthcare: "+",
      childcare: "●",
      school: "S",
      education: "U",
      care: "♥",
      social: "◆",
      institution: "■",
      other: "●",
    })[safeCategory(category)] || "●";
  }

  function formatDistance(meters) {
    const value = toNumber(meters);
    if (value === null) return "–";
    if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)} km`;
    return `${Math.round(value / 10) * 10} m`;
  }

  function cardinal(degrees) {
    const value = toNumber(degrees);
    if (value === null) return "–";
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    const normalized = ((value % 360) + 360) % 360;
    return labels[Math.round(normalized / 22.5) % 16];
  }

  function contextRequestUrl(input) {
    try {
      const raw = typeof input === "string" ? input : input?.url;
      if (!raw) return null;
      const url = new URL(raw, window.location.href);
      return url.pathname === "/api/smoke-context" ? url : null;
    } catch (_) {
      return null;
    }
  }

  function clearLayer() {
    if (state.layer) state.layer.clearLayers();
  }

  function clearPlaces() {
    state.places = [];
    state.payload = null;
    state.loading = false;
    clearLayer();
    updateControl();
  }

  function ensureLayer() {
    if (!state.map || !state.L) return null;
    if (!state.layer) state.layer = state.L.layerGroup().addTo(state.map);
    return state.layer;
  }

  function osmUrl(lat, lon) {
    return `https://www.openstreetmap.org/?mlat=${encodeURIComponent(lat)}&mlon=${encodeURIComponent(lon)}#map=17/${encodeURIComponent(lat)}/${encodeURIComponent(lon)}`;
  }

  function popupHtml(place) {
    const lat = toNumber(place.latitude);
    const lon = toNumber(place.longitude);
    const name = escapeHtml(place.name || place.category_label || "OSM-fund");
    const category = escapeHtml(place.category_label || "Sårbart sted");
    const distance = escapeHtml(formatDistance(place.distance_m));
    const bearing = escapeHtml(cardinal(place.bearing_deg));
    const offset = toNumber(place.sector_offset_deg);
    const offsetText = offset === null ? "" : ` · ${Math.round(offset)}° fra sektorcenter`;
    const link = lat === null || lon === null
      ? ""
      : `<br><a href="${osmUrl(lat, lon)}" target="_blank" rel="noopener noreferrer">Åbn i OpenStreetMap</a>`;
    return (
      `<strong>${name}</strong><br>` +
      `${category} · ${distance} · ${bearing}${escapeHtml(offsetText)}` +
      `<br><small>Vindsektor-fund – ikke dokumenteret røgpåvirkning.</small>${link}`
    );
  }

  function renderPlaces() {
    const layer = ensureLayer();
    if (!layer) return;
    layer.clearLayers();
    if (!state.visible || !state.places.length) {
      updateControl();
      return;
    }

    state.places.slice(0, 25).forEach((place) => {
      const lat = toNumber(place?.latitude);
      const lon = toNumber(place?.longitude);
      if (lat === null || lon === null) return;
      const category = safeCategory(place.category);
      const glyph = escapeHtml(categoryGlyph(category));
      const icon = state.L.divIcon({
        className: "ib-context-map-pin-shell",
        html: `<span class="ib-context-map-pin ${category}" aria-hidden="true">${glyph}</span>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
        popupAnchor: [0, -14],
      });
      const marker = state.L.marker([lat, lon], { icon, keyboard: true });
      marker.bindTooltip(
        `${escapeHtml(place.name || place.category_label || "OSM-fund")} · ${escapeHtml(formatDistance(place.distance_m))}`,
        { direction: "top", opacity: 0.96 }
      );
      marker.bindPopup(popupHtml(place), { maxWidth: 310 });
      marker.addTo(layer);
    });
    updateControl();
  }

  function ensureControl() {
    if (!state.map || !state.L || state.control) return;
    const ContextControl = state.L.Control.extend({
      options: { position: "topright" },
      onAdd() {
        const container = state.L.DomUtil.create("div", "ib-context-map-control leaflet-bar");
        container.hidden = true;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ib-context-map-toggle";
        button.title = "Vis/skjul sårbare steder fra Røgkontekst";
        const label = document.createElement("span");
        label.textContent = "Sårbare steder";
        const count = document.createElement("strong");
        count.textContent = "0";
        button.append(label, count);
        button.addEventListener("click", () => {
          state.visible = !state.visible;
          button.setAttribute("aria-pressed", state.visible ? "true" : "false");
          renderPlaces();
        });
        button.setAttribute("aria-pressed", "true");
        state.L.DomEvent.disableClickPropagation(container);
        state.L.DomEvent.disableScrollPropagation(container);
        container.appendChild(button);
        state.controlButton = button;
        state.controlCount = count;
        return container;
      },
    });
    state.control = new ContextControl();
    state.control.addTo(state.map);
  }

  function updateControl() {
    const container = state.control?._container;
    if (!container) return;
    const count = state.places.length;
    container.hidden = count === 0 && !state.loading;
    if (state.controlCount) state.controlCount.textContent = state.loading ? "…" : String(count);
    if (state.controlButton) {
      state.controlButton.classList.toggle("inactive", !state.visible);
      state.controlButton.classList.toggle("loading", state.loading);
      state.controlButton.setAttribute("aria-pressed", state.visible ? "true" : "false");
      state.controlButton.title = state.loading
        ? "Røgkontekst scanner …"
        : `${state.visible ? "Skjul" : "Vis"} ${count} sårbare steder fra Røgkontekst`;
    }
  }

  function attachMap(mapInstance) {
    if (!mapInstance || state.map === mapInstance) return;
    state.map = mapInstance;
    state.L = window.L;
    state.layer = null;
    state.control = null;
    state.controlButton = null;
    state.controlCount = null;
    ensureLayer();
    ensureControl();
    renderPlaces();
    window.dispatchEvent(new CustomEvent("indsatsbrief:smoke-map-bridge-ready"));
  }

  function patchLeafletMapFactory() {
    if (!window.L?.map || window.L.map.__ibContextBridgeWrapped) return Boolean(window.L?.map);
    const original = window.L.map;
    const wrapped = function (...args) {
      const instance = original.apply(this, args);
      attachMap(instance);
      return instance;
    };
    wrapped.__ibContextBridgeWrapped = true;
    wrapped.__ibContextBridgeOriginal = original;
    window.L.map = wrapped;
    return true;
  }

  function watchLeafletLoader() {
    if (patchLeafletMapFactory()) return;
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes || []) {
          if (!(node instanceof HTMLScriptElement)) continue;
          const src = node.getAttribute("src") || "";
          if (!src.includes("leaflet") || !src.endsWith("leaflet.js")) continue;
          node.addEventListener("load", () => patchLeafletMapFactory(), { once: true });
        }
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(() => {
      if (patchLeafletMapFactory()) observer.disconnect();
    }, 2500);
  }

  function installFetchBridge() {
    if (!nativeFetch || window.fetch?.__ibContextMapWrapped) return;
    const wrappedFetch = async (...args) => {
      const contextUrl = contextRequestUrl(args[0]);
      if (!contextUrl) return nativeFetch(...args);

      state.loading = true;
      clearLayer();
      updateControl();
      try {
        const response = await nativeFetch(...args);
        response.clone().json().then((payload) => {
          state.loading = false;
          if (payload?.ok && Array.isArray(payload.places)) {
            state.payload = payload;
            state.places = payload.places.filter((place) => (
              toNumber(place?.latitude) !== null && toNumber(place?.longitude) !== null
            ));
            state.visible = true;
            renderPlaces();
          } else {
            clearPlaces();
          }
        }).catch(() => clearPlaces());
        return response;
      } catch (error) {
        clearPlaces();
        throw error;
      }
    };
    wrappedFetch.__ibContextMapWrapped = true;
    wrappedFetch.__ibContextMapOriginal = nativeFetch;
    window.fetch = wrappedFetch;
  }

  function watchIncidentChanges() {
    const install = () => {
      const frame = document.getElementById("map-frame");
      if (!frame || frame.__ibContextMapObserved) return;
      frame.__ibContextMapObserved = true;
      new MutationObserver(() => clearPlaces())
        .observe(frame, { attributes: true, attributeFilter: ["src"] });
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", install, { once: true });
    } else {
      install();
    }
  }

  window.IndsatsBriefSmokeMapBridge = {
    clear: clearPlaces,
    getMap: () => state.map,
    getPlaces: () => state.places.slice(),
    setVisible(value) {
      state.visible = Boolean(value);
      renderPlaces();
    },
  };

  installFetchBridge();
  watchLeafletLoader();
  watchIncidentChanges();
})();
