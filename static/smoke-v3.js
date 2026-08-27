(() => {
  "use strict";

  const MODEL_VERSION = "Røgmodel v3.0";
  const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  const EARTH_RADIUS_M = 6371008.8;
  const FORECAST_MINUTES = [0, 15, 30, 45, 60];
  const STEP_MINUTES = 5;
  const LOW_WIND_MS = 0.8;
  const G = 9.80665;
  const AIR_DENSITY = 1.2;
  const AIR_CP = 1005;
  const KELVIN_OFFSET = 273.15;

  const FIRE_PROFILES = {
    small: {
      label: "Lille brand",
      detail: "Bil, container, mindre rum/udhus",
      defaultArea: 15,
      hrrDensityKwM2: 180,
      sourceHeightM: 4,
    },
    medium: {
      label: "Mellem brand",
      detail: "Bolig, lejlighed, mindre erhverv",
      defaultArea: 80,
      hrrDensityKwM2: 250,
      sourceHeightM: 7,
    },
    large: {
      label: "Stor brand",
      detail: "Større bygning, lager, gård",
      defaultArea: 300,
      hrrDensityKwM2: 300,
      sourceHeightM: 10,
    },
    very_large: {
      label: "Meget stor brand",
      detail: "Industri, stort lager/kompleks",
      defaultArea: 900,
      hrrDensityKwM2: 350,
      sourceHeightM: 15,
    },
  };

  const INTENSITY_FACTORS = {
    low: { label: "Lav intensitet", factor: 0.65 },
    normal: { label: "Normal intensitet", factor: 1.0 },
    high: { label: "Høj intensitet", factor: 1.45 },
  };

  const PARTICLES = {
    coarse: {
      label: "Groft aske-/materialenedfald",
      settlingVelocity: 0.30,
      className: "coarse",
    },
    soot: {
      label: "Sod/grove partikler",
      settlingVelocity: 0.03,
      className: "soot",
    },
  };

  let leafletPromise = null;
  let map = null;
  let mapFrame = null;
  let mapHost = null;
  let summaryHost = null;
  let controlsHost = null;
  let legendHost = null;
  let layerGroups = {};
  let latestContext = null;
  let renderSequence = 0;
  let fireProfileKey = "medium";
  let intensityKey = "normal";
  let burningAreaM2 = FIRE_PROFILES.medium.defaultArea;
  let showGround = true;
  let showFallout = true;
  let showHeight = true;
  let showFuture = true;
  const weatherCache = new Map();

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const toNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const deg2rad = (value) => (value * Math.PI) / 180;
  const rad2deg = (value) => (value * 180) / Math.PI;
  const normalizeBearing = (value) => ((value % 360) + 360) % 360;

  function formatDistance(meters) {
    if (!Number.isFinite(meters)) return "–";
    if (meters >= 10000) return `${(meters / 1000).toFixed(0)} km`;
    if (meters >= 1000) return `${(meters / 1000).toFixed(1)} km`;
    return `${Math.round(meters / 10) * 10} m`;
  }

  function currentIncidentData() {
    try {
      return typeof latestIncidentData !== "undefined" && latestIncidentData ? latestIncidentData : null;
    } catch (_) {
      return null;
    }
  }

  function extractCoordinates(data) {
    if (!data || typeof data !== "object") return null;
    const short = data.short_report_data || {};
    const coordinates = short.coordinates || {};
    const latitude = toNumber(data.latitude ?? coordinates.latitude);
    const longitude = toNumber(data.longitude ?? coordinates.longitude);
    if (latitude === null || longitude === null) return null;
    return { latitude, longitude };
  }

  function coordinatesFromFrame(frame) {
    const src = frame?.getAttribute("src");
    if (!src) return null;
    try {
      const url = new URL(src, window.location.href);
      const marker = url.searchParams.get("marker");
      if (!marker) return null;
      const [latText, lonText] = marker.split(",");
      const latitude = toNumber(latText);
      const longitude = toNumber(lonText);
      if (latitude === null || longitude === null) return null;
      return { latitude, longitude };
    } catch (_) {
      return null;
    }
  }

  function extractWeather(data) {
    return data?.weather || data?.short_report_data?.weather || {};
  }

  function detectAsbestos(data) {
    const building = data?.building || data?.short_report_data?.building || {};
    const check = building?.asbestos_check || {};
    const status = String(check.status || "").toLowerCase();
    return {
      registered: status === "yes",
      uncertain: ["unknown", "not_returned", "partial_no"].includes(status),
      source: building?.asbestos_fallback?.source || check.source || null,
    };
  }

  function destinationPoint(latitude, longitude, bearingDegrees, distanceMeters) {
    const angularDistance = distanceMeters / EARTH_RADIUS_M;
    const bearing = deg2rad(bearingDegrees);
    const lat1 = deg2rad(latitude);
    const lon1 = deg2rad(longitude);
    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(angularDistance) +
      Math.cos(lat1) * Math.sin(angularDistance) * Math.cos(bearing)
    );
    const lon2 = lon1 + Math.atan2(
      Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(lat1),
      Math.cos(angularDistance) - Math.sin(lat1) * Math.sin(lat2)
    );
    return [rad2deg(lat2), rad2deg(lon2)];
  }

  function bearingBetween(a, b) {
    const lat1 = deg2rad(a[0]);
    const lat2 = deg2rad(b[0]);
    const deltaLon = deg2rad(b[1] - a[1]);
    const y = Math.sin(deltaLon) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
    return normalizeBearing(rad2deg(Math.atan2(y, x)));
  }

  function vectorFromWind(speed, fromDegrees) {
    if (speed === null || fromDegrees === null || speed < 0) return null;
    const to = normalizeBearing(fromDegrees + 180);
    const angle = deg2rad(to);
    return { east: speed * Math.sin(angle), north: speed * Math.cos(angle) };
  }

  function vectorToWind(vector) {
    if (!vector) return { speed: null, to: null, from: null };
    const speed = Math.hypot(vector.east, vector.north);
    if (!Number.isFinite(speed) || speed <= 0) return { speed: 0, to: null, from: null };
    const to = normalizeBearing(rad2deg(Math.atan2(vector.east, vector.north)));
    return { speed, to, from: normalizeBearing(to + 180) };
  }

  function angleDifference(a, b) {
    const diff = Math.abs(normalizeBearing(a) - normalizeBearing(b));
    return Math.min(diff, 360 - diff);
  }

  function lerpAngle(a, b, t) {
    if (a === null) return b;
    if (b === null) return a;
    const delta = ((b - a + 540) % 360) - 180;
    return normalizeBearing(a + delta * t);
  }

  function cardinalDirection(degrees) {
    const labels = ["N", "NNØ", "NØ", "ØNØ", "Ø", "ØSØ", "SØ", "SSØ", "S", "SSV", "SV", "VSV", "V", "VNV", "NV", "NNV"];
    return labels[Math.round(normalizeBearing(degrees) / 22.5) % 16];
  }

  function valueAt(object, key, index) {
    const values = object?.[key];
    if (!Array.isArray(values) || index < 0 || index >= values.length) return null;
    return toNumber(values[index]);
  }

  function nearestHourlyValue(hourly, key) {
    if (!Array.isArray(hourly?.time) || !Array.isArray(hourly?.[key])) return null;
    const now = Date.now();
    let best = null;
    let diff = Infinity;
    hourly.time.forEach((time, index) => {
      const ms = new Date(time).getTime();
      if (!Number.isFinite(ms)) return;
      const candidateDiff = Math.abs(ms - now);
      if (candidateDiff < diff) {
        diff = candidateDiff;
        best = toNumber(hourly[key][index]);
      }
    });
    return best;
  }

  function selectForecastIndexes(times) {
    if (!Array.isArray(times) || !times.length) return [];
    const parsed = times.map((value) => new Date(value).getTime());
    const now = Date.now();
    let startIndex = 0;
    let startDiff = Infinity;
    parsed.forEach((value, index) => {
      const diff = Math.abs(value - now);
      if (Number.isFinite(value) && diff < startDiff) {
        startIndex = index;
        startDiff = diff;
      }
    });
    const start = parsed[startIndex];
    return FORECAST_MINUTES.map((minute) => {
      const target = start + minute * 60000;
      let best = startIndex;
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
      temperature: valueAt(minutely, "temperature_2m", index),
    }));
  }

  async function fetchWeather(latitude, longitude) {
    const key = `${latitude.toFixed(3)},${longitude.toFixed(3)}`;
    if (weatherCache.has(key)) return weatherCache.get(key);
    const promise = (async () => {
      try {
        const variables = [
          "temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
          "wind_speed_80m", "wind_direction_80m", "wind_speed_120m", "wind_direction_120m",
          "wind_gusts_10m", "cloud_cover", "precipitation", "shortwave_radiation", "is_day",
        ].join(",");
        const params = new URLSearchParams({
          latitude: String(latitude),
          longitude: String(longitude),
          current: variables,
          minutely_15: variables,
          hourly: "boundary_layer_height",
          forecast_minutely_15: "5",
          forecast_hours: "3",
          wind_speed_unit: "ms",
          timezone: "Europe/Copenhagen",
        });
        const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) return {};
        const raw = await response.json();
        const current = raw.current || {};
        return {
          current: {
            temperature: toNumber(current.temperature_2m),
            humidity: toNumber(current.relative_humidity_2m),
            wind10Speed: toNumber(current.wind_speed_10m),
            wind10Direction: toNumber(current.wind_direction_10m),
            wind80Speed: toNumber(current.wind_speed_80m),
            wind80Direction: toNumber(current.wind_direction_80m),
            wind120Speed: toNumber(current.wind_speed_120m),
            wind120Direction: toNumber(current.wind_direction_120m),
            windGust: toNumber(current.wind_gusts_10m),
            cloudCover: toNumber(current.cloud_cover),
            precipitation: toNumber(current.precipitation),
            shortwaveRadiation: toNumber(current.shortwave_radiation),
            isDay: toNumber(current.is_day),
            boundaryLayerHeight: nearestHourlyValue(raw.hourly, "boundary_layer_height"),
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

  function rawWeatherValue(raw, ...keys) {
    for (const key of keys) {
      const value = toNumber(raw?.[key]);
      if (value !== null) return value;
    }
    return null;
  }

  function mergeCurrent(raw, fetched) {
    return {
      temperature: rawWeatherValue(raw, "temperature_c", "temperature", "temperature_2m") ?? fetched.temperature ?? 15,
      humidity: rawWeatherValue(raw, "relative_humidity_percent", "humidity_percent", "relative_humidity_2m") ?? fetched.humidity ?? null,
      wind10Speed: rawWeatherValue(raw, "wind_speed_ms", "wind_speed_10m") ?? fetched.wind10Speed ?? null,
      wind10Direction: rawWeatherValue(raw, "wind_direction_degrees", "wind_direction_10m") ?? fetched.wind10Direction ?? null,
      wind80Speed: fetched.wind80Speed ?? null,
      wind80Direction: fetched.wind80Direction ?? null,
      wind120Speed: fetched.wind120Speed ?? null,
      wind120Direction: fetched.wind120Direction ?? null,
      windGust: rawWeatherValue(raw, "wind_gust_ms", "wind_gusts_10m") ?? fetched.windGust ?? null,
      cloudCover: fetched.cloudCover ?? null,
      precipitation: rawWeatherValue(raw, "precipitation") ?? fetched.precipitation ?? null,
      shortwaveRadiation: fetched.shortwaveRadiation ?? null,
      isDay: fetched.isDay ?? null,
      boundaryLayerHeight: fetched.boundaryLayerHeight ?? null,
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
    return { className, dominant, label: labels[dominant] || "neutral" };
  }

  function dispersionSigma(distanceMeters, stabilityClass) {
    const x = Math.max(25, distanceMeters);
    const formulas = {
      A: [0.22 * x / Math.sqrt(1 + 0.0001 * x), 0.20 * x],
      B: [0.16 * x / Math.sqrt(1 + 0.0001 * x), 0.12 * x],
      C: [0.11 * x / Math.sqrt(1 + 0.0001 * x), 0.08 * x / Math.sqrt(1 + 0.0002 * x)],
      D: [0.08 * x / Math.sqrt(1 + 0.0001 * x), 0.06 * x / Math.sqrt(1 + 0.0015 * x)],
      E: [0.06 * x / Math.sqrt(1 + 0.0001 * x), 0.03 * x / (1 + 0.0003 * x)],
      F: [0.04 * x / Math.sqrt(1 + 0.0001 * x), 0.016 * x / (1 + 0.0003 * x)],
    };
    const [sigmaY, sigmaZ] = formulas[stabilityClass] || formulas.D;
    return { sigmaY: Math.max(8, sigmaY), sigmaZ: Math.max(5, sigmaZ) };
  }

  function interpolateForecastSample(current, forecast, minute) {
    const samples = [{ ...current, minute: 0 }, ...forecast.filter((item) => item.minute > 0)]
      .sort((a, b) => a.minute - b.minute);
    let left = samples[0];
    let right = samples[samples.length - 1];
    for (let index = 0; index < samples.length - 1; index += 1) {
      if (minute >= samples[index].minute && minute <= samples[index + 1].minute) {
        left = samples[index];
        right = samples[index + 1];
        break;
      }
    }
    const span = Math.max(1, right.minute - left.minute);
    const t = clamp((minute - left.minute) / span, 0, 1);
    const lerp = (a, b) => {
      if (a === null || a === undefined) return b ?? null;
      if (b === null || b === undefined) return a;
      return a + (b - a) * t;
    };
    return {
      minute,
      wind10Speed: lerp(left.wind10Speed, right.wind10Speed),
      wind10Direction: lerpAngle(left.wind10Direction, right.wind10Direction, t),
      wind80Speed: lerp(left.wind80Speed, right.wind80Speed),
      wind80Direction: lerpAngle(left.wind80Direction, right.wind80Direction, t),
      wind120Speed: lerp(left.wind120Speed, right.wind120Speed),
      wind120Direction: lerpAngle(left.wind120Direction, right.wind120Direction, t),
      windGust: lerp(left.windGust, right.windGust),
      humidity: lerp(left.humidity, right.humidity),
      cloudCover: lerp(left.cloudCover, right.cloudCover),
      precipitation: lerp(left.precipitation, right.precipitation),
      shortwaveRadiation: lerp(left.shortwaveRadiation, right.shortwaveRadiation),
      isDay: left.isDay ?? right.isDay ?? current.isDay,
      temperature: lerp(left.temperature, right.temperature) ?? current.temperature,
      boundaryLayerHeight: current.boundaryLayerHeight,
    };
  }

  function interpolateWindByHeight(sample, heightMeters) {
    const levels = [
      { h: 10, speed: sample.wind10Speed, direction: sample.wind10Direction },
      { h: 80, speed: sample.wind80Speed, direction: sample.wind80Direction },
      { h: 120, speed: sample.wind120Speed, direction: sample.wind120Direction },
    ].filter((item) => item.speed !== null && item.direction !== null);
    if (!levels.length) return { speed: null, to: null, from: null };
    if (levels.length === 1) return vectorToWind(vectorFromWind(levels[0].speed, levels[0].direction));
    levels.sort((a, b) => a.h - b.h);
    if (heightMeters <= levels[0].h) return vectorToWind(vectorFromWind(levels[0].speed, levels[0].direction));
    if (heightMeters >= levels[levels.length - 1].h) {
      const top = levels[levels.length - 1];
      return vectorToWind(vectorFromWind(top.speed, top.direction));
    }
    let lower = levels[0];
    let upper = levels[1];
    for (let index = 0; index < levels.length - 1; index += 1) {
      if (heightMeters >= levels[index].h && heightMeters <= levels[index + 1].h) {
        lower = levels[index];
        upper = levels[index + 1];
        break;
      }
    }
    const t = (heightMeters - lower.h) / Math.max(1, upper.h - lower.h);
    const lowerVector = vectorFromWind(lower.speed, lower.direction);
    const upperVector = vectorFromWind(upper.speed, upper.direction);
    return vectorToWind({
      east: lowerVector.east + (upperVector.east - lowerVector.east) * t,
      north: lowerVector.north + (upperVector.north - lowerVector.north) * t,
    });
  }

  function fireModelInputs(current) {
    const profile = FIRE_PROFILES[fireProfileKey];
    const intensity = INTENSITY_FACTORS[intensityKey];
    const area = clamp(toNumber(burningAreaM2) || profile.defaultArea, 1, 10000);
    const hrrMw = (area * profile.hrrDensityKwM2 * intensity.factor) / 1000;
    const convectiveMw = hrrMw * 0.60;
    const tempK = (current.temperature ?? 15) + KELVIN_OFFSET;
    const buoyancyFlux = G * convectiveMw * 1e6 / (AIR_DENSITY * AIR_CP * tempK);
    return { profile, intensity, area, hrrMw, convectiveMw, buoyancyFlux };
  }

  function plumeHeight(distanceMeters, windSpeed, fire, current) {
    const x = Math.max(15, distanceMeters);
    const u = Math.max(0.8, windSpeed || current.wind10Speed || 2);
    const rise = 1.6 * Math.cbrt(Math.max(0.01, fire.buoyancyFlux)) * Math.pow(x, 2 / 3) / u;
    let center = fire.profile.sourceHeightM + rise;
    const pbl = current.boundaryLayerHeight;
    if (pbl !== null && pbl > 50) {
      center = Math.min(center, Math.max(fire.profile.sourceHeightM + 20, pbl * 0.9));
    } else {
      center = Math.min(center, 900);
    }
    return Math.max(fire.profile.sourceHeightM, center);
  }

  function buildModel(coordinates, current, forecast) {
    const stability = estimateStability(current);
    const fire = fireModelInputs(current);
    const maxMinute = showFuture ? 60 : 15;
    const points = [{
      minute: 0,
      point: [coordinates.latitude, coordinates.longitude],
      distance: 0,
      centerHeight: fire.profile.sourceHeightM,
      lowerHeight: 0,
      upperHeight: fire.profile.sourceHeightM + 10,
      rawImpact: 0,
      impact: 0,
      sigmaY: 10,
      sigmaZ: 5,
      wind: interpolateWindByHeight(current, fire.profile.sourceHeightM),
      sample: current,
    }];
    let cursor = points[0].point;
    let totalDistance = 0;
    let previousHeight = fire.profile.sourceHeightM;

    for (let minute = STEP_MINUTES; minute <= maxMinute; minute += STEP_MINUTES) {
      const sample = interpolateForecastSample(current, forecast, minute - STEP_MINUTES / 2);
      let effectiveWind = interpolateWindByHeight(sample, previousHeight);
      if (effectiveWind.speed === null || effectiveWind.to === null) {
        effectiveWind = interpolateWindByHeight(current, previousHeight);
      }
      if (effectiveWind.speed === null || effectiveWind.to === null) break;
      const segmentDistance = Math.max(0, effectiveWind.speed) * STEP_MINUTES * 60;
      cursor = destinationPoint(cursor[0], cursor[1], effectiveWind.to, segmentDistance);
      totalDistance += segmentDistance;
      const centerHeight = plumeHeight(totalDistance, effectiveWind.speed, fire, current);
      const { sigmaY, sigmaZ } = dispersionSigma(totalDistance, stability.dominant);
      const lowerHeight = Math.max(0, centerHeight - 2 * sigmaZ);
      const upperHeight = centerHeight + 2 * sigmaZ;
      const rawImpact = (
        fire.hrrMw /
        (Math.max(0.8, effectiveWind.speed) * sigmaY * sigmaZ)
      ) * Math.exp(-(centerHeight * centerHeight) / (2 * sigmaZ * sigmaZ));
      points.push({
        minute,
        point: cursor,
        distance: totalDistance,
        centerHeight,
        lowerHeight,
        upperHeight,
        rawImpact,
        impact: 0,
        sigmaY,
        sigmaZ,
        wind: effectiveWind,
        sample,
      });
      previousHeight = centerHeight;
    }

    const maxImpact = Math.max(...points.map((item) => item.rawImpact), 1e-12);
    points.forEach((item) => { item.impact = item.rawImpact / maxImpact; });
    return { points, stability, fire, maxImpact };
  }

  function segmentPolygon(start, end, widthStart, widthEnd) {
    const bearing = bearingBetween(start, end);
    return [
      destinationPoint(start[0], start[1], bearing - 90, widthStart),
      destinationPoint(end[0], end[1], bearing - 90, widthEnd),
      destinationPoint(end[0], end[1], bearing + 90, widthEnd),
      destinationPoint(start[0], start[1], bearing + 90, widthStart),
    ];
  }

  function impactStyle(impact) {
    if (impact >= 0.5) return { color: "#ef4444", fill: "#ef4444", opacity: 0.28, label: "høj relativ jordpåvirkning" };
    if (impact >= 0.2) return { color: "#f97316", fill: "#f97316", opacity: 0.22, label: "moderat relativ jordpåvirkning" };
    if (impact >= 0.05) return { color: "#facc15", fill: "#facc15", opacity: 0.16, label: "lav relativ jordpåvirkning" };
    return { color: "#94a3b8", fill: "#94a3b8", opacity: 0.08, label: "meget fortyndet i modellen" };
  }

  function modelConfidence(current, forecast) {
    let score = 94;
    const notes = [];
    if (current.wind10Speed === null || current.wind10Direction === null) return { score: 15, label: "Lav", notes: ["manglende vinddata"] };
    if (current.wind10Speed < 1) { score -= 45; notes.push("meget svag vind"); }
    else if (current.wind10Speed < 2) { score -= 22; notes.push("svag vind"); }
    const gustRatio = current.windGust !== null && current.wind10Speed > 0 ? current.windGust / current.wind10Speed : 1;
    if (gustRatio > 1.8) { score -= 16; notes.push("kraftige vindstød"); }
    else if (gustRatio > 1.45) { score -= 8; notes.push("vindstød"); }
    const dirs = [current.wind10Direction, current.wind80Direction, current.wind120Direction].filter((value) => value !== null);
    if (dirs.length >= 2) {
      let shear = 0;
      dirs.forEach((a) => dirs.forEach((b) => { shear = Math.max(shear, angleDifference(a, b)); }));
      if (shear > 45) { score -= 18; notes.push("stor vinddrejning med højden"); }
      else if (shear > 25) { score -= 9; notes.push("vinddrejning med højden"); }
    } else {
      score -= 10;
      notes.push("begrænset vindprofil");
    }
    if (!forecast.length) { score -= 12; notes.push("mangler korttidsprognose"); }
    if (current.boundaryLayerHeight === null) { score -= 6; notes.push("blandingshøjde ikke tilgængelig"); }
    score = clamp(Math.round(score), 10, 98);
    return { score, label: score >= 75 ? "Høj" : score >= 50 ? "Middel" : "Lav", notes };
  }

  function findSustainedThreshold(points, key, threshold, startIndex = 1) {
    for (let index = startIndex; index < points.length; index += 1) {
      if (points[index][key] <= threshold && points.slice(index).every((item) => item[key] <= threshold)) {
        return points[index];
      }
    }
    return null;
  }

  function findElevatedPoint(points) {
    for (let index = 1; index < points.length; index += 1) {
      const point = points[index];
      if (point.lowerHeight >= 30 && point.impact <= 0.2 && points.slice(index).every((item) => item.impact <= 0.25)) {
        return point;
      }
    }
    return null;
  }

  function interpolateTrajectoryPoint(points, minute) {
    if (!points.length) return null;
    if (minute <= points[0].minute) return points[0];
    if (minute >= points[points.length - 1].minute) return points[points.length - 1];
    for (let index = 0; index < points.length - 1; index += 1) {
      const a = points[index];
      const b = points[index + 1];
      if (minute >= a.minute && minute <= b.minute) {
        const t = (minute - a.minute) / Math.max(1, b.minute - a.minute);
        return {
          minute,
          point: [a.point[0] + (b.point[0] - a.point[0]) * t, a.point[1] + (b.point[1] - a.point[1]) * t],
          distance: a.distance + (b.distance - a.distance) * t,
          centerHeight: a.centerHeight + (b.centerHeight - a.centerHeight) * t,
          sigmaY: a.sigmaY + (b.sigmaY - a.sigmaY) * t,
          wind: {
            speed: a.wind.speed + (b.wind.speed - a.wind.speed) * t,
            to: lerpAngle(a.wind.to, b.wind.to, t),
          },
        };
      }
    }
    return points[points.length - 1];
  }

  function falloutEstimate(model, current) {
    const releasePoint = interpolateTrajectoryPoint(model.points, Math.min(10, model.points[model.points.length - 1].minute));
    if (!releasePoint) return [];
    const rainFactor = (current.precipitation ?? 0) >= 1 ? 0.45 : (current.precipitation ?? 0) > 0.1 ? 0.7 : 1;
    return Object.values(PARTICLES).map((particle) => {
      let settlingSeconds = releasePoint.centerHeight / particle.settlingVelocity;
      settlingSeconds *= rainFactor;
      const settlingMinutes = settlingSeconds / 60;
      const peakMinute = Math.min(model.points[model.points.length - 1].minute, 10 + settlingMinutes);
      const target = interpolateTrajectoryPoint(model.points, peakMinute);
      const beyondWindow = 10 + settlingMinutes > model.points[model.points.length - 1].minute;
      return {
        ...particle,
        peakMinute,
        beyondWindow,
        target,
        peakDistance: target?.distance ?? null,
        releaseHeight: releasePoint.centerHeight,
        rainEnhanced: rainFactor < 1,
      };
    });
  }

  function clearLayers(L) {
    Object.values(layerGroups).forEach((group) => {
      if (group && map?.hasLayer(group)) map.removeLayer(group);
    });
    layerGroups = {
      incident: L.layerGroup().addTo(map),
      smoke: L.layerGroup().addTo(map),
      ground: L.layerGroup(),
      fallout: L.layerGroup(),
      height: L.layerGroup(),
    };
    syncLayerVisibility();
  }

  function syncLayerVisibility() {
    if (!map) return;
    const settings = {
      ground: showGround,
      fallout: showFallout,
      height: showHeight,
    };
    Object.entries(settings).forEach(([key, visible]) => {
      const group = layerGroups[key];
      if (!group) return;
      if (visible && !map.hasLayer(group)) group.addTo(map);
      if (!visible && map.hasLayer(group)) map.removeLayer(group);
    });
  }

  function renderModel(L, coordinates, current, forecast, asbestos) {
    clearLayers(L);
    const model = buildModel(coordinates, current, forecast);
    const confidence = modelConfidence(current, forecast);
    const points = model.points;

    const fireIcon = L.divIcon({
      className: "ib-v3-fire-icon",
      html: "<span>🔥</span>",
      iconSize: [34, 34],
      iconAnchor: [17, 25],
    });
    L.marker([coordinates.latitude, coordinates.longitude], { icon: fireIcon })
      .bindTooltip("Hændelsessted", { direction: "top" })
      .addTo(layerGroups.incident);

    if (current.wind10Speed !== null && current.wind10Speed < LOW_WIND_MS) {
      const radius = Math.max(350, (current.windGust ?? 1.5) * 15 * 60);
      L.circle([coordinates.latitude, coordinates.longitude], {
        radius,
        color: "#facc15",
        weight: 1.5,
        fillColor: "#facc15",
        fillOpacity: 0.08,
      }).bindTooltip("Meget svag vind · retning usikker").addTo(layerGroups.smoke);
    }

    for (let index = 1; index < points.length; index += 1) {
      const a = points[index - 1];
      const b = points[index];
      const widthA = Math.max(25, a.sigmaY * 2.0);
      const widthB = Math.max(35, b.sigmaY * 2.0);
      const smokePolygon = segmentPolygon(a.point, b.point, widthA, widthB);
      L.polygon(smokePolygon, {
        color: "#cbd5e1",
        weight: 0.7,
        opacity: 0.25,
        fillColor: "#94a3b8",
        fillOpacity: 0.06,
        interactive: false,
      }).addTo(layerGroups.smoke);

      const style = impactStyle(b.impact);
      const groundPolygon = segmentPolygon(a.point, b.point, Math.max(18, a.sigmaY * 1.1), Math.max(24, b.sigmaY * 1.1));
      L.polygon(groundPolygon, {
        color: style.color,
        weight: 1.0,
        opacity: 0.62,
        fillColor: style.fill,
        fillOpacity: style.opacity,
      }).bindTooltip(
        `<strong>+${b.minute} min · ${formatDistance(b.distance)}</strong><br>` +
        `Relativ jordpåvirkning: ${Math.round(b.impact * 100)}%<br>` +
        `Røgcenter: ca. ${Math.round(b.centerHeight)} m<br>` +
        `Nedre modelkant: ca. ${Math.round(b.lowerHeight)} m`,
        { sticky: true }
      ).addTo(layerGroups.ground);
    }

    L.polyline(points.map((item) => item.point), {
      color: "#f8fafc",
      weight: 2,
      opacity: 0.78,
      dashArray: "8 8",
      interactive: false,
    }).addTo(layerGroups.smoke);

    points.filter((item) => [15, 30, 45, 60].includes(item.minute)).forEach((item) => {
      const marker = L.circleMarker(item.point, {
        radius: 4,
        color: "#60a5fa",
        fillColor: "#0f172a",
        fillOpacity: 1,
        weight: 2,
      }).addTo(layerGroups.height);
      marker.bindTooltip(
        `<strong>+${item.minute} min</strong><br>` +
        `Afstand: ${formatDistance(item.distance)}<br>` +
        `Røgcenter: ~${Math.round(item.centerHeight)} m<br>` +
        `Modelinterval: ${Math.round(item.lowerHeight)}–${Math.round(item.upperHeight)} m`,
        { permanent: item.minute === 15 || item.minute === 60, direction: "top", className: "ib-v3-time-label" }
      );
    });

    const fallout = falloutEstimate(model, current);
    fallout.forEach((item) => {
      if (!item.target || item.peakDistance === null) return;
      const startDistance = Math.max(100, item.peakDistance * (item.className === "coarse" ? 0.35 : 0.20));
      const endDistance = Math.max(startDistance + 100, item.peakDistance * (item.beyondWindow ? 1.05 : 1.55));
      const bearing = points.length > 1 ? bearingBetween(points[0].point, item.target.point) : (points[0].wind.to || 0);
      const start = destinationPoint(coordinates.latitude, coordinates.longitude, bearing, startDistance);
      const end = destinationPoint(coordinates.latitude, coordinates.longitude, bearing, endDistance);
      const sigma = dispersionSigma(Math.max(100, item.peakDistance), model.stability.dominant).sigmaY;
      const polygon = segmentPolygon(start, end, Math.max(45, sigma * 0.7), Math.max(80, sigma * 1.3));
      const isCoarse = item.className === "coarse";
      L.polygon(polygon, {
        color: isCoarse ? "#d97706" : "#a78bfa",
        weight: 1.4,
        opacity: 0.72,
        dashArray: "6 6",
        fillColor: isCoarse ? "#d97706" : "#8b5cf6",
        fillOpacity: isCoarse ? 0.12 : 0.08,
      }).bindTooltip(
        `<strong>${item.label}</strong><br>` +
        `${item.beyondWindow ? "Kan fortsætte ud over +60 min" : `Tyngdepunkt omkring ${formatDistance(item.peakDistance)}`}<br>` +
        `${item.rainEnhanced ? "Nedbør øger forventet udvaskning." : "Tørdeposition er stærkt afhængig af partikelstørrelse."}`,
        { sticky: true }
      ).addTo(layerGroups.fallout);
    });

    const diluted = findSustainedThreshold(points, "impact", 0.10, 2);
    const elevated = findElevatedPoint(points);
    renderSummary({ model, current, confidence, diluted, elevated, fallout, asbestos });
    renderLegend();
    syncLayerVisibility();

    const bounds = L.latLngBounds(points.map((item) => item.point));
    Object.values(layerGroups).forEach((group) => {
      group?.eachLayer?.((layer) => {
        if (typeof layer.getBounds === "function") bounds.extend(layer.getBounds());
        else if (typeof layer.getLatLng === "function") bounds.extend(layer.getLatLng());
      });
    });
    return bounds;
  }

  function metricCard(title, value, detail, tone = "") {
    const card = document.createElement("div");
    card.className = `ib-v3-metric ${tone}`.trim();
    const titleEl = document.createElement("span");
    titleEl.className = "ib-v3-metric-title";
    titleEl.textContent = title;
    const valueEl = document.createElement("strong");
    valueEl.textContent = value;
    const detailEl = document.createElement("small");
    detailEl.textContent = detail;
    card.append(titleEl, valueEl, detailEl);
    return card;
  }

  function renderSummary({ model, current, confidence, diluted, elevated, fallout, asbestos }) {
    if (!summaryHost) return;
    summaryHost.hidden = false;
    summaryHost.replaceChildren();
    const heading = document.createElement("div");
    heading.className = "ib-v3-summary-heading";
    const effective = model.points[1]?.wind || interpolateWindByHeight(current, model.fire.profile.sourceHeightM);
    const direction = effective.to === null ? "usikker retning" : `mod ${cardinalDirection(effective.to)}`;
    heading.innerHTML = `<div><strong>${MODEL_VERSION}</strong><span>Screening for spredning, plume-rise og nedfald</span></div>`;
    const confidenceBadge = document.createElement("span");
    confidenceBadge.className = `ib-v3-confidence ${confidence.label.toLowerCase()}`;
    confidenceBadge.textContent = `${confidence.label} tillid · ${confidence.score}%`;
    heading.appendChild(confidenceBadge);
    summaryHost.appendChild(heading);

    const metrics = document.createElement("div");
    metrics.className = "ib-v3-metrics";
    metrics.append(
      metricCard("Røgdrift", direction, `${(effective.speed ?? current.wind10Speed ?? 0).toFixed(1)} m/s · stabilitet ${model.stability.className}`),
      metricCard(
        "Markant fortyndet",
        diluted ? `fra ca. ${formatDistance(diluted.distance)}` : "> +60 min",
        diluted ? "<10% af modellens højeste relative jordpåvirkning" : "Falder ikke vedvarende under 10% i modelvinduet",
        diluted ? "good" : "warn"
      ),
      metricCard(
        "Røg løftet",
        elevated ? `fra ca. ${formatDistance(elevated.distance)}` : "ikke entydigt",
        elevated ? `nedre modelkant ~${Math.round(elevated.lowerHeight)} m og lav jordkontakt` : "røgfanen kan fortsat blande ned mod terræn",
        elevated ? "good" : "warn"
      ),
      metricCard(
        "Brandantagelse",
        `~${model.fire.hrrMw.toFixed(model.fire.hrrMw >= 100 ? 0 : 1)} MW`,
        `${Math.round(model.fire.area)} m² · ${model.fire.intensity.label.toLowerCase()}`
      )
    );
    summaryHost.appendChild(metrics);

    const details = document.createElement("div");
    details.className = "ib-v3-detail-grid";
    const finalPoint = model.points[model.points.length - 1];
    const pbl = current.boundaryLayerHeight !== null ? `${Math.round(current.boundaryLayerHeight)} m` : "ikke tilgængelig";
    details.innerHTML = `
      <div><span>+${finalPoint.minute} min transport</span><strong>${formatDistance(finalPoint.distance)}</strong></div>
      <div><span>Røgcenter +${finalPoint.minute}</span><strong>~${Math.round(finalPoint.centerHeight)} m</strong></div>
      <div><span>Blandingshøjde</span><strong>${pbl}</strong></div>
      <div><span>Relativ jordpåvirkning +${finalPoint.minute}</span><strong>${Math.round(finalPoint.impact * 100)}%</strong></div>
    `;
    summaryHost.appendChild(details);

    const falloutLine = document.createElement("div");
    falloutLine.className = "ib-v3-note";
    const coarse = fallout.find((item) => item.className === "coarse");
    const soot = fallout.find((item) => item.className === "soot");
    const coarseText = coarse
      ? (coarse.beyondWindow ? "groft nedfald kan fortsætte ud over modelvinduet" : `groft nedfald har vejledende tyngdepunkt omkring ${formatDistance(coarse.peakDistance)}`)
      : "groft nedfald kunne ikke estimeres";
    const sootText = soot
      ? (soot.beyondWindow ? "sod/grove partikler kan transporteres længere end +60 min" : `sod/grove partikler omkring ${formatDistance(soot.peakDistance)}`)
      : "";
    falloutLine.textContent = `Nedfald: ${coarseText}${sootText ? ` · ${sootText}` : ""}.`;
    summaryHost.appendChild(falloutLine);

    if (asbestos.registered) {
      const asbestosNote = document.createElement("div");
      asbestosNote.className = "ib-v3-asbestos-note";
      asbestosNote.textContent = "⚠ BBR har asbest registreret. Nedfaldsoverlayet viser kun fysisk partikeltransport – det dokumenterer ikke asbestkoncentration eller forurening.";
      summaryHost.appendChild(asbestosNote);
    }

    const disclaimer = document.createElement("div");
    disclaimer.className = "ib-v3-disclaimer";
    const confidenceNotes = confidence.notes.length ? ` Modelusikkerhed: ${confidence.notes.join(", ")}.` : "";
    disclaimer.textContent = `Screeningmodel: farver viser relativ jordpåvirkning, ikke giftighed. “Fortyndet” er ikke det samme som sikker luft. Faktisk risiko afhænger af hvad der brænder, emissionsrater, målinger og lokale forhold.${confidenceNotes}`;
    summaryHost.appendChild(disclaimer);
  }

  function renderLegend() {
    if (!legendHost) return;
    legendHost.innerHTML = `
      <div class="ib-v3-legend-title">Kortlag</div>
      <div><i class="red"></i>Høj relativ jordpåvirkning</div>
      <div><i class="orange"></i>Moderat</div>
      <div><i class="yellow"></i>Lav</div>
      <div><i class="grey"></i>Meget fortyndet</div>
      <div><i class="fallout"></i>Groft nedfald</div>
      <div><i class="soot"></i>Sod/grove partikler</div>
    `;
  }

  function loadPreferences() {
    try {
      const savedProfile = localStorage.getItem("indsatsbrief.smokeV3.profile");
      const savedIntensity = localStorage.getItem("indsatsbrief.smokeV3.intensity");
      if (savedProfile && FIRE_PROFILES[savedProfile]) fireProfileKey = savedProfile;
      if (savedIntensity && INTENSITY_FACTORS[savedIntensity]) intensityKey = savedIntensity;
    } catch (_) {}
    burningAreaM2 = FIRE_PROFILES[fireProfileKey].defaultArea;
  }

  function savePreference(key, value) {
    try { localStorage.setItem(key, String(value)); } catch (_) {}
  }

  function buildControls() {
    if (!controlsHost) return;
    controlsHost.replaceChildren();

    const profileLabel = document.createElement("label");
    profileLabel.className = "ib-v3-control ib-v3-profile";
    profileLabel.innerHTML = "<span>Brandstørrelse</span>";
    const profileSelect = document.createElement("select");
    Object.entries(FIRE_PROFILES).forEach(([key, profile]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = `${profile.label} · ${profile.detail}`;
      option.selected = key === fireProfileKey;
      profileSelect.appendChild(option);
    });
    profileSelect.addEventListener("change", () => {
      fireProfileKey = profileSelect.value;
      burningAreaM2 = FIRE_PROFILES[fireProfileKey].defaultArea;
      savePreference("indsatsbrief.smokeV3.profile", fireProfileKey);
      buildControls();
      rerender();
    });
    profileLabel.appendChild(profileSelect);

    const areaLabel = document.createElement("label");
    areaLabel.className = "ib-v3-control";
    areaLabel.innerHTML = "<span>Brændende areal</span>";
    const areaWrap = document.createElement("div");
    areaWrap.className = "ib-v3-input-suffix";
    const areaInput = document.createElement("input");
    areaInput.type = "number";
    areaInput.min = "1";
    areaInput.max = "10000";
    areaInput.step = "5";
    areaInput.value = String(Math.round(burningAreaM2));
    const suffix = document.createElement("span");
    suffix.textContent = "m²";
    areaInput.addEventListener("change", () => {
      burningAreaM2 = clamp(toNumber(areaInput.value) || FIRE_PROFILES[fireProfileKey].defaultArea, 1, 10000);
      areaInput.value = String(Math.round(burningAreaM2));
      rerender();
    });
    areaWrap.append(areaInput, suffix);
    areaLabel.appendChild(areaWrap);

    const intensityLabel = document.createElement("label");
    intensityLabel.className = "ib-v3-control";
    intensityLabel.innerHTML = "<span>Brandintensitet</span>";
    const intensitySelect = document.createElement("select");
    Object.entries(INTENSITY_FACTORS).forEach(([key, intensity]) => {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = intensity.label;
      option.selected = key === intensityKey;
      intensitySelect.appendChild(option);
    });
    intensitySelect.addEventListener("change", () => {
      intensityKey = intensitySelect.value;
      savePreference("indsatsbrief.smokeV3.intensity", intensityKey);
      rerender();
    });
    intensityLabel.appendChild(intensitySelect);

    const toggles = document.createElement("div");
    toggles.className = "ib-v3-toggles";
    const toggleDefinitions = [
      ["Jordpåvirkning", () => showGround, (value) => { showGround = value; }],
      ["Nedfald", () => showFallout, (value) => { showFallout = value; }],
      ["Røghøjde", () => showHeight, (value) => { showHeight = value; }],
      ["+60 min", () => showFuture, (value) => { showFuture = value; rerender(); }],
    ];
    toggleDefinitions.forEach(([labelText, getter, setter]) => {
      const label = document.createElement("label");
      label.className = "ib-v3-toggle";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = getter();
      checkbox.addEventListener("change", () => {
        setter(checkbox.checked);
        syncLayerVisibility();
      });
      label.append(checkbox, document.createTextNode(labelText));
      toggles.appendChild(label);
    });

    controlsHost.append(profileLabel, areaLabel, intensityLabel, toggles);
  }

  function ensureHosts() {
    mapFrame = document.getElementById("map-frame");
    if (!mapFrame) return false;
    if (!mapHost) {
      mapHost = document.createElement("div");
      mapHost.id = "ib-operational-map";
      mapHost.setAttribute("role", "region");
      mapHost.setAttribute("aria-label", `${MODEL_VERSION} på OpenStreetMap`);
      mapFrame.insertAdjacentElement("afterend", mapHost);
      mapFrame.style.display = "none";
    }
    if (!legendHost) {
      legendHost = document.createElement("div");
      legendHost.id = "ib-v3-legend";
      mapHost.appendChild(legendHost);
    }
    if (!summaryHost) {
      summaryHost = document.createElement("div");
      summaryHost.id = "ib-smoke-summary";
      summaryHost.hidden = true;
      mapHost.insertAdjacentElement("afterend", summaryHost);
    }
    if (!controlsHost) {
      controlsHost = document.createElement("div");
      controlsHost.id = "ib-smoke-controls";
      summaryHost.insertAdjacentElement("afterend", controlsHost);
      buildControls();
    }
    return true;
  }

  function loadLeaflet() {
    if (window.L?.map) return Promise.resolve(window.L);
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise((resolve, reject) => {
      if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = LEAFLET_CSS;
        document.head.appendChild(link);
      }
      const existing = document.querySelector(`script[src="${LEAFLET_JS}"]`);
      if (existing) {
        existing.addEventListener("load", () => resolve(window.L), { once: true });
        existing.addEventListener("error", reject, { once: true });
        return;
      }
      const script = document.createElement("script");
      script.src = LEAFLET_JS;
      script.onload = () => resolve(window.L);
      script.onerror = () => reject(new Error("Leaflet kunne ikke indlæses"));
      document.head.appendChild(script);
    });
    return leafletPromise;
  }

  function rerender() {
    if (!latestContext || !window.L) return;
    const { L, coordinates, current, forecast, asbestos } = latestContext;
    const bounds = renderModel(L, coordinates, current, forecast, asbestos);
    requestAnimationFrame(() => {
      map.invalidateSize();
      if (bounds?.isValid?.()) map.fitBounds(bounds.pad(0.08), { maxZoom: 15, animate: false });
    });
  }

  async function render() {
    const sequence = ++renderSequence;
    if (!ensureHosts()) return;
    const incidentData = currentIncidentData();
    const coordinates = extractCoordinates(incidentData) || coordinatesFromFrame(mapFrame);
    if (!coordinates) return;
    try {
      const L = await loadLeaflet();
      if (sequence !== renderSequence) return;
      if (!map) {
        map = L.map(mapHost, { zoomControl: true, attributionControl: true });
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);
      }
      const fetched = await fetchWeather(coordinates.latitude, coordinates.longitude);
      if (sequence !== renderSequence) return;
      const current = mergeCurrent(extractWeather(incidentData), fetched.current || {});
      const forecast = Array.isArray(fetched.forecast) ? fetched.forecast : [];
      const asbestos = detectAsbestos(incidentData);
      latestContext = { L, coordinates, current, forecast, asbestos };
      const bounds = renderModel(L, coordinates, current, forecast, asbestos);
      requestAnimationFrame(() => {
        map.invalidateSize();
        if (bounds?.isValid?.()) map.fitBounds(bounds.pad(0.08), { maxZoom: 15, animate: false });
        else map.setView([coordinates.latitude, coordinates.longitude], 15);
      });
    } catch (error) {
      console.warn(`[IndsatsBrief] ${MODEL_VERSION} kunne ikke indlæses`, error);
      mapFrame.style.display = "";
      mapHost?.remove();
      mapHost = null;
      if (summaryHost) {
        summaryHost.hidden = false;
        summaryHost.textContent = "Røgmodel kunne ikke indlæses. OpenStreetMap vises uden model-overlay.";
      }
    }
  }

  function start() {
    loadPreferences();
    if (!ensureHosts()) return;
    buildControls();
    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => mutation.type === "attributes" && mutation.attributeName === "src")) render();
    });
    observer.observe(mapFrame, { attributes: true, attributeFilter: ["src"] });
    if (mapFrame.getAttribute("src")) render();
    window.addEventListener("resize", () => map?.invalidateSize());
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
