#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import deque
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import psutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(APP_DIR, "src")
STATIC_DIR = os.path.join(APP_DIR, "static")
RUNTIME_PROFILES_PATH = os.path.join(APP_DIR, "runtime_profiles.json")
QUOTA_STATE_PATHS = [
    os.path.join(APP_DIR, "quota_state.json"),
    os.path.join(APP_DIR, "src", "quota_state.json"),
]

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cert_installer import install_ca, uninstall_ca, is_ca_trusted
from constants import __version__
from downloader.gas_downloader import DownloadManager
from downloader.web import handle_downloader_get, handle_downloader_post
from google_ip_scanner import scan_sync
from lan_utils import log_lan_access
from mitm import CA_CERT_FILE
from proxy_server import ProxyServer
from stats import stats
from suggestions.suggestions_page import render_suggestions_html

try:
    from setup_wizard import open_setup_browser, handle_setup_get, handle_setup_post, default_config
except Exception as exc:
    print(f"[SETUP] setup_wizard import failed: {exc}")
    open_setup_browser = None
    handle_setup_get = None
    handle_setup_post = None
    default_config = None

try:
    from suggestions.auto_tuner import AutoTuner
except Exception:
    AutoTuner = None

try:
    from config_optimizer import (
        normalize_script_ids as project_normalize_script_ids,
        optimize_config_for_script_count,
        optimize_and_write_runtime_profiles,
        optimizer_message,
    )
except Exception as exc:
    print(f"[OPTIMIZER] config_optimizer import failed: {exc}")
    project_normalize_script_ids = None
    optimize_config_for_script_count = None
    optimize_and_write_runtime_profiles = None
    optimizer_message = None


STATIC_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml; charset=utf-8",
    ".ico": "image/x-icon",
}

PLACEHOLDER_AUTH_KEYS = {
    "",
    "CHANGE_ME_TO_A_STRONG_SECRET",
    "your-secret-password-here",
}

PLACEHOLDER_SCRIPT_IDS = {
    "",
    "YOUR_APPS_SCRIPT_DEPLOYMENT_ID",
    "CHANGE_ME",
}

OPTIMIZER_MANAGED_KEYS = (
    "runtime_mode", "label",
    "relay_timeout", "range_probe_timeout", "h2_stream_timeout", "h2_connect_timeout",
    "tls_connect_timeout", "tcp_connect_timeout",
    "parallel_relay", "h2_connections", "enable_batch", "enable_sub_batch",
    "batch_window_micro", "batch_window_macro", "batch_max",
    "chunked_download_min_size", "chunked_download_chunk_size", "chunked_download_max_parallel",
    "chunked_download_max_chunks", "max_response_body_bytes",
    "video_prefetch_enabled", "video_prefetch_next_ranges", "video_prefetch_parallel",
    "video_prefetch_chunk_size", "video_cache_max_mb", "video_cache_ttl_seconds",
    "manifest_prefetch_enabled", "manifest_prefetch_next_segments", "manifest_prefetch_parallel",
    "manifest_cache_max_mb", "manifest_cache_ttl_seconds",
    "video_passthrough_enabled", "video_passthrough_relay_timeout",
    "video_priority_retry_attempts", "video_priority_retry_delay_ms", "video_priority_parallel_relay",
    "youtube_sabr_booster_enabled", "youtube_sabr_timeout", "youtube_sabr_max_parallel",
    "youtube_sabr_retry_attempts", "youtube_sabr_retry_delay_ms",
    "turbo_mode_enabled", "turbo_parallel_relay", "turbo_force_no_delay", "turbo_skip_download_mode",
    "telegram_mode_enabled", "telegram_parallel_relay",
)

live_logs = deque(maxlen=300)
error_logs = deque(maxlen=140)
download_logs = deque(maxlen=200)
video_logs = deque(maxlen=200)
sabr_logs = deque(maxlen=200)

RUNTIME_LOCK = threading.RLock()
SHUTDOWN_EVENT = threading.Event()
STATS_SERVER = None
MAIN_ASYNC_LOOP = None
SHUTDOWN_PRINTED = False

WEB_RUNTIME = {
    "config": None,
    "server": None,
    "mode_state": None,
    "quota_start": None,
    "downloader": None,
    "auto_tuner": None,
    "auto_task": None,
    "server_health": None,
    "server_health_last": 0,
    "server_health_error": "-",
    "setup_restart_requested": False,
    "setup_restart_at": 0,
    "setup_completed_now": False,
    "setup_mode": False,
    "config_path": os.path.join(APP_DIR, "config.json"),
    "runtime_stats_baseline": None,
    "script_runtime_baseline": None,
    "runtime_baseline_since": int(time.time()),
    "runtime_baseline_reason": "startup",
}

PROCESS = psutil.Process(os.getpid())
try:
    PROCESS.cpu_percent(interval=None)
except Exception:
    pass


class DashboardLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            low = msg.lower()

            if (
                "exception in callback _proactorbasepipetransport._call_connection_lost" in low
                or "proactor_events.py" in low
                or "connectionreseterror: [winerror 10054]" in low
            ):
                return

            if "generate_204" in low or "/s/search/audio/" in low or "manifest.webmanifest" in low:
                return

            is_download = any(x in low for x in (
                "parallel download", "parallel streaming download", "download progress",
                "download complete", "chunks",
            ))
            is_sabr = (
                "sabr boost" in low
                or "youtube sabr" in low
                or ("sabr" in low and "googlevideo" in low)
                or ("video passthrough" in low and "googlevideo" in low and "sabr" in low)
            )
            is_video = (
                is_sabr
                or "googlevideo" in low
                or "videoplayback" in low
                or "youtube.com/api/stats" in low
                or "youtube.com/youtubei/v1/log_event" in low
                or "youtube.com/ptracking" in low
                or "ytimg.com" in low
                or "video traffic" in low
                or "video range" in low
                or "video stream" in low
                or "video prefetch" in low
                or "video passthrough" in low
                or "manifest" in low
                or "hls " in low
                or "prefetch hit" in low
                or "prefetch miss" in low
                or "prefetch ->" in low
                or "prefetch →" in low
                or "[prefetch]" in low
                or ("resp ←" in low and any(x in low for x in ("youtube", "googlevideo", "ytimg")))
                or ("sni-rewrite tunnel" in low and any(x in low for x in ("youtube", "googlevideo", "ytimg")))
            )
            is_error = "[error]" in low or " error " in low or "relay error" in low or "failed" in low

            if is_sabr:
                sabr_logs.append(msg)
            elif is_error:
                error_logs.append(msg)
            elif is_video:
                video_logs.append(msg)
            elif is_download:
                download_logs.append(msg)
            else:
                live_logs.append(msg)
        except Exception:
            pass


def setup_logging(level_name: str):
    handler = DashboardLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(getattr(logging, str(level_name or "INFO").upper(), logging.INFO))
    root.propagate = False
    root.addHandler(handler)


def read_static_text(filename: str) -> str:
    path = os.path.join(STATIC_DIR, os.path.basename(filename))
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_static_bytes(filename: str):
    safe_name = os.path.basename(filename)
    path = os.path.join(STATIC_DIR, safe_name)
    ext = os.path.splitext(safe_name)[1].lower()
    content_type = STATIC_MIME_TYPES.get(ext, "application/octet-stream")
    with open(path, "rb") as f:
        return f.read(), content_type


def safe_int(value, default=0):
    try:
        if value in (None, "", "-", "None"):
            return default
        return int(float(value))
    except Exception:
        return default


def script_tier_for_count(count):
    count = max(0, safe_int(count, 0))
    if count <= 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 3
    if count == 4:
        return 4
    return 5


def strict_script_ids(value):
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    out = []
    seen = set()
    for item in raw_items:
        x = str(item or "").strip()
        if not x:
            continue
        if any(ch in x for ch in (",", "[", "]", '"', "'")):
            raise RuntimeError("Only one clean Script ID is allowed per entry. Comma, brackets and quotes are not allowed.")
        if any(ch.isspace() for ch in x):
            raise RuntimeError("Script ID must not contain spaces, tabs or newlines.")
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def tolerant_script_ids(value):
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    out = []
    seen = set()
    for item in raw_items:
        x = str(item or "").strip()
        if not x:
            continue
        if any(ch in x for ch in (",", "[", "]", '"', "'")):
            continue
        if any(ch.isspace() for ch in x):
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def valid_config_script_ids(config):
    ids = tolerant_script_ids((config or {}).get("script_ids") or (config or {}).get("script_id") or [])
    return [x for x in ids if x not in PLACEHOLDER_SCRIPT_IDS]


def config_path_from_args(raw_path: str) -> str:
    raw = str(raw_path or "config.json").strip()
    return raw if os.path.isabs(raw) else os.path.join(APP_DIR, raw)


