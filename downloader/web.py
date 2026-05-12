import json
import os
import subprocess
import sys
import logging
import mimetypes

from urllib.parse import urlparse, parse_qs

from downloader.gas_downloader import DownloadManager

# MHR_DOWNLOADER_HANDLER_COMPAT_FIX
def _reply_html(handler, html, status=200):
    fn = getattr(handler, "_send_html", None) or getattr(handler, "send_html", None)
    if fn is None:
        raise RuntimeError("handler has no html response method")
    return fn(html, status) if fn.__name__ == "send_html" else fn(html)

def _reply_json(handler, data, status=200):
    fn = getattr(handler, "_send_json", None) or getattr(handler, "send_json", None)
    if fn is None:
        raise RuntimeError("handler has no json response method")
    return fn(data, status)



DOWNLOADER_HTML = r"""<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<title>MHR Downloader</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#05070d;--card:#0d111ccc;--card2:#111827cc;--line:#243044;
  --text:#e8eef8;--muted:#93a4bb;--cyan:#22d3ee;--green:#34d399;--red:#fb7185;
  --yellow:#fbbf24;--purple:#a78bfa;--shadow:0 22px 70px #0008;
  --font-ui:Inter,"Segoe UI",Roboto,Arial,sans-serif;
  --font-fa:Vazirmatn,Vazir,IRANSans,"Segoe UI",Tahoma,Arial,sans-serif;
  --font-mono:"Cascadia Mono",Consolas,monospace;
}
*{box-sizing:border-box}
body{
  margin:0;
  color:var(--text);
  font-family:var(--font-ui);
  background:
    radial-gradient(circle at 8% -10%,#2563eb55,transparent 32%),
    radial-gradient(circle at 92% 0,#7c3aed44,transparent 30%),
    linear-gradient(180deg,#080b13,var(--bg));
}
html[lang="fa"] body{
  font-family:var(--font-fa);
  direction:rtl;
  text-align:right;
}
html[lang="en"] body{
  font-family:var(--font-ui);
  direction:ltr;
  text-align:left;
}
header{
  position:sticky;top:0;z-index:20;background:#080b13d9;backdrop-filter:blur(16px);
  border-bottom:1px solid #ffffff12;padding:15px 18px;
  display:flex;justify-content:space-between;align-items:center;gap:12px;
}
h1{margin:0;font-size:20px;letter-spacing:-.03em}
html[lang="fa"] h1{letter-spacing:0}
a{color:#67e8f9;text-decoration:none;font-weight:900}
.headerLinks{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
main{padding:16px;max-width:1550px;margin:auto}
.card{
  background:linear-gradient(180deg,var(--card),var(--card2));
  border:1px solid #ffffff12;border-radius:24px;padding:16px;margin-bottom:14px;
  box-shadow:var(--shadow);backdrop-filter:blur(18px)
}
button,input{
  padding:11px 13px;border-radius:14px;border:1px solid #263449;background:#030712;color:var(--text);
  outline:none;font-family:inherit
}
input{width:100%;min-width:0}
input:focus{border-color:#60a5fa88;box-shadow:0 0 0 3px #3b82f622}
button{cursor:pointer;font-weight:900;transition:.15s;white-space:nowrap}
button:hover{background:#111827;transform:translateY(-1px)}
button.primary{background:linear-gradient(135deg,#2563eb,#7c3aed);border-color:#93c5fd}
button.danger{border-color:#7f1d1d;color:#fecaca;background:#22070dcc}
button.good{border-color:#166534;color:#86efac;background:#052e1a99}
button.warn{border-color:#854d0e;color:#fde68a;background:#211505cc}
button.on{background:linear-gradient(135deg,#059669,#34d399);border-color:#86efac;color:#04130a}
button.off{background:#22070dcc;border-color:#7f1d1d;color:#fecaca}
.langMini{
  background:#111827!important;border:1px solid #ffffff20!important;color:white!important;
  border-radius:999px!important;padding:10px 13px!important;font-weight:950!important
}
.hero{display:flex;flex-direction:column;gap:13px}
.formGrid{
  display:grid;grid-template-columns:2fr 1.25fr .7fr auto;gap:10px;align-items:stretch
}
.field,.infoBlock{
  background:#030712;border:1px solid #263449;border-radius:16px;padding:10px;min-width:0
}
.field label,.infoTitle,.label{
  display:block;color:var(--muted);font-size:11px;font-weight:950;
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:7px
}
html[lang="fa"] .field label,
html[lang="fa"] .infoTitle,
html[lang="fa"] .label{letter-spacing:0}
.help,.hint{
  color:var(--muted);font-size:12px;line-height:1.55;margin-top:7px
}
.actionField{min-width:120px}
.fullBtn{width:100%;min-height:42px}
.h2box{
  display:flex;justify-content:space-between;align-items:center;gap:12px;background:#08101f;
  border:1px solid #1f2937;border-radius:18px;padding:13px;flex-wrap:wrap
}
.queueControls{
  background:#08101f;border:1px solid #1f2937;border-radius:18px;padding:13px
}
.controlHeader{
  display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:12px
}
.controlHeader h2{margin:0 0 4px;font-size:17px}
.controlButtons{display:flex;gap:8px;flex-wrap:wrap}
.controlGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.topstats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.metric{background:#08101f;border:1px solid #1f2937;border-radius:16px;padding:12px;min-height:76px}
.value{font-size:20px;font-weight:950;margin-top:7px;word-break:break-all}
.job{border:1px solid #ffffff12;background:#08101faa;border-radius:22px;padding:15px;margin:13px 0}
.jobhead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
.name{font-weight:950;font-size:16px;word-break:break-all}
.url{font-size:12px;color:var(--muted);word-break:break-all;margin-top:5px}
.status{display:inline-flex;padding:6px 10px;border-radius:999px;background:#111827;border:1px solid #263449;font-size:12px;font-weight:950}
.done{color:var(--green)}.error{color:var(--red)}.downloading{color:var(--cyan)}.paused{color:var(--yellow)}.starting,.probing,.queued,.scheduled{color:var(--purple)}
.bar{height:14px;background:#111827;border-radius:999px;overflow:hidden;margin:13px 0;border:1px solid #ffffff10}
.fill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--green));transition:width .25s}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:10px 0}
.meta{color:var(--muted);font-size:13px;line-height:1.7;word-break:break-all}
.logs{
  height:260px;
  overflow-y:auto;
  overflow-x:hidden;
  background:#020617;
  border:1px solid #1f2937;
  border-radius:16px;
  padding:10px;
  margin-top:12px;
  scroll-behavior:auto;
  direction:ltr;
  text-align:left;
  overscroll-behavior:contain;
  scrollbar-width:thin;
  scrollbar-color:#334155 #020617;
}
.logs::-webkit-scrollbar{width:10px}
.logs::-webkit-scrollbar-track{background:#020617;border-radius:999px}
.logs::-webkit-scrollbar-thumb{background:#334155;border-radius:999px;border:2px solid #020617}
.logs::-webkit-scrollbar-thumb:hover{background:#475569}
.log{
  font-family:var(--font-mono);
  font-size:12px;
  line-height:1.55;
  border:1px solid #111827;
  border-left:4px solid #334155;
  background:#0b1220;
  border-radius:10px;
  padding:7px 9px;
  margin:5px 0;
  color:#cbd5e1;
  white-space:pre-wrap;
  word-break:break-word;
}
.log.ERROR{color:#fecaca;border-left-color:#fb7185;background:#2a0b14}
.log.WARN{color:#fde68a;border-left-color:#fbbf24;background:#251a05}
.log.DONE{color:#86efac;border-left-color:#34d399;background:#052e1a}
.log.INFO{color:#cbd5e1;border-left-color:#38bdf8;background:#071527}
.logTime{color:#94a3b8}
.logLevel{font-weight:950;margin:0 5px}
.logMsg{color:inherit}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}
.empty{color:var(--muted);padding:25px;text-align:center;border:1px dashed #334155;border-radius:18px}
.modal{display:none;position:fixed;inset:0;background:#000b;z-index:100;align-items:center;justify-content:center;padding:20px}
.modal.on{display:flex}
.player{width:min(1100px,96vw);background:#05070d;border:1px solid #334155;border-radius:22px;padding:14px;box-shadow:0 30px 90px #000}
.player video{width:100%;max-height:78vh;border-radius:14px;background:#000}
.playerTop{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:10px}
html[lang="fa"] header,
html[lang="fa"] .headerLinks,
html[lang="fa"] .formGrid,
html[lang="fa"] .h2box,
html[lang="fa"] .controlHeader,
html[lang="fa"] .controlButtons,
html[lang="fa"] .controlGrid,
html[lang="fa"] .topstats,
html[lang="fa"] .grid,
html[lang="fa"] .jobhead,
html[lang="fa"] .tools,
html[lang="fa"] .playerTop{direction:rtl}
html[lang="fa"] input{text-align:right}
html[lang="en"] input{text-align:left}
@media(max-width:1150px){
  .formGrid{grid-template-columns:1fr 1fr}
  .actionField{grid-column:span 2}
  .controlGrid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:1000px){.grid,.topstats{grid-template-columns:repeat(2,1fr)}}
@media(max-width:680px){
  main{padding:10px}
  .formGrid,.controlGrid,.grid,.topstats{grid-template-columns:1fr}
  .actionField{grid-column:auto}
}
</style>
</head>
<body>
<header>
  <h1 data-i18n="title">MHR Python Downloader</h1>
  <div class="headerLinks">
    <a href="/" data-i18n="dashboard">Dashboard</a>
    <button class="langMini" type="button" id="langBtn">FA</button>
  </div>
</header>

<main>
<div class="card hero">
  <div class="formGrid">
    <div class="field">
      <label for="url" data-i18n="download_url">Download URL</label>
      <input id="url" data-i18n-placeholder="url_placeholder" placeholder="Paste direct or generated download URL...">
      <div class="help" data-i18n="url_help">Direct file link or generated temporary URL. This is the main download address.</div>
    </div>

    <div class="field">
      <label for="scheduleInput" data-i18n="start_time">Start time</label>
      <input id="scheduleInput" type="datetime-local">
      <div class="help" data-i18n="start_time_help">Optional. Empty means start as soon as the queue allows.</div>
    </div>

    <div class="field">
      <label for="priorityInput" data-i18n="queue_priority">Queue priority</label>
      <input id="priorityInput" type="number" min="0" max="999" value="0">
      <div class="help" data-i18n="priority_help">Higher number starts earlier. Use 0 for normal priority.</div>
    </div>

    <div class="field actionField">
      <label>&nbsp;</label>
      <button class="primary fullBtn" onclick="add()" data-i18n="download">Download</button>
      <div class="help" data-i18n="download_help">Adds this link to the downloader queue.</div>
    </div>
  </div>

  <div class="h2box">
    <div>
      <b><span data-i18n="h2_title">HTTP/2 Downloader Acceleration</span> <span style="color:#fbbf24" data-i18n="experimental">(Experimental)</span></b>
      <div class="hint" data-i18n="h2_help">
        H2 can be faster on some links because it reuses multiplexed connections, especially for responsive CDNs or smaller chunks.
        H1 is usually more predictable and is the safer fallback when H2 shows stream timeouts, reconnects, or repeated retries.
        Try H2 for speed; switch it off if you see instability.
      </div>
    </div>
    <button id="h2Toggle" class="off" onclick="toggleH2()">H2: OFF</button>
  </div>

  <div class="queueControls">
    <div class="controlHeader">
      <div>
        <h2 data-i18n="download_controls">Download Controls</h2>
        <div class="hint" data-i18n="controls_help">Queue and global downloader settings. These controls affect all downloads, not just one file.</div>
      </div>
      <div class="controlButtons">
        <button class="good" onclick="resumeAll()" data-i18n="resume_all">Resume All</button>
        <button class="warn" onclick="pauseAll()" data-i18n="pause_all">Pause All</button>
        <button onclick="saveDownloaderSettings()" data-i18n="save">Save</button>
      </div>
    </div>

    <div class="controlGrid">
      <div class="field">
        <label for="maxActiveInput" data-i18n="max_active">Max active downloads</label>
        <input id="maxActiveInput" type="number" min="1" max="20" value="2">
        <div class="help" data-i18n="max_active_help">How many downloads can run at the same time. Example: 2 means two active downloads, the rest wait in queue.</div>
      </div>

      <div class="field">
        <label for="speedLimitInput" data-i18n="speed_limit">Global speed limit</label>
        <input id="speedLimitInput" type="number" min="0" step="0.1" placeholder="0 = unlimited">
        <div class="help" data-i18n="speed_limit_help">MB/s limit for the whole downloader. 0 means unlimited.</div>
      </div>

      <div class="infoBlock">
        <div class="infoTitle" data-i18n="auto_retry">Auto retry failed chunks</div>
        <div class="help" data-i18n="auto_retry_help">If a chunk fails, downloader retries it automatically. Controlled by downloader_auto_retry_rounds in config.</div>
      </div>

      <div class="infoBlock">
        <div class="infoTitle" data-i18n="verify_size">Verify final file size</div>
        <div class="help" data-i18n="verify_size_help">After completion, downloader checks the final file size to detect broken or incomplete downloads.</div>
      </div>
    </div>
  </div>

  <div class="topstats">
    <div class="metric"><div class="label" data-i18n="active">Active</div><div class="value" id="sumActive">0</div></div>
    <div class="metric"><div class="label" data-i18n="done">Done</div><div class="value" id="sumDone">0</div></div>
    <div class="metric"><div class="label" data-i18n="total_speed">Total Speed</div><div class="value" id="sumSpeed">0 B/s</div></div>
    <div class="metric"><div class="label" data-i18n="quota_used">Quota Used</div><div class="value" id="sumQuota">0</div></div>
  </div>
</div>

<div class="card">
  <h2 data-i18n="downloads">Downloads</h2>
  <div id="jobs"></div>
</div>
</main>

<div class="modal" id="modal" onclick="closePlayer(event)">
  <div class="player" onclick="event.stopPropagation()">
    <div class="playerTop">
      <b id="playerTitle" data-i18n="player">Player</b>
      <button onclick="closePlayer()" data-i18n="close">Close</button>
    </div>
    <video id="videoPlayer" controls autoplay></video>
  </div>
</div>

<script>
const I18N = {
  en:{
    title:"MHR Python Downloader",dashboard:"Dashboard",
    download_url:"Download URL",url_placeholder:"Paste direct or generated download URL...",
    url_help:"Direct file link or generated temporary URL. This is the main download address.",
    start_time:"Start time",start_time_help:"Optional. Empty means start as soon as the queue allows.",
    queue_priority:"Queue priority",priority_help:"Higher number starts earlier. Use 0 for normal priority.",
    download:"Download",download_help:"Adds this link to the downloader queue.",
    h2_title:"HTTP/2 Downloader Acceleration",experimental:"(Experimental)",
    h2_help:"H2 can be faster on some links because it reuses multiplexed connections, especially for responsive CDNs or smaller chunks. H1 is usually more predictable and is the safer fallback when H2 shows stream timeouts, reconnects, or repeated retries. Try H2 for speed; switch it off if you see instability.",
    download_controls:"Download Controls",controls_help:"Queue and global downloader settings. These controls affect all downloads, not just one file.",
    resume_all:"Resume All",pause_all:"Pause All",save:"Save",
    max_active:"Max active downloads",max_active_help:"How many downloads can run at the same time. Example: 2 means two active downloads, the rest wait in queue.",
    speed_limit:"Global speed limit",speed_limit_help:"MB/s limit for the whole downloader. 0 means unlimited.",
    auto_retry:"Auto retry failed chunks",auto_retry_help:"If a chunk fails, downloader retries it automatically. Controlled by downloader_auto_retry_rounds in config.",
    verify_size:"Verify final file size",verify_size_help:"After completion, downloader checks the final file size to detect broken or incomplete downloads.",
    active:"Active",done:"Done",total_speed:"Total Speed",quota_used:"Quota Used",
    downloads:"Downloads",no_downloads:"No downloads yet",
    progress:"Progress",speed:"Speed",eta:"ETA",finished_in:"Finished In",elapsed:"Elapsed",quota:"Quota",downloaded:"Downloaded",total:"Total",chunks:"Chunks",errors:"Errors",
    path:"Path",relay_requests:"Relay requests",relay_rx:"Relay RX",relay_tx:"Relay TX",chunk:"Chunk",parallel:"Parallel",
    priority:"Priority",scheduled:"Scheduled",limit:"Limit",unlimited:"Unlimited",error:"Error",
    pause:"Pause",resume:"Resume",cancel:"Cancel",play:"Play",open_folder:"Open Folder",remove:"Remove from List",delete_disk:"Delete from Disk",
    player:"Player",close:"Close",no_logs:"No logs",
    h2_on:"H2: ON",h2_off:"H2: OFF",h2_enabled:"H2 ENABLED",h2_disabled:"H2 DISABLED",
    h2_on_title:"HTTP/2 downloader is enabled",h2_off_title:"HTTP/2 downloader is disabled",
    delete_confirm:"Delete downloaded file/part file from disk?",remove_confirm:"Remove this item from the list?",cancel_confirm:"Cancel this download?",
    complete:"Download complete",failed:"failed",
    status:{starting:"starting",probing:"probing",downloading:"downloading",paused:"paused",done:"done",error:"error",cancelled:"cancelled",queued:"queued",scheduled:"scheduled"}
  },
  fa:{
    title:"دانلودر پایتون MHR",dashboard:"داشبورد",
    download_url:"لینک دانلود",url_placeholder:"لینک مستقیم یا تولیدشده دانلود را وارد کن...",
    url_help:"لینک مستقیم فایل یا لینک موقت تولیدشده را وارد کن. این آدرس اصلی دانلود است.",
    start_time:"زمان شروع",start_time_help:"اختیاری است. اگر خالی باشد، دانلود به‌محض آزاد بودن صف شروع می‌شود.",
    queue_priority:"اولویت صف",priority_help:"عدد بالاتر زودتر شروع می‌شود. عدد ۰ یعنی اولویت عادی.",
    download:"دانلود",download_help:"این لینک را به صف دانلود اضافه می‌کند.",
    h2_title:"شتاب‌دهی دانلودر با HTTP/2",experimental:"(آزمایشی)",
    h2_help:"H2 روی بعضی لینک‌ها می‌تواند سریع‌تر باشد، چون از اتصال‌های multiplexed استفاده می‌کند؛ مخصوصاً برای CDNهای سریع یا چانک‌های کوچک‌تر. H1 معمولاً قابل‌پیش‌بینی‌تر است و وقتی H2 تایم‌اوت استریم، reconnect یا retry زیاد می‌دهد گزینه امن‌تری است. برای سرعت H2 را امتحان کن؛ اگر ناپایداری دیدی خاموشش کن.",
    download_controls:"کنترل‌های دانلود",controls_help:"تنظیمات صف و دانلودر کلی. این گزینه‌ها روی همه دانلودها اثر دارند، نه فقط یک فایل.",
    resume_all:"ادامه همه",pause_all:"مکث همه",save:"ذخیره",
    max_active:"حداکثر دانلود همزمان",max_active_help:"تعداد دانلودهایی که همزمان اجرا می‌شوند. مثال: ۲ یعنی دو دانلود فعال باشند و بقیه در صف بمانند.",
    speed_limit:"محدودیت سرعت کلی",speed_limit_help:"محدودیت سرعت کل دانلودر بر حسب MB/s. عدد ۰ یعنی نامحدود.",
    auto_retry:"تلاش مجدد خودکار چانک‌های ناموفق",auto_retry_help:"اگر یک چانک ناموفق شود، دانلودر خودکار دوباره تلاش می‌کند. مقدار آن از downloader_auto_retry_rounds در config کنترل می‌شود.",
    verify_size:"بررسی اندازه نهایی فایل",verify_size_help:"بعد از پایان دانلود، اندازه فایل نهایی بررسی می‌شود تا دانلود خراب یا ناقص مشخص شود.",
    active:"فعال",done:"تمام‌شده",total_speed:"سرعت کل",quota_used:"سهمیه مصرف‌شده",
    downloads:"دانلودها",no_downloads:"هنوز دانلودی وجود ندارد",
    progress:"پیشرفت",speed:"سرعت",eta:"زمان باقی‌مانده",finished_in:"اتمام در",elapsed:"زمان سپری‌شده",quota:"سهمیه",downloaded:"دانلودشده",total:"کل",chunks:"چانک‌ها",errors:"خطاها",
    path:"مسیر",relay_requests:"درخواست‌های رله",relay_rx:"دریافت رله",relay_tx:"ارسال رله",chunk:"چانک",parallel:"موازی",
    priority:"اولویت",scheduled:"زمان‌بندی",limit:"محدودیت",unlimited:"نامحدود",error:"خطا",
    pause:"مکث",resume:"ادامه",cancel:"لغو",play:"پخش",open_folder:"باز کردن پوشه",remove:"حذف از لیست",delete_disk:"حذف از دیسک",
    player:"پلیر",close:"بستن",no_logs:"لاگی وجود ندارد",
    h2_on:"H2: روشن",h2_off:"H2: خاموش",h2_enabled:"H2 روشن",h2_disabled:"H2 خاموش",
    h2_on_title:"دانلودر HTTP/2 روشن است",h2_off_title:"دانلودر HTTP/2 خاموش است",
    delete_confirm:"فایل دانلودشده یا فایل موقت از دیسک حذف شود؟",remove_confirm:"این مورد از لیست حذف شود؟",cancel_confirm:"این دانلود لغو شود؟",
    complete:"دانلود کامل شد",failed:"ناموفق",
    status:{starting:"در حال شروع",probing:"در حال بررسی",downloading:"در حال دانلود",paused:"متوقف",done:"تمام‌شده",error:"خطا",cancelled:"لغوشده",queued:"در صف",scheduled:"زمان‌بندی‌شده"}
  }
};

let previousDone = {};
let h2Enabled = false;
let userPinnedLog = {};
let savedLogScroll = {};
let firstRender = true;
let lastSettingsPaint = 0;

function normalizeLang(v){
  v = String(v || "").toLowerCase();
  return v.startsWith("fa") ? "fa" : v.startsWith("en") ? "en" : "";
}

function lang(){
  let urlLang = "";
  try{
    urlLang = normalizeLang(new URLSearchParams(location.search).get("lang"));
  }catch(e){}

  const pageLang = normalizeLang(localStorage.getItem("mhr.downloader.lang"));
  const htmlLang = normalizeLang(document.documentElement.getAttribute("lang"));
  const globalLang = normalizeLang(localStorage.getItem("mhr.lang") || localStorage.getItem("mhr.language"));

  return urlLang || pageLang || htmlLang || globalLang || "en";
}
function t(k){
  const l = lang();
  return (I18N[l] && I18N[l][k]) || I18N.en[k] || k;
}
function statusText(s){
  s = String(s || "").toLowerCase();
  return (I18N[lang()].status && I18N[lang()].status[s]) || s;
}
function setLang(next){
  next = normalizeLang(next) || "en";

  localStorage.setItem("mhr.downloader.lang", next);
  localStorage.setItem("mhr.lang", next);
  localStorage.setItem("mhr.language", next);

  const u = new URL(location.href);
  u.searchParams.set("lang", next);
  history.replaceState(null, "", u.toString());

  applyStaticI18n();
  refresh();
}
function applyStaticI18n(){
  const l = lang();
  try{
    localStorage.setItem("mhr.downloader.lang", l);
  }catch(e){}
  document.documentElement.lang = l;
  document.documentElement.dir = l === "fa" ? "rtl" : "ltr";
  document.body.dir = l === "fa" ? "rtl" : "ltr";
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
  });
  const btn = document.getElementById("langBtn");
  if(btn){
    btn.textContent = l === "fa" ? "EN" : "FA";
    btn.title = l === "fa" ? "English" : "فارسی";
  }
  const h2Btn = document.getElementById("h2Toggle");
  if(h2Btn){
    h2Btn.textContent = h2Enabled ? t("h2_on") : t("h2_off");
    h2Btn.title = h2Enabled ? t("h2_on_title") : t("h2_off_title");
  }
}
document.getElementById("langBtn").addEventListener("click", () => setLang(lang() === "fa" ? "en" : "fa"));

function fmt(n){
  n=Number(n||0);
  let u=["B","KB","MB","GB","TB"],i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++}
  return n.toFixed(1)+" "+u[i]
}
function cls(s){return String(s||"").toLowerCase()}
function esc(s){return String(s ?? "").replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[m]))}
function isVideo(name){return /\.(mp4|webm|mkv|mov|m4v|avi|ts)$/i.test(String(name||""))}

function jobRouteText(j){
  const logs = j.logs || [];
  const hit = logs.find(l => String(l.message || "").includes("Downloader relay route:"));
  if(!hit) return h2Enabled ? t("h2_enabled") : t("h2_disabled");

  const msg = String(hit.message || "");
  if(msg.includes("H2 preferred")) return "H2 USED";
  if(msg.includes("H1 forced")) return "H1 USED";
  return h2Enabled ? t("h2_enabled") : t("h2_disabled");
}

function jobRouteClass(j){
  const txt = jobRouteText(j);
  return txt.includes("H2") ? "done" : "error";
}


function rememberLogPositions(){
  document.querySelectorAll(".logs").forEach(box=>{
    const id=box.dataset.id;
    const nearBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 80;
    userPinnedLog[id] = nearBottom;
    savedLogScroll[id] = {
      top: box.scrollTop,
      height: box.scrollHeight,
      nearBottom
    };
  });
}

function restoreLogPositions(){
  document.querySelectorAll(".logs").forEach(box=>{
    const id = box.dataset.id;
    const prev = savedLogScroll[id];

    if(firstRender || !prev || prev.nearBottom){
      box.scrollTop = box.scrollHeight;
      return;
    }

    // Keep the same visual position even if new log lines were appended.
    const delta = box.scrollHeight - prev.height;
    box.scrollTop = Math.max(0, prev.top + delta);
  });
  firstRender=false;
}
async function add(){
  const url=document.getElementById("url").value.trim();
  if(!url)return;
  const schedule_at=document.getElementById("scheduleInput").value || "";
  const priority=Number(document.getElementById("priorityInput").value || 0);
  const r=await fetch("/api/downloader/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,schedule_at,priority})});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  document.getElementById("url").value="";
  refresh();
}
async function toggleH2(){
  const r=await fetch("/api/downloader/toggle-h2",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:!h2Enabled})});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  refresh();
}
async function pauseAll(){
  const r=await fetch("/api/downloader/pause-all",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  refresh();
}
async function resumeAll(){
  const r=await fetch("/api/downloader/resume-all",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  refresh();
}
async function saveDownloaderSettings(){
  const max_active=Number(document.getElementById("maxActiveInput").value || 2);
  const speedLimitMB=Number(document.getElementById("speedLimitInput").value || 0);
  const speed_limit_bps=Math.max(0, Math.floor(speedLimitMB * 1024 * 1024));
  const r=await fetch("/api/downloader/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({max_active,speed_limit_bps})});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  refresh();
}
async function act(id,action){
  let msg=null;
  if(action==="delete-file") msg=t("delete_confirm");
  if(action==="remove") msg=t("remove_confirm");
  if(action==="cancel") msg=t("cancel_confirm");
  if(msg && !confirm(msg)) return;
  const r=await fetch("/api/downloader/"+action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});
  const j=await r.json();
  if(!j.ok) alert(j.error||t("failed"));
  refresh();
}
function playFile(id, filename){
  const v=document.getElementById("videoPlayer");
  document.getElementById("playerTitle").textContent=filename || t("player");
  v.src="/api/downloader/file?id="+encodeURIComponent(id)+"&t="+Date.now();
  document.getElementById("modal").classList.add("on");
  v.play().catch(()=>{});
}
function closePlayer(){
  const m=document.getElementById("modal");
  const v=document.getElementById("videoPlayer");
  v.pause();v.removeAttribute("src");v.load();m.classList.remove("on");
}
function maybeNotify(j){
  if(j.status==="done" && !previousDone[j.id]){
    previousDone[j.id]=true;
    try{if(Notification.permission==="granted") new Notification(t("complete"), {body:j.filename});}catch(e){}
  }
}
function renderSummary(jobs){
  sumActive.textContent=jobs.filter(j=>["downloading","starting","probing"].includes(j.status)).length;
  sumDone.textContent=jobs.filter(j=>j.status==="done").length;
  sumSpeed.textContent=fmt(jobs.reduce((a,j)=>a+Number(j.speed||0),0))+"/s";
  sumQuota.textContent=jobs.reduce((a,j)=>a+Number(j.quota_used||0),0);
}
function paintSettings(settings){
  const now = Date.now();
  if(now - lastSettingsPaint < 700) return;
  lastSettingsPaint = now;
  const maxActive=document.getElementById("maxActiveInput");
  const speedLimit=document.getElementById("speedLimitInput");
  if(maxActive && document.activeElement !== maxActive && settings.max_active !== undefined) maxActive.value=settings.max_active || 2;
  if(speedLimit && document.activeElement !== speedLimit && settings.speed_limit_bps !== undefined){
    const mb=Number(settings.speed_limit_bps||0)/1024/1024;
    speedLimit.value=mb ? mb.toFixed(1) : 0;
  }
}

function fmtDuration(sec){
  sec = Math.max(0, Math.floor(Number(sec || 0)));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(s).padStart(2,"0");
}
function jobTimeLabel(j){
  if(j.status === "done") return t("finished_in");
  if(j.status === "error" || j.status === "cancelled") return t("elapsed");
  return t("eta");
}
function jobTimeValue(j){
  const started = Number(j.started_at || 0);
  const finished = Number(j.finished_at || 0);
  if(j.status === "done" && started > 0 && finished > 0) return fmtDuration(finished - started);
  if((j.status === "error" || j.status === "cancelled") && started > 0) return fmtDuration((finished || Date.now()/1000) - started);
  return j.eta || "-";
}

function jobHtml(j){
  const p=Number(j.percent||0);
  const logs=(j.logs||[]).slice(-160).map(l=>`<div class="log ${esc(l.level)}"><span class="logTime">[${esc(l.t)}]</span> <span class="logLevel">${esc(l.level)}:</span> <span class="logMsg">${esc(l.message)}</span></div>`).join("");
  const canPlay=j.status==="done" && isVideo(j.filename);
  const scheduled=j.scheduled_at ? new Date(j.scheduled_at*1000).toLocaleString() : "-";
  const limit=j.speed_limit_bps ? fmt(j.speed_limit_bps)+"/s" : t("unlimited");
  return `<div class="job">
    <div class="jobhead">
      <div>
        <div class="name">${esc(j.filename)}</div>
        <div class="url">${esc(j.url||"")}</div>
      </div>
      <div>
        <span class="status ${cls(j.status)}">${esc(statusText(j.status))}</span>
        <span class="status ${jobRouteClass(j)}">
          ${esc(jobRouteText(j))}
        </span>
      </div>
    </div>
    <div class="bar"><div class="fill" style="width:${p}%"></div></div>
    <div class="grid">
      <div class="metric"><div class="label">${t("progress")}</div><div class="value">${p.toFixed(1)}%</div></div>
      <div class="metric"><div class="label">${t("speed")}</div><div class="value">${fmt(j.speed)}/s</div></div>
      <div class="metric"><div class="label">${esc(jobTimeLabel(j))}</div><div class="value">${esc(jobTimeValue(j))}</div></div>
      <div class="metric"><div class="label">${t("quota")}</div><div class="value">${j.quota_used}</div></div>
      <div class="metric"><div class="label">${t("downloaded")}</div><div class="value">${fmt(j.downloaded)}</div></div>
      <div class="metric"><div class="label">${t("total")}</div><div class="value">${fmt(j.total_size)}</div></div>
      <div class="metric"><div class="label">${t("chunks")}</div><div class="value">${j.done_chunks}/${j.total_chunks}</div></div>
      <div class="metric"><div class="label">${t("errors")}</div><div class="value">${j.relay_errors}</div></div>
    </div>
    <div class="meta">
      ${t("path")}: ${esc(j.path)}<br>
      ${t("relay_requests")}: ${j.relay_requests} · ${t("relay_rx")}: ${fmt(j.relay_received_bytes)} · ${t("relay_tx")}: ${fmt(j.relay_sent_bytes)} · ${t("chunk")}: ${fmt(j.chunk_size)} · ${t("parallel")}: ${j.parallel}
      <br>${t("priority")}: ${esc(j.priority ?? 0)} · ${t("scheduled")}: ${esc(scheduled)} · ${t("limit")}: ${esc(limit)}
      ${j.error ? "<br>"+t("error")+": "+esc(j.error) : ""}
    </div>
    <div class="tools">
      <button onclick="act('${j.id}','pause')">${t("pause")}</button>
      <button class="good" onclick="act('${j.id}','resume')">${t("resume")}</button>
      <button class="warn" onclick="act('${j.id}','cancel')">${t("cancel")}</button>
      ${canPlay ? `<button class="primary" onclick="playFile('${j.id}', '${esc(j.filename)}')">${t("play")}</button>` : ""}
      <button onclick="act('${j.id}','open-folder')">${t("open_folder")}</button>
      <button onclick="act('${j.id}','remove')">${t("remove")}</button>
      <button class="danger" onclick="act('${j.id}','delete-file')">${t("delete_disk")}</button>
    </div>
    <div class="logs" data-id="${j.id}">${logs || `<div class="log">${t("no_logs")}</div>`}</div>
  </div>`;
}
async function refresh(){
  rememberLogPositions();
  let data={jobs:[],settings:{}};
  try{
    const r=await fetch("/api/downloader/status",{cache:"no-store"});
    data=await r.json();
  }catch(e){
    return;
  }
  const jobs=data.jobs||[];
  const settings=data.settings||{};
  h2Enabled=!!settings.h2_enabled;
  paintSettings(settings);
  applyStaticI18n();

  const h2Btn=document.getElementById("h2Toggle");
  if(h2Btn){
    h2Btn.textContent=h2Enabled ? t("h2_on") : t("h2_off");
    h2Btn.className=h2Enabled ? "on" : "off";
    h2Btn.title=h2Enabled ? t("h2_on_title") : t("h2_off_title");
  }

  jobs.forEach(maybeNotify);
  renderSummary(jobs);
  const box=document.getElementById("jobs");
  box.innerHTML=jobs.map(jobHtml).join("") || `<div class="empty">${t("no_downloads")}</div>`;
  restoreLogPositions();
}
try{if(Notification.permission==="default") Notification.requestPermission()}catch(e){}
applyStaticI18n();
refresh();
setInterval(refresh,1000);
</script>
</body>
</html>"""


