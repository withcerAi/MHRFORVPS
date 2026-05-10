#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

_PLACEHOLDER_AUTH_KEYS = {
    "",
    "CHANGE_ME_TO_A_STRONG_SECRET",
    "your-secret-password-here",
}

_PLACEHOLDER_SCRIPT_IDS = {
    "",
    "YOUR_APPS_SCRIPT_DEPLOYMENT_ID",
    "CHANGE_ME",
}


import sys

_SRC_DIR = HERE / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from config_optimizer import (
        optimize_config_for_script_count,
        optimize_and_write_runtime_profiles,
        optimizer_message,
    )
except Exception as exc:
    print(f"[SETUP] config_optimizer import failed: {exc}")
    optimize_config_for_script_count = None
    optimize_and_write_runtime_profiles = None
    optimizer_message = None


def _setup_normalize_downloader_dir(value):
    raw = str(value or "").strip().strip('"').strip("'")

    if not raw:
        raw = "downloads"

    if raw.replace("/", "\\").lower() == "d:\\downloadpy":
        raw = "downloads"

    try:
        p = Path(raw)
        target = p if p.is_absolute() else (HERE / p)
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    return raw


def _read_json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0") or "0")
        raw = handler.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _send_json(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler, html, status=200):
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _load_existing_config():
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _strict_single_script_id(value):
    x = str(value or "").strip()

    if not x:
        return ""

    if any(ch in x for ch in (",", "[", "]", '"', "'")):
        raise ValueError("هر بار فقط یک Deployment ID وارد کن. comma / bracket / quote مجاز نیست.")

    if any(ch.isspace() for ch in x):
        raise ValueError("Deployment ID نباید فاصله، tab یا خط جدید داشته باشد.")

    return x


def _strict_script_ids(value):
    if value is None:
        return []

    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]

    out = []
    seen = set()

    for item in raw_items:
        x = _strict_single_script_id(item)
        if not x:
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


def _script_tier(count):
    try:
        count = int(count or 0)
    except Exception:
        count = 0

    if count <= 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 3
    if count == 4:
        return 4
    return 5