def load_json_file(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json_file(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def current_config_path():
    return config_path_from_args(WEB_RUNTIME.get("config_path") or "config.json")


def load_config_file():
    return load_json_file(current_config_path())


def write_config_file(config):
    write_json_file(current_config_path(), config)


def reset_quota_state():
    removed = []
    for path in QUOTA_STATE_PATHS:
        try:
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)
        except Exception as exc:
            logging.getLogger("Main").warning("quota_state reset failed for %s: %s", path, exc)
    try:
        if hasattr(stats, "reset"):
            stats.reset()
    except Exception:
        pass
    return removed


def file_sha256(path):
    try:
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def optimizer_fingerprint(config):
    """
    Semantic optimizer fingerprint.

    This intentionally ignores:
    - booleans / true-false switches
    - runtime_mode / label
    - values that belong to the currently selected runtime profile

    The actual drift decision is done in optimizer_config_drift().
    This function remains for compatibility with older saved configs.
    """
    data = {}
    for key in OPTIMIZER_MANAGED_KEYS:
        if key in ("runtime_mode", "label"):
            continue
        if key not in (config or {}):
            continue
        value = (config or {}).get(key)
        if isinstance(value, bool):
            continue
        data[key] = value

    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mark_optimizer_fingerprint(config):
    """
    Mark optimizer as clean after Apply Optimization.

    The fingerprint is kept for backward compatibility, but drift detection below
    is now semantic/profile-aware.
    """
    config["optimizer_config_fingerprint"] = optimizer_fingerprint(config)
    config["optimizer_runtime_profiles_fingerprint"] = file_sha256(RUNTIME_PROFILES_PATH)
    config["optimizer_fingerprint_version"] = "managed-runtime-v3-profile-aware"
    config["optimizer_config_changed"] = False
    config["optimizer_config_changed_reason"] = ""
    return config


def optimizer_config_drift(config):
    """
    Return True only when the active optimizer state is really out of sync.

    Rules:
    - Do NOT drift on boolean true/false changes.
    - Do NOT drift when user switches manual mode between basic/medium/ultra/download.
      The values are compared against the selected mode inside runtime_profiles.json.
    - DO drift when runtime_mode is auto, because Auto can tune values outside the
      selected optimizer profile.
    - DO drift when a non-boolean optimizer-managed value is manually changed and no
      longer matches the selected manual runtime profile.
    """
    cfg = config or {}

    # Old configs without optimizer mark should not be reported as changed.
    saved_config = str(cfg.get("optimizer_config_fingerprint") or "").strip()
    if not saved_config:
        return False

    mode = str(cfg.get("runtime_mode") or "basic").strip().lower()

    # Auto mode is intentionally treated as "out of optimizer profile".
    if mode == "auto" or bool(cfg.get("auto_tune_enabled", False)):
        cfg["optimizer_config_changed_reason"] = "runtime mode is auto"
        return True

    manual_modes = {"basic", "medium", "ultra", "download"}
    if mode not in manual_modes:
        mode = "basic"

    try:
        profiles = load_json_file(RUNTIME_PROFILES_PATH)
    except Exception:
        cfg["optimizer_config_changed_reason"] = "runtime_profiles.json could not be loaded"
        return True

    if not isinstance(profiles, dict) or mode not in profiles:
        cfg["optimizer_config_changed_reason"] = f"runtime profile '{mode}' is missing"
        return True

    expected = profiles.get(mode) or {}
    if not isinstance(expected, dict):
        cfg["optimizer_config_changed_reason"] = f"runtime profile '{mode}' is invalid"
        return True

    ignored_keys = {
        # Mode switching should not count as config drift.
        "runtime_mode",
        "label",

        # Boolean/switch-style keys are ignored by request.
        "enable_batch",
        "enable_sub_batch",
        "video_prefetch_enabled",
        "manifest_prefetch_enabled",
        "video_passthrough_enabled",
        "youtube_sabr_booster_enabled",
        "turbo_mode_enabled",
        "turbo_force_no_delay",
        "turbo_skip_download_mode",
        "telegram_mode_enabled",
    }

    # If H2 was disabled from dashboard, h2_connections becomes 0.
    # That is a switch effect, not a manual optimizer value change.
    if bool(cfg.get("h2_disabled_by_dashboard", False)):
        ignored_keys.add("h2_connections")

    def _norm(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, list):
            return [_norm(x) for x in value]
        if isinstance(value, dict):
            return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        return value

    for key in OPTIMIZER_MANAGED_KEYS:
        if key in ignored_keys:
            continue

        if key not in expected:
            continue

        current_value = cfg.get(key)
        expected_value = expected.get(key)

        # Ignore booleans everywhere, even if a new boolean key is later added.
        if isinstance(current_value, bool) or isinstance(expected_value, bool):
            continue

        if _norm(current_value) != _norm(expected_value):
            cfg["optimizer_config_changed_reason"] = (
                f"{key} changed from optimizer profile value "
                f"{expected_value!r} to {current_value!r}"
            )
            return True

    cfg["optimizer_config_changed_reason"] = ""
    return False



# PATCH_APPLY_SCRIPT_OPTIMIZER_MISSING_FUNC_START
def apply_script_optimizer(config, target_tier=None):
    """
    Apply Script Count Optimizer safely.

    Fixes:
    - handle_optimizer_post() calls apply_script_optimizer(), but some main.py
      builds did not define it.
    - Supports selecting a target tier from dashboard.
    - Keeps all Script IDs; tier only controls optimizer profile.
    - Marks optimizer fingerprint clean after apply.
    """
    cfg = deepcopy(config or {})

    ids = tolerant_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])
    if not ids:
        raise RuntimeError("at least one script_id is required")

    cfg["script_ids"] = ids
    cfg.pop("script_id", None)

    count = len(ids)
    max_tier = script_tier_for_count(count)

    requested = safe_int(target_tier, max_tier)
    if requested <= 0:
        requested = max_tier

    tier = max(1, min(max_tier, requested))

    cfg["script_count"] = count
    cfg["script_tier"] = tier
    cfg["optimized_for_script_count"] = tier
    cfg["optimized_at"] = int(time.time())
    cfg.setdefault("optimizer_version", "script-count-v1")

    if optimize_config_for_script_count is not None:
        optimized = None

        # Try newer possible signatures first.
        try:
            optimized = optimize_config_for_script_count(cfg, target_tier=tier)
        except TypeError:
            try:
                optimized = optimize_config_for_script_count(cfg, tier)
            except TypeError:
                optimized = optimize_config_for_script_count(cfg)

        if isinstance(optimized, dict):
            cfg = optimized

    # Force dashboard/user-selected tier metadata after optimizer call.
    cfg["script_ids"] = ids
    cfg.pop("script_id", None)
    cfg["script_count"] = count
    cfg["script_tier"] = tier
    cfg["optimized_for_script_count"] = tier
    cfg["optimized_at"] = int(time.time())
    cfg["optimizer_version"] = str(cfg.get("optimizer_version") or "script-count-v1")

    try:
        cfg = mark_optimizer_fingerprint(cfg)
    except Exception:
        cfg["optimizer_config_changed"] = False
        cfg["optimizer_config_changed_reason"] = ""

    return cfg
# PATCH_APPLY_SCRIPT_OPTIMIZER_MISSING_FUNC_END


def handle_optimizer_get(handler):
    try:
        config = WEB_RUNTIME.get("config") or load_config_file()
        handler.send_json(optimizer_status_payload(config))
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 500)


def handle_optimizer_post(handler, data):
    try:
        current = WEB_RUNTIME.get("config") or {}
        try:
            config = load_config_file()
        except Exception:
            config = deepcopy(current)

        old_ids = tolerant_script_ids(config.get("script_ids") or config.get("script_id") or [])
        new_ids = strict_script_ids(data.get("script_ids", data.get("ids", old_ids)))
        if not new_ids:
            handler.send_json({"ok": False, "error": "at least one script_id is required"}, 400)
            return

        apply_now = bool(data.get("apply", False))
        target_tier = data.get("target_tier", data.get("tier", data.get("script_tier")))

        config["script_ids"] = new_ids
        config.pop("script_id", None)
        ids_changed = old_ids != new_ids

        if apply_now:
            config = apply_script_optimizer(config, target_tier)
        else:
            count = len(new_ids)
            max_tier = script_tier_for_count(count)
            active = safe_int(config.get("optimized_for_script_count") or config.get("script_tier") or max_tier, max_tier)
            config["script_count"] = count
            config["script_tier"] = min(max_tier, max(1, active))
            config["optimized_for_script_count"] = min(max_tier, max(1, active))
            config.setdefault("optimizer_version", "script-count-v1")

        write_config_file(config)
        removed = reset_quota_state() if (ids_changed or apply_now) else []

        if apply_now and optimize_and_write_runtime_profiles is not None:
            try:
                optimize_and_write_runtime_profiles(config)
                config = mark_optimizer_fingerprint(config)
                write_config_file(config)
            except Exception as exc:
                logging.getLogger("Main").warning("runtime_profiles optimizer failed: %s", exc)

        with RUNTIME_LOCK:
            runtime_config = WEB_RUNTIME.get("config")
            if isinstance(runtime_config, dict):
                runtime_config.clear()
                runtime_config.update(config)
            else:
                WEB_RUNTIME["config"] = config
            server = WEB_RUNTIME.get("server")
            if server is not None and hasattr(server, "apply_runtime_config"):
                server.apply_runtime_config(config)
            reset_runtime_baselines("script_optimizer")

        lang = str(config.get("mhr_lang") or "en").lower()
        tier = safe_int(config.get("optimized_for_script_count") or config.get("script_tier"), 0)
        if optimizer_message is not None:
            msg = optimizer_message(tier, lang)
        elif lang.startswith("fa"):
            msg = f"کانفیگ مناسب Tier {tier} برای {len(new_ids)} اسکریپت ID ذخیره شد."
        else:
            msg = f"Optimized tier {tier} config saved for {len(new_ids)} script ID(s)."

        if apply_now:
            threading.Thread(target=lambda: (time.sleep(1.2), request_self_restart(0.5)), daemon=True).start()

        payload = optimizer_status_payload(config)
        payload.update({
            "message": msg,
            "ids_changed": ids_changed,
            "quota_reset": bool(removed),
            "removed": removed,
            "restart_required": apply_now,
            "restart_delay_seconds": 6 if apply_now else 0,
        })
        handler.send_json(payload)
    except Exception as exc:
        handler.send_json({"ok": False, "error": str(exc)}, 500)


def is_sabr_line(text):
    t = str(text or "").lower()
    return "sabr boost" in t or "youtube sabr" in t or "video sabr direct" in t or ("sabr" in t and "googlevideo" in t)


def is_sabr_success(text):
    t = str(text or "").lower()
    return any(x in t for x in ("sabr boost ok", "sabr direct", "video sabr direct", "status=200", " 200 ", "success", "complete", "done"))


def is_sabr_failure(text):
    t = str(text or "").lower()
    return any(x in t for x in ("sabr boost failed", "sabr boost error", "error", "failed", "timeout", " 403 ", " 429 ", " 500 ", " 502 ", " 503 ", " 504 ", "status=4", "status=5", "relay error"))


def get_sabr_summary():
    total = ok = failed = 0
    last = "-"
    for line in list(sabr_logs)[-200:]:
        if not is_sabr_line(line):
            continue
        total += 1
        last = str(line)[-180:]
        if is_sabr_failure(line):
            failed += 1
        elif is_sabr_success(line):
            ok += 1
    unknown = max(0, total - ok - failed)
    success_rate = round((ok / max(1, ok + failed)) * 100, 1) if ok + failed else 0
    return {"total": total, "success": ok, "failed": failed, "unknown": unknown, "success_rate": success_rate, "last": last}


def get_sabr_dashboard_stats():
    summary = get_sabr_summary()
    decided = summary["success"] + summary["failed"]
    return {
        "sabr_total_logs": summary["total"],
        "sabr_success": summary["success"],
        "sabr_failed": summary["failed"],
        "sabr_unknown": summary["unknown"],
        "sabr_success_rate": f"{round((summary['success'] / decided) * 100, 1)}%" if decided else "-",
    }


def script_runtime_key(item):
    return str(item.get("sid") or item.get("script") or item.get("short_id") or "").strip()