def get_downloader_manager(runtime):
    mgr = runtime.get("downloader")
    if mgr is not None:
        return mgr

    cfg = runtime.get("config") or {}
    mgr = DownloadManager(cfg, download_dir=cfg.get("downloader_dir", "downloads"))
    runtime["downloader"] = mgr
    return mgr


def handle_downloader_get(handler, runtime):
    clean_path = urlparse(handler.path).path

    # MHR_DOWNLOADER_WEB_FIX_V3
    if clean_path in ("/downloader", "/downloader/", "/downloads", "/download"):
        _reply_html(handler, DOWNLOADER_HTML)
        return True

    if clean_path.startswith("/api/downloader/file"):
        return _send_downloader_file(handler, runtime)

    if clean_path == "/api/downloader/status":
        mgr = get_downloader_manager(runtime)
        _reply_json(handler, {"jobs": mgr.snapshot(), "settings": mgr.settings_snapshot() if hasattr(mgr, "settings_snapshot") else {}})
        return True

    return False




def _get_job(runtime, jid):
    mgr = get_downloader_manager(runtime)
    return getattr(mgr, "jobs", {}).get(str(jid))


def _delete_file_for_job(job):
    deleted = []
    for attr in ("path", "part_path", "state_path"):
        path = getattr(job.state, attr, "") or ""
        if path and os.path.exists(path):
            os.remove(path)
            deleted.append(path)
    job.cancel()
    return deleted