def build_default_config(user_values=None):
    user_values = user_values or {}

    lang = str(user_values.get("mhr_lang") or "fa").strip().lower()
    lang = "fa" if lang.startswith("fa") else "en"

    script_ids = _strict_script_ids(user_values.get("script_ids"))

    # Important:
    # auth_key must be provided by the user.
    # Do NOT generate a random auth_key here.
    auth_key = str(user_values.get("auth_key") or "").strip()

    downloader_dir = _setup_normalize_downloader_dir(user_values.get("downloader_dir"))

    exit_url = str(user_values.get("exit_node_health_url") or "").strip()
    exit_key = str(user_values.get("exit_node_health_key") or "").strip()

    cfg = {
        "mode": "apps_script",
        "runtime_mode": "basic",
        "label": "Basic Stable - setup profile",

        "setup_completed": True,
        "setup_completed_at": int(time.time()),

        "script_blacklist_ttl": 20,

        "google_ip": "216.239.38.120",
        "front_domains": [
            "www.google.com",
            "mail.google.com",
        ],

        "script_ids": script_ids,
        "script_quota_limit": 20000,
        "auth_key": auth_key,

        "listen_host": "127.0.0.1",
        "listen_port": 8085,
        "socks5_enabled": True,
        "socks5_port": 1080,
        "dashboard_port": 9099,

        "log_level": "INFO",
        "verify_ssl": True,
        "lan_sharing": False,

        "relay_timeout": 90,
        "range_probe_timeout": 60,
        "h2_stream_timeout": 60,
        "h2_connect_timeout": 20,
        "tls_connect_timeout": 20,
        "tcp_connect_timeout": 10,

        "max_response_body_bytes": 209715200,

        "parallel_relay": 1,
        "h2_connections": 3,
        "h2_disabled_by_dashboard": False,

        "enable_batch": True,
        "enable_sub_batch": True,
        "batch_window_micro": 0.015,
        "batch_window_macro": 0.12,
        "batch_max": 20,

        "chunked_download_extensions": [
            ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi",
            ".zip", ".rar", ".7z", ".iso", ".apk", ".exe", ".bin", ".pdf",
        ],
        "chunked_download_min_size": 8388608,
        "chunked_download_chunk_size": 2097152,
        "chunked_download_max_parallel": 4,
        "chunked_download_max_chunks": 512,

        "block_hosts": [],
        "bypass_hosts": [
            "localhost",
            ".local",
            ".lan",
            ".home.arpa",
        ],

        "direct_google_exclude": [
            "gemini.google.com",
            "aistudio.google.com",
            "notebooklm.google.com",
            "labs.google.com",
            "meet.google.com",
            "accounts.google.com",
            "ogs.google.com",
            "mail.google.com",
            "calendar.google.com",
            "drive.google.com",
            "docs.google.com",
            "chat.google.com",
            "maps.google.com",
            "play.google.com",
            "translate.google.com",
            "assistant.google.com",
            "lens.google.com",
        ],

        "direct_google_allow": [
            "www.google.com",
            "safebrowsing.google.com",
        ],

        "youtube_via_relay": True,
        "hosts": {},

        "video_prefetch_enabled": True,
        "video_prefetch_next_ranges": 2,
        "video_prefetch_parallel": 2,
        "video_prefetch_chunk_size": 1048576,
        "video_cache_max_mb": 512,
        "video_cache_ttl_seconds": 600,

        "manifest_prefetch_enabled": True,
        "manifest_prefetch_next_segments": 2,
        "manifest_prefetch_parallel": 1,
        "manifest_cache_max_mb": 512,
        "manifest_cache_ttl_seconds": 600,

        "video_passthrough_enabled": True,
        "video_passthrough_relay_timeout": 120,

        "video_priority_retry_attempts": 2,
        "video_priority_retry_delay_ms": 120,
        "video_priority_parallel_relay": 1,

        "youtube_sabr_booster_enabled": True,
        "youtube_sabr_timeout": 180,
        "youtube_sabr_max_parallel": 2,
        "youtube_sabr_retry_attempts": 2,
        "youtube_sabr_retry_delay_ms": 120,

        "turbo_mode_enabled": True,
        "turbo_parallel_relay": 1,
        "turbo_force_no_delay": True,
        "turbo_skip_download_mode": False,
        "turbo_coalesce_window_ms": 0,
        "turbo_small_request_max": 0,
        "turbo_min_upload_padding": 0,
        "turbo_chunk_size": 102400,
        "turbo_parallel": 2,
        "turbo_min_size": 102400,

        "telegram_mode_enabled": False,
        "telegram_cache_ttl_seconds": 8,
        "telegram_hosts": [
            "telegram.org",
            "telegram.me",
            "t.me",
        ],
        "telegram_serial_per_host": False,
        "telegram_coalesce_window_ms": 0,
        "telegram_max_delay_ms": 0,
        "telegram_parallel_relay": 2,
        "telegram_burst_limit": 0,
        "telegram_burst_cooldown_ms": 0,
        "telegram_cidrs": [
            "91.108.4.0/22",
            "91.108.8.0/22",
            "91.108.12.0/22",
            "91.108.16.0/22",
            "91.108.20.0/22",
            "91.108.56.0/22",
            "91.105.192.0/23",
            "149.154.160.0/20",
            "185.76.151.0/24",
        ],

        "downloader_dir": downloader_dir,
        "downloader_chunk_size": 8388608,
        "downloader_parallel": 4,
        "downloader_retries": 4,
        "downloader_h2_enabled": False,
        "downloader_h2_wait_seconds": 2.5,
        "downloader_h2_timeout": 45,
        "downloader_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "downloader_headers": {
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        },

        "exit_node_health_url": exit_url,
        "exit_node_health_key": exit_key,
        "exit_node_health_timeout": 45,
        "exit_node_health_interval": 120,
        "front_ip_diagnostic_window_seconds": 120,
        "front_ip_diagnostic_timeout_threshold": 8,
        "front_ip_diagnostic_warning_cooldown": 30,

        "downloader_auto_open_folder": False,
        "downloader_verify_final_size": True,
        "downloader_auto_retry_rounds": 2,
        "downloader_speed_limit_bps": 0,
        "downloader_max_active": 2,

        "auto_tune_enabled": False,
        "auto_mode_ready": False,
        "auto_tune_h2": True,
        "auto_tune_interval": 30,
        "auto_tune_suggestion_interval_seconds": 60,

        "video_prefetch_disabled_by_dashboard": False,
        "manifest_prefetch_disabled_by_dashboard": False,
        "video_passthrough_disabled_by_dashboard": False,
        "sabr_disabled_by_dashboard": False,
        "turbo_disabled_by_dashboard": False,

        "mhr_lang": lang,
    }

    if not exit_url:
        cfg.pop("exit_node_health_url", None)

    if not exit_key:
        cfg.pop("exit_node_health_key", None)

    if optimize_config_for_script_count is not None:
        cfg = optimize_config_for_script_count(cfg)
    else:
        count = len(cfg.get("script_ids") or [])
        tier = _script_tier(count)
        cfg["script_count"] = count
        cfg["script_tier"] = tier
        cfg["optimized_for_script_count"] = tier
        cfg["optimized_at"] = int(time.time())
        cfg["optimizer_version"] = "script-count-v1"

    return cfg


