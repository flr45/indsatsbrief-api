(() => {
  "use strict";

  const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  const LEAFLET_CSS_INTEGRITY = "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=";
  const LEAFLET_JS_INTEGRITY = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
  const EARTH_RADIUS_M = 6371008.8;
  const LOW_WIND_MS = 1.0;
  const FORECAST_MINUTES = [0, 15, 30, 45, 60];
  const weatherCache = new Map();

  const FIRE_PROFILES = {
    small: {
      label: "Lille brand",
      detail: "Bil, container, mindre udhus",
      weights: { wind10: 0.85, wind80: 0.15, wind120: 0 },
      spreadFactor: 1.12,
      thermalLift: "Lav/moderat",
    },
    medium: {
      label: "Mellem brand",
      detail: "Bolig, lejlighed, mindre erhverv",
      weights: { wind10: 0.55, wind80: 0.35, wind120: 0.10 },
      spreadFactor: 1.0,
      thermalLift: "Moderat",
    },
    large: {
      label: "Stor brand",
      detail: "Industri, lager, gård, større tagbrand",
      weights: { wind10: 0.30, wind80: 0.45, wind120: 0.25 },
      spreadFactor: 0.92,
      thermalLift: "Betydelig",
    },
  };

  let leafletPromise = null;
  let operationalMap = null;
  let incidentLayer = null;
  let smokeLayer = null;
  let forecastLayer = null;
  let mapFrame = null;
  let mapHost = null;
  let summaryHost = null;
  let controlsHost = null;
  let smokeVisible = true;
  let forecastVisible = true;
  let fireProfileKey = "medium";
  let renderSequence = 0;
  let lastRenderContext = null;

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
      return typeof latestIncidentData !== "undefined" && latestIncidentData ? latestIncidentData : null;
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
      const [latValue, lonValue] = marker.split(",");
      const latitude = toNumber(latValue);
      const longitude = toNumber(lonValue);
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
    return labels[Math.round(normalizeBearing(degrees) / 22.5) % 16];
  }

  function angularDifference(a, b) {
    const diff = Math.abs(normalizeBearing(a) - normalizeBearing(b));
    return Math.min(diff, 360 - diff);
  }

  function destinationPoint(latitude, longitude, bearingDegrees, distanceMeters) {
    const angularDistance = distanceMeters / EARTH_RADIUS_M;
    const bearing = degreesToRadians(bearingDegrees);
    const lat1 = degreesToRadians(latitude);
    const lon1 = degreesToRadians(longitude);
    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(angularDistance) + Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing)
    );
    const lon2 = lon1 + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2)
    );
    return [radiansToDegrees(lat2), radiansToDegrees(lon2)];
  }

  function bearingBetween(a, b) {
    const lat1 = degreesToRadians(a[0]);
    const lat2 = degreesToRadians(b[0]);
    const deltaLon = degreesToRadians(b[1] - a[1]);
    const y = Math.sin(deltaLon) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
    return normalizeBearing(radiansToDegrees(Math.atan2(y, x)));
  }

  function vectorFromWind(speed, fromDegrees) {
    if (speed === null || fromDegrees === null || speed < 0) return null;
    const toDegrees = normalizeBearing(fromDegrees + 180);
    const radians = degreesToRadians(toDegrees);
    return { east: speed * Math.sin(radians), north: speed * Math.cos(radians) };
  }

  function vectorToWind(vector) {
    if (!vector) return { speed: null, to: null, from: null };
    const speed = Math.hypot(vector.east, vector.north);
    if (!Number.isFinite(speed) || speed <= 0) return { speed: 0, to: null, from: null };
    const to = normalizeBearing(radiansToDegrees(Math.atan2(vector.east, vector.north)));
    return { speed, to, from: normalizeBearing(to + 180) };
  }

  function weightedWind(sample, profile) {
    const levels = [
      [sample.wind10Speed, sample.wind10Direction, profile.weights.wind10],
      [sample.wind80Speed, sample.wind80Direction, profile.weights.wind80],
      [sample.wind120Speed, sample.wind120Direction, profile.weights.wind120],
    ];
    let east = 0;
    let north = 0;
    let totalWeight = 0;
    for (const [speed, direction, weight] of levels) {
      if (!weight || speed === null || direction === null) continue;
      const vector = vectorFromWind(speed, direction);
      if (!vector) continue;
      east += vector.east * weight;
      north += vector.north * weight;
      totalWeight += weight;
    }
    if (totalWeight <= 0) return { speed: null, to: null, from: null };
    return vectorToWind({ east: east / totalWeight, north: north / totalWeight });
  }

  function valueAt(object, key, index) {
    const values = object?.[key];
    if (!Array.isArray(values) || index < 0 || index >= values.length) return null;
    return toNumber(values[index]);
  }

  function selectForecastIndexes(times) {
    if (!Array.isArray(times) || !times.length) return [];
    const parsed = times.map((time) => new Date(time).getTime());
    const start = parsed[0];
    return FORECAST_MINUTES.map((minute) => {
      const target = start + minute * 60000;
      let best = 0;
      let bestDiff = Infinity;
      parsed.forEach((value, index) => {
        const diff = Math.abs(value - target);
        if (Number.isFinite(value) && diff < bestDiff) {
          best = index;
          bestDiff = diff;
        }
      });
      return best;
    });
  }

  function normalizeForecast(raw) {
    const minutely = raw?.minutely_15 || {};
    const indexes = selectForecastIndexes(minutely.time);
    if (!indexes.length) return [];
    return indexes.map((index, outputIndex) => ({
      minute: FORECAST_MINUTES[outputIndex],
      time: minutely.time?.[index] || null,
      wind10Speed: valueAt(minutely, "wind_speed_10m", index),
      wind10Direction: valueAt(minutely, "wind_direction_10m", index),
      wind80Speed: valueAt(minutely, "wind_speed_80m", index),
      wind80Direction: valueAt(minutely, "wind_direction_80m", index),
      wind120Speed: valueAt(minutely, "wind_speed_120m", index),
      wind120Direction: valueAt(minutely, "wind_direction_120m", index),
      windGust: valueAt(minutely, "wind_gusts_10m", index),
      humidity: valueAt(minutely, "relative_humidity_2m", index),
      cloudCover: valueAt(minutely, "cloud_cover", index),
      precipitation: valueAt(minutely, "precipitation", index),
      shortwaveRadiation: valueAt(minutely, "shortwave_radiation", index),
      isDay: valueAt(minutely, "is_day", index),
    }));
  }

  async function fetchMapWeather(latitude, longitude) {
    const key = `${latitude.toFixed(3)},${longitude.toFixed(3)}`;
    if (weatherCache.has(key)) return weatherCache.get(key);
    const promise = (async () => {
      try {
        const variables = [
          "wind_speed_10m", "wind_direction_10m", "wind_speed_80m", "wind_direction_80m",
          "wind_speed_120m", "wind_direction_120m", "wind_gusts_10m", "relative_humidity_2m",
          "cloud_cover", "precipitation", "shortwave_radiation", "is_day",
        ].join(",");
        const params = new URLSearchParams({
          latitude: String(latitude),
          longitude: String(longitude),
          current: variables,
          minutely_15: variables,
          forecast_minutely_15: "5",
          wind_speed_unit: "ms",
          timezone: "Europe/Copenhagen",
        });
        const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return {};
        const raw = await response.json();
        const current = raw?.current || {};
        return {
          current: {
            wind10Speed: toNumber(current.wind_speed_10m),
            wind10Direction: toNumber(current.wind_direction_10m),
            wind80Speed: toNumber(current.wind_speed_80m),
            wind80Direction: toNumber(current.wind_direction_80m),
            wind120Speed: toNumber(current.wind_speed_120m),
            wind120Direction: toNumber(current.wind_direction_120m),
            windGust: toNumber(current.wind_gusts_10m),
            humidity: toNumber(current.relative_humidity_2m),
            cloudCover: toNumber(current.cloud_cover),
            precipitation: toNumber(current.precipitation),
            shortwaveRadiation: toNumber(current.shortwave_radiation),
            isDay: toNumber(current.is_day),
          },
          forecast: normalizeForecast(raw),
          source: "Open-Meteo",
        };
      } catch (_) {
        return {};
      }
    })();
    weatherCache.set(key, promise);
    return promise;
  }

  function weatherValue(weather, ...keys) {
    for (const key of keys) {
      const value = toNumber(weather?.[key]);
      if (value !== null) return value;
    }
    return null;
  }

  function mergeCurrentWeather(rawWeather, fetchedCurrent) {
    return {
      wind10Speed: weatherValue(rawWeather, "wind_speed_ms", "wind_speed_10m") ?? fetchedCurrent.wind10Speed ?? null,
      wind10Direction: weatherValue(rawWeather, "wind_direction_degrees", "wind_direction_10m") ?? fetchedCurrent.wind10Direction ?? null,
      wind80Speed: fetchedCurrent.wind80Speed ?? null,
      wind80Direction: fetchedCurrent.wind80Direction ?? null,
      wind120Speed: fetchedCurrent.wind120Speed ?? null,
      wind120Direction: fetchedCurrent.wind120Direction ?? null,
      windGust: weatherValue(rawWeather, "wind_gust_ms", "wind_gusts_10m") ?? fetchedCurrent.windGust ?? null,
      humidity: weatherValue(rawWeather, "relative_humidity_2m", "relative_humidity_percent", "humidity_percent") ?? fetchedCurrent.humidity ?? null,
      cloudCover: fetchedCurrent.cloudCover ?? null,
      precipitation: weatherValue(rawWeather, "precipitation") ?? fetchedCurrent.precipitation ?? null,
      shortwaveRadiation: fetchedCurrent.shortwaveRadiation ?? null,
      isDay: fetchedCurrent.isDay ?? null,
    };
  }

  function estimateStability(sample) {
    const speed = sample.wind10Speed ?? 3;
    const isDay = sample.isDay === 1 || (sample.isDay === null && (sample.shortwaveRadiation ?? 0) > 20);
    const radiation = sample.shortwaveRadiation;
    const cloud = sample.cloudCover;
    let className = "D";

    if (isDay) {
      const strength = radiation === null ? 2 : radiation >= 700 ? 3 : radiation >= 350 ? 2 : radiation >= 100 ? 1 : 0;
      if (speed < 2) className = strength >= 3 ? "A" : strength === 2 ? "A/B" : strength === 1 ? "B" : "D";
      else if (speed < 3) className = strength >= 3 ? "A/B" : strength === 2 ? "B" : strength === 1 ? "C" : "D";
      else if (speed < 5) className = strength >= 3 ? "B" : strength === 2 ? "B/C" : strength === 1 ? "C" : "D";
      else if (speed < 6) className = strength >= 3 ? "C" : strength === 2 ? "C/D" : "D";
      else className = strength >= 3 ? "C" : "D";
    } else {
      const cloudy = cloud !== null && cloud >= 55;
      if (speed < 2) className = cloudy ? "E" : "F";
      else if (speed < 3) className = cloudy ? "E" : "F";
      else if (speed < 5) className = cloudy ? "D" : "E";
      else className = "D";
    }

    const dominant = className.includes("/") ? className.split("/")[1] : className;
    const labels = { A: "meget ustabil", B: "ustabil", C: "let ustabil", D: "neutral", E: "stabil", F: "meget stabil" };
    return {
      className,
      dominant,
      label: labels[dominant] || "neutral",
      derivedFromFullData: radiation !== null && cloud !== null && sample.isDay !== null,
    };
  }

  function stabilityHalfAngle(stability) {
    return ({ A: 47, B: 39, C: 31, D: 24, E: 18, F: 13 })[stability.dominant] || 24;
  }

  function windShear(sample) {
    const directions = [sample.wind10Direction, sample.wind80Direction, sample.wind120Direction].filter((value) => value !== null);
    if (directions.length < 2) return null;
    let maxDiff = 0;
    for (let i = 0; i < directions.length; i += 1) {
      for (let j = i + 1; j < directions.length; j += 1) {
        maxDiff = Math.max(maxDiff, angularDifference(directions[i], directions[j]));
      }
    }
    return maxDiff;
  }

  function plumeHalfAngle(sample, stability, profile) {
    let angle = stabilityHalfAngle(stability) * profile.spreadFactor;
    const gustRatio = sample.windGust !== null && sample.wind10Speed > 0 ? sample.windGust / sample.wind10Speed : 1;
    if (gustRatio >= 1.4) angle += clamp((gustRatio - 1.4) * 12, 0, 10);
    const shear = windShear(sample);
    if (shear !== null) angle += clamp(shear * 0.18, 0, 12);
    if ((sample.wind10Speed ?? 0) < 2) angle += 5;
    return clamp(angle, 11, 58);
  }

  function confidenceAssessment(current, forecast, stability) {
    let score = 92;
    const notes = [];
    if (current.wind10Speed === null || current.wind10Direction === null) {
      return { score: 15, label: "Lav", notes: ["manglende 10 m vinddata"] };
    }
    if (current.wind10Speed < 1) {
      score -= 45;
      notes.push("meget svag vind");
    } else if (current.wind10Speed < 2) {
      score -= 22;
      notes.push("svag vind");
    }
    const gustRatio = current.windGust !== null && current.wind10Speed > 0 ? current.windGust / current.wind10Speed : 1;
    if (gustRatio > 1.8) {
      score -= 16;
      notes.push("kraftige vindstød");
    } else if (gustRatio > 1.45) {
      score -= 8;
      notes.push("varierende vind");
    }
    const shear = windShear(current);
    if (shear !== null && shear > 45) {
      score -= 20;
      notes.push("stor vinddrejning med højden");
    } else if (shear !== null && shear > 25) {
      score -= 10;
      notes.push("vinddrejning med højden");
    } else if (shear === null) {
      score -= 8;
      notes.push("mangler vindprofil i højden");
    }
    if (!stability.derivedFromFullData) {
      score -= 8;
      notes.push("forenklet stabilitetsestimat");
    }
    const profile = FIRE_PROFILES[fireProfileKey];
    const futureDirections = forecast.map((sample) => weightedWind(sample, profile).to).filter((value) => value !== null);
    if (futureDirections.length >= 2) {
      const spread = Math.max(...futureDirections.map((direction) => angularDifference(futureDirections[0], direction)));
      if (spread > 60) {
        score -= 18;
        notes.push("stor prognosticeret vinddrejning");
      } else if (spread > 30) {
        score -= 9;
        notes.push("prognosticeret vinddrejning");
      }
    } else {
      score -= 8;
      notes.push("begrænset korttidsprognose");
    }
    score = clamp(Math.round(score), 10, 98);
    return { score, label: score >= 75 ? "Høj" : score >= 50 ? "Middel" : "Lav", notes };
  }

  function buildTrajectory(coordinates, current, forecast, profile) {
    const source = [coordinates.latitude, coordinates.longitude];
    const samples = forecast.length ? forecast : [{ ...current, minute: 0 }];
    const minuteMap = new Map(samples.map((sample) => [sample.minute, sample]));
    const stepMinutes = [0, 15, 30, 45, 60];
    const points = [{ minute: 0, point: source, sample: minuteMap.get(0) || current, distance: 0 }];
    let cursor = source;
    let totalDistance = 0;

    for (let index = 1; index < stepMinutes.length; index += 1) {
      const startMinute = stepMinutes[index - 1];
      const endMinute = stepMinutes[index];
      let sample = minuteMap.get(startMinute);
      if (!sample) {
        const available = samples.filter((item) => item.minute <= startMinute).sort((a, b) => b.minute - a.minute);
        sample = available[0] || current;
      }
      const effective = weightedWind(sample, profile);
      if (effective.speed === null || effective.to === null) break;
      const segmentDistance = effective.speed * (endMinute - startMinute) * 60;
      cursor = destinationPoint(cursor[0], cursor[1], effective.to, segmentDistance);
      totalDistance += segmentDistance;
      points.push({ minute: endMinute, point: cursor, sample, distance: totalDistance });
    }
    return points;
  }

  function corridorPolygon(trajectory, halfAngle, widthScale = 1) {
    if (trajectory.length < 2) return [];
    const left = [];
    const right = [];
    trajectory.forEach((item, index) => {
      let bearing;
      if (index === 0) bearing = bearingBetween(item.point, trajectory[index + 1].point);
      else if (index === trajectory.length - 1) bearing = bearingBetween(trajectory[index - 1].point, item.point);
      else bearing = bearingBetween(trajectory[index - 1].point, trajectory[index + 1].point);
      const width = index === 0 ? 18 : clamp(Math.tan(degreesToRadians(halfAngle)) * item.distance * 0.72 * widthScale, 35, 3200);
      left.push(destinationPoint(item.point[0], item.point[1], bearing - 90, width));
      right.push(destinationPoint(item.point[0], item.point[1], bearing + 90, width));
    });
    return [...left, ...right.reverse()];
  }

  function addTimeTooltip(L, point, label) {
    const anchor = L.circleMarker(point, { radius: 1, opacity: 0, fillOpacity: 0, interactive: false }).addTo(forecastLayer);
    anchor.bindTooltip(label, {
      permanent: true,
      direction: "top",
      className: "ib-smoke-time-tooltip",
      offset: [0, -2],
    }).openTooltip();
  }

  function installSmokeStyles() {
    if (document.getElementById("ib-smoke-map-styles")) return;
    const style = document.createElement("style");
    style.id = "ib-smoke-map-styles";
    style.textContent = `
      #map-frame.ib-smoke-map-fallback { display: none !important; }
      #ib-operational-map { width: 100%; height: 390px; border-radius: 14px; overflow: hidden; background: #07111f; border: 1px solid rgba(126,158,191,.22); }
      #ib-smoke-summary { margin-top: 10px; padding: 11px 12px; border: 1px solid rgba(126,158,191,.20); border-radius: 12px; background: rgba(2,12,23,.72); color: #dce7f2; font-size: 13px; line-height: 1.45; }
      .ib-smoke-summary-row { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
      .ib-smoke-summary-main { font-weight:850; color:#fff; }
      .ib-smoke-summary-meta { color:#aebfd2; margin-top:5px; }
      .ib-smoke-disclaimer { color:#93a7bb; margin-top:6px; font-size:12px; }
      .ib-smoke-badges { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
      .ib-smoke-badge { display:inline-flex; align-items:center; min-height:25px; padding:3px 8px; border-radius:999px; border:1px solid rgba(148,177,207,.18); background:rgba(148,163,184,.09); color:#dce7f2; font-size:11px; font-weight:800; }
      .ib-smoke-badge.high { border-color:rgba(34,197,94,.28); color:#bbf7d0; background:rgba(34,197,94,.10); }
      .ib-smoke-badge.medium { border-color:rgba(250,204,21,.28); color:#fde68a; background:rgba(250,204,21,.10); }
      .ib-smoke-badge.low { border-color:rgba(248,113,113,.30); color:#fecaca; background:rgba(239,68,68,.10); }
      #ib-smoke-controls { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:8px; align-items:end; margin-top:9px; }
      #ib-smoke-controls label { font-size:11px !important; color:#aebfd2 !important; }
      #ib-smoke-controls select { min-height:36px !important; height:36px; font-size:12px; padding:4px 30px 4px 9px; }
      .ib-smoke-toggle { display:inline-flex; align-items:center; gap:6px; min-height:36px; padding:5px 9px; border-radius:999px; border:1px solid rgba(126,158,191,.22); background:rgba(17,34,55,.78); color:#eef5fb; font-size:12px; font-weight:800; cursor:pointer; user-select:none; }
      .ib-smoke-toggle input { width:16px !important; height:16px; min-height:auto !important; margin:0; }
      .ib-smoke-time-tooltip { background:rgba(2,12,23,.92) !important; color:#fff !important; border:1px solid rgba(255,255,255,.18) !important; border-radius:999px !important; box-shadow:0 4px 12px rgba(0,0,0,.28) !important; font-size:11px !important; font-weight:900 !important; padding:3px 7px !important; }
      .ib-smoke-time-tooltip::before { display:none !important; }
      #ib-operational-map .leaflet-control-attribution { font-size:10px; }
      @media (max-width:1000px) { #ib-operational-map { height:340px; } }
      @media (max-width:700px) { #ib-operational-map { height:300px; } #ib-smoke-controls { grid-template-columns:1fr 1fr; } #ib-smoke-controls .ib-fire-profile-wrap { grid-column:1/-1; } }
      @media (max-width:480px) { #ib-operational-map { height:280px; } #ib-smoke-controls { grid-template-columns:1fr; } #ib-smoke-controls .ib-fire-profile-wrap { grid-column:auto; } }
    `;
    document.head.appendChild(style);
  }

  function loadPreferences() {
    try {
      const savedProfile = localStorage.getItem("indsatsbrief.smoke.fireProfile");
      if (savedProfile && FIRE_PROFILES[savedProfile]) fireProfileKey = savedProfile;
      const savedForecast = localStorage.getItem("indsatsbrief.smoke.forecastVisible");
      if (savedForecast !== null) forecastVisible = savedForecast !== "false";
    } catch (_) {}
  }

  function savePreference(key, value) {
    try { localStorage.setItem(key, String(value)); } catch (_) {}
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
      mapHost.setAttribute("aria-label", "OpenStreetMap med vejledende intelligent røgdrift");
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
    if (!controlsHost) {
      controlsHost = document.createElement("div");
      controlsHost.id = "ib-smoke-controls";
      summaryHost.insertAdjacentElement("afterend", controlsHost);
      buildControls();
    }
    return true;
  }

  function buildControls() {
    if (!controlsHost) return;
    controlsHost.replaceChildren();
    const profileWrap = document.createElement("label");
    profileWrap.className = "ib-fire-profile-wrap";
    profileWrap.appendChild(document.createTextNode("Brandprofil"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Brandprofil for røgmodel");
    Object.entries(FIRE_PROFILES).forEach(([key, profile]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = `${profile.label} · ${profile.detail}`;
      option.selected = key === fireProfileKey;
      select.appendChild(option);
    });
    select.addEventListener("change", () => {
      fireProfileKey = select.value;
      savePreference("indsatsbrief.smoke.fireProfile", fireProfileKey);
      rerenderFromContext();
    });
    profileWrap.appendChild(select);

    const smokeToggle = document.createElement("label");
    smokeToggle.className = "ib-smoke-toggle";
    const smokeCheckbox = document.createElement("input");
    smokeCheckbox.type = "checkbox";
    smokeCheckbox.checked = smokeVisible;
    smokeCheckbox.addEventListener("change", () => {
      smokeVisible = smokeCheckbox.checked;
      syncLayerVisibility();
    });
    smokeToggle.append(smokeCheckbox, document.createTextNode("Røgfane"));

    const forecastToggle = document.createElement("label");
    forecastToggle.className = "ib-smoke-toggle";
    const forecastCheckbox = document.createElement("input");
    forecastCheckbox.type = "checkbox";
    forecastCheckbox.checked = forecastVisible;
    forecastCheckbox.addEventListener("change", () => {
      forecastVisible = forecastCheckbox.checked;
      savePreference("indsatsbrief.smoke.forecastVisible", forecastVisible);
      syncLayerVisibility();
      rerenderFromContext();
    });
    forecastToggle.append(forecastCheckbox, document.createTextNode("+60 min"));
    controlsHost.append(profileWrap, smokeToggle, forecastToggle);
  }

  function syncLayerVisibility() {
    if (!operationalMap) return;
    if (smokeLayer) {
      if (smokeVisible && !operationalMap.hasLayer(smokeLayer)) smokeLayer.addTo(operationalMap);
      if (!smokeVisible && operationalMap.hasLayer(smokeLayer)) operationalMap.removeLayer(smokeLayer);
    }
    if (forecastLayer) {
      const show = smokeVisible && forecastVisible;
      if (show && !operationalMap.hasLayer(forecastLayer)) forecastLayer.addTo(operationalMap);
      if (!show && operationalMap.hasLayer(forecastLayer)) operationalMap.removeLayer(forecastLayer);
    }
  }

  function clearOperationalLayers(L) {
    [incidentLayer, smokeLayer, forecastLayer].forEach((layer) => {
      if (layer && operationalMap?.hasLayer(layer)) operationalMap.removeLayer(layer);
    });
    incidentLayer = L.layerGroup().addTo(operationalMap);
    smokeLayer = L.layerGroup();
    forecastLayer = L.layerGroup();
    syncLayerVisibility();
  }

  function renderSummary({ current, stability, confidence, effectiveWind, shear, profile }) {
    if (!summaryHost) return;
    summaryHost.hidden = false;
    summaryHost.replaceChildren();
    const row = document.createElement("div");
    row.className = "ib-smoke-summary-row";
    const main = document.createElement("div");
    main.className = "ib-smoke-summary-main";
    main.textContent = effectiveWind.to === null
      ? "Røgretning usikker"
      : `Forventet røgdrift mod ${cardinalDirection(effectiveWind.to)} · ${effectiveWind.speed.toFixed(1)} m/s`;
    row.appendChild(main);
    summaryHost.appendChild(row);

    const meta = document.createElement("div");
    meta.className = "ib-smoke-summary-meta";
    const gust = current.windGust !== null ? ` · vindstød ${current.windGust.toFixed(1)} m/s` : "";
    const humidity = current.humidity !== null ? ` · RH ${Math.round(current.humidity)} %` : "";
    const rain = current.precipitation !== null && current.precipitation > 0 ? ` · nedbør ${current.precipitation.toFixed(1)} mm` : "";
    meta.textContent = `Stabilitet ${stability.className} (${stability.label})${gust}${humidity}${rain}`;
    summaryHost.appendChild(meta);

    const badges = document.createElement("div");
    badges.className = "ib-smoke-badges";
    const confidenceBadge = document.createElement("span");
    const confidenceClass = confidence.label === "Høj" ? "high" : confidence.label === "Middel" ? "medium" : "low";
    confidenceBadge.className = `ib-smoke-badge ${confidenceClass}`;
    confidenceBadge.textContent = `Modeltillid: ${confidence.label} ${confidence.score}%`;
    badges.appendChild(confidenceBadge);
    const profileBadge = document.createElement("span");
    profileBadge.className = "ib-smoke-badge";
    profileBadge.textContent = `${profile.label} · termisk løft ${profile.thermalLift.toLowerCase()}`;
    badges.appendChild(profileBadge);
    if (shear !== null) {
      const shearBadge = document.createElement("span");
      shearBadge.className = "ib-smoke-badge";
      shearBadge.textContent = `Vinddrejning 10–120 m: ${Math.round(shear)}°`;
      badges.appendChild(shearBadge);
    }
    summaryHost.appendChild(badges);

    const disclaimer = document.createElement("div");
    disclaimer.className = "ib-smoke-disclaimer";
    const noteText = confidence.notes.length ? ` Usikkerhed: ${confidence.notes.join(", ")}.` : "";
    disclaimer.textContent = `Vejledende spredningsmodel. Mørk kerne = mest sandsynlig korridor; lys zone = meteorologisk usikkerhed.${noteText} Ikke røgkoncentration, toksikologisk farezone eller sikkerhedsafstand.`;
    summaryHost.appendChild(disclaimer);
  }

  function renderLowWind(L, coordinates, current, stability, confidence, profile) {
    const radius = Math.max(250, (current.windGust ?? 1) * 15 * 60);
    L.circle([coordinates.latitude, coordinates.longitude], {
      radius,
      color: "#facc15",
      weight: 1.5,
      opacity: 0.75,
      fillColor: "#facc15",
      fillOpacity: 0.08,
      interactive: false,
    }).addTo(smokeLayer);
    L.circle([coordinates.latitude, coordinates.longitude], {
      radius: Math.max(100, radius * 0.42),
      color: "#f97316",
      weight: 1.5,
      opacity: 0.8,
      fillColor: "#ef4444",
      fillOpacity: 0.14,
      interactive: false,
    }).addTo(smokeLayer);
    renderSummary({
      current,
      stability,
      confidence,
      effectiveWind: { speed: current.wind10Speed ?? 0, to: null },
      shear: windShear(current),
      profile,
    });
    return L.latLngBounds(
      destinationPoint(coordinates.latitude, coordinates.longitude, 225, radius),
      destinationPoint(coordinates.latitude, coordinates.longitude, 45, radius)
    );
  }

  function renderSmartPlume(L, coordinates, current, forecast, profile) {
    const stability = estimateStability(current);
    const confidence = confidenceAssessment(current, forecast, stability);
    const effectiveCurrent = weightedWind(current, profile);
    const halfAngle = plumeHalfAngle(current, stability, profile);
    const trajectory = buildTrajectory(coordinates, current, forecast, profile);
    const displayedTrajectory = forecastVisible ? trajectory : trajectory.filter((item) => item.minute <= 15);
    if (displayedTrajectory.length < 2 || effectiveCurrent.to === null) {
      return renderLowWind(L, coordinates, current, stability, confidence, profile);
    }

    const uncertaintyPolygon = corridorPolygon(displayedTrajectory, halfAngle, 1.0);
    const corePolygon = corridorPolygon(displayedTrajectory, Math.max(8, halfAngle * 0.46), 0.72);
    L.polygon(uncertaintyPolygon, {
      color: "#facc15",
      weight: 1.2,
      opacity: 0.62,
      fillColor: "#facc15",
      fillOpacity: 0.09,
      interactive: false,
    }).addTo(smokeLayer);
    L.polygon(corePolygon, {
      color: "#fb923c",
      weight: 1.4,
      opacity: 0.78,
      fillColor: "#ef4444",
      fillOpacity: 0.18,
      interactive: false,
    }).addTo(smokeLayer);

    const linePoints = displayedTrajectory.map((item) => item.point);
    L.polyline(linePoints, {
      color: "#f8fafc",
      weight: 2,
      opacity: 0.85,
      dashArray: "7 7",
      interactive: false,
    }).addTo(smokeLayer);

    if (forecastVisible) {
      displayedTrajectory
        .filter((item) => [15, 30, 60].includes(item.minute))
        .forEach((item) => addTimeTooltip(L, item.point, `+${item.minute} min`));
      forecast.slice(1).forEach((sample) => {
        if (![15, 30, 60].includes(sample.minute)) return;
        const effective = weightedWind(sample, profile);
        if (effective.to === null || effective.speed === null) return;
        const origin = displayedTrajectory.find((item) => item.minute === sample.minute)?.point;
        if (!origin) return;
        const arrowEnd = destinationPoint(origin[0], origin[1], effective.to, clamp(effective.speed * 120, 100, 650));
        L.polyline([origin, arrowEnd], {
          color: "#60a5fa",
          weight: 2,
          opacity: 0.68,
          dashArray: "3 6",
          interactive: false,
        }).addTo(forecastLayer);
      });
    }

    renderSummary({ current, stability, confidence, effectiveWind: effectiveCurrent, shear: windShear(current), profile });
    const bounds = L.latLngBounds([[coordinates.latitude, coordinates.longitude]]);
    [smokeLayer, forecastLayer].forEach((group) => {
      group.eachLayer((layer) => {
        if (typeof layer.getBounds === "function") bounds.extend(layer.getBounds());
        else if (typeof layer.getLatLng === "function") bounds.extend(layer.getLatLng());
      });
    });
    return bounds;
  }

  function rerenderFromContext() {
    if (!lastRenderContext || !window.L) return;
    const { L, coordinates, current, forecast } = lastRenderContext;
    clearOperationalLayers(L);
    L.circleMarker([coordinates.latitude, coordinates.longitude], {
      radius: 8,
      color: "#fff",
      weight: 2,
      fillColor: "#dc2626",
      fillOpacity: 1,
    }).bindTooltip("Hændelsessted", { direction: "top" }).addTo(incidentLayer);
    const profile = FIRE_PROFILES[fireProfileKey];
    let bounds;
    if (current.wind10Speed !== null && current.wind10Speed >= LOW_WIND_MS && current.wind10Direction !== null) {
      bounds = renderSmartPlume(L, coordinates, current, forecast, profile);
    } else {
      const stability = estimateStability(current);
      bounds = renderLowWind(L, coordinates, current, stability, confidenceAssessment(current, forecast, stability), profile);
    }
    fitMap(bounds, coordinates);
  }

  function fitMap(bounds, coordinates) {
    requestAnimationFrame(() => {
      operationalMap?.invalidateSize();
      if (bounds?.isValid?.()) operationalMap.fitBounds(bounds.pad(0.10), { maxZoom: 15, animate: false });
      else operationalMap?.setView([coordinates.latitude, coordinates.longitude], 16);
    });
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
        operationalMap = L.map(mapHost, { zoomControl: true, attributionControl: true });
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
      }).bindTooltip("Hændelsessted", { direction: "top" }).addTo(incidentLayer);

      const rawWeather = extractWeather(incidentData);
      const fetched = await fetchMapWeather(coordinates.latitude, coordinates.longitude);
      if (sequence !== renderSequence) return;
      const current = mergeCurrentWeather(rawWeather, fetched.current || {});
      const forecast = Array.isArray(fetched.forecast) ? fetched.forecast : [];
      lastRenderContext = { L, coordinates, current, forecast };

      let bounds;
      const profile = FIRE_PROFILES[fireProfileKey];
      if (current.wind10Speed !== null && current.wind10Speed >= LOW_WIND_MS && current.wind10Direction !== null) {
        bounds = renderSmartPlume(L, coordinates, current, forecast, profile);
      } else if (current.wind10Speed !== null) {
        const stability = estimateStability(current);
        bounds = renderLowWind(L, coordinates, current, stability, confidenceAssessment(current, forecast, stability), profile);
      } else {
        summaryHost.hidden = false;
        summaryHost.textContent = "Røgmodel ikke vist · mangler brugbare vinddata. Kortet fungerer fortsat normalt.";
        bounds = L.latLngBounds([[coordinates.latitude, coordinates.longitude]]);
      }
      fitMap(bounds, coordinates);
    } catch (error) {
      console.warn("[IndsatsBrief] Røgkort v2 kunne ikke indlæses:", error);
      mapFrame.classList.remove("ib-smoke-map-fallback");
      mapHost?.remove();
      mapHost = null;
      if (summaryHost) {
        summaryHost.hidden = false;
        summaryHost.textContent = "Røgkort kunne ikke indlæses. OpenStreetMap-iframe bruges som fallback.";
      }
    }
  }

  function start() {
    installSmokeStyles();
    loadPreferences();
    if (!ensureMapHosts()) return;
    buildControls();
    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => mutation.type === "attributes" && mutation.attributeName === "src")) {
        renderOperationalMap();
      }
    });
    observer.observe(mapFrame, { attributes: true, attributeFilter: ["src"] });
    if (mapFrame.getAttribute("src")) renderOperationalMap();
    window.addEventListener("resize", () => operationalMap?.invalidateSize());
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
