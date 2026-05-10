"use strict";

/*
  MHR Dashboard JS - clean full rewrite
  - No patch blocks
  - No duplicate let/const declarations
  - Safe with missing HTML nodes
  - Dashboard stats/logs/modes/toggles/downloader/suggestions/script optimizer
*/

const $ = (id) => document.getElementById(id);

/* ----------------------------- State ----------------------------- */

let currentLang = normalizeLang(localStorage.getItem("mhr.lang") || "en");

let lastStatsSnapshot = null;
let lastLogs = {
  live: [],
  errors: [],
  full_errors: [],
  script_errors: [],
  downloads: [],
  video: [],
  sabr: [],
  h2: []
};

let lastRenderedLogs = [];
let logsPaused = false;
let prev = null;
let peaks = { rx: 0, tx: 0, client: 0 };
let speedHistory = { rx: [], tx: [], client: [] };

let activeTab = localStorage.getItem("mhr.tab") || "overview";
let activeLog = localStorage.getItem("mhr.logtab") || "live";
if (activeLog === "errors") activeLog = "full_errors";
let activeCfgTab = localStorage.getItem("mhr.cfgtab") || "network";
let viewMode = localStorage.getItem("mhr.viewMode") || "advanced";

let advisorLastFetch = 0;
let advisorFetchInFlight = false;

let optimizerState = null;
let optimizerIds = [];
let optimizerSelectedTier = 1;
let optimizerLoaded = false;

/* ----------------------------- I18N ----------------------------- */

const BASIC_FA = {
  "Mode": "حالت",
  "Auto": "خودکار",
  "Basic": "پایه",
  "Medium": "متوسط",
  "Ultra": "اولترا",
  "Download": "دانلود",
  "ON": "روشن",
  "OFF": "خاموش",
  "YES": "بله",
  "NO": "خیر",
  "ONLINE": "آنلاین",
  "OFFLINE": "آفلاین",
  "HEALTHY": "سالم",
  "BAD": "خراب",
  "Warning": "هشدار",
  "Critical": "بحرانی",
  "Analyzing": "در حال بررسی",
  "Errors": "خطاها",
  "Downloads": "دانلودها",
  "Video": "ویدیو",
  "SABR": "SABR",
  "Telegram": "تلگرام",
  "Turbo": "توربو",
  "Video Prefetch": "پیش‌بارگذاری ویدیو",
  "Manifest Prefetch": "پیش‌بارگذاری Manifest",
  "Video Passthrough": "عبور مستقیم ویدیو",
  "Auto Tune": "تنظیم خودکار",
  "Safe Mode": "حالت امن",
  "H2 Healer": "هیلر H2",
  "Script Optimizer": "بهینه‌ساز اسکریپت",
  "Optimizer ready": "بهینه‌ساز آماده است",
  "Optimizer load failed": "لود بهینه‌ساز شکست خورد",
  "No Script IDs configured": "هیچ Script ID ثبت نشده است",
  "Add at least one Apps Script deployment ID.": "حداقل یک Apps Script Deployment ID اضافه کن.",
  "Config changed": "کانفیگ تغییر کرده",
  "Optimizer needs refresh": "بهینه‌ساز نیاز به بروزرسانی دارد",
  "Applied": "اعمال شده",
  "Not applied": "اعمال نشده",
  "Not ready": "آماده نیست",
  "Active": "فعال",
  "Available": "قابل انتخاب",
  "Selected": "انتخاب‌شده",
  "Current": "فعال",
  "Locked": "قفل",
  "Remove": "حذف",
  "Saved.": "ذخیره شد.",
  "At least one Script ID is required.": "حداقل یک Script ID لازم است.",
  "This tier is locked for your current Script ID count.": "این Tier برای تعداد فعلی Script ID قفل است.",
  "Apply optimization now? MHR will save config, reset quota_state.json, and restart.": "بهینه‌سازی اعمال شود؟ کانفیگ ذخیره می‌شود، quota_state ریست می‌شود و MHR ری‌استارت می‌شود.",
  "Optimization applied. MHR is restarting.": "بهینه‌سازی اعمال شد. MHR ری‌استارت می‌شود.",
  "Enter a Script ID first.": "اول یک Script ID وارد کن.",
  "No logs": "لاگی نیست",
  "No errors": "خطایی نیست",
  "No download logs": "لاگ دانلودی نیست",
  "No video/SABR logs": "لاگ ویدیو/SABR نیست",
  "Disconnected": "قطع شده",
  "Live": "زنده",
  "Pause Live": "توقف لاگ زنده",
  "Resume Live": "ادامه لاگ زنده",
  "Advisor offline": "Advisor آفلاین است",
  "No action needed": "اقدامی لازم نیست",
  "Attention recommended": "نیاز به توجه",
  "No data": "داده‌ای نیست",
  "Sensitive values are intentionally hidden by the dashboard API.": "مقادیر حساس عمداً توسط API داشبورد مخفی شده‌اند."
};

function normalizeLang(lang) {
  lang = String(lang || "en").toLowerCase();
  return lang.startsWith("fa") ? "fa" : "en";
}

function getLang() {
  return currentLang;
}

function isFa() {
  return currentLang === "fa";
}

function t(key, fallback) {
  const pack = (window.MHR_I18N && window.MHR_I18N[currentLang]) || {};
  const en = (window.MHR_I18N && window.MHR_I18N.en) || {};
  if (pack[key] !== undefined) return pack[key];
  if (en[key] !== undefined) return en[key];

  const fb = fallback !== undefined ? fallback : key;
  if (currentLang === "fa" && BASIC_FA[fb]) return BASIC_FA[fb];
  if (currentLang === "fa" && BASIC_FA[key]) return BASIC_FA[key];
  return fb;
}

function tx(value) {
  if (value === null || value === undefined) return value;
  let s = String(value);
  if (currentLang !== "fa") return s;

  if (BASIC_FA[s]) return BASIC_FA[s];

  s = s.replace(/\bON\b/g, BASIC_FA.ON);
  s = s.replace(/\bOFF\b/g, BASIC_FA.OFF);
  s = s.replace(/\bYES\b/g, BASIC_FA.YES);
  s = s.replace(/\bNO\b/g, BASIC_FA.NO);
  s = s.replace(/\bONLINE\b/g, BASIC_FA.ONLINE);
  s = s.replace(/\bOFFLINE\b/g, BASIC_FA.OFFLINE);
  s = s.replace(/\bHEALTHY\b/g, BASIC_FA.HEALTHY);
  s = s.replace(/\bBAD\b/g, BASIC_FA.BAD);

  s = s.replace(/Mode:/g, BASIC_FA.Mode + ":");
  s = s.replace(/Errors:/g, BASIC_FA.Errors + ":");
  s = s.replace(/Turbo:/g, BASIC_FA.Turbo + ":");
  s = s.replace(/Telegram:/g, BASIC_FA.Telegram + ":");
  s = s.replace(/Video Prefetch/g, BASIC_FA["Video Prefetch"]);
  s = s.replace(/Manifest Prefetch/g, BASIC_FA["Manifest Prefetch"]);
  s = s.replace(/Video Passthrough/g, BASIC_FA["Video Passthrough"]);
  s = s.replace(/Auto Tune/g, BASIC_FA["Auto Tune"]);
  s = s.replace(/H2 Healer/g, BASIC_FA["H2 Healer"]);
  s = s.replace(/No logs/g, BASIC_FA["No logs"]);
  s = s.replace(/No errors/g, BASIC_FA["No errors"]);

  return s;
}

function applyLang() {
  currentLang = normalizeLang(localStorage.getItem("mhr.lang") || currentLang || "en");
  const rtl = currentLang === "fa";

  document.documentElement.lang = currentLang;
  document.documentElement.dir = rtl ? "rtl" : "ltr";

  if (document.body) {
    document.body.dir = rtl ? "rtl" : "ltr";
    document.body.classList.toggle("rtl", rtl);
    document.body.classList.toggle("ltr", !rtl);
  }

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key) el.textContent = t(key, el.textContent);
  });

  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (key) el.title = t(key, el.title);
  });

  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) el.setAttribute("placeholder", t(key, el.getAttribute("placeholder")));
  });

  const langBtn = $("langBtn");
  if (langBtn) {
    langBtn.textContent = rtl ? "EN" : "FA";
    langBtn.title = rtl ? "English" : "فارسی";
  }
}

function setLang(lang) {
  currentLang = normalizeLang(lang);
  localStorage.setItem("mhr.lang", currentLang);
  applyLang();

  if (lastStatsSnapshot) render(lastStatsSnapshot);
  if (activeTab === "logs") renderLogs();
  if (activeTab === "optimizer") renderOptimizer();
}

function toggleLang() {
  setLang(currentLang === "fa" ? "en" : "fa");
}

