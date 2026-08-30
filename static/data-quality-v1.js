(() => {
  "use strict";

  const PANEL_ID = "ib-data-quality";

  const asObject = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

  function currentIncidentData() {
    try {
      return typeof latestIncidentData !== "undefined" && latestIncidentData ? latestIncidentData : null;
    } catch (_) {
      return null;
    }
  }

  function firstText(...values) {
    for (const value of values) {
      if (typeof value === "string" && value.trim()) return value.trim();
    }
    return null;
  }

  function firstTime(...values) {
    for (const value of values) {
      if (!value) continue;
      const date = new Date(value);
      if (!Number.isNaN(date.getTime())) return date;
    }
    return null;
  }

  function formatTime(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat("da-DK", {
      hour: "2-digit",
      minute: "2-digit",
      day: "2-digit",
      month: "2-digit",
    }).format(date);
  }

  function ageLabel(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return null;
    const ageSeconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
    if (ageSeconds < 120) return "under 2 min";
    if (ageSeconds < 3600) return `${Math.round(ageSeconds / 60)} min`;
    if (ageSeconds < 86400) return `${Math.round(ageSeconds / 3600)} t`;
    return `${Math.round(ageSeconds / 86400)} døgn`;
  }

  function hasUsefulWeather(weather) {
    const keys = [
      "wind_speed_ms",
      "wind_speed_10m",
      "wind_direction_degrees",
      "wind_direction_10m",
      "temperature_c",
      "temperature",
      "temperature_2m",
    ];
    return keys.some((key) => weather[key] !== undefined && weather[key] !== null && weather[key] !== "");
  }

  function buildingSource(data) {
    const short = asObject(data?.short_report_data);
    const building = asObject(data?.building || short.building);
    if (!Object.keys(building).length) return null;

    const fallback = asObject(building.provider_fallback);
    const fallbackUsed = fallback.fallback_used === true;
    const provider = fallbackUsed
      ? firstText(fallback.fallback, building.provider, building.source)
      : firstText(fallback.primary, building.provider, building.source);
    const verification = firstText(
      building.verification_status,
      building.address_match_status,
      building.match_status
    );
    const checked = firstTime(
      building.checked_at,
      building.fetched_at,
      building.updated_at,
      building.data_timestamp
    );

    const details = [];
    if (fallbackUsed) details.push("fallback anvendt");
    if (verification) details.push(verification.replaceAll("_", " "));
    if (checked) details.push(`data ${ageLabel(checked)} gammel`);

    return {
      key: "bbr",
      label: "BBR",
      value: provider || "Kilde ikke oplyst",
      detail: details.join(" · ") || "Kildetid ikke oplyst i datasættet",
      state: fallbackUsed ? "fallback" : provider ? "good" : "unknown",
      title: checked ? `Kildetid: ${formatTime(checked)}` : "BBR-data indeholder ikke et eksplicit kildetidspunkt.",
    };
  }

  function weatherSource(data) {
    const short = asObject(data?.short_report_data);
    const weather = asObject(data?.weather || short.weather);
    if (!hasUsefulWeather(weather)) return null;

    const source = firstText(weather.source, weather.provider, weather.weather_source);
    const observed = firstTime(
      weather.observed_at,
      weather.observation_time,
      weather.current_time,
      weather.time,
      weather.updated_at,
      weather.fetched_at
    );
    const age = observed ? ageLabel(observed) : null;
    const details = [];
    if (observed) details.push(`${formatTime(observed)} · ${age} gammel`);
    else details.push("kildetid ikke oplyst");

    return {
      key: "weather",
      label: "Vejr",
      value: source || "Vejrdata",
      detail: details.join(" · "),
      state: observed && Date.now() - observed.getTime() > 60 * 60 * 1000 ? "warning" : source ? "good" : "unknown",
      title: "Røganalysens egen Open-Meteo-prognose vises separat, når røganalyse startes.",
    };
  }

  function osmSource(data) {
    const short = asObject(data?.short_report_data);
    const risk = asObject(data?.osm_risk_check || short.osm_risk_check);
    const surroundings = asObject(data?.surroundings || short.surroundings);
    const sourceData = Object.keys(risk).length ? risk : surroundings;
    if (!Object.keys(sourceData).length) return null;

    const source = firstText(sourceData.source, sourceData.provider) || "OpenStreetMap";
    const failed = sourceData.ok === false || Boolean(sourceData.error);
    const degraded = sourceData.degraded === true || sourceData.cache === "stale";
    const checked = firstTime(sourceData.checked_at, sourceData.fetched_at, sourceData.updated_at);
    const details = [];
    if (failed) details.push("opslag fejlede");
    else if (degraded) details.push("delresultat/cache");
    else details.push("opslag tilgængeligt");
    if (checked) details.push(`${ageLabel(checked)} gammel`);

    return {
      key: "osm",
      label: "OSM",
      value: source,
      detail: details.join(" · "),
      state: failed ? "warning" : degraded ? "fallback" : "good",
      title: "OpenStreetMap kan være ufuldstændigt. Røgkontekst viser desuden cache/delresultat ved hvert scan.",
    };
  }

  function addressSource(data) {
    const address = asObject(data?.address);
    if (!Object.keys(address).length) return null;
    const source = firstText(address.source, address.provider, address.address_source);
    if (!source) return null;
    const checked = firstTime(address.checked_at, address.fetched_at, address.updated_at);
    return {
      key: "address",
      label: "Adresse",
      value: source,
      detail: checked ? `${formatTime(checked)} · ${ageLabel(checked)} gammel` : "kildetid ikke oplyst",
      state: "good",
      title: "Adressekilden er oplyst af datasættet.",
    };
  }

  function qualityItems(data) {
    return [addressSource(data), buildingSource(data), weatherSource(data), osmSource(data)].filter(Boolean);
  }

  function createItem(item) {
    const card = document.createElement("div");
    card.className = `ib-data-quality-item ${item.state || "unknown"}`;
    card.dataset.source = item.key;
    if (item.title) card.title = item.title;

    const label = document.createElement("span");
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.textContent = item.value;
    const detail = document.createElement("small");
    detail.textContent = item.detail;
    card.append(label, value, detail);
    return card;
  }

  function ensureHost() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;

    const glance = document.getElementById("ib-operational-glance");
    const status = document.getElementById("status");
    const grid = document.querySelector(".main-grid");
    const anchor = glance || status || grid;
    if (!anchor) return null;

    panel = document.createElement("details");
    panel.id = PANEL_ID;
    panel.className = "ib-data-quality";
    panel.setAttribute("aria-label", "Datagrundlag og kildekvalitet");

    if (glance) glance.insertAdjacentElement("afterend", panel);
    else if (status) status.insertAdjacentElement("afterend", panel);
    else grid.insertAdjacentElement("beforebegin", panel);
    return panel;
  }

  function render() {
    const data = currentIncidentData();
    const panel = ensureHost();
    if (!panel) return;
    if (!data) {
      panel.hidden = true;
      return;
    }

    const items = qualityItems(data);
    if (!items.length) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    const wasOpen = panel.open;
    panel.replaceChildren();
    panel.open = wasOpen;

    const summary = document.createElement("summary");
    const summaryText = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = "Datagrundlag";
    const states = items.map((item) => item.state);
    const status = document.createElement("small");
    status.textContent = states.includes("warning")
      ? "Kilde med fejl eller gammel data"
      : states.includes("fallback")
        ? "Fallback/delresultat anvendt"
        : states.includes("unknown")
          ? "Nogle kildedetaljer mangler"
          : "Kilder oplyst";
    summaryText.append(title, status);
    const hint = document.createElement("em");
    hint.textContent = panel.open ? "Skjul" : "Vis kilder";
    summary.append(summaryText, hint);
    summary.addEventListener("click", () => window.setTimeout(() => {
      hint.textContent = panel.open ? "Skjul" : "Vis kilder";
    }, 0));
    panel.appendChild(summary);

    const body = document.createElement("div");
    body.className = "ib-data-quality-body";
    const grid = document.createElement("div");
    grid.className = "ib-data-quality-grid";
    items.forEach((item) => grid.appendChild(createItem(item)));
    body.appendChild(grid);

    const note = document.createElement("p");
    note.textContent = "Kildepanelet viser kun det, som datasættet faktisk oplyser. Manglende kildetid eller provider bliver derfor markeret som ukendt i stedet for at blive gættet.";
    body.appendChild(note);
    panel.appendChild(body);
  }

  function observe() {
    const target = document.getElementById("report") || document.body;
    const observer = new MutationObserver(() => window.requestAnimationFrame(render));
    observer.observe(target, { childList: true, subtree: true });
    window.addEventListener("indsatsbrief:data-quality-refresh", render);
    window.setInterval(render, 60 * 1000);
  }

  function start() {
    render();
    observe();
    window.setTimeout(render, 700);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
