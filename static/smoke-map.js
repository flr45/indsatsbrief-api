(() => {
  "use strict";

  const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  const LEAFLET_CSS_INTEGRITY = "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
  const LEAFLET_JS_INTEGRITY = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
  const EARTH_RADIUS_M = 6371008.8;
  const SMOKE_MIN_DIRECTIONAL_WIND_MS = 1.0;
  const TIME_BANDS_MINUTES = [5, 10, 15];
  const weatherCache = new Map();

  let leafletPromise = null;
  let operationalMap = null;
  let incidentLayer = null;
  let smokeLayer = null;
  let mapFrame = null;
  let mapHost = null;
  let summaryHost = null;
  let smokeVisible = true;
  let renderSequence = 0;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const toNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const degreesToRadians = (degrees) => (degrees * Math.PI) / 180;
  const radiansToDegrees = (radians) => (radians * 180) / Math.PI;
  const normalizeBearing = (degrees) => ((degrees % 360) + 360) % 360;

  function currentIncidentData() {
    try {
      return typeof latestIncidentData !== "undefined" && latestIncidentData
        ? latestIncidentData
        : null;
    } catch (_) {
      return null;
    }
  }

  function extractCoordinatesFromData(data) {
    if (!data || typeof data !== "object") return null;
    const short = data.short_report_data || {};
    const coordinates = short.coordinates || {};
    const latitude = toNumber(data.latitude ?? coordinates.latitude);
    const longitude = toNumber(data.longitude ?? coordinates.longitude);
    if (latitude === null || longitude === null) return null;
    return { latitude, longitude };
  }

  function extractCoordinatesFromFrame(frame) {
    const src = frame?.getAttribute("src");
    if (!src) return null;
    try {
      const url = new URL(src, window.location.href);
      const marker = url.searchParams.get("marker");
      if (!marker) return null;
      const [latitudeValue, longitudeValue] = marker.split(",");
      const latitude = toNumber(latitudeValue);
      const longitude = toNumber(longitudeValue);
      if (latitude === null || longitude === null) return null;
      return { latitude, longitude };
    } catch (_) {
      return null;
    }
  }

  function extractWeather(data) {
    if (!data || typeof data !== "object") return {};
    return data.weather || data.short_report_data?.weather || {};
  }

  function cardinalDirection(degrees) {
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    const normalized = normalizeBearing(degrees);
    return labels[Math.round(normalized / 22.5) % 16];
  }

  function destinationPoint(latitude, longitude, bearingDegrees, distanceMeters) {
    const angularDistance = distanceMeters / EARTH_RADIUS_M;
    const bearing = degreesToRadians(bearingDegrees);
    const lat1 = degreesToRadians(latitude);
    const lon1 = degreesToRadians(longitude);

    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing)
    );
    const lon2 = lon1 + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2)
    );

    return [radiansToDegrees(lat2), radiansToDegrees(lon2)];
  }

  function sectorBand(latitude, longitude, bearing, halfAngle, innerRadius, outerRadius) {
    const points = [];
    const step = Math.max(2, Math.min(5, halfAngle / 4));
    for (let angle = bearing - halfAngle; angle <= bearing + halfAngle + 0.001; angle += step) {
      points.push(destinationPoint(latitude, longitude, angle, outerRadius));
    }

    if (innerRadius > 0) {
      for (let angle = bearing + halfAngle; angle >= bearing - halfAngle - 0.001; angle -= step) {
        points.push(destinationPoint(latitude, longitude, angle, innerRadius));
      }
    } else {
      points.push([latitude, longitude]);
    }

    return points;
  }

  function estimatedHalfAngle(windSpeed, windGust) {
    let halfAngle;
    if (windSpeed < 1.5) halfAngle = 42;
    else if (windSpeed < 3) halfAngle = 31;
    else if (windSpeed < 6) halfAngle = 23;
    else if (windSpeed < 10) halfAngle = 17;
    else halfAngle = 14;

    if (windGust && windSpeed > 0 && windGust / windSpeed >= 1.55) {
      halfAngle += 6;
    }
    return clamp(halfAngle, 12, 48);
  }

  function weatherValue(weather, ...keys) {
    for (const key of keys) {
      const value = toNumber(weather?.[key]);
      if (value !== null) return value;
    }
    return null;
  }

  async function fetchMapWeather(latitude, longitude) {
    const key = `${latitude.toFixed(3)},${longitude.toFixed(3)}`;
    if (weatherCache.has(key)) return weatherCache.get(key);

    const promise = (async () => {
      try {
        const params = new URLSearchParams({
          latitude: String(latitude),
          longitude: String(longitude),
          current: "relative_humidity_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
          wind_speed_unit: "ms",
          timezone: "Europe/Copenhagen",
        });
        const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return {};
        const data = await response.json();
        const current = data?.current || {};
        return {
          humidity: toNumber(current.relative_humidity_2m),
          windDirection: toNumber(current.wind_direction_10m),
          windSpeed: toNumber(current.wind_speed_10m),
          windGust: toNumber(current.wind_gusts_10m),
        };
      } catch (_) {
        return {};
      }
    })();

    weatherCache.set(key, promise);
    return promise;
  }

  function installSmokeStyles() {
    if (document.getElementById("ib-smoke-map-styles")) return;
    const style = document.createElement("style");
    style.id = "ib-smoke-map-styles";
    style.textContent = `
      #map-frame.ib-smoke-map-fallback { display: none !important; }
      #ib-operational-map {
        width: 100%;
        height: 360px;
        border-radius: 14px;
        overflow: hidden;
        background: #07111f;
        border: 1px solid rgba(126, 158, 191, 0.22);
      }
      #ib-smoke-summary {
        margin-top: 10px;
        padding: 10px 12px;
        border: 1px solid rgba(126, 158, 191, 0.20);
        border-radius: 12px;
        background: rgba(2, 12, 23, 0.72);
        color: #dce7f2;
        font-size: 13px;
        line-height: 1.45;
      }
      .ib-smoke-summary-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: wrap;
      }
      .ib-smoke-summary-main { font-weight: 850; color: #fff; }
      .ib-smoke-summary-meta { color: #aebfd2; margin-top: 4px; }
      .ib-smoke-disclaimer { color: #93a7bb; margin-top: 5px; font-size: 12px; }
      .ib-smoke-toggle {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        min-height: 34px;
        padding: 5px 9px;
        border-radius: 999px;
        border: 1px solid rgba(126, 158, 191, 0.22);
        background: rgba(17, 34, 55, 0.78);
        color: #eef5fb;
        font-size: 12px;
        font-weight: 800;
        cursor: pointer;
        user-select: none;
      }
      .ib-smoke-toggle input { width: 16px !important; height: 16px; min-height: auto !important; margin: 0; }
      .ib-smoke-time-tooltip {
        background: rgba(2, 12, 23, 0.90) !important;
        color: #fff !important;
        border: 1px solid rgba(255,255,255,0.18) !important;
        border-radius: 999px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.28) !important;
        font-size: 11px !important;
        font-weight: 900 !important;
        padding: 3px 7px !important;
      }
      .ib-smoke-time-tooltip::before { display: none !important; }
      #ib-operational-map .leaflet-control-attribution { font-size: 10px; }
      @media (max-width: 1000px) { #ib-operational-map { height: 320px; } }
      @media (max-width: 700px) { #ib-operational-map { height: 280px; } }
      @media (max-width: 480px) { #ib-operational-map { height: 260px; } }
    `;
    document.head.appendChild(style);
  }

  function loadLeaflet() {
    if (window.L?.map) return Promise.resolve(window.L);
    if (leafletPromise) return leafletPromise;

    leafletPromise = new Promise((resolve, reject) => {
      if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = LEAFLET_CSS;
        link.integrity = LEAFLET_CSS_INTEGRITY;
        link.crossOrigin = "";
        document.head.appendChild(link);
      }

      const existing = document.querySelector(`script[src="${LEAFLET_JS}"]`);
      if (existing) {
        existing.addEventListener("load", () => resolve(window.L), { once: true });
        existing.addEventListener("error", () => reject(new Error("Leaflet kunne ikke indlæses")), { once: true });
        return;
      }

      const script = document.createElement("script");
      script.src = LEAFLET_JS;
      script.integrity = LEAFLET_JS_INTEGRITY;
      script.crossOrigin = "";
      script.onload = () => resolve(window.L);
      script.onerror = () => reject(new Error("Leaflet kunne ikke indlæses"));
      document.head.appendChild(script);
    });

    return leafletPromise;
  }

  function ensureMapHosts() {
    mapFrame = document.getElementById("map-frame");
    if (!mapFrame) return false;

    if (!mapHost) {
      mapHost = document.createElement("div");
      mapHost.id = "ib-operational-map";
      mapHost.setAttribute("role", "region");
      mapHost.setAttribute("aria-label", "OpenStreetMap med vejledende røgdrift");
      mapFrame.insertAdjacentElement("afterend", mapHost);
      mapFrame.classList.add("ib-smoke-map-fallback");
    }

    if (!summaryHost) {
      summaryHost = document.createElement("div");
      summaryHost.id = "ib-smoke-summary";
      summaryHost.hidden = true;
      const mapLinks = mapFrame.closest("#map-section")?.querySelector(".map-links");
      if (mapLinks) mapLinks.insertAdjacentElement("beforebegin", summaryHost);
      else mapHost.insertAdjacentElement("afterend", summaryHost);
    }

    return true;
  }

  function setSummary(message, meta = "", disclaimer = "") {
    if (!summaryHost) return;
    summaryHost.hidden = false;
    summaryHost.replaceChildren();

    const row = document.createElement("div");
    row.className = "ib-smoke-summary-row";

    const main = document.createElement("div");
    main.className = "ib-smoke-summary-main";
    main.textContent = message;
    row.appendChild(main);

    if (smokeLayer?.getLayers?.().length) {
      const label = document.createElement("label");
      label.className = "ib-smoke-toggle";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = smokeVisible;
      checkbox.addEventListener("change", () => {
        smokeVisible = checkbox.checked;
        if (!operationalMap || !smokeLayer) return;
        if (smokeVisible) smokeLayer.addTo(operationalMap);
        else operationalMap.removeLayer(smokeLayer);
      });
      label.append(checkbox, document.createTextNode("Røgfane"));
      row.appendChild(label);
    }

    summaryHost.appendChild(row);

    if (meta) {
      const metaElement = document.createElement("div");
      metaElement.className = "ib-smoke-summary-meta";
      metaElement.textContent = meta;
      summaryHost.appendChild(metaElement);
    }

    if (disclaimer) {
      const disclaimerElement = document.createElement("div");
      disclaimerElement.className = "ib-smoke-disclaimer";
      disclaimerElement.textContent = disclaimer;
      summaryHost.appendChild(disclaimerElement);
    }
  }

  function clearOperationalLayers(L) {
    if (incidentLayer && operationalMap) operationalMap.removeLayer(incidentLayer);
    if (smokeLayer && operationalMap) operationalMap.removeLayer(smokeLayer);
    incidentLayer = L.layerGroup().addTo(operationalMap);
    smokeLayer = L.layerGroup();
    if (smokeVisible) smokeLayer.addTo(operationalMap);
  }

  function addTimeTooltip(L, latitude, longitude, bearing, distanceMeters, label) {
    const point = destinationPoint(latitude, longitude, bearing, distanceMeters);
    const anchor = L.circleMarker(point, {
      radius: 1,
      opacity: 0,
      fillOpacity: 0,
      interactive: false,
    }).addTo(smokeLayer);
    anchor.bindTooltip(label, {
      permanent: true,
      direction: "top",
      className: "ib-smoke-time-tooltip",
      offset: [0, -2],
    }).openTooltip();
  }

  function renderDirectionalPlume(L, coordinates, weather) {
    const latitude = coordinates.latitude;
    const longitude = coordinates.longitude;
    const windFrom = normalizeBearing(weather.windDirection);
    const smokeTo = normalizeBearing(windFrom + 180);
    const halfAngle = estimatedHalfAngle(weather.windSpeed, weather.windGust);
    const distances = TIME_BANDS_MINUTES.map((minutes) => weather.windSpeed * minutes * 60);
    const fills = ["#ef4444", "#f97316", "#facc15"];
    const fillOpacity = [0.24, 0.18, 0.13];
    const outlines = ["#f87171", "#fb923c", "#fde047"];

    let innerRadius = 0;
    distances.forEach((outerRadius, index) => {
      const polygon = sectorBand(latitude, longitude, smokeTo, halfAngle, innerRadius, outerRadius);
      L.polygon(polygon, {
        color: outlines[index],
        weight: 1.5,
        opacity: 0.75,
        fillColor: fills[index],
        fillOpacity: fillOpacity[index],
        interactive: false,
      }).addTo(smokeLayer);
      addTimeTooltip(L, latitude, longitude, smokeTo, outerRadius, `${TIME_BANDS_MINUTES[index]} min`);
      innerRadius = outerRadius;
    });

    const tip = destinationPoint(latitude, longitude, smokeTo, distances[distances.length - 1]);
    L.polyline([[latitude, longitude], tip], {
      color: "#f8fafc",
      weight: 2,
      opacity: 0.8,
      dashArray: "7 7",
      interactive: false,
    }).addTo(smokeLayer);

    const bounds = L.latLngBounds([[latitude, longitude]]);
    smokeLayer.eachLayer((layer) => {
      if (typeof layer.getBounds === "function") bounds.extend(layer.getBounds());
      else if (typeof layer.getLatLng === "function") bounds.extend(layer.getLatLng());
    });

    const gustText = weather.windGust !== null ? ` · vindstød ${weather.windGust.toFixed(1)} m/s` : "";
    const humidityText = weather.humidity !== null ? ` · luftfugtighed ${Math.round(weather.humidity)} %` : "";
    setSummary(
      `Røgdrift mod ${cardinalDirection(smokeTo)} · ${weather.windSpeed.toFixed(1)} m/s`,
      `Transportkorridor: 5 / 10 / 15 min${gustText}${humidityText}`,
      "Vejledende transportestimat ud fra aktuel vind. Farvebåndene viser transporttid – ikke røgkoncentration eller sikkerhedsafstand."
    );

    return bounds;
  }

  function renderLowWindUncertainty(L, coordinates, weather) {
    const latitude = coordinates.latitude;
    const longitude = coordinates.longitude;
    const baseSpeed = Math.max(weather.windSpeed, 0.5);
    const distances = TIME_BANDS_MINUTES.map((minutes) => baseSpeed * minutes * 60);
    const fills = ["#ef4444", "#f97316", "#facc15"];
    const fillOpacity = [0.10, 0.075, 0.05];

    [...distances].reverse().forEach((radius, reverseIndex) => {
      const index = distances.length - 1 - reverseIndex;
      L.circle([latitude, longitude], {
        radius,
        color: fills[index],
        weight: 1.4,
        opacity: 0.65,
        fillColor: fills[index],
        fillOpacity: fillOpacity[index],
        interactive: false,
      }).addTo(smokeLayer);
    });

    const humidityText = weather.humidity !== null ? ` · luftfugtighed ${Math.round(weather.humidity)} %` : "";
    setSummary(
      `Svag vind (${weather.windSpeed.toFixed(1)} m/s) · røgretning usikker`,
      `Viser lokal usikkerhedszone for 5 / 10 / 15 min${humidityText}`,
      "Ved svag vind kan lokal turbulens, bygninger og termik dominere. Zonerne er ikke røgkoncentration eller sikkerhedsafstand."
    );

    const radius = distances[distances.length - 1];
    return L.latLngBounds(
      destinationPoint(latitude, longitude, 225, radius),
      destinationPoint(latitude, longitude, 45, radius)
    );
  }

  async function renderOperationalMap() {
    const sequence = ++renderSequence;
    if (!ensureMapHosts()) return;

    const incidentData = currentIncidentData();
    const coordinates = extractCoordinatesFromData(incidentData) || extractCoordinatesFromFrame(mapFrame);
    if (!coordinates) return;

    try {
      const L = await loadLeaflet();
      if (sequence !== renderSequence) return;

      if (!operationalMap) {
        operationalMap = L.map(mapHost, {
          zoomControl: true,
          attributionControl: true,
        });
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(operationalMap);
      }

      clearOperationalLayers(L);

      L.circleMarker([coordinates.latitude, coordinates.longitude], {
        radius: 8,
        color: "#fff",
        weight: 2,
        fillColor: "#dc2626",
        fillOpacity: 1,
      })
        .bindTooltip("Hændelsessted", { direction: "top" })
        .addTo(incidentLayer);

      const rawWeather = extractWeather(incidentData);
      const fallbackWeather = await fetchMapWeather(coordinates.latitude, coordinates.longitude);
      if (sequence !== renderSequence) return;

      const embeddedWindDirection = weatherValue(rawWeather, "wind_direction_degrees", "wind_direction_10m");
      const embeddedWindSpeed = weatherValue(rawWeather, "wind_speed_ms", "wind_speed_10m");
      const embeddedWindGust = weatherValue(rawWeather, "wind_gust_ms", "wind_gusts_10m");
      const embeddedHumidity = weatherValue(rawWeather, "relative_humidity_2m", "relative_humidity_percent", "humidity_percent");

      const weather = {
        windDirection: embeddedWindDirection ?? fallbackWeather.windDirection ?? null,
        windSpeed: embeddedWindSpeed ?? fallbackWeather.windSpeed ?? null,
        windGust: embeddedWindGust ?? fallbackWeather.windGust ?? null,
        humidity: embeddedHumidity ?? fallbackWeather.humidity ?? null,
      };

      let bounds;
      if (weather.windSpeed !== null && weather.windSpeed >= SMOKE_MIN_DIRECTIONAL_WIND_MS && weather.windDirection !== null) {
        bounds = renderDirectionalPlume(L, coordinates, weather);
      } else if (weather.windSpeed !== null) {
        bounds = renderLowWindUncertainty(L, coordinates, weather);
      } else {
        setSummary(
          "Røgfane ikke vist · mangler vinddata",
          weather.humidity !== null ? `Luftfugtighed ${Math.round(weather.humidity)} %` : "",
          "Kortet fungerer fortsat normalt. Røgdrift vises automatisk, når vinddata er tilgængelige."
        );
        bounds = L.latLngBounds([[coordinates.latitude, coordinates.longitude]]);
      }

      requestAnimationFrame(() => {
        operationalMap.invalidateSize();
        if (bounds?.isValid?.() && bounds.getNorthEast && bounds.getSouthWest) {
          const northEast = bounds.getNorthEast();
          const southWest = bounds.getSouthWest();
          const samePoint = northEast.lat === southWest.lat && northEast.lng === southWest.lng;
          if (samePoint) operationalMap.setView([coordinates.latitude, coordinates.longitude], 16);
          else operationalMap.fitBounds(bounds.pad(0.12), { maxZoom: 15, animate: false });
        } else {
          operationalMap.setView([coordinates.latitude, coordinates.longitude], 16);
        }
      });
    } catch (error) {
      console.warn("[IndsatsBrief] Røgkort kunne ikke indlæses:", error);
      mapFrame.classList.remove("ib-smoke-map-fallback");
      mapHost?.remove();
      mapHost = null;
      setSummary(
        "Røgkort kunne ikke indlæses",
        "OpenStreetMap-iframe bruges som fallback.",
        "Den eksisterende kortvisning er bevaret."
      );
    }
  }

  function start() {
    installSmokeStyles();
    if (!ensureMapHosts()) return;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => mutation.type === "attributes" && mutation.attributeName === "src")) {
        renderOperationalMap();
      }
    });
    observer.observe(mapFrame, { attributes: true, attributeFilter: ["src"] });

    if (mapFrame.getAttribute("src")) renderOperationalMap();

    window.addEventListener("resize", () => {
      operationalMap?.invalidateSize();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