/* ----------------------------- Helpers ----------------------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  }[m]));
}

function setText(id, value) {
  const el = $(id);
  if (!el) return;

  const rawIds = {
    rawStats: true,
    featureRaw: true,
    downloadRecentLogs: true,
    videoRecentLogs: true,
    recentErrors: true
  };

  if (rawIds[id]) el.textContent = String(value ?? "-");
  else el.textContent = String(tx(value) ?? "-");
}

function setHTML(id, value) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = value || "";
}

function setClass(id, cls) {
  const el = $(id);
  if (!el) return;
  el.className = cls;
}

function fmtBytes(n) {
  n = Number(n || 0);
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return n.toFixed(1) + " " + u[i];
}

function fmtMbps(bytesPerSec) {
  const mbps = (Number(bytesPerSec || 0) * 8) / 1000 / 1000;
  return (mbps < 1 ? mbps.toFixed(2) : mbps.toFixed(1)) + " Mbps";
}

function fmtTime(s) {
  s = Number(s || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = Math.floor(s % 60);
  return [h, m, x].map((v) => String(v).padStart(2, "0")).join(":");
}

function fmtMaybeBytes(v) {
  if (v === "-" || v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  return fmtBytes(n);
}

function fmtMaybeSeconds(v) {
  if (v === "-" || v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  return n + "s";
}

function fmtMaybeMs(v) {
  if (v === "-" || v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  return n + " ms";
}

function yn(v) {
  return v ? t("common.on", "ON") : t("common.off", "OFF");
}

function yesNo(v) {
  return v ? t("common.yes", "YES") : t("common.no", "NO");
}

function prettyMode(m) {
  m = String(m || "-").toLowerCase();
  const names = {
    auto: t("mode.auto", "Auto"),
    basic: t("mode.basic", "Basic"),
    medium: t("mode.medium", "Medium"),
    ultra: t("mode.ultra", "Ultra"),
    download: t("mode.download", "Download")
  };
  return names[m] || (m.charAt(0).toUpperCase() + m.slice(1));
}

function listSummary(v, maxItems = 8) {
  if (Array.isArray(v)) {
    if (!v.length) return "-";
    const shown = v.slice(0, maxItems).map((x) => String(x)).join(", ");
    return v.length > maxItems ? shown + " ... +" + (v.length - maxItems) : shown;
  }

  if (v && typeof v === "object") {
    const keys = Object.keys(v);
    if (!keys.length) return "-";
    const shown = keys.slice(0, maxItems).join(", ");
    return keys.length > maxItems ? shown + " ... +" + (keys.length - maxItems) : shown;
  }

  if (v === null || v === undefined || v === "") return "-";
  return String(v);
}

function cfgGet(c, key, fallback = "-") {
  if (!c || !(key in c) || c[key] === null || c[key] === undefined || c[key] === "") return fallback;
  return c[key];
}

function badgeClass(state) {
  if (state === "good") return "badge good";
  if (state === "bad") return "badge bad";
  if (state === "warn") return "badge warn";
  if (state === "info") return "badge info";
  if (state === "purple") return "badge purple";
  return "badge";
}

function setBadge(id, text, state) {
  const el = $(id);
  if (!el) return;
  el.className = badgeClass(state);
  el.textContent = tx(text);
}

function setFeatureBadge(id, label, on) {
  const el = $(id);
  if (!el) return;
  el.className = "badge featureBadge " + (on ? "on" : "off");
  el.textContent = tx(label) + ": " + (on ? t("common.on", "ON") : t("common.off", "OFF"));
}

function kv(id, obj, left = false) {
  const el = $(id);
  if (!el) return;
  el.className = "kv" + (left ? " left" : "");
  el.innerHTML = Object.entries(obj || {}).map(([k, v]) => {
    return `<div class="k">${esc(tx(k))}</div><div class="v">${esc(tx(v))}</div>`;
  }).join("");
}

function pill(name, on, kind) {
  const cls = kind || (on ? "good" : "bad");
  const val = on === true ? t("common.on", "ON") : on === false ? t("common.off", "OFF") : tx(on);
  return `<span class="pill ${esc(cls)}">${esc(tx(name))}: ${esc(val)}</span>`;
}

function tile(name, state, detail, kind) {
  return `<div class="tile ${esc(kind || "")}">
    <div class="name">${esc(tx(name))}</div>
    <div class="state">${esc(tx(state))}</div>
    <div class="meta">${esc(tx(detail || ""))}</div>
  </div>`;
}

function healthBox(name, state, detail, kind) {
  return `<div class="healthBox ${esc(kind || "")}">
    <div class="name">${esc(tx(name))}</div>
    <div class="state">${esc(tx(state))}</div>
    <div class="meta">${esc(tx(detail || ""))}</div>
  </div>`;
}

function configBox(name, value, kind) {
  return `<div class="configBox ${esc(kind || "")}">
    <div class="name">${esc(tx(name))}</div>
    <div class="state">${esc(tx(value))}</div>
  </div>`;
}

function table(headers, rows) {
  if (!rows || !rows.length) return `<div class="empty">${esc(t("label.noData", "No data"))}</div>`;
  return `<div class="tableWrap"><table><thead><tr>${
    headers.map((h) => `<th>${esc(tx(h))}</th>`).join("")
  }</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function raw(id, obj) {
  setText(id, typeof obj === "string" ? obj : JSON.stringify(obj, null, 2));
}

function pick(obj, keys) {
  const out = {};
  keys.forEach((k) => {
    if (obj && k in obj) out[k] = obj[k];
  });
  return out;
}

function smoothSpeed(name, value) {
  if (!isFinite(value) || value < 0) return 0;
  const h = speedHistory[name] || (speedHistory[name] = []);
  h.push(value);
  if (h.length > 5) h.shift();
  return h.reduce((a, b) => a + b, 0) / h.length;
}

function statusStateFromH2(text) {
  const v = String(text || "").toLowerCase();
  if (v.includes("off")) return "bad";
  if (v.includes("cooldown") || v.includes("starting")) return "warn";
  if (v.includes("on")) return "good";
  return "info";
}

function errorState(rate) {
  rate = Number(rate || 0);
  if (rate >= 8) return "bad";
  if (rate >= 2) return "warn";
  return "good";
}

/* ----------------------------- Navigation ----------------------------- */

function setTab(name) {
  activeTab = name || "overview";
  localStorage.setItem("mhr.tab", activeTab);

  document.querySelectorAll(".tabPage").forEach((x) => {
    x.classList.toggle("active", x.id === "tab_" + activeTab);
  });

  document.querySelectorAll(".tabBtn").forEach((x) => {
    x.classList.toggle("active", x.dataset.tab === activeTab);
  });

  if (activeTab === "logs") renderLogs();
  if (activeTab === "optimizer") loadScriptOptimizer();
}

function setLogTab(name) {
  activeLog = name || "live";
  localStorage.setItem("mhr.logtab", activeLog);
  renderLogs();
}

function setCfgTab(name) {
  activeCfgTab = name || "network";
  localStorage.setItem("mhr.cfgtab", activeCfgTab);
  renderAdvancedConfig(lastStatsSnapshot || {});
}

function setViewMode(mode) {
  viewMode = mode === "simple" ? "simple" : "advanced";
  localStorage.setItem("mhr.viewMode", viewMode);

  if (document.body) {
    document.body.classList.toggle("compact", viewMode === "simple");
  }

  $("view_simple")?.classList.toggle("active", viewMode === "simple");
  $("view_advanced")?.classList.toggle("active", viewMode !== "simple");
}

function toggleSettings() {
  $("settingsDrawer")?.classList.toggle("open");
}

/* ----------------------------- API Actions ----------------------------- */

async function post(url, data) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data || {})
  });
  return r.json();
}

async function setMode(mode) {
  const j = await post("/mode", { mode });
  if (!j.ok) alert(j.error || "failed");
  refresh();
}

function mhrSuggestionsUrl() {
  const lang = normalizeLang(getLang ? getLang() : localStorage.getItem("mhr.lang") || "en");
  return "/suggestions?lang=" + encodeURIComponent(lang);
}

function openSuggestions() {
  window.open(mhrSuggestionsUrl(), "_blank", "noopener,noreferrer");
}

async function toggleFeature(name) {
  const j = await post("/toggle", { name });
  if (!j.ok) {
    alert(j.error || "failed");
    return;
  }
  await refresh();
  if (name === "disable_all") {
    try { await refreshAdvisor(true); } catch (e) {}
  }
}

async function clearLog(name) {
  const j = await post("/clear-log", { name });
  if (!j.ok) alert(j.error || "failed");
  await refresh();
}

function clearActiveLog() {
  return clearLog(activeLog === "errors" ? "full_errors" : activeLog);
}

function copyActiveLog() {
  const lines = lastRenderedLogs.map((x) => x.raw || x.text || String(x)).join("\n");
  try {
    navigator.clipboard.writeText(lines);
  } catch (e) {
    alert(lines);
  }
}

function togglePauseLogs() {
  logsPaused = !logsPaused;
  setText("pauseLogsBtn", logsPaused ? t("btn.resumeLive", "Resume Live") : t("btn.pauseLive", "Pause Live"));
}

/* ----------------------------- Logs ----------------------------- */

