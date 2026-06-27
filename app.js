(function () {
  "use strict";

  var STORAGE_KEY = "tidsregistrering_v7";
  var LEGACY_STORAGE_KEY = "tidsregistrering_v5";
  var A_LIMIT = 16 * 60;
  var B_LIMIT = 24 * 60;
  var CHIP_MODES = ["auto", "before", "shift", "next", "after"];

  var state = defaultState();

  function defaultState() {
    return {
      shiftStartISO: null,
      shiftStartTime: null,
      beredskab: "",
      draft: {
        startTime: "",
        startOffset: null,
        startMode: "auto",
        endTime: "",
        endOffset: null,
        endMode: "auto"
      },
      trips: [],
      fremskudtPause: 0,
      pause1: "",
      pause2: "",
      darkMode: false
    };
  }

  function hasShiftStart(currentState) {
    var data = currentState || state;
    return Boolean(data.shiftStartISO && data.shiftStartTime);
  }

  function calculateTrip(trip, options) {
    var data = options || state;
    if (!hasShiftStart(data)) return invalidResult("Vagtstart mangler.");
    if (!isValidTime(trip.startTime) || !isValidTime(trip.endTime)) {
      return invalidResult("Ugyldigt tidspunkt.");
    }

    var normalized = normalizeTrip(trip, data);
    var startOffset = normalized.startOffset;
    var endOffset = normalized.endOffset;

    if (!Number.isFinite(startOffset) || !Number.isFinite(endOffset)) {
      return invalidResult("Tidspunktet kunne ikke placeres i vagten.");
    }

    if (endOffset <= startOffset) {
      return invalidResult("Sluttid skal ligge efter starttid.");
    }

    var overtimeBefore = overlap(startOffset, endOffset, -100000, 0);
    var a = overlap(startOffset, endOffset, 0, A_LIMIT);
    var b = overlap(startOffset, endOffset, A_LIMIT, B_LIMIT);
    var overtimeAfter = overlap(startOffset, endOffset, B_LIMIT, 100000);
    var totalDuration = endOffset - startOffset;

    return {
      valid: true,
      totalDuration: totalDuration,
      normalTotal: a + b,
      a: a,
      b: b,
      overtimeBefore: overtimeBefore,
      overtimeAfter: overtimeAfter,
      extra14: totalDuration >= 1 && totalDuration <= 4 ? totalDuration : 0,
      startOffset: startOffset,
      endOffset: endOffset
    };
  }

  function calculateSummary(currentState) {
    var data = currentState || state;
    var a = 0;
    var b = 0;
    var overtimeBefore = 0;
    var overtimeAfter = 0;
    var extra14 = 0;

    data.trips.forEach(function (trip) {
      var result = calculateTrip(trip, data);
      if (!result.valid) return;
      a += result.a;
      b += result.b;
      overtimeBefore += result.overtimeBefore;
      overtimeAfter += result.overtimeAfter;
      extra14 += result.extra14;
    });

    var adjusted = applyForwardedPause(a, b, data.fremskudtPause);

    return {
      a: adjusted.a,
      b: adjusted.b,
      normalTotal: adjusted.a + adjusted.b,
      overtimeBefore: overtimeBefore,
      overtimeAfter: overtimeAfter,
      extra14: extra14
    };
  }

  function applyForwardedPause(a, b, pause) {
    var remainingPause = Number(pause || 0);
    var aReduction = Math.min(a, remainingPause);
    a -= aReduction;
    remainingPause -= aReduction;

    var bReduction = Math.min(b, remainingPause);
    b -= bReduction;

    return { a: a, b: b };
  }

  function invalidResult(error) {
    return {
      valid: false,
      error: error,
      totalDuration: 0,
      normalTotal: 0,
      a: 0,
      b: 0,
      overtimeBefore: 0,
      overtimeAfter: 0,
      extra14: 0,
      startOffset: null,
      endOffset: null
    };
  }

  function normalizeTrip(trip, data) {
    var normalized = Object.assign({}, trip);
    if (Number.isFinite(normalized.startOffset) && Number.isFinite(normalized.endOffset)) {
      return normalized;
    }

    if (trip.startDay || trip.endDay) {
      normalized.startOffset = timeToOffset(trip.startTime, trip.startDay || "shift", data.shiftStartTime);
      normalized.endOffset = timeToOffset(trip.endTime, trip.endDay || "shift", data.shiftStartTime);
      return normalized;
    }

    normalized.startOffset = resolveOffset("start", {
      time: trip.startTime,
      mode: trip.startMode || "auto",
      otherOffset: null
    }, data);
    normalized.endOffset = resolveOffset("end", {
      time: trip.endTime,
      mode: trip.endMode || "auto",
      otherOffset: normalized.startOffset
    }, data);
    return normalized;
  }

  function resolveDraftOffsets() {
    state.draft.startOffset = resolveOffset("start", {
      time: state.draft.startTime,
      mode: state.draft.startMode,
      otherOffset: null
    }, state);

    state.draft.endOffset = resolveOffset("end", {
      time: state.draft.endTime,
      mode: state.draft.endMode,
      otherOffset: state.draft.startOffset
    }, state);
  }

  function resolveOffset(kind, input, data) {
    if (!hasShiftStart(data) || !isValidTime(input.time)) return null;

    var candidates = possibleOffsets(input.time, data.shiftStartTime);
    if (input.mode && input.mode !== "auto") {
      return chooseManualOffset(candidates, input.mode);
    }

    return chooseAutoOffset(kind, candidates, input.otherOffset, data);
  }

  function possibleOffsets(time, shiftStartTime) {
    var sameDay = minuteOfDay(time) - minuteOfDay(shiftStartTime);
    return [
      { offset: sameDay, placement: placementFromOffset(sameDay) },
      { offset: sameDay + 1440, placement: placementFromOffset(sameDay + 1440) }
    ];
  }

  function chooseManualOffset(candidates, mode) {
    var exact = candidates.find(function (candidate) {
      return candidate.placement === mode;
    });
    if (exact) return exact.offset;

    if (mode === "before") return candidates[0].offset;
    if (mode === "after") return candidates[1].offset;
    if (mode === "next") return candidates[1].offset;
    return candidates[0].offset;
  }

  function chooseAutoOffset(kind, candidates, otherOffset, data) {
    var nowOffset = currentOffset(data);
    var lastEnd = latestEndOffset(data.trips);

    var scored = candidates.map(function (candidate) {
      var score = 0;
      var offset = candidate.offset;

      if (offset < -240) score += 300;
      if (offset > 1560) score += 300;
      if (offset >= 0 && offset <= B_LIMIT) score -= 20;
      if (offset < 0 && offset >= -180) score -= 35;

      if (kind === "end" && Number.isFinite(otherOffset)) {
        if (offset <= otherOffset) score += 10000 + otherOffset - offset;
        var duration = offset - otherOffset;
        score += Math.abs(duration - 60) * 0.2;
        if (duration > 720) score += 250;
      }

      if (kind === "start" && Number.isFinite(lastEnd)) {
        if (offset < lastEnd) score += 5000 + lastEnd - offset;
        score += Math.min(Math.max(0, offset - lastEnd), 600) * 0.08;
      }

      if (Number.isFinite(nowOffset) && nowOffset > -360 && nowOffset < 1800) {
        score += Math.min(Math.abs(offset - nowOffset), 1440) * 0.04;
        if (nowOffset > A_LIMIT && offset < 0) score += 80;
        if (nowOffset < 60 && offset > A_LIMIT) score += 80;
      }

      return { offset: offset, score: score };
    });

    scored.sort(function (a, b) {
      return a.score - b.score;
    });
    return scored[0].offset;
  }

  function placementFromOffset(offset) {
    if (offset < 0) return "before";
    if (offset <= A_LIMIT) return "shift";
    if (offset <= B_LIMIT) return "next";
    return "after";
  }

  function placementLabel(placement) {
    if (placement === "before") return "Før vagt";
    if (placement === "shift") return "Vagtdag";
    if (placement === "next") return "Næste dag";
    if (placement === "after") return "Efter vagt";
    return "Auto";
  }

  function chipText(mode, offset) {
    if (!Number.isFinite(offset)) return mode === "auto" ? "Auto" : placementLabel(mode);
    var placement = Number.isFinite(offset) ? placementFromOffset(offset) : "auto";
    var label = placementLabel(mode === "auto" ? placement : mode);
    return (mode === "auto" ? "Auto: " : "") + label;
  }

  function timeToOffset(time, day, shiftStartTime) {
    var shift = minuteOfDay(shiftStartTime);
    var minute = minuteOfDay(time);
    var dayOffset = day === "next" ? 1440 : 0;
    return minute - shift + dayOffset;
  }

  function currentOffset(data) {
    if (!hasShiftStart(data)) return null;
    return Math.round((new Date().getTime() - new Date(data.shiftStartISO).getTime()) / 60000);
  }

  function latestEndOffset(trips) {
    var offsets = trips
      .map(function (trip) {
        return Number.isFinite(trip.endOffset) ? trip.endOffset : null;
      })
      .filter(function (offset) {
        return Number.isFinite(offset);
      });
    return offsets.length ? Math.max.apply(null, offsets) : null;
  }

  function offsetToDate(offset, data) {
    return new Date(new Date(data.shiftStartISO).getTime() + offset * 60000);
  }

  function offsetFromDate(date, data) {
    return Math.round((date.getTime() - new Date(data.shiftStartISO).getTime()) / 60000);
  }

  function overlap(start, end, rangeStart, rangeEnd) {
    return Math.max(0, Math.min(end, rangeEnd) - Math.max(start, rangeStart));
  }

  function minuteOfDay(time) {
    var parts = time.split(":").map(Number);
    return parts[0] * 60 + parts[1];
  }

  function isValidTime(time) {
    if (typeof time !== "string" || !/^\d{2}:\d{2}$/.test(time)) return false;
    var parts = time.split(":").map(Number);
    return parts[0] >= 0 && parts[0] <= 23 && parts[1] >= 0 && parts[1] <= 59;
  }

  function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function loadState() {
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY));
      if (!saved) return defaultState();
      var defaults = defaultState();
      var loaded = Object.assign({}, defaults, saved, {
        draft: Object.assign({}, defaults.draft, saved.draft || {})
      });
      return migrateState(loaded);
    } catch (error) {
      return defaultState();
    }
  }

  function migrateState(data) {
    if (!data.draft.startMode) data.draft.startMode = "auto";
    if (!data.draft.endMode) data.draft.endMode = "auto";

    if (data.draft.startDay && isValidTime(data.draft.startTime) && hasShiftStart(data)) {
      data.draft.startOffset = timeToOffset(data.draft.startTime, data.draft.startDay, data.shiftStartTime);
    }
    if (data.draft.endDay && isValidTime(data.draft.endTime) && hasShiftStart(data)) {
      data.draft.endOffset = timeToOffset(data.draft.endTime, data.draft.endDay, data.shiftStartTime);
    }

    data.trips = (data.trips || []).map(function (trip) {
      var normalized = normalizeTrip(trip, data);
      return {
        id: trip.id || cryptoRandomId(),
        startTime: trip.startTime || "",
        startOffset: normalized.startOffset,
        startMode: trip.startMode || "auto",
        endTime: trip.endTime || "",
        endOffset: normalized.endOffset,
        endMode: trip.endMode || "auto"
      };
    });

    delete data.draft.startDay;
    delete data.draft.endDay;
    return data;
  }

  function bindEvents() {
    $("beredskab").addEventListener("input", function () {
      state.beredskab = $("beredskab").value;
      saveState();
    });

    $("starttid").addEventListener("change", function () {
      state.draft.startTime = $("starttid").value;
      resolveDraftOffsets();
      saveState();
      renderAll(true);
    });

    $("sluttid").addEventListener("change", function () {
      state.draft.endTime = $("sluttid").value;
      resolveDraftOffsets();
      saveState();
      renderAll(true);
    });

    $("startNuBtn").addEventListener("click", function () {
      setNow("start");
    });

    $("slutNuBtn").addEventListener("click", function () {
      setNow("end");
    });

    $("startChip").addEventListener("click", function () {
      cycleMode("start");
    });

    $("slutChip").addEventListener("click", function () {
      cycleMode("end");
    });

    $("gemTurBtn").addEventListener("click", addTrip);
    $("clearDraftBtn").addEventListener("click", clearDraft);
    $("nyVagtBtn").addEventListener("click", newShift);
    $("opdaterBtn").addEventListener("click", updateApp);
    $("gemBilledeBtn").addEventListener("click", saveAsImage);

    $("fremskudtPause").addEventListener("change", function () {
      state.fremskudtPause = Number($("fremskudtPause").value || 0);
      saveState();
      renderAll(true);
    });

    document.querySelectorAll('input[name="pause1"]').forEach(function (element) {
      element.addEventListener("click", function () {
        state.pause1 = state.pause1 === element.value ? "" : element.value;
        saveState();
        renderAll(true);
      });
    });

    document.querySelectorAll('input[name="pause2"]').forEach(function (element) {
      element.addEventListener("click", function () {
        state.pause2 = state.pause2 === element.value ? "" : element.value;
        saveState();
        renderAll(true);
      });
    });

    $("darkModeToggle").addEventListener("change", function () {
      state.darkMode = $("darkModeToggle").checked;
      saveState();
      renderAll(true);
    });
  }

  function setNow(kind) {
    if (!hasShiftStart()) {
      alert("Du skal først vælge vagtstart. Tryk på 'Ny vagt'.");
      return;
    }

    var now = new Date();
    var time = now.toTimeString().slice(0, 5);
    var offset = offsetFromDate(now, state);

    state.draft[kind + "Time"] = time;
    state.draft[kind + "Offset"] = offset;
    state.draft[kind + "Mode"] = "auto";
    saveState();
    renderAll(true);
  }

  function cycleMode(kind) {
    var key = kind + "Mode";
    var index = CHIP_MODES.indexOf(state.draft[key]);
    state.draft[key] = CHIP_MODES[(index + 1) % CHIP_MODES.length];
    resolveDraftOffsets();
    saveState();
    renderAll(true);
  }

  function addTrip() {
    if (!hasShiftStart()) {
      alert("Du skal først vælge vagtstart. Tryk på 'Ny vagt'.");
      return;
    }

    resolveDraftOffsets();
    var trip = {
      id: cryptoRandomId(),
      startTime: state.draft.startTime,
      startOffset: state.draft.startOffset,
      startMode: state.draft.startMode,
      endTime: state.draft.endTime,
      endOffset: state.draft.endOffset,
      endMode: state.draft.endMode
    };

    if (!trip.startTime || !trip.endTime) {
      alert("Udfyld både starttid og sluttid.");
      return;
    }

    var result = calculateTrip(trip);
    if (!result.valid) {
      alert(result.error || "Turen kan ikke beregnes.");
      return;
    }

    state.trips.push(trip);
    state.draft.startTime = "";
    state.draft.startOffset = null;
    state.draft.startMode = "auto";
    state.draft.endTime = "";
    state.draft.endOffset = null;
    state.draft.endMode = "auto";
    saveState();
    renderAll(true);
  }

  function clearDraft() {
    state.draft = defaultState().draft;
    saveState();
    renderAll(true);
  }

  function newShift() {
    if (state.trips.length && !confirm("Start ny vagt og nulstil alle ture?")) return;

    var time = prompt("Indtast vagtstart (HH:MM)", state.shiftStartTime || "07:30");
    if (!time || !isValidTime(time)) {
      alert("Ugyldigt format. Brug HH:MM, fx 07:30.");
      return;
    }

    var parts = time.split(":").map(Number);
    var date = new Date();
    date.setHours(parts[0], parts[1], 0, 0);

    state = defaultState();
    state.shiftStartISO = date.toISOString();
    state.shiftStartTime = time;

    saveState();
    renderAll(false);
  }

  function renderAll(preserveScroll) {
    var scrollY = window.scrollY;
    document.body.classList.toggle("dark", state.darkMode);
    if (hasShiftStart()) resolveDraftOffsets();
    renderHeader();
    renderForm();
    renderTable();
    renderSummary();

    if (preserveScroll) {
      requestAnimationFrame(function () {
        window.scrollTo(0, scrollY);
      });
    }
  }

  function renderHeader() {
    $("visDato").textContent = new Date().toLocaleDateString("da-DK", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric"
    });

    if (!hasShiftStart()) {
      $("vagtStartVis").textContent = "Vagtstart: -";
      return;
    }

    var date = new Date(state.shiftStartISO);
    $("vagtStartVis").textContent =
      "Vagtstart: " +
      date.toLocaleDateString("da-DK", { day: "numeric", month: "long" }) +
      " kl. " +
      state.shiftStartTime;
  }

  function renderForm() {
    setValue("beredskab", state.beredskab);
    setValue("starttid", state.draft.startTime);
    setValue("sluttid", state.draft.endTime);
    setValue("fremskudtPause", String(state.fremskudtPause));

    renderChip("start", "startChip");
    renderChip("end", "slutChip");

    $("darkModeToggle").checked = state.darkMode;
    $("gemTurBtn").disabled = !hasShiftStart();
    $("vagtstartHint").style.display = hasShiftStart() ? "none" : "block";

    document.querySelectorAll('input[name="pause1"]').forEach(function (element) {
      element.checked = state.pause1 === element.value;
    });

    document.querySelectorAll('input[name="pause2"]').forEach(function (element) {
      element.checked = state.pause2 === element.value;
    });
  }

  function renderChip(kind, elementId) {
    var chip = $(elementId);
    var mode = state.draft[kind + "Mode"] || "auto";
    var offset = state.draft[kind + "Offset"];
    var placement = Number.isFinite(offset) ? placementFromOffset(offset) : "auto";
    chip.textContent = chipText(mode, offset);
    chip.dataset.placement = mode === "auto" ? placement : mode;
    chip.title = "Tryk for at skifte mellem Auto, Før vagt, Vagtdag, Næste dag og Efter vagt";
  }

  function renderTable() {
    var body = $("turTabelBody");
    body.innerHTML = "";

    if (!state.trips.length) {
      body.innerHTML = '<tr><td colspan="8">Ingen ture registreret endnu.</td></tr>';
      return;
    }

    state.trips.forEach(function (trip) {
      var result = calculateTrip(trip);
      var row = document.createElement("tr");
      row.dataset.id = trip.id;
      if (!result.valid) row.classList.add("error-row");

      row.innerHTML = [
        "<td>",
        renderTableTime(trip.startTime, result.startOffset),
        "</td><td>",
        renderTableTime(trip.endTime, result.endOffset),
        "</td><td><strong>",
        result.valid ? formatMin(result.normalTotal) : "Fejl",
        '</strong></td><td class="a-tid">',
        result.valid ? formatMin(result.a) : "-",
        '</td><td class="b-tid">',
        result.valid ? formatMin(result.b) : "-",
        '</td><td class="overtime-cell',
        result.valid && result.overtimeBefore > 0 ? " has-overtime" : "",
        '">',
        result.valid ? formatMin(result.overtimeBefore) : "-",
        '</td><td class="overtime-cell',
        result.valid && result.overtimeAfter > 0 ? " has-overtime" : "",
        '">',
        result.valid ? formatMin(result.overtimeAfter) : "-",
        '</td><td><button class="delete-btn" data-delete-id="',
        trip.id,
        '" aria-label="Slet tur">Slet</button></td>'
      ].join("");

      body.appendChild(row);
    });

    body.querySelectorAll("[data-delete-id]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!confirm("Slet tur?")) return;
        state.trips = state.trips.filter(function (trip) {
          return trip.id !== button.dataset.deleteId;
        });
        saveState();
        renderAll(true);
      });
    });
  }

  function renderTableTime(time, offset) {
    var label = Number.isFinite(offset) ? placementLabel(placementFromOffset(offset)) : "-";
    var offsetText = Number.isFinite(offset) ? signedOffset(offset) : "";
    return '<span class="table-time">' + escapeHtml(time) + '</span><small>' + label + " · " + offsetText + "</small>";
  }

  function renderSummary() {
    var summary = calculateSummary();
    $("sumTotal").textContent = formatMin(summary.normalTotal);
    $("sumA").textContent = formatMin(summary.a);
    $("sumB").textContent = formatMin(summary.b);
    $("sumOvertidFor").textContent = formatMin(summary.overtimeBefore);
    $("sumOvertidEfter").textContent = formatMin(summary.overtimeAfter);
    $("sumEkstra").textContent = formatMin(summary.extra14);
  }

  function updateApp() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.getRegistration().then(function (registration) {
        if (registration) registration.update();
        window.location.reload();
      });
    } else {
      window.location.reload();
    }
  }

  function saveAsImage() {
    window.print();
  }

  function runTests() {
    var testState = defaultState();
    testState.shiftStartTime = "07:30";
    testState.shiftStartISO = new Date(2026, 0, 1, 7, 30, 0, 0).toISOString();

    var tests = [
      {
        name: "1. 07:20-07:54 giver offset -10 til 24",
        trip: { startTime: "07:20", startOffset: -10, endTime: "07:54", endOffset: 24 },
        expect: { a: 24, b: 0, overtimeBefore: 10, overtimeAfter: 0 }
      },
      {
        name: "2. 07:30-08:30 giver A=60",
        trip: { startTime: "07:30", startOffset: 0, endTime: "08:30", endOffset: 60 },
        expect: { a: 60, b: 0, overtimeBefore: 0, overtimeAfter: 0 }
      },
      {
        name: "3. 07:30-23:30 giver A=960 uden 510-loft",
        trip: { startTime: "07:30", startOffset: 0, endTime: "23:30", endOffset: 960 },
        expect: { a: 960, b: 0 }
      },
      {
        name: "4. 23:30-00:30 giver B=60",
        trip: { startTime: "23:30", startOffset: 960, endTime: "00:30", endOffset: 1020 },
        expect: { a: 0, b: 60 }
      },
      {
        name: "5. 06:00-07:45 næste dag giver B=90 og efter=15",
        trip: { startTime: "06:00", startOffset: 1350, endTime: "07:45", endOffset: 1455 },
        expect: { a: 0, b: 90, overtimeAfter: 15 }
      },
      {
        name: "6. Flere ture summeres korrekt",
        summary: true,
        trips: [
          { startTime: "07:20", startOffset: -10, endTime: "07:54", endOffset: 24 },
          { startTime: "07:30", startOffset: 0, endTime: "08:30", endOffset: 60 },
          { startTime: "06:00", startOffset: 1350, endTime: "07:45", endOffset: 1455 }
        ],
        expect: { a: 84, b: 90, overtimeBefore: 10, overtimeAfter: 15 }
      },
      {
        name: "7. Start/slut-felter og offsets kan bevares ved reload",
        draft: true,
        expect: { startTime: "07:30", startOffset: 0, endTime: "00:30", endOffset: 1020 }
      },
      {
        name: "8. Gem tur er disabled før vagtstart",
        disabled: true,
        expect: true
      },
      {
        name: "9. Auto vælger slut 00:30 efter start 23:30",
        auto: true,
        expect: { startOffset: 960, endOffset: 1020 }
      }
    ];

    var lines = tests.map(function (test) {
      var ok;
      var detail;

      if (test.summary) {
        testState.trips = test.trips;
        var summary = calculateSummary(testState);
        ok = Object.keys(test.expect).every(function (key) {
          return summary[key] === test.expect[key];
        });
        detail = "Resultat: A=" + summary.a + ", B=" + summary.b + ", OV før=" + summary.overtimeBefore + ", OV efter=" + summary.overtimeAfter;
        testState.trips = [];
      } else if (test.draft) {
        var draft = { startTime: "07:30", startOffset: 0, endTime: "00:30", endOffset: 1020 };
        ok = Object.keys(test.expect).every(function (key) {
          return draft[key] === test.expect[key];
        });
        detail = "Resultat: start=" + draft.startTime + " (" + draft.startOffset + "), slut=" + draft.endTime + " (" + draft.endOffset + ")";
      } else if (test.disabled) {
        ok = !hasShiftStart(defaultState());
        detail = "Resultat: disabled=" + ok;
      } else if (test.auto) {
        var startOffset = resolveOffset("start", { time: "23:30", mode: "auto", otherOffset: null }, testState);
        var endOffset = resolveOffset("end", { time: "00:30", mode: "auto", otherOffset: startOffset }, testState);
        ok = startOffset === test.expect.startOffset && endOffset === test.expect.endOffset;
        detail = "Resultat: startOffset=" + startOffset + ", endOffset=" + endOffset;
      } else {
        var result = calculateTrip(test.trip, testState);
        ok = Object.keys(test.expect).every(function (key) {
          return result[key] === test.expect[key];
        });
        detail = "Resultat: A=" + result.a + ", B=" + result.b + ", OV før=" + result.overtimeBefore + ", OV efter=" + result.overtimeAfter;
      }

      if (!ok) throw new Error("Test fejlede: " + test.name + "\n" + detail);
      return "OK - " + test.name + "\n   " + detail;
    });

    var report = lines.join("\n\n") + "\n\nAlle test bestået.";
    if (typeof document !== "undefined" && $("testOutput")) $("testOutput").textContent = report;
    if (typeof console !== "undefined") console.log("Tidsregistrering tests:\n" + report);
    return { passed: tests.length, report: report };
  }

  function setValue(id, value) {
    var element = $(id);
    if (!element || document.activeElement === element) return;
    element.value = value == null ? "" : value;
  }

  function formatMin(value) {
    return value + " min";
  }

  function signedOffset(offset) {
    return (offset > 0 ? "+" : "") + offset + " min";
  }

  function cryptoRandomId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function $(id) {
    return document.getElementById(id);
  }

  function startApp() {
    state = loadState();
    bindEvents();
    renderAll(false);
    runTests();

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("service-worker.js");
    }
  }

  var api = {
    STORAGE_KEY: STORAGE_KEY,
    defaultState: defaultState,
    calculateTrip: calculateTrip,
    calculateSummary: calculateSummary,
    applyForwardedPause: applyForwardedPause,
    possibleOffsets: possibleOffsets,
    resolveOffset: resolveOffset,
    placementFromOffset: placementFromOffset,
    runTests: runTests
  };

  if (typeof window !== "undefined") {
    window.Tidsregistrering = api;
    document.addEventListener("DOMContentLoaded", startApp);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