def reset_runtime_baselines(reason="-"):
    WEB_RUNTIME["runtime_stats_baseline"] = None
    WEB_RUNTIME["script_runtime_baseline"] = None
    WEB_RUNTIME["runtime_baseline_since"] = int(time.time())
    WEB_RUNTIME["runtime_baseline_reason"] = str(reason or "-")
    try:
        tuner = WEB_RUNTIME.get("auto_tuner")
        if tuner is not None:
            tuner.cached = None
            tuner.cached_at = 0
    except Exception:
        pass


def apply_runtime_baselines(snapshot):
    if not isinstance(snapshot, dict):
        return snapshot

    now = int(time.time())
    if WEB_RUNTIME.get("runtime_stats_baseline") is None:
        WEB_RUNTIME["runtime_stats_baseline"] = {
            key: int(snapshot.get(key, 0) or 0)
            for key in (
                "google_requests", "proxy_requests", "errors", "bytes_to_google",
                "bytes_from_google", "bytes_to_client", "bytes_from_client", "quota_used",
            )
        }
        WEB_RUNTIME["runtime_baseline_since"] = now
        WEB_RUNTIME.setdefault("runtime_baseline_reason", "startup")

    base = WEB_RUNTIME.get("runtime_stats_baseline") or {}

    def delta(key):
        return max(0, safe_int(snapshot.get(key, 0), 0) - safe_int(base.get(key, 0), 0))

    snapshot["runtime_google_requests"] = delta("google_requests")
    snapshot["runtime_proxy_requests"] = delta("proxy_requests")
    snapshot["runtime_errors"] = delta("errors")
    snapshot["runtime_bytes_to_google"] = delta("bytes_to_google")
    snapshot["runtime_bytes_from_google"] = delta("bytes_from_google")
    snapshot["runtime_bytes_to_client"] = delta("bytes_to_client")
    snapshot["runtime_bytes_from_client"] = delta("bytes_from_client")
    snapshot["runtime_quota_delta"] = delta("quota_used")
    snapshot["runtime_error_rate"] = round((snapshot["runtime_errors"] / max(1, snapshot["runtime_google_requests"])) * 100, 3)

    since = safe_int(WEB_RUNTIME.get("runtime_baseline_since", now), now)
    snapshot["runtime_baseline_since"] = since
    snapshot["runtime_baseline_age_seconds"] = max(0, now - since)
    snapshot["runtime_baseline_reason"] = WEB_RUNTIME.get("runtime_baseline_reason", "startup")

    scripts = snapshot.get("script_ids") or []
    if WEB_RUNTIME.get("script_runtime_baseline") is None:
        WEB_RUNTIME["script_runtime_baseline"] = {}

    baseline = WEB_RUNTIME["script_runtime_baseline"]
    for item in scripts:
        key = script_runtime_key(item)
        if not key:
            continue
        baseline.setdefault(key, {
            "requests": safe_int(item.get("requests"), 0),
            "errors": safe_int(item.get("errors"), 0),
            "bytes": safe_int(item.get("bytes"), 0),
        })
        b = baseline[key]
        req = max(0, safe_int(item.get("requests"), 0) - safe_int(b.get("requests"), 0))
        err = max(0, safe_int(item.get("errors"), 0) - safe_int(b.get("errors"), 0))
        byt = max(0, safe_int(item.get("bytes"), 0) - safe_int(b.get("bytes"), 0))
        item["runtime_requests"] = req
        item["runtime_errors"] = err
        item["runtime_bytes"] = byt
        item["runtime_error_rate"] = round((err / max(1, req)) * 100, 2)

    return snapshot


def effective_h2_status(config, effective):
    h2_disabled = bool((config or {}).get("h2_disabled_by_dashboard", False))
    configured_total = safe_int((effective or {}).get("h2_connections", (config or {}).get("h2_connections", 0)), 0)
    live = safe_int((effective or {}).get("h2_live_connections", 0), 0)
    until = safe_int((effective or {}).get("h2_disabled_until", 0), 0)
    now = int(time.time())
    cooldown = max(0, until - now) if until > now else max(0, until)
    available = bool((effective or {}).get("h2_available", False))

    if h2_disabled or configured_total <= 0:
        return {
            "h2_connections": 0, "h2_live_connections": 0, "h2_available": False,
            "h2_disabled_until": 0, "h2_disabled_by_dashboard": True,
            "h2_dashboard_on": False, "h2_status_text": "OFF", "h2_status_class": "red",
        }
    if cooldown > 0:
        return {
            "h2_connections": configured_total, "h2_live_connections": live, "h2_available": available,
            "h2_disabled_until": cooldown, "h2_disabled_by_dashboard": False,
            "h2_dashboard_on": True, "h2_status_text": f"Cooldown {cooldown}s", "h2_status_class": "yellow",
        }
    if available and live > 0:
        return {
            "h2_connections": configured_total, "h2_live_connections": live, "h2_available": True,
            "h2_disabled_until": 0, "h2_disabled_by_dashboard": False,
            "h2_dashboard_on": True, "h2_status_text": f"ON {live}/{configured_total}", "h2_status_class": "green",
        }
    return {
        "h2_connections": configured_total, "h2_live_connections": live, "h2_available": False,
        "h2_disabled_until": 0, "h2_disabled_by_dashboard": False,
        "h2_dashboard_on": True, "h2_status_text": f"Starting {live}/{configured_total}", "h2_status_class": "yellow",
    }


def get_suggestion_payload():
    config = WEB_RUNTIME.get("config") or {}
    tuner = WEB_RUNTIME.get("auto_tuner")
    if tuner is None:
        if AutoTuner is None:
            return {
                "ok": False,
                "note": "AutoTuner is not available.",
                "error_rate": "-",
                "next_refresh_in": "-",
                "score": {},
                "reasons": ["suggestions/auto_tuner.py could not be imported."],
                "changes": [],
            }
        tuner = AutoTuner(config)
        WEB_RUNTIME["auto_tuner"] = tuner

    snap = stats.snapshot()
    apply_runtime_baselines(snap)
    snap["exit_node_health"] = WEB_RUNTIME.get("server_health")
    snap["exit_node_health_last"] = WEB_RUNTIME.get("server_health_last", 0)
    snap["exit_node_health_error"] = WEB_RUNTIME.get("server_health_error", "-")
    snap["config"] = dict(config)
    logs = {
        "live": list(live_logs)[-100:],
        "errors": list(error_logs)[-100:],
        "downloads": list(download_logs)[-100:],
        "video": list(video_logs)[-100:],
        "sabr": list(sabr_logs)[-100:],
    }
    return tuner.suggest(snap, logs)


def fetch_exit_node_health_via_relay(config):
    url = str((config or {}).get("exit_node_health_url") or "").rstrip("/")
    key = str((config or {}).get("exit_node_health_key") or "")
    timeout = float((config or {}).get("exit_node_health_timeout", 45) or 45)
    if not url or not key:
        raise RuntimeError("exit node health is not configured")

    proxy_host = str((config or {}).get("listen_host") or "127.0.0.1")
    if proxy_host in ("0.0.0.0", "::"):
        proxy_host = "127.0.0.1"
    proxy_port = int((config or {}).get("listen_port") or 8080)
    proxy_url = f"http://{proxy_host}:{proxy_port}"

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    req = urllib.request.Request(
        url,
        headers={
            "X-Health-Key": key,
            "Cache-Control": "no-store",
            "User-Agent": "MHR-Dashboard-Relay-Health/1.0",
        },
        method="GET",
    )
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
        data = json.loads(raw)
        data["_via"] = "local_proxy_gas_relay"
        data["_proxy"] = proxy_url
        return data


def exit_node_health_loop(config):
    log = logging.getLogger("ServerHealth")
    while not SHUTDOWN_EVENT.is_set():
        try:
            data = fetch_exit_node_health_via_relay(config)
            WEB_RUNTIME["server_health"] = data
            WEB_RUNTIME["server_health_last"] = int(time.time())
            WEB_RUNTIME["server_health_error"] = "-"
            tuner = WEB_RUNTIME.get("auto_tuner")
            if tuner is not None:
                tuner.cached = None
                tuner.cached_at = 0
            log.info("Exit node health OK via relay")
        except Exception as exc:
            WEB_RUNTIME["server_health_error"] = str(exc)
            tuner = WEB_RUNTIME.get("auto_tuner")
            if tuner is not None:
                try:
                    tuner.cached = None
                    tuner.cached_at = 0
                except Exception:
                    pass
            log.warning("Exit node health via relay failed: %s", exc)

        delay = max(30, safe_int((config or {}).get("exit_node_health_interval", 120), 120))
        SHUTDOWN_EVENT.wait(delay)


def start_exit_node_health_monitor(config):
    thread = threading.Thread(target=exit_node_health_loop, args=(config,), daemon=True)
    thread.start()
    return thread


def collect_effective_state(server, config):
    effective = {}
    try:
        if server is not None and hasattr(server, "export_runtime_state"):
            effective.update(server.export_runtime_state() or {})
        fronter = getattr(server, "fronter", None)
        if fronter is not None and hasattr(fronter, "export_runtime_state"):
            front = fronter.export_runtime_state() or {}
            for key in (
                "h2_connections", "h2_live_connections", "h2_available", "h2_disabled_until", "h2_rebuilding",
                "batch_enabled", "enable_batch", "enable_sub_batch", "batch_window_micro", "batch_window_macro",
                "batch_max", "blacklisted_scripts", "front_connect_host", "front_sni_host", "front_http_host",
                "front_ip", "front_ip_recent_timeouts", "front_ip_diagnostic_threshold", "sni_rotation",
                "disabled_scripts", "blacklisted_script_details", "last_blacklist_events", "pool_max", "pool_min_idle", "pool_idle",
            ):
                if key in front:
                    effective[key] = front[key]
    except Exception as exc:
        logging.getLogger("Main").debug("dashboard effective state failed: %s", exc)
    return effective


