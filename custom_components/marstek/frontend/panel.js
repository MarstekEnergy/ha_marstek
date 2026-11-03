/*
 * Marstek sidebar panel.
 *
 * Vanilla custom element, no build step, no external deps (ECharts is loaded
 * lazily from a local static bundle). Home Assistant injects `hass`, `panel`,
 * `narrow` and `route`. The layout mirrors the ems-agent "mars" device page
 * (residentMars header + HMH information / totalStatistics / setting /
 * error-list tabs) rendered in a classic black-and-white theme.
 *
 * IMPORTANT: entity_id slugs are derived from translation_key, not from
 * entity_description.key. For marstek these differ in a few cases
 * (pv_power -> pv_total_power, a/b/c_power -> phase_a/b/c_power,
 * device_mode_select -> device_mode), so every field lookup below uses the
 * translation_key. collectDevices reads translation_key from hass.entities
 * (with a slug-based fallback) and splits display state (sensor/binary_sensor)
 * from control targets (select/number/switch/button).
 *
 * Cloud-only / MQTT cd=xx data (history breakdowns, error list, battery-pack
 * list, OTA, parallel mode, anti-reverse, restart, wifi history) is
 * shown as explicit placeholders because the local UDP API does not expose it.
 */
(() => {
  // Resolve bundled assets relative to this module instead of hard-coding
  // /marstek_static/... paths: panel.js is served as a module, so import.meta
  // points at /marstek_static/frontend/panel.js and assets next to it need no
  // prefix bookkeeping when files are reorganised. brand/ is one level up
  // because HACS requires it at the component root.
  const STATIC_BASE = new URL(".", import.meta.url);
  const asset = (path) => new URL(path, STATIC_BASE).href;

  // translation_key -> { label, unit }
  const FIELDS = {
    battery_soc: { label: "Battery SOC", unit: "%" },
    battery_power: { label: "Battery Power", unit: "W" },
    battery_status: { label: "Battery Status", unit: "" },
    battery_temperature: { label: "Temperature", unit: "°C" },
    battery_remaining_capacity: { label: "Remaining", unit: "Wh" },
    battery_capacity: { label: "Capacity", unit: "Wh" },
    battery_rated_capacity: { label: "Rated Capacity", unit: "Wh" },
    device_mode: { label: "Operating Mode", unit: "" },
    ongrid_power: { label: "Grid Power", unit: "W" },
    offgrid_power: { label: "Off-grid Power", unit: "W" },
    pv_total_power: { label: "Solar Power", unit: "W" },
    total_power: { label: "CT Total", unit: "W" },
    phase_a_power: { label: "Phase A", unit: "W" },
    phase_b_power: { label: "Phase B", unit: "W" },
    phase_c_power: { label: "Phase C", unit: "W" },
    ble_state: { label: "Bluetooth", unit: "" },
    wifi_rssi: { label: "WiFi RSSI", unit: "dBm" },
    wifi_ssid: { label: "WiFi SSID", unit: "" },
    wifi_ip: { label: "WiFi IP", unit: "" },
    wifi_gateway: { label: "Gateway", unit: "" },
    wifi_netmask: { label: "Subnet Mask", unit: "" },
    wifi_dns: { label: "DNS", unit: "" },
    ct_state: { label: "CT Status", unit: "" },
    total_pv_energy: { label: "Solar Generation", unit: "Wh" },
    total_grid_output_energy: { label: "Grid Sell", unit: "Wh" },
    total_grid_input_energy: { label: "Grid Purchase", unit: "Wh" },
    total_load_energy: { label: "Load Usage", unit: "Wh" },
    ct_input_energy: { label: "CT Input", unit: "Wh" },
    ct_output_energy: { label: "CT Output", unit: "Wh" },
    charge_permission: { label: "Charge Permission", unit: "" },
    discharge_permission: { label: "Discharge Permission", unit: "" },
    connected: { label: "Connected", unit: "" },
    // controls
    dod: { label: "Discharge Depth", unit: "%" },
    power_version: { label: "Power Version", unit: "W" },
    bluetooth_advertising: { label: "Bluetooth Advertising", unit: "" },
    led: { label: "Front-panel LED", unit: "" },
  };

  // Inline stroke icons (SF-Symbols-ish) so the navbar menu needs no icon font.
  const TAB_ICON_INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.6"/><path d="M12 11.2v5.3"/><circle cx="12" cy="7.8" r="1.05" fill="currentColor" stroke="none"/></svg>';
  const TAB_ICON_SETTINGS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3.8 8h9.4"/><circle cx="16.4" cy="8" r="2.4"/><path d="M20.2 16h-9.4"/><circle cx="7.6" cy="16" r="2.4"/></svg>';
  // Power glyph used by the restart control (no text button in the row).
  const ICON_POWER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><path d="M12 2.4v9.2"/></svg>';
  // Lock glyphs for the settings guard: open shackle = page editable,
  // closed shackle = every control on the page is frozen.
  const ICON_LOCK_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4.6" y="10.6" width="14.8" height="9.6" rx="2.6"/><path d="M8.4 10.6V7.7a3.6 3.6 0 0 1 7.2 0"/></svg>';
  const ICON_LOCK_CLOSED = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4.6" y="10.6" width="14.8" height="9.6" rx="2.6"/><path d="M8.4 10.6V7.4a3.6 3.6 0 0 1 7.2 0v3.2"/><path d="M12 14.4v2.4"/></svg>';
  const TABS = [
    { key: "information", label: "Device Information", icon: TAB_ICON_INFO },
    { key: "settings", label: "Device Settings", icon: TAB_ICON_SETTINGS },
  ];
  const MODE_OPTIONS = ["auto", "ai", "manual", "ups"];
  // "custom" is not a button here — the date-range picker on the right is the
  // entry point for custom windows; it sets this._statTab = "custom" itself.
  // "year" was dropped: raw history is purged after ~10 days, so a 12-month
  // window could only ever show a partial, misleading line.
  const STAT_TABS = ["now", "today", "month"];
  const CONTROL_DOMAINS = ["select", "number", "switch", "button"];

  // --- helpers -------------------------------------------------------------
  const esc = (s) => {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  };
  const fmt = (v, unit) => {
    if (v === null || v === undefined || v === "unknown" || v === "unavailable") return "—";
    const n = Number(v);
    if (!Number.isNaN(n) && unit === "Wh") return (n / 1000).toFixed(2) + " kWh";
    return String(v) + (unit ? " " + unit : "");
  };
  // Animated numbers: split value+unit so the digits tween while the unit stays
  // still. numHtml renders the initial markup; animateNum tweens updates.
  const numParts = (v, unit) => {
    if (v === null || v === undefined || v === "unknown" || v === "unavailable") return null;
    const n = Number(v);
    if (Number.isNaN(n)) return null;
    if (unit === "Wh") return { n: n / 1000, decimals: 2, unit: "kWh" };
    return { n, decimals: 0, unit: unit || "" };
  };
  const numHtml = (p) => p
    ? `<span class="mk-num" data-v="${p.n}" data-dec="${p.decimals}">${p.n.toFixed(p.decimals)}</span>${p.unit ? `<span class="mk-unit"> ${p.unit}</span>` : ""}`
    : `<span class="mk-num" data-v="">—</span>`;
  function animateNum(numEl, p) {
    if (!numEl) return;
    if (!p) { numEl.textContent = "—"; numEl.dataset.v = ""; return; }
    const decimals = p.decimals != null ? p.decimals : parseInt(numEl.dataset.dec || "0", 10);
    numEl.dataset.dec = String(decimals);
    const from = parseFloat(numEl.dataset.v);
    numEl.dataset.v = String(p.n);
    if (isFinite(from) && from === p.n) return;
    // Replay the entry animation: drop the class, force a reflow so the running
    // animation is cancelled, then add it back.
    numEl.classList.remove("tick");
    void numEl.offsetWidth;
    numEl.classList.add("tick");
    // Drop the class once the sweep lands so the slow idle glint resumes.
    numEl.addEventListener("animationend", () => numEl.classList.remove("tick"), { once: true });
    const start = isFinite(from) ? from : p.n;
    const dur = 450, t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - t, 3);
      numEl.textContent = (start + (p.n - start) * e).toFixed(decimals);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  // WiFi RSSI → lit bars (0-4) and a colour tier for the header chip.
  // -55 dBm and better is a full house; below -85 the link barely holds.
  const sigInfo = (rssi) => {
    const n = Number(rssi);
    if (rssi === null || rssi === undefined || rssi === "" || !isFinite(n)) return { bars: 0, lvl: "none" };
    if (n >= -55) return { bars: 4, lvl: "ok" };
    if (n >= -67) return { bars: 3, lvl: "ok" };
    if (n >= -75) return { bars: 2, lvl: "mid" };
    if (n >= -85) return { bars: 1, lvl: "weak" };
    return { bars: 1, lvl: "bad" };
  };
  // WiFi glyph: a dot plus three concentric arcs, lit from the inside out —
  // the shape phones use for WiFi, unlike the bars that mean cellular.
  const sigHtml = (rssi) => {
    const s = sigInfo(rssi);
    const lit = (i) => (i < s.bars ? " on" : "");
    return `<span class="mk-sig" data-sig="${s.bars}" data-lvl="${s.lvl}" title="WiFi ${fmt(rssi, "dBm")}">` +
      `<svg viewBox="0 3 20 15" aria-hidden="true">` +
      `<circle data-bar class="dot${lit(0)}" cx="10" cy="13" r="1.5"/>` +
      `<path data-bar class="a1${lit(1)}" d="M7.48 10.52A3.5 3.5 0 0 1 12.52 10.52"/>` +
      `<path data-bar class="a2${lit(2)}" d="M5.33 8.33A6.6 6.6 0 0 1 14.67 8.33"/>` +
      `<path data-bar class="a3${lit(3)}" d="M3.14 6.14A9.7 9.7 0 0 1 16.86 6.14"/>` +
      `</svg></span>`;
  };
  const keyForSlug = (slug) => {
    if (Object.prototype.hasOwnProperty.call(FIELDS, slug)) return slug;
    for (const key of Object.keys(FIELDS)) {
      if (slug.length > key.length + 1 && slug.endsWith("_" + key)) return key;
    }
    return null;
  };

  function collectDevices(hass) {
    let ents = Object.values(hass.entities || {}).filter((e) => e.platform === "marstek");
    if (!ents.length) {
      // Fallback when the frontend entity registry is unavailable: derive a
      // single device group from hass.states, matching by slug (≈ translation_key).
      ents = Object.keys(hass.states)
        .filter((eid) => keyForSlug(eid.split(".").pop()))
        .map((eid) => ({ entity_id: eid, platform: "marstek", device_id: "_fallback_" }));
    }
    const byDevice = new Map();
    for (const e of ents) {
      const did = e.device_id || "_nodevice_";
      if (!byDevice.has(did)) byDevice.set(did, { deviceId: did, entities: [] });
      byDevice.get(did).entities.push(e);
    }
    const devices = [];
    for (const [did, grp] of byDevice) {
      const dev = (hass.devices && hass.devices[did]) || {};
      const states = {};
      const entityIds = {};
      for (const e of grp.entities) {
        const st = hass.states[e.entity_id];
        if (!st) continue;
        const slug = e.entity_id.split(".").pop();
        const domain = e.entity_id.split(".")[0];
        const tk = e.translation_key || keyForSlug(slug) || slug;
        // entity_id for history fetch + service calls; controls win over sensors
        if (CONTROL_DOMAINS.includes(domain) || !entityIds[tk]) entityIds[tk] = e.entity_id;
        // display state: sensors win over controls
        if (domain === "sensor" || domain === "binary_sensor" || !states[tk]) states[tk] = st;
      }
      if (!Object.keys(states).length) continue;
      devices.push({
        deviceId: did,
        name: dev.name || "Marstek device",
        model: dev.model || "",
        swVersion: dev.sw_version || "",
        states,
        entityIds,
      });
    }
    return devices;
  }

  const val = (d, k) => (d.states[k] ? d.states[k].state : null);
  const num = (d, k) => {
    const v = val(d, k);
    if (v === null || v === "unknown" || v === "unavailable") return null;
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  };

  let echartsPromise = null;
  function loadEcharts() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (!echartsPromise) {
      echartsPromise = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = asset("echarts.min.js");
        s.onload = () => resolve(window.echarts);
        s.onerror = () => reject(new Error("echarts load failed"));
        document.head.appendChild(s);
      });
    }
    return echartsPromise;
  }

  // Litepicker is loaded lazily from a local static bundle (same pattern as
  // ECharts) and drives the Energy Statistics custom range window. Its
  // dual-month, two-input layout mirrors Element UI's date range picker.
  let litepickerPromise = null;
  function loadLitepicker() {
    if (window.Litepicker) return Promise.resolve(window.Litepicker);
    if (!litepickerPromise) {
      litepickerPromise = new Promise((resolve, reject) => {
        const css = document.createElement("link");
        css.rel = "stylesheet";
        css.href = asset("litepicker.css");
        document.head.appendChild(css);
        const s = document.createElement("script");
        s.src = asset("litepicker.js");
        s.onload = () => resolve(window.Litepicker);
        s.onerror = () => reject(new Error("litepicker load failed"));
        document.head.appendChild(s);
      });
    }
    return litepickerPromise;
  }

  // --- toast notifications ------------------------------------------------
  // A tiny toast host lives on document.body so it floats above the panel.
  // Loading toasts stay until resolved to success/error; others auto-dismiss.
  function toast(msg, type = "info") {
    console.log("[marstek-panel] toast", type, msg);
    const accent =
      type === "loading" ? "#999" : type === "success" ? "#34C759"
      : type === "error" ? "#FF3B30" : "#111";
    // Centered square modal box. Loading also drops a backdrop overlay so the
    // action is unmistakable and blocks interaction until it resolves.
    const box = document.createElement("div");
    box.style.cssText =
      "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:99999;" +
      "width:210px;min-height:180px;padding:26px 22px;background:#fff;" +
      "border:1px solid #d0d0d0;border-radius:18px;box-shadow:0 12px 40px rgba(0,0,0,.25);" +
      "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;" +
      "font-family:inherit;color:#111;font-size:14px;text-align:center;";
    const iconBox = document.createElement("div");
    iconBox.style.cssText = "display:flex;align-items:center;justify-content:center;width:40px;height:40px;";
    const txt = document.createElement("div");
    txt.textContent = msg;
    box.appendChild(iconBox);
    box.appendChild(txt);
    let overlay = null;
    if (type === "loading") {
      overlay = document.createElement("div");
      overlay.style.cssText = "position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,.45);";
      document.body.appendChild(overlay);
      iconBox.innerHTML =
        '<span style="display:inline-block;width:34px;height:34px;border:3px solid #e0e0e0;border-top-color:#000;border-radius:50%;animation:mkspin .7s linear infinite"></span>';
    } else {
      iconBox.innerHTML =
        `<span style="font-size:34px;font-weight:700;line-height:1;color:${accent}">${type === "success" ? "✓" : type === "error" ? "✕" : "ℹ"}</span>`;
    }
    document.body.appendChild(box);
    const dismiss = () => { box.remove(); if (overlay) overlay.remove(); };
    if (type !== "loading") setTimeout(dismiss, type === "error" ? 3000 : 1800);
    return {
      resolve(t, m) {
        if (overlay) { overlay.remove(); overlay = null; }
        const acc = t === "success" ? "#34C759" : "#FF3B30";
        iconBox.innerHTML =
          `<span style="font-size:34px;font-weight:700;line-height:1;color:${acc}">${t === "success" ? "✓" : "✕"}</span>`;
        txt.textContent = m;
        setTimeout(dismiss, t === "error" ? 3000 : 1800);
      },
      dismiss,
    };
  }
  // Run a service call with loading→success/error feedback.
  async function runWithToast(label, fn) {
    const t = toast(label + "…", "loading");
    try {
      await fn();
      t.resolve("success", label + " done");
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      t.resolve("error", label + " failed: " + msg);
    }
  }
  // Lightweight confirm dialog (modal + backdrop), returns Promise<boolean>.
  function confirmDialog(title, body) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.style.cssText =
        "position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,.45);" +
        "display:flex;align-items:center;justify-content:center;";
      const box = document.createElement("div");
      box.style.cssText =
        "width:320px;padding:24px;background:#fff;border:1px solid #d0d0d0;" +
        "border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.25);font-family:inherit;color:#111;font-size:14px;";
      box.innerHTML =
        `<div style="font-size:16px;font-weight:600;margin-bottom:8px">${title}</div>` +
        `<div style="color:#666;line-height:1.5;margin-bottom:20px">${body}</div>` +
        `<div style="display:flex;gap:10px;justify-content:flex-end">` +
        `<button data-cd="cancel" style="border:1px solid #d0d0d0;background:#fff;color:#111;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px">Cancel</button>` +
        `<button data-cd="ok" style="border:1px solid #000;background:#000;color:#fff;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px">Restart</button></div>`;
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      const done = (v) => { overlay.remove(); resolve(v); };
      box.querySelector('[data-cd="ok"]').addEventListener("click", () => done(true));
      box.querySelector('[data-cd="cancel"]').addEventListener("click", () => done(false));
      overlay.addEventListener("click", (e) => { if (e.target === overlay) done(false); });
    });
  }
  // YYYY-MM-DD in local time, for <input type="date"> values.
  const fmtDate = (ms) => {
    const d = new Date(ms);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  };
  // Map a stat tab to a {start, end} window (ms). The "custom" tab honours the
  // date-range picker values stored on the panel element; the other tabs are
  // quick presets that always end "now".
  function computeRange(tab, self) {
    const now = Date.now();
    const today = new Date();
    if (tab === "now") return { start: now - 3600 * 1000, end: now };
    if (tab === "today") return { start: new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime(), end: now };
    if (tab === "month") return { start: new Date(today.getFullYear(), today.getMonth(), 1).getTime(), end: now };
    if (tab === "custom") {
      const s = self && self._customStart ? new Date(self._customStart + "T00:00:00") : new Date(now - 30 * 24 * 3600 * 1000);
      const e = self && self._customEnd ? new Date(self._customEnd + "T23:59:59") : new Date();
      return { start: s.getTime(), end: Math.min(e.getTime(), now) };
    }
    return { start: now - 24 * 3600 * 1000, end: now };
  }
  // HA websocket history (now/today 折线 + charge/discharge today 卡片用)。
  async function fetchHistory(hass, entityId, startMs, endMs) {
    // history_during_period returns full State objects ({state, last_updated
    // as an ISO string}) unless minimal_response is set, in which case each
    // entry is a [state, last_changed, last_updated] tuple with unix-second
    // timestamps. Parse both, plus a compressed {s, lu} shape for safety.
    try {
      const res = await hass.callWS({
        type: "history/history_during_period",
        start_time: new Date(startMs).toISOString(),
        end_time: new Date(endMs || Date.now()).toISOString(),
        entity_ids: [entityId],
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: true,
      });
      const list = (res && res[entityId]) || [];
      return list
        .map((p) => {
          if (Array.isArray(p)) return { t: p[2] * 1000, v: Number(p[0]) };
          const raw = p.state != null ? p.state : p.s;
          const lu = p.last_updated != null ? p.last_updated : p.lu;
          const t = typeof lu === "number" ? lu * 1000 : new Date(lu).getTime();
          return { t, v: Number(raw) };
        })
        .filter((p) => Number.isFinite(p.t) && !Number.isNaN(p.v));
    } catch (err) {
      console.error("[marstek-panel] history fetch failed for", entityId, err);
      return [];
    }
  }
  // Raw string-state history (for enum sensors like battery_status, which
  // fetchHistory would drop because Number("charging") is NaN). Returns
  // [{t, s}] sorted by time.
  async function fetchStateHistory(hass, entityId, startMs, endMs) {
    try {
      const res = await hass.callWS({
        type: "history/history_during_period",
        start_time: new Date(startMs).toISOString(),
        end_time: new Date(endMs || Date.now()).toISOString(),
        entity_ids: [entityId],
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: true,
      });
      const list = (res && res[entityId]) || [];
      return list
        .map((p) => {
          if (Array.isArray(p)) return { t: p[2] * 1000, s: p[0] };
          const raw = p.state != null ? p.state : p.s;
          const lu = p.last_updated != null ? p.last_updated : p.lu;
          const t = typeof lu === "number" ? lu * 1000 : new Date(lu).getTime();
          return { t, s: raw };
        })
        .filter((p) => Number.isFinite(p.t) && p.s != null);
    } catch (err) {
      console.error("[marstek-panel] state history fetch failed for", entityId, err);
      return [];
    }
  }
  // Long-term statistics from the recorder. Raw history rows are purged after
  // ~10 days, but the hourly aggregates are kept forever, so the month view
  // integrates those instead of raw samples. `start`/`end` come back as epoch
  // ms; entries look like {start, end, mean, min, max}.
  async function fetchStatistics(hass, entityId, startMs, endMs, period, types) {
    try {
      const res = await hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(startMs).toISOString(),
        end_time: new Date(endMs || Date.now()).toISOString(),
        statistic_ids: [entityId],
        period,
        types: types && types.length ? types : ["mean"],
      });
      const list = (res && res[entityId]) || [];
      return list
        .map((r) => ({
          start: r.start,
          end: r.end != null ? r.end : r.start,
          mean: r.mean != null ? r.mean : r.state,
          min: r.min,
          max: r.max,
        }))
        .filter((r) => Number.isFinite(r.start));
    } catch (err) {
      console.error("[marstek-panel] statistics fetch failed for", entityId, err);
      return [];
    }
  }
  // Compute battery charge / discharge energy (Wh) over a window by
  // integrating battery_power magnitude split by the battery_status timeline
  // (raw history). `incomplete` is true when the recorder's raw history does
  // not cover the window start (e.g. month view with default 10-day purge),
  // in which case the caller should show "—" rather than an under-count.
  async function chargeDischargeForRange(hass, d, startMs, endMs) {
    const bpEid = d.entityIds.battery_power;
    const bsEid = d.entityIds.battery_status;
    if (!bpEid || !bsEid) return { charge: null, discharge: null, incomplete: true };
    let batPts = await fetchHistory(hass, bpEid, startMs, endMs);
    const statusPts = await fetchStateHistory(hass, bsEid, startMs, endMs);
    if (batPts.length < 2) return { charge: null, discharge: null, incomplete: true };
    const incomplete = batPts[0].t - startMs > 2 * 24 * 3600 * 1000;
    // The recorder writes no row while a value is steady, so the tail reading
    // otherwise loses the last interval (up to the whole window when the
    // battery is idle). Hold it to the window end if the gap is still within
    // the poll cadence — a longer gap means the device dropped off, and faking
    // that much energy would over-count.
    const tail = batPts[batPts.length - 1];
    if (endMs > tail.t && endMs - tail.t <= 30 * 60 * 1000) {
      batPts = batPts.concat([{ t: endMs, v: tail.v }]);
    }
    let charge = 0, discharge = 0, i = 0;
    for (let j = 1; j < batPts.length; j++) {
      const dt = (batPts[j].t - batPts[j - 1].t) / 1000 / 3600; // hours
      const wh = batPts[j - 1].v * dt;
      if (wh <= 0) continue;
      while (i < statusPts.length && statusPts[i].t <= batPts[j].t) i++;
      const st = i > 0 ? statusPts[i - 1].s : null;
      if (st === "charging") charge += wh;
      else if (st === "selling") discharge += wh;
    }
    return { charge, discharge, incomplete };
  }
  // Fill the Information tab's Charge/Discharge Today cards from local
  // history (now/today 保留 recorder)。月度卡片暂不查 recorder，占位等数据源接入。
  async function fillChargeDischarge(panel, d) {
    const hass = panel._hass;
    const now = Date.now();
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const set = (key, wh, incomplete) => {
      const el = panel.querySelector(`[data-stat="${key}"]`);
      if (!el) return;
      el.textContent = wh == null || incomplete ? "—" : (wh / 1000).toFixed(2) + " kWh";
    };
    const t = await chargeDischargeForRange(hass, d, today.getTime(), now);
    set("charge_today", t.charge, t.incomplete);
    set("discharge_today", t.discharge, t.incomplete);
    // 月度总数暂不查 recorder，占位等数据源接入
    set("charge_month", null, true);
    set("discharge_month", null, true);
  }
  // Split a power history (battery_power magnitude) into charge/discharge
  // series using the battery_status timeline. status values: "charging" →
  // charging (green), "selling" → discharging (red); "idle" → neither.
  // Charge is plotted positive and discharge negative so the two directions
  // sit on opposite sides of the 0 line on a shared axis. Each series is
  // filled with 0 at every sample so the lines stay continuous (no gap while
  // idle) and the tooltip shows 0 W instead of a stale value.
  function splitByStatus(powerPts, statusPts) {
    const charge = [], discharge = [];
    // Accept both point shapes: fetchHistory returns {t,v}, holdResample
    // returns [t,v]. Reading p.t on an array yields undefined, which silently
    // produced a pair of empty series.
    const timeOf = (p) => (Array.isArray(p) ? p[0] : p.t);
    const valOf = (p) => (Array.isArray(p) ? p[1] : p.v);
    let i = 0; // statusPts pointer; both arrays are time-ascending
    for (const p of powerPts) {
      const t = timeOf(p);
      const v = valOf(p);
      while (i < statusPts.length && statusPts[i].t <= t) i++;
      // status active at t is the last one with t <= t, i.e. statusPts[i-1]
      const st = i > 0 ? statusPts[i - 1].s : null;
      if (st === "charging") { charge.push([t, v]); discharge.push([t, 0]); }
      else if (st === "selling") { discharge.push([t, -v]); charge.push([t, 0]); }
      else { charge.push([t, 0]); discharge.push([t, 0]); }
    }
    return { charge, discharge };
  }
  // Resample a recorder series onto a fixed time grid with sample-and-hold.
  //
  // The recorder only writes a row when the state *changes*, so a flat stretch
  // (a full battery overnight, an idle inverter) yields zero rows and the chart
  // has nothing to hover there — the axis pointer jumps from one distant change
  // to the next. A state, however, holds its value until the next change, so
  // every point of a regular grid has a *known* value: carry the last reading
  // forward. Grid points and the real change timestamps are merged (nothing is
  // invented, and a short spike between two grid points is still drawn at its
  // true time).
  //
  // Aims at roughly one point per stepMs; stepMs is chosen from the zoom so a
  // day stays a few hundred points instead of tens of thousands.
  const holdResample = (pts, startMs, endMs, stepMs) => {
    if (!pts.length) return [];
    const timeOf = (p) => (Array.isArray(p) ? p[0] : p.t);
    const valOf = (p) => (Array.isArray(p) ? p[1] : p.v);
    // Seed with the newest reading at/before the window start. The history API
    // returns it as the first row because include_start_time_state defaults to
    // true, so the flat stretch before the first in-window change is real. With
    // no seed the window simply has no data until the first reading.
    let seed = null;
    const gridStart = timeOf(pts[0]) > startMs ? timeOf(pts[0]) : startMs;
    for (const p of pts) { if (timeOf(p) <= startMs) seed = p; else break; }
    const times = new Set();
    for (let t = gridStart; t < endMs; t += stepMs) times.add(t);
    times.add(endMs);
    for (const p of pts) {
      const t = timeOf(p);
      if (t >= gridStart && t <= endMs) times.add(t);
    }
    const out = [];
    let i = 0;
    let cur = seed ? valOf(seed) : null;
    for (const t of [...times].sort((a, b) => a - b)) {
      while (i < pts.length && timeOf(pts[i]) <= t) cur = valOf(pts[i++]);
      if (cur != null) out.push([t, cur]);
    }
    return out;
  };
  // Grid step for a window: fine when zoomed in, coarse over days.
  const gridStep = (startMs, endMs) => {
    const span = endMs - startMs;
    if (span <= 3 * 3600 * 1000) return 60 * 1000;
    if (span <= 2 * 24 * 3600 * 1000) return 5 * 60 * 1000;
    if (span <= 7 * 24 * 3600 * 1000) return 15 * 60 * 1000;
    return 60 * 60 * 1000;
  };
  // Stabilize a battery_status timeline against recovery glitches. The
  // integration derives charging/selling from the sign of ongrid_power, so a
  // reconnect (or a firmware hiccup) can flip the status for one or two polls
  // and split one power reading between the charge and discharge lines as a
  // pair of twin spikes. Transitions that hold for less than minHoldMs are
  // dropped so the previous status carries over; unavailable/unknown states
  // are removed first so a recorder drop-out is not treated as an idle flip.
  function stabilizeStatus(statusPts, minHoldMs) {
    const valid = statusPts.filter((p) => p.s !== "unavailable" && p.s !== "unknown");
    if (valid.length < 2) return valid;
    const out = [valid[0]];
    let i = 1;
    while (i < valid.length) {
      if (valid[i].s === out[out.length - 1].s) { i++; continue; }
      // Measure how long the candidate status holds before the next change.
      let j = i + 1;
      while (j < valid.length && valid[j].s === valid[i].s) j++;
      const hold = (j < valid.length ? valid[j].t : Infinity) - valid[i].t;
      if (hold >= minHoldMs) out.push(valid[i]);
      i = j; // skip the whole run either way
    }
    return out;
  }
  // Shared frosted-card shell for axis tooltips (iOS-settings look: white
  // card, soft shadow, rounded corners). The formatters render their own
  // inner rows and wrap them in this shell.
  const TIP_SHELL =
    'background:#fff;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.16),0 2px 8px rgba(0,0,0,.06);' +
    'padding:12px 14px;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;';
  const tipRow = (marker, name, value) =>
    `<div style="display:flex;align-items:center;justify-content:space-between;gap:20px;padding:3px 0">` +
    `<span style="color:#6E6E73;font-size:12px;white-space:nowrap">${marker}${esc(name)}</span>` +
    `<b style="color:#1D1D1F;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap">${value}</b></div>`;
  const tipHead = (when) =>
    `<div style="color:#86868B;font-size:11px;font-weight:600;letter-spacing:.01em;margin-bottom:2px">${esc(when)}</div>`;
  // Axis-tooltip formatter factory. ECharts' built-in axis tooltip can
  // collapse to a single series when the axisPointer snaps to a point, so we
  // render every series passed in `params` explicitly.
  const axisTooltip = (fmt) => (params) => {
    const arr = Array.isArray(params) ? params : [params];
    if (!arr.length) return "";
    const first = arr[0];
    const xv = Array.isArray(first.value) ? first.value[0] : first.axisValue;
    const when =
      first.axisValueLabel != null
        ? first.axisValueLabel
        : typeof xv === "number" && xv > 1e12
          ? new Date(xv).toLocaleString()
          : String(xv ?? "");
    const rows = arr.map((p) => {
      const v = Array.isArray(p.value) ? p.value[1] : p.value;
      const shown = v == null || v === "" ? "—" : fmt ? fmt(v, p.seriesName) : +( +v ).toFixed(2);
      return tipRow(p.marker, p.seriesName, shown);
    });
    return `<div style="${TIP_SHELL}">${tipHead(when)}${rows.join("")}</div>`;
  };
  // Binary-search the largest sample with t <= x (data sorted ascending).
  // Returns the whole [t, v] sample so callers can also apply a staleness
  // cutoff (sparse series like charge/discharge have gaps while idle).
  const nearestLE = (data, x) => {
    if (!data.length) return null;
    let lo = 0, hi = data.length - 1, res = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (data[mid][0] <= x) { res = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return res == null ? data[0] : data[res];
  };
  // Manual axis tooltip: ignore ECharts' single-series params and look up
  // every series' value at the pointer x ourselves. This guarantees all
  // visible series appear in the tooltip on a time axis.
  const makeTooltip = (seriesData, fmt) => (params) => {
    const p = Array.isArray(params) ? params[0] : params;
    let x = p && p.axisValue;
    if (x == null && p && Array.isArray(p.value)) x = p.value[0];
    if (x == null) return "";
    const when = typeof x === "number" ? new Date(x).toLocaleString() : String(x);
    const rows = [];
    for (const s of seriesData) {
      if (!s.data.length) continue;
      const pt = nearestLE(s.data, x);
      // Sparse series (charge/discharge) only sample while active; if the last
      // sample is more than one poll interval (60s) before the cursor, show 0 W
      // instead of a stale value. Continuous 30s-polled lines are unaffected.
      const v = pt == null ? null : (x - pt[0] > 60000 ? 0 : pt[1]);
      const dot = `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${s.color};margin-right:7px;vertical-align:middle"></span>`;
      const shown = v == null ? "—" : fmt ? fmt(v, s.name) : v;
      rows.push(tipRow(dot, s.name, shown));
    }
    return `<div style="${TIP_SHELL}">${tipHead(when)}${rows.join("")}</div>`;
  };
  const fmtPower = (v, name) => name === "SOC" ? (+v).toFixed(1) + " %" : Math.round(+v).toLocaleString() + " W";

  // --- styles (classic black & white) -------------------------------------
  const STYLE = `
    :host { display:block;
      --bg:#F5F5F7; --surface:#FFFFFF; --surface2:#F2F2F4; --surface3:#E8E8ED;
      --border:rgba(0,0,0,0.08); --border2:rgba(0,0,0,0.12);
      --text:#1D1D1F; --sub:#6E6E73; --faint:#A1A1A6;
      --accent:#000000; --accent-press:#000000; --accent-soft:rgba(0,0,0,0.06);
      --green:#34C759; --yellow:#FFCC00; --red:#FF3B30;
      --shadow-sm:0 1px 2px rgba(0,0,0,0.04), 0 2px 8px rgba(0,0,0,0.04);
      --shadow-md:0 2px 10px rgba(0,0,0,0.06), 0 8px 24px rgba(0,0,0,0.06);
      --shadow-lg:0 8px 28px rgba(0,0,0,0.14);
      color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Roboto,sans-serif;
      font-size:14px; background:var(--bg); -webkit-font-smoothing:antialiased; }
    * { box-sizing:border-box; }
    .mk-wrap { max-width:1440px; margin:0 auto; padding:28px 28px 72px; }
    .mk-card { background:var(--surface); border:1px solid var(--border); border-radius:20px; box-shadow:var(--shadow-sm); }
    .mk-pad { padding:24px; }
    .mk-title { font-size:18px; font-weight:600; letter-spacing:-0.01em; }
    .mk-section-title { font-size:17px; font-weight:600; padding:22px 24px 0; letter-spacing:-0.01em; }
    .mk-sub { color:var(--sub); font-size:12px; }
    /* Header card: identity row on top, parameter groups underneath behind a
       hairline, so the device and its numbers read as one block. */
    .mk-header { border:1px solid var(--border); border-radius:20px;
      background:var(--surface); box-shadow:var(--shadow-sm); overflow:hidden; }
    .mk-header-top { display:flex; align-items:center; gap:18px; padding:20px 24px; }
    .mk-header-body { flex:1; min-width:0; display:flex; flex-direction:column; gap:10px; }
    .mk-head-row { display:flex; align-items:center; justify-content:space-between; gap:18px; flex-wrap:wrap; }
    /* The product PNGs are already alpha-cut, so the header slot stays
       transparent: no tinted tile behind the device, just the cut-out object
       with a soft contact shadow. The emoji fallback keeps its own tile since
       a bare glyph would look unanchored. */
    .mk-device-icon { width:84px; height:84px; border-radius:18px;
      display:flex; align-items:center; justify-content:center; font-size:38px; color:#bbb; flex-shrink:0; overflow:visible; background:transparent; }
    .mk-device-img { width:84px; height:84px; object-fit:contain; filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12)); }
    .mk-device-emoji { display:none; width:100%; height:100%; align-items:center; justify-content:center; font-size:34px; border-radius:18px; background:var(--surface2); }
    .mk-name-row { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; min-width:0; }
    .mk-name { font-size:20px; font-weight:600; line-height:1.15; letter-spacing:-0.02em; }
    /* Status reads as a small signal, not a badge. */
    .mk-state { display:inline-flex; align-items:center; gap:7px; font-size:12.5px; color:var(--sub); }
    .mk-state::before { content:""; width:7px; height:7px; border-radius:50%; background:#C7C7CC; flex-shrink:0; }
    .mk-state.on::before { background:var(--green); box-shadow:0 0 0 4px rgba(52,199,89,0.14); }
    /* Spec line: muted label, ink value, spacing instead of punctuation. */
    .mk-spec { display:flex; flex-wrap:wrap; gap:3px 26px; font-size:12.5px; min-width:0; }
    .mk-spec > span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
    .mk-spec b { font-weight:400; color:var(--faint); margin-right:7px; }
    .mk-pill { display:inline-flex; align-items:center; margin-top:3px; padding:4px 14px; border-radius:13px; font-size:12px; font-weight:600; }
    .mk-pill.on { background:var(--green); color:#fff; } .mk-pill.off { background:#C7C7CC; color:#fff; }
    .mk-pill.on::before, .mk-pill.off::before { content:""; width:6px; height:6px; border-radius:50%; background:#fff; margin-right:6px; flex-shrink:0; }
    .mk-pill.on::before { box-shadow:0 0 6px rgba(255,255,255,.85); animation:mk-pulse 2.4s ease-in-out infinite; }
    .mk-pill.off::before { opacity:.55; }
    @keyframes mk-pulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }
    .mk-progress { height:6px; border-radius:6px; background:var(--surface3); overflow:hidden; }
    .mk-progress > div { height:100%; background:var(--green); transition:width .8s cubic-bezier(.4,0,.2,1), background .6s ease; position:relative; overflow:hidden; }
    .mk-progress[data-soc="low"] > div { background:var(--red); }
    .mk-progress[data-soc="mid"] > div { background:var(--yellow); }
    .mk-progress > div::after { content:""; position:absolute; top:0; left:-150%; width:150%; height:100%; background:linear-gradient(90deg,transparent,rgba(255,255,255,.35),transparent); animation:mk-shimmer 2.4s ease-in-out infinite; }
    @keyframes mk-shimmer { 0%{ left:-150%; } 55%{ left:100%; } 100%{ left:100%; } }
    .mk-soc-big, .mk-stat .n { font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1; letter-spacing:-0.02em; }
    .clickable { cursor:pointer; transition:filter .15s; }
    .clickable:hover { filter:brightness(1.04); }
    .mk-details { border-top:1px solid var(--border); padding-top:10px; margin-top:10px; }
    .mk-details > summary { cursor:pointer; font-size:12px; color:var(--sub); font-weight:600; list-style:none; display:flex; align-items:center; gap:7px; }
    .mk-details > summary::-webkit-details-marker { display:none; }
    .mk-details > summary::before { content:"▸"; color:var(--faint); transition:transform .2s; }
    .mk-details[open] > summary::before { transform:rotate(90deg); }
    @media (prefers-reduced-motion:reduce) {
      .mk-progress > div::after { animation:none; }
      .mk-pill.on::before { animation:none; }
      .mk-num { animation:none; }
    }
    /* SOC: the number ends the identity row, the ten-step bar ends the spec
       row, so both rows close on the same right edge. */
    /* The big SOC figure gets its own stack: the rounded SF face reads warmer
       than the UI text face at display sizes and still lines up on the grid,
       falling back to the same system sans where it does not exist. */
    .mk-soc-big { font-family:ui-rounded,"SF Pro Rounded","SF Pro Display",-apple-system,
        BlinkMacSystemFont,"Segoe UI Variable Display","Segoe UI",Roboto,sans-serif;
      font-size:20px; line-height:1; font-weight:700; letter-spacing:-0.035em;
      font-variant-numeric:tabular-nums lining-nums; font-optical-sizing:auto; }
    .mk-soc-big .mk-unit { font-size:12px; font-weight:500; opacity:.5; margin-left:2px; }
    /* WiFi strength chip: the familiar glyph — a dot and three concentric arcs
       lit from the inside out, so a weak link shows only the inner arc. */
    .mk-sig { display:inline-flex; align-items:center; margin-left:3px; vertical-align:middle; }
    .mk-sig svg { display:block; width:14px; height:auto; }
    .mk-sig [data-bar] { color:var(--surface3); transition:color .25s; }
    .mk-sig path { fill:none; stroke:currentColor; stroke-width:1.9; stroke-linecap:round; }
    .mk-sig circle { fill:currentColor; }
    .mk-sig [data-bar].on { color:var(--green); }
    .mk-sig[data-lvl="mid"] [data-bar].on { color:#FF9F0A; }
    .mk-sig[data-lvl="weak"] [data-bar].on { color:#FF9F0A; }
    .mk-sig[data-lvl="bad"] [data-bar].on { color:var(--red); }
    /* SOC bar: segments light up left to right when the card renders, and the
       leading segment keeps a faint pulse so a live number never looks frozen. */
    .mk-soc-seg { display:flex; gap:3px; margin-left:auto; align-self:center; }
    .mk-soc-seg i { width:9px; height:6px; border-radius:3px; background:rgba(29,29,31,0.10);
      transition:background-color .32s ease, opacity .32s ease; }
    .mk-soc-seg i.on { background:var(--green); animation:mkSegIn .4s cubic-bezier(.34,1.56,.64,1) both;
      animation-delay:calc(var(--i,0) * 38ms); }
    @keyframes mkSegIn { from { opacity:.25; transform:scaleX(.35); } to { opacity:1; transform:scaleX(1); } }
    .mk-soc-seg i.tip { animation:mkSegPulse 2.6s ease-in-out infinite; }
    @keyframes mkSegPulse { 0%,100% { filter:brightness(1); } 50% { filter:brightness(1.45); } }
    /* While the battery is charging the lit steps keep sweeping instead of
       settling, so the bar reads as "energy going in" rather than a static level. */
    .mk-header[data-charge="on"] .mk-soc-seg i.on {
      animation:mkSegFlow 1.6s ease-in-out infinite;
      animation-delay:calc(var(--i,0) * 70ms);
    }
    @keyframes mkSegFlow { 0%,100% { opacity:.5; } 45% { opacity:1; } }
    @media (prefers-reduced-motion:reduce) {
      .mk-soc-seg i, .mk-soc-seg i.on, .mk-soc-seg i.tip,
      .mk-header[data-charge="on"] .mk-soc-seg i.on { animation:none; transition:none; }
    }
    .mk-header[data-soc="mid"] [data-soc-val] { color:#C07400; }
    .mk-header[data-soc="low"] [data-soc-val] { color:var(--red); }
    .mk-header[data-soc="mid"] .mk-soc-seg i.on { background:#FF9F0A; }
    .mk-header[data-soc="low"] .mk-soc-seg i.on { background:var(--red); }
    .mk-header[data-soc="na"] .mk-soc-seg i.on { background:#C7C7CC; }
    @media (max-width:640px) {
      .mk-device-icon { width:64px; height:64px; border-radius:14px; }
      .mk-device-img { width:64px; height:64px; }
      .mk-device-emoji { border-radius:14px; }
    }
    /* Navbar trailing group: device picker + plain text menu. */
    .mk-nav-right { margin-left:auto; display:flex; align-items:center; gap:20px; min-width:0; }
    .mk-tabs { display:flex; align-items:center; gap:22px; }
    .mk-tab { display:inline-flex; align-items:center; gap:7px; cursor:pointer; color:var(--sub);
      font-size:15px; font-weight:500; letter-spacing:-0.01em; white-space:nowrap; transition:color .18s; }
    .mk-tab svg { width:17px; height:17px; flex-shrink:0; display:block; }
    .mk-tab:hover { color:var(--text); }
    .mk-tab.active { color:var(--text); font-weight:600; }
    @media (max-width:640px) {
      .mk-navbar { padding:0 14px; }
      .mk-nav-right { gap:12px; }
      .mk-tabs { gap:14px; }
      .mk-tab { font-size:13px; }
      /* Keep the picker icon-only so logo + menu still fit. */
      .mk-nav-right .mk-devsel-name { display:none; }
    }
    .mk-grid { display:grid; gap:20px; margin-top:20px; }
    .mk-row { display:flex; justify-content:space-between; align-items:center; padding:10px 0; border-bottom:1px solid var(--border); }
    .mk-row:last-child { border-bottom:none; } .mk-row .k { color:var(--sub); } .mk-row .v { font-weight:600; }
    .mk-ph { color:var(--faint); font-size:13px; padding:18px; text-align:center; background:var(--surface2); border:1px dashed var(--border2); border-radius:14px; }
    .mk-device-info { display:flex; gap:20px; margin-top:20px; }
    .mk-info-right { display:flex; flex-direction:column; gap:20px; flex:1.1; }
    .mk-stat-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:14px; padding:24px; }
    .mk-stat .l { font-size:12px; color:var(--sub); font-weight:500; } .mk-stat .n { font-size:22px; font-weight:600; margin-top:4px; letter-spacing:-0.02em; } .mk-stat .u { font-size:12px; color:var(--faint); }
    /* Parameter rows: label left / value right, two per line so the card stays
       compact. Both cards share the same 24px inset, every row is filled edge
       to edge, and values align on a common right edge instead of floating in
       oversized tiles. */
    .mk-grid-info { display:grid; grid-template-columns:1fr; gap:0 32px; padding:6px 24px 18px; }
    .mk-grid-info .mk-stat { display:flex; align-items:baseline; justify-content:space-between; gap:14px;
      padding:7px 0; border-bottom:1px solid var(--border); }
    .mk-grid-info .mk-stat:nth-last-child(-n+2) { border-bottom:none; }
    .mk-grid-info .mk-stat .l { font-size:13px; color:var(--sub); font-weight:400; white-space:nowrap; }
    .mk-grid-info .mk-stat .n { font-size:15px; font-weight:600; margin-top:0; text-align:right; letter-spacing:-0.01em; }
    /* Rows that map to an entity open HA's more-info dialog on click. The
       negative margin keeps the text on the 24px inset while the hover band
       bleeds 8px outwards. */
    .mk-grid-info .mk-stat.has-entity { cursor:pointer; margin:0 -8px; padding-left:8px; padding-right:8px;
      border-radius:8px; transition:background .15s; }
    .mk-grid-info .mk-stat.has-entity:hover { background:rgba(29,29,31,0.04); }
    .mk-grid-info .mk-stat.has-entity:active { background:rgba(29,29,31,0.07); }
    .mk-grid-info .mk-stat.has-entity:focus-visible { outline:2px solid rgba(29,29,31,0.35); outline-offset:-2px; }
    @media (min-width:640px) { .mk-grid-info { grid-template-columns:1fr 1fr; } }
    /* The same parameter groups, hosted inside the header card: two columns of
       label/value rows under a hairline, no second card, no extra inset. */
    .mk-head-stats { display:grid; grid-template-columns:1fr 1fr; gap:0 40px;
      border-top:1px solid var(--border); padding:0 24px 16px; }
    .mk-head-stats .mk-section-title { padding:16px 0 0; font-size:13px; color:var(--sub); font-weight:600; }
    .mk-head-stats .mk-grid-info { padding:6px 0 0; }
    /* The low-interest tail of each parameter column (cumulative totals, network
       config) starts folded behind a pill handle, so the header card opens on
       the numbers people actually watch. 0fr → 1fr animates the real content
       height, so nothing has to be measured or hard-coded. */
    .mk-hs-more { display:grid; grid-template-rows:0fr;
      transition:grid-template-rows .42s cubic-bezier(.22,1,.36,1); }
    .mk-hs-more-in { overflow:hidden; min-height:0; }
    .mk-hs-more-in > .mk-grid-info { border-top:1px solid var(--border); }
    .mk-head-stats[data-hs="open"] .mk-hs-more { grid-template-rows:1fr; }
    /* The handle is deliberately quiet: a bare text link, no chrome, so it
       reads as part of the card rather than a button. */
    .mk-hs-toggle { grid-column:1/-1; justify-self:end; display:inline-flex;
      align-items:center; justify-content:center; margin:9px 0 0; padding:4px 0; border:0;
      border-radius:10px; background:none; color:var(--faint); font:inherit; font-size:12px;
      font-weight:500; letter-spacing:.01em; cursor:pointer; transition:color .18s; }
    .mk-hs-toggle:hover { color:var(--sub); }
    .mk-hs-toggle:active { color:var(--text); }
    .mk-hs-toggle:focus-visible { outline:2px solid rgba(29,29,31,0.25); outline-offset:1px; }
    @media (max-width:900px) { .mk-head-stats { grid-template-columns:1fr; gap:0; } }
    /* Energy card — inset-grouped domain blocks: each domain is a tinted
       rounded block with label-left / value-right rows (iOS settings style). */
    .mk-energy-domains { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; padding:0 24px 24px; }
    .mk-energy-domain { min-width:0; }
    .mk-domain-name { font-size:12px; font-weight:600; color:var(--sub); margin-bottom:4px; }
    .mk-domain-stats .mk-stat { display:flex; align-items:baseline; gap:8px; padding:8px 0; border-bottom:1px solid var(--border); }
    .mk-domain-stats .mk-stat:last-child { border-bottom:none; }
    .mk-domain-stats .mk-stat .l { flex:1; font-size:13px; color:var(--sub); font-weight:400; }
    .mk-domain-stats .mk-stat .n { font-size:15px; font-weight:600; margin-top:0; letter-spacing:-0.01em; }
    .mk-domain-stats .mk-stat .u { font-size:11px; }
    .mk-statbar { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:12px 16px; border:1px solid var(--border); border-radius:14px; background:var(--surface); box-shadow:var(--shadow-sm); flex-wrap:wrap; }
    .mk-statbar-left { display:flex; align-items:center; gap:16px; } .mk-statbar-right { display:flex; align-items:center; gap:12px; }
    .mk-seg { display:inline-flex; background:var(--surface3); border:none; border-radius:10px; padding:2px; gap:2px; }
    .mk-seg button { border:none; background:transparent; color:var(--sub); padding:7px 16px; cursor:pointer; font-size:13px; font-weight:500; border-radius:8px; transition:color .2s, background .2s, box-shadow .2s; }
    .mk-seg button:hover:not(.active) { color:var(--text); }
    .mk-seg button.active { background:var(--surface); color:var(--text); box-shadow:var(--shadow-sm); font-weight:600; }
    .mk-chart { width:100%; height:420px; }
    .mk-set-row { display:flex; gap:20px; flex-wrap:wrap; margin-top:20px; }
    .mk-set-col { flex:1; min-width:320px; display:flex; flex-direction:column; gap:20px; }
    .mk-set-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
    .mk-edit-btn { width:34px; height:34px; border:1px solid var(--border2); border-radius:10px; background:var(--surface); cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:16px; color:var(--sub); transition:background .15s, color .15s, border-color .15s; }
    .mk-edit-btn svg { width:17px; height:17px; display:block; }
    .mk-edit-btn:hover { background:var(--surface2); }
    /* Locked state: the glyph inverts so the guard is unmistakable. */
    .mk-edit-btn.is-locked, .mk-edit-btn.is-locked:hover { background:var(--text); border-color:var(--text); color:#fff; }
    /* Freeze every editing control on the page while the lock is engaged.
       pointer-events stays untouched so the "not allowed" cursor can resolve
       on hover — clicks are blocked by the disabled attribute plus the
       _locked guards in the event handlers. */
    .mk-wrap[data-locked] [data-edit-ctrl] { opacity:.4; cursor:not-allowed; }
    .mk-wrap[data-locked] [data-edit-ctrl] * { cursor:not-allowed; }
    /* Hover feedback is suppressed as well: only the cursor reacts, so the
       control never looks like it would accept the gesture. */
    .mk-wrap[data-locked] .mk-seg button:hover:not(.active) { color:var(--sub); }
    .mk-wrap[data-locked] .mk-btn:hover:not(.primary) { background:var(--surface); }
    .mk-wrap[data-locked] .mk-btn.primary:hover { background:var(--accent); }
    .mk-wrap[data-locked] .mk-icon-btn:hover { background:var(--surface); color:var(--sub); border-color:var(--border2); }
    .mk-wrap[data-locked] .mk-toggle:hover { background:#C7C7CC; }
    .mk-wrap[data-locked] .mk-toggle.on:hover { background:var(--green); }
    .mk-toggle { width:42px; height:24px; border-radius:12px; background:#C7C7CC; position:relative; cursor:pointer; transition:background .2s; border:none; }
    .mk-toggle.on { background:var(--green); }
    .mk-toggle::after { content:""; position:absolute; top:2px; left:2px; width:20px; height:20px; border-radius:50%; background:#fff; transition:left .2s; box-shadow:0 1px 3px rgba(0,0,0,0.2); }
    .mk-toggle.on::after { left:20px; }
    .mk-range { width:100%; accent-color:var(--accent); }
    .mk-btn { border:1px solid var(--border2); background:var(--surface); color:var(--text); padding:8px 16px; border-radius:10px; cursor:pointer; font-size:13px; font-weight:500; transition:background .15s; }
    .mk-btn:hover { background:var(--surface2); } .mk-btn.primary { background:var(--accent); color:#fff; border-color:var(--accent); } .mk-btn.primary:hover { background:var(--accent-press); } .mk-btn.danger { border-color:var(--red); color:var(--red); }
    /* Circular glyph button (restart / power actions) — icon only, no chrome text. */
    .mk-icon-btn { width:34px; height:34px; padding:0; border-radius:50%; border:1px solid var(--border2);
      background:var(--surface); color:var(--sub); display:inline-flex; align-items:center; justify-content:center;
      cursor:pointer; transition:background .15s, color .15s, border-color .15s, transform .1s; }
    .mk-icon-btn svg { width:17px; height:17px; display:block; }
    .mk-icon-btn:hover { background:var(--surface2); color:var(--text); }
    .mk-icon-btn:active { transform:scale(.93); }
    .mk-icon-btn:focus-visible { outline:2px solid rgba(0,0,0,0.35); outline-offset:2px; }
    .mk-icon-btn.danger:hover { color:var(--red); border-color:var(--red); background:rgba(255,59,48,0.08); }
    select.mk-sel { border:1px solid var(--border2); background:var(--surface); color:var(--text); padding:7px 10px; border-radius:10px; font-size:13px; }
    .mk-devsel { position:relative; }
    .mk-devsel-btn { display:flex; align-items:center; gap:8px; border:1px solid var(--border2); background:var(--surface); color:var(--text); padding:6px 12px; border-radius:10px; cursor:pointer; font-size:14px; transition:background .15s; }
    .mk-devsel-btn:hover { background:var(--surface2); }
    .mk-devsel-img { width:22px; height:22px; object-fit:contain; }
    .mk-devsel-emoji { font-size:18px; line-height:1; }
    .mk-devsel-name { font-weight:500; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .mk-devsel-menu { position:absolute; top:calc(100% + 6px); right:0; z-index:20; min-width:240px; background:var(--surface); border:1px solid var(--border); border-radius:14px; box-shadow:var(--shadow-lg); overflow:hidden; max-height:320px; overflow-y:auto; }
    .mk-devsel-item { display:flex; align-items:center; gap:10px; padding:8px 12px; cursor:pointer; border-bottom:1px solid var(--border); }
    .mk-devsel-item:last-child { border-bottom:none; }
    .mk-devsel-item:hover { background:var(--surface2); }
    .mk-devsel-item.active { background:var(--accent-soft); color:var(--accent); }
    .mk-devsel-item.active .mk-devsel-model { color:var(--accent); opacity:.7; }
    .mk-devsel-item .mk-devsel-model { margin-left:auto; font-size:11px; color:var(--faint); }
    table.mk-table { width:100%; border-collapse:collapse; font-size:13px; }
    table.mk-table th, table.mk-table td { padding:12px 8px; text-align:center; border-bottom:1px solid var(--border); }
    table.mk-table th { color:var(--sub); font-weight:600; font-size:12px; }
    .mk-dot { display:inline-block; width:10px; height:10px; border-radius:50%; } .mk-dot.on { background:var(--green); } .mk-dot.off { background:#C7C7CC; }
    .mk-legend { display:flex; gap:16px; font-size:12px; color:var(--sub); padding:0 24px 12px; }
    .mk-legend .d { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
    /* Range picker — two inputs joined by a separator. */
    .mk-rangepicker { display:inline-flex; align-items:center; border:1px solid var(--border2); border-radius:10px; background:var(--surface); overflow:hidden; transition:border-color .2s; }
    .mk-rangepicker:focus-within { border-color:var(--accent); }
    .mk-range-input { border:none; background:transparent; color:var(--text); padding:7px 10px; font-size:13px; width:92px; cursor:pointer; outline:none; }
    .mk-range-sep { color:var(--faint); padding:0 2px; }
    .mk-range-icon { display:flex; align-items:center; padding:0 8px; border-left:1px solid var(--border); color:var(--sub); }
    /* Litepicker overrides — system-blue endpoints, soft range fill. */
    .litepicker { border:1px solid var(--border) !important; border-radius:14px !important; box-shadow:var(--shadow-lg) !important; font-family:inherit !important; }
    .litepicker .container__months { border-radius:14px !important; }
    .litepicker .container__months .month__header { color:var(--text) !important; font-weight:600 !important; }
    .litepicker .container__months .month__week { color:var(--sub) !important; }
    .litepicker .container__months .button-previous-month, .litepicker .container__months .button-next-month { color:var(--sub) !important; }
    .litepicker .container__days .day-item { color:var(--text) !important; border-radius:8px !important; }
    .litepicker .container__days .day-item:hover { background:var(--surface2) !important; color:var(--text) !important; }
    .litepicker .container__days .day-item.is-today { color:var(--accent) !important; font-weight:600 !important; }
    .litepicker .container__days .day-item.is-today::before { background:var(--accent) !important; }
    .litepicker .container__days .day-item.is-in-range { background:var(--accent-soft) !important; color:var(--accent) !important; }
    .litepicker .container__days .day-item.is-start-date,
    .litepicker .container__days .day-item.is-end-date { background:var(--accent) !important; color:#fff !important; }
    .litepicker .container__days .day-item.is-start-date.is-end-date { background:var(--accent) !important; }
    .litepicker .container__days .day-item.is-disabled { color:var(--faint) !important; }
    .litepicker .container__days .week-number { color:var(--sub) !important; }
    .mk-spinner { width:14px; height:14px; border:2px solid var(--surface3); border-top-color:var(--accent); border-radius:50%; display:inline-block; animation:mkspin .7s linear infinite; }
    @keyframes mkspin { to { transform:rotate(360deg); } }
    /* Frosted top navbar — saturated blur, system-grey translucent. */
    .mk-navbar { position:sticky; top:0; height:66px; display:flex; align-items:center; gap:12px; padding:0 24px; background:rgba(245,245,247,0.72); backdrop-filter:saturate(180%) blur(20px); -webkit-backdrop-filter:saturate(180%) blur(20px); border-bottom:1px solid var(--border); z-index:100; box-sizing:border-box; }
    .mk-navbar-logo { height:34px; width:auto; display:block; }
    /* Animated number + static unit. */
    .mk-num { display:inline-block; font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1;
      will-change:transform, opacity; }
    .mk-unit { font-size:.78em; color:var(--faint); font-weight:500; }
    /* A changed reading pops into place instead of silently swapping digits,
       so the number still feels live when polls are 30 s apart. */
    @keyframes mkNumTick { from { opacity:.35; transform:scale(.94); } to { opacity:1; transform:none; } }
    .mk-num.tick { animation:mkNumTick .38s cubic-bezier(.22,1,.36,1); }
    /* Pill warn/bad — "sandwich" semantic tints (text/border/bg). */
    .mk-pill.warn { color:#8A6D1A; border:1px solid rgba(255,204,0,.37); background:rgba(255,204,0,.14); }
    .mk-pill.bad { color:var(--red); border:1px solid rgba(255,59,48,.35); background:rgba(255,59,48,.10); }
    .mk-pill.warn::before, .mk-pill.bad::before { content:""; width:6px; height:6px; border-radius:50%; margin-right:6px; flex-shrink:0; }
    .mk-pill.warn::before { background:var(--yellow); } .mk-pill.bad::before { background:var(--red); }
    /* Overline — small uppercase section kicker. */
    .mk-overline { font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--faint); }
    /* Slim scrollbar for internal scroll areas. */
    .mk-devsel-menu::-webkit-scrollbar { width:8px; }
    .mk-devsel-menu::-webkit-scrollbar-thumb { background:#C7C7CC; border-radius:10px; border:2px solid transparent; background-clip:content-box; }
  `;

  // --- residentMars header -------------------------------------------------
  // Map the device_type string (HA sets the device model to device_type) to a
  // bundled product image, mirroring ems-agent's residentHMG getDeviceImg().
  // Only Venus A/D (VenusAD), Venus E mini (VenusEmini) and the Venus C/E
  // default (VenusCE) ships images; any other model falls back to 🔋.
  function deviceImage(d) {
    const m = String(d.model || "").toLowerCase();
    const pick = (f) => asset("img/" + f);
    if (/(vnsa|venus a|venusa)/.test(m)) return pick("VenusAD.png");
    if (/(vnsd|venus d|venusd)/.test(m)) return pick("VenusAD.png");
    if (/(venusemini|venus e mini|venuse mini)/.test(m)) return pick("VenusEmini.png");
    if (/venus|vns/.test(m)) return pick("VenusCE.png");
    return null;
  }

  function renderHeader(d, statsHtml = "") {
    const online = val(d, "connected") === "on";
    const soc = num(d, "battery_soc");
    const socPct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
    // The device reports version 0 when it cannot read its firmware string,
    // so a zero/blank value means "unknown" rather than firmware "0".
    const fw = String(d.swVersion ?? "").trim();
    const fwText = fw && fw !== "0" ? fw : "—";
    const img = deviceImage(d);
    const icon = img
      ? `<img class="mk-device-img" data-devimg src="${esc(img)}" alt="${esc(d.name)}"><span class="mk-device-emoji">🔋</span>`
      : `<span class="mk-device-emoji" style="display:flex">🔋</span>`;
    // Local API (Marstek.GetDevice) returns ble_mac/wifi_mac only — no SN
    // string and no inverter_power, so only show fields the integration has.
    const socTier = soc === null ? "na" : soc < 20 ? "low" : soc < 50 ? "mid" : "ok";
    // Ten-step level bar: one step per 10 %, so the bar and the number agree.
    // `--i` staggers the reveal; the last lit step is the pulsing tip.
    const litSegs = Math.round(socPct / 10);
    const socSegs = Array.from({ length: 10 }, (_, i) => {
      const on = i < litSegs;
      const tip = on && i === litSegs - 1;
      return `<i class="${on ? "on" : ""}${tip ? " tip" : ""}" style="--i:${i}"></i>`;
    }).join("");
    // Header: icon column beside two text rows. The identity row ends with the
    // level readout; the spec line below spans the full text width.
    // data-charge drives a continuous sweep on the level steps while charging.
    const charging = val(d, "battery_status") === "charging";
    return `<div class="mk-header" data-soc="${socTier}" data-charge="${charging ? "on" : "off"}">
      <div class="mk-header-top">
        <div class="mk-device-icon">${icon}</div>
        <div class="mk-header-body">
          <div class="mk-head-row">
            <div class="mk-name-row">
              <span class="mk-name">${esc(d.name)}</span>
              <span class="mk-state ${online ? "on" : "off"}">${online ? "Online" : "Offline"}</span>
            </div>
            <div class="mk-soc-big" data-soc-val title="Battery level">${numHtml(soc === null ? null : { n: soc, decimals: 0, unit: "%" })}</div>
          </div>
          <div class="mk-spec">
            <span><b>Model</b>${esc(d.model || "—")}</span>
            <span><b>Firmware</b>${esc(d.swVersion || "—")}</span>
            <span><b>Mode</b><span data-meta-mode>${esc(val(d, "device_mode") || "—")}</span></span>
            <span><b>WiFi</b><span data-meta-wifi>${esc(val(d, "wifi_ssid") || "—")}</span>${sigHtml(val(d, "wifi_rssi"))}</span>
            <div class="mk-soc-seg" data-soc-seg title="Battery level">${socSegs}</div>
          </div>
        </div>
      </div>
      ${statsHtml}
    </div>`;
  }

  // Six entries fill three rows of the two-column parameter grid, so the tail
  // of each column folds away as "three rows" regardless of the viewport.
  const HS_FOLD_ITEMS = 6;
  const splitTail = (arr, n) => {
    const cut = Math.max(0, arr.length - n);
    return [arr.slice(0, cut), arr.slice(cut)];
  };

  // --- Tab 1: information --------------------------------------------------
  function renderInformation(d, statTab, self) {
    // Energy card: group live power + cumulative energy per domain, merging
    // the old Statistics card (charge/discharge + total grid) and Cumulative
    // Totals card into one. Grid shown once (no duplicate In/Out vs Sell/Purchase).
    // Solar domain only for PV models (VenusA/VenusD); pv_total_power entity
    // exists only for PV_DEVICE_TYPES (see const.py).
    // Energy card: flat stat grid (no domain grouping). Solar entries only
    // for PV models (VenusA/VenusD); pv_total_power entity exists only for
    // PV_DEVICE_TYPES (see const.py).
    const hasPv = !!d.entityIds.pv_total_power;
    // Each row carries the state key (`e`) it displays so a click can open
    // Home Assistant's own more-info dialog for that entity.
    const energy = [];
    if (hasPv) {
      energy.push({ l: "Solar Power", v: fmt(val(d, "pv_total_power"), "W"), e: "pv_total_power" });
      energy.push({ l: "Solar Generation", v: fmt(val(d, "total_pv_energy"), "Wh"), e: "total_pv_energy" });
    }
    energy.push({ l: "Grid Power", v: fmt(val(d, "ongrid_power"), "W"), e: "ongrid_power" });
    energy.push({ l: "Grid Purchase", v: fmt(val(d, "total_grid_input_energy"), "Wh"), e: "total_grid_input_energy" });
    energy.push({ l: "Grid Sell", v: fmt(val(d, "total_grid_output_energy"), "Wh"), e: "total_grid_output_energy" });
    energy.push({ l: "Battery Power", v: fmt(val(d, "battery_power"), "W"), e: "battery_power" });
    energy.push({ l: "Charge Today", k: "charge_today" });
    energy.push({ l: "Discharge Today", k: "discharge_today" });
    energy.push({ l: "Charge Month", k: "charge_month" });
    energy.push({ l: "Discharge Month", k: "discharge_month" });
    energy.push({ l: "Load Power", v: fmt(val(d, "total_power"), "W"), e: "total_power" });
    energy.push({ l: "Load Energy", v: fmt(val(d, "total_load_energy"), "Wh"), e: "total_load_energy" });
    energy.push({ l: "CT Input", v: fmt(val(d, "ct_input_energy"), "Wh"), e: "ct_input_energy" });
    energy.push({ l: "CT Output", v: fmt(val(d, "ct_output_energy"), "Wh"), e: "ct_output_energy" });
    // Device card: non-energy state (temperature, capacity, network, CT/BT,
    // phase). Energy values live in the Energy card, not duplicated here.
    const basic = [
      ["Temperature", fmt(val(d, "battery_temperature"), "°C"), "battery_temperature"],
      ["Remaining Capacity", fmt(val(d, "battery_remaining_capacity"), "Wh"), "battery_remaining_capacity"],
      ["Off-grid Power", fmt(val(d, "offgrid_power"), "W"), "offgrid_power"],
      ["Phase A", fmt(val(d, "phase_a_power"), "W"), "phase_a_power"],
      ["Phase B", fmt(val(d, "phase_b_power"), "W"), "phase_b_power"],
      ["Phase C", fmt(val(d, "phase_c_power"), "W"), "phase_c_power"],
      ["WiFi RSSI", fmt(val(d, "wifi_rssi"), "dBm"), "wifi_rssi"],
      ["Gateway", esc(val(d, "wifi_gateway") || "—"), "wifi_gateway"],
      ["Subnet Mask", esc(val(d, "wifi_netmask") || "—"), "wifi_netmask"],
      ["DNS", esc(val(d, "wifi_dns") || "—"), "wifi_dns"],
      ["CT Status", val(d, "ct_state") === "on" ? "Connected" : val(d, "ct_state") === "off" ? "Not connected" : "—", "ct_state"],
      ["Bluetooth", esc(val(d, "ble_state") || "—"), "ble_state"],
    ];
    // Resolve the entity behind a row from the state object actually shown, so
    // the dialog always matches the displayed value (sensors over controls).
    const entityFor = (key) => (key && d.states[key] ? d.states[key].entity_id : "");
    const stat = (it) => {
      const eid = entityFor(it.e);
      return `<div class="mk-stat${eid ? " has-entity" : ""}"${eid ? ` data-entity="${esc(eid)}" role="button" tabindex="0" title="${esc(eid)}"` : ""}><div class="l">${it.l}</div><div class="n"${it.k ? ` data-stat="${it.k}"` : ""}>${it.v != null ? it.v : "—"}</div></div>`;
    };
    // Both parameter groups live inside the header card now, side by side under
    // a hairline, so the device identity and its numbers read as one block.
    // Each column's last three rows sit in a fold that the handle slides open.
    const hsOpen = !!(self && self._hsOpen);
    const row = (b) => stat({ l: b[0], v: b[1], e: b[2] });
    const [energyMain, energyTail] = splitTail(energy, HS_FOLD_ITEMS);
    const [basicMain, basicTail] = splitTail(basic, HS_FOLD_ITEMS);
    const fold = (html) => `
          <div class="mk-hs-more"><div class="mk-hs-more-in">
            <div class="mk-grid-info">${html}</div>
          </div></div>`;
    const stats = `
      <div class="mk-head-stats" data-hs="${hsOpen ? "open" : "closed"}">
        <div>
          <div class="mk-section-title">Power &amp; Energy</div>
          <div class="mk-grid-info">${energyMain.map(stat).join("")}</div>${fold(energyTail.map(stat).join(""))}
        </div>
        <div>
          <div class="mk-section-title">Device Status</div>
          <div class="mk-grid-info">${basicMain.map(row).join("")}</div>${fold(basicTail.map(row).join(""))}
        </div>
        <button class="mk-hs-toggle" type="button" data-hs-toggle aria-expanded="${hsOpen}">
          <span data-hs-label>${hsOpen ? "Show less" : "Show more"}</span>
        </button>
      </div>`;
    return `
      ${renderHeader(d, stats)}
      <div style="margin-top:20px">${renderStatistics(d, statTab, self)}</div>
      ${renderErrors()}`;
  }

  // --- Tab 2: statistics ---------------------------------------------------
  function renderStatistics(d, statTab, self) {
    const statTabs = STAT_TABS.map((t) =>
      `<button class="${t === statTab ? "active" : ""}" data-stattab="${t}">${t}</button>`
    ).join("");
    return `
      <div class="mk-card mk-pad">
        <div class="mk-set-head">
          <div class="mk-title" style="font-size:16px">Battery</div>
          <div class="mk-seg" data-stattabs>${statTabs}</div>
        </div>
        <div class="mk-sub" data-battery-totals></div>
        <div class="mk-chart" data-chart="battery" style="height:300px"></div>
      </div>`;
  }

  async function renderCharts(panel, d, startMs, endMs, statTab) {
    // Token guard: if a newer renderCharts call starts before this one
    // finishes its async fetches, abort the stale call so it doesn't
    // overwrite the newer chart with stale data.
    const token = (panel._chartToken || 0) + 1;
    panel._chartToken = token;
    console.log("[marstek-panel] renderCharts entityIds:", d.entityIds, "tab:", statTab);
    let echarts;
    try {
      echarts = await loadEcharts();
    } catch {
      panel.root.querySelectorAll("[data-chart]").forEach((el) => {
        el.innerHTML = '<div class="mk-ph">ECharts failed to load</div>';
      });
      return;
    }
    if (panel._chartToken !== token) return; // superseded by a newer call
    const noData = (el, msg) => { el.innerHTML = `<div class="mk-ph">${msg}</div>`; };
    const mkChart = (el) => {
      if (!el) return null;
      const old = echarts.getInstanceByDom(el);
      if (old) old.dispose();
      el.innerHTML = "";
      const chart = echarts.init(el);
      // Inside zoom has no handle to drag back, so a double-click restores the
      // full preset window on any chart that added the dataZoom component.
      chart.on("dblclick", () => chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
      return chart;
    };
    const hexToRgba = (hex, a) => {
      const h = String(hex || "").replace("#", "");
      const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
      const n = parseInt(full, 16);
      if (Number.isNaN(n)) return `rgba(0,0,0,${a})`;
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    };
    // Vertical fade from the series colour into the 0 line (flip for the
    // negative discharge half so the tint pools below the axis instead).
    const areaGrad = (color, flip) => ({
      type: "linear", x: 0, y: 0, x2: 0, y2: 1,
      colorStops: flip
        ? [{ offset: 0, color: hexToRgba(color, 0) }, { offset: 1, color: hexToRgba(color, 0.30) }]
        : [{ offset: 0, color: hexToRgba(color, 0.30) }, { offset: 1, color: hexToRgba(color, 0) }],
    });
    // Tooltip chrome is drawn by the formatter's frosted card, so ECharts'
    // own box is made transparent and borderless.
    const tipConf = (formatter) => ({
      trigger: "axis", confine: true,
      backgroundColor: "transparent", borderWidth: 0, padding: 0,
      extraCssText: "box-shadow:none;border:none;",
      axisPointer: { type: "line", snap: false, lineStyle: { color: "#C7C7CC", width: 1, type: "dashed" } },
      formatter,
    });
    // --- zoom ---------------------------------------------------------------
    // Two dataZoom components share the same window: "inside" for wheel /
    // drag-on-plot, and an optional visible slider drawn under the grid.
    // filterMode:none keeps every point in the series (only the visible window
    // changes), so lines and their shaded areas keep their shape at any zoom.
    // Double-click resets both (see mkChart).
    const insideZoom = () => ({
      type: "inside", xAxisIndex: 0, filterMode: "none", start: 0, end: 100,
      zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false,
    });
    // Visible slider styled to the same muted palette: hairline track, faint
    // blue selection, white round handles, and a "HH:MM – HH:MM" caption that
    // only appears while dragging.
    const sliderZoom = () => ({
      type: "slider", xAxisIndex: 0, filterMode: "none", start: 0, end: 100,
      height: 20, bottom: 8,
      borderColor: "transparent",
      backgroundColor: "rgba(0,0,0,0.03)",
      fillerColor: "rgba(10,132,255,0.08)",
      dataBackground: {
        lineStyle: { color: "rgba(29,29,31,0.22)", width: 1 },
        areaStyle: { color: "rgba(29,29,31,0.05)" },
      },
      selectedDataBackground: {
        lineStyle: { color: "rgba(10,132,255,0.55)", width: 1 },
        areaStyle: { color: "rgba(10,132,255,0.10)" },
      },
      handleSize: "110%",
      handleStyle: { color: "#FFFFFF", borderColor: "rgba(0,0,0,0.18)", borderWidth: 1, shadowBlur: 4, shadowColor: "rgba(0,0,0,0.16)" },
      moveHandleSize: 3,
      moveHandleStyle: { color: "rgba(0,0,0,0.14)" },
      brushSelect: false,
      textStyle: { color: "#8E8E93", fontSize: 10 },
      labelFormatter: (v) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
    });
    const baseOpt = (series, extra = {}) => ({
      animationDuration: 420, animationEasing: "cubicOut",
      grid: { left: 48, right: 48, top: 46, bottom: 34 },
      tooltip: tipConf(axisTooltip()),
      dataZoom: [insideZoom()],
      // Legend hugs the very top edge so it cannot cover the axis names, which
      // ECharts draws at each axis' top end. grid.top keeps the names clear of
      // the legend even when a narrow card wraps it onto two lines.
      legend: { data: series.map((x) => x.name), top: 0, right: 12, icon: "circle", itemWidth: 9, itemHeight: 9, itemGap: 16, textStyle: { color: "#6E6E73", fontSize: 12 } },
      // Pin the time axis to the selected window so it visibly changes when
      // switching presets, even when the recorder only holds a short slice.
      xAxis: { type: "time", min: startMs, max: endMs, axisLine: { lineStyle: { color: "#E5E5EA" } }, axisTick: { show: false }, axisLabel: { color: "#8E8E93", fontSize: 11, hideOverlap: true } },
      ...extra,
      series,
    });
    const line = (name, data, color, yidx = 0, dashed = false, area = false, areaFlip = false) => ({
      name, type: "line", smooth: true, symbol: "none", yAxisIndex: yidx,
      lineStyle: { width: 2, color, type: dashed ? "dashed" : "solid", cap: "round", join: "round" },
      itemStyle: { color }, emphasis: { focus: "series", lineStyle: { width: 3 } },
      areaStyle: area ? { color: areaGrad(color, areaFlip) } : undefined,
      data,
    });
    // Render a grouped bar chart from bucketed energy (Wh) series and write
    // a per-series total string into totalsEl. buckets: [{name,color,map:Map(ms→Wh),total}].
    const renderBars = (el, buckets, gran, totalsEl, emptyMsg) => {
      const keys = [];
      for (const b of buckets) for (const k of b.map.keys()) if (!keys.includes(k)) keys.push(k);
      keys.sort((a, b) => a - b);
      const labels = keys.map((t) =>
        gran === "month"
          ? new Date(t).toLocaleString(undefined, { month: "short" })
          : `${new Date(t).getMonth() + 1}/${new Date(t).getDate()}`
      );
      const series = buckets.map((b) => ({
        name: b.name, type: "bar", barMaxWidth: 26,
        itemStyle: { color: b.color, borderRadius: [5, 5, 0, 0] },
        data: keys.map((t) => +(((b.map.get(t) || 0) / 1000).toFixed(3))),
      }));
      if (totalsEl) {
        totalsEl.textContent = buckets.map((b) => `${b.name} ${(b.total / 1000).toFixed(2)}`).join(" · ") + " kWh";
      }
      if (!keys.length) { noData(el, emptyMsg || "No history yet"); return; }
      mkChart(el).setOption({
        animationDuration: 420, animationEasing: "cubicOut",
        grid: { left: 52, right: 20, top: 36, bottom: 28 },
        tooltip: {
          trigger: "axis", confine: true,
          backgroundColor: "transparent", borderWidth: 0, padding: 0, extraCssText: "box-shadow:none;border:none;",
          axisPointer: { type: "shadow", shadowStyle: { color: "rgba(0,0,0,0.04)" } },
          formatter: axisTooltip((v) => v == null ? "—" : (+v).toFixed(2) + " kWh"),
        },
        legend: { top: 4, right: 12, icon: "circle", itemWidth: 9, itemHeight: 9, textStyle: { color: "#6E6E73", fontSize: 12 } },
        xAxis: { type: "category", data: labels, axisLine: { lineStyle: { color: "#E5E5EA" } }, axisTick: { show: false }, axisLabel: { color: "#8E8E93", fontSize: 11 } },
        yAxis: { type: "value", name: "kWh", nameGap: 6, nameTextStyle: { color: "#A1A1A6" }, splitLine: { lineStyle: { color: "#F0F0F2" } }, axisLabel: { color: "#8E8E93", fontSize: 11 } },
        series,
      });
    };
    const pull = async (key) => {
      const eid = d.entityIds[key];
      if (!eid) return [];
      const data = (await fetchHistory(panel._hass, eid, startMs, endMs)).map((p) => [p.t, p.v]);
      console.log("[marstek-panel] history", key, eid, data.length, "points");
      return data;
    };
    // Raw {t, v} points (for splitting battery power by status).
    const pullRaw = async (key) => {
      const eid = d.entityIds[key];
      if (!eid) return [];
      return await fetchHistory(panel._hass, eid, startMs, endMs);
    };
    // String-state points (enum sensors like battery_status).
    const pullStatus = async (key) => {
      const eid = d.entityIds[key];
      if (!eid) return [];
      return await fetchStateHistory(panel._hass, eid, startMs, endMs);
    };

    // Battery: SOC (yellow dashed, right axis) + charge (green) / discharge
    // (red), derived from battery_power magnitude split by battery_status.
    const batEl = panel.root.querySelector('[data-chart="battery"]');
    const batTotalsEl = panel.root.querySelector("[data-battery-totals]");
    if (batEl) {
      if (statTab === "month") {
        // Long period: daily energy throughput. The charge/discharge split
        // needs the battery_status timeline (raw history), which is purged
        // after ~10 days, so a month cannot be rebuilt from raw rows. Long-term
        // statistics keep the hourly mean battery_power forever, so integrate
        // those into per-day Wh instead: mean W × bucket hours.
        const eid = d.entityIds.battery_power;
        const map = new Map();
        let total = 0;
        if (eid) {
          const rows = await fetchStatistics(panel._hass, eid, startMs, endMs, "day", ["mean"]);
          for (const r of rows) {
            if (r.mean == null) continue;
            const wh = r.mean * ((r.end - r.start) / 3600000);
            const key = new Date(r.start).setHours(0, 0, 0, 0);
            map.set(key, (map.get(key) || 0) + wh);
            total += wh;
          }
        }
        if (!map.size) {
          // Statistics disabled or not generated yet: integrate whatever raw
          // rows the recorder still holds, per day. Partial, but better than
          // an empty card.
          const raw = await pullRaw("battery_power");
          for (let i = 1; i < raw.length; i++) {
            const wh = raw[i - 1].v * ((raw[i].t - raw[i - 1].t) / 3600000);
            const key = new Date(raw[i - 1].t).setHours(0, 0, 0, 0);
            map.set(key, (map.get(key) || 0) + wh);
            total += wh;
          }
        }
        const buckets = map.size ? [{ name: "Throughput", color: "#5AC8FA", map, total }] : [];
        renderBars(batEl, buckets, "day", batTotalsEl, "No battery history yet");
      } else {
        let socPts = await pull("battery_soc");
        // Both series are resampled onto a regular grid so the axis has a value
        // at every point instead of only where the recorder saw a change.
        // A window with no rows at all (fresh install, purged) still falls back
        // to the live reading so the card is not empty.
        const stepMs = gridStep(startMs, endMs);
        if (!socPts.length) {
          const live = num(d, "battery_soc");
          if (live != null) socPts = [[startMs, live], [endMs, live]];
        } else {
          socPts = holdResample(socPts, startMs, endMs, stepMs);
        }
        // Same for power: an idle battery keeps one 0 W row, so the grid fills
        // the flat stretches before the series is split by status.
        const batPts = holdResample(await pullRaw("battery_power"), startMs, endMs, stepMs);
        const statusPts = await pullStatus("battery_status");
        // Debounce recovery glitches: status flips shorter than ~3 polls would
        // otherwise split one power reading into twin charge/discharge spikes.
        const { charge, discharge } = splitByStatus(batPts, stabilizeStatus(statusPts, 90 * 1000));
        const hasPower = charge.length > 0 || discharge.length > 0;
        const hasSoc = socPts.length > 0;
        // Devices that report SOC but no battery_power history leave the W
        // axis empty. Drop the unused W axis in that case and put SOC on the
        // single axis so the chart does not collapse onto a lone leftover axis.
        const socYidx = hasPower ? 1 : 0;
        const batData = [
          { name: "Charge", color: "#34C759", data: charge },
          { name: "Discharge", color: "#FF3B30", data: discharge },
          { name: "SOC", color: "#FFCC00", data: socPts },
        ];
        // Latest SOC, rendered as the badge text. Captured here instead of in
        // the label formatter: a markPoint built from `coord` only carries no
        // value, so a formatter reading params.value produced an empty badge.
        const socTail = socPts[socPts.length - 1];
        const socTailLabel = socTail ? "SOC " + Math.round(+socTail[1]) + "%" : "";
        const series = [
          // Charge / discharge: slightly heavier stroke with a soft glow so the
          // two halves of the symmetric W axis stay readable where they stack.
          // Plain point-to-point segments, like HA's own history chart: sparse
          // recorder rows are connected straight instead of being drawn as
          // sample-and-hold steps, so the curve reads as a continuous signal.
          Object.assign(line("Charge", charge, "#34C759", 0, false, true), {
            smooth: false,
            lineStyle: { width: 2.4, color: "#34C759", cap: "round", join: "round", shadowColor: hexToRgba("#34C759", 0.30), shadowBlur: 8, shadowOffsetY: 3 },
            markLine: hasPower ? { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: "rgba(29,29,31,0.14)", width: 1, type: [3, 3] }, data: [{ yAxis: 0 }] } : undefined,
          }),
          Object.assign(line("Discharge", discharge, "#FF3B30", 0, false, true, true), {
            smooth: false,
            lineStyle: { width: 2.4, color: "#FF3B30", cap: "round", join: "round", shadowColor: hexToRgba("#FF3B30", 0.28), shadowBlur: 8, shadowOffsetY: -3 },
          }),
          {
            // SOC: no per-point symbols (cleaner over the power lines); only the
            // tail marker carries a dot. clip:false lets that dot and its label
            // render past the grid edge. Straight segments between samples —
            // HA's history chart draws the same recorder rows the same way.
            name: "SOC", type: "line", smooth: false, yAxisIndex: socYidx, clip: false,
            symbol: "circle", symbolSize: 0, showSymbol: false,
            lineStyle: { width: 2, color: "#FFCC00", type: [7, 5], cap: "round", join: "round", shadowColor: hexToRgba("#FFCC00", 0.35), shadowBlur: 6 },
            itemStyle: { color: "#FFCC00", borderColor: "#fff", borderWidth: 1 },
            emphasis: { focus: "series", lineStyle: { width: 3 } },
            // Softer yellow wash: the plot now carries three zone tints behind
            // it and a 16 % area film was veiling them into one beige block.
            areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: hexToRgba("#FFCC00", 0.10) }, { offset: 1, color: hexToRgba("#FFCC00", 0) }] } },
            // SOC bands: three stacked zones — red reserve ≤20 %, amber buffer
            // 20–50 %, green healthy ≥50 %. Each zone gets its own faint tint
            // plus a corner caption, and the two edges carry a dashed guide in
            // the colour of the lower zone. Clip the bands to the plot so the
            // tints never bleed over the axes.
            markArea: {
              silent: true,
              label: { show: true, position: "insideTopLeft", padding: [5, 0, 0, 7], fontSize: 10, fontWeight: 700, formatter: (p) => p.name },
              data: [
                [
                  { yAxis: 0, name: "LOW ≤20%", itemStyle: { color: hexToRgba("#FF3B30", 0.085) }, label: { color: hexToRgba("#FF3B30", 0.65) } },
                  { yAxis: 20 },
                ],
                [
                  { yAxis: 20, name: "MID 20–50%", itemStyle: { color: hexToRgba("#FF9F0A", 0.08) }, label: { color: hexToRgba("#C07400", 0.85) } },
                  { yAxis: 50 },
                ],
                [
                  { yAxis: 50, name: "OK ≥50%", itemStyle: { color: hexToRgba("#34C759", 0.06) }, label: { color: hexToRgba("#1F8B3B", 0.70) } },
                  { yAxis: 100 },
                ],
              ],
            },
            markLine: {
              silent: true, symbol: "none", label: { show: false }, lineStyle: { width: 1, type: [4, 4] },
              data: [
                { yAxis: 20, lineStyle: { color: hexToRgba("#FF3B30", 0.35) } },
                { yAxis: 50, lineStyle: { color: hexToRgba("#FF9F0A", 0.38) } },
              ],
            },
            markPoint: {
              symbol: "circle", symbolSize: 11, silent: true,
              itemStyle: { color: "#FFCC00", borderColor: "#fff", borderWidth: 2.5, shadowColor: hexToRgba("#FFCC00", 0.55), shadowBlur: 10 },
              label: {
                // Anchored to the left of the dot so the badge lands inside the
                // plot: never clipped by the grid edge and never fighting the
                // % axis labels on the right.
                show: true, position: "left", distance: 10,
                color: "#1D1D1F", fontSize: 11, fontWeight: 700,
                backgroundColor: "rgba(255,255,255,0.95)",
                borderColor: hexToRgba("#FFCC00", 0.55), borderWidth: 1, borderRadius: 9,
                padding: [3, 8, 3, 8],
                shadowColor: "rgba(0,0,0,0.12)", shadowBlur: 8, shadowOffsetY: 2,
                formatter: () => socTailLabel,
              },
              data: [{ coord: socTail, value: socTailLabel }],
            },
            data: socPts,
          },
        ].filter((s) => s.data.length);
        // Symmetric W axis: charge is positive, discharge negative, so 0 sits
        // in the middle and both directions share matching tick steps.
        let maxW = 0;
        for (const pt of charge) if (pt[1] > maxW) maxW = pt[1];
        for (const pt of discharge) if (-pt[1] > maxW) maxW = -pt[1];
        maxW = Math.max(100, Math.ceil((maxW * 1.1) / 100) * 100);
        const yAxis = [];
        // nameGap pulls the unit down towards the plot so it clears the legend
        // band at the top of the canvas.
        if (hasPower) yAxis.push({ type: "value", name: "W", min: -maxW, max: maxW, nameGap: 8, nameTextStyle: { color: "#A1A1A6" }, splitLine: { lineStyle: { color: "#F0F0F2" } }, axisLabel: { color: "#8E8E93", fontSize: 11 } });
        if (hasSoc) yAxis.push({ type: "value", name: "%", min: 0, max: 100, nameGap: 8, nameTextStyle: { color: "#A1A1A6" }, splitLine: { show: false }, axisLabel: { color: "#8E8E93", fontSize: 11 } });
        if (batTotalsEl) batTotalsEl.textContent = "";
        if (!series.length) noData(batEl, "No battery history yet");
        else mkChart(batEl).setOption(baseOpt(series, {
          tooltip: tipConf(makeTooltip(batData, fmtPower)),
          // right padding keeps the tail badge off the edge; top matches the
          // raised legend so the "W" axis name stays visible. bottom reserves
          // the slider band (20px slider + caption) under the time labels, so
          // the two never sit on top of each other.
          grid: { left: 52, right: 56, top: 46, bottom: 64 },
          dataZoom: [insideZoom(), sliderZoom()],
          yAxis,
        }));
      }
    }
    // WiFi signal strength (dBm) from the local wifi_rssi sensor history.
    // The sensor only writes a recorder row when the value changes, so a
    // stable link yields just one or two points — invisible as a symbol-less
    // line. Expand the readings sample-and-hold style (each reading holds
    // until the next one, up to the window end) so the chart draws a level
    // line even from a single sample.
    const wifiEl = panel.root.querySelector('[data-chart="wifi"]');
    if (wifiEl) {
      const raw = await pull("wifi_rssi");
      // Same grid resample as the battery card: a stable link writes almost no
      // rows, so without it the axis pointer has nothing to report between two
      // distant readings. Keep step:"end" — a signal level holds until the next
      // reading, it does not ramp.
      const rssi = holdResample(raw, startMs, endMs, gridStep(startMs, endMs));
      const series = [{ name: "RSSI", type: "line", step: "end", smooth: false, symbol: "none",
        lineStyle: { width: 2, color: "#3B7FF2", cap: "round" }, itemStyle: { color: "#3B7FF2" }, data: rssi }];
      if (!rssi.length) noData(wifiEl, "No WiFi signal history yet — enable the WiFi signal strength entity");
      else mkChart(wifiEl).setOption(baseOpt(series, {
        legend: { show: false },
        tooltip: tipConf(axisTooltip((v) => v == null ? "—" : Math.round(+v) + " dBm")),
        grid: { left: 48, right: 24, top: 36, bottom: 30 },
        yAxis: { type: "value", name: "dBm", min: -100, max: -20, nameTextStyle: { color: "#A1A1A6" }, splitLine: { lineStyle: { color: "#F0F0F2" } }, axisLabel: { color: "#8E8E93", fontSize: 11 } },
      }));
    }
  }

  // --- Tab 3: settings -----------------------------------------------------
  function renderSettings(d, panel) {
    const modeEid = d.entityIds.device_mode;
    const modeCur = (d.states.device_mode && d.states.device_mode.state) || "";
    const pvEid = d.entityIds.power_version;
    const pvOpts = (d.states.power_version && d.states.power_version.attributes.options) || [];
    const pvCur = (d.states.power_version && d.states.power_version.state) || "";
    const bleEid = d.entityIds.bluetooth_advertising;
    const ledEid = d.entityIds.led;
    // Page-level edit guard: one padlock freezes every control on this tab.
    const locked = !!(panel && panel._locked);
    const dis = locked ? " disabled" : "";
    const modeBtns = MODE_OPTIONS.map((m) =>
      `<button class="mk-mode-btn ${m === modeCur ? "active" : ""}" data-mode="${m}" data-edit-ctrl${dis}>${m.toUpperCase()}</button>`
    ).join("");
    const manualRows = [
      ["Charge", "00:00", "06:00", "MON", "800", "open"],
      ["Discharge", "18:00", "22:00", "MON-FRI", "1200", "open"],
    ];
    return `
      <div class="mk-card mk-pad">
        <div class="mk-set-head">
          <div class="mk-title" style="font-size:18px">Work Mode</div>
          <button class="mk-edit-btn ${locked ? "is-locked" : ""}" data-mode-edit
            title="${locked ? "Unlock editing" : "Lock editing"}"
            aria-pressed="${locked}" aria-label="${locked ? "Unlock editing" : "Lock editing"}">${locked ? ICON_LOCK_CLOSED : ICON_LOCK_OPEN}</button>
        </div>
        <div class="mk-sub" data-lock-note style="margin:-10px 0 10px;display:${locked ? "block" : "none"}">Editing is locked — tap the padlock to unlock this page.</div>
        <div class="mk-row" style="margin-top:12px"><span class="k">Maximum Grid Power (W)</span><span class="v" style="color:#999">—</span></div>
        <div class="mk-row"><span class="k">Current Mode</span>
          <span class="v"><div class="mk-seg" data-mode-group>${modeBtns}</div></span></div>
        <div style="margin-top:16px">
          <div class="mk-sub" style="margin-bottom:8px">Manual time slots (read-only; edit via Marstek app / set_manual_schedule service)</div>
          <table class="mk-table">
            <thead><tr><th>Enabled</th><th>Power Status</th><th>Start</th><th>To</th><th>End</th><th>Days</th><th>Power(W)</th></tr></thead>
            <tbody>
              ${manualRows.map((r) => `<tr>
                <td><span class="mk-dot ${r[5] === "open" ? "on" : "off"}"></span></td>
                <td>${r[0]}</td><td>${r[1]}</td><td>→</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>
      <div class="mk-set-row">
        <div class="mk-set-col">
          <div class="mk-card mk-pad">
            <div class="mk-title" style="font-size:16px;margin-bottom:12px">Discharge Depth (DOD)</div>
            <div class="mk-row"><span class="k">Range 30 – 88 %</span><span class="v" data-dod-val>—</span></div>
            <input class="mk-range" type="range" min="30" max="88" step="1" value="60" data-dod data-edit-ctrl${dis}/>
            <div style="margin-top:10px"><button class="mk-btn primary" data-dod-set data-edit-ctrl${dis}>Set DOD</button></div>
            <div class="mk-sub" style="margin-top:8px">DOD is write-only until firmware reports it</div>
          </div>
        </div>
        <div class="mk-set-col">
          <div class="mk-card mk-pad">
            <div class="mk-title" style="font-size:16px;margin-bottom:12px">Device Controls</div>
            ${mkToggleRow("Front-panel LED", ledEid, "led")}
            ${mkToggleRow("Bluetooth Advertising", bleEid, "ble")}
            <div class="mk-row"><span class="k">Power Version</span>
              <select class="mk-sel" data-pv data-edit-ctrl${dis}>
                ${pvOpts.map((o) => `<option value="${esc(o)}" ${o === pvCur ? "selected" : ""}>${esc(o)} W</option>`).join("")}
              </select></div>
            <div class="mk-row" style="margin-top:12px"><span class="k">Device Restart</span>
              <button class="mk-icon-btn danger" data-restart data-edit-ctrl${dis} title="Restart device" aria-label="Restart device">${ICON_POWER}</button></div>
          </div>
          <div class="mk-card mk-pad">
            <div class="mk-set-head"><div class="mk-title" style="font-size:16px">WiFi Signal History</div></div>
            <div class="mk-legend"><span><span class="d" style="background:#0A84FF"></span>Strong</span>
              <span><span class="d" style="background:#3B7FF2"></span>Medium</span>
              <span><span class="d" style="background:#A9CFF5"></span>Weak</span></div>
            <div class="mk-row"><span class="k">Current RSSI</span><span class="v" data-wifi-rssi>${fmt(val(d, "wifi_rssi"), "dBm")}</span></div>
            <div class="mk-chart" data-chart="wifi" style="height:220px;margin-top:8px"></div>
            <div class="mk-sub" style="margin-top:8px">Charted from the local wifi_rssi sensor history (HA recorder). If empty, enable the WiFi signal strength entity.</div>
          </div>
        </div>
      </div>`;
  }
  const mkToggleRow = (label, eid, kind) => {
    if (!eid) return `<div class="mk-row"><span class="k">${label}</span><span class="v" style="color:#999">—</span></div>`;
    return `<div class="mk-row"><span class="k">${label}</span><div class="mk-toggle" data-tog="${kind}" data-edit-ctrl></div></div>`;
  };

  // --- Tab 4: errors -------------------------------------------------------
  function renderErrors() {
    return `<div class="mk-card mk-pad" style="margin-top:20px">
      <div class="mk-section-title" style="padding:0 0 16px">Real Time Fault List</div>
      <table class="mk-table">
        <thead><tr><th>NO</th><th>Error Code</th><th>Error Information</th><th>Solution</th><th>Time</th></tr></thead>
        <tbody><tr><td colspan="5"><div class="mk-ph">Error list comes from the Marstek cloud API (/device/get_device_error), which the local UDP integration does not expose. View faults in the Marstek mobile app.</div></td></tr></tbody>
      </table>
    </div>`;
  }

  // --- element -------------------------------------------------------------
  class MarstekPanel extends HTMLElement {
    constructor() {
      super();
      this._hass = null;
      this._tab = "information";
      this._deviceId = null;
      this._devices = [];
      this._statTab = "today";
      this._hsOpen = false; // header stats fold: hidden by default
      this._locked = false; // settings page edit guard (padlock)
      this._dodVal = 60;
      this._customStart = null; // YYYY-MM-DD, set by the range picker
      this._customEnd = null;
      this._fp = null; // flatpickr instance
    }
    set hass(hass) {
      this._hass = hass;
      this._devices = collectDevices(hass);
      if (!this._deviceId && this._devices.length) this._deviceId = this._devices[0].deviceId;
      // Skip full DOM re-renders once a chart container exists (battery on the
      // information tab, wifi on the settings tab). _render() replaces
      // innerHTML, which destroys the ECharts containers that async
      // renderCharts() is still populating (websocket history fetches yield
      // mid-flight). The stale call then renders on a detached node and the
      // visible div stays blank. Charts use historical data via websocket, not
      // live state, so only live text (header + current RSSI) is refreshed.
      if (this.querySelector("[data-chart='battery'], [data-chart='wifi']")) {
        const d = this._currentDevice();
        if (d) this._refreshHeader(d);
        return;
      }
      this._render();
    }
    set panel(p) { this._panel = p; }
    connectedCallback() { this._render(); }
    get root() { return this; }
    _currentDevice() { return this._devices.find((d) => d.deviceId === this._deviceId) || this._devices[0] || null; }
    // Update header values in-place without rebuilding DOM (preserves charts).
    _refreshHeader(d) {
      const soc = num(d, "battery_soc");
      const socPct = soc === null ? 0 : Math.max(0, Math.min(100, soc));
      const socTier = soc === null ? "na" : soc < 20 ? "low" : soc < 50 ? "mid" : "ok";
      // The tier is expressed as a data attribute so the CSS owns the colour.
      const socWrap = this.querySelector(".mk-header");
      if (socWrap) {
        socWrap.setAttribute("data-soc", socTier);
        socWrap.setAttribute("data-charge", val(d, "battery_status") === "charging" ? "on" : "off");
      }
      // Toggle classes instead of rebuilding: the CSS transitions animate the
      // change, while the entry reveal only runs on a full render.
      const seg = this.querySelector("[data-soc-seg]");
      if (seg) {
        const lit = Math.round(socPct / 10);
        Array.from(seg.children).forEach((el, i) => {
          el.classList.toggle("on", i < lit);
          el.classList.toggle("tip", i === lit - 1);
        });
      }
      const socVal = this.querySelector("[data-soc-val]");
      if (socVal) animateNum(socVal.querySelector(".mk-num"), soc === null ? null : { n: soc, decimals: 0, unit: "%" });
      const state = this.querySelector(".mk-state");
      if (state) {
        const online = val(d, "connected") === "on";
        state.className = "mk-state " + (online ? "on" : "off");
        state.textContent = online ? "Online" : "Offline";
      }
      const modeEl = this.querySelector("[data-meta-mode]");
      if (modeEl) modeEl.textContent = val(d, "device_mode") || "—";
      const wifiEl = this.querySelector("[data-meta-wifi]");
      if (wifiEl) wifiEl.textContent = val(d, "wifi_ssid") || "—";
      const rssi = this.querySelector("[data-wifi-rssi]");
      if (rssi) rssi.textContent = fmt(val(d, "wifi_rssi"), "dBm");
      // Signal chip beside the level figure: repaint bars without rebuilding.
      const sig = this.querySelector("[data-sig]");
      if (sig) {
        const info = sigInfo(val(d, "wifi_rssi"));
        sig.dataset.sig = String(info.bars);
        sig.dataset.lvl = info.lvl;
        sig.title = "WiFi " + fmt(val(d, "wifi_rssi"), "dBm");
        sig.querySelectorAll("[data-bar]").forEach((el, i) => el.classList.toggle("on", i < info.bars));
      }
    }
    // Paint the current _locked state onto the existing DOM. Deliberately not
    // a re-render: _render() replaces innerHTML, which would dispose the WiFi
    // chart instance mounted on this tab.
    _applyLock() {
      const wrap = this.querySelector(".mk-wrap");
      if (wrap) wrap.toggleAttribute("data-locked", this._locked);
      const btn = this.querySelector("[data-mode-edit]");
      if (btn) {
        btn.classList.toggle("is-locked", this._locked);
        btn.innerHTML = this._locked ? ICON_LOCK_CLOSED : ICON_LOCK_OPEN;
        const label = this._locked ? "Unlock editing" : "Lock editing";
        btn.title = label;
        btn.setAttribute("aria-label", label);
        btn.setAttribute("aria-pressed", String(this._locked));
      }
      const note = this.querySelector("[data-lock-note]");
      if (note) note.style.display = this._locked ? "block" : "none";
      this.querySelectorAll("[data-edit-ctrl]").forEach((el) => {
        if ("disabled" in el) el.disabled = this._locked;
      });
    }
    _render() {
      if (!this._hass) {
        this.innerHTML = `<style>${STYLE}</style><div class="mk-wrap"><div class="mk-ph">Loading…</div></div>`;
        return;
      }
      if (!this._devices.length) {
        this.innerHTML = `<style>${STYLE}</style><div class="mk-wrap">
          <div class="mk-title">Marstek</div>
          <div class="mk-ph" style="margin-top:16px">No Marstek devices configured. Add the integration first.</div></div>`;
        return;
      }
      const d = this._currentDevice();
      const tabs = TABS.map((t) =>
        `<div class="mk-tab ${t.key === this._tab ? "active" : ""}" data-tab="${t.key}">${t.icon}<span>${t.label}</span></div>`
      ).join("");
      // Custom device dropdown (native <select> options cannot embed images).
      // Only shown when more than one Marstek device is configured.
      const devSel = this._devices.length > 1
        ? (() => {
            const cur = d || this._devices[0];
            const curImg = deviceImage(cur) || "";
            const items = this._devices
              .map((x) => {
                const img = deviceImage(x) || "";
                return `<div class="mk-devsel-item ${x.deviceId === this._deviceId ? "active" : ""}" data-devsel-item="${esc(x.deviceId)}">
                  ${img ? `<img class="mk-devsel-img" data-devimg src="${esc(img)}" alt="">` : `<span class="mk-devsel-emoji">🔋</span>`}
                  <span class="mk-devsel-name">${esc(x.name)}</span>
                  <span class="mk-devsel-model">${esc(x.model || "")}</span>
                </div>`;
              })
              .join("");
            return `<div class="mk-devsel" data-devsel>
              <button class="mk-devsel-btn" type="button" data-devsel-btn>
                ${curImg ? `<img class="mk-devsel-img" data-devimg src="${esc(curImg)}" alt="">` : `<span class="mk-devsel-emoji">🔋</span>`}
                <span class="mk-devsel-name">${esc(cur.name)}</span>
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M8 10L4 6h8z" fill="#777"/></svg>
              </button>
              <div class="mk-devsel-menu" data-devsel-menu hidden>${items}</div>
            </div>`;
          })()
        : "";
      let body = "";
      if (!d) body = '<div class="mk-ph">No device selected</div>';
      else if (this._tab === "information") body = renderInformation(d, this._statTab, this);
      else if (this._tab === "settings") body = renderSettings(d, this);
      this.innerHTML = `<style>${STYLE}</style><nav class="mk-navbar">
        <img class="mk-navbar-logo" src="${asset("../brand/logo@2x.png")}" alt="Marstek">
        <div class="mk-nav-right">${devSel}<div class="mk-tabs">${tabs}</div></div>
      </nav><div class="mk-wrap"${this._locked ? " data-locked" : ""}>
        ${body}
      </div>`;
      this._wire();
      if (this._tab === "information" && d) {
        fillChargeDischarge(this, d).catch((e) => console.error("[marstek-panel] charge/discharge fill failed", e));
        const r = computeRange(this._statTab, this);
        renderCharts(this, d, r.start, r.end, this._statTab);
      }
      // Settings tab hosts the WiFi signal chart (last 24h of wifi_rssi).
      if (this._tab === "settings" && d) {
        const end = Date.now();
        renderCharts(this, d, end - 24 * 3600 * 1000, end);
      }
    }
    _wire() {
      this.querySelectorAll("[data-devimg]").forEach((img) =>
        img.addEventListener("error", () => {
          img.style.display = "none";
          const emoji = img.parentElement.querySelector(".mk-device-emoji");
          if (emoji) emoji.style.display = "flex";
        })
      );
      // Parameter rows open Home Assistant's built-in more-info dialog for the
      // sensor behind them. The event must be composed so it escapes this
      // element's shadow root and reaches the frontend's listener.
      this.querySelectorAll("[data-entity]").forEach((el) => {
        const open = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          this.dispatchEvent(new CustomEvent("hass-more-info", {
            detail: { entityId: el.dataset.entity },
            bubbles: true,
            composed: true,
          }));
        };
        el.addEventListener("click", open);
        el.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") open(ev);
        });
      });
      // Header stats fold: the handle slides the last three rows of both
      // parameter columns in and out without re-rendering the card.
      const hsBtn = this.querySelector("[data-hs-toggle]");
      const hsBox = this.querySelector(".mk-head-stats");
      if (hsBtn && hsBox) {
        hsBtn.addEventListener("click", () => {
          this._hsOpen = hsBox.dataset.hs !== "open";
          hsBox.dataset.hs = this._hsOpen ? "open" : "closed";
          hsBtn.setAttribute("aria-expanded", String(this._hsOpen));
          const label = hsBtn.querySelector("[data-hs-label]");
          if (label) label.textContent = this._hsOpen ? "Show less" : "Show more";
        });
      }
      this.querySelectorAll("[data-tab]").forEach((el) =>
        el.addEventListener("click", () => {
          this._tab = el.dataset.tab;
          if (this._fp) { this._fp.destroy(); this._fp = null; }
          if (window.echarts) {
            this.querySelectorAll("[data-chart]").forEach((c) => {
              const inst = window.echarts.getInstanceByDom(c);
              if (inst) inst.dispose();
            });
          }
          this._render();
        })
      );
      // Clean up a previous outside-click listener before (re)wiring so they
      // do not stack across renders.
      if (this._devselDocClick) {
        document.removeEventListener("click", this._devselDocClick);
        this._devselDocClick = null;
      }
      const devsel = this.querySelector("[data-devsel]");
      if (devsel) {
        const btn = devsel.querySelector("[data-devsel-btn]");
        const menu = devsel.querySelector("[data-devsel-menu]");
        const toggle = (open) => { menu.hidden = !open; };
        btn.addEventListener("click", (e) => { e.stopPropagation(); toggle(menu.hidden); });
        devsel.querySelectorAll("[data-devsel-item]").forEach((item) =>
          item.addEventListener("click", () => {
            this._deviceId = item.dataset.devselItem;
            this._render();
          })
        );
        const onDocClick = () => toggle(false);
        document.addEventListener("click", onDocClick);
        this._devselDocClick = onDocClick;
      }
      this.querySelectorAll("[data-stattab]").forEach((b) =>
        b.addEventListener("click", () => {
          this.querySelectorAll("[data-stattab]").forEach((x) => x.classList.remove("active"));
          b.classList.add("active");
          const tab = b.dataset.stattab;
          this._statTab = tab;
          const d = this._currentDevice();
          if (!d) return;
          const r = computeRange(tab, this);
          // Presets also sync the range picker so the two stay consistent.
          if (tab !== "custom") {
            this._customStart = fmtDate(r.start);
            this._customEnd = fmtDate(Math.min(r.end, Date.now()));
            if (this._fp) {
              // setDateRange may emit "selected" synchronously; suppress it so
              // the preset we just clicked stays highlighted.
              this._suppressPicker = true;
              this._fp.setDateRange(this._customStart, this._customEnd);
              setTimeout(() => { this._suppressPicker = false; }, 0);
            }
          }
          renderCharts(this, d, r.start, r.end, tab);
        })
      );
      // Range picker (Litepicker, two-input range). Completing a selection
      // applies the window immediately and switches the active stat tab to
      // "custom". The dual-month layout mirrors Element UI's date range picker.
      const startEl = this.querySelector('[data-range="start"]');
      const endEl = this.querySelector('[data-range="end"]');
      if (startEl && endEl) {
        loadLitepicker()
          .then((Litepicker) => {
            const r = computeRange(this._statTab, this);
            const isCustom = this._statTab === "custom" && this._customStart && this._customEnd;
            const startStr = isCustom ? this._customStart : fmtDate(r.start);
            const endStr = isCustom ? this._customEnd : fmtDate(Math.min(r.end, Date.now()));
            this._fp = new Litepicker({
              element: startEl,
              elementEnd: endEl,
              singleMode: false,
              numberOfMonths: 2,
              numberOfColumns: 2,
              format: "YYYY-MM-DD",
              maxDate: new Date(),
              startDate: startStr,
              endDate: endStr,
              autoApply: true,
              showTooltip: false,
              dropdowns: { years: true, months: true },
              setup: (picker) => {
                picker.on("selected", (d1, d2) => {
                  if (this._suppressPicker) return;
                  this._customStart = d1.format("YYYY-MM-DD");
                  this._customEnd = d2.format("YYYY-MM-DD");
                  this.querySelectorAll("[data-stattab]").forEach((x) => x.classList.remove("active"));
                  this._statTab = "custom";
                  const dev = this._currentDevice();
                  if (dev) {
                    const rr = computeRange("custom", this);
                    renderCharts(this, dev, rr.start, rr.end, "custom");
                  }
                });
              },
            });
          })
          .catch((err) => console.error("[marstek-panel] litepicker load failed", err));
      }
      // Padlock beside the Work Mode title freezes every editing control on
      // this page. Toggled in place (no re-render) so the WiFi chart survives.
      const lockBtn = this.querySelector("[data-mode-edit]");
      if (lockBtn) lockBtn.addEventListener("click", () => {
        this._locked = !this._locked;
        this._applyLock();
      });
      this.querySelectorAll("[data-mode-group] .mk-mode-btn").forEach((b) =>
        b.addEventListener("click", () => {
          if (this._locked) return;
          const d = this._currentDevice();
          const eid = d && d.entityIds.device_mode;
          if (!eid) return;
          runWithToast("Set mode", () => this._hass.callService("select", "select_option", { entity_id: eid, option: b.dataset.mode }));
        })
      );
      const dodEl = this.querySelector("[data-dod]");
      const dodVal = this.querySelector("[data-dod-val]");
      if (dodEl) {
        dodEl.value = this._dodVal;
        if (dodVal) dodVal.textContent = this._dodVal + " %";
        dodEl.addEventListener("input", () => {
          if (this._locked) return;
          this._dodVal = Number(dodEl.value);
          if (dodVal) dodVal.textContent = this._dodVal + " %";
        });
      }
      const dodSet = this.querySelector("[data-dod-set]");
      if (dodSet) dodSet.addEventListener("click", () => {
        if (this._locked) return;
        const d = this._currentDevice();
        const eid = d && d.entityIds.dod;
        if (!eid) return;
        runWithToast("Set DOD", () => this._hass.callService("number", "set_value", { entity_id: eid, value: this._dodVal }));
      });
      const restartBtn = this.querySelector("[data-restart]");
      if (restartBtn) restartBtn.addEventListener("click", async () => {
        if (this._locked) return;
        const ok = await confirmDialog(
          "Restart Device",
          "Are you sure you want to restart the device? Operation will be briefly interrupted."
        );
        if (!ok) return;
        // Restart is MQTT cd=16 — not available from the local UDP API. Placeholder only.
        toast("info", "Restart requires MQTT (cd=16) — local API not supported yet");
      });
      const pvEl = this.querySelector("[data-pv]");
      if (pvEl) pvEl.addEventListener("change", () => {
        if (this._locked) return;
        const d = this._currentDevice();
        const eid = d && d.entityIds.power_version;
        if (!eid) return;
        runWithToast("Set power version", () => this._hass.callService("select", "select_option", { entity_id: eid, option: pvEl.value }));
      });
      this.querySelectorAll("[data-tog]").forEach((t) =>
        t.addEventListener("click", async () => {
          if (this._locked) return;
          const d = this._currentDevice();
          const kind = t.dataset.tog;
          const eid = d && (kind === "led" ? d.entityIds.led : d.entityIds.bluetooth_advertising);
          if (!eid) return;
          const on = !t.classList.contains("on");
          t.classList.toggle("on", on);
          const label = kind === "led" ? "Toggle LED" : "Toggle Bluetooth";
          runWithToast(label, () =>
            this._hass.callService("switch", on ? "turn_on" : "turn_off", { entity_id: eid }).catch((e) => {
              t.classList.toggle("on", !on); // roll back the optimistic toggle
              throw e;
            })
          );
        })
      );
    }
  }
  customElements.define("marstek-panel", MarstekPanel);
})();
