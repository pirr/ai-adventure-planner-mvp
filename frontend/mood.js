/* ===========================================================================
   Mood Launcher — production logic (vanilla). Loads AFTER app.js, so it can
   call its globals: runSearch, currentLang, t, $, enterPlanning, ensureMap.
   Strategy: the wizard chips/inputs stay in the DOM (hidden); presets set them
   and call runSearch(). Inputs are the single source of truth for the payload.
   =========================================================================== */
(function () {
  // ---- localized copy for the launcher (app i18n covers the rest) --------
  var LX = {
    en: {
      vibe_q: "Ready for a", vibe_word: "trip?", or_pick: "Or pick a vibe",
      everything: "Just show me everything nearby", perfect: "Perfect right now",
      best_now: "Find best trip now", best_near: "Best near me", best_now_sub: "I will choose the strongest ready-to-go plan.",
      your_vibe: "Popular", time: "Time", interest: "Interest", crew: "Crew", effort: "Effort",
      transport: "Transport", f_transport: "How are you getting there?", recent: "Recent", apply: "Apply", reset: "Reset", changes: "changed",
      f_time: "How much time?", f_crew: "Who\u2019s coming?", f_effort: "Effort level", f_interest: "What are you into?",
      advanced: "Advanced",
      morning: "Morning", afternoon: "Afternoon", evening: "Evening",
      greet_morning: "Good morning", greet_afternoon: "Good afternoon", greet_evening: "Good evening",
      ctx_morning: "This morning", ctx_afternoon: "This afternoon", ctx_evening: "This evening",
      sky_morning: "Crisp", sky_afternoon: "Clear", sky_evening: "Golden", loc: "Tivat",
      picked: "Map point", mine: "My location",
      use_loc: "Use my location", loc_title: "Where are you starting from?", map_hint: "Tap the map to set your start",
      enter_coords: "Enter coordinates", lat: "Latitude", lon: "Longitude", set_btn: "Set",
      describe_ph: "Describe it: time, company, mood…",
      describe_fail: "Couldn’t understand that — try different words or pick a vibe",
      describe_mic: "Dictate",
    },
    ru: {
      vibe_q: "\u0413\u043e\u0442\u043e\u0432\u044b", vibe_word: "\u043f\u043e\u0435\u0445\u0430\u0442\u044c?", or_pick: "\u0418\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0438\u0435",
      everything: "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0432\u0441\u0451 \u0440\u044f\u0434\u043e\u043c", perfect: "\u0421\u0435\u0439\u0447\u0430\u0441 \u0441\u0430\u043c\u043e\u0435 \u0442\u043e",
      best_now: "\u041d\u0430\u0439\u0442\u0438 \u043b\u0443\u0447\u0448\u0443\u044e \u043f\u043e\u0435\u0437\u0434\u043a\u0443", best_near: "\u041b\u0443\u0447\u0448\u0435\u0435 \u0440\u044f\u0434\u043e\u043c", best_now_sub: "\u042f \u0432\u044b\u0431\u0435\u0440\u0443 \u0433\u043e\u0442\u043e\u0432\u044b\u0439 \u043f\u043b\u0430\u043d \u0441 \u043c\u0430\u0440\u0448\u0440\u0443\u0442\u043e\u043c.",
      your_vibe: "\u041f\u043e\u043f\u0443\u043b\u044f\u0440\u043d\u043e", time: "\u0412\u0440\u0435\u043c\u044f", interest: "\u0418\u043d\u0442\u0435\u0440\u0435\u0441", crew: "\u041a\u043e\u043c\u043f\u0430\u043d\u0438\u044f", effort: "\u041d\u0430\u0433\u0440\u0443\u0437\u043a\u0430",
      transport: "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442", f_transport: "\u041a\u0430\u043a \u0434\u043e\u0431\u0438\u0440\u0430\u0435\u0442\u0435\u0441\u044c?", recent: "\u041d\u0435\u0434\u0430\u0432\u043d\u0435\u0435", apply: "\u041f\u0440\u0438\u043c\u0435\u043d\u0438\u0442\u044c", reset: "\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c", changes: "\u0438\u0437\u043c.",
      f_time: "\u0421\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438?", f_crew: "\u041a\u0442\u043e \u0438\u0434\u0451\u0442?", f_effort: "\u0423\u0440\u043e\u0432\u0435\u043d\u044c \u043d\u0430\u0433\u0440\u0443\u0437\u043a\u0438", f_interest: "\u0427\u0442\u043e \u0438\u043d\u0442\u0435\u0440\u0435\u0441\u043d\u043e?",
      advanced: "\u0414\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u043e",
      morning: "\u0423\u0442\u0440\u043e", afternoon: "\u0414\u0435\u043d\u044c", evening: "\u0412\u0435\u0447\u0435\u0440",
      greet_morning: "\u0414\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e", greet_afternoon: "\u0414\u043e\u0431\u0440\u044b\u0439 \u0434\u0435\u043d\u044c", greet_evening: "\u0414\u043e\u0431\u0440\u044b\u0439 \u0432\u0435\u0447\u0435\u0440",
      ctx_morning: "\u0421\u0435\u0433\u043e\u0434\u043d\u044f \u0443\u0442\u0440\u043e\u043c", ctx_afternoon: "\u0421\u0435\u0433\u043e\u0434\u043d\u044f \u0434\u043d\u0451\u043c", ctx_evening: "\u0421\u0435\u0433\u043e\u0434\u043d\u044f \u0432\u0435\u0447\u0435\u0440\u043e\u043c",
      sky_morning: "\u0421\u0432\u0435\u0436\u043e", sky_afternoon: "\u042f\u0441\u043d\u043e", sky_evening: "\u0417\u043e\u043b\u043e\u0442\u043e\u0439 \u0447\u0430\u0441", loc: "\u0422\u0438\u0432\u0430\u0442",
      picked: "\u0422\u043e\u0447\u043a\u0430 \u043d\u0430 \u043a\u0430\u0440\u0442\u0435", mine: "\u041c\u043e\u0451 \u043c\u0435\u0441\u0442\u043e",
      use_loc: "\u041c\u043e\u0451 \u043c\u0435\u0441\u0442\u043e", loc_title: "\u041e\u0442\u043a\u0443\u0434\u0430 \u043d\u0430\u0447\u043d\u0451\u043c?", map_hint: "\u041a\u043e\u0441\u043d\u0438\u0442\u0435\u0441\u044c \u043a\u0430\u0440\u0442\u044b, \u0447\u0442\u043e\u0431\u044b \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u0441\u0442\u0430\u0440\u0442",
      enter_coords: "\u0412\u0432\u0435\u0441\u0442\u0438 \u043a\u043e\u043e\u0440\u0434\u0438\u043d\u0430\u0442\u044b", lat: "\u0428\u0438\u0440\u043e\u0442\u0430", lon: "\u0414\u043e\u043b\u0433\u043e\u0442\u0430", set_btn: "\u0413\u043e\u0442\u043e\u0432\u043e",
      describe_ph: "\u041e\u043f\u0438\u0448\u0438\u0442\u0435: \u0432\u0440\u0435\u043c\u044f, \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f, \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0438\u0435\u2026",
      describe_fail: "\u041d\u0435 \u043f\u043e\u043b\u0443\u0447\u0438\u043b\u043e\u0441\u044c \u043f\u043e\u043d\u044f\u0442\u044c \u2014 \u043f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0434\u0440\u0443\u0433\u0438\u043c\u0438 \u0441\u043b\u043e\u0432\u0430\u043c\u0438 \u0438\u043b\u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u0438\u0435",
      describe_mic: "\u041d\u0430\u0434\u0438\u043a\u0442\u043e\u0432\u0430\u0442\u044c",
    },
  };
  function lx(k) { var d = LX[currentLang] || LX.en; return d[k] != null ? d[k] : LX.en[k]; }

  // ---- presets (bundle time+crew+transport+intensity+interests) ----------
  var PRESETS = [
    { key: "quick", icon: "zap", grad: "linear-gradient(150deg,#2f7f6e,#163a2c)", dayparts: ["morning", "afternoon", "evening"],
      time: 60, crew: "solo", transport: "walk", intensity: "easy", interests: ["viewpoints", "nature"], childrenAges: [], maxWalkingKm: 1.5,
      en: { t: "Quick Escape", s: "Under an hour, on foot" }, ru: { t: "\u0411\u044b\u0441\u0442\u0440\u044b\u0439 \u0432\u044b\u0445\u043e\u0434", s: "\u041c\u0435\u043d\u044c\u0448\u0435 \u0447\u0430\u0441\u0430, \u043f\u0435\u0448\u043a\u043e\u043c" } },
    { key: "coffee", icon: "coffee", grad: "linear-gradient(150deg,#c98b4a,#7a4a22)", dayparts: ["morning"],
      time: 120, crew: "solo", transport: "walk", intensity: "easy", interests: ["viewpoints", "food"], childrenAges: [], maxWalkingKm: 2,
      en: { t: "Coffee & a View", s: "A short, scenic start" }, ru: { t: "\u041a\u043e\u0444\u0435 \u0441 \u0432\u0438\u0434\u043e\u043c", s: "\u041a\u043e\u0440\u043e\u0442\u043a\u043e \u0438 \u0436\u0438\u0432\u043e\u043f\u0438\u0441\u043d\u043e" } },
    { key: "family", icon: "users", grad: "linear-gradient(150deg,#3f97a6,#1f5a63)", dayparts: ["morning", "afternoon"],
      time: 240, crew: "family", transport: "car", intensity: "easy", interests: ["history", "water", "nature"], childrenAges: [], maxWalkingKm: 2.5,
      en: { t: "Family Day Out", s: "Easy & kid-friendly" }, ru: { t: "\u0421 \u0441\u0435\u043c\u044c\u0451\u0439", s: "\u041b\u0435\u0433\u043a\u043e, \u0441 \u0434\u0435\u0442\u044c\u043c\u0438" } },
    { key: "sunset", icon: "mountain-snow", grad: "linear-gradient(150deg,#d6794e,#a84e28)", dayparts: ["afternoon", "evening"],
      time: 120, crew: "couple", transport: "car", intensity: "easy", interests: ["viewpoints"], childrenAges: [], maxWalkingKm: 2.5,
      en: { t: "Sunset Views", s: "Golden-hour viewpoints" }, ru: { t: "\u0417\u0430\u043a\u0430\u0442\u043d\u044b\u0435 \u0432\u0438\u0434\u044b", s: "\u0421\u043c\u043e\u0442\u0440\u043e\u0432\u044b\u0435 \u043d\u0430 \u0437\u0430\u043a\u0430\u0442\u0435" } },
    { key: "history", icon: "castle", grad: "linear-gradient(150deg,#a86b3f,#5e3520)", dayparts: ["morning", "afternoon"],
      time: 300, crew: "family", transport: "car", intensity: "medium", interests: ["history", "fortresses"], childrenAges: [], maxWalkingKm: 3,
      en: { t: "History Hunt", s: "Fortresses & old towns" }, ru: { t: "\u041f\u043e \u0438\u0441\u0442\u043e\u0440\u0438\u0438", s: "\u041a\u0440\u0435\u043f\u043e\u0441\u0442\u0438 \u0438 \u0441\u0442\u0430\u0440\u044b\u0435 \u0433\u043e\u0440\u043e\u0434\u0430" } },
    { key: "dinner", icon: "utensils", grad: "linear-gradient(150deg,#7a5230,#3a2414)", dayparts: ["evening"],
      time: 240, crew: "couple", transport: "car", intensity: "easy", interests: ["food", "history"], childrenAges: [], maxWalkingKm: 2,
      en: { t: "Dinner & a Stroll", s: "Old-town lanes & a bite" }, ru: { t: "\u0423\u0436\u0438\u043d \u0438 \u043f\u0440\u043e\u0433\u0443\u043b\u043a\u0430", s: "\u0423\u043b\u043e\u0447\u043a\u0438 \u0441\u0442\u0430\u0440\u043e\u0433\u043e \u0433\u043e\u0440\u043e\u0434\u0430" } },
    { key: "water", icon: "waves", grad: "linear-gradient(150deg,#3f97a6,#1f4d3a)", dayparts: ["morning", "afternoon"],
      time: 240, crew: "family", transport: "car", intensity: "easy", interests: ["water", "nature"], childrenAges: [], maxWalkingKm: 2.5,
      en: { t: "By the Water", s: "Beaches & quiet coves" }, ru: { t: "\u0423 \u0432\u043e\u0434\u044b", s: "\u041f\u043b\u044f\u0436\u0438 \u0438 \u0431\u0443\u0445\u0442\u044b" } },
    { key: "surprise", icon: "dices", grad: "linear-gradient(150deg,#6a4a86,#2c2750)", dayparts: ["morning", "afternoon", "evening"],
      time: 300, crew: "solo", transport: "car", intensity: "medium", interests: ["history", "fortresses", "viewpoints", "water", "nature"], childrenAges: [], maxWalkingKm: 5,
      en: { t: "Surprise Me", s: "Anything great nearby" }, ru: { t: "\u0423\u0434\u0438\u0432\u0438 \u043c\u0435\u043d\u044f", s: "\u0427\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c \u043a\u043b\u0430\u0441\u0441\u043d\u043e\u0435" } },
  ];
  var DAYPARTS = {
    morning: { icon: "sunrise", temp: 16, feature: "coffee" },
    afternoon: { icon: "sun", temp: 19, feature: "history" },
    evening: { icon: "sunset", temp: 21, feature: "sunset" },
  };

  function defaultDaypart() {
    var h = new Date().getHours();
    if (h >= 5 && h < 11) return "morning";
    if (h >= 11 && h < 17) return "afternoon";
    return "evening";
  }
  var daypart = defaultDaypart();
  var currentMood = null;
  var placeLabel = null;
  var sheetAutoOpened = false;
  // "Describe your trip" is available only when the backend has a real LLM
  // provider configured (GET /api/features). Hidden until confirmed.
  var featureParse = false;
  fetch("/api/features")
    .then(function (r) { return r.json(); })
    .then(function (f) { featureParse = !!(f && f.parse); if (featureParse) buildLauncher(); })
    .catch(function () {});
  function smartNowPreset() {
    var interests = {
      morning: ["viewpoints", "food", "nature"],
      afternoon: ["history", "viewpoints", "nature"],
      evening: ["viewpoints", "food"],
    }[daypart] || ["viewpoints", "nature"];
    return {
      key: "now", icon: "sparkles", time: 120, crew: "solo", transport: "car",
      intensity: "easy", interests: interests, childrenAges: [], maxWalkingKm: 3,
      useLiveData: true,
      en: { t: "Best trip now", s: "Ready-to-go plan" },
      ru: { t: "\u041b\u0443\u0447\u0448\u0435\u0435 \u0441\u0435\u0439\u0447\u0430\u0441", s: "\u0413\u043e\u0442\u043e\u0432\u044b\u0439 \u043f\u043b\u0430\u043d" },
    };
  }
  function locName() { return placeLabel || lx("loc"); }
  function useMyLocation() { placeLabel = lx("mine"); updateContext(); if (window.requestGeolocation) window.requestGeolocation(); }
  function theMap() {
    try { if (window.appMap && window.appMap.on) return window.appMap; } catch (e) {}
    try { if (typeof map !== "undefined" && map && map.on) return map; } catch (e) {}
    return null;
  }

  // Tap anywhere on the start-screen map to set the start point (no recenter).
  function onPlanningMapClick(e) {
    if (!document.body.classList.contains("planning")) return;
    placeLabel = lx("picked");
    if (window.setLocation) window.setLocation(e.latlng.lat, e.latlng.lng, lx("picked"), { recenter: false });
  }
  function wireMapClick() {
    var tries = 0;
    (function attach() {
      var m = theMap();
      if (m) { m.off("click", onPlanningMapClick); m.on("click", onPlanningMapClick); return; }
      if (tries++ < 25) setTimeout(attach, 100);
    })();
  }

  // Launcher sheet: peek (location bar) <-> open (vibe presets).
  function openLauncher() { var s = $("launchSheet"); if (s) s.classList.add("open"); invalidateSoon(); }
  function toggleLauncher() { var s = $("launchSheet"); if (s) s.classList.toggle("open"); invalidateSoon(); }
  function syncPlanningSheet() {
    var s = $("launchSheet"); if (!s) return;
    if (document.body.classList.contains("loc-set")) s.classList.add("open");
    else s.classList.remove("open");
    invalidateSoon();
  }
  function invalidateSoon() { if (window.appMap) setTimeout(function () { try { window.appMap.invalidateSize(); } catch (e) {} }, 460); }

  // Any location set (tap / GPS / coords) funnels through app.js setOrigin,
  // which dispatches 'origin-set'. React once: mark set, update chip, auto-open.
  document.addEventListener("origin-set", function () {
    if (!document.body.classList.contains("planning")) return;
    document.body.classList.add("loc-set");
    updateContext();
    if (!sheetAutoOpened) { sheetAutoOpened = true; openLauncher(); }
  });

  function buildLocBar() {
    var host = $("launchLoc"); if (!host) return;
    host.innerHTML =
      '<p class="loc-title">' + lx("loc_title") + '</p>' +
      '<button type="button" class="loc-cta" id="locGps">' + icon("locate-fixed") + ' ' + lx("use_loc") + '</button>';
    var lg = $("locGps"); if (lg) lg.addEventListener("click", useMyLocation);
    refreshIcons();
  }
  function setMapHint() {
    var h = $("mapHint"); if (!h) return;
    h.innerHTML = icon("locate-fixed") + ' ' + lx("map_hint");
    refreshIcons();
  }

  var $ = function (id) { return document.getElementById(id); };
  function icon(name) { return '<i data-lucide="' + name + '"></i>'; }
  function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
  function pt(p) { return (p[currentLang] || p.en); }

  // ---- write a preset's selection into the hidden wizard chips/inputs -----
  function setSingle(containerId, attr, value) {
    var c = $(containerId); if (!c) return;
    c.querySelectorAll(".tile").forEach(function (tile) {
      tile.classList.toggle("is-active", String(tile.dataset[attr]) === String(value));
    });
  }
  function setInterests(list) {
    var c = $("interestChips"); if (!c) return;
    c.querySelectorAll(".tile").forEach(function (tile) {
      tile.classList.toggle("is-active", list.indexOf(tile.dataset.interest) !== -1);
    });
  }
  function setInput(id, value) { var el = $(id); if (el) el.value = value == null ? "" : String(value); }
  function setChecked(id, value) { var el = $(id); if (el) el.checked = !!value; }
  function normalizeCrewState(crew) {
    if (crew === "solo" || crew === "couple") setInput("childrenAges", "");
  }
  function applyPreset(p) {
    setSingle("timeChips", "minutes", p.time);
    setSingle("groupChips", "group", p.crew);
    setSingle("transportChips", "transport", p.transport);
    setSingle("intensityChips", "intensity", p.intensity);
    setInterests(p.interests);
    setInput("childrenAges", (p.childrenAges || []).join(", "));
    setInput("maxWalkingKm", p.maxWalkingKm);
    setChecked("withDog", p.withDog);
    setChecked("withElderly", p.withElderly);
    setChecked("reducedMobility", p.reducedMobility);
    if (p.useLiveData !== undefined) setChecked("useLiveData", p.useLiveData);
    normalizeCrewState(p.crew);
  }

  function choosePreset(p) {
    if (window.setRequestText) window.setRequestText(null);
    currentMood = p;
    applyPreset(p);
    commitSearch();
    buildFilterBar();
  }
  function chooseNow() {
    choosePreset(smartNowPreset());
  }

  // ---- "Describe your trip": parse free text into a preset-shaped search ---
  var PARSED_FACET = {
    available_minutes: "time", transport_mode: "transport", group_type: "crew",
    children_ages: "crew", with_dog: "crew", with_elderly: "crew",
    reduced_mobility: "crew", intensity: "effort", interests: "interest",
  };
  var aiSetFacets = [];

  // The wizard's time input is chip-based; snap parsed minutes to the largest
  // chip that still fits the stated budget (rounding up could recommend trips
  // the user has no time for).
  function snapMinutes(m) {
    var vals = Array.prototype.map.call(
      document.querySelectorAll("#timeChips .tile"),
      function (t) { return parseInt(t.dataset.minutes, 10); }
    ).sort(function (a, b) { return a - b; });
    var best = vals[0] || 120;
    vals.forEach(function (v) { if (v <= m) best = v; });
    return best;
  }

  function parsedToPreset(parsed) {
    var p = smartNowPreset();   // missing fields keep time-of-day defaults
    if (parsed.available_minutes != null) p.time = snapMinutes(parsed.available_minutes);
    if (parsed.transport_mode) p.transport = parsed.transport_mode;
    if (parsed.group_type) {
      // The crew chips only have solo/couple/family tiles. Scoring treats
      // "kids" like "family" (has_children) and "dog" like with_dog, so the
      // mapping loses nothing.
      var crew = parsed.group_type;
      if (crew === "kids") crew = "family";
      if (crew === "dog") { crew = "solo"; p.withDog = true; }
      p.crew = crew;
    }
    if (parsed.intensity) p.intensity = parsed.intensity;
    if (parsed.interests) p.interests = parsed.interests;
    if (parsed.children_ages) p.childrenAges = parsed.children_ages;
    if (parsed.max_walking_km != null) p.maxWalkingKm = parsed.max_walking_km;
    if (parsed.with_dog != null) p.withDog = parsed.with_dog;
    if (parsed.with_elderly != null) p.withElderly = parsed.with_elderly;
    if (parsed.reduced_mobility != null) p.reducedMobility = parsed.reduced_mobility;
    return p;
  }

  function describeFail() {
    var err = $("describeErr"); if (err) err.classList.remove("hidden");
    var box = $("describeBox"); if (box) box.classList.remove("busy");
  }

  function submitDescribe() {
    var input = $("describeInput"); if (!input) return;
    var text = input.value.trim();
    if (text.length < 3) return;
    var err = $("describeErr"); if (err) err.classList.add("hidden");
    var box = $("describeBox"); if (box) box.classList.add("busy");
    fetch("/api/parse-request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        lang: currentLang,
        anonymous_id: typeof anonymousId === "function" ? anonymousId() : null,
      }),
    })
      .then(function (r) { if (!r.ok) throw new Error("parse http " + r.status); return r.json(); })
      .then(function (data) {
        var parsed = data && data.parsed;
        if (!parsed) { describeFail(); return; }
        if (box) box.classList.remove("busy");
        aiSetFacets = [];
        Object.keys(parsed).forEach(function (k) {
          var facet = PARSED_FACET[k];
          if (parsed[k] != null && facet && aiSetFacets.indexOf(facet) === -1) aiSetFacets.push(facet);
        });
        if (window.setRequestText) window.setRequestText(text);
        currentMood = null;                  // custom situation, not a vibe
        applyPreset(parsedToPreset(parsed));
        commitSearch();
        buildFilterBar();
      })
      .catch(function () { describeFail(); });
  }

  function wireMic() {
    var btn = $("describeMic"); if (!btn) return;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var rec = null;
    btn.addEventListener("click", function () {
      if (rec) { rec.stop(); return; }   // tap again = stop listening
      rec = new SR();
      rec.lang = currentLang === "ru" ? "ru-RU" : "en-US";
      rec.interimResults = true;
      rec.onresult = function (e) {
        var text = Array.prototype.map.call(e.results, function (r) { return r[0].transcript; }).join(" ").trim();
        var input = $("describeInput"); if (input) input.value = text;
        if (e.results[e.results.length - 1].isFinal) submitDescribe();
      };
      rec.onend = function () { rec = null; btn.classList.remove("listening"); };
      rec.onerror = function () { rec = null; btn.classList.remove("listening"); };
      btn.classList.add("listening");
      rec.start();
    });
  }

  // ---- launcher UI -------------------------------------------------------
  function buildLauncher() {
    var host = $("launchBody"); if (!host) return;
    var dp = DAYPARTS[daypart];
    var list = PRESETS.filter(function (p) { return p.dayparts.indexOf(daypart) !== -1; });
    var grid = list;

    var html = '';
    html += '<div class="greet-row">';
    html += '  <div class="greet">';
    html += '    <span class="hello">' + icon(dp.icon) + ' ' + lx("ctx_" + daypart) + '</span>';
    html += '    <h2>' + lx("vibe_q") + ' <span class="accent">' + lx("vibe_word") + '</span></h2>';
    html += '  </div>';
    html += '  <div class="daypart-switch">';
    ["morning", "afternoon", "evening"].forEach(function (k) {
      html += '<button type="button" class="dp-btn ' + (k === daypart ? "on" : "") + '" data-dp="' + k + '" title="' + lx(k) + '">' + icon(DAYPARTS[k].icon) + '</button>';
    });
    html += '  </div>';
    html += '</div>';

    if (featureParse) {
      html += '<div class="describe-box" id="describeBox">';
      html += '  <input id="describeInput" maxlength="500" autocomplete="off" placeholder="' + lx("describe_ph") + '">';
      if (window.SpeechRecognition || window.webkitSpeechRecognition) {
        html += '<button type="button" class="describe-mic" id="describeMic" title="' + lx("describe_mic") + '">' + icon("mic") + "</button>";
      }
      html += '  <button type="button" class="describe-go" id="describeGo">' + icon("arrow-right") + "</button>";
      html += "</div>";
      html += '<p class="describe-err hidden" id="describeErr">' + lx("describe_fail") + "</p>";
    }

    html += '<button type="button" class="foryou best-now" id="bestNowBtn">';
    html += '  <span class="foryou-ic">' + icon("sparkles") + '</span>';
    html += '  <span class="foryou-text">';
    html += '    <span class="foryou-badge">' + icon("locate-fixed") + ' ' + lx("best_near") + '</span>';
    html += '    <span class="foryou-title">' + lx("best_now") + '</span>';
    html += '    <span class="foryou-sub">' + lx("best_now_sub") + '</span>';
    html += '  </span>';
    html += '  <span class="foryou-go">' + icon("arrow-right") + '</span>';
    html += '</button>';

    html += '<div class="vibe-label">' + lx("or_pick") + '</div>';
    html += '<div class="preset-grid">';
    grid.forEach(function (p) {
      var c = pt(p);
      html += '<button type="button" class="preset-card" data-preset="' + p.key + '" style="background:' + p.grad + '">';
      html += '  <span class="scrim"></span>';
      html += '  <span class="p-ic">' + icon(p.icon) + '</span>';
      html += '  <span class="p-title">' + c.t + '</span>';
      html += '  <span class="p-sub">' + c.s + '</span>';
      html += '</button>';
    });
    html += '</div>';
    html += '<button type="button" class="everything" data-preset="surprise">' + icon("sparkles") + ' ' + lx("everything") + '</button>';
    html += '<details class="coord-entry advanced-entry"><summary>' + lx("advanced") + '</summary>';
    html += '<div class="coord-row"><input id="mLat" inputmode="decimal" placeholder="' + lx("lat") + '"><input id="mLon" inputmode="decimal" placeholder="' + lx("lon") + '"><button type="button" class="coord-set" id="mSet">' + lx("set_btn") + '</button></div></details>';

    host.innerHTML = html;
    var now = $("bestNowBtn"); if (now) now.addEventListener("click", chooseNow);
    var dGo = $("describeGo"); if (dGo) dGo.addEventListener("click", submitDescribe);
    var dIn = $("describeInput");
    if (dIn) dIn.addEventListener("keydown", function (e) { if (e.key === "Enter") submitDescribe(); });
    wireMic();
    host.querySelectorAll("[data-preset]").forEach(function (el) {
      el.addEventListener("click", function () {
        var p = PRESETS.filter(function (x) { return x.key === el.dataset.preset; })[0];
        if (p) choosePreset(p);
      });
    });
    host.querySelectorAll(".dp-btn").forEach(function (el) {
      el.addEventListener("click", function () { daypart = el.dataset.dp; buildLauncher(); updateContext(); });
    });
    var ms = $("mSet"); if (ms) ms.addEventListener("click", function () {
      var la = parseFloat(($("mLat") || {}).value), lo = parseFloat(($("mLon") || {}).value);
      if (!isNaN(la) && !isNaN(lo) && window.setLocation) { window.setLocation(la, lo, lx("picked")); placeLabel = lx("picked"); updateContext(); }
    });
    refreshIcons();
  }

  // ---- top-bar context chips (location + weather) ------------------------
  function updateContext() {
    var wrap = $("ctxWrap"); if (!wrap) return;
    wrap.innerHTML = '<button type="button" class="ctx-chip btn" id="ctxLoc">' + icon("locate-fixed") + ' ' + locName() + '</button>';
    var cl = $("ctxLoc"); if (cl) cl.addEventListener("click", useMyLocation);
    refreshIcons();
  }

  // ---- results filters: read/write hidden chips, stage edits, apply once ----
  var FACETS = [
    { key: "time", cont: "timeChips", attr: "minutes", multi: false, label: "time", title: "f_time", field: "minutes" },
    { key: "transport", cont: "transportChips", attr: "transport", multi: false, label: "transport", title: "f_transport", field: "transport" },
    { key: "crew", cont: "groupChips", attr: "group", multi: false, label: "crew", title: "f_crew", field: "group" },
    { key: "interest", cont: "interestChips", attr: "interest", multi: true, label: "interest", title: "f_interest", field: "interests" },
    { key: "effort", cont: "intensityChips", attr: "intensity", multi: false, label: "effort", title: "f_effort", field: "intensity" },
  ];
  function tileText(tile) { var s = tile.querySelector(".tile-text"); return s ? s.textContent.trim() : tile.textContent.trim(); }
  function tileIcon(tile) { var i = tile.querySelector("[data-lucide]"); return i ? i.getAttribute("data-lucide") : "circle"; }
  function facetValue(f) {
    var c = $(f.cont); if (!c) return "";
    if (f.multi) {
      var on = Array.prototype.slice.call(c.querySelectorAll(".tile.is-active"));
      if (!on.length) return "\u2014";
      return on.length > 1 ? tileText(on[0]) + " +" + (on.length - 1) : tileText(on[0]);
    }
    var a = c.querySelector(".tile.is-active");
    return a ? tileText(a) : "\u2014";
  }

  // --- state: read/write the hidden chips; vibe label is cosmetic ---
  function activeVal(cont, attr) { var c = $(cont); if (!c) return null; var a = c.querySelector(".tile.is-active"); return a ? a.dataset[attr] : null; }
  function activeVals(cont, attr) { var c = $(cont); if (!c) return []; return Array.prototype.slice.call(c.querySelectorAll(".tile.is-active")).map(function (t) { return t.dataset[attr]; }); }
  function readFacets() {
    return {
      minutes: activeVal("timeChips", "minutes"),
      transport: activeVal("transportChips", "transport"),
      group: activeVal("groupChips", "group"),
      intensity: activeVal("intensityChips", "intensity"),
      interests: activeVals("interestChips", "interest").slice().sort(),
    };
  }
  function readState() {
    var f = readFacets();
    f.vibeKey = currentMood ? currentMood.key : null;
    f.childrenAges = ($("childrenAges") || {}).value || "";
    f.maxWalkingKm = ($("maxWalkingKm") || {}).value || "";
    f.withDog = !!(($("withDog") || {}).checked);
    f.withElderly = !!(($("withElderly") || {}).checked);
    f.reducedMobility = !!(($("reducedMobility") || {}).checked);
    f.useLiveData = !!(($("useLiveData") || {}).checked);
    return f;
  }
  function presetByKey(k) { return k === "now" ? smartNowPreset() : PRESETS.filter(function (p) { return p.key === k; })[0] || null; }
  function writeState(s) {
    setSingle("timeChips", "minutes", s.minutes);
    setSingle("transportChips", "transport", s.transport);
    setSingle("groupChips", "group", s.group);
    setSingle("intensityChips", "intensity", s.intensity);
    setInterests((s.interests || []).slice());
    currentMood = s.vibeKey ? presetByKey(s.vibeKey) : null;
    setInput("childrenAges", s.childrenAges || "");
    setInput("maxWalkingKm", s.maxWalkingKm || "");
    setChecked("withDog", s.withDog);
    setChecked("withElderly", s.withElderly);
    setChecked("reducedMobility", s.reducedMobility);
    if (s.useLiveData !== undefined) setChecked("useLiveData", s.useLiveData);
    normalizeCrewState(s.group);
  }
  function interestsEqual(a, b) { return a.length === b.length && a.every(function (x, i) { return x === b[i]; }); }
  function facetsEqual(a, b) {
    return a.minutes === b.minutes && a.transport === b.transport && a.group === b.group && a.intensity === b.intensity && interestsEqual(a.interests, b.interests);
  }
  var appliedSnapshot = null; // full state (incl. vibeKey) of the last searched filters
  function isPending() { return !!appliedSnapshot && !facetsEqual(readFacets(), appliedSnapshot); }
  function changeCount() {
    if (!appliedSnapshot) return 0;
    var f = readFacets(), n = 0;
    ["minutes", "transport", "group", "intensity"].forEach(function (k) { if (f[k] !== appliedSnapshot[k]) n++; });
    if (!interestsEqual(f.interests, appliedSnapshot.interests)) n++;
    return n;
  }
  function facetChanged(f) {
    if (!appliedSnapshot) return false;
    var cur = readFacets();
    if (f.multi) return !interestsEqual(cur.interests, appliedSnapshot.interests);
    return cur[f.field] !== appliedSnapshot[f.field];
  }

  // --- commit a search (the only place runSearch fires from the filters) ---
  function commitSearch() {
    if (typeof window.runSearch === "function") window.runSearch();
    var s = readState();
    saveRecent(s);
    appliedSnapshot = s;
  }
  function applyStaged() {
    if (window.setRequestText) window.setRequestText(null);
    openFacetKey = null; commitSearch(); buildFilterBar();
  }
  function resetStaged() { if (appliedSnapshot) writeState(appliedSnapshot); buildFilterBar(); }

  // --- recent choices (localStorage cache of the last 3) ---
  var RECENT_KEY = "ap.recentChoices";
  function loadRecents() { try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; } }
  function persistRecents(list) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) {} }
  function sameChoice(a, b) {
    return a.vibeKey === b.vibeKey && a.minutes === b.minutes && a.transport === b.transport && a.group === b.group && a.intensity === b.intensity &&
      (a.interests || []).join(",") === (b.interests || []).join(",");
  }
  function saveRecent(state) {
    var list = loadRecents().filter(function (x) { return !sameChoice(x, state); });
    list.unshift(state);
    persistRecents(list.slice(0, 3));
  }
  function valueLabel(cont, attr, val) {
    var c = $(cont); if (!c || val == null) return val || "";
    var t = c.querySelector('.tile[data-' + attr + '="' + val + '"]');
    return t ? tileText(t) : String(val);
  }
  function recentLabel(s) {
    if (s.vibeKey) { var p = presetByKey(s.vibeKey); if (p) return icon(p.icon) + " " + pt(p).t; }
    return valueLabel("timeChips", "minutes", s.minutes) + " \u00b7 " + valueLabel("transportChips", "transport", s.transport) + " \u00b7 " + valueLabel("groupChips", "group", s.group);
  }
  function renderRecents() {
    var row = $("recentRow"); if (!row) return;
    var rec = loadRecents();
    if (!rec.length || openFacetKey) { row.className = "recent-row hidden"; row.innerHTML = ""; return; }
    var html = '<span class="recent-label">' + lx("recent") + "</span>";
    rec.forEach(function (s, i) { html += '<button type="button" class="recent-chip" data-i="' + i + '">' + recentLabel(s) + "</button>"; });
    row.className = "recent-row";
    row.innerHTML = html;
    row.querySelectorAll(".recent-chip").forEach(function (el) {
      el.addEventListener("click", function () { writeState(rec[Number(el.dataset.i)]); buildFilterBar(); });
    });
    refreshIcons();
  }

  // --- apply / reset bar (shown only when there are staged changes) ---
  function renderApplyBar() {
    var bar = $("applyBar"); if (!bar) return;
    if (!isPending()) { bar.className = "apply-bar hidden"; bar.innerHTML = ""; return; }
    bar.className = "apply-bar";
    bar.innerHTML = '<span class="apply-count">' + changeCount() + " " + lx("changes") + "</span>" +
      '<span class="apply-actions"><button type="button" class="apply-reset" id="filterReset">' + lx("reset") + "</button>" +
      '<button type="button" class="apply-go" id="filterApply">' + lx("apply") + "</button></span>";
    $("filterReset").addEventListener("click", resetStaged);
    $("filterApply").addEventListener("click", applyStaged);
  }

  // --- filter bar ---
  var openFacetKey = null;
  function buildFilterBar() {
    var bar = $("filterbar"); if (!bar) return;
    var html = '<button type="button" class="mood-pill" id="moodPill">' + icon(currentMood ? currentMood.icon : "dices") + " " +
      (currentMood ? pt(currentMood).t : lx("your_vibe")) + " " + icon("chevron-down") + "</button>";
    FACETS.forEach(function (f) {
      html += '<button type="button" class="fchip' + (facetChanged(f) ? " changed" : "") +
        (aiSetFacets.indexOf(f.key) !== -1 ? " ai-set" : "") +
        '" data-facet="' + f.key + '"><span class="fk">' + lx(f.label) + "</span><b>" + facetValue(f) + "</b>" + icon("chevron-down") + "</button>";
    });
    bar.innerHTML = html;
    $("moodPill").addEventListener("click", function () { resetStaged(); if (window.enterPlanning) window.enterPlanning(); setTimeout(syncPlanningSheet, 0); });
    bar.querySelectorAll("[data-facet]").forEach(function (el) {
      el.addEventListener("click", function () { toggleFacet(el.dataset.facet); });
    });
    renderFacetPanel();
    renderRecents();
    renderApplyBar();
    refreshIcons();
    if (aiSetFacets.length) {
      aiSetFacets = [];   // one-shot: highlight only right after a parse
      bar.querySelectorAll(".fchip.ai-set").forEach(function (el) {
        el.addEventListener("animationend", function () { el.classList.remove("ai-set"); }, { once: true });
      });
    }
  }
  function toggleFacet(key) { openFacetKey = (openFacetKey === key ? null : key); if (window.openSheet) window.openSheet(); renderFacetPanel(); renderRecents(); syncChipActive(); }
  function syncChipActive() {
    var bar = $("filterbar"); if (!bar) return;
    bar.querySelectorAll(".fchip").forEach(function (el) { el.classList.toggle("active", el.dataset.facet === openFacetKey); });
  }
  function renderFacetPanel() {
    var panel = $("facetPanel"); if (!panel) return;
    if (!openFacetKey) { panel.className = "facet-panel hidden"; panel.innerHTML = ""; return; }
    var f = FACETS.filter(function (x) { return x.key === openFacetKey; })[0];
    var c = $(f.cont); if (!c) return;
    var html = "<h4>" + lx(f.title) + '</h4><div class="facet-opts">';
    c.querySelectorAll(".tile").forEach(function (tile) {
      var on = tile.classList.contains("is-active");
      html += '<button type="button" class="facet-pill ' + (on ? "on" : "") + '" data-val="' + (tile.dataset[f.attr] || "") + '">' + icon(tileIcon(tile)) + " " + tileText(tile) + "</button>";
    });
    html += "</div>";
    panel.className = "facet-panel";
    panel.innerHTML = html;
    panel.querySelectorAll(".facet-pill").forEach(function (pill) {
      pill.addEventListener("click", function () {
        if (f.multi) { setInterestToggle(f, pill.dataset.val); }
        else { setSingle(f.cont, f.attr, pill.dataset.val); if (f.key === "crew") normalizeCrewState(pill.dataset.val); openFacetKey = null; }
        currentMood = null;       // customizing clears the vibe label
        buildFilterBar();         // stage only \u2014 NO runSearch
      });
    });
    refreshIcons();
  }
  function setInterestToggle(f, val) {
    var c = $(f.cont); if (!c) return;
    c.querySelectorAll(".tile").forEach(function (tile) {
      if (String(tile.dataset[f.attr]) === String(val)) tile.classList.toggle("is-active");
    });
  }

  // ---- re-render on language change --------------------------------------
  document.querySelectorAll(".lang-btn").forEach(function (btn) {
    btn.addEventListener("click", function () { setTimeout(function () { buildLocBar(); buildLauncher(); updateContext(); setMapHint(); if (document.body.classList.contains("exploring")) buildFilterBar(); }, 0); });
  });

  // ---- boot --------------------------------------------------------------
  function boot() {
    if (typeof window.ensureMap === "function") window.ensureMap();
    wireMapClick();
    var grip = document.querySelector(".launch-grip"); if (grip) grip.addEventListener("click", toggleLauncher);
    var eb = $("editBtn"); if (eb) eb.addEventListener("click", function () { resetStaged(); setTimeout(syncPlanningSheet, 0); });
    buildLocBar();
    buildLauncher();
    setMapHint();
    updateContext();
    refreshIcons();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