def build_dashboard_config(config, effective):
    h2 = effective_h2_status(config, effective)
    out = {
        "listen_host": effective.get("listen_host", config.get("listen_host", "127.0.0.1")),
        "listen_port": effective.get("listen_port", config.get("listen_port", "-")),
        "socks5_enabled": effective.get("socks5_enabled", config.get("socks5_enabled", True)),
        "socks5_port": effective.get("socks5_port", config.get("socks5_port", "-")),
        "relay_timeout": config.get("relay_timeout", effective.get("relay_timeout", "-")),
        "range_probe_timeout": effective.get("range_probe_timeout", config.get("range_probe_timeout", "-")),
        "h2_stream_timeout": effective.get("h2_stream_timeout", config.get("h2_stream_timeout", "-")),
        "h2_connect_timeout": effective.get("h2_connect_timeout", config.get("h2_connect_timeout", "-")),
        "tls_connect_timeout": effective.get("tls_connect_timeout", config.get("tls_connect_timeout", "-")),
        "tcp_connect_timeout": config.get("tcp_connect_timeout", "-"),
        "parallel_relay": effective.get("parallel_relay", config.get("parallel_relay", "-")),
        "chunked_download_chunk_size": effective.get("chunked_download_chunk_size", config.get("chunked_download_chunk_size", 0)),
        "chunked_download_max_parallel": effective.get("chunked_download_max_parallel", config.get("chunked_download_max_parallel", "-")),
        "chunked_download_extensions": effective.get("chunked_download_extensions", config.get("chunked_download_extensions", [])),
        "video_prefetch_enabled": effective.get("video_prefetch_enabled", config.get("video_prefetch_enabled", False)),
        "video_prefetch_next_ranges": effective.get("video_prefetch_next_ranges", config.get("video_prefetch_next_ranges", "-")),
        "video_prefetch_parallel": effective.get("video_prefetch_parallel", config.get("video_prefetch_parallel", "-")),
        "video_prefetch_chunk_size": effective.get("video_prefetch_chunk_size", config.get("video_prefetch_chunk_size", 0)),
        "manifest_prefetch_enabled": effective.get("manifest_prefetch_enabled", config.get("manifest_prefetch_enabled", False)),
        "manifest_prefetch_next_segments": effective.get("manifest_prefetch_next_segments", config.get("manifest_prefetch_next_segments", "-")),
        "manifest_prefetch_parallel": effective.get("manifest_prefetch_parallel", config.get("manifest_prefetch_parallel", "-")),
        "manifest_cache_max_mb": config.get("manifest_cache_max_mb", 0),
        "video_cache_max_mb": config.get("video_cache_max_mb", 0),
        "video_cache_ttl_seconds": config.get("video_cache_ttl_seconds", "-"),
        "manifest_cache_ttl_seconds": config.get("manifest_cache_ttl_seconds", "-"),
        "youtube_via_relay": config.get("youtube_via_relay", False),
        "video_passthrough_enabled": effective.get("video_passthrough_enabled", config.get("video_passthrough_enabled", False)),
        "video_passthrough_relay_timeout": effective.get("video_passthrough_relay_timeout", config.get("video_passthrough_relay_timeout", "-")),
        "youtube_sabr_booster_enabled": effective.get("youtube_sabr_booster_enabled", config.get("youtube_sabr_booster_enabled", True)),
        "youtube_sabr_timeout": effective.get("youtube_sabr_timeout", config.get("youtube_sabr_timeout", 180)),
        "youtube_sabr_max_parallel": effective.get("youtube_sabr_max_parallel", config.get("youtube_sabr_max_parallel", 2)),
        "youtube_sabr_retry_attempts": effective.get("youtube_sabr_retry_attempts", config.get("youtube_sabr_retry_attempts", 2)),
        "youtube_sabr_retry_delay_ms": effective.get("youtube_sabr_retry_delay_ms", config.get("youtube_sabr_retry_delay_ms", 120)),
        "telegram_mode_enabled": effective.get("telegram_mode_enabled", config.get("telegram_mode_enabled", False)),
        "telegram_parallel_relay": effective.get("telegram_parallel_relay", config.get("telegram_parallel_relay", "-")),
        "turbo_mode_enabled": effective.get("turbo_mode_enabled", config.get("turbo_mode_enabled", False)),
        "turbo_parallel_relay": effective.get("turbo_parallel_relay", config.get("turbo_parallel_relay", config.get("parallel_relay", "-"))),
        "turbo_force_no_delay": effective.get("turbo_force_no_delay", config.get("turbo_force_no_delay", True)),
        "turbo_skip_download_mode": effective.get("turbo_skip_download_mode", config.get("turbo_skip_download_mode", False)),
        "turbo_coalesce_window_ms": effective.get("turbo_coalesce_window_ms", config.get("turbo_coalesce_window_ms", 0)),
        "turbo_min_upload_padding": effective.get("turbo_min_upload_padding", config.get("turbo_min_upload_padding", 0)),
        "enable_batch": effective.get("enable_batch", config.get("enable_batch", True)),
        "batch_enabled": effective.get("batch_enabled", config.get("enable_batch", True)),
        "enable_sub_batch": effective.get("enable_sub_batch", config.get("enable_sub_batch", True)),
        "batch_window_micro": effective.get("batch_window_micro", config.get("batch_window_micro", "-")),
        "batch_window_macro": effective.get("batch_window_macro", config.get("batch_window_macro", "-")),
        "batch_max": effective.get("batch_max", config.get("batch_max", "-")),
        "blacklisted_scripts": effective.get("blacklisted_scripts", 0),
        "auto_tune_enabled": bool(config.get("auto_tune_enabled", False)),
        "auto_mode_ready": bool(config.get("auto_mode_ready", False)),
        "auto_tune_h2": bool(config.get("auto_tune_h2", True)),
        "auto_tune_last_reason": config.get("auto_tune_last_reason", "-"),
        "auto_tune_last_change_at": config.get("auto_tune_last_change_at", 0),
        "safe_mode_active": bool(config.get("_safe_mode_active", False)),
        "turbo_disabled_by_dashboard": bool(config.get("turbo_disabled_by_dashboard", False)),
        "sabr_disabled_by_dashboard": bool(config.get("sabr_disabled_by_dashboard", False)),
        "video_prefetch_disabled_by_dashboard": bool(config.get("video_prefetch_disabled_by_dashboard", False)),
        "manifest_prefetch_disabled_by_dashboard": bool(config.get("manifest_prefetch_disabled_by_dashboard", False)),
        "video_passthrough_disabled_by_dashboard": bool(config.get("video_passthrough_disabled_by_dashboard", False)),
        **h2,
    }

    extra_keys = (
        "mode", "runtime_mode", "label", "script_blacklist_ttl", "google_ip", "front_domain", "front_domains",
        "script_quota_limit", "verify_ssl", "lan_sharing", "max_response_body_bytes", "chunked_download_min_size",
        "chunked_download_max_chunks", "block_hosts", "bypass_hosts", "direct_google_exclude", "direct_google_allow",
        "hosts", "video_priority_retry_attempts", "video_priority_retry_delay_ms", "video_priority_parallel_relay",
        "turbo_small_request_max", "turbo_chunk_size", "turbo_parallel", "turbo_min_size",
        "telegram_cache_ttl_seconds", "telegram_hosts", "telegram_serial_per_host", "telegram_coalesce_window_ms",
        "telegram_max_delay_ms", "telegram_burst_limit", "telegram_burst_cooldown_ms", "telegram_cidrs",
        "downloader_dir", "downloader_chunk_size", "downloader_parallel", "downloader_retries", "downloader_h2_enabled",
        "downloader_h2_wait_seconds", "downloader_h2_timeout", "downloader_user_agent", "downloader_headers",
        "exit_node_health_url", "exit_node_health_timeout", "exit_node_health_interval",
        "front_ip_diagnostic_window_seconds", "front_ip_diagnostic_timeout_threshold", "front_ip_diagnostic_warning_cooldown",
        "script_count", "script_tier", "optimized_for_script_count", "optimized_at", "optimizer_version",
    )
    for key in extra_keys:
        if key in config and key not in out:
            out[key] = config.get(key)

    for key in (
        "front_connect_host", "front_sni_host", "front_http_host", "front_ip", "front_ip_recent_timeouts",
        "front_ip_diagnostic_threshold", "h2_available", "h2_rebuilding", "sni_rotation", "disabled_scripts",
        "blacklisted_script_details", "last_blacklist_events", "pool_max", "pool_min_idle", "pool_idle",
    ):
        if key in effective:
            out[key] = effective.get(key)

    script_ids = config.get("script_ids") or config.get("script_id") or []
    out["script_ids_count"] = len(tolerant_script_ids(script_ids))
    out["front_domains_count"] = len(config.get("front_domains") or [])
    out["direct_google_exclude_count"] = len(config.get("direct_google_exclude") or [])
    out["telegram_cidrs_count"] = len(config.get("telegram_cidrs") or [])
    out["disabled_scripts_count"] = len(effective.get("disabled_scripts") or [])
    out["has_auth_key"] = bool(config.get("auth_key"))
    out["exit_node_health_key_set"] = bool(config.get("exit_node_health_key"))

    try:
        drift = optimizer_config_drift(config)
        out["optimizer_config_drift"] = drift
        out["optimizer_config_changed"] = drift
        out["config_changed"] = drift
        out["optimizer_config_fingerprint_set"] = bool(config.get("optimizer_config_fingerprint"))
        out["optimizer_runtime_profiles_fingerprint_set"] = bool(config.get("optimizer_runtime_profiles_fingerprint"))
        out["optimizer_fingerprint_version"] = config.get("optimizer_fingerprint_version", "-")
    except Exception:
        out["optimizer_config_drift"] = False
        out["optimizer_config_fingerprint_set"] = False
        out["optimizer_fingerprint_version"] = "-"

    for secret_key in ("auth_key", "script_id", "script_ids", "exit_node_health_key"):
        out.pop(secret_key, None)

    return out


def build_stats_payload():
    snap = stats.snapshot()
    apply_runtime_baselines(snap)
    config = WEB_RUNTIME.get("config") or {}
    mode_state = WEB_RUNTIME.get("mode_state") or {}
    server = WEB_RUNTIME.get("server")
    effective = collect_effective_state(server, config)

    snap["web_mode"] = str(config.get("runtime_mode") or mode_state.get("key") or mode_state.get("mode") or "-")
    snap["exit_node_health"] = WEB_RUNTIME.get("server_health")
    snap["exit_node_health_last"] = WEB_RUNTIME.get("server_health_last", 0)
    snap["exit_node_health_error"] = WEB_RUNTIME.get("server_health_error", "-")
    snap.update(get_sabr_dashboard_stats())
    snap["sabr_summary"] = get_sabr_summary()

    if WEB_RUNTIME.get("quota_start") is None:
        WEB_RUNTIME["quota_start"] = safe_int(snap.get("quota_used"), 0)
    snap["runtime_quota_used"] = max(0, safe_int(snap.get("quota_used"), 0) - safe_int(WEB_RUNTIME.get("quota_start"), 0))

    snap["config"] = build_dashboard_config(config, effective)

    try:
        snap["system"] = {
            "pid": PROCESS.pid,
            "rss": PROCESS.memory_info().rss,
            "threads": PROCESS.num_threads(),
            "cpu_percent": f"{float(PROCESS.cpu_percent(interval=None) or 0.0):.1f}%",
        }
    except Exception:
        snap["system"] = {}

    return snap


class StatsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    # MHR_HANDLER_ALIAS_FIX
    def _send_json(self, data, status=200):
        return self.send_json(data, status)

    def _send_html(self, html, status=200):
        return self.send_html(html, status)

    def send_json(self, data, status=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def send_html(self, html, status=200):
        try:
            body = str(html or "").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def send_static(self, filename):
        try:
            body, content_type = read_static_bytes(filename)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def read_json_body(self):
        length = safe_int(self.headers.get("Content-Length", "0"), 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
        return json.loads(raw or "{}")

    def do_GET(self):
        path = urlparse(self.path).path

        # MHR_DOWNLOADER_ROUTE_FIX_V2
        if (
            path in ("/downloader", "/downloader/", "/downloads", "/download")
            or path.startswith("/api/downloader/")
        ):
            try:
                if handle_downloader_get(self, WEB_RUNTIME):
                    return
            except Exception as exc:
                try:
                    self._send_json({"ok": False, "error": "downloader route failed: " + str(exc)}, 500)
                except Exception:
                    self.send_response(500)
                    self.end_headers()
                return


        if handle_setup_get is not None and handle_setup_get(self, WEB_RUNTIME):
            return

        if path in ("/api/script-optimizer", "/api/script-optimizer/status", "/api/scripts/optimizer", "/api/scripts/optimizer/status"):
            handle_optimizer_get(self)
            return

        if path in ("/", "/dashboard"):
            self.send_html(read_static_text("dashboard.html"))
            return

        if path.startswith("/static/"):
            self.send_static(path.rsplit("/", 1)[-1])
            return

        if handle_downloader_get(self, WEB_RUNTIME):
            return

        if path in ("/suggestions", "/suggestion"):
            self.send_html(render_suggestions_html(get_suggestion_payload()))
            return

        if path in ("/api/suggestions", "/suggestion-data"):
            self.send_json(get_suggestion_payload())
            return

        if path in ("/server-health", "/api/server-health"):
            self.send_json({
                "ok": True,
                "data": WEB_RUNTIME.get("server_health"),
                "last": WEB_RUNTIME.get("server_health_last", 0),
                "error": WEB_RUNTIME.get("server_health_error", "-"),
            })
            return

        if path in ("/api/exit-node", "/exit-node-status", "/api/server-status"):
            try:
                self.send_json({"ok": True, "status": "online", "data": fetch_exit_node_health_via_relay(WEB_RUNTIME.get("config") or {})})
            except Exception as exc:
                self.send_json({"ok": False, "status": "offline_or_locked", "error": str(exc)}, 200)
            return

        if path in ("/stats", "/api/dashboard"):
            self.send_json(build_stats_payload())
            return

        if path in ("/logs", "/api/logs"):
            self.send_json({
                "live": list(live_logs)[-100:],
                "errors": list(error_logs)[-100:],
                "downloads": list(download_logs)[-100:],
                "video": list(video_logs)[-100:],
                "sabr": list(sabr_logs)[-100:],
            })
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.read_json_body()

            if handle_setup_post is not None and handle_setup_post(self, WEB_RUNTIME, data):
                return

            if path in (
                "/api/script-optimizer/save", "/api/script-optimizer/ids", "/api/script-optimizer/save-ids",
                "/api/script-optimizer/apply", "/api/script-optimizer/optimize", "/api/scripts/optimizer/save",
            ):
                if path in ("/api/script-optimizer/apply", "/api/script-optimizer/optimize"):
                    data["apply"] = True
                    if "tier" in data and "target_tier" not in data:
                        data["target_tier"] = data.get("tier")
                else:
                    data.setdefault("apply", False)
                handle_optimizer_post(self, data)
                return

            if handle_downloader_post(self, WEB_RUNTIME, data):
                return

            config = WEB_RUNTIME.get("config")
            server = WEB_RUNTIME.get("server")
            mode_state = WEB_RUNTIME.get("mode_state")
            if config is None or server is None or mode_state is None:
                self.send_json({"ok": False, "error": "runtime not ready"}, 503)
                return

            if path == "/apply-suggestion":
                self.handle_apply_suggestion(config, server, mode_state)
                return

            if path == "/clear-log":
                self.handle_clear_log(data)
                return

            if path == "/mode":
                self.handle_mode(data, config, server, mode_state)
                return

            if path == "/toggle":
                self.handle_toggle(data, config, server)
                return

            self.send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def handle_apply_suggestion(self, config, server, mode_state):
        if AutoTuner is None:
            self.send_json({"ok": False, "error": "AutoTuner is not available."}, 503)
            return
        tuner = WEB_RUNTIME.get("auto_tuner") or AutoTuner(config)
        WEB_RUNTIME["auto_tuner"] = tuner
        suggestion = get_suggestion_payload()
        with RUNTIME_LOCK:
            tuner.apply(config, suggestion)
            config["auto_mode_ready"] = True
            config["auto_tune_enabled"] = True
            config["runtime_mode"] = "auto"
            mode_state["name"] = "Auto Suggestion"
            mode_state["key"] = "auto"
            if hasattr(server, "apply_runtime_config"):
                server.apply_runtime_config(config)
            reset_runtime_baselines("apply_suggestion")
        logging.getLogger("Main").info("Suggestion applied -> auto mode id=%s", suggestion.get("id"))
        self.send_json({"ok": True, "mode": "auto", "suggestion": suggestion})

    def handle_clear_log(self, data):
        name = str(data.get("name", "")).strip().lower()
        logs = {"live": live_logs, "errors": error_logs, "downloads": download_logs, "video": video_logs, "sabr": sabr_logs}
        if name not in logs:
            self.send_json({"ok": False, "error": "invalid log name"}, 400)
            return
        logs[name].clear()
        logging.getLogger("Main").info("Cleared %s logs from web dashboard", name)
        self.send_json({"ok": True, "name": name})

    def handle_mode(self, data, config, server, mode_state):
        mode = str(data.get("mode", "")).strip().lower()
        if mode not in ("auto", "basic", "medium", "ultra", "download"):
            self.send_json({"ok": False, "error": "invalid mode"}, 400)
            return
        if mode == "auto" and not bool(config.get("auto_mode_ready", False)):
            self.send_json({"ok": False, "error": "Auto is locked. Open Suggestions and click Apply to Auto mode first."}, 400)
            return

        with RUNTIME_LOCK:
            if mode == "auto":
                label = "Auto Tune"
                config["runtime_mode"] = "auto"
                config["auto_tune_enabled"] = True
            else:
                label = apply_runtime_profile(config, mode)
                config["runtime_mode"] = mode
            mode_state["name"] = label
            mode_state["key"] = config.get("runtime_mode", mode)
            if hasattr(server, "apply_runtime_config"):
                server.apply_runtime_config(config)
            reset_runtime_baselines("mode:" + mode)

        logging.getLogger("Main").info("Web mode switch -> %s", mode)
        self.send_json({"ok": True, "mode": mode, "label": label})

    def handle_toggle(self, data, config, server):
        name = str(data.get("name", "")).strip().lower()
        with RUNTIME_LOCK:
            ok, error = apply_toggle(config, name)
            if not ok:
                self.send_json({"ok": False, "error": error}, 400)
                return
            if hasattr(server, "apply_runtime_config"):
                server.apply_runtime_config(config)
            reset_runtime_baselines("toggle:" + name)
        logging.getLogger("Main").info("Web toggle -> %s", name)
        self.send_json({"ok": True, "name": name})


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc_type, exc, tb = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError)):
            return
        return super().handle_error(request, client_address)


def start_stats_server(port=9099):
    global STATS_SERVER
    if STATS_SERVER is not None:
        try:
            STATS_SERVER.shutdown()
        except Exception:
            pass
        try:
            STATS_SERVER.server_close()
        except Exception:
            pass
        STATS_SERVER = None

    try:
        server = QuietThreadingHTTPServer(("127.0.0.1", int(port)), StatsHandler)
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98, 10048):
            print()
            print(f"[ERROR] Dashboard port {port} is already in use.")
            print("Close the old MHR process or change dashboard_port in config.json.")
        raise

    STATS_SERVER = server
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    return server


