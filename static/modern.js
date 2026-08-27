(() => {
  "use strict";

  document.documentElement.classList.add("ib-modern");

  const onReady = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else {
      callback();
    }
  };

  onReady(() => {
    // Harden links opened in a new tab.
    document.querySelectorAll('a[target="_blank"]').forEach((link) => {
      const rel = new Set((link.getAttribute("rel") || "").split(/\s+/).filter(Boolean));
      rel.add("noopener");
      rel.add("noreferrer");
      link.setAttribute("rel", Array.from(rel).join(" "));
    });

    const addressInput = document.querySelector("#address");
    const autocomplete = document.querySelector("#autocomplete-list");

    // Cmd/Ctrl+K is a fast, familiar way to jump to the incident address.
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && addressInput) {
        event.preventDefault();
        addressInput.focus();
        addressInput.select?.();
      }

      if (event.key === "Escape") {
        if (autocomplete) {
          autocomplete.replaceChildren();
          autocomplete.style.display = "none";
        }
        if (document.activeElement instanceof HTMLElement) {
          document.activeElement.blur();
        }
      }
    });

    // Remember only harmless UI preferences — never addresses or report content.
    const preferenceIds = ["radius", "assistance-radius", "assistance-limit"];
    preferenceIds.forEach((id) => {
      const element = document.getElementById(id);
      if (!element) return;

      const key = `indsatsbrief.preference.${id}`;
      try {
        const saved = localStorage.getItem(key);
        if (saved !== null && Array.from(element.options || []).some((option) => option.value === saved)) {
          element.value = saved;
        }
      } catch (_) {
        // Private browsing/storage restrictions should never affect the app.
      }

      element.addEventListener("change", () => {
        try {
          localStorage.setItem(key, element.value);
        } catch (_) {
          // Ignore storage failures.
        }
      });
    });

    // Mark clicked buttons as busy if the existing application disables them
    // while an async operation is running. We intentionally do not change the
    // application's request logic.
    let lastActionButton = null;
    document.addEventListener("click", (event) => {
      const button = event.target.closest?.("button");
      if (button instanceof HTMLButtonElement && !button.disabled) {
        lastActionButton = button;
      }
    }, true);

    const syncBusyState = (button) => {
      if (!(button instanceof HTMLButtonElement)) return;
      const busy = button.disabled && button === lastActionButton;
      button.classList.toggle("ib-busy", busy);
      button.setAttribute("aria-busy", busy ? "true" : "false");
      if (!button.disabled && button === lastActionButton) {
        lastActionButton = null;
      }
    };

    document.querySelectorAll("button").forEach((button) => {
      const observer = new MutationObserver(() => syncBusyState(button));
      observer.observe(button, { attributes: true, attributeFilter: ["disabled"] });
    });

    // Small non-invasive operational status marker on the main brief page.
    const topActions = document.querySelector(".top-actions");
    if (topActions && !topActions.querySelector(".ib-runtime-chip")) {
      const chip = document.createElement("span");
      chip.className = "ib-runtime-chip";
      chip.textContent = "IndsatsBrief online";
      chip.title = "Programmet er indlæst og klar";
      topActions.prepend(chip);
    }

    // Ctrl/Cmd+Enter sends common text-based follow-up forms when an explicit
    // button exists nearby. This remains opt-in by keyboard and does not alter
    // normal Enter behavior in textareas.
    document.querySelectorAll("textarea").forEach((textarea) => {
      textarea.addEventListener("keydown", (event) => {
        if (!(event.metaKey || event.ctrlKey) || event.key !== "Enter") return;
        const container = textarea.closest("form, .card, .tool-panel, section, .admin-card") || textarea.parentElement;
        const button = container?.querySelector("button:not(:disabled)");
        if (button) {
          event.preventDefault();
          button.click();
        }
      });
    });
  });
})();
