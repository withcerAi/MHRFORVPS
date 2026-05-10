import json
import os
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
RESET_HOUR = 10
RESET_MINUTE = 30
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "quota_state.json")


def _now():
    return time.time()


def _next_reset_epoch(now_ts=None):
    if now_ts is None:
        now_ts = _now()

    now_local = datetime.fromtimestamp(now_ts, IRAN_TZ)
    reset_today = now_local.replace(
        hour=RESET_HOUR,
        minute=RESET_MINUTE,
        second=0,
        microsecond=0,
    )

    if now_local < reset_today:
        return reset_today.timestamp()

    return (reset_today + timedelta(days=1)).timestamp()


def _fmt_reset_time(epoch):
    return datetime.fromtimestamp(epoch, IRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _looks_rate_limited(text: str) -> bool:
    if not text:
        return False

    t = str(text).lower()
    markers = (
        "quota",
        "quota exceeded",
        "daily limit",
        "rate limit",
        "too many",
        "service invoked too many times",
        "urlfetch",
        "exceeded maximum execution",
        "limit exceeded",
        "429",
        "دفعات زیاد",
        "بیش از حد",
        "سهمیه",
    )
    return any(m in t for m in markers)


@dataclass
class RuntimeStats:
    started_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    google_requests: int = 0
    target_requests: int = 0
    proxy_requests: int = 0
    errors: int = 0

    bytes_to_google: int = 0
    bytes_from_google: int = 0
    bytes_to_client: int = 0
    bytes_from_client: int = 0

    per_host: dict = field(default_factory=dict)

    video_prefetch_requests: int = 0
    video_prefetch_hits: int = 0
    video_prefetch_misses: int = 0
    video_prefetch_errors: int = 0
    video_prefetch_bytes: int = 0
    video_prefetch_active: int = 0

    # PATCH_TELEGRAM_TURBO_STATS_FIELDS
    telegram_requests: int = 0
    telegram_delayed: int = 0
    telegram_bytes: int = 0
    telegram_active: int = 0

    turbo_requests: int = 0
    turbo_padded: int = 0
    turbo_padding_bytes: int = 0
    turbo_delayed: int = 0

    script_quota_limit: int = 20000
    script_ids_order: list = field(default_factory=list)
    script_names: dict = field(default_factory=dict)
    script_usage: dict = field(default_factory=dict)

    reset_epoch: float = 0.0

    def __post_init__(self):
        self._load_state()

    def _load_state(self):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        self.reset_epoch = float(data.get("reset_epoch") or _next_reset_epoch())

        if _now() >= self.reset_epoch:
            self.reset_epoch = _next_reset_epoch()
            self.script_usage = {}
            self._save_state_unlocked()
            return

        self.script_usage = data.get("script_usage", {}) or {}

    def _save_state_unlocked(self):
        try:
            data = {
                "reset_epoch": self.reset_epoch,
                "reset_at_iran": _fmt_reset_time(self.reset_epoch),
                "script_usage": self.script_usage,
            }
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass

    def _maybe_reset_unlocked(self):
        now = _now()
        if self.reset_epoch <= 0:
            self.reset_epoch = _next_reset_epoch(now)

        if now >= self.reset_epoch:
            self.script_usage = {}
            self.reset_epoch = _next_reset_epoch(now)
            self._save_state_unlocked()

    def configure_scripts(self, script_ids=None, script_names=None, quota_limit=20000):
        if isinstance(script_ids, str):
            script_ids = [script_ids]

        script_ids = [
            str(x).strip()
            for x in (script_ids or [])
            if x is not None and str(x).strip()
        ]
        script_ids = list(script_ids or [])

        with self.lock:
            self._maybe_reset_unlocked()

            self.script_ids_order = script_ids
            self.script_names = dict(script_names or {})
            try:
                self.script_quota_limit = int(quota_limit or 20000)
            except Exception:
                self.script_quota_limit = 20000

            for sid in script_ids:
                self._ensure_script_unlocked(sid)

            self._save_state_unlocked()

    def _script_name_unlocked(self, sid: str) -> str:
        sid = str(sid or "")
        if sid in self.script_names:
            return str(self.script_names[sid])

        try:
            idx = self.script_ids_order.index(sid)
            return f"GAS-{idx + 1}"
        except Exception:
            pass

        return ("..." + sid[-8:]) if len(sid) > 10 else (sid or "-")

    def _ensure_script_unlocked(self, sid: str):
        sid = str(sid or "").strip()
        if not sid or sid.lower() in ("none", "unknown", "-"):
            return {
                "requests": 0,
                "bytes": 0,
                "errors": 0,
                "rate_limited": False,
                "last_status": "-",
                "last_error": "",
                "last_seen": 0,
                "status_errors": {},
                "name": "-"
            }
        item = self.script_usage.setdefault(sid, {})
        item.setdefault("requests", 0)
        item.setdefault("bytes", 0)
        item.setdefault("errors", 0)
        item.setdefault("rate_limited", False)
        item.setdefault("last_status", "-")
        item.setdefault("last_error", "")
        item.setdefault("last_seen", 0)
        item.setdefault("status_errors", {})
        item["name"] = self._script_name_unlocked(sid)
        return item

    def add_google_request(
        self,
        host: str,
        sent: int = 0,
        received: int = 0,
        error: bool = False,
        script_id: str | None = None,
        count: int = 1,
        status=None,
        response_text: str = "",
        status_code=None,
    ):
        count = max(1, int(count or 1))

        real_status = status_code if status_code is not None else status
        try:
            status_int = int(real_status)
        except Exception:
            status_int = None

        rate_limited = _looks_rate_limited(response_text)
        is_error = bool(error or rate_limited or (status_int is not None and status_int >= 500))

        with self.lock:
            self._maybe_reset_unlocked()

            self.google_requests += count
            self.target_requests += count
            self.bytes_to_google += max(0, int(sent or 0))
            self.bytes_from_google += max(0, int(received or 0))

            if is_error:
                self.errors += count

            h = host or "unknown"
            item = self.per_host.setdefault(h, {"requests": 0, "bytes": 0, "errors": 0})
            item["requests"] += count
            item["bytes"] += max(0, int(received or 0))
            if is_error:
                item["errors"] += count

            if script_id:
                s = self._ensure_script_unlocked(script_id)
                s["requests"] += count
                s["bytes"] += max(0, int(received or 0))
                s["last_seen"] = int(_now())

                if is_error:
                    s["errors"] += count
                    s["last_status"] = str(real_status or "ERR")
                    if rate_limited:
                        s["rate_limited"] = True
                        s["last_error"] = "rate_limit_or_quota"
                    else:
                        s["last_error"] = str(real_status or "error")
                else:
                    # Previous errors remain only in total errors; live status becomes OK again.
                    s["rate_limited"] = False
                    s["last_status"] = "OK"
                    s["last_error"] = ""

            self._save_state_unlocked()

    def add_proxy_request(self, host: str = "", received: int = 0):
        with self.lock:
            self.proxy_requests += 1
            self.bytes_from_client += max(0, int(received or 0))

    def add_proxy_response(self, host: str = "", size: int = 0):
        with self.lock:
            self.proxy_requests += 1
            self.bytes_to_client += max(0, int(size or 0))

    def add_client_out(self, size: int):
        with self.lock:
            self.bytes_to_client += max(0, int(size or 0))

    def add_video_prefetch(self, event: str, size: int = 0):
        event = str(event or "").lower()
        with self.lock:
            if event == "request":
                self.video_prefetch_requests += 1
            elif event == "hit":
                self.video_prefetch_hits += 1
            elif event == "miss":
                self.video_prefetch_misses += 1
            elif event == "error":
                self.video_prefetch_errors += 1
            elif event == "active_start":
                self.video_prefetch_active += 1
            elif event == "active_done":
                self.video_prefetch_active = max(0, self.video_prefetch_active - 1)

            self.video_prefetch_bytes += max(0, int(size or 0))


    # PATCH_TELEGRAM_TURBO_STATS_METHODS
    def add_telegram(self, event: str, size: int = 0):
        event = str(event or "").lower()
        with self.lock:
            if event == "request":
                self.telegram_requests += 1
            elif event == "delayed":
                self.telegram_delayed += 1
            elif event == "active_start":
                self.telegram_active += 1
            elif event == "active_done":
                self.telegram_active = max(0, self.telegram_active - 1)
            self.telegram_bytes += max(0, int(size or 0))

    def add_turbo(self, event: str, size: int = 0):
        event = str(event or "").lower()
        with self.lock:
            if event == "request":
                self.turbo_requests += 1
            elif event == "padded":
                self.turbo_padded += 1
            elif event == "delayed":
                self.turbo_delayed += 1
            self.turbo_padding_bytes += max(0, int(size or 0))

    def snapshot(self):
        with self.lock:
            self._maybe_reset_unlocked()

            uptime = int(_now() - self.started_at)

            hosts = sorted(
                self.per_host.items(),
                key=lambda x: x[1].get("bytes", 0),
                reverse=True,
            )[:10]

            ordered = [
                str(x).strip()
                for x in (self.script_ids_order or [])
                if x is not None and str(x).strip() and str(x).strip().lower() not in ("none", "unknown", "-")
            ]

            for sid in list(self.script_usage.keys()):
                sid_clean = str(sid or "").strip()
                if not sid_clean or sid_clean.lower() in ("none", "unknown", "-"):
                    continue
                if sid_clean not in ordered:
                    ordered.append(sid_clean)

            scripts = []
            for sid in ordered:
                item = self._ensure_script_unlocked(sid)
                used = int(item.get("requests", 0))
                limit = int(self.script_quota_limit or 20000)
                status_errors = item.get("status_errors", {}) or {}

                scripts.append({
                    "sid": sid,
                    "script": item.get("name") or self._script_name_unlocked(sid),
                    "short_id": ("..." + str(sid)[-8:]) if len(str(sid or "")) > 10 else str(sid or "-"),
                    "requests": used,
                    "limit": limit,
                    "quota": f"{used}/{limit}",
                    "quota_percent": round((used / limit * 100), 1) if limit else 0,
                    "bytes": int(item.get("bytes", 0)),
                    "errors": int(item.get("errors", 0)),
                    "status_errors": status_errors,
                    "rate_limited": bool(item.get("rate_limited", False)),
                    "last_status": item.get("last_status", "-"),
                    "last_error": item.get("last_error", ""),
                })

            total_used = sum(x["requests"] for x in scripts)
            total_limit = len(scripts) * int(self.script_quota_limit or 20000)

            return {
                "uptime_seconds": uptime,
                "google_requests": self.google_requests,
                "target_requests": self.target_requests,
                "proxy_requests": self.proxy_requests,
                "errors": self.errors,

                "bytes_to_google": self.bytes_to_google,
                "bytes_from_google": self.bytes_from_google,
                "bytes_to_client": self.bytes_to_client,
                "bytes_from_client": self.bytes_from_client,

                "video_prefetch_requests": self.video_prefetch_requests,
                "video_prefetch_hits": self.video_prefetch_hits,
                "video_prefetch_misses": self.video_prefetch_misses,
                "video_prefetch_errors": self.video_prefetch_errors,
                "video_prefetch_bytes": self.video_prefetch_bytes,
                "video_prefetch_active": self.video_prefetch_active,

                # PATCH_TELEGRAM_TURBO_SNAPSHOT
                "telegram_requests": self.telegram_requests,
                "telegram_delayed": self.telegram_delayed,
                "telegram_bytes": self.telegram_bytes,
                "telegram_active": self.telegram_active,

                "turbo_requests": self.turbo_requests,
                "turbo_padded": self.turbo_padded,
                "turbo_padding_bytes": self.turbo_padding_bytes,
                "turbo_delayed": self.turbo_delayed,

                "top_hosts": [{"host": h, **v} for h, v in hosts],

                "quota_used": total_used,
                "quota_limit": total_limit,
                "quota": f"{total_used}/{total_limit}",
                "reset_epoch": self.reset_epoch,
                "reset_at_iran": _fmt_reset_time(self.reset_epoch),
                "reset_in_seconds": max(0, int(self.reset_epoch - _now())),

                "script_ids": scripts,
            }


stats = RuntimeStats()