def apply_toggle(config, name):
    if name == "auto":
        if not bool(config.get("auto_mode_ready", False)):
            return False, "Auto is locked. Go to Suggestions and click Apply to Auto mode first."
        config["auto_tune_enabled"] = not bool(config.get("auto_tune_enabled", False))
        if config["auto_tune_enabled"]:
            config["runtime_mode"] = "auto"
        return True, ""

    if name == "disable_all":
        safe_on = bool(
            config.get("turbo_disabled_by_dashboard", False)
            and config.get("sabr_disabled_by_dashboard", False)
            and config.get("video_prefetch_disabled_by_dashboard", False)
            and config.get("manifest_prefetch_disabled_by_dashboard", False)
            and config.get("video_passthrough_disabled_by_dashboard", False)
            and config.get("h2_disabled_by_dashboard", False)
        )
        if safe_on:
            prev = config.get("_safe_mode_previous_state") or {}
            config["telegram_mode_enabled"] = bool(prev.get("telegram_mode_enabled", config.get("telegram_mode_enabled", False)))
            config["turbo_mode_enabled"] = bool(prev.get("turbo_mode_enabled", True))
            config["youtube_sabr_booster_enabled"] = bool(prev.get("youtube_sabr_booster_enabled", True))
            config["video_prefetch_enabled"] = bool(prev.get("video_prefetch_enabled", True))
            config["manifest_prefetch_enabled"] = bool(prev.get("manifest_prefetch_enabled", True))
            config["video_passthrough_enabled"] = bool(prev.get("video_passthrough_enabled", True))
            restore_h2 = safe_int(prev.get("h2_connections", config.get("_h2_connections_before_dashboard_off", 1)), 1)
            config["h2_connections"] = max(1, restore_h2)
            config["h2_disabled_by_dashboard"] = False
            config["_h2_connections_before_dashboard_off"] = config["h2_connections"]
            config["_safe_mode_active"] = False
        else:
            current_h2 = max(1, safe_int(config.get("h2_connections", 1), 1))
            config["_safe_mode_previous_state"] = {
                "telegram_mode_enabled": bool(config.get("telegram_mode_enabled", False)),
                "turbo_mode_enabled": bool(config.get("turbo_mode_enabled", False)),
                "youtube_sabr_booster_enabled": bool(config.get("youtube_sabr_booster_enabled", True)),
                "video_prefetch_enabled": bool(config.get("video_prefetch_enabled", False)),
                "manifest_prefetch_enabled": bool(config.get("manifest_prefetch_enabled", False)),
                "video_passthrough_enabled": bool(config.get("video_passthrough_enabled", False)),
                "h2_connections": current_h2,
            }
            config["telegram_mode_enabled"] = False
            config["turbo_mode_enabled"] = False
            config["youtube_sabr_booster_enabled"] = False
            config["video_prefetch_enabled"] = False
            config["manifest_prefetch_enabled"] = False
            config["video_passthrough_enabled"] = False
            config["h2_connections"] = 0
            config["_h2_connections_before_dashboard_off"] = current_h2
            config["_safe_mode_active"] = True

        config["turbo_disabled_by_dashboard"] = not bool(config.get("turbo_mode_enabled", False))
        config["sabr_disabled_by_dashboard"] = not bool(config.get("youtube_sabr_booster_enabled", False))
        config["video_prefetch_disabled_by_dashboard"] = not bool(config.get("video_prefetch_enabled", False))
        config["manifest_prefetch_disabled_by_dashboard"] = not bool(config.get("manifest_prefetch_enabled", False))
        config["video_passthrough_disabled_by_dashboard"] = not bool(config.get("video_passthrough_enabled", False))
        config["h2_disabled_by_dashboard"] = safe_on is False
        return True, ""

    if name == "telegram":
        config["telegram_mode_enabled"] = not bool(config.get("telegram_mode_enabled", False))
    elif name == "turbo":
        on = not bool(config.get("turbo_mode_enabled", False))
        config["turbo_mode_enabled"] = on
        config["turbo_disabled_by_dashboard"] = not on
    elif name == "sabr":
        on = not bool(config.get("youtube_sabr_booster_enabled", True))
        config["youtube_sabr_booster_enabled"] = on
        config["sabr_disabled_by_dashboard"] = not on
    elif name == "video_prefetch":
        on = not bool(config.get("video_prefetch_enabled", False))
        config["video_prefetch_enabled"] = on
        config["video_prefetch_disabled_by_dashboard"] = not on
    elif name == "manifest_prefetch":
        on = not bool(config.get("manifest_prefetch_enabled", False))
        config["manifest_prefetch_enabled"] = on
        config["manifest_prefetch_disabled_by_dashboard"] = not on
    elif name == "video_passthrough":
        on = not bool(config.get("video_passthrough_enabled", False))
        config["video_passthrough_enabled"] = on
        config["video_passthrough_disabled_by_dashboard"] = not on
    elif name == "h2":
        currently_disabled = bool(config.get("h2_disabled_by_dashboard", False)) or safe_int(config.get("h2_connections", 0), 0) <= 0
        if currently_disabled:
            restore = max(1, safe_int(config.get("_h2_connections_before_dashboard_off", config.get("h2_connections", 1)), 1))
            config["h2_disabled_by_dashboard"] = False
            config["h2_connections"] = restore
            config["_h2_connections_before_dashboard_off"] = restore
        else:
            current = max(1, safe_int(config.get("h2_connections", 1), 1))
            config["_h2_connections_before_dashboard_off"] = current
            config["h2_disabled_by_dashboard"] = True
            config["h2_connections"] = 0
    else:
        return False, "invalid toggle"

    return True, ""


def apply_runtime_profile(config, mode):
    try:
        profiles = load_json_file(RUNTIME_PROFILES_PATH)
    except Exception as exc:
        logging.getLogger("Main").error("Failed to load runtime_profiles.json: %s", exc)
        return str(config.get("runtime_mode", "basic"))

    key = str(mode or config.get("runtime_mode") or "basic").lower()
    auto_mode = key == "auto"
    if auto_mode:
        key = "basic"
    if key not in profiles:
        key = "basic"

    keep = {
        "telegram_mode_enabled": bool(config.get("telegram_mode_enabled", False)),
        "turbo_mode_enabled": bool(config.get("turbo_mode_enabled", False)),
        "youtube_sabr_booster_enabled": bool(config.get("youtube_sabr_booster_enabled", True)),
        "sabr_disabled_by_dashboard": bool(config.get("sabr_disabled_by_dashboard", False)),
        "turbo_disabled_by_dashboard": bool(config.get("turbo_disabled_by_dashboard", False)),
        "h2_disabled_by_dashboard": bool(config.get("h2_disabled_by_dashboard", False)),
        "video_prefetch_disabled_by_dashboard": bool(config.get("video_prefetch_disabled_by_dashboard", False)),
        "video_prefetch_enabled": bool(config.get("video_prefetch_enabled", False)),
        "manifest_prefetch_disabled_by_dashboard": bool(config.get("manifest_prefetch_disabled_by_dashboard", False)),
        "manifest_prefetch_enabled": bool(config.get("manifest_prefetch_enabled", False)),
        "video_passthrough_disabled_by_dashboard": bool(config.get("video_passthrough_disabled_by_dashboard", False)),
        "video_passthrough_enabled": bool(config.get("video_passthrough_enabled", False)),
    }

    profile = dict(profiles.get(key) or {})
    profile_h2 = max(1, safe_int(profile.get("h2_connections", config.get("h2_connections", 1)), 1))
    config.update(profile)
    config["runtime_mode"] = "auto" if auto_mode else key

    config["video_prefetch_disabled_by_dashboard"] = keep["video_prefetch_disabled_by_dashboard"]
    config["video_prefetch_enabled"] = False if keep["video_prefetch_disabled_by_dashboard"] else keep["video_prefetch_enabled"]
    config["manifest_prefetch_disabled_by_dashboard"] = keep["manifest_prefetch_disabled_by_dashboard"]
    config["manifest_prefetch_enabled"] = False if keep["manifest_prefetch_disabled_by_dashboard"] else keep["manifest_prefetch_enabled"]
    config["video_passthrough_disabled_by_dashboard"] = keep["video_passthrough_disabled_by_dashboard"]
    config["video_passthrough_enabled"] = False if keep["video_passthrough_disabled_by_dashboard"] else keep["video_passthrough_enabled"]

    if key != "download" and keep["telegram_mode_enabled"]:
        config["telegram_mode_enabled"] = True

    config["turbo_disabled_by_dashboard"] = keep["turbo_disabled_by_dashboard"]
    config["turbo_mode_enabled"] = False if keep["turbo_disabled_by_dashboard"] else keep["turbo_mode_enabled"]
    config["sabr_disabled_by_dashboard"] = keep["sabr_disabled_by_dashboard"]
    config["youtube_sabr_booster_enabled"] = False if keep["sabr_disabled_by_dashboard"] else keep["youtube_sabr_booster_enabled"]

    config["h2_disabled_by_dashboard"] = keep["h2_disabled_by_dashboard"]
    config["_h2_connections_before_dashboard_off"] = profile_h2
    config["h2_connections"] = 0 if keep["h2_disabled_by_dashboard"] else profile_h2

    return str(profile.get("label") or key)


def parse_args():
    parser = argparse.ArgumentParser(
        prog="domainfront-tunnel",
        description="Local HTTP proxy that relays traffic through Google Apps Script.",
    )
    parser.add_argument("-c", "--config", default=os.environ.get("DFT_CONFIG", "config.json"))
    parser.add_argument("-p", "--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--socks5-port", type=int, default=None)
    parser.add_argument("--disable-socks5", action="store_true")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=None)
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--install-cert", action="store_true")
    parser.add_argument("--uninstall-cert", action="store_true")
    parser.add_argument("--no-cert-check", action="store_true")
    parser.add_argument("--scan", action="store_true")
    return parser.parse_args()


def apply_overrides(args, config):
    if os.environ.get("DFT_AUTH_KEY"):
        config["auth_key"] = os.environ["DFT_AUTH_KEY"]
    if os.environ.get("DFT_SCRIPT_ID"):
        config["script_id"] = os.environ["DFT_SCRIPT_ID"]
    if args.port is not None:
        config["listen_port"] = args.port
    elif os.environ.get("DFT_PORT"):
        config["listen_port"] = int(os.environ["DFT_PORT"])
    if args.host is not None:
        config["listen_host"] = args.host
    elif os.environ.get("DFT_HOST"):
        config["listen_host"] = os.environ["DFT_HOST"]
    if args.socks5_port is not None:
        config["socks5_port"] = args.socks5_port
    elif os.environ.get("DFT_SOCKS5_PORT"):
        config["socks5_port"] = int(os.environ["DFT_SOCKS5_PORT"])
    if args.disable_socks5:
        config["socks5_enabled"] = False
    if args.log_level is not None:
        config["log_level"] = args.log_level
    elif os.environ.get("DFT_LOG_LEVEL"):
        config["log_level"] = os.environ["DFT_LOG_LEVEL"]


def default_setup_config():
    if default_config is not None:
        try:
            return default_config()
        except Exception:
            pass
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
        "front_domains": ["www.google.com", "mail.google.com"],
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
        "mhr_lang": "fa",
    }


def set_config_defaults(config):
    config.setdefault("auto_tune_enabled", False)
    config.setdefault("auto_mode_ready", False)
    config.setdefault("auto_tune_h2", True)
    config.setdefault("auto_tune_interval", 30)
    config.setdefault("auto_tune_suggestion_interval_seconds", 60)
    config.setdefault("video_prefetch_disabled_by_dashboard", False)
    config.setdefault("manifest_prefetch_disabled_by_dashboard", False)
    config.setdefault("video_passthrough_disabled_by_dashboard", False)
    config.setdefault("sabr_disabled_by_dashboard", False)
    config.setdefault("turbo_disabled_by_dashboard", False)
    config.setdefault("exit_node_health_url", "")
    config.setdefault("exit_node_health_key", "")
    config.setdefault("exit_node_health_interval", 120)
    config.setdefault("dashboard_port", 9099)
    config.setdefault("listen_host", "127.0.0.1")
    config.setdefault("listen_port", 8085)
    config.setdefault("socks5_enabled", True)
    config.setdefault("socks5_port", 1080)
    return config


def needs_setup(config, config_missing=False):
    config_completed = bool((config or {}).get("setup_completed", False))
    auth_key = str((config or {}).get("auth_key") or "").strip()
    auth_ok = bool(auth_key) and auth_key not in PLACEHOLDER_AUTH_KEYS
    ids = valid_config_script_ids(config or {})
    script_ids_ok = bool(ids)
    need = bool(config_missing) or not config_completed or not auth_ok or not script_ids_ok

    print("[SETUP-CHECK] config_missing  =", bool(config_missing))
    print("[SETUP-CHECK] setup_completed =", config_completed)
    print("[SETUP-CHECK] auth_ok         =", auth_ok)
    print("[SETUP-CHECK] script_count    =", len(ids))
    print("[SETUP-CHECK] script_ids_ok   =", script_ids_ok)
    print("[SETUP-CHECK] needs_setup     =", need)
    return need