function parseLogLine(line, fallbackType) {
  const rawLine = String(line ?? "");
  const m = rawLine.match(/^(\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([^:]+):\s*([\s\S]*)$/);

  let time = "-";
  let level = "-";
  let source = fallbackType || "-";
  let text = rawLine;

  if (m) {
    time = m[1];
    level = m[2];
    source = m[3];
    text = m[4];
  } else {
    const m2 = rawLine.match(/^(\d{2}:\d{2}:\d{2})\s+([\s\S]*)$/);
    if (m2) {
      time = m2[1];
      text = m2[2];
    }
  }

  const low = rawLine.toLowerCase();
  let kind = "";

  if (low.includes("runtime config applied")) {
    return { raw: rawLine, time, level, source, text, kind: "" };
  }

  if (
    low.includes("error") ||
    low.includes("failed") ||
    low.includes("failure") ||
    low.includes("relay error") ||
    low.includes("relay failure") ||
    low.includes("timeout") ||
    low.includes("timeouterror") ||
    low.includes("connectionerror") ||
    low.includes("temporarily disabled") ||
    low.includes("front-ip signal") ||
    low.includes("hint=error") ||
    low.includes("status=403") ||
    low.includes("status=429") ||
    low.includes("status=500") ||
    low.includes("status=502") ||
    low.includes("status=503") ||
    low.includes("status=504")
  ) kind = "error";
  else if (low.includes("warn") || low.includes("limit")) kind = "warn";
  else if (fallbackType === "script_errors") kind = "error";
  else if (fallbackType === "full_errors") kind = "error";
  else if (fallbackType === "script_errors" || fallbackType === "full_errors" || fallbackType === "errors") kind = "error";
  else if (fallbackType === "full_errors" || fallbackType === "script_errors" || fallbackType === "h2") kind = "warn";
  else if (fallbackType === "video" || fallbackType === "sabr") kind = "video";
  else if (fallbackType === "downloads") kind = "download";

  return { raw: rawLine, time, level, source, text, kind };
}

function filterLines(lines, q) {
  q = String(q || "").toLowerCase();
  return q ? lines.filter((x) => String(x).toLowerCase().includes(q)) : lines;
}


function formatLogTabName(name) {
  const map = {
    live: "All / Live",
    errors: "Errors",
    script_errors: "Script Errors",
    full_errors: "Full Errors",
    downloads: "Downloads",
    video: "Video",
    sabr: "SABR",
    h2: "H2"
  };
  return map[name] || name;
}

function renderLogs() {
  const tabs = ["live", "full_errors", "script_errors", "downloads", "video", "sabr", "h2"];

  tabs.forEach((name) => {
    $("logtab_" + name)?.classList.toggle("active", name === activeLog);
    setText("count_" + name, (lastLogs[name] || []).length ? `(${(lastLogs[name] || []).length})` : "");
  });

  setText("activeLogLabel", tx(formatLogTabName(activeLog)));

  const q = $("logSearch")?.value || "";
  const lines = filterLines(lastLogs[activeLog] || [], q).slice(-220);
  const parsed = lines.map((x) => parseLogLine(x, activeLog));
  lastRenderedLogs = parsed;

  setText("logCountPill", parsed.length + " lines");

  const box = $("logOutput");
  if (!box) return;

  const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 90;

  box.innerHTML = parsed.map((x) => {
    return `<div class="logline ${esc(x.kind)}">
      <div class="time">${esc(x.time)}</div>
      <div class="level">${esc(x.level)}</div>
      <div class="source">${esc(x.source)}</div>
      <div>${esc(tx(x.text))}</div>
    </div>`;
  }).join("") || `<div class="logline"><div>-</div><div>-</div><div>-</div><div>${esc(t("log.noLogs", "No logs"))}</div></div>`;

  if (nearBottom && !logsPaused) box.scrollTop = box.scrollHeight;
}

/* ----------------------------- Top UI / Toggles ----------------------------- */

function setActiveMode(m) {
  m = String(m || "-").toLowerCase();

  ["auto", "basic", "medium", "ultra", "download"].forEach((x) => {
    $("mode_" + x)?.classList.toggle("active", x === m);
  });

  setBadge("topMode", t("common.mode", "Mode") + ": " + prettyMode(m), "purple");
  setBadge("modeMiniBadge", prettyMode(m), "purple");
}

function isSafeModeActive(c) {
  if (!c) return false;

  if (c.safe_mode_active === true) return true;
  if (c.safe_mode_active === false) return false;

  return !!(
    c.turbo_disabled_by_dashboard &&
    c.sabr_disabled_by_dashboard &&
    c.video_prefetch_disabled_by_dashboard &&
    c.manifest_prefetch_disabled_by_dashboard &&
    c.video_passthrough_disabled_by_dashboard &&
    c.h2_disabled_by_dashboard &&
    c.h2_healer_disabled_by_dashboard
  );
}

function updateSafeModeButtons(c) {
  const on = isSafeModeActive(c);
  const label = on ? "Safe Mode: ON" : "Safe Mode: OFF";

  ["safeModeBtn", "settingsSafeModeBtn", "actionSafeModeBtn", "featuresSafeModeBtn"].forEach((id) => {
    const el = $(id);
    if (!el) return;

    if (id === "settingsSafeModeBtn") el.className = on ? "safeToggleBtn on" : "safeToggleBtn off";
    else el.className = on ? "bad safeModeCompact on" : "warn safeModeCompact off";

    el.textContent = tx(label);
  });
}

function updateToggleButtons(c) {
  const map = [
    ["tgBtn", !!c.telegram_mode_enabled, "Telegram"],
    ["turboBtn", !!c.turbo_mode_enabled, "Turbo"],
    ["sabrBtn", !!c.youtube_sabr_booster_enabled, "SABR"],
    ["vpBtn", !!c.video_prefetch_enabled, "Video Prefetch"],
    ["mpBtn", !!c.manifest_prefetch_enabled, "Manifest Prefetch"],
    ["vpassBtn", !!c.video_passthrough_enabled, "Video Passthrough"]
  ];

  map.forEach(([id, on, label]) => {
    const el = $(id);
    if (!el) return;
    el.className = on ? "good" : "bad";
    el.textContent = tx(label) + ": " + (on ? t("common.on", "ON") : t("common.off", "OFF"));
  });

  const h2On = !!c.h2_dashboard_on;
  const h2 = $("h2Btn");
  if (h2) {
    h2.className = h2On ? "good" : "bad";
    h2.textContent = h2On ? tx("H2: ON") : tx("H2: OFF");
  }

  const h2HealerOn = !!c.h2_healer_enabled;
  const h2Healer = $("h2HealerBtn");
  if (h2Healer) {
    h2Healer.className = h2HealerOn ? "good" : "bad";
    h2Healer.textContent = tx("H2 Healer") + ": " + (h2HealerOn ? t("common.on", "ON") : t("common.off", "OFF"));
  }

  updateSafeModeButtons(c);
}

/* ----------------------------- Advisor ----------------------------- */

function advisorLevelState(level) {
  level = String(level || "analyzing").toLowerCase();
  if (level === "critical") return "bad";
  if (level === "warning") return "warn";
  if (level === "healthy") return "good";
  return "info";
}

function advisorLevelLabel(level) {
  level = String(level || "analyzing").toLowerCase();
  if (level === "critical") return "Critical";
  if (level === "warning") return "Warning";
  if (level === "healthy") return "Healthy";
  return "Analyzing";
}

function renderAdvisor(data) {
  data = data || {};

  const rawLevel = String(data.health_level || "analyzing").toLowerCase();
  const state = advisorLevelState(rawLevel);
  const label = advisorLevelLabel(rawLevel);
  const changes = Array.isArray(data.changes) ? data.changes.length : 0;
  const sample = data.request_sample ?? "-";
  const next = data.next_refresh_in ?? 60;
  const safeMode = !!data.safe_mode_active;

  let hint = data.note || "Analyzing traffic data...";

  if (changes > 0) {
    hint = changes + " recommended change" + (changes === 1 ? "" : "s") + " available. Review Suggestions before applying.";
  } else if (rawLevel === "healthy") {
    hint = safeMode
      ? "Safe Mode is active. Keep browsing normally so MHR can confirm stability."
      : "No action needed right now. Keep browsing normally.";
  } else if (rawLevel === "warning") {
    hint = "Some issues were detected. Open Suggestions or Diagnostics.";
  } else if (rawLevel === "critical") {
    hint = "Critical issues detected. Review Suggestions or apply Safe Mode.";
  }

  setBadge("advisorBadge", "Advisor: " + label, state);
  setText("actionTitle", label === "Healthy" ? "No action needed" : "Attention recommended");
  setText("actionHint", hint + " · Sample: " + sample + " requests · Auto checks every " + next + "s");
}

async function refreshAdvisor(force = false) {
  const now = Date.now();
  if (advisorFetchInFlight) return;
  if (!force && now - advisorLastFetch < 60000) return;

  advisorLastFetch = now;
  advisorFetchInFlight = true;

  try {
    const r = await fetch("/api/suggestions", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    renderAdvisor(await r.json());
  } catch (e) {
    setBadge("advisorBadge", "Advisor: Offline", "warn");
    setText("actionTitle", "Advisor offline");
    setText("actionHint", "Suggestions data is not available right now: " + String(e));
  } finally {
    advisorFetchInFlight = false;
  }
}

/* ----------------------------- Front Health ----------------------------- */

function frontStateFromStats(c) {
  const recent = Number(c.front_ip_recent_timeouts || 0);
  const threshold = Number(c.front_ip_diagnostic_threshold || c.front_ip_diagnostic_timeout_threshold || 0);
  const h2Live = Number(c.h2_live_connections || 0);
  const h2Total = Number(c.h2_connections || 0);
  const cooldown = Number(c.h2_disabled_until || 0);

  if (threshold > 0 && recent >= threshold) return ["BAD", "bad"];
  if (cooldown > 0) return ["H2 COOLDOWN", "warn"];
  if (recent > 0) return ["TIMEOUT SIGNAL", "warn"];
  if (h2Total > 0 && h2Live <= 0) return ["H2 STARTING", "warn"];
  return ["HEALTHY", "good"];
}

function renderFrontHealth(s) {
  s = s || {};
  const c = s.config || {};
  const pair = frontStateFromStats(c);

  setBadge("frontHealthBadge", pair[0], pair[1]);

  const recent = Number(c.front_ip_recent_timeouts || 0);
  const threshold = c.front_ip_diagnostic_threshold || c.front_ip_diagnostic_timeout_threshold || "-";
  const h2Live = c.h2_live_connections ?? "-";
  const h2Total = c.h2_connections ?? "-";
  const cooldown = Number(c.h2_disabled_until || 0);

  setHTML("frontHealthGrid", [
    healthBox("Front IP", c.front_ip || c.front_connect_host || c.google_ip || "-", "Connect host", pair[1] === "bad" ? "bad" : "info"),
    healthBox("Main SNI", c.front_sni_host || c.front_domain || "-", "TLS SNI host", "info"),
    healthBox("Recent Timeouts", recent + " / " + threshold, "Diagnostic threshold", recent > 0 ? (pair[1] === "bad" ? "bad" : "warn") : "good"),
    healthBox("H2 Live", h2Live + " / " + h2Total, "Live / configured", Number(h2Live) > 0 ? "good" : "warn"),
    healthBox("H2 Cooldown", cooldown > 0 ? cooldown + "s" : "0s", "Remaining cooldown", cooldown > 0 ? "warn" : "good"),
    healthBox("Relay Timeout", (c.relay_timeout ?? "-") + "s", "Main relay", ""),
    healthBox("Range Probe", (c.range_probe_timeout ?? "-") + "s", "Video range probe", ""),
    healthBox("H2 Stream", (c.h2_stream_timeout ?? "-") + "s", "H2 stream", "")
  ].join(""));

  kv("frontHealthStats", {
    "HTTP Host": c.front_http_host || "script.google.com",
    "H2 Connect Timeout": (c.h2_connect_timeout ?? "-") + "s",
    "TLS Connect Timeout": (c.tls_connect_timeout ?? "-") + "s",
    "TCP Connect Timeout": (c.tcp_connect_timeout ?? "-") + "s",
    "Diagnostic Window": (c.front_ip_diagnostic_window_seconds ?? "-") + "s",
    "Timeout Threshold": threshold,
    "Warning Cooldown": (c.front_ip_diagnostic_warning_cooldown ?? "-") + "s",
    "Disabled Scripts": c.blacklisted_scripts ?? c.disabled_scripts_count ?? "-",
    "H2 Available": c.h2_available ? "YES" : "NO",
    "H2 Rebuilding": c.h2_rebuilding ? "YES" : "NO"
  }, true);

  setText("sniRotationList", "SNI pool: " + listSummary(c.sni_rotation || c.front_sni_rotation || c.front_domains || [], 20));
}

/* ----------------------------- Advanced Config ----------------------------- */

function renderAdvancedConfig(s) {
  s = s || {};
  const c = s.config || {};

  ["network", "routing", "timeouts", "downloads", "video", "turbo", "telegram", "downloader", "security", "all"].forEach((name) => {
    $("cfgtab_" + name)?.classList.toggle("active", name === activeCfgTab);
  });

  let rows = [];
  let note = "";

  if (activeCfgTab === "network") {
    rows = [
      ["Runtime Mode", prettyMode(cfgGet(c, "runtime_mode", s.web_mode || "-")), "info"],
      ["Profile Label", cfgGet(c, "label", "-"), "info"],
      ["Mode", cfgGet(c, "mode", "apps_script")],
      ["Listen", cfgGet(c, "listen_host", "127.0.0.1") + ":" + cfgGet(c, "listen_port", "-")],
      ["SOCKS5", yn(!!cfgGet(c, "socks5_enabled", false)) + " / Port " + cfgGet(c, "socks5_port", "-"), !!cfgGet(c, "socks5_enabled", false) ? "good" : "bad"],
      ["LAN Sharing", yn(!!cfgGet(c, "lan_sharing", false)), !!cfgGet(c, "lan_sharing", false) ? "warn" : ""],
      ["Verify SSL", yn(!!cfgGet(c, "verify_ssl", true)), !!cfgGet(c, "verify_ssl", true) ? "good" : "warn"],
      ["Google IP", cfgGet(c, "google_ip", "-")],
      ["Front Domains", listSummary(cfgGet(c, "front_domains", []))],
      ["Front Domain Count", cfgGet(c, "front_domains_count", "-")],
      ["Scripts Count", cfgGet(c, "script_ids_count", "-")],
      ["Auth Key", cfgGet(c, "has_auth_key", false) ? "SET / hidden" : "NOT SET", cfgGet(c, "has_auth_key", false) ? "warn" : "bad"]
    ];
    note = "Sensitive values are intentionally hidden by the dashboard API.";
  } else if (activeCfgTab === "routing") {
    rows = [
      ["YouTube Via Relay", yn(!!cfgGet(c, "youtube_via_relay", false)), !!cfgGet(c, "youtube_via_relay", false) ? "good" : "warn"],
      ["Direct Google Allow", listSummary(cfgGet(c, "direct_google_allow", []))],
      ["Direct Google Exclude", listSummary(cfgGet(c, "direct_google_exclude", []))],
      ["Direct Exclude Count", cfgGet(c, "direct_google_exclude_count", "-")],
      ["Bypass Hosts", listSummary(cfgGet(c, "bypass_hosts", []))],
      ["Block Hosts", listSummary(cfgGet(c, "block_hosts", []))],
      ["Hosts Overrides", listSummary(cfgGet(c, "hosts", {}))],
      ["Script Blacklist TTL", fmtMaybeSeconds(cfgGet(c, "script_blacklist_ttl", "-"))],
      ["Blacklisted Scripts Live", cfgGet(c, "blacklisted_scripts", "-")]
    ];
  } else if (activeCfgTab === "timeouts") {
    rows = [
      ["Relay Timeout", fmtMaybeSeconds(cfgGet(c, "relay_timeout", "-"))],
      ["Range Probe Timeout", fmtMaybeSeconds(cfgGet(c, "range_probe_timeout", "-"))],
      ["H2 Stream Timeout", fmtMaybeSeconds(cfgGet(c, "h2_stream_timeout", "-"))],
      ["H2 Connect Timeout", fmtMaybeSeconds(cfgGet(c, "h2_connect_timeout", "-"))],
      ["TLS Connect Timeout", fmtMaybeSeconds(cfgGet(c, "tls_connect_timeout", "-"))],
      ["TCP Connect Timeout", fmtMaybeSeconds(cfgGet(c, "tcp_connect_timeout", "-"))],
      ["Video Passthrough Timeout", fmtMaybeSeconds(cfgGet(c, "video_passthrough_relay_timeout", "-"))],
      ["SABR Timeout", fmtMaybeSeconds(cfgGet(c, "youtube_sabr_timeout", "-"))],
      ["Downloader H2 Timeout", fmtMaybeSeconds(cfgGet(c, "downloader_h2_timeout", "-"))],
      ["Exit Node Health Timeout", fmtMaybeSeconds(cfgGet(c, "exit_node_health_timeout", "-"))]
    ];
  } else if (activeCfgTab === "downloads") {
    rows = [
      ["Chunked Extensions", listSummary(cfgGet(c, "chunked_download_extensions", []), 12)],
      ["Min Size", fmtMaybeBytes(cfgGet(c, "chunked_download_min_size", "-"))],
      ["Chunk Size", fmtMaybeBytes(cfgGet(c, "chunked_download_chunk_size", "-"))],
      ["Max Parallel", cfgGet(c, "chunked_download_max_parallel", "-")],
      ["Max Chunks", cfgGet(c, "chunked_download_max_chunks", "-")],
      ["Max Response Body", fmtMaybeBytes(cfgGet(c, "max_response_body_bytes", "-"))]
    ];
  } else if (activeCfgTab === "video") {
    rows = [
      ["Video Prefetch", yn(!!cfgGet(c, "video_prefetch_enabled", false)), !!cfgGet(c, "video_prefetch_enabled", false) ? "good" : "bad"],
      ["Video Next Ranges", cfgGet(c, "video_prefetch_next_ranges", "-")],
      ["Video Parallel", cfgGet(c, "video_prefetch_parallel", "-")],
      ["Video Chunk Size", fmtMaybeBytes(cfgGet(c, "video_prefetch_chunk_size", "-"))],
      ["Video Cache Max", cfgGet(c, "video_cache_max_mb", "-") + " MB"],
      ["Video Cache TTL", fmtMaybeSeconds(cfgGet(c, "video_cache_ttl_seconds", "-"))],
      ["Manifest Prefetch", yn(!!cfgGet(c, "manifest_prefetch_enabled", false)), !!cfgGet(c, "manifest_prefetch_enabled", false) ? "good" : "bad"],
      ["Manifest Next Segments", cfgGet(c, "manifest_prefetch_next_segments", "-")],
      ["Manifest Parallel", cfgGet(c, "manifest_prefetch_parallel", "-")],
      ["Manifest Cache Max", cfgGet(c, "manifest_cache_max_mb", "-") + " MB"],
      ["Manifest Cache TTL", fmtMaybeSeconds(cfgGet(c, "manifest_cache_ttl_seconds", "-"))],
      ["Video Passthrough", yn(!!cfgGet(c, "video_passthrough_enabled", false)), !!cfgGet(c, "video_passthrough_enabled", false) ? "good" : "bad"]
    ];
  } else if (activeCfgTab === "turbo") {
    rows = [
      ["Turbo Mode", yn(!!cfgGet(c, "turbo_mode_enabled", false)), !!cfgGet(c, "turbo_mode_enabled", false) ? "good" : "bad"],
      ["Turbo Parallel Relay", cfgGet(c, "turbo_parallel_relay", "-")],
      ["Turbo Parallel", cfgGet(c, "turbo_parallel", "-")],
      ["Turbo Force No Delay", yn(!!cfgGet(c, "turbo_force_no_delay", false))],
      ["Turbo Skip Download Mode", yn(!!cfgGet(c, "turbo_skip_download_mode", false))],
      ["Turbo Coalesce Window", fmtMaybeMs(cfgGet(c, "turbo_coalesce_window_ms", "-"))],
      ["Turbo Small Request Max", fmtMaybeBytes(cfgGet(c, "turbo_small_request_max", "-"))],
      ["Turbo Min Upload Padding", fmtMaybeBytes(cfgGet(c, "turbo_min_upload_padding", "-"))],
      ["Turbo Chunk Size", fmtMaybeBytes(cfgGet(c, "turbo_chunk_size", "-"))],
      ["Turbo Min Size", fmtMaybeBytes(cfgGet(c, "turbo_min_size", "-"))]
    ];
  } else if (activeCfgTab === "telegram") {
    rows = [
      ["Telegram Mode", yn(!!cfgGet(c, "telegram_mode_enabled", false)), !!cfgGet(c, "telegram_mode_enabled", false) ? "good" : "bad"],
      ["Telegram Parallel Relay", cfgGet(c, "telegram_parallel_relay", "-")],
      ["Cache TTL", fmtMaybeSeconds(cfgGet(c, "telegram_cache_ttl_seconds", "-"))],
      ["Hosts", listSummary(cfgGet(c, "telegram_hosts", []))],
      ["Serial Per Host", yn(!!cfgGet(c, "telegram_serial_per_host", false))],
      ["Coalesce Window", fmtMaybeMs(cfgGet(c, "telegram_coalesce_window_ms", "-"))],
      ["Max Delay", fmtMaybeMs(cfgGet(c, "telegram_max_delay_ms", "-"))],
      ["CIDR Count", cfgGet(c, "telegram_cidrs_count", "-")],
      ["CIDRs", listSummary(cfgGet(c, "telegram_cidrs", []), 5)]
    ];
  } else if (activeCfgTab === "downloader") {
    rows = [
      ["Downloader Directory", cfgGet(c, "downloader_dir", "-")],
      ["Chunk Size", fmtMaybeBytes(cfgGet(c, "downloader_chunk_size", "-"))],
      ["Parallel", cfgGet(c, "downloader_parallel", "-")],
      ["Retries", cfgGet(c, "downloader_retries", "-")],
      ["H2 Enabled", yn(!!cfgGet(c, "downloader_h2_enabled", false)), !!cfgGet(c, "downloader_h2_enabled", false) ? "good" : "bad"],
      ["H2 Wait", cfgGet(c, "downloader_h2_wait_seconds", "-") + "s"],
      ["H2 Timeout", fmtMaybeSeconds(cfgGet(c, "downloader_h2_timeout", "-"))],
      ["User Agent", cfgGet(c, "downloader_user_agent", "-")],
      ["Headers", listSummary(cfgGet(c, "downloader_headers", {}))]
    ];
  } else if (activeCfgTab === "security") {
    rows = [
      ["Auth Key", cfgGet(c, "has_auth_key", false) ? "SET / hidden" : "NOT SET", cfgGet(c, "has_auth_key", false) ? "warn" : "bad"],
      ["Exit Node Key", cfgGet(c, "exit_node_health_key_set", false) ? "SET / hidden" : "NOT SET", cfgGet(c, "exit_node_health_key_set", false) ? "warn" : "bad"],
      ["Verify SSL", yn(!!cfgGet(c, "verify_ssl", true)), !!cfgGet(c, "verify_ssl", true) ? "good" : "warn"],
      ["LAN Sharing", yn(!!cfgGet(c, "lan_sharing", false)), !!cfgGet(c, "lan_sharing", false) ? "warn" : ""],
      ["Exit Node Health URL", cfgGet(c, "exit_node_health_url", "-")],
      ["Exit Node Interval", fmtMaybeSeconds(cfgGet(c, "exit_node_health_interval", "-"))],
      ["Exit Node Timeout", fmtMaybeSeconds(cfgGet(c, "exit_node_health_timeout", "-"))],
      ["Health Error", cfgGet(s, "exit_node_health_error", "-")]
    ];
  } else if (activeCfgTab === "all") {
    setHTML("advancedCfgBody", `<div class="rawBox">${esc(JSON.stringify(c, null, 2))}</div>`);
    return;
  }

  setHTML("advancedCfgBody",
    '<div class="configGrid">' + rows.map((r) => configBox(r[0], r[1], r[2] || "")).join("") + '</div>' +
    (note ? '<div class="note info">' + esc(tx(note)) + '</div>' : "")
  );
}

/* ----------------------------- Script Optimizer ----------------------------- */

function optimizerTierForCount(count) {
  count = Number(count || 0);
  if (count <= 1) return 1;
  if (count === 2) return 2;
  if (count === 3) return 3;
  if (count === 4) return 4;
  return 5;
}

function optimizerNormalizeIds(value) {
  if (value === null || value === undefined) return [];

  let rawItems = [];

  if (Array.isArray(value)) {
    rawItems = value;
  } else {
    const rawValue = String(value || "").trim();
    if (!rawValue) return [];

    try {
      const parsed = JSON.parse(rawValue);
      rawItems = Array.isArray(parsed) ? parsed : [parsed];
    } catch (e) {
      rawItems = [rawValue];
    }
  }

  const expanded = [];

  rawItems.forEach((item) => {
    let x = String(item || "").trim();
    x = x
      .replace(/\\r\\n/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\r/g, "\n")
      .replace(/\r/g, "\n");

    x.replace(/,/g, "\n").split("\n").forEach((part) => expanded.push(part));
  });

  const out = [];
  const seen = new Set();

  expanded.forEach((part) => {
    let x = String(part || "").trim();
    x = x.replace(/^[\[\]'"]+|[\]\]'"]+$/g, "").trim();
    x = x.replace(/\\"/g, "").replace(/\\'/g, "").trim();

    if (!x || x === "[" || x === "]" || x === ",") return;
    if (seen.has(x)) return;

    seen.add(x);
    out.push(x);
  });

  return out;
}

function optimizerShortId(id) {
  id = String(id || "");
  if (id.length <= 34) return id;
  return id.slice(0, 16) + "..." + id.slice(-10);
}

function optimizerActiveTier(state) {
  state = state || {};
  return Number(state.active_tier || state.optimized_for_script_count || state.script_tier || 0);
}

function optimizerStateChanged(state) {
  state = state || {};
  return !!(
    state.config_changed ||
    state.optimizer_config_changed ||
    state.optimizer_config_drift ||
    state.runtime_profiles_changed ||
    state.optimizer_runtime_profiles_changed ||
    (
      state.needs_attention &&
      /config changed|runtime_profiles|کانفیگ|تغییر/i.test(String(state.attention || ""))
    )
  );
}

function optimizerRenderOverviewCard(c) {
  const overviewCard = $("optimizerOverviewCard");
  if (overviewCard) {
    overviewCard.style.removeProperty("display");
  }

  c = c || {};

  const count = Number(c.script_ids_count || c.script_count || 0);
  const maxTier = optimizerTierForCount(count);
  const active = Number(c.optimized_for_script_count || c.script_tier || 0);
  const changed = !!(c.config_changed || c.optimizer_config_changed || c.optimizer_config_drift);

  setText("optimizerTier", active ? ("Tier " + active) : "-");
  setText("optimizerMeta", count + " ID(s) · active tier: " + (active || "-"));

  if (changed) setBadge("optimizerMiniBadge", "Config changed", "warn");
  else if (count > 0 && active === maxTier) setBadge("optimizerMiniBadge", "Optimized", "good");
  else if (count > 0 && active > 0) setBadge("optimizerMiniBadge", "Review", "warn");
  else setBadge("optimizerMiniBadge", "Not set", "bad");
}

function optimizerEnsureTopBadge() {
  const actions = document.querySelector(".headerActions") || document.querySelector(".quickBtns") || document.querySelector(".navMain");
  if (!actions) return null;

  let slot = $("optimizerNavSlot");
  if (!slot) {
    slot = document.createElement("div");
    slot.id = "optimizerNavSlot";
    slot.className = "optimizerNavSlot";
    actions.insertBefore(slot, actions.firstChild || null);
  }

  let badge = $("optimizerNavBadge");
  if (!badge) {
    badge = document.createElement("span");
    badge.id = "optimizerNavBadge";
    badge.className = "optimizerNavBadge warn";
    badge.textContent = isFa() ? "بهینه‌ساز: در حال بررسی..." : "Script Optimizer: Checking...";
    slot.appendChild(badge);
  }

  return badge;
}

function optimizerPaintTopBadge(state) {
  const badge = optimizerEnsureTopBadge();
  if (!badge) return;

  state = state || optimizerState || {};
  const changed = optimizerStateChanged(state);
  const tier = optimizerActiveTier(state);

  badge.className = "optimizerNavBadge";

  if (changed) {
    badge.classList.add("warn");
    badge.textContent = isFa()
      ? "بهینه‌ساز: کانفیگ تغییر کرده · Tier " + (tier || "-")
      : "Script Optimizer: Config changed · Tier " + (tier || "-");
    return;
  }

  if (tier > 0) {
    badge.classList.add("ok");
    badge.textContent = isFa()
      ? "بهینه‌ساز: اعمال شده · Tier " + tier
      : "Script Optimizer: Applied · Tier " + tier;
    return;
  }

  badge.classList.add("bad");
  badge.textContent = isFa() ? "بهینه‌ساز: اعمال نشده" : "Script Optimizer: Not applied";
}

function optimizerHideOldHero() {
  /*
    Only hide legacy optimizer hero blocks.
    Do not hide #optimizerOverviewCard because it is the real Overview card.
    The previous code added inline display:none to this card, so it disappeared
    until a full browser refresh.
  */
  document.querySelectorAll(".optimizerHero").forEach((x) => {
    x.style.display = "none";
  });

  const overviewCard = $("optimizerOverviewCard");
  if (overviewCard) {
    overviewCard.style.removeProperty("display");
  }
}

function optimizerTierLabel(n) {
  return "Tier " + n;
}

function optimizerTierDesc(n) {
  if (n === 5) return isFa() ? "برای ۵ یا بیشتر Script ID" : "For 5 or more Script IDs";
  return isFa() ? "برای " + n + " Script ID" : "For " + n + " Script ID" + (n === 1 ? "" : "s");
}

function renderOptimizerIds() {
  const box = $("optimizerIdList");
  if (!box) return;

  if (!optimizerIds.length) {
    box.innerHTML = '<div class="empty">' + esc(isFa() ? "هیچ ID ثبت نشده است." : "No IDs loaded.") + '</div>';
    return;
  }

  box.innerHTML = optimizerIds.map((id, idx) => {
    return `<div class="optimizerIdRow">
      <span class="optimizerIdIndex">#${idx + 1}</span>
      <div class="optimizerIdValue">
        <div class="name">Deployment ID</div>
        <div class="value" title="${esc(id)}">${esc(optimizerShortId(id))}</div>
      </div>
      <button class="bad" onclick="optimizerRemoveId(${idx})">${esc(t("Remove", "Remove"))}</button>
    </div>`;
  }).join("");
}

function renderOptimizerTiers() {
  const grid = $("optimizerTierGrid");
  if (!grid) return;

  const count = optimizerIds.length;
  const maxTier = optimizerTierForCount(count);
  const current = optimizerActiveTier(optimizerState);

  if (!optimizerSelectedTier) optimizerSelectedTier = Number((optimizerState || {}).selected_tier || current || maxTier || 1);
  if (optimizerSelectedTier > maxTier) optimizerSelectedTier = maxTier;

  grid.innerHTML = [1, 2, 3, 4, 5].map((n) => {
    const locked = n > maxTier;
    const selected = n === Number(optimizerSelectedTier);
    const isCurrent = n === Number(current);

    let state = t("Available", "Available");
    if (locked) state = t("Locked", "Locked");
    else if (selected) state = t("Selected", "Selected");
    else if (isCurrent) state = t("Current", "Current");

    return `<div class="optimizerTierCard ${locked ? "locked" : ""} ${isCurrent ? "current" : ""} ${selected ? "selected" : ""}"
      data-optimizer-tier="${n}"
      onclick="${locked ? "" : "optimizerSelectTier(" + n + ")"}">
      <div class="tierName">${esc(optimizerTierLabel(n))}</div>
      <div class="tierMeta">${esc(optimizerTierDesc(n))}</div>
      <div class="tierState">${esc(state)}</div>
    </div>`;
  }).join("");
}

function renderOptimizerKv() {
  const state = optimizerState || {};
  const count = optimizerIds.length;
  const maxTier = optimizerTierForCount(count);
  const current = optimizerActiveTier(state);
  const locked = [1, 2, 3, 4, 5].filter((x) => x > maxTier);
  const optimizedAt = state.optimized_at ? new Date(Number(state.optimized_at) * 1000).toLocaleString() : "-";

  kv("optimizerKv", {
    "Script IDs Count": count,
    "Selected Tier": optimizerSelectedTier || "-",
    "Active Optimized Tier": current || "-",
    "Locked Tiers": locked.length ? locked.join(", ") : "-",
    "Optimizer Version": state.optimizer_version || "-",
    "Optimized At": optimizedAt
  }, true);
}

function renderOptimizer() {
  optimizerHideOldHero();

  const state = optimizerState || {};
  const count = optimizerIds.length;
  const maxTier = optimizerTierForCount(count);
  const current = optimizerActiveTier(state);
  const changed = optimizerStateChanged(state);

  setText("optimizerCountBadge", count + " ID(s)");
  setBadge("optimizerActiveBadge", t("Active", "Active") + ": " + (current || "-"), current ? "good" : "bad");

  if (count <= 0) {
    setText("optimizerTitle", "No Script IDs configured");
    setText("optimizerHint", "Add at least one Apps Script deployment ID.");
    setBadge("optimizerStatusBadge", "Optimizer: Not ready", "bad");
  } else if (changed) {
    setText("optimizerTitle", "Optimizer needs refresh");
    setText("optimizerHint", state.attention || "Config or runtime_profiles.json changed after last optimization.");
    setBadge("optimizerStatusBadge", "Config changed", "warn");
  } else {
    setText("optimizerTitle", "Optimizer ready");
    setText("optimizerHint", count + " ID(s) · max available tier: " + maxTier);
    setBadge("optimizerStatusBadge", current ? ("Tier " + current) : "-", current ? "good" : "warn");
  }

  const warn = $("optimizerSoftWarning");
  if (warn) {
    if (changed) {
      warn.style.display = "";
      warn.className = "note optimizerNoticeWarn";
      warn.textContent = tx(state.attention || "Config or runtime_profiles.json changed after last optimization. Apply optimization again.");
    } else if (count > 0 && current > 0 && current !== maxTier) {
      warn.style.display = "";
      warn.className = "note optimizerNoticeWarn";
      warn.textContent = tx("You selected a different tier than the active optimized tier. Click Apply to make it active.");
    } else {
      warn.style.display = "none";
      warn.textContent = "";
    }
  }

  optimizerPaintTopBadge(state);
  renderOptimizerIds();
  renderOptimizerTiers();
  renderOptimizerKv();
}

async function optimizerFetchStatus() {
  const urls = ["/api/script-optimizer/status", "/api/script-optimizer"];

  for (const url of urls) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      const txtBody = await r.text();
      if (!txtBody.trim()) continue;

      const j = JSON.parse(txtBody);
      if (j && j.ok) return j;
    } catch (e) {}
  }

  throw new Error("optimizer status failed");
}

async function loadScriptOptimizer() {
  try {
    optimizerHideOldHero();

    const j = await optimizerFetchStatus();

    optimizerState = j;
    optimizerIds = optimizerNormalizeIds(j.script_ids || []);
    optimizerSelectedTier = Number(j.selected_tier || j.active_tier || j.optimized_for_script_count || j.script_tier || optimizerTierForCount(optimizerIds.length) || 1);
    optimizerLoaded = true;

    renderOptimizer();
  } catch (e) {
    setText("optimizerTitle", "Optimizer load failed");
    setText("optimizerHint", String(e));
    setBadge("optimizerStatusBadge", "Optimizer: -", "bad");
    optimizerPaintTopBadge(null);
  }
}

function optimizerParseIds() {
  return optimizerNormalizeIds(optimizerIds);
}

function optimizerSetDraft(ids) {
  optimizerIds = optimizerNormalizeIds(ids);
  const maxTier = optimizerTierForCount(optimizerIds.length);
  if (Number(optimizerSelectedTier || 1) > maxTier) optimizerSelectedTier = maxTier;
  renderOptimizer();
}

function optimizerAddId() {
  const input = $("optimizerNewId") || $("optimizerIdInput") || $("optimizerFinalIdInput");
  const ids = optimizerNormalizeIds(input ? input.value : "");

  if (!ids.length) {
    alert(t("Enter a Script ID first.", "Enter a Script ID first."));
    return;
  }

  optimizerIds = optimizerNormalizeIds(optimizerIds.concat(ids));
  if (input) input.value = "";

  const maxTier = optimizerTierForCount(optimizerIds.length);
  if (Number(optimizerSelectedTier || 1) > maxTier) optimizerSelectedTier = maxTier;

  renderOptimizer();
}

function optimizerRemoveId(index) {
  optimizerIds = optimizerIds.filter((_, i) => i !== index);

  const maxTier = optimizerTierForCount(optimizerIds.length);
  if (Number(optimizerSelectedTier || 1) > maxTier) optimizerSelectedTier = maxTier;

  renderOptimizer();
}

function optimizerTogglePaste(force) {
  const box = $("optimizerBulkPaste");
  const actions = $("optimizerBulkActions");
  if (!box) return;

  const show = force === undefined ? (box.style.display === "none" || !box.style.display) : !!force;

  box.style.display = show ? "" : "none";
  if (actions) actions.style.display = show ? "" : "none";

  if (show) {
    box.value = optimizerIds.join("\n");
    box.focus();
  }
}

function optimizerImportBulk() {
  const box = $("optimizerBulkPaste");
  optimizerIds = optimizerNormalizeIds(box ? box.value : "");
  optimizerTogglePaste(false);

  const maxTier = optimizerTierForCount(optimizerIds.length);
  if (Number(optimizerSelectedTier || 1) > maxTier) optimizerSelectedTier = maxTier;

  renderOptimizer();
}

function optimizerCleanIds() {
  optimizerIds = optimizerNormalizeIds(optimizerIds);
  renderOptimizer();
}

function optimizerSelectTier(tier) {
  tier = Number(tier || 1);
  const maxTier = optimizerTierForCount(optimizerIds.length);

  if (tier > maxTier) {
    alert(t("This tier is locked for your current Script ID count.", "This tier is locked for your current Script ID count."));
    return;
  }

  optimizerSelectedTier = tier;
  renderOptimizer();
}

async function saveScriptOptimizer(apply) {
  const ids = optimizerNormalizeIds(optimizerIds);

  if (!ids.length) {
    alert(t("At least one Script ID is required.", "At least one Script ID is required."));
    return;
  }

  const maxTier = optimizerTierForCount(ids.length);
  const tier = Number(optimizerSelectedTier || maxTier);

  if (tier > maxTier) {
    alert(t("This tier is locked for your current Script ID count.", "This tier is locked for your current Script ID count."));
    return;
  }

  if (apply) {
    const ok = confirm(t(
      "Apply optimization now? MHR will save config, reset quota_state.json, and restart.",
      "Apply optimization now? MHR will save config, reset quota_state.json, and restart."
    ));
    if (!ok) return;
  }

  try {
    const url = apply ? "/api/script-optimizer/apply" : "/api/script-optimizer/save-ids";
    const body = apply
      ? { script_ids: ids, ids: ids, tier: tier, target_tier: tier, apply: true }
      : { script_ids: ids, ids: ids, apply: false };

    let r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

    let j = await r.json();

    if (!j.ok && !apply) {
      r = await fetch("/api/script-optimizer/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      j = await r.json();
    }

    if (!j.ok) {
      alert(j.error || "failed");
      return;
    }

    const state = j.state || j;

    optimizerState = state;
    optimizerIds = optimizerNormalizeIds(state.script_ids || ids);
    optimizerSelectedTier = Number(state.selected_tier || state.active_tier || state.optimized_for_script_count || tier);
    optimizerLoaded = true;

    renderOptimizer();

    try { await refresh(); } catch (e) {}

    if (j.restart_required) {
      alert(j.message || t("Optimization applied. MHR is restarting.", "Optimization applied. MHR is restarting."));
      setTimeout(() => {
        location.href = "/?fresh=" + Date.now();
      }, Math.max(1200, Number(j.restart_delay_seconds || 4) * 1000));
    } else {
      alert(j.message || t("Saved.", "Saved."));
    }
  } catch (e) {
    alert(String(e));
  }
}

/* ----------------------------- Main Render ----------------------------- */

function render(s) {
  s = s || {};
  lastStatsSnapshot = s;

  const c = s.config || {};
  const now = Date.now() / 1000;

  let speeds = { rx: 0, tx: 0, client: 0, req: 0 };

  if (prev) {
    const dt = now - prev.t;

    if (dt >= 0.5) {
      speeds.rx = ((s.bytes_from_google || 0) - prev.rx) / dt;
      speeds.tx = ((s.bytes_to_google || 0) - prev.tx) / dt;
      speeds.client = ((s.bytes_to_client || 0) - prev.client) / dt;
      speeds.req = ((s.google_requests || 0) - prev.req) / dt;

      speeds.rx = smoothSpeed("rx", speeds.rx);
      speeds.tx = smoothSpeed("tx", speeds.tx);
      speeds.client = smoothSpeed("client", speeds.client);

      peaks.rx = Math.max(peaks.rx, speeds.rx || 0);
      peaks.tx = Math.max(peaks.tx, speeds.tx || 0);
      peaks.client = Math.max(peaks.client, speeds.client || 0);
    }
  }

  prev = {
    t: now,
    rx: s.bytes_from_google || 0,
    tx: s.bytes_to_google || 0,
    client: s.bytes_to_client || 0,
    req: s.google_requests || 0
  };

  const mode = s.web_mode || c.runtime_mode || "-";
  // Dashboard-visible errors:
  // Prefer log-derived error count because many real runtime problems
  // are WARNING/timeout/H2/front-IP events and may not increment stats.errors.
  const statErrors = Number(s.errors || 0);
  const logErrors = Number(
    s.dashboard_log_full_errors ??
    s.dashboard_log_errors ??
    (s.dashboard_log_counts && (s.dashboard_log_counts.full_errors ?? s.dashboard_log_counts.errors)) ??
    (lastLogs.full_errors ? lastLogs.full_errors.length : 0) ??
    0
  );
  const visibleErrors = Math.max(statErrors, logErrors);

  const errRate = (visibleErrors / Math.max(1, s.google_requests || s.proxy_requests || 1)) * 100;
  const errRateText = errRate.toFixed(2) + "%";
  const errSt = errorState(errRate);
  const exitOnline = !!(s.exit_node_health && s.exit_node_health.ok);
  const h2Text = c.h2_status_text || (c.h2_dashboard_on ? "ON" : "OFF");
  const h2State = statusStateFromH2(h2Text);
  const sabrOn = !!c.youtube_sabr_booster_enabled;
  const turboOn = !!c.turbo_mode_enabled;

  optimizerRenderOverviewCard(c);
  setActiveMode(mode);
  updateToggleButtons(c);
  renderFrontHealth(s);

  setText("liveText", t("status.live", "Live") + " · " + new Date().toLocaleTimeString());
  setBadge("topExit", "Exit node: " + (exitOnline ? "ONLINE" : "OFFLINE"), exitOnline ? "good" : "bad");
  setBadge("topH2", "H2: " + h2Text, h2State);
  setFeatureBadge("topTurbo", "Turbo", turboOn);
  setFeatureBadge("topSabr", "SABR", sabrOn);
  setFeatureBadge("topVideoPrefetch", "Video Prefetch", !!c.video_prefetch_enabled);
  setFeatureBadge("topManifestPrefetch", "Manifest Prefetch", !!c.manifest_prefetch_enabled);
  setFeatureBadge("topVideoPassthrough", "Video Passthrough", !!c.video_passthrough_enabled);
  setBadge("topErrors", "Errors: " + visibleErrors + " · " + errRateText, errSt);
  setBadge("topSpeed", "↓ " + fmtMbps(speeds.rx) + " / ↑ " + fmtMbps(speeds.tx), "info");

  setText("health", exitOnline ? "ONLINE" : "OFFLINE");
  setClass("health", "value " + (exitOnline ? "goodText" : "badText"));
  setBadge("healthBadge", exitOnline ? "ONLINE" : "OFFLINE", exitOnline ? "good" : "bad");
  setText("healthMeta", exitOnline ? "Exit node reachable through relay path" : "Exit node offline, locked, or health check failed");

  setText("mode", prettyMode(mode));
  setText("modeMeta", "Runtime profile: " + prettyMode(mode) + " · Auto ready: " + (c.auto_mode_ready ? "YES" : "NO"));

  setText("h2Status", h2Text);
  setClass("h2Status", "value " + (h2State === "good" ? "goodText" : h2State === "bad" ? "badText" : "warnText"));
  setBadge("h2MiniBadge", h2Text, h2State);
  setText(
    "h2HealerMeta",
    "Healer: " + (c.h2_healer_enabled ? "ON" : "OFF") +
    " · delay " + (c.h2_healer_delay_seconds ?? "-") + "s" +
    " · cooldown " + (c.h2_healer_cooldown_seconds ?? "-") + "s"
  );

  setText("errorRate", errRateText);
  setClass("errorRate", "value " + (errSt === "good" ? "goodText" : errSt === "bad" ? "badText" : "warnText"));
  setBadge("errorBadge", String(visibleErrors) + " errors", errSt);

  setText("downSpeed", fmtMbps(speeds.rx));
  setText("downPeak", fmtMbps(peaks.rx));
  setText("upSpeed", fmtMbps(speeds.tx));
  setText("upPeak", fmtMbps(peaks.tx));
  setText("clientSpeed", fmtMbps(speeds.client));
  setText("reqSpeed", speeds.req.toFixed(2));
  setText("runtimeQuota", String(s.runtime_quota_used || 0));
  setText("uptime", fmtTime(s.uptime_seconds));
  setText("resetIn", fmtTime(s.reset_in_seconds));
  setText("errors", visibleErrors);
  setText("fromGoogleMini", fmtBytes(s.bytes_from_google));
  setText("toGoogleMini", fmtBytes(s.bytes_to_google));
  setText("clientTrafficMini", fmtBytes((s.bytes_to_client || 0) + (s.bytes_from_client || 0)));

  kv("coreStats", {
    "HTTP Proxy": `${c.listen_host || "127.0.0.1"}:${c.listen_port || "-"}`,
    "SOCKS5": c.socks5_enabled ? String(c.socks5_port) : "Disabled",
    "Google Requests": s.google_requests || 0,
    "Proxy Requests": s.proxy_requests || 0,
    "Requests / sec": speeds.req.toFixed(2),
    "Errors": visibleErrors,
    "Reset At": s.reset_at_iran || "-"
  });

  kv("trafficStats", {
    "Total Traffic": fmtBytes((s.bytes_to_google || 0) + (s.bytes_from_google || 0) + (s.bytes_to_client || 0) + (s.bytes_from_client || 0)),
    "Google Traffic": fmtBytes((s.bytes_to_google || 0) + (s.bytes_from_google || 0)),
    "Client Traffic": fmtBytes((s.bytes_to_client || 0) + (s.bytes_from_client || 0)),
    "To Google": fmtBytes(s.bytes_to_google),
    "From Google": fmtBytes(s.bytes_from_google),
    "Quota": `${s.quota_used || 0}/${s.quota_limit || 0}`,
    "Runtime Quota": s.runtime_quota_used || 0
  });

  kv("relayStats", {
    "Error Rate": errRateText,
    "Relay Timeout": (c.relay_timeout || "-") + "s",
    "Parallel Relay": c.parallel_relay || "-",
    "Chunk Size": fmtBytes(c.chunked_download_chunk_size || 0),
    "Max Parallel": c.chunked_download_max_parallel || "-",
    "Download Mode": JSON.stringify(c.chunked_download_extensions) === '["*"]' ? "*" : "Extensions",
    "H2 Connections": c.h2_connections ?? "-",
    "H2 Live": c.h2_live_connections ?? "-",
    "Batch": c.enable_batch ? "ON" : "OFF",
    "Sub-batch": c.enable_sub_batch ? "ON" : "OFF",
    "Batch Max": c.batch_max ?? "-"
  });

  kv("networkProxyStats", {
    "HTTP Proxy": `${c.listen_host || "127.0.0.1"}:${c.listen_port || "-"}`,
    "SOCKS5": c.socks5_enabled ? "ON / Port " + c.socks5_port : "OFF",
    "LAN Sharing": yn(!!c.lan_sharing),
    "Verify SSL": yn(!!c.verify_ssl),
    "Google IP": c.google_ip || "-",
    "Front Domain": c.front_domain || "-",
    "Front Domains": listSummary(c.front_domains || []),
    "Script IDs Count": c.script_ids_count ?? c.script_count ?? "-",
    "Mode": c.mode || "apps_script"
  }, true);

  kv("routingStats", {
    "YouTube Via Relay": yn(!!c.youtube_via_relay),
    "Direct Google Allow": listSummary(c.direct_google_allow || []),
    "Direct Google Exclude": listSummary(c.direct_google_exclude || []),
    "Direct Exclude Count": c.direct_google_exclude_count ?? "-",
    "Bypass Hosts": listSummary(c.bypass_hosts || []),
    "Block Hosts": listSummary(c.block_hosts || []),
    "Hosts Overrides": listSummary(c.hosts || {}),
    "Script Blacklist TTL": fmtMaybeSeconds(c.script_blacklist_ttl),
    "Blacklisted Scripts": c.blacklisted_scripts ?? "-"
  }, true);

  setHTML("timeoutGrid", [
    configBox("Relay", fmtMaybeSeconds(c.relay_timeout)),
    configBox("Range Probe", fmtMaybeSeconds(c.range_probe_timeout)),
    configBox("H2 Stream", fmtMaybeSeconds(c.h2_stream_timeout)),
    configBox("H2 Connect", fmtMaybeSeconds(c.h2_connect_timeout)),
    configBox("TLS Connect", fmtMaybeSeconds(c.tls_connect_timeout)),
    configBox("TCP Connect", fmtMaybeSeconds(c.tcp_connect_timeout)),
    configBox("Video Passthrough", fmtMaybeSeconds(c.video_passthrough_relay_timeout)),
    configBox("SABR", fmtMaybeSeconds(c.youtube_sabr_timeout)),
    configBox("Downloader H2", fmtMaybeSeconds(c.downloader_h2_timeout)),
    configBox("Exit Node Health", fmtMaybeSeconds(c.exit_node_health_timeout))
  ].join(""));

  setHTML("featureTiles", [
    tile("Telegram", yn(!!c.telegram_mode_enabled), c.telegram_mode_enabled ? "Telegram profile active" : "Standard relay", c.telegram_mode_enabled ? "good" : "bad"),
    tile("Turbo", yn(turboOn), turboOn ? "Acceleration enabled" : "Acceleration disabled", turboOn ? "good" : "bad"),
    tile("H2", c.h2_status_text || yn(!!c.h2_dashboard_on), h2Text, h2State),
    tile("H2 Healer", yn(!!c.h2_healer_enabled), "Delay " + (c.h2_healer_delay_seconds ?? "-") + "s · cooldown " + (c.h2_healer_cooldown_seconds ?? "-") + "s", c.h2_healer_enabled ? "good" : "bad"),
    tile("SABR", yn(sabrOn), sabrOn ? "Booster enabled" : "Booster disabled", sabrOn ? "good" : "bad"),
    tile("Video Prefetch", yn(!!c.video_prefetch_enabled), "Range lookahead", c.video_prefetch_enabled ? "good" : "bad"),
    tile("Manifest Prefetch", yn(!!c.manifest_prefetch_enabled), "Manifest lookahead", c.manifest_prefetch_enabled ? "good" : "bad"),
    tile("Video Passthrough", yn(!!c.video_passthrough_enabled), "Direct video passthrough", c.video_passthrough_enabled ? "good" : "bad"),
    tile("Auto Tune", yn(!!c.auto_tune_enabled), "Ready: " + yesNo(!!c.auto_mode_ready), c.auto_tune_enabled ? "good" : "warn")
  ].join(""));

  setHTML("features", [
    pill("Auto Tune", !!c.auto_tune_enabled),
    pill("Telegram", !!c.telegram_mode_enabled),
    pill("Turbo", turboOn),
    pill("H2", !!c.h2_dashboard_on),
    pill("H2 Healer", !!c.h2_healer_enabled),
    pill("SABR", sabrOn),
    pill("Range Prefetch", !!c.video_prefetch_enabled),
    pill("Manifest Prefetch", !!c.manifest_prefetch_enabled),
    pill("Video Passthrough", !!c.video_passthrough_enabled),
    pill("SOCKS5", !!c.socks5_enabled)
  ].join(""));

  kv("featureStats", {
    "Range Next": c.video_prefetch_next_ranges ?? "-",
    "Range Parallel": c.video_prefetch_parallel ?? "-",
    "Manifest Next": c.manifest_prefetch_next_segments ?? "-",
    "Manifest Parallel": c.manifest_prefetch_parallel ?? "-",
    "Turbo Padding": fmtBytes(c.turbo_min_upload_padding || 0),
    "Telegram Mode": c.telegram_mode_enabled ? "ON" : "OFF",
    "H2 Healer": c.h2_healer_enabled ? "ON" : "OFF",
    "H2 Healer Delay": (c.h2_healer_delay_seconds ?? "-") + "s",
    "H2 Healer Cooldown": (c.h2_healer_cooldown_seconds ?? "-") + "s",
    "Auto Ready": c.auto_mode_ready ? "YES" : "NO",
    "Auto Reason": c.auto_tune_last_reason || "-"
  }, true);

  const baseParallel = Number(c.parallel_relay || 0);
  const turboParallel = Number(c.turbo_parallel_relay || 0);
  const turboBoost = turboOn && turboParallel > baseParallel;

  if (!turboOn) {
    setHTML("turboStatus", `<span class="pill bad">Turbo: OFF</span><span class="pill bad">Idle</span>`);
  } else if (turboBoost) {
    setHTML("turboStatus", `<span class="pill good">Turbo: BOOST</span><span class="pill good">Extra boost active</span>`);
  } else {
    setHTML("turboStatus", `<span class="pill warn">Turbo: PASSIVE</span><span class="pill warn">Base already stronger</span>`);
  }

  kv("turboStats", {
    "Status": !turboOn ? "OFF" : (turboBoost ? "BOOST active" : "PASSIVE"),
    "Base Parallel": baseParallel || "-",
    "Turbo Parallel": turboParallel || "-",
    "Effective": turboBoost ? "Turbo is boosting eligible requests" : "Base mode is already equal or stronger",
    "No Delay": c.turbo_force_no_delay ? "ON" : "OFF",
    "Delay": (c.turbo_coalesce_window_ms ?? 0) + " ms",
    "Download Mode": c.turbo_skip_download_mode ? "Skip" : "Allowed",
    "H2 Pool": (c.h2_connections ?? "-") + " connections",
    "Sub-batch": c.enable_sub_batch ? "ON" : "OFF",
    "Batch Window": String(c.batch_window_micro ?? "-") + " / " + String(c.batch_window_macro ?? "-")
  }, true);

  raw("featureRaw", pick(c, [
    "auto_tune_enabled", "auto_mode_ready", "auto_tune_h2", "auto_tune_last_reason",
    "telegram_mode_enabled", "telegram_parallel_relay", "telegram_cache_ttl_seconds",
    "turbo_mode_enabled", "turbo_parallel_relay", "turbo_parallel", "turbo_force_no_delay",
    "turbo_skip_download_mode", "turbo_coalesce_window_ms", "turbo_small_request_max",
    "turbo_min_upload_padding", "turbo_chunk_size", "turbo_min_size",
    "h2_dashboard_on", "h2_status_text", "h2_connections", "h2_live_connections",
    "h2_healer_enabled", "h2_healer_delay_seconds", "h2_healer_cooldown_seconds", "h2_healer_check_interval",
    "youtube_sabr_booster_enabled", "video_prefetch_enabled", "manifest_prefetch_enabled",
    "video_passthrough_enabled"
  ]));

  const hits = Number(s.video_prefetch_hits || 0);
  const miss = Number(s.video_prefetch_misses || 0);
  const hitRate = hits + miss ? ((hits / (hits + miss)) * 100).toFixed(1) + "%" : "0.0%";

  kv("videoStats", {
    "Requests": s.video_prefetch_requests || 0,
    "Active": s.video_prefetch_active || 0,
    "Hits": hits,
    "Misses": miss,
    "Hit Rate": hitRate,
    "Errors": s.video_prefetch_errors || 0,
    "Bytes": fmtBytes(s.video_prefetch_bytes),
    "Video Prefetch": c.video_prefetch_enabled ? "ON" : "OFF",
    "Manifest Prefetch": c.manifest_prefetch_enabled ? "ON" : "OFF"
  });

  setHTML("sabrStatus", sabrOn
    ? `<span class="pill good">SABR Booster: ON</span>`
    : `<span class="pill bad">SABR Booster: OFF</span>`);

  kv("sabrStats", {
    "Enabled": sabrOn ? "ON" : "OFF",
    "Timeout": (c.youtube_sabr_timeout ?? "-") + "s",
    "Max Parallel": c.youtube_sabr_max_parallel ?? "-",
    "Retry Attempts": c.youtube_sabr_retry_attempts ?? "-",
    "Retry Delay": (c.youtube_sabr_retry_delay_ms ?? "-") + " ms",
    "Total": Number(s.sabr_total_logs || 0),
    "Success": Number(s.sabr_success || 0),
    "Failed": Number(s.sabr_failed || 0),
    "Unknown": Number(s.sabr_unknown || 0),
    "Success Rate": s.sabr_success_rate ?? "-",
    "Mode": "POST googlevideo / sabr=1 only"
  });

  setHTML("videoConfigGrid", [
    configBox("Video Cache Max", (c.video_cache_max_mb ?? "-") + " MB"),
    configBox("Video Cache TTL", fmtMaybeSeconds(c.video_cache_ttl_seconds)),
    configBox("Manifest Cache Max", (c.manifest_cache_max_mb ?? "-") + " MB"),
    configBox("Manifest Cache TTL", fmtMaybeSeconds(c.manifest_cache_ttl_seconds)),
    configBox("Video Chunk Size", fmtMaybeBytes(c.video_prefetch_chunk_size)),
    configBox("Priority Retry Attempts", c.video_priority_retry_attempts ?? "-"),
    configBox("Priority Retry Delay", fmtMaybeMs(c.video_priority_retry_delay_ms)),
    configBox("Priority Parallel Relay", c.video_priority_parallel_relay ?? "-"),
    configBox("Passthrough Timeout", fmtMaybeSeconds(c.video_passthrough_relay_timeout)),
    configBox("SABR Max Parallel", c.youtube_sabr_max_parallel ?? "-")
  ].join(""));

  raw("videoRecentLogs", [
    ...(lastLogs.video || []).slice(-30),
    ...(lastLogs.sabr || []).slice(-30)
  ].join("\n") || "No video/SABR logs");

  kv("downloaderStats", {
    "Downloader Directory": c.downloader_dir || "-",
    "Chunk Size": fmtMaybeBytes(c.downloader_chunk_size),
    "Parallel": c.downloader_parallel ?? "-",
    "Retries": c.downloader_retries ?? "-",
    "H2 Enabled": yn(!!c.downloader_h2_enabled),
    "H2 Wait": (c.downloader_h2_wait_seconds ?? "-") + "s",
    "H2 Timeout": fmtMaybeSeconds(c.downloader_h2_timeout),
    "User Agent": c.downloader_user_agent || "-",
    "Headers": listSummary(c.downloader_headers || {})
  }, true);

  kv("chunkedDownloadStats", {
    "Extensions": listSummary(c.chunked_download_extensions || [], 16),
    "Min Size": fmtMaybeBytes(c.chunked_download_min_size),
    "Chunk Size": fmtMaybeBytes(c.chunked_download_chunk_size),
    "Max Parallel": c.chunked_download_max_parallel ?? "-",
    "Max Chunks": c.chunked_download_max_chunks ?? "-",
    "Max Response Body": fmtMaybeBytes(c.max_response_body_bytes),
    "Mode": JSON.stringify(c.chunked_download_extensions) === '["*"]' ? "All files" : "Selected extensions"
  }, true);

  raw("downloadRecentLogs", (lastLogs.downloads || []).slice(-80).join("\n") || "No download logs");

  const eh = s.exit_node_health || null;
  const ehErr = s.exit_node_health_error || "-";
  const ehAge = s.exit_node_health_last ? Math.max(0, Math.floor(Date.now() / 1000 - Number(s.exit_node_health_last || 0))) : null;

  if (eh && eh.ok) {
    setHTML("exitNodeStatus", `<span class="pill good">Server: ONLINE</span><span class="pill info">${ehAge === null ? "-" : ehAge + "s ago"}</span>`);
    kv("exitNodeStats", {
      "Status": eh.status || "healthy",
      "Inflight": eh.inflight ?? "-",
      "Requests": eh.totalRequests ?? "-",
      "Errors": eh.totalErrors ?? "-",
      "Server Traffic In": fmtBytes(eh.totalBytesIn || 0),
      "Server Traffic Out": fmtBytes(eh.totalBytesOut || 0),
      "Uptime": fmtTime(eh.uptime_seconds || 0),
      "RSS RAM": fmtBytes(eh.memory?.rss || 0),
      "Heap Used": fmtBytes(eh.memory?.heapUsed || 0),
      "Heap Total": fmtBytes(eh.memory?.heapTotal || 0),
      "External": fmtBytes(eh.memory?.external || 0),
      "Array Buffers": fmtBytes(eh.memory?.arrayBuffers || 0),
      "Memory Cleanups": eh.memoryCleanupCount ?? 0,
      "Cleanup / Hour": eh.uptime_seconds
        ? ((Number(eh.memoryCleanupCount || 0) / Math.max(1, Number(eh.uptime_seconds || 0) / 3600)).toFixed(2) + "/h")
        : "0.00/h",
      "Soft Limit": (eh.memoryLimits?.softLimitMB ?? "-") + " MB",
      "Hard Limit": (eh.memoryLimits?.hardLimitMB ?? "-") + " MB",
      "Max Inflight": eh.memoryLimits?.maxInflight ?? "-",
      "Max Sockets": eh.memoryLimits?.maxSockets ?? "-",
      "Max Free Sockets": eh.memoryLimits?.maxFreeSockets ?? "-",
      "HTTP Sockets": eh.agent?.httpSockets ?? "-",
      "HTTP Free Sockets": eh.agent?.httpFreeSockets ?? "-",
      "HTTPS Sockets": eh.agent?.httpsSockets ?? "-",
      "HTTPS Free Sockets": eh.agent?.httpsFreeSockets ?? "-"
    }, true);
  } else {
    setHTML("exitNodeStatus", `<span class="pill bad">Server: OFFLINE / LOCKED</span>`);
    kv("exitNodeStats", {
      "Last Check": ehAge === null ? "Never" : ehAge + "s ago",
      "Error": ehErr
    }, true);
  }

  setHTML("diagnosticSignals", [
    healthBox("Exit Node", exitOnline ? "ONLINE" : "OFFLINE", ehErr, exitOnline ? "good" : "bad"),
    healthBox("H2", h2Text, "Live " + (c.h2_live_connections ?? "-") + " / " + (c.h2_connections ?? "-"), h2State),
    healthBox("H2 Healer", c.h2_healer_enabled ? "ON" : "OFF", "Delay " + (c.h2_healer_delay_seconds ?? "-") + "s · cooldown " + (c.h2_healer_cooldown_seconds ?? "-") + "s", c.h2_healer_enabled ? "good" : "bad"),
    healthBox("Error Rate", errRateText, visibleErrors + " visible errors", errSt),
    healthBox("Front Health", frontStateFromStats(c)[0], "Recent timeouts: " + (c.front_ip_recent_timeouts || 0), frontStateFromStats(c)[1]),
    healthBox("SABR", sabrOn ? "ON" : "OFF", "Success rate: " + (s.sabr_success_rate ?? "-"), sabrOn ? "good" : "bad"),
    healthBox("Runtime Baseline", fmtTime(s.runtime_baseline_age_seconds || 0), s.runtime_baseline_reason || "-", "info"),
    healthBox("Blacklisted Scripts", c.blacklisted_scripts ?? 0, "Disabled scripts: " + (c.disabled_scripts_count ?? "-"), Number(c.blacklisted_scripts || 0) > 0 ? "warn" : "good"),
    healthBox("Quota", String(s.runtime_quota_used || 0), "Session runtime quota", "purple")
  ].join(""));

  kv("systemStats", {
    "PID": s.system?.pid || "-",
    "Process RAM": fmtBytes(s.system?.rss || 0),
    "Threads": s.system?.threads || "-",
    "CPU %": s.system?.cpu_percent ?? "-"
  });

  raw("recentErrors", (lastLogs.full_errors || lastLogs.errors || []).slice(-80).join("\n") || "No errors");
  raw("rawStats", s);

  const scriptRows = (s.script_ids || []).map((x) => {
    const pct = Number(x.quota_percent || 0);
    const totalErrors = Number(x.errors || 0);
    const runtimeErrors = Number(x.runtime_errors || 0);
    const runtimeReq = Number(x.runtime_requests || 0);
    const runtimeRate = Number(x.runtime_error_rate || 0);
    const statusRaw = String(x.last_status || "OK");
    const status = statusRaw.toLowerCase();

    const statusOk = (!status || status === "ok" || status === "200" || status === "success" || status === "healthy");
    const runtimeBad = runtimeReq >= 20 && runtimeErrors >= 10 && runtimeRate >= 35;
    const runtimeWarn = !runtimeBad && runtimeReq >= 10 && runtimeErrors > 0;
    const bad = !!x.rate_limited || pct >= 95 || !statusOk || runtimeBad;
    const warn = !bad && (pct >= 80 || runtimeWarn);
    const cls = bad ? "bad" : warn ? "warn" : "good";

    const rtCls = runtimeBad ? "cellBad" : runtimeErrors > 0 ? "cellWarn" : "cellGood";
    const totalErrCls = totalErrors >= 50 ? "cellWarn" : totalErrors > 0 ? "cellWarn" : "cellGood";
    const rtText = runtimeReq > 0 ? `${runtimeErrors}/${runtimeReq} (${runtimeRate.toFixed(1)}%)` : "0/0";

    return `<tr class="${cls}">
      <td>${esc(x.script || x.short_id || "-")}</td>
      <td>${x.requests || 0}/${x.limit || 0}</td>
      <td>${x.quota_percent || 0}%</td>
      <td>${fmtBytes(x.bytes)}</td>
      <td class="${rtCls}">${rtText}</td>
      <td class="${totalErrCls}">${totalErrors}</td>
      <td>${x.rate_limited ? "LIMIT" : esc(statusRaw || "OK")}</td>
    </tr>`;
  });

  setHTML("scripts", table(["Script", "Used", "%", "Data", "Runtime Err", "Total Err", "Status"], scriptRows));

  const hostRows = (s.top_hosts || []).map((h) => {
    const bad = Number(h.errors || 0) > 0;
    return `<tr class="${bad ? "bad" : ""}">
      <td>${esc(h.host || "-")}</td>
      <td>${h.requests || 0}</td>
      <td>${fmtBytes(h.bytes)}</td>
      <td>${h.errors || 0}</td>
    </tr>`;
  });

  setHTML("hosts", table(["Host", "Req", "Data", "Errors"], hostRows));

  renderAdvancedConfig(s);

  if (activeTab === "optimizer" && optimizerLoaded) {
    optimizerPaintTopBadge(optimizerState);
  } else {
    optimizerPaintTopBadge({
      script_count: c.script_count || c.script_ids_count || 0,
      active_tier: c.optimized_for_script_count || c.script_tier || 0,
      config_changed: c.config_changed,
      optimizer_config_changed: c.optimizer_config_changed,
      optimizer_config_drift: c.optimizer_config_drift
    });
  }
}

/* ----------------------------- Refresh Loop ----------------------------- */

async function refresh() {
  try {
    const a = fetch("/stats", { cache: "no-store" });
    const b = logsPaused ? Promise.resolve(null) : fetch("/logs", { cache: "no-store" });

    const [statsResp, logsResp] = await Promise.all([a, b]);

    if (!statsResp.ok) throw new Error("stats HTTP " + statsResp.status);

    const s = await statsResp.json();

    if (logsResp) {
      if (!logsResp.ok) throw new Error("logs HTTP " + logsResp.status);
      const logs = await logsResp.json();
      lastLogs = Object.assign({
        live: [],
        errors: [],
        full_errors: [],
        script_errors: [],
        downloads: [],
        video: [],
        sabr: [],
        h2: []
      }, logs || {});

      if (!lastLogs.full_errors || !lastLogs.full_errors.length) {
        lastLogs.full_errors = lastLogs.errors || [];
      }
      lastLogs.errors = lastLogs.full_errors;
    }

    render(s);

    if (activeTab === "logs") renderLogs();

    setText("liveText", t("status.live", "Live") + " · " + new Date().toLocaleTimeString());

    const statusEl = $("status");
    if (statusEl) statusEl.className = "liveMini";

    const dot = $("liveDot");
    if (dot) dot.className = "dot";
  } catch (e) {
    setText("liveText", t("status.disconnected", "Disconnected") + ": " + String(e));

    const statusEl = $("status");
    if (statusEl) statusEl.className = "liveMini";

    const dot = $("liveDot");
    if (dot) dot.className = "dot bad";
  }
}

/* ----------------------------- Init ----------------------------- */

function initDashboard() {
  applyLang();
  setViewMode(viewMode);
  setTab(activeTab);
  setLogTab(activeLog);

  optimizerEnsureTopBadge();

  refresh();
  refreshAdvisor(true);

  setInterval(refresh, 1000);
  setInterval(() => refreshAdvisor(false), 60000);

  setInterval(() => {
    optimizerFetchStatus()
      .then((j) => {
        optimizerState = j;
        if (!optimizerLoaded) optimizerIds = optimizerNormalizeIds(j.script_ids || []);
        optimizerPaintTopBadge(j);

        if (activeTab === "optimizer" && optimizerLoaded) renderOptimizer();
      })
      .catch(() => {});
  }, 5000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initDashboard);
} else {
  initDashboard();
}

/* Export for inline onclick handlers */
window.setLang = setLang;
window.toggleLang = toggleLang;
window.setTab = setTab;
window.setLogTab = setLogTab;
window.setCfgTab = setCfgTab;
window.setViewMode = setViewMode;
window.toggleSettings = toggleSettings;
window.setMode = setMode;
window.openSuggestions = openSuggestions;
window.toggleFeature = toggleFeature;
window.clearLog = clearLog;
window.clearActiveLog = clearActiveLog;
window.copyActiveLog = copyActiveLog;
window.togglePauseLogs = togglePauseLogs;
window.loadScriptOptimizer = loadScriptOptimizer;
window.saveScriptOptimizer = saveScriptOptimizer;
window.optimizerParseIds = optimizerParseIds;
window.optimizerSetDraft = optimizerSetDraft;
window.optimizerAddId = optimizerAddId;
window.optimizerRemoveId = optimizerRemoveId;
window.optimizerTogglePaste = optimizerTogglePaste;
window.optimizerImportBulk = optimizerImportBulk;
window.optimizerCleanIds = optimizerCleanIds;
window.optimizerSelectTier = optimizerSelectTier;
window.refresh = refresh;