def _send_downloader_file(handler, runtime):
    qs = parse_qs(urlparse(handler.path).query)
    jid = (qs.get("id") or [""])[0]
    job = _get_job(runtime, jid)
    if not job:
        _reply_json(handler, {"ok": False, "error": "job not found"}, 404)
        return True

    path = getattr(job.state, "path", "") or ""
    if not path or not os.path.exists(path):
        _reply_json(handler, {"ok": False, "error": "file not found"}, 404)
        return True

    size = os.path.getsize(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    range_header = handler.headers.get("Range", "")

    start = 0
    end = size - 1
    status = 200

    if range_header.startswith("bytes="):
        try:
            part = range_header.split("=", 1)[1].split(",", 1)[0]
            a, _, b = part.partition("-")
            if a:
                start = int(a)
            if b:
                end = int(b)
            end = min(end, size - 1)
            status = 206
        except Exception:
            start = 0
            end = size - 1
            status = 200

    length = max(0, end - start + 1)

    handler.send_response(status)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()

    # Browser/video players often cancel old Range requests when seeking,
    # refreshing, closing the tab, or switching to another byte range.
    # On Windows this shows up as ConnectionResetError [WinError 10054].
    # It is not a server crash; just stop streaming this response quietly.
    try:
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    handler.wfile.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                    return True
                remaining -= len(chunk)
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
        return True

    return True

def _open_folder_for_job(job):
    path = getattr(job.state, "path", "") or getattr(job.state, "part_path", "")
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        return False, "folder not found"

    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True, folder
    except Exception as exc:
        return False, str(exc)


def handle_downloader_post(handler, runtime, data):
    clean_path = urlparse(handler.path).path

    if not clean_path.startswith("/api/downloader/"):
        return False

    mgr = get_downloader_manager(runtime)
    log = logging.getLogger("Main")

    if clean_path == "/api/downloader/add":
        url = str(data.get("url", "")).strip()
        if not url:
            _reply_json(handler, {"ok": False, "error": "missing url"}, 400)
            return True

        scheduled_at = data.get("schedule_at") or data.get("scheduled_at") or 0
        priority = int(data.get("priority", 0) or 0)
        jid = mgr.add(url, scheduled_at=scheduled_at, priority=priority)
        log.info("Downloader add -> %s", url)
        _reply_json(handler, {"ok": True, "id": jid})
        return True

    if clean_path == "/api/downloader/pause":
        mgr.pause(str(data.get("id", "")))
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/resume":
        mgr.resume(str(data.get("id", "")))
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/cancel":
        mgr.cancel(str(data.get("id", "")))
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/remove":
        mgr.remove(str(data.get("id", "")))
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/delete-file":
        jid = str(data.get("id", ""))
        job = getattr(mgr, "jobs", {}).get(jid)
        if not job:
            _reply_json(handler, {"ok": False, "error": "job not found"}, 404)
            return True
        try:
            deleted = _delete_file_for_job(job)
            mgr.remove(jid)
            _reply_json(handler, {"ok": True, "deleted": deleted})
        except Exception as exc:
            _reply_json(handler, {"ok": False, "error": str(exc)}, 500)
        return True

    if clean_path == "/api/downloader/open-folder":
        jid = str(data.get("id", ""))
        job = getattr(mgr, "jobs", {}).get(jid)
        if not job:
            _reply_json(handler, {"ok": False, "error": "job not found"}, 404)
            return True
        ok, result = _open_folder_for_job(job)
        if ok:
            _reply_json(handler, {"ok": True, "folder": result})
        else:
            _reply_json(handler, {"ok": False, "error": result}, 500)
        return True


    # PATCH_DOWNLOADER_FEATURES_API_START
    if clean_path == "/api/downloader/pause-all":
        if hasattr(mgr, "pause_all"):
            mgr.pause_all()
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/resume-all":
        if hasattr(mgr, "resume_all"):
            mgr.resume_all()
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/cancel-all":
        if hasattr(mgr, "cancel_all"):
            mgr.cancel_all()
        _reply_json(handler, {"ok": True})
        return True

    if clean_path == "/api/downloader/settings":
        speed_limit_bps = data.get("speed_limit_bps", None)
        max_active = data.get("max_active", None)
        auto_open_folder = data.get("auto_open_folder", None)
        verify_final_size = data.get("verify_final_size", None)

        if speed_limit_bps is not None and hasattr(mgr, "set_speed_limit"):
            mgr.set_speed_limit(int(speed_limit_bps or 0))

        if max_active is not None and hasattr(mgr, "set_max_active"):
            mgr.set_max_active(int(max_active or 1))

        if auto_open_folder is not None and hasattr(mgr, "set_auto_open_folder"):
            mgr.set_auto_open_folder(bool(auto_open_folder))

        if verify_final_size is not None and hasattr(mgr, "set_verify_final_size"):
            mgr.set_verify_final_size(bool(verify_final_size))

        cfg = runtime.get("config")
        if isinstance(cfg, dict):
            if speed_limit_bps is not None:
                cfg["downloader_speed_limit_bps"] = int(speed_limit_bps or 0)
            if max_active is not None:
                cfg["downloader_max_active"] = int(max_active or 1)
            if auto_open_folder is not None:
                cfg["downloader_auto_open_folder"] = bool(auto_open_folder)
            if verify_final_size is not None:
                cfg["downloader_verify_final_size"] = bool(verify_final_size)

        _reply_json(handler, {"ok": True, "settings": mgr.settings_snapshot() if hasattr(mgr, "settings_snapshot") else {}})
        return True
    # PATCH_DOWNLOADER_FEATURES_API_END

    if clean_path == "/api/downloader/toggle-h2":
        enabled = bool(data.get("enabled", False))

        if hasattr(mgr, "set_h2_enabled"):
            enabled = mgr.set_h2_enabled(enabled)
        else:
            mgr.config["downloader_h2_enabled"] = enabled

        cfg = runtime.get("config")
        if isinstance(cfg, dict):
            cfg["downloader_h2_enabled"] = enabled

        log.info("Downloader H2 -> %s", "ON" if enabled else "OFF")
        _reply_json(handler, {"ok": True, "h2_enabled": enabled})
        return True

    _reply_json(handler, {"ok": False, "error": "not found"}, 404)
    return True