def ensure_setup_cert(args=None):
    try:
        if args is not None and bool(getattr(args, "no_cert_check", False)):
            print("[SETUP] Certificate check skipped by --no-cert-check")
            return True
        if not os.path.exists(CA_CERT_FILE):
            print("[SETUP] Creating MITM CA certificate...")
            from mitm import MITMCertManager
            MITMCertManager()
        try:
            trusted = is_ca_trusted(CA_CERT_FILE)
        except Exception as exc:
            print(f"[SETUP] Could not check CA trust state: {exc}")
            trusted = False
        if trusted:
            print("[SETUP] MITM CA is already trusted.")
            return True
        print("[SETUP] Installing MITM CA certificate...")
        print("[SETUP] Windows may ask for Administrator permission.")
        if install_ca(CA_CERT_FILE):
            print("[SETUP] MITM CA installed. Restart browser if needed.")
            return True
        print("[SETUP] CA auto-install failed.")
        print("[SETUP] You can continue setup, but HTTPS interception may not work until CA is trusted.")
        print("[SETUP] Later you can run: python main.py --install-cert")
        return False
    except Exception as exc:
        print(f"[SETUP] Certificate setup failed: {exc}")
        print("[SETUP] You can continue setup, but run later: python main.py --install-cert")
        return False


def open_url_later(url, delay_seconds=0.6):
    def worker():
        try:
            time.sleep(max(0.1, float(delay_seconds or 0.6)))
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def request_self_restart(delay_seconds=1.5):
    if WEB_RUNTIME.get("setup_restart_requested"):
        return
    WEB_RUNTIME["setup_restart_requested"] = True
    WEB_RUNTIME["setup_restart_at"] = int(time.time())

    def worker():
        try:
            time.sleep(max(0.5, float(delay_seconds or 1.5)))
            shutdown_services(close_stats=True)
            print()
            print("[SETUP] Config saved. Restarting MHR with the new config...")
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as exc:
            print(f"[SETUP] Auto restart failed: {exc}")
            print("[SETUP] Please close this window and run: python main.py")
    threading.Thread(target=worker, daemon=True).start()


def shutdown_services(close_stats=False):
    try:
        mgr = WEB_RUNTIME.get("downloader")
        if mgr is not None and hasattr(mgr, "shutdown"):
            mgr.shutdown()
    except Exception:
        pass
    try:
        server = STATS_SERVER
        if server is not None:
            server.shutdown()
            if close_stats:
                server.server_close()
    except Exception:
        pass


def request_shutdown(signum=None, frame=None):
    global SHUTDOWN_PRINTED
    if not SHUTDOWN_PRINTED:
        SHUTDOWN_PRINTED = True
        print()
        print("Stopping MHR...")
    try:
        SHUTDOWN_EVENT.set()
    except Exception:
        pass
    try:
        threading.Thread(target=shutdown_services, kwargs={"close_stats": False}, daemon=True).start()
    except Exception:
        pass
    try:
        loop = MAIN_ASYNC_LOOP
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
    except Exception:
        pass


async def auto_tuner_loop(config, server):
    log = logging.getLogger("AutoTuner")
    tuner = WEB_RUNTIME.get("auto_tuner")
    if tuner is None:
        return
    while not SHUTDOWN_EVENT.is_set():
        try:
            await asyncio.sleep(float(config.get("auto_tune_interval", 30) or 30))
            if SHUTDOWN_EVENT.is_set():
                break
            if not bool(config.get("auto_tune_enabled", False)):
                continue
            if str(config.get("runtime_mode", "")).lower() != "auto":
                continue

            snap = stats.snapshot()
            apply_runtime_baselines(snap)
            effective = collect_effective_state(server, config)
            merged_config = dict(config or {})
            merged_config.update(effective or {})
            snap["config"] = merged_config
            snap["exit_node_health"] = WEB_RUNTIME.get("server_health")
            snap["exit_node_health_last"] = WEB_RUNTIME.get("server_health_last", 0)
            snap["exit_node_health_error"] = WEB_RUNTIME.get("server_health_error", "-")
            logs = {
                "live": list(live_logs)[-120:],
                "errors": list(error_logs)[-120:],
                "downloads": list(download_logs)[-120:],
                "video": list(video_logs)[-120:],
                "sabr": list(sabr_logs)[-120:],
            }
            new_config, decision = tuner.tune(snap, config, logs)
            if decision.get("changed"):
                with RUNTIME_LOCK:
                    config.clear()
                    config.update(new_config)
                    if hasattr(server, "apply_runtime_config"):
                        server.apply_runtime_config(config)
                log.warning("Auto tune applied: %s", decision.get("reason"))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.debug("auto tuner loop error: %s", exc)


async def run_auto_dashboard(config, mode_name):
    global MAIN_ASYNC_LOOP
    MAIN_ASYNC_LOOP = asyncio.get_running_loop()
    log = logging.getLogger("Main")

    server = ProxyServer(config)
    mode_state = {"name": mode_name, "key": config.get("runtime_mode", "basic")}
    WEB_RUNTIME["config"] = config
    WEB_RUNTIME["server"] = server
    WEB_RUNTIME["mode_state"] = mode_state
    WEB_RUNTIME["downloader"] = DownloadManager(config, download_dir=config.get("downloader_dir", "downloads"))
    WEB_RUNTIME["auto_tuner"] = AutoTuner(config) if AutoTuner is not None else None

    if hasattr(server, "apply_runtime_config"):
        server.apply_runtime_config(config)

    try:
        WEB_RUNTIME["quota_start"] = safe_int(stats.snapshot().get("quota_used", 0), 0)
    except Exception:
        WEB_RUNTIME["quota_start"] = 0

    proxy_task = asyncio.create_task(server.start(), name="proxy-server")
    auto_task = None

    try:
        await asyncio.sleep(float(config.get("startup_health_delay", 1.0) or 1.0))
        if proxy_task.done():
            exc = proxy_task.exception()
            if exc:
                raise exc
            return

        if config.get("exit_node_health_url") and config.get("exit_node_health_key"):
            start_exit_node_health_monitor(config)
            log.info("Exit node health monitor started via relay")
        else:
            log.info("Exit node health monitor skipped: not configured")

        auto_task = asyncio.create_task(auto_tuner_loop(config, server), name="auto-tuner")
        WEB_RUNTIME["auto_task"] = auto_task

        while not SHUTDOWN_EVENT.is_set():
            if proxy_task.done():
                exc = proxy_task.exception()
                if exc:
                    raise exc
                return
            await asyncio.sleep(0.2)

        log.info("Shutdown requested")
    finally:
        try:
            auto_task = WEB_RUNTIME.get("auto_task") or auto_task
            if auto_task and not auto_task.done():
                auto_task.cancel()
                await asyncio.gather(auto_task, return_exceptions=True)
        except Exception:
            pass
        try:
            if not proxy_task.done():
                proxy_task.cancel()
                await asyncio.gather(proxy_task, return_exceptions=True)
        except Exception:
            pass
        try:
            await asyncio.wait_for(server.stop(), timeout=3.0)
        except Exception:
            pass
        shutdown_services(close_stats=True)
        MAIN_ASYNC_LOOP = None


def prepare_cert(args, log):
    if not os.path.exists(CA_CERT_FILE):
        from mitm import MITMCertManager
        MITMCertManager()
    if args.no_cert_check:
        return
    if not is_ca_trusted(CA_CERT_FILE):
        log.warning("MITM CA is not trusted - attempting automatic installation...")
        if install_ca(CA_CERT_FILE):
            log.info("CA certificate installed. Restart browser if needed.")
        else:
            log.error("Auto-install failed. Run with --install-cert or install ca/ca.crt manually.")
    else:
        log.info("MITM CA is already trusted.")


def run_setup_mode(config, config_path, dashboard_port, args):
    setup_logging(config.get("log_level", "INFO"))
    ensure_setup_cert(args)
    WEB_RUNTIME["config"] = config
    WEB_RUNTIME["setup_mode"] = True
    WEB_RUNTIME["config_path"] = config_path
    WEB_RUNTIME["setup_completed_now"] = False
    WEB_RUNTIME["setup_restart_requested"] = False
    start_stats_server(dashboard_port)

    setup_url = f"http://127.0.0.1:{dashboard_port}/setup"
    print()
    print("MHR setup is required")
    print(f"Setup:      {setup_url}")
    print("Complete setup. MHR will restart automatically.")
    print("Press Ctrl+C to stop.")

    try:
        if open_setup_browser is not None:
            open_setup_browser("127.0.0.1", dashboard_port)
        else:
            open_url_later(setup_url, 0.8)
    except Exception:
        open_url_later(setup_url, 0.8)

    restart_started = False
    try:
        while not SHUTDOWN_EVENT.is_set():
            if WEB_RUNTIME.get("setup_completed_now") and not restart_started:
                restart_started = True
                print()
                print("[SETUP] Setup completed. Preparing clean restart...")
                request_self_restart(1.2)
            time.sleep(0.2)
    except KeyboardInterrupt:
        request_shutdown()
    finally:
        shutdown_services(close_stats=True)
        if SHUTDOWN_EVENT.is_set():
            print("MHR stopped.")
            print("MHR متوقف شد")