def _write_config(cfg):
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>MHR Setup Wizard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="autocomplete" content="off">
<style>
:root{
  --bg:#050812;
  --card:#0b1220dd;
  --card2:#101827dd;
  --line:#ffffff16;
  --text:#edf5ff;
  --muted:#8fa3bd;
  --green:#34d399;
  --yellow:#fbbf24;
  --red:#fb7185;
  --cyan:#22d3ee;
  --purple:#a78bfa;
  --font-fa:Vazirmatn,Vazir,IRANSans,"Segoe UI",Tahoma,Arial,sans-serif;
  --font-en:Inter,"Segoe UI",Roboto,Arial,sans-serif;
  --mono:"Cascadia Mono",Consolas,monospace;
}
*{box-sizing:border-box}
body{
  margin:0;
  min-height:100vh;
  background:
    radial-gradient(circle at 10% 0%,#2563eb35,transparent 30%),
    radial-gradient(circle at 90% 0%,#7c3aed35,transparent 30%),
    linear-gradient(180deg,#08111f,var(--bg));
  color:var(--text);
  font-family:var(--font-fa);
}
html[lang="en"] body{direction:ltr;text-align:left;font-family:var(--font-en)}
html[lang="fa"] body{direction:rtl;text-align:right;font-family:var(--font-fa)}
main{max-width:980px;margin:0 auto;padding:20px}
.card{
  background:linear-gradient(180deg,var(--card),var(--card2));
  border:1px solid var(--line);
  border-radius:24px;
  padding:18px;
  margin:14px 0;
  box-shadow:0 24px 70px #0008;
}
.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
h1{margin:0;font-size:26px;letter-spacing:-.03em}
h2{margin:0 0 10px;font-size:18px}
p{line-height:1.75}
.muted{color:var(--muted);line-height:1.7}
.step{display:none}
.step.active{display:block}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.field{background:#03071280;border:1px solid #ffffff12;border-radius:18px;padding:13px}
label{display:block;color:var(--muted);font-size:12px;font-weight:950;margin-bottom:8px}
input{
  width:100%;
  border:1px solid #263449;
  background:#020617;
  color:var(--text);
  border-radius:14px;
  padding:12px;
  outline:none;
  font-family:inherit;
}
input:focus{border-color:#60a5fa88;box-shadow:0 0 0 3px #3b82f622}
.help{color:var(--muted);font-size:12px;line-height:1.65;margin-top:8px}
.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:16px}
html[lang="en"] .actions{justify-content:flex-end}
html[lang="fa"] .actions{justify-content:flex-start}
button{
  background:linear-gradient(135deg,#2563eb,#7c3aed);
  color:white;
  border:0;
  border-radius:999px;
  padding:12px 17px;
  font-weight:950;
  cursor:pointer;
  font-family:inherit;
}
button.secondary{background:#111827;border:1px solid #ffffff20}
button.good{background:linear-gradient(135deg,#059669,#34d399)}
button.bad{background:#2a0710;border:1px solid #7f1d1d;color:#fecaca}
button:disabled{opacity:.45;cursor:not-allowed}
.langBtn{background:#111827;border:1px solid #ffffff20}
.warn{border:1px solid #854d0e;background:#211505cc;color:#fde68a;border-radius:16px;padding:12px;line-height:1.65}
.ok{border:1px solid #166534;background:#052e1a99;color:#bbf7d0;border-radius:16px;padding:12px;line-height:1.65}
.error{border:1px solid #7f1d1d;background:#2a0710cc;color:#fecaca;border-radius:16px;padding:12px;line-height:1.65}
.idAddRow{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}
.idList{display:grid;gap:8px;margin-top:12px}
.idItem{
  display:grid;
  grid-template-columns:1fr auto;
  gap:10px;
  align-items:center;
  border:1px solid #ffffff14;
  background:#02061799;
  border-radius:14px;
  padding:10px;
}
.idText{
  direction:ltr;
  text-align:left;
  font-family:var(--mono);
  color:#dbeafe;
  overflow-wrap:anywhere;
}
.previewBox{margin-top:12px}
.previewBox pre{
  margin:0;
  direction:ltr;
  text-align:left;
  white-space:pre-wrap;
  word-break:break-word;
  background:#020617;
  border:1px solid #ffffff12;
  border-radius:14px;
  padding:12px;
  color:#a5f3fc;
  font-family:var(--mono);
}
@media(max-width:760px){
  main{padding:10px}
  .grid{grid-template-columns:1fr}
  .idAddRow{grid-template-columns:1fr}
}
</style>
</head>
<body>
<main>
  <div class="card">
    <div class="top">
      <div>
        <h1 data-i18n="title">راه‌اندازی اولیه MHR</h1>
        <p class="muted" data-i18n="subtitle">چند مقدار اصلی را وارد کن تا config.json ساخته شود. بعد از پایان، MHR خودش ری‌استارت می‌شود.</p>
      </div>
      <button type="button" class="langBtn" id="langBtn">EN</button>
    </div>
  </div>

  <div class="card step active" id="stepLang">
    <h2 data-i18n="chooseLang">انتخاب زبان</h2>
    <p class="muted" data-i18n="chooseLangHelp">زبان رابط کاربری Dashboard / Downloader / Suggestions هم با همین مقدار هماهنگ می‌شود.</p>
    <div class="actions">
      <button type="button" class="good" id="btnFa">فارسی</button>
      <button type="button" class="secondary" id="btnEn">English</button>
    </div>
  </div>

  <div class="card step" id="stepScript">
    <h2 data-i18n="scriptTitle">اطلاعات Apps Script</h2>
    <div class="warn" data-i18n="authWarn">همه Deploymentهای Apps Script باید دقیقاً همین auth_key را داخل Code.gs داشته باشند.</div>

    <div class="grid" style="margin-top:12px">
      <div class="field">
        <label data-i18n="authKey">auth_key مشترک</label>
        <input id="authKey" value="" autocomplete="new-password" name="mhr_auth_key_manual" spellcheck="false" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true">
        <div class="help" data-i18n="authHelp">این رمز باید در Code.gs هم داخل AUTH_KEY قرار بگیرد.</div>
      </div>

      <div class="field">
        <label data-i18n="downloaderDir">پوشه دانلودها</label>
        <input id="downloaderDir" value="__DOWNLOADER_DIR__" autocomplete="off" name="mhr_downloader_dir" spellcheck="false" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true">
        <div class="help" data-i18n="downloaderHelp">پیش‌فرض، پوشه downloads داخل همین پروژه است.</div>
      </div>
    </div>

    <div class="field" style="margin-top:12px">
      <label data-i18n="scriptIds">Deployment ID ها</label>

      <div class="ok" style="margin-bottom:12px" data-i18n="scriptRecommended">
        پیشنهاد: برای سرعت و پایداری بهتر، ۵ تا Deployment ID وارد کنید.
      </div>

      <div class="idAddRow">
        <input id="scriptIdInput" placeholder="AKfycb..." spellcheck="false" autocomplete="new-password" name="mhr_script_id_manual" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true">
        <button type="button" id="addIdBtn" data-i18n="addId">افزودن ID</button>
      </div>

      <div class="help" data-i18n="scriptHelp">
        هر بار فقط یک Deployment ID وارد کن. فاصله، comma، خط جدید، bracket و quote مجاز نیست.
      </div>

      <div id="scriptIdList" class="idList"></div>

      <div class="field previewBox">
        <label data-i18n="configPreview">پیش‌نمایش داخل config.json</label>
        <pre id="scriptIdsPreview">"script_ids": []</pre>
      </div>
    </div>

    <div class="actions">
      <button type="button" class="secondary" id="back1" data-i18n="back">قبلی</button>
      <button type="button" id="next1" data-i18n="next">بعدی</button>
    </div>
  </div>

  <div class="card step" id="stepExit">
    <h2 data-i18n="exitTitle">Exit Node / Server Health</h2>
    <p class="muted" data-i18n="exitHelp">این بخش اختیاری است، ولی اگر VPS/Exit Node داری به تحلیل سلامت سرور و فشار اتصال کمک می‌کند.</p>

    <div class="grid">
      <div class="field">
        <label data-i18n="exitUrl">آدرس Health نود خروجی</label>
        <input id="exitUrl" placeholder="http://YOUR_VPS_IP:8081" autocomplete="off" name="mhr_exit_url" spellcheck="false" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true">
        <div class="help" data-i18n="exitUrlHelp">اگر نداری خالی بگذار.</div>
      </div>

      <div class="field">
        <label data-i18n="exitKey">exit_node_health_key</label>
        <input id="exitKey" placeholder="MHR-Health-Strong-Key" autocomplete="new-password" name="mhr_exit_key_manual" spellcheck="false" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true">
        <div class="help" data-i18n="exitKeyHelp">باید با کلید Health روی VPS یکی باشد. اگر نداری خالی بگذار.</div>
      </div>
    </div>

    <div class="actions">
      <button type="button" class="secondary" id="back2" data-i18n="back">قبلی</button>
      <button type="button" id="next2" data-i18n="next">بعدی</button>
    </div>
  </div>

  <div class="card step" id="stepReview">
    <h2 data-i18n="reviewTitle">بررسی نهایی</h2>
    <div id="reviewBox" class="ok"></div>
    <div class="actions">
      <button type="button" class="secondary" id="back3" data-i18n="back">قبلی</button>
      <button type="button" class="good" id="finishBtn" data-i18n="finish">ساخت config.json و راه‌اندازی</button>
    </div>
    <div id="resultBox" style="margin-top:14px"></div>
  </div>
</main>

<script>
var DEFAULT_LANG = "__DEFAULT_LANG__";
var INITIAL_SCRIPT_IDS = [];

var T = {
  en: {
    title:"MHR first-time setup",
    subtitle:"Enter the required values. MHR will create config.json and restart automatically.",
    chooseLang:"Choose language",
    chooseLangHelp:"Dashboard / Downloader / Suggestions will use this shared language.",
    scriptTitle:"Apps Script information",
    authWarn:"All Apps Script deployments must use exactly the same auth_key inside Code.gs.",
    authKey:"Shared auth_key",
    authHelp:"This must match AUTH_KEY inside Code.gs.",
    downloaderDir:"Download folder",
    downloaderHelp:"Default is the downloads folder inside this project.",
    scriptIds:"Deployment IDs",
    scriptRecommended:"Recommended: enter 5 Deployment IDs for better speed and stability.",
    addId:"Add ID",
    removeId:"Remove",
    singleIdOnly:"Enter only one clean Deployment ID. Spaces, comma, newline, brackets and quotes are not allowed.",
    configPreview:"Preview inside config.json",
    scriptHelp:"Enter one Deployment ID at a time. Spaces, comma, newline, brackets and quotes are not allowed.",
    exitTitle:"Exit Node / Server Health",
    exitHelp:"Optional. If you have a VPS/Exit Node, it helps Dashboard analyze server health and connection pressure.",
    exitUrl:"Exit Node health URL",
    exitUrlHelp:"Leave empty if you do not have it.",
    exitKey:"exit_node_health_key",
    exitKeyHelp:"Must match your VPS health key. Leave empty if you do not have it.",
    reviewTitle:"Final review",
    back:"Back",
    next:"Next",
    finish:"Create config.json and start",
    missingScript:"Please enter at least one Deployment ID.",
    missingAuth:"auth_key is required.",
    restarting:"Setup completed. MHR is restarting with the new config. Please wait...",
    dashboardIn:"Dashboard will open in",
    seconds:"seconds.",
    failed:"Failed"
  },
  fa: {
    title:"راه‌اندازی اولیه MHR",
    subtitle:"چند مقدار اصلی را وارد کن تا config.json ساخته شود. بعد از پایان، MHR خودش ری‌استارت می‌شود.",
    chooseLang:"انتخاب زبان",
    chooseLangHelp:"زبان رابط کاربری Dashboard / Downloader / Suggestions هم با همین مقدار هماهنگ می‌شود.",
    scriptTitle:"اطلاعات Apps Script",
    authWarn:"همه Deploymentهای Apps Script باید دقیقاً همین auth_key را داخل Code.gs داشته باشند.",
    authKey:"auth_key مشترک",
    authHelp:"این رمز باید در Code.gs هم داخل AUTH_KEY قرار بگیرد.",
    downloaderDir:"پوشه دانلودها",
    downloaderHelp:"پیش‌فرض، پوشه downloads داخل همین پروژه است.",
    scriptIds:"Deployment ID ها",
    scriptRecommended:"پیشنهاد: برای سرعت و پایداری بهتر، ۵ تا Deployment ID وارد کنید.",
    addId:"افزودن ID",
    removeId:"حذف",
    singleIdOnly:"هر بار فقط یک Deployment ID تمیز وارد کن. فاصله، comma، خط جدید، bracket و quote مجاز نیست.",
    configPreview:"پیش‌نمایش داخل config.json",
    scriptHelp:"هر بار فقط یک Deployment ID وارد کن. فاصله، comma، خط جدید، bracket و quote مجاز نیست.",
    exitTitle:"Exit Node / Server Health",
    exitHelp:"این بخش اختیاری است، ولی اگر VPS/Exit Node داری به تحلیل سلامت سرور و فشار اتصال کمک می‌کند.",
    exitUrl:"آدرس Health نود خروجی",
    exitUrlHelp:"اگر نداری خالی بگذار.",
    exitKey:"exit_node_health_key",
    exitKeyHelp:"باید با کلید Health روی VPS یکی باشد. اگر نداری خالی بگذار.",
    reviewTitle:"بررسی نهایی",
    back:"قبلی",
    next:"بعدی",
    finish:"ساخت config.json و راه‌اندازی",
    missingScript:"حداقل یک Deployment ID وارد کن.",
    missingAuth:"auth_key الزامی است.",
    restarting:"راه‌اندازی کامل شد. MHR با کانفیگ جدید در حال ری‌استارت است. لطفاً صبر کن...",
    dashboardIn:"داشبورد تا",
    seconds:"ثانیه دیگر باز می‌شود.",
    failed:"ناموفق"
  }
};

var currentStep = 0;
var steps = ["stepLang", "stepScript", "stepExit", "stepReview"];
var SCRIPT_IDS = [];

function gid(id){
  return document.getElementById(id);
}

function setupPrivacyNoAutofill(){
  try{
    var fields = ["authKey", "scriptIdInput", "exitKey", "exitUrl", "downloaderDir"];

    for(var i = 0; i < fields.length; i++){
      var el = gid(fields[i]);
      if(!el) continue;

      var sensitive = fields[i] === "authKey" || fields[i] === "scriptIdInput" || fields[i] === "exitKey";

      el.setAttribute("autocomplete", sensitive ? "new-password" : "off");
      el.setAttribute("autocorrect", "off");
      el.setAttribute("autocapitalize", "off");
      el.setAttribute("spellcheck", "false");
      el.setAttribute("data-lpignore", "true");
      el.setAttribute("data-1p-ignore", "true");
      el.setAttribute("name", "mhr_" + fields[i] + "_" + Date.now() + "_" + Math.floor(Math.random() * 1000000));
    }

    try{
      localStorage.removeItem("mhr.setup.auth_key");
      localStorage.removeItem("mhr.setup.script_id");
      localStorage.removeItem("mhr.setup.script_ids");
      localStorage.removeItem("mhr.setup.exit_key");
      localStorage.removeItem("mhr.setup.exit_url");
      sessionStorage.removeItem("mhr.setup.auth_key");
      sessionStorage.removeItem("mhr.setup.script_id");
      sessionStorage.removeItem("mhr.setup.script_ids");
      sessionStorage.removeItem("mhr.setup.exit_key");
      sessionStorage.removeItem("mhr.setup.exit_url");
    }catch(e){}
  }catch(e){}
}

function currentLang(){
  var v = localStorage.getItem("mhr.lang") || DEFAULT_LANG || "fa";
  v = String(v).toLowerCase();
  return v.indexOf("fa") === 0 ? "fa" : "en";
}

function setLang(l){
  l = String(l || "fa").toLowerCase().indexOf("fa") === 0 ? "fa" : "en";

  localStorage.setItem("mhr.lang", l);
  localStorage.setItem("mhr.language", l);

  document.documentElement.lang = l;
  document.documentElement.dir = l === "fa" ? "rtl" : "ltr";
  document.body.dir = l === "fa" ? "rtl" : "ltr";

  if(gid("langBtn")){
    gid("langBtn").textContent = l === "fa" ? "EN" : "FA";
  }

  var nodes = document.querySelectorAll("[data-i18n]");
  for(var i = 0; i < nodes.length; i++){
    var k = nodes[i].getAttribute("data-i18n");
    if(T[l] && T[l][k]){
      nodes[i].textContent = T[l][k];
    }
  }

  renderScriptIdList();
}

function toggleLang(){
  setLang(currentLang() === "fa" ? "en" : "fa");
}

function showStep(){
  for(var i = 0; i < steps.length; i++){
    var el = gid(steps[i]);
    if(el){
      el.classList.toggle("active", i === currentStep);
    }
  }

  if(steps[currentStep] === "stepReview"){
    paintReview();
  }
}

function nextStep(){
  if(currentStep === 1){
    var key = gid("authKey") ? gid("authKey").value.trim() : "";

    if(!key){
      alert(T[currentLang()].missingAuth);
      return;
    }

    if(SCRIPT_IDS.length < 1){
      alert(T[currentLang()].missingScript);
      return;
    }
  }

  currentStep = Math.min(steps.length - 1, currentStep + 1);
  showStep();
}

function prevStep(){
  currentStep = Math.max(0, currentStep - 1);
  showStep();
}

function hasBadIdChars(value){
  value = String(value || "");

  for(var i = 0; i < value.length; i++){
    var code = value.charCodeAt(i);
    var ch = value.charAt(i);

    if(code === 32 || code === 9 || code === 10 || code === 13){
      return true;
    }

    if(ch === "," || ch === "[" || ch === "]" || ch === "'" || code === 34){
      return true;
    }
  }

  return false;
}

function escapeHtml(v){
  return String(v || "")
    .split("&").join("&amp;")
    .split("<").join("&lt;")
    .split(">").join("&gt;")
    .split(String.fromCharCode(34)).join("&quot;");
}

function syncPreview(){
  var pre = gid("scriptIdsPreview");
  if(pre){
    pre.textContent = JSON.stringify({script_ids: SCRIPT_IDS}, null, 2);
  }
}

function renderScriptIdList(){
  var list = gid("scriptIdList");
  if(!list){
    return;
  }

  var l = currentLang();
  var removeText = T[l].removeId || "Remove";

  if(SCRIPT_IDS.length < 1){
    list.innerHTML = '<div class="warn">' + (l === "fa" ? "هنوز هیچ Deployment ID اضافه نشده است." : "No Deployment ID added yet.") + '</div>';
    syncPreview();
    return;
  }

  var html = "";

  for(var i = 0; i < SCRIPT_IDS.length; i++){
    html += '<div class="idItem">';
    html += '<div class="idText">' + escapeHtml(SCRIPT_IDS[i]) + '</div>';
    html += '<button type="button" class="bad" data-remove-idx="' + i + '">' + removeText + '</button>';
    html += '</div>';
  }

  list.innerHTML = html;

  var buttons = list.querySelectorAll("button[data-remove-idx]");
  for(var j = 0; j < buttons.length; j++){
    buttons[j].addEventListener("click", function(){
      var idx = parseInt(this.getAttribute("data-remove-idx"), 10);
      if(!isNaN(idx)){
        SCRIPT_IDS.splice(idx, 1);
        renderScriptIdList();
      }
    });
  }

  syncPreview();
}

function addScriptId(){
  var input = gid("scriptIdInput");
  var raw = input ? String(input.value || "").trim() : "";

  if(!raw){
    return;
  }

  if(hasBadIdChars(raw)){
    alert(T[currentLang()].singleIdOnly);
    return;
  }

  if(SCRIPT_IDS.indexOf(raw) === -1){
    SCRIPT_IDS.push(raw);
  }

  if(input){
    input.value = "";
  }

  renderScriptIdList();
}

function paintReview(){
  var l = currentLang();
  var exitUrl = gid("exitUrl") ? gid("exitUrl").value.trim() : "";
  var box = gid("reviewBox");

  if(!box){
    return;
  }

  if(l === "fa"){
    box.innerHTML =
      "<b>آماده ساخت config.json هستیم.</b><br>" +
      "تعداد Deployment ID: <b>" + SCRIPT_IDS.length + "</b><br>" +
      "Exit Node Health: <b>" + (exitUrl ? "فعال" : "غیرفعال / اختیاری") + "</b><br>" +
      "زبان پیش‌فرض: <b>" + l + "</b><br>" +
      "بعد از ذخیره، برنامه خودش ری‌استارت می‌شود.";
  }else{
    box.innerHTML =
      "<b>Ready to create config.json.</b><br>" +
      "Deployment IDs: <b>" + SCRIPT_IDS.length + "</b><br>" +
      "Exit Node Health: <b>" + (exitUrl ? "Enabled" : "Disabled / optional") + "</b><br>" +
      "Default language: <b>" + l + "</b><br>" +
      "After saving, MHR will restart automatically.";
  }
}

async function finishSetup(){
  var l = currentLang();

  var payload = {
    mhr_lang: l,
    auth_key: gid("authKey") ? gid("authKey").value.trim() : "",
    script_ids: SCRIPT_IDS.slice(),
    downloader_dir: gid("downloaderDir") ? gid("downloaderDir").value.trim() : "downloads",
    exit_node_health_url: gid("exitUrl") ? gid("exitUrl").value.trim() : "",
    exit_node_health_key: gid("exitKey") ? gid("exitKey").value.trim() : ""
  };

  if(!payload.auth_key){
    alert(T[l].missingAuth);
    return;
  }

  if(payload.script_ids.length < 1){
    alert(T[l].missingScript);
    return;
  }

  var box = gid("resultBox");
  if(box){
    box.innerHTML = '<div class="warn">' + (l === "fa" ? "در حال ذخیره config.json..." : "Saving config.json...") + '</div>';
  }

  try{
    var r = await fetch("/api/setup/save", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });

    var j = await r.json();

    if(!j.ok){
      if(box){
        box.innerHTML = '<div class="error">' + T[l].failed + ': ' + (j.error || "unknown") + '</div>';
      }
      return;
    }

    var wait = Number(j.restart_delay_seconds || 8);
    var optimizerMsg = j.optimizer_message || "";

    document.body.innerHTML =
      '<main><div class="card">' +
      '<h1>' + (l === "fa" ? "راه‌اندازی کامل شد" : "Setup completed") + '</h1>' +
      '<p>' + T[l].restarting + '</p>' +
      (optimizerMsg ? '<div class="ok">' + optimizerMsg + '</div>' : '') +
      '<p>' + T[l].dashboardIn + ' <b id="countdown">' + wait + '</b> ' + T[l].seconds + '</p>' +
      '</div></main>';

    var left = wait;
    var timer = setInterval(function(){
      left -= 1;

      var el = gid("countdown");
      if(el){
        el.textContent = String(Math.max(0, left));
      }

      if(left <= 0){
        clearInterval(timer);
        location.href = j.next_url || "/";
      }
    }, 1000);

  }catch(e){
    if(box){
      box.innerHTML = '<div class="error">' + T[l].failed + ': ' + String(e) + '</div>';
    }
  }
}

function bindEvents(){
  gid("langBtn").addEventListener("click", toggleLang);

  gid("btnFa").addEventListener("click", function(){
    setLang("fa");
    nextStep();
  });

  gid("btnEn").addEventListener("click", function(){
    setLang("en");
    nextStep();
  });

  gid("addIdBtn").addEventListener("click", addScriptId);

  gid("back1").addEventListener("click", prevStep);
  gid("next1").addEventListener("click", nextStep);

  gid("back2").addEventListener("click", prevStep);
  gid("next2").addEventListener("click", nextStep);

  gid("back3").addEventListener("click", prevStep);
  gid("finishBtn").addEventListener("click", finishSetup);

  gid("scriptIdInput").addEventListener("keydown", function(e){
    if(e.key === "Enter"){
      e.preventDefault();
      addScriptId();
    }
  });

  gid("scriptIdInput").addEventListener("beforeinput", function(e){
    if(e.data && hasBadIdChars(e.data)){
      e.preventDefault();
    }
  });

  gid("scriptIdInput").addEventListener("paste", function(e){
    var txt = "";

    try{
      txt = (e.clipboardData || window.clipboardData).getData("text") || "";
    }catch(err){
      txt = "";
    }

    if(hasBadIdChars(txt)){
      e.preventDefault();
      alert(T[currentLang()].singleIdOnly);
    }
  });
}

function boot(){
  setupPrivacyNoAutofill();
  bindEvents();
  setLang(DEFAULT_LANG);
  renderScriptIdList();
  showStep();
}

boot();
</script>
</body>
</html>
"""


def render_setup_html():
    """
    Privacy-safe setup page.

    Sensitive values are intentionally not prefilled:
    - auth_key
    - script_ids
    - exit_node_health_url
    - exit_node_health_key

    downloader_dir is not secret, so it may use existing/default value.
    """
    existing = _load_existing_config()

    ddir = _setup_normalize_downloader_dir(existing.get("downloader_dir"))

    lang = str(existing.get("mhr_lang") or "fa").lower()
    lang = "fa" if lang.startswith("fa") else "en"

    html = HTML_TEMPLATE
    html = html.replace("__DOWNLOADER_DIR__", html_escape(ddir, quote=True))
    html = html.replace("__DEFAULT_LANG__", lang)

    return html


def handle_setup_get(handler, runtime):
    clean_path = urlparse(handler.path).path

    if clean_path in ("/setup", "/setup/"):
        _send_html(handler, render_setup_html())
        return True

    if clean_path == "/api/setup/status":
        cfg = _load_existing_config()
        _send_json(handler, {
            "ok": True,
            "setup_completed": bool(cfg.get("setup_completed", False)),
            "mhr_lang": cfg.get("mhr_lang", "fa"),
        })
        return True

    return False


def handle_setup_post(handler, runtime, data=None):
    clean_path = urlparse(handler.path).path

    if clean_path != "/api/setup/save":
        return False

    if data is None:
        data = _read_json_body(handler)

    try:
        cfg = build_default_config(data)

        if not cfg.get("auth_key"):
            _send_json(handler, {"ok": False, "error": "auth_key is required"}, 400)
            return True

        ids = cfg.get("script_ids") or []
        if not ids:
            _send_json(handler, {"ok": False, "error": "at least one script_id is required"}, 400)
            return True

        count = len(ids)
        tier = _script_tier(count)

        cfg["script_count"] = count
        cfg["script_tier"] = cfg.get("script_tier", tier)
        cfg["optimized_for_script_count"] = cfg.get("optimized_for_script_count", tier)
        cfg["optimized_at"] = cfg.get("optimized_at", int(time.time()))
        cfg["optimizer_version"] = cfg.get("optimizer_version", "script-count-v1")

        _write_config(cfg)

        try:
            if optimize_and_write_runtime_profiles is not None:
                optimize_and_write_runtime_profiles(cfg)
        except Exception as exc:
            print(f"[SETUP] runtime_profiles optimizer failed: {exc}")

        if isinstance(runtime, dict):
            runtime["config"] = cfg
            runtime["setup_completed_now"] = True
            runtime["setup_mode"] = False

        lang = str(cfg.get("mhr_lang") or "fa")
        if optimizer_message is not None:
            opt_msg = optimizer_message(count, lang)
        elif lang.lower().startswith("fa"):
            opt_msg = f"کانفیگ مناسب برای {count} اسکریپت ID ثبت شد. بعداً از داشبورد می‌توانی ID اضافه کنی و دوباره Optimize بزنی."
        else:
            opt_msg = f"Optimized config registered for {count} script ID(s). Later you can add IDs from Dashboard and optimize again."

        _send_json(handler, {
            "ok": True,
            "message": "Setup completed. MHR is restarting with the new config.",
            "optimizer_message": opt_msg,
            "script_count": count,
            "script_tier": cfg.get("script_tier", tier),
            "optimized_for_script_count": cfg.get("optimized_for_script_count", tier),
            "optimizer_version": cfg.get("optimizer_version", "script-count-v1"),
            "restart_required": True,
            "restart_delay_seconds": 8,
            "next_url": "/",
        })
        return True

    except ValueError as exc:
        _send_json(handler, {"ok": False, "error": str(exc)}, 400)
        return True

    except Exception as exc:
        _send_json(handler, {"ok": False, "error": str(exc)}, 500)
        return True


def default_config():
    return {
        "mode": "apps_script",
        "runtime_mode": "basic",
        "label": "Setup Required",
        "setup_completed": False,

        "listen_host": "127.0.0.1",
        "listen_port": 8085,
        "socks5_enabled": True,
        "socks5_port": 1080,
        "dashboard_port": 9099,

        "log_level": "INFO",
        "verify_ssl": True,
        "lan_sharing": False,

        "auth_key": "",
        "script_ids": [],

        "google_ip": "216.239.38.120",
        "front_domains": [
            "www.google.com",
            "mail.google.com",
        ],

        "relay_timeout": 90,
        "range_probe_timeout": 60,
        "h2_stream_timeout": 60,
        "h2_connect_timeout": 20,
        "tls_connect_timeout": 20,
        "tcp_connect_timeout": 10,

        "parallel_relay": 1,
        "h2_connections": 3,
        "h2_disabled_by_dashboard": False,

        "downloader_dir": "downloads",
        "downloader_chunk_size": 8388608,
        "downloader_parallel": 4,
        "downloader_retries": 4,
        "downloader_h2_enabled": False,
        "downloader_h2_wait_seconds": 2.5,
        "downloader_h2_timeout": 45,

        "exit_node_health_url": "",
        "exit_node_health_key": "",
        "exit_node_health_timeout": 45,
        "exit_node_health_interval": 120,

        "auto_tune_enabled": False,
        "auto_mode_ready": False,
        "auto_tune_h2": True,
        "auto_tune_interval": 30,
        "auto_tune_suggestion_interval_seconds": 60,

        "video_prefetch_disabled_by_dashboard": False,
        "manifest_prefetch_disabled_by_dashboard": False,
        "video_passthrough_disabled_by_dashboard": False,
        "sabr_disabled_by_dashboard": False,
        "turbo_disabled_by_dashboard": False,

        "mhr_lang": "fa",
    }


def setup_required(config):
    if not isinstance(config, dict):
        return True

    if not bool(config.get("setup_completed", False)):
        return True

    auth_key = str(config.get("auth_key") or "").strip()
    if not auth_key or auth_key in _PLACEHOLDER_AUTH_KEYS:
        return True

    sid = config.get("script_ids") or config.get("script_id") or []

    if isinstance(sid, str):
        ids = [sid.strip()] if sid.strip() else []
    elif isinstance(sid, list):
        ids = [str(x).strip() for x in sid if str(x).strip()]
    else:
        ids = []

    if not ids:
        return True

    if any(x in _PLACEHOLDER_SCRIPT_IDS for x in ids):
        return True

    return False


def open_setup_browser(host="127.0.0.1", port=9099):
    try:
        import webbrowser
        webbrowser.open(f"http://{host}:{int(port)}/setup")
        return True
    except Exception:
        return False