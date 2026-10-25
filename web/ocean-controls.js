(() => {
  "use strict";

  const byId = id => document.getElementById(id);
  const storageKey = "bookpack-ocean-settings-v2";
  const defaults = Object.freeze({ sea: 56, daylight: 55, glass: 10, auto: false });
  const presets = {
    calm: { sea: 18, daylight: 70 },
    sunset: { sea: 56, daylight: 12 },
    swell: { sea: 82, daylight: 55 }
  };
  const frame = byId("ocean-scene");
  const panel = byId("ocean-panel");
  const launcher = byId("ocean-toggle");
  const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
  let prefs = { ...defaults };
  let saveTimer;
  let sendFrame = 0;
  let renderFailed = false;
  const clamp = (value, low, high, fallback) => Number.isFinite(Number(value))
    ? Math.min(high, Math.max(low, Number(value))) : fallback;

  function readPreferences(value) {
    const data = value && typeof value === "object" ? value : {};
    return {
      sea: clamp(data.sea, 0, 100, defaults.sea),
      daylight: clamp(data.daylight, 0, 100, defaults.daylight),
      glass: clamp(data.glass, 0, 40, defaults.glass),
      auto: data.auto === true
    };
  }
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) prefs = readPreferences(JSON.parse(saved));
    else {
      const previous = JSON.parse(localStorage.getItem("bookpack-ocean-settings-v1"));
      prefs = readPreferences({ ...previous, glass: defaults.glass });
    }
  } catch {}
  function saveNow() {
    clearTimeout(saveTimer);
    try { localStorage.setItem(storageKey, JSON.stringify(prefs)); } catch {}
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 160); }
  function daylightName(value) {
    return value < 12 ? "黄昏" : value < 30 ? "夕照" : value < 62 ? "午后" : "正午";
  }
  function updateRange(id, value) {
    const input = byId(id);
    input.value = String(value);
    input.style.setProperty("--range-fill", ((value - Number(input.min)) / (Number(input.max) - Number(input.min)) * 100) + "%");
  }
  function render() {
    updateRange("ocean-waves", prefs.sea);
    updateRange("ocean-daylight", prefs.daylight);
    updateRange("ocean-glass", prefs.glass);
    byId("ocean-waves-value").textContent = Math.round(prefs.sea);
    byId("ocean-daylight-value").textContent = daylightName(prefs.daylight);
    byId("ocean-daylight").setAttribute("aria-valuetext", daylightName(prefs.daylight) + "，" + Math.round(prefs.daylight));
    byId("ocean-glass-value").textContent = Math.round(prefs.glass) + "%";
    byId("ocean-auto").setAttribute("aria-pressed", String(prefs.auto));
    document.documentElement.style.setProperty("--glass-opacity", prefs.glass / 100);
    document.querySelectorAll("[data-ocean-preset]").forEach(button => {
      const preset = presets[button.dataset.oceanPreset];
      const selected = !prefs.auto && prefs.sea === preset.sea && Math.abs(prefs.daylight - preset.daylight) < .1;
      button.setAttribute("aria-pressed", String(selected));
      button.disabled = renderFailed;
    });
    byId("ocean-waves").disabled = renderFailed;
    byId("ocean-daylight").disabled = renderFailed;
    byId("ocean-auto").disabled = renderFailed;
    byId("ocean-reload").hidden = !renderFailed;
    const reducedAuto = prefs.auto && motionPreference.matches;
    byId("ocean-feedback").hidden = !renderFailed && !reducedAuto;
    byId("ocean-feedback").textContent = renderFailed
      ? "动态海面未能启动，可重新加载。玻璃调节仍可使用。"
      : reducedAuto ? "系统已减少动态，日光循环暂时停住。" : "";
  }
  function sendSettings() {
    frame.contentWindow?.postMessage({
      type: "oss-set", sea: prefs.sea, t: prefs.daylight, auto: prefs.auto
    }, location.origin);
  }
  function changed() {
    render();
    scheduleSave();
    if (!sendFrame) sendFrame = requestAnimationFrame(() => { sendFrame = 0; sendSettings(); });
  }
  function openPanel(open, returnFocus = false) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) byId("ocean-waves").focus({ preventScroll: true });
    else if (returnFocus) launcher.focus({ preventScroll: true });
  }
  function loadOcean() {
    renderFailed = false;
    render();
    const url = new URL(frame.dataset.src, location.origin);
    url.search = new URLSearchParams({
      sea: prefs.sea, t: prefs.daylight, auto: prefs.auto ? "1" : "0",
      parentOrigin: location.origin
    }).toString();
    frame.src = url.href;
  }

  launcher.addEventListener("click", () => openPanel(panel.hidden));
  byId("ocean-close").addEventListener("click", () => openPanel(false, true));
  document.addEventListener("pointerdown", event => {
    if (!panel.hidden && !byId("ocean-controls").contains(event.target)) openPanel(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      openPanel(false, true);
    }
  });
  byId("ocean-waves").addEventListener("input", event => { prefs.sea = Number(event.target.value); changed(); });
  byId("ocean-daylight").addEventListener("input", event => {
    prefs.daylight = Number(event.target.value);
    prefs.auto = false;
    changed();
  });
  byId("ocean-glass").addEventListener("input", event => { prefs.glass = Number(event.target.value); changed(); });
  byId("ocean-auto").addEventListener("click", () => { prefs.auto = !prefs.auto; changed(); });
  byId("ocean-reset").addEventListener("click", () => { prefs = { ...defaults }; changed(); });
  byId("ocean-reload").addEventListener("click", loadOcean);
  document.querySelectorAll("[data-ocean-preset]").forEach(button => {
    button.addEventListener("click", () => {
      Object.assign(prefs, presets[button.dataset.oceanPreset], { auto: false });
      changed();
    });
  });
  frame.addEventListener("load", sendSettings);
  window.addEventListener("message", event => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
    if (event.data?.type === "bookpack-ocean-ready") { renderFailed = false; render(); sendSettings(); }
    if (event.data?.type === "bookpack-ocean-error") { renderFailed = true; render(); }
  });
  window.addEventListener("storage", event => {
    if (event.key !== storageKey) return;
    try { prefs = readPreferences(JSON.parse(event.newValue)); render(); sendSettings(); } catch {}
  });
  window.addEventListener("pagehide", saveNow);
  motionPreference.addEventListener("change", render);
  // The renderer owns the clock; this only reflects its current daylight in the slider.
  setInterval(() => {
    if (!prefs.auto || document.hidden) return;
    const current = frame.contentWindow?.__ossState?.();
    if (current && !current.manualTimeOfDay) { prefs.daylight = current.daylight; render(); }
  }, 500);

  render();
  loadOcean();
})();