def main():
    args = parse_args()

    try:
        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)
    except Exception:
        pass

    if args.install_cert or args.uninstall_cert:
        setup_logging("INFO")
        if args.install_cert:
            if not os.path.exists(CA_CERT_FILE):
                from mitm import MITMCertManager
                MITMCertManager()
            sys.exit(0 if install_ca(CA_CERT_FILE) else 1)
        sys.exit(0 if uninstall_ca(CA_CERT_FILE) else 1)

    config_path = config_path_from_args(args.config)
    WEB_RUNTIME["config_path"] = config_path

    print("[CONFIG] app_dir     =", APP_DIR)
    print("[CONFIG] cwd         =", os.getcwd())
    print("[CONFIG] config_path =", config_path)

    config_missing = False
    try:
        config = load_json_file(config_path)
    except FileNotFoundError:
        config_missing = True
        print("[SETUP] config.json not found. Starting web setup wizard...")
        config = default_setup_config()
        config["setup_completed"] = False
        config["script_ids"] = []
        config["auth_key"] = str(config.get("auth_key") or "")
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in config: {exc}")
        print("Fix config.json or delete it to run setup wizard again.")
        sys.exit(1)

    set_config_defaults(config)
    apply_overrides(args, config)
    dashboard_port = int(config.get("dashboard_port", 9099))

    if needs_setup(config, config_missing):
        run_setup_mode(config, config_path, dashboard_port, args)
        return

    auth_key = str(config.get("auth_key") or "").strip()
    if not auth_key or auth_key in PLACEHOLDER_AUTH_KEYS:
        print("Refusing to start: auth_key is unset or placeholder.")
        sys.exit(1)

    script_ids = valid_config_script_ids(config)
    if not script_ids:
        print("Missing or placeholder script_id/script_ids in config.")
        sys.exit(1)
    config["script_ids"] = script_ids
    config.pop("script_id", None)
    config["mode"] = "apps_script"

    if args.scan:
        setup_logging("INFO")
        sys.exit(0 if scan_sync(config.get("front_domain", "www.google.com")) else 1)

    mode_name = apply_runtime_profile(config, config.get("runtime_mode", "basic"))

    setup_logging(config.get("log_level", "INFO"))
    log = logging.getLogger("Main")

    start_stats_server(dashboard_port)
    dashboard_url = f"http://127.0.0.1:{dashboard_port}/"
    log.info("Web dashboard: %s", dashboard_url)

    prepare_cert(args, log)

    lan_sharing = bool(config.get("lan_sharing", False))
    listen_host = str(config.get("listen_host", "127.0.0.1"))
    if lan_sharing and listen_host == "127.0.0.1":
        config["listen_host"] = "0.0.0.0"
        listen_host = "0.0.0.0"
        log.info("LAN sharing enabled - listening on all interfaces")

    if lan_sharing or listen_host in ("0.0.0.0", "::"):
        socks_port = config.get("socks5_port", 1080) if config.get("socks5_enabled", True) else None
        log_lan_access(config.get("listen_port", 8080), socks_port)

    try:
        print()
        print("MHR is running")
        print(f"Dashboard:  http://127.0.0.1:{dashboard_port}/")
        print(f"Downloader: http://127.0.0.1:{dashboard_port}/downloader")
        print("Press Ctrl+C to stop.")
        open_url_later(dashboard_url, 1.5)
        asyncio.run(run_auto_dashboard(config, mode_name))
    except KeyboardInterrupt:
        request_shutdown()
        log.info("Stopped by Ctrl+C")
    except OSError as exc:
        request_shutdown()
        if getattr(exc, "errno", None) in (48, 98, 10048):
            print()
            print("[ERROR] Proxy port is already in use.")
            print(f"Address/port conflict: {exc}")
            print("Close the previous MHR process, or change listen_port in config.json.")
        else:
            print()
            print(f"[ERROR] {exc}")
        raise
    finally:
        request_shutdown()
        shutdown_services(close_stats=True)
        time.sleep(0.3)
        print("MHR stopped.")
        print("MHR متوقف شد")



# PATCH_SCRIPT_OPTIMIZER_STATUS_REPAIR_START
# Repaired Script Optimizer status backend.
# Fixes missing optimizer_status_payload and handles Auto mode correctly.

def optimizer_config_drift(config):
    """
    Drift rules:
    - Auto mode means runtime is outside Script Optimizer.
    - Boolean true/false switch changes do not trigger drift.
    - Switching manual modes basic/medium/ultra/download does not trigger drift.
    - Non-boolean manual changes outside selected runtime profile trigger drift.
    """
    cfg = config or {}

    mode = str(cfg.get("runtime_mode") or "basic").strip().lower()

    if mode == "auto" or bool(cfg.get("auto_tune_enabled", False)):
        cfg["optimizer_config_changed_reason"] = "runtime mode is auto"
        return True

    saved_config = str(cfg.get("optimizer_config_fingerprint") or "").strip()
    if not saved_config:
        return False

    manual_modes = {"basic", "medium", "ultra", "download"}
    if mode not in manual_modes:
        mode = "basic"

    try:
        profiles = load_json_file(RUNTIME_PROFILES_PATH)
    except Exception:
        cfg["optimizer_config_changed_reason"] = "runtime_profiles.json could not be loaded"
        return True

    if not isinstance(profiles, dict) or mode not in profiles:
        cfg["optimizer_config_changed_reason"] = f"runtime profile {mode!r} is missing"
        return True

    expected = profiles.get(mode) or {}
    if not isinstance(expected, dict):
        cfg["optimizer_config_changed_reason"] = f"runtime profile {mode!r} is invalid"
        return True

    ignored_keys = {
        "runtime_mode",
        "label",

        "enable_batch",
        "enable_sub_batch",
        "video_prefetch_enabled",
        "manifest_prefetch_enabled",
        "video_passthrough_enabled",
        "youtube_sabr_booster_enabled",
        "turbo_mode_enabled",
        "turbo_force_no_delay",
        "turbo_skip_download_mode",
        "telegram_mode_enabled",
    }

    if bool(cfg.get("h2_disabled_by_dashboard", False)):
        ignored_keys.add("h2_connections")

    def _norm(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, list):
            return [_norm(x) for x in value]
        if isinstance(value, dict):
            return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        return value

    for key in OPTIMIZER_MANAGED_KEYS:
        if key in ignored_keys:
            continue

        if key not in expected:
            continue

        current_value = cfg.get(key)
        expected_value = expected.get(key)

        if isinstance(current_value, bool) or isinstance(expected_value, bool):
            continue

        if _norm(current_value) != _norm(expected_value):
            cfg["optimizer_config_changed_reason"] = (
                f"{key} changed from optimizer profile value "
                f"{expected_value!r} to {current_value!r}"
            )
            return True

    cfg["optimizer_config_changed_reason"] = ""
    return False


def optimizer_status_payload(config):
    """
    Full payload for /api/script-optimizer/status.
    This function was missing/broken in the patched main.py.
    """
    cfg = config or {}

    ids = tolerant_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])
    count = len(ids)
    max_tier = script_tier_for_count(count)

    active_tier = safe_int(
        cfg.get("optimized_for_script_count") or cfg.get("script_tier") or 0,
        0,
    )

    if active_tier < 1:
        active_tier = max_tier if count else 1

    if active_tier > max_tier:
        active_tier = max_tier

    runtime_mode_now = str(cfg.get("runtime_mode") or "").strip().lower()
    auto_mode_now = runtime_mode_now == "auto" or bool(cfg.get("auto_tune_enabled", False))

    drift = optimizer_config_drift(cfg)
    needs_attention = count <= 0 or drift or active_tier != max_tier or auto_mode_now

    lang = str(cfg.get("mhr_lang") or "en").lower()
    attention = ""

    if auto_mode_now:
        if lang.startswith("fa"):
            attention = (
                "حالت Auto فعال است؛ پروفایل از Script Optimizer خارج شده است. "
                "برای برگشت، یک مود دستی مثل Basic/Medium/Ultra انتخاب کن و Apply Optimization بزن."
            )
        else:
            attention = (
                "Auto mode is active; runtime is outside Script Optimizer. "
                "To return, choose a manual mode like Basic/Medium/Ultra and click Apply Optimization."
            )
    elif count <= 0:
        attention = "هیچ Script ID ثبت نشده است." if lang.startswith("fa") else "No script IDs are configured."
    elif drift:
        reason = str(cfg.get("optimizer_config_changed_reason") or "config changed after last optimization")
        if lang.startswith("fa"):
            attention = "کانفیگ بعد از آخرین بهینه‌سازی تغییر کرده است. برای معتبر شدن دوباره، Apply Optimization را بزن."
        else:
            attention = f"Config changed after last optimization. Reason: {reason}. Click Apply Optimization again."
    elif active_tier != max_tier:
        attention = (
            f"شما {count} اسکریپت ID داری، ولی سطح فعال Tier {active_tier} است."
            if lang.startswith("fa")
            else f"You have {count} script ID(s), but the active optimized tier is {active_tier}."
        )

    tiers = []
    for n in range(1, 6):
        tiers.append({
            "tier": n,
            "label": f"Tier {n}",
            "description": "For 5 or more Script IDs" if n == 5 else f"For {n} Script ID" + ("" if n == 1 else "s"),
            "locked": n > max_tier,
            "available": n <= max_tier,
            "active": n == active_tier,
        })

    if auto_mode_now:
        status_text = "Auto · Out of Script Optimizer"
    elif needs_attention:
        status_text = "Config Changed"
    else:
        status_text = f"Applied · Tier {active_tier}"

    return {
        "ok": True,

        "script_ids": ids,
        "ids": ids,
        "script_count": count,
        "count": count,

        "max_tier": max_tier,
        "max_available_tier": max_tier,
        "script_tier": active_tier,
        "active_tier": active_tier,
        "selected_tier": active_tier,
        "optimized_for_script_count": active_tier,

        "optimized_at": safe_int(cfg.get("optimized_at"), 0),
        "optimizer_version": cfg.get("optimizer_version", "-"),

        "locked_tiers": [n for n in range(1, 6) if n > max_tier],
        "tiers": tiers,

        "needs_attention": bool(needs_attention),
        "attention": attention,

        "config_changed": bool(drift or auto_mode_now),
        "optimizer_config_changed": bool(drift or auto_mode_now),
        "optimizer_config_drift": bool(drift or auto_mode_now),
        "auto_mode_out_of_optimizer": bool(auto_mode_now),

        "optimizer_config_fingerprint_set": bool(cfg.get("optimizer_config_fingerprint")),
        "optimizer_runtime_profiles_fingerprint_set": bool(cfg.get("optimizer_runtime_profiles_fingerprint")),
        "optimizer_fingerprint_version": cfg.get("optimizer_fingerprint_version", "-"),
        "optimizer_config_changed_reason": "runtime mode is auto" if auto_mode_now else str(cfg.get("optimizer_config_changed_reason") or ""),

        "label": cfg.get("label", "-"),
        "runtime_mode": cfg.get("runtime_mode", "-"),
        "status_text": status_text,
    }


try:
    _MHR_REPAIRED_BUILD_DASHBOARD_CONFIG_ORIGINAL = build_dashboard_config

    def build_dashboard_config(config, effective):
        out = _MHR_REPAIRED_BUILD_DASHBOARD_CONFIG_ORIGINAL(config, effective)
        cfg = config or {}

        runtime_mode_now = str(cfg.get("runtime_mode") or "").strip().lower()
        auto_mode_now = runtime_mode_now == "auto" or bool(cfg.get("auto_tune_enabled", False))

        if auto_mode_now:
            out["optimizer_config_drift"] = True
            out["optimizer_config_changed"] = True
            out["config_changed"] = True
            out["auto_mode_out_of_optimizer"] = True
            out["optimizer_config_changed_reason"] = "runtime mode is auto"
            out["script_optimizer_status_text"] = "Auto · Out of Script Optimizer"
        else:
            out["auto_mode_out_of_optimizer"] = False

        return out

except NameError:
    pass

# PATCH_SCRIPT_OPTIMIZER_STATUS_REPAIR_END



if __name__ == "__main__":
    main()
