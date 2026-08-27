(() => {
  "use strict";

  const UI_VERSION = "UX 3";
  const SMOKE_MODEL_VERSION = "Røgmodel v3.0";

  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  }

  function ensureSearchSection() {
    const input = document.getElementById("address");
    const card = input?.closest(".search-card, .card, section");
    if (card && !card.id) card.id = "ib-search-section";
    return card;
  }

  function buildQuickNav() {
    if (document.querySelector(".ib-quick-nav")) return;
    const main = document.querySelector("main");
    if (!main || !document.getElementById("address")) return;

    ensureSearchSection();
    const candidates = [
      ["ib-search-section", "Søg adresse"],
      ["result", "Brief"],
      ["map-section", "Kort"],
      ["assistance-section", "Assistance"],
      ["resource-section", "Ressourcer"],
    ];
    const items = candidates.filter(([id]) => document.getElementById(id));
    if (items.length < 2) return;

    const nav = document.createElement("nav");
    nav.className = "ib-quick-nav";
    nav.setAttribute("aria-label", "Hurtig navigation i indsatsbrief");

    items.forEach(([id, label]) => {
      const link = document.createElement("a");
      link.href = `#${id}`;
      link.dataset.target = id;
      link.textContent = label;
      link.addEventListener("click", (event) => {
        const target = document.getElementById(id);
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        if (id === "ib-search-section") {
          window.setTimeout(() => document.getElementById("address")?.focus(), 250);
        }
      });
      nav.appendChild(link);
    });

    const topLine = main.querySelector(".topline, header");
    if (topLine) topLine.insertAdjacentElement("afterend", nav);
    else main.prepend(nav);

    if ("IntersectionObserver" in window) {
      const links = new Map(
        Array.from(nav.querySelectorAll("a")).map((link) => [link.dataset.target, link])
      );
      const observer = new IntersectionObserver(
        (entries) => {
          const visible = entries
            .filter((entry) => entry.isIntersecting)
            .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
          if (!visible) return;
          links.forEach((link) => link.classList.remove("ib-active"));
          links.get(visible.target.id)?.classList.add("ib-active");
        },
        { rootMargin: "-18% 0px -65% 0px", threshold: [0.05, 0.2, 0.5] }
      );
      items.forEach(([id]) => {
        const target = document.getElementById(id);
        if (target) observer.observe(target);
      });
    }
  }

  function mapIsExpanded(mapSection) {
    return document.fullscreenElement === mapSection || mapSection.classList.contains("ib-map-expanded");
  }

  function syncMapButton(mapSection, button) {
    const expanded = mapIsExpanded(mapSection);
    button.textContent = expanded ? "× Luk stort kort" : "⛶ Forstør kort";
    button.setAttribute("aria-pressed", expanded ? "true" : "false");
    document.body.classList.toggle("ib-map-lock", mapSection.classList.contains("ib-map-expanded"));
    window.setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }

  async function toggleExpandedMap(mapSection, button) {
    if (document.fullscreenElement === mapSection) {
      await document.exitFullscreen?.();
      syncMapButton(mapSection, button);
      return;
    }

    if (mapSection.classList.contains("ib-map-expanded")) {
      mapSection.classList.remove("ib-map-expanded");
      syncMapButton(mapSection, button);
      return;
    }

    if (typeof mapSection.requestFullscreen === "function") {
      try {
        await mapSection.requestFullscreen();
        syncMapButton(mapSection, button);
        return;
      } catch (_) {
        // Fall back to a CSS fullscreen layer when the browser blocks Fullscreen API.
      }
    }

    mapSection.classList.add("ib-map-expanded");
    syncMapButton(mapSection, button);
  }

  function enhanceMap() {
    const mapSection = document.getElementById("map-section");
    if (!mapSection || mapSection.dataset.ibEnhanced === "true") return;
    mapSection.dataset.ibEnhanced = "true";

    const heading = mapSection.querySelector(":scope > h2");
    if (!heading) return;

    const headingRow = document.createElement("div");
    headingRow.className = "ib-map-heading-row";
    heading.insertAdjacentElement("beforebegin", headingRow);
    headingRow.appendChild(heading);

    const actions = document.createElement("div");
    actions.className = "ib-map-actions";

    const version = document.createElement("span");
    version.className = "ib-model-version";
    version.textContent = SMOKE_MODEL_VERSION;
    version.title = "5-minutters screening, vindprofil i højden, plume-rise, relativ jordpåvirkning og vejledende nedfald";

    const expandButton = document.createElement("button");
    expandButton.type = "button";
    expandButton.className = "ib-map-action";
    expandButton.setAttribute("aria-pressed", "false");
    expandButton.textContent = "⛶ Forstør kort";
    expandButton.addEventListener("click", () => toggleExpandedMap(mapSection, expandButton));

    actions.append(version, expandButton);
    headingRow.appendChild(actions);

    document.addEventListener("fullscreenchange", () => syncMapButton(mapSection, expandButton));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && mapSection.classList.contains("ib-map-expanded")) {
        mapSection.classList.remove("ib-map-expanded");
        syncMapButton(mapSection, expandButton);
      }
    });
  }

  function ensureRiskBanner(report) {
    const result = document.getElementById("result");
    if (!result || !report) return null;
    let banner = result.querySelector(".ib-risk-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "ib-risk-banner";
      banner.setAttribute("role", "status");
      report.insertAdjacentElement("beforebegin", banner);
    }
    return banner;
  }

  function emphasizeOperationalRisks() {
    const report = document.getElementById("report");
    if (!report) return;
    const banner = ensureRiskBanner(report);
    if (!banner) return;

    let positive = null;
    let uncertain = null;
    report.querySelectorAll("li").forEach((item) => {
      const text = (item.textContent || "").trim();
      const lower = text.toLowerCase();
      item.classList.remove("ib-asbestos-positive", "ib-asbestos-uncertain");

      if (/^asbest\s*:/i.test(text) && /registreret/i.test(text)) {
        item.classList.add("ib-asbestos-positive");
        positive ||= text;
      } else if (
        lower.includes("asbestkontrol") &&
        (lower.includes("ikke returneret") || lower.includes("ukendt") || lower.includes("kunne ikke") || lower.includes("ikke alle"))
      ) {
        item.classList.add("ib-asbestos-uncertain");
        uncertain ||= text;
      }
    });

    banner.className = "ib-risk-banner";
    banner.replaceChildren();
    if (positive) {
      banner.classList.add("ib-visible", "ib-critical");
      banner.textContent = `⚠ ${positive}`;
    } else if (uncertain) {
      banner.classList.add("ib-visible", "ib-caution");
      banner.textContent = `Asbeststatus kræver opmærksomhed · ${uncertain}`;
    }
  }

  function observeDynamicUi() {
    const report = document.getElementById("report");
    if (report) {
      const observer = new MutationObserver(() => emphasizeOperationalRisks());
      observer.observe(report, { childList: true, subtree: true, characterData: true });
      emphasizeOperationalRisks();
    }

    const pageObserver = new MutationObserver(() => enhanceMap());
    pageObserver.observe(document.body, { childList: true, subtree: true });
  }

  onReady(() => {
    document.documentElement.dataset.ibOperationalUi = UI_VERSION;
    buildQuickNav();
    enhanceMap();
    observeDynamicUi();
  });
})();
