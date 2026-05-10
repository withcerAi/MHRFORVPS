"""
Local HTTP proxy server.

Intercepts the user's browser traffic and forwards everything through
the Apps Script relay (MITM-decrypts HTTPS locally, forwards requests
as JSON to script.google.com fronted through www.google.com).
"""

import asyncio
import logging
import re
import socket
import ssl
import time
import ipaddress
# PATCH_TELEGRAM_FAST_CIDR
from collections import defaultdict
from urllib.parse import parse_qs
from urllib.parse import urlparse, urljoin

try:
    import certifi
except Exception:  # optional dependency fallback
    certifi = None

from constants import (
    CACHE_MAX_MB,
    CACHE_TTL_MAX,
    CACHE_TTL_STATIC_LONG,
    CACHE_TTL_STATIC_MED,
    CLIENT_IDLE_TIMEOUT,
    GOOGLE_DIRECT_ALLOW_EXACT,
    GOOGLE_DIRECT_ALLOW_SUFFIXES,
    GOOGLE_DIRECT_EXACT_EXCLUDE,
    GOOGLE_DIRECT_SUFFIX_EXCLUDE,
    GOOGLE_OWNED_EXACT,
    GOOGLE_OWNED_SUFFIXES,
    LARGE_FILE_EXTS,
    MAX_HEADER_BYTES,
    MAX_REQUEST_BODY_BYTES,
    SNI_REWRITE_SUFFIXES,
    STATIC_EXTS,
    TCP_CONNECT_TIMEOUT,
    TRACE_HOST_SUFFIXES,
    UNCACHEABLE_HEADER_NAMES,
)
from domain_fronter import DomainFronter
from stats import stats

log = logging.getLogger("Proxy")

_SENSITIVE_LOG_QUERY_KEYS = {
    "token", "access_token", "auth", "authorization", "key", "api_key",
    "sig", "sigh", "signature", "sid", "ssid", "session", "cookie",
    "cpn", "cid", "ei", "id_token", "refresh_token", "password",
}


def _safe_log_url(url: str, max_len: int = 220) -> str:
    """
    Truncate long URLs and redact sensitive query values before logging.
    Keeps enough shape for debugging while avoiding huge dashboard logs.
    """
    s = str(url or "")
    try:
        parsed = urlparse(s)

        # Non-URL strings, such as raw request lines.
        if not parsed.scheme or not parsed.netloc:
            return s if len(s) <= max_len else s[:max_len] + "...[truncated]"

        query_out = ""
        if parsed.query:
            parts = []
            raw_parts = parsed.query.split("&")
            for pair in raw_parts[:8]:
                key, sep, value = pair.partition("=")
                low_key = key.lower()

                if low_key in _SENSITIVE_LOG_QUERY_KEYS:
                    parts.append(f"{key}=<redacted>")
                elif len(value) > 48:
                    parts.append(f"{key}={value[:16]}...[truncated]")
                else:
                    parts.append(pair[:90])

            if len(raw_parts) > 8:
                parts.append("...[more]")

            query_out = "?" + "&".join(parts)

        out = f"{parsed.scheme}://{parsed.netloc}{parsed.path}{query_out}"
        if len(out) > max_len:
            out = out[:max_len] + "...[truncated]"
        return out

    except Exception:
        return s if len(s) <= max_len else s[:max_len] + "...[truncated]"


# --- STRICT_VIDEO_PRIORITY_FIX_START ---
def _strict_real_video_url(url: str, headers: dict | None = None) -> bool:
    u = str(url or "").lower()
    h = (urlparse(url).hostname or "").lower()
    p = urlparse(u).path.lower()

    # These are not video requests, so video priority/retry should not be applied.
    non_video = (
        "chrome-sync",
        "clients4.google.com/chrome-sync",
        "/api/stats/",
        "/youtubei/v1/log_event",
        "/youtubei/v1/guide",
        "/youtubei/v1/notification",
        "/generate_204",
        "rotateboundcookies",
        "rotatecookies",
        "accounts.google.com",
        "ogs.google.com",
        "play.google.com/log",
        "android.clients.google.com",
        "oauthaccountmanager.googleapis.com",
        "securitydomain-pa.googleapis.com",
    )
    if any(x in u for x in non_video):
        return False

    if "googlevideo.com" in h or "videoplayback" in u:
        return True

    if p.endswith((
        ".mp4", ".webm", ".m4s", ".m3u8", ".mpd",
        ".ts", ".m2ts", ".mp3", ".aac", ".ogg", ".opus"
    )):
        return True

    accept = ""
    sec_dest = ""
    if headers:
        for k, v in headers.items():
            lk = str(k).lower()
            if lk == "accept":
                accept = str(v).lower()
            elif lk == "sec-fetch-dest":
                sec_dest = str(v).lower()

    if "video/" in accept or "audio/" in accept:
        return True
    if sec_dest in ("video", "audio", "media"):
        return True

    return False
# --- STRICT_VIDEO_PRIORITY_FIX_END ---


# --- FORCE GOOGLE VIDEO THROUGH GAS RELAY PATCH ---
_FORCE_GAS_RELAY_SUFFIXES = (
    "googlevideo.com",
    "youtube.com",
    "ytimg.com",
    "googleusercontent.com",
    "ggpht.com",
)

def _force_gas_relay_host(host: str, url: str = "") -> bool:
    host = (host or "").lower().strip(".")
    url = (url or "").lower()
    return (
        any(host == x or host.endswith("." + x) for x in _FORCE_GAS_RELAY_SUFFIXES)
        or "googlevideo.com" in url
        or "videoplayback" in url
        or "sabr=1" in url
    )
# --- END FORCE GOOGLE VIDEO THROUGH GAS RELAY PATCH ---



def _is_ip_literal(host: str) -> bool:
    """True for IPv4/IPv6 literals (strips brackets around IPv6)."""
    h = host.strip("[]")
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def _parse_content_length(header_block: bytes) -> int:
    """Return Content-Length or 0. Matches only the exact header name."""
    for raw_line in header_block.split(b"\r\n"):
        name, sep, value = raw_line.partition(b":")
        if not sep:
            continue
        if name.strip().lower() == b"content-length":
            try:
                return int(value.strip())
            except ValueError:
                return 0
    return 0


def _has_unsupported_transfer_encoding(header_block: bytes) -> bool:
    """True when the request uses Transfer-Encoding, which we don't stream."""
    for raw_line in header_block.split(b"\r\n"):
        name, sep, value = raw_line.partition(b":")
        if not sep:
            continue
        if name.strip().lower() != b"transfer-encoding":
            continue
        encodings = [
            token.strip().lower()
            for token in value.decode(errors="replace").split(",")
            if token.strip()
        ]
        return any(token != "identity" for token in encodings)
    return False


# --- VIDEO_PREFETCH_PATCH_START ---
class VideoRangeCache:
    """Small TTL cache for HTTP 206 video Range responses."""

    def __init__(self, max_mb: int = 256, ttl: int = 180):
        self._store: dict[str, tuple[bytes, float, int]] = {}
        self._size = 0
        self._max = max(16, int(max_mb or 256)) * 1024 * 1024
        self._ttl = max(10, int(ttl or 180))
        self._lock = asyncio.Lock()

    @staticmethod
    def key(method: str, url: str, range_header: str) -> str:
        return f"{method.upper()}|{url}|{str(range_header).strip().lower()}"

    async def get(self, method: str, url: str, range_header: str) -> bytes | None:
        k = self.key(method, url, range_header)
        async with self._lock:
            entry = self._store.get(k)
            if not entry:
                return None

            raw, expires, size = entry
            if time.time() > expires:
                self._store.pop(k, None)
                self._size = max(0, self._size - size)
                return None

            return raw

    async def put(self, method: str, url: str, range_header: str, raw_response: bytes):
        if not raw_response:
            return

        if not (
            raw_response.startswith(b"HTTP/1.1 206") or
            raw_response.startswith(b"HTTP/1.0 206")
        ):
            return

        size = len(raw_response)
        if size <= 0 or size > self._max // 2:
            return

        k = self.key(method, url, range_header)
        async with self._lock:
            while self._size + size > self._max and self._store:
                oldest = next(iter(self._store))
                _raw, _expires, old_size = self._store.pop(oldest)
                self._size = max(0, self._size - old_size)

            old = self._store.get(k)
            if old:
                self._size = max(0, self._size - old[2])

            self._store[k] = (raw_response, time.time() + self._ttl, size)
            self._size += size
# --- VIDEO_PREFETCH_PATCH_END ---

# --- MANIFEST_PREFETCH_CACHE_START ---
class ManifestSegmentCache:
    """Cache exact segment URLs verified from HLS manifests. No guessing."""

    def __init__(self, max_mb: int = 256, ttl: int = 180):
        self._store: dict[str, tuple[bytes, float, int]] = {}
        self._size = 0
        self._max = max(16, int(max_mb or 256)) * 1024 * 1024
        self._ttl = max(10, int(ttl or 180))
        self._lock = asyncio.Lock()

    async def get(self, url: str) -> bytes | None:
        async with self._lock:
            item = self._store.get(url)
            if not item:
                return None
            raw, expires, size = item
            if time.time() > expires:
                self._store.pop(url, None)
                self._size = max(0, self._size - size)
                return None
            return raw

    async def put(self, url: str, raw: bytes):
        if not raw:
            return
        size = len(raw)
        if size <= 0 or size > self._max // 2:
            return
        async with self._lock:
            while self._size + size > self._max and self._store:
                oldest = next(iter(self._store))
                _raw, _exp, old_size = self._store.pop(oldest)
                self._size = max(0, self._size - old_size)
            old = self._store.get(url)
            if old:
                self._size = max(0, self._size - old[2])
            self._store[url] = (raw, time.time() + self._ttl, size)
            self._size += size
# --- MANIFEST_PREFETCH_CACHE_END ---

class ResponseCache:
    """Simple LRU response cache — avoids repeated relay calls."""

    def __init__(self, max_mb: int = 50):
        self._store: dict[str, tuple[bytes, float]] = {}
        self._size = 0
        self._max = max_mb * 1024 * 1024
        self.hits = 0
        self.misses = 0

    def get(self, url: str) -> bytes | None:
        entry = self._store.get(url)
        if not entry:
            self.misses += 1
            return None
        raw, expires = entry
        if time.time() > expires:
            self._size -= len(raw)
            del self._store[url]
            self.misses += 1
            return None
        self.hits += 1
        return raw

    def put(self, url: str, raw_response: bytes, ttl: int = 300):
        size = len(raw_response)
        if size > self._max // 4 or size == 0:
            return
        # Evict oldest to make room
        while self._size + size > self._max and self._store:
            oldest = next(iter(self._store))
            self._size -= len(self._store[oldest][0])
            del self._store[oldest]
        if url in self._store:
            self._size -= len(self._store[url][0])
        self._store[url] = (raw_response, time.time() + ttl)
        self._size += size

    @staticmethod
    def parse_ttl(raw_response: bytes, url: str) -> int:
        """Determine cache TTL from response headers and URL."""
        hdr_end = raw_response.find(b"\r\n\r\n")
        if hdr_end < 0:
            return 0
        hdr = raw_response[:hdr_end].decode(errors="replace").lower()

        # Don't cache errors or non-200
        if b"HTTP/1.1 200" not in raw_response[:20]:
            return 0
        if "no-store" in hdr or "private" in hdr or "set-cookie:" in hdr:
            return 0

        # Explicit max-age
        m = re.search(r"max-age=(\d+)", hdr)
        if m:
            return min(int(m.group(1)), CACHE_TTL_MAX)

        # Heuristic by content type / extension
        path = url.split("?")[0].lower()
        for ext in STATIC_EXTS:
            if path.endswith(ext):
                return CACHE_TTL_STATIC_LONG

        ct_m = re.search(r"content-type:\s*([^\r\n]+)", hdr)
        ct = ct_m.group(1) if ct_m else ""
        if "image/" in ct or "font/" in ct:
            return CACHE_TTL_STATIC_LONG
        if "text/css" in ct or "javascript" in ct:
            return CACHE_TTL_STATIC_MED
        if "text/html" in ct or "application/json" in ct:
            return 0  # don't cache dynamic content by default

        return 0


class ProxyServer:
    # Pulled from constants.py so users can override any subset via config.
    _GOOGLE_DIRECT_EXACT_EXCLUDE  = GOOGLE_DIRECT_EXACT_EXCLUDE
    _GOOGLE_DIRECT_SUFFIX_EXCLUDE = GOOGLE_DIRECT_SUFFIX_EXCLUDE
    _GOOGLE_DIRECT_ALLOW_EXACT    = GOOGLE_DIRECT_ALLOW_EXACT
    _GOOGLE_DIRECT_ALLOW_SUFFIXES = GOOGLE_DIRECT_ALLOW_SUFFIXES
    _TRACE_HOST_SUFFIXES          = TRACE_HOST_SUFFIXES
    _DOWNLOAD_DEFAULT_EXTS        = tuple(sorted(LARGE_FILE_EXTS))
    _DOWNLOAD_ACCEPT_MARKERS      = (
        "application/octet-stream",
        "application/zip",
        "application/x-bittorrent",
        "video/",
        "audio/",
    )

    def __init__(self, config: dict):
        self.host = config.get("listen_host", "127.0.0.1")
        self.port = config.get("listen_port", 8080)
        self.socks_enabled = config.get("socks5_enabled", True)
        self.socks_host = config.get("socks5_host", self.host)
        self.socks_port = config.get("socks5_port", 1080)
        if self.socks_enabled and self.socks_host == self.host \
                and int(self.socks_port) == int(self.port):
            raise ValueError(
                f"listen_port and socks5_port must differ on the same host "
                f"(both set to {self.port} on {self.host}). "
                f"Change one of them in config.json."
            )
        self.fronter = DomainFronter(config)
        self._runtime_config_ref = config
        self.mitm = None
        self._cache = ResponseCache(max_mb=CACHE_MAX_MB)
        # --- SABR_PASSTHROUGH_INIT_START ---
        self._video_passthrough_enabled = bool(config.get("video_passthrough_enabled", True))
        self._video_passthrough_relay_timeout = self._cfg_float(
            config, "video_passthrough_relay_timeout", 120.0, minimum=5.0,
        )
        # --- SABR_PASSTHROUGH_INIT_END ---

        # --- YOUTUBE_SABR_BOOSTER_INIT_START ---
        self._youtube_sabr_booster_enabled = bool(config.get("youtube_sabr_booster_enabled", True))
        self._youtube_sabr_timeout = self._cfg_float(
            config, "youtube_sabr_timeout", 180.0, minimum=30.0,
        )
        self._youtube_sabr_max_parallel = self._cfg_int(
            config, "youtube_sabr_max_parallel", 2, minimum=1,
        )
        self._youtube_sabr_retry_attempts = self._cfg_int(
            config, "youtube_sabr_retry_attempts", 2, minimum=1,
        )
        self._youtube_sabr_retry_delay_ms = self._cfg_int(
            config, "youtube_sabr_retry_delay_ms", 120, minimum=0,
        )
        self._youtube_sabr_sem = asyncio.Semaphore(self._youtube_sabr_max_parallel)
        # --- YOUTUBE_SABR_BOOSTER_INIT_END ---

        # --- MANIFEST_PREFETCH_INIT_START ---
        self._manifest_prefetch_enabled = bool(config.get("manifest_prefetch_enabled", True))
        self._manifest_prefetch_next_segments = self._cfg_int(
            config, "manifest_prefetch_next_segments", 4, minimum=0,
        )
        self._manifest_prefetch_parallel = self._cfg_int(
            config, "manifest_prefetch_parallel", 2, minimum=1,
        )
        self._manifest_cache = ManifestSegmentCache(
            max_mb=self._cfg_int(config, "manifest_cache_max_mb", 256, minimum=16),
            ttl=self._cfg_int(config, "manifest_cache_ttl_seconds", 180, minimum=10),
        )
        self._manifest_prefetch_sem = asyncio.Semaphore(self._manifest_prefetch_parallel)
        self._manifest_prefetch_tasks: set[asyncio.Task] = set()
        self._manifest_prefetch_inflight: set[str] = set()
        self._manifest_segments: dict[str, list[str]] = {}
        self._segment_to_manifest: dict[str, str] = {}
        # --- MANIFEST_PREFETCH_INIT_END ---
        # --- VIDEO_PREFETCH_INIT_START ---
        self._video_prefetch_enabled = bool(config.get("video_prefetch_enabled", True))
        self._video_prefetch_count = self._cfg_int(
            config, "video_prefetch_next_ranges", 3, minimum=0,
        )
        self._video_prefetch_parallel = self._cfg_int(
            config, "video_prefetch_parallel", 2, minimum=1,
        )
        self._video_prefetch_chunk_size = self._cfg_int(
            config, "video_prefetch_chunk_size", 1024 * 1024, minimum=128 * 1024,
        )
        self._video_cache = VideoRangeCache(
            max_mb=self._cfg_int(config, "video_cache_max_mb", 256, minimum=16),
            ttl=self._cfg_int(config, "video_cache_ttl_seconds", 180, minimum=10),
        )
        self._video_prefetch_tasks: set[asyncio.Task] = set()
        self._video_prefetch_inflight: set[str] = set()
        self._video_prefetch_sem = asyncio.Semaphore(self._video_prefetch_parallel)
        self._video_extensions = tuple(str(x).lower() for x in config.get(
            "video_detect_extensions",
            [
                ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv",
                ".ts", ".m2ts", ".m3u8", ".mpd", ".m4s", ".mp2t",
                ".3gp", ".3gpp", ".ogv", ".ogg", ".googlevideo", ".videoplayback",
            ],
        ))
        self._video_keywords = tuple(str(x).lower() for x in config.get(
            "video_detect_keywords",
            [
                "videoplayback", "mime=video", "video/", "audio/",
                "googlevideo", "range=", "itag=", "clen=", "dur=",
                "hls", "dash", ".m3u8", ".mpd", ".m4s",
            ],
        ))
        # --- VIDEO_PREFETCH_INIT_END ---
        self._direct_fail_until: dict[str, float] = {}
        self._servers: list[asyncio.base_events.Server] = []
        self._client_tasks: set[asyncio.Task] = set()
        self._loop = None
        self._fronter_override_lock = asyncio.Lock()

        # PATCH_TELEGRAM_TURBO_INIT
        self._telegram_locks = defaultdict(asyncio.Lock)
        self._telegram_last_seen: dict[str, float] = {}
        self._turbo_enabled = bool(config.get("turbo_mode_enabled", False))
        self._turbo_small_request_max = self._cfg_int(config, "turbo_small_request_max", 0, minimum=0)
        self._turbo_min_upload_padding = self._cfg_int(config, "turbo_min_upload_padding", 0, minimum=0)
        self._turbo_coalesce_window_ms = self._cfg_int(config, "turbo_coalesce_window_ms", 100, minimum=0)
        self._turbo_skip_download_mode = bool(config.get("turbo_skip_download_mode", False))
        self._turbo_parallel_relay = self._cfg_int(config, "turbo_parallel_relay", 3, minimum=1)
        self._turbo_force_no_delay = bool(config.get("turbo_force_no_delay", True))

        self._telegram_mode_enabled = bool(config.get("telegram_mode_enabled", False))
        self._telegram_serial_per_host = bool(config.get("telegram_serial_per_host", False))
        self._telegram_coalesce_window_ms = self._cfg_int(config, "telegram_coalesce_window_ms", 35, minimum=0)
        self._telegram_max_delay_ms = self._cfg_int(config, "telegram_max_delay_ms", 120, minimum=0)
        self._telegram_parallel_relay = self._cfg_int(config, "telegram_parallel_relay", 1, minimum=1)
        self._telegram_hosts = tuple(
            str(x).lower().strip().lstrip(".")
            for x in config.get("telegram_hosts", ["telegram.org", "telegram.me", "t.me"])
            if str(x).strip()
        )
        self._telegram_cidrs = tuple(str(x).strip() for x in config.get("telegram_cidrs", []))
        self._telegram_networks = []
        for cidr in self._telegram_cidrs:
            try:
                self._telegram_networks.append(ipaddress.ip_network(cidr, strict=False))
            except Exception:
                log.warning("Invalid telegram_cidrs entry ignored: %s", cidr)
        self._tcp_connect_timeout = self._cfg_float(
            config, "tcp_connect_timeout", TCP_CONNECT_TIMEOUT, minimum=1.0,
        )
        self._download_min_size = self._cfg_int(
            config, "chunked_download_min_size", 5 * 1024 * 1024, minimum=0,
        )
        self._download_chunk_size = self._cfg_int(
            config, "chunked_download_chunk_size", 512 * 1024, minimum=64 * 1024,
        )
        self._download_max_parallel = self._cfg_int(
            config, "chunked_download_max_parallel", 8, minimum=1,
        )
        self._download_max_chunks = self._cfg_int(
            config, "chunked_download_max_chunks", 256, minimum=1,
        )
        self._download_extensions, self._download_any_extension = (
            self._normalize_download_extensions(
                config.get(
                    "chunked_download_extensions",
                    list(self._DOWNLOAD_DEFAULT_EXTS),
                )
            )
        )

        # hosts override — DNS fake-map: domain/suffix → IP
        # Checked before any real DNS lookup; supports exact and suffix matching.
        raw_hosts = config.get("hosts", {})
        if isinstance(raw_hosts, dict):
            self._hosts: dict[str, str] = {
                str(k).lower().rstrip("."): str(v).strip()
                for k, v in raw_hosts.items()
                if isinstance(v, str) and str(v).strip()
            }
            ignored_hosts = [
                str(k)
                for k, v in raw_hosts.items()
                if not (isinstance(v, str) and str(v).strip())
            ]
            if ignored_hosts:
                log.warning(
                    "Ignoring non-IP entries in config.hosts: %s",
                    ", ".join(ignored_hosts[:8]),
                )
        else:
            self._hosts = {}
        configured_direct_exclude = config.get("direct_google_exclude", [])
        self._direct_google_exclude = {
            h.lower().rstrip(".")
            for h in (
                list(self._GOOGLE_DIRECT_EXACT_EXCLUDE) +
                list(configured_direct_exclude)
            )
        }
        configured_direct_allow = config.get("direct_google_allow", [])
        self._direct_google_allow = {
            h.lower().rstrip(".")
            for h in (
                list(self._GOOGLE_DIRECT_ALLOW_EXACT) +
                list(configured_direct_allow)
            )
        }

        # ── Per-host policy ────────────────────────────────────────
        # block_hosts  — refuse traffic entirely (close or 403)
        # bypass_hosts — route directly (no MITM, no relay)
        # Both accept exact hostnames and leading-dot suffix patterns,
        # e.g. ".local" matches any *.local domain.
        self._block_hosts  = self._load_host_rules(config.get("block_hosts", []))
        self._bypass_hosts = self._load_host_rules(config.get("bypass_hosts", []))

        # Route YouTube through the relay when requested; the Google frontend
        # IP can enforce SafeSearch on the SNI-rewrite path.
        if config.get("youtube_via_relay", False):
            self._SNI_REWRITE_SUFFIXES = tuple(
                s for s in SNI_REWRITE_SUFFIXES
                if s not in self._YOUTUBE_SNI_SUFFIXES
            )
            log.info("youtube_via_relay enabled — YouTube routed through relay")
        else:
            self._SNI_REWRITE_SUFFIXES = SNI_REWRITE_SUFFIXES

        try:
            from mitm import MITMCertManager
            self.mitm = MITMCertManager()
        except ImportError:
            log.error("Apps Script relay requires the 'cryptography' package.")
            log.error("Run: pip install cryptography")
            raise SystemExit(1)

    # ── Host-policy helpers ───────────────────────────────────────

    @staticmethod
    def _cfg_int(config: dict, key: str, default: int, *, minimum: int = 1) -> int:
        try:
            value = int(config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    @staticmethod
    def _cfg_float(config: dict, key: str, default: float,
                   *, minimum: float = 0.1) -> float:
        try:
            value = float(config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)

    @classmethod
    def _normalize_download_extensions(cls, raw) -> tuple[tuple[str, ...], bool]:
        values = raw if isinstance(raw, (list, tuple)) else cls._DOWNLOAD_DEFAULT_EXTS
        normalized: list[str] = []
        any_extension = False
        seen: set[str] = set()
        for item in values:
            ext = str(item).strip().lower()
            if not ext:
                continue
            if ext in {"*", ".*"}:
                any_extension = True
                continue
            if not ext.startswith("."):
                ext = "." + ext
            if ext not in seen:
                seen.add(ext)
                normalized.append(ext)
        if not normalized and not any_extension:
            normalized = list(cls._DOWNLOAD_DEFAULT_EXTS)
        return tuple(normalized), any_extension

    def _track_current_task(self) -> asyncio.Task | None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        return task

    def _untrack_task(self, task: asyncio.Task | None) -> None:
        if task is not None:
            self._client_tasks.discard(task)

    @staticmethod
    def _load_host_rules(raw) -> tuple[set[str], tuple[str, ...]]:
        """Accept a list of host strings; return (exact_set, suffix_tuple).

        A rule starting with '.' (e.g. ".internal") is a suffix rule.
        Everything else is treated as an exact match. Case-insensitive.
        """
        exact: set[str] = set()
        suffixes: list[str] = []
        for item in raw or []:
            h = str(item).strip().lower().rstrip(".")
            if not h:
                continue
            if h.startswith("."):
                suffixes.append(h)
            else:
                exact.add(h)
        return exact, tuple(suffixes)

    @staticmethod
    def _host_matches_rules(host: str,
                            rules: tuple[set[str], tuple[str, ...]]) -> bool:
        exact, suffixes = rules
        h = host.lower().rstrip(".")
        if h in exact:
            return True
        for s in suffixes:
            if h.endswith(s):
                return True
        return False

    def _is_blocked(self, host: str) -> bool:
        return self._host_matches_rules(host, self._block_hosts)

    def _is_bypassed(self, host: str) -> bool:
        return self._host_matches_rules(host, self._bypass_hosts)

    @staticmethod
    def _header_value(headers: dict | None, name: str) -> str:
        if not headers:
            return ""
        for key, value in headers.items():
            if key.lower() == name:
                return str(value)
        return ""

    def _cache_allowed(self, method: str, url: str,
                       headers: dict | None, body: bytes) -> bool:
        if method.upper() != "GET" or body:
            return False
        for name in UNCACHEABLE_HEADER_NAMES:
            if self._header_value(headers, name):
                return False
        return self.fronter._is_static_asset_url(url)

    @classmethod
    def _should_trace_host(cls, host: str) -> bool:
        h = host.lower().rstrip(".")
        return any(
            token == h or token in h or h.endswith("." + token)
            for token in cls._TRACE_HOST_SUFFIXES
        )

    def _log_response_summary(self, url: str, response: bytes):
        status, headers, body = self.fronter._split_raw_response(response)
        host = (urlparse(url).hostname or "").lower()

        if status >= 300 or self._should_trace_host(host):
            location = headers.get("location", "") or "-"
            server = headers.get("server", "") or "-"
            cf_ray = headers.get("cf-ray", "") or "-"
            content_type = headers.get("content-type", "") or "-"
            body_len = len(body)

            body_hint = "-"
            rate_limited = False

            # Handle text-like responses (HTML, plain text, JSON…)
            if ("text" in content_type.lower() or "json" in content_type.lower()) and body:
                sample = body[:1200].decode(errors="replace").lower()

                # --- Structured HTML title extraction ---
                if "<title>" in sample and "</title>" in sample:
                    title = sample.split("<title>", 1)[1].split("</title>", 1)[0]
                    body_hint = title.strip()[:120] or "-"

                # --- Known content patterns ---
                elif "captcha" in sample:
                    body_hint = "captcha"
                elif "turnstile" in sample:
                    body_hint = "turnstile"
                elif "loading" in sample:
                    body_hint = "loading"

                # --- Rate-limit / quota markers ---
                rate_limit_markers = (
                    "too many",
                    "rate limit",
                    "quota",
                    "quota exceeded",
                    "request limit",
                    "دفعات زیاد",
                    "بیش از حد",
                    "سرویس در طول یک روز",
                )

                if any(m in sample for m in rate_limit_markers):
                    rate_limited = True
                    body_hint = "quota_exceeded"

            log_msg = (
                "RESP ← %s status=%s type=%s len=%s server=%s location=%s cf-ray=%s hint=%s"
            )
            log_args = (
                host or url[:60],
                status,
                content_type,
                body_len,
                server,
                location,
                cf_ray,
                body_hint,
            )

            if rate_limited:
                log.warning("RATE LIMIT detected! " + log_msg, *log_args)
            else:
                log.info(log_msg, *log_args)

    async def start(self):
        self._loop = asyncio.get_running_loop()
        http_srv = await asyncio.start_server(self._on_client, self.host, self.port)
        socks_srv = None

        if self.socks_enabled:
            try:
                socks_srv = await asyncio.start_server(
                    self._on_socks_client, self.socks_host, self.socks_port
                )
            except OSError as e:
                log.error("SOCKS5 listener failed on %s:%d: %s",
                          self.socks_host, self.socks_port, e)

        self._servers = [s for s in (http_srv, socks_srv) if s]

        log.info(
            "HTTP proxy listening on %s:%d",
            self.host, self.port,
        )
        if socks_srv:
            log.info(
                "SOCKS5 proxy listening on %s:%d",
                self.socks_host, self.socks_port,
            )

        try:
            async with http_srv:
                if socks_srv:
                    async with socks_srv:
                        await asyncio.gather(
                            http_srv.serve_forever(),
                            socks_srv.serve_forever(),
                        )
                else:
                    await http_srv.serve_forever()
        except asyncio.CancelledError:
            raise

    async def stop(self):
        """Shut down all listeners and release relay resources."""
        for srv in self._servers:
            try:
                srv.close()
            except Exception:
                pass
        for srv in self._servers:
            try:
                await srv.wait_closed()
            except Exception:
                pass
        self._servers = []

        current = asyncio.current_task()
        client_tasks = [task for task in self._client_tasks if task is not current]
        for task in client_tasks:
            task.cancel()
        if client_tasks:
            await asyncio.gather(*client_tasks, return_exceptions=True)
        self._client_tasks.clear()

        # --- VIDEO_PREFETCH_STOP_START ---
        prefetch_tasks = list(getattr(self, "_video_prefetch_tasks", set()))
        for task in prefetch_tasks:
            task.cancel()
        if prefetch_tasks:
            await asyncio.gather(*prefetch_tasks, return_exceptions=True)
        self._video_prefetch_tasks.clear()
        # --- VIDEO_PREFETCH_STOP_END ---


        # --- MANIFEST_PREFETCH_STOP ---
        manifest_tasks = list(getattr(self, "_manifest_prefetch_tasks", set()))
        for task in manifest_tasks:
            task.cancel()
        if manifest_tasks:
            await asyncio.gather(*manifest_tasks, return_exceptions=True)
        self._manifest_prefetch_tasks.clear()

        try:
            await self.fronter.close()
        except Exception as exc:
            log.debug("fronter.close: %s", exc)

    # ── client handler ────────────────────────────────────────────

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        task = self._track_current_task()
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not first_line:
                return

            # Read remaining headers
            header_block = first_line
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                header_block += line
                if len(header_block) > MAX_HEADER_BYTES:
                    log.warning("Request header block exceeds cap — closing")
                    return
                if line in (b"\r\n", b"\n", b""):
                    break

            if _has_unsupported_transfer_encoding(header_block):
                log.warning("Unsupported Transfer-Encoding on client request")
                writer.write(
                    b"HTTP/1.1 501 Not Implemented\r\n"
                    b"Connection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
                return

            request_line = first_line.decode(errors="replace").strip()
            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                return

            method = parts[0].upper()

            if method == "CONNECT":
                await self._do_connect(parts[1], reader, writer)
            else:
                await self._do_http(header_block, reader, writer)

        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            log.debug("Timeout: %s", addr)
        except Exception as e:
            log.error("Error (%s): %s", addr, e)
        finally:
            self._untrack_task(task)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _on_socks_client(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        task = self._track_current_task()
        try:
            header = await asyncio.wait_for(reader.readexactly(2), timeout=15)
            ver, nmethods = header[0], header[1]
            if ver != 5:
                return

            methods = await asyncio.wait_for(reader.readexactly(nmethods), timeout=10)
            if 0x00 not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return

            writer.write(b"\x05\x00")
            await writer.drain()

            req = await asyncio.wait_for(reader.readexactly(4), timeout=15)
            ver, cmd, _rsv, atyp = req
            if ver != 5 or cmd != 0x01:
                writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                return

            if atyp == 0x01:
                raw = await asyncio.wait_for(reader.readexactly(4), timeout=10)
                host = socket.inet_ntoa(raw)
            elif atyp == 0x03:
                ln = (await asyncio.wait_for(reader.readexactly(1), timeout=10))[0]
                host = (await asyncio.wait_for(reader.readexactly(ln), timeout=10)).decode(
                    errors="replace"
                )
            elif atyp == 0x04:
                raw = await asyncio.wait_for(reader.readexactly(16), timeout=10)
                host = socket.inet_ntop(socket.AF_INET6, raw)
            else:
                writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
                return

            port_raw = await asyncio.wait_for(reader.readexactly(2), timeout=10)
            port = int.from_bytes(port_raw, "big")

            log.info("SOCKS5 CONNECT → %s:%d", host, port)

            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            await self._handle_target_tunnel(host, port, reader, writer)

        except asyncio.IncompleteReadError:
            pass
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            log.debug("SOCKS5 timeout: %s", addr)
        except Exception as e:
            log.error("SOCKS5 error (%s): %s", addr, e)
        finally:
            self._untrack_task(task)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── CONNECT (HTTPS tunnelling) ────────────────────────────────

    async def _do_connect(self, target: str, reader, writer):
        host, _, port_str = target.rpartition(":")
        try:
            port = int(port_str) if port_str else 443
        except ValueError:
            log.warning("CONNECT invalid target: %r", target)
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return
        if not host:
            host, port = target, 443

        log.info("CONNECT → %s:%d", host, port)

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        await self._handle_target_tunnel(host, port, reader, writer)

    async def _handle_target_tunnel(self, host: str, port: int,
                                    reader: asyncio.StreamReader,
                                    writer: asyncio.StreamWriter):
        """Route a target connection through the Apps Script relay."""
        # ── Block / bypass policy ─────────────────────────────────
        if self._is_blocked(host):
            log.warning("BLOCKED → %s:%d (matches block_hosts)", host, port)
            try:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
            return

        if self._is_bypassed(host):
            log.info("Bypass tunnel → %s:%d (matches bypass_hosts)", host, port)
            await self._do_direct_tunnel(host, port, reader, writer)
            return

        # ── IP-literal destinations ───────────────────────────────
        # Prefer a direct tunnel first (works for unblocked IPs and keeps
        # TLS end-to-end). If the network blocks the route (common for
        # Telegram data-centers behind DPI), fall back to:
        #   • port 443 → MITM + relay through Apps Script
        #   • port 80  → plain-HTTP relay through Apps Script
        #   • other    → give up (non-HTTP; can't be relayed)
        # We use a shorter connect timeout for IP literals (4 s) because
        # when the route is DPI-dropped, waiting longer doesn't help and
        # clients like Telegram speed up DC-rotation when we fail fast.
        # We remember per-IP failures for a short while so subsequent
        # connects skip the doomed direct attempt.
        if _is_ip_literal(host):
            if not self._direct_temporarily_disabled(host):
                log.info("Direct tunnel → %s:%d (IP literal)", host, port)
                ok = await self._do_direct_tunnel(
                    host, port, reader, writer, timeout=4.0,
                )
                if ok:
                    return
                self._remember_direct_failure(host, ttl=300)
                if port not in (80, 443):
                    log.debug("Direct tunnel failed for %s:%d", host, port) if self._is_noisy_direct_fail_host(host, port) else log.warning("Direct tunnel failed for %s:%d", host, port)
                    return
                log.warning(
                    "Direct tunnel fallback → %s:%d (switching to relay)",
                    host, port,
                )
            else:
                log.info(
                    "Relay fallback → %s:%d (direct temporarily disabled)",
                    host, port,
                )
            if port == 443:
                await self._do_mitm_connect(host, port, reader, writer)
            elif port == 80:
                await self._do_plain_http_tunnel(host, port, reader, writer)
            return

        override_ip = None if _force_gas_relay_host(host) else self._sni_rewrite_ip(host)
        if override_ip:
            # SNI-blocked domain: MITM-decrypt from browser, then
            # re-connect to the override IP with SNI=front_domain so
            # the ISP never sees the blocked hostname in the TLS handshake.
            log.info("SNI-rewrite tunnel → %s via %s (SNI: %s)",
                     host, override_ip, self.fronter.sni_host)
            await self._do_sni_rewrite_tunnel(host, port, reader, writer,
                                              connect_ip=override_ip)
        elif self._is_google_domain(host) and not _force_gas_relay_host(host):
            if self._direct_temporarily_disabled(host):
                log.info("Relay fallback → %s (direct tunnel temporarily disabled)", host)
                if port == 443:
                    await self._do_mitm_connect(host, port, reader, writer)
                else:
                    await self._do_plain_http_tunnel(host, port, reader, writer)
                return

            log.info("Direct tunnel → %s (Google domain, skipping relay)", host)
            ok = await self._do_direct_tunnel(host, port, reader, writer)
            if ok:
                return

            self._remember_direct_failure(host)
            log.warning("Direct tunnel fallback → %s (switching to relay)", host)
            if port == 443:
                await self._do_mitm_connect(host, port, reader, writer)
            else:
                await self._do_plain_http_tunnel(host, port, reader, writer)
        elif port == 443:
            await self._do_mitm_connect(host, port, reader, writer)
        elif port == 80:
            await self._do_plain_http_tunnel(host, port, reader, writer)
        else:
            # Non-HTTP port (e.g. mtalk:5228 XMPP, IMAP, SMTP, SSH) —
            # payload isn't HTTP, so we can't relay or MITM. Tunnel bytes.
            log.info("Direct tunnel → %s:%d (non-HTTP port)", host, port)
            ok = await self._do_direct_tunnel(host, port, reader, writer)
            if not ok:
                log.debug("Direct tunnel failed for %s:%d", host, port) if self._is_noisy_direct_fail_host(host, port) else log.warning("Direct tunnel failed for %s:%d", host, port)

    # ── Hosts override (fake DNS) ─────────────────────────────────

    # Built-in list of domains that must be reached via Google's frontend IP
    # with SNI rewritten to `front_domain` (default: www.google.com).
    # Source: constants.SNI_REWRITE_SUFFIXES.
    # When youtube_via_relay is enabled the YouTube suffixes are removed so
    # YouTube goes through the Apps Script relay instead.
    _YOUTUBE_SNI_SUFFIXES = frozenset({
        "youtube.com", "youtu.be", "youtube-nocookie.com",
    })
    _SNI_REWRITE_SUFFIXES = SNI_REWRITE_SUFFIXES

    def _sni_rewrite_ip(self, host: str) -> str | None:
        """Return the IP to SNI-rewrite `host` through, or None.

        Order of precedence:
          1. Explicit entry in config `hosts` map (exact or suffix match).
          2. Built-in `_SNI_REWRITE_SUFFIXES` → mapped to config `google_ip`.
        """
        ip = self._hosts_ip(host)
        if ip:
            return ip
        h = host.lower().rstrip(".")
        for suffix in self._SNI_REWRITE_SUFFIXES:
            if h == suffix or h.endswith("." + suffix):
                return self.fronter.connect_host  # configured google_ip
        return None

    def _hosts_ip(self, host: str) -> str | None:
        """Return override IP for host if defined in config 'hosts', else None.

        Supports exact match and suffix match (e.g. 'youtube.com' matches
        'www.youtube.com', 'm.youtube.com', etc.).
        """
        h = host.lower().rstrip(".")
        if h in self._hosts:
            return self._hosts[h]
        # suffix match: check every parent label
        parts = h.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in self._hosts:
                return self._hosts[parent]
        return None

    # ── Google domain detection ───────────────────────────────────

    # Google-owned domains that may use the raw direct-tunnel shortcut.
    # YouTube/googlevideo SNIs are blocked; they go through
    # _do_sni_rewrite_tunnel via the hosts map instead.
    # Source: constants.GOOGLE_OWNED_SUFFIXES / GOOGLE_OWNED_EXACT.
    _GOOGLE_OWNED_SUFFIXES = GOOGLE_OWNED_SUFFIXES
    _GOOGLE_OWNED_EXACT = GOOGLE_OWNED_EXACT

    def _is_google_domain(self, host: str) -> bool:
        """Return True if host should use the raw direct Google shortcut."""
        h = host.lower().rstrip(".")
        if self._is_direct_google_excluded(h):
            return False
        if not self._is_google_owned_domain(h):
            return False
        return self._is_direct_google_allowed(h)

    def _is_google_owned_domain(self, host: str) -> bool:
        if host in self._GOOGLE_OWNED_EXACT:
            return True
        for suffix in self._GOOGLE_OWNED_SUFFIXES:
            if host.endswith(suffix):
                return True
        return False

    def _is_direct_google_excluded(self, host: str) -> bool:
        if host in self._direct_google_exclude:
            return True
        for suffix in self._GOOGLE_DIRECT_SUFFIX_EXCLUDE:
            if host.endswith(suffix):
                return True
        for token in self._direct_google_exclude:
            if token.startswith(".") and host.endswith(token):
                return True
        return False

    def _is_direct_google_allowed(self, host: str) -> bool:
        if host in self._direct_google_allow:
            return True
        for suffix in self._GOOGLE_DIRECT_ALLOW_SUFFIXES:
            if host.endswith(suffix):
                return True
        for token in self._direct_google_allow:
            if token.startswith(".") and host.endswith(token):
                return True
        return False

    def _direct_temporarily_disabled(self, host: str) -> bool:
        h = host.lower().rstrip(".")
        now = time.time()
        disabled = False
        for key in self._direct_failure_keys(h):
            until = self._direct_fail_until.get(key, 0)
            if until > now:
                disabled = True
            else:
                self._direct_fail_until.pop(key, None)
        return disabled

    def _remember_direct_failure(self, host: str, ttl: int = 600):
        until = time.time() + ttl
        for key in self._direct_failure_keys(host.lower().rstrip(".")):
            self._direct_fail_until[key] = until

    def _is_noisy_direct_fail_host(self, host: str, port: int) -> bool:
        h = str(host or "").lower().rstrip(".")
        if h == "mtalk.google.com" and int(port or 0) in (5228, 5229, 5230):
            return True
        if h.startswith("149.154.") or h.startswith("91.108."):
            return True
        return False


    def _direct_failure_keys(self, host: str) -> tuple[str, ...]:
        keys = [host]
        if host.endswith(".google.com") or host == "google.com":
            keys.append("*.google.com")
        if host.endswith(".googleapis.com") or host == "googleapis.com":
            keys.append("*.googleapis.com")
        if host.endswith(".gstatic.com") or host == "gstatic.com":
            keys.append("*.gstatic.com")
        if host.endswith(".googleusercontent.com") or host == "googleusercontent.com":
            keys.append("*.googleusercontent.com")
        return tuple(dict.fromkeys(keys))

    async def _open_tcp_connection(self, target: str, port: int,
                                   timeout: float = 10.0):
        """Connect with IPv4-first resolution and clearer failure reporting."""
        errors: list[str] = []
        loop = asyncio.get_running_loop()

        # Strip IPv6 brackets (CONNECT may deliver "[::1]" as the hostname).
        # ipaddress.ip_address() rejects the bracketed form, which would
        # otherwise force a DNS lookup for an IP literal and fail.
        lookup_target = target.strip()
        if lookup_target.startswith("[") and lookup_target.endswith("]"):
            lookup_target = lookup_target[1:-1]

        try:
            ipaddress.ip_address(lookup_target)
            candidates = [(0, lookup_target)]
        except ValueError:
            try:
                infos = await asyncio.wait_for(
                    loop.getaddrinfo(
                        lookup_target,
                        port,
                        family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM,
                    ),
                    timeout=timeout,
                )
            except Exception as exc:
                raise OSError(f"dns lookup failed for {lookup_target}: {exc!r}") from exc

            candidates = []
            seen = set()
            for family, _type, _proto, _canon, sockaddr in infos:
                ip = sockaddr[0]
                key = (family, ip)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((family, ip))

            candidates.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)

        for family, ip in candidates:
            try:
                return await asyncio.wait_for(
                    asyncio.open_connection(ip, port, family=family or 0),
                    timeout=timeout,
                )
            except Exception as exc:
                fam = "ipv4" if family == socket.AF_INET else (
                    "ipv6" if family == socket.AF_INET6 else "auto"
                )
                errors.append(f"{ip} ({fam}): {exc!r}")

        raise OSError("; ".join(errors) or f"connect failed for {target}:{port}")

    # ── Direct tunnel (no MITM) ───────────────────────────────────

    async def _do_direct_tunnel(self, host: str, port: int,
                                reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter,
                                connect_ip: str | None = None,
                                timeout: float | None = None):
        """Pipe raw TLS bytes directly to the target server.

        connect_ip overrides DNS: the TCP connection goes to that IP
        while the browser's TLS (SNI=host) is piped through unchanged.
        Without an override we connect to the real hostname so browser-safe
        Google properties (Gemini assets, Play, Accounts, etc.) use their
        normal edge instead of being forced onto the fronting IP.
        """
        target_ip = connect_ip or host
        effective_timeout = (
            self._tcp_connect_timeout if timeout is None else float(timeout)
        )
        if self._is_noisy_direct_fail_host(host, port) and timeout is None:
            effective_timeout = min(float(effective_timeout), 2.0)
        try:
            r_remote, w_remote = await self._open_tcp_connection(
                target_ip, port, timeout=effective_timeout,
            )
        except Exception as e:
            log.debug("Direct tunnel connect failed (%s via %s): %s",
                      host, target_ip, e) if self._is_noisy_direct_fail_host(host, port) else log.error("Direct tunnel connect failed (%s via %s): %s",
                      host, target_ip, e)
            return False

        async def pipe(src, dst, label):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            except Exception as e:
                log.debug("Pipe %s ended: %s", label, e)
            finally:
                # Half-close rather than hard-close so the other direction
                # can still flush final bytes (important for TLS close_notify).
                try:
                    if not dst.is_closing() and dst.can_write_eof():
                        dst.write_eof()
                except Exception:
                    try:
                        dst.close()
                    except Exception:
                        pass

        await asyncio.gather(
            pipe(reader, w_remote, f"client→{host}"),
            pipe(r_remote, writer, f"{host}→client"),
        )
        return True

    # ── SNI-rewrite tunnel ────────────────────────────────────────

    async def _do_sni_rewrite_tunnel(self, host: str, port: int, reader, writer,
                                     connect_ip: str | None = None):
        """MITM-decrypt TLS from browser, then re-encrypt toward connect_ip
        using SNI=front_domain (e.g. www.google.com).

        The ISP only ever sees SNI=www.google.com in the outgoing handshake,
        hiding the blocked hostname (e.g. www.youtube.com).
        """
        target_ip = connect_ip or self.fronter.connect_host
        sni_out   = self.fronter.sni_host  # e.g. "www.google.com"

        # Step 1: MITM — accept TLS from the browser
        ssl_ctx_server = self.mitm.get_server_context(host)
        loop = asyncio.get_running_loop()
        transport = writer.transport
        protocol  = transport.get_protocol()
        try:
            new_transport = await loop.start_tls(
                transport, protocol, ssl_ctx_server, server_side=True,
            )
        except Exception as e:
            log.debug("SNI-rewrite TLS accept failed (%s): %s", host, e)
            return
        writer._transport = new_transport

        # Step 2: open outgoing TLS to target IP with the safe SNI
        ssl_ctx_client = ssl.create_default_context()
        if certifi is not None:
            try:
                ssl_ctx_client.load_verify_locations(cafile=certifi.where())
            except Exception:
                pass
        if not self.fronter.verify_ssl:
            ssl_ctx_client.check_hostname = False
            ssl_ctx_client.verify_mode = ssl.CERT_NONE
        try:
            r_out, w_out = await asyncio.wait_for(
                asyncio.open_connection(
                    target_ip, port,
                    ssl=ssl_ctx_client,
                    server_hostname=sni_out,
                ),
                timeout=self._tcp_connect_timeout,
            )
        except Exception as e:
            log.error("SNI-rewrite outbound connect failed (%s via %s): %s",
                      host, target_ip, e)
            return

        # Step 3: pipe application-layer bytes between the two TLS sessions
        async def pipe(src, dst, label):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            except Exception as exc:
                log.debug("Pipe %s ended: %s", label, exc)
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(
            pipe(reader, w_out, f"client→{host}"),
            pipe(r_out,  writer, f"{host}→client"),
        )

    # ── MITM CONNECT (apps_script mode) ───────────────────────────

    async def _do_plain_http_tunnel(self, host: str, port: int, reader, writer):
        """Handle plain HTTP over SOCKS5 in apps_script mode."""
        log.info("Plain HTTP relay → %s:%d", host, port)
        await self._relay_http_stream(host, port, reader, writer)

    async def _do_mitm_connect(self, host: str, port: int, reader, writer):
        """Intercept TLS, decrypt HTTP, and relay through Apps Script."""
        ssl_ctx = self.mitm.get_server_context(host)

        # Upgrade the existing connection to TLS (we are the server)
        loop = asyncio.get_running_loop()
        transport = writer.transport
        protocol = transport.get_protocol()

        try:
            new_transport = await loop.start_tls(
                transport, protocol, ssl_ctx, server_side=True,
            )
        except Exception as e:
            # TLS handshake failed. Common causes:
            #   • Telegram Desktop / MTProto over port 443 sends obfuscated
            #     non-TLS bytes — we literally cannot decrypt these, and
            #     since the target IP is blocked we can't direct-tunnel
            #     either. Telegram will rotate to another DC on its own;
            #     failing fast here lets that happen sooner.
            #   • Client CONNECTs but never speaks TLS (some probes).
            if _is_ip_literal(host) and port == 443:
                log.info(
                    "Non-TLS traffic on %s:%d (likely Telegram MTProto / "
                    "obfuscated protocol). This DC appears blocked; the "
                    "client should rotate to another endpoint shortly.",
                    host, port,
                )
            elif port != 443:
                log.debug(
                    "TLS handshake skipped for %s:%d (non-HTTPS): %s",
                    host, port, e,
                )
            else:
                log.debug("TLS handshake failed for %s: %s", host, e)
            # Close the client side so it fails fast and can retry, rather
            # than hanging on a half-open connection.
            try:
                if not writer.is_closing():
                    writer.close()
            except Exception:
                pass
            return

        # Update writer to use the new TLS transport
        writer._transport = new_transport

        await self._relay_http_stream(host, port, reader, writer)

    async def _relay_http_stream(self, host: str, port: int, reader, writer):
        """Read decrypted/origin-form HTTP requests and relay them."""
        # Read and relay HTTP requests from the browser (now decrypted)
        while True:
            try:
                first_line = await asyncio.wait_for(
                    reader.readline(), timeout=CLIENT_IDLE_TIMEOUT
                )
                if not first_line:
                    break

                header_block = first_line
                oversized_headers = False
                while True:
                    line = await asyncio.wait_for(reader.readline(), timeout=10)
                    header_block += line
                    if len(header_block) > MAX_HEADER_BYTES:
                        oversized_headers = True
                        break
                    if line in (b"\r\n", b"\n", b""):
                        break

                # Reject truncated / oversized header blocks cleanly rather
                # than forwarding a half-parsed request to the relay — doing
                # so would send malformed JSON payloads to Apps Script and
                # leave the client hanging until its own timeout fires.
                if oversized_headers:
                    log.warning(
                        "MITM header block exceeds %d bytes — closing (%s)",
                        MAX_HEADER_BYTES, host,
                    )
                    try:
                        writer.write(
                            b"HTTP/1.1 431 Request Header Fields Too Large\r\n"
                            b"Connection: close\r\n"
                            b"Content-Length: 0\r\n\r\n"
                        )
                        await writer.drain()
                    except Exception:
                        pass
                    break

                # Read body
                body = b""
                if _has_unsupported_transfer_encoding(header_block):
                    log.warning("Unsupported Transfer-Encoding → %s:%d", host, port)
                    writer.write(
                        b"HTTP/1.1 501 Not Implemented\r\n"
                        b"Connection: close\r\n"
                        b"Content-Length: 0\r\n\r\n"
                    )
                    await writer.drain()
                    break
                length = _parse_content_length(header_block)
                if length > MAX_REQUEST_BODY_BYTES:
                    raise ValueError(f"Request body too large: {length} bytes")
                if length > 0:
                    body = await reader.readexactly(length)

                # Parse the request
                request_line = first_line.decode(errors="replace").strip()
                parts = request_line.split(" ", 2)
                if len(parts) < 2:
                    break

                method = parts[0]
                path = parts[1]

                # Parse headers
                headers = {}
                for raw_line in header_block.split(b"\r\n")[1:]:
                    if b":" in raw_line:
                        k, v = raw_line.decode(errors="replace").split(":", 1)
                        headers[k.strip()] = v.strip()

                # Shortening the length of X API URLs to prevent relay errors.
                if (host == "x.com" or host == "twitter.com") and  re.match(r"/i/api/graphql/[^/]+/[^?]+\?variables=", path):
                    path = path.split("&")[0]

                # MITM traffic arrives as origin-form paths; SOCKS/plain HTTP can
                # also send absolute-form requests. Normalize both to full URLs.
                if path.startswith("http://") or path.startswith("https://"):
                    url = path
                elif port == 443:
                    url = f"https://{host}{path}"
                elif port == 80:
                    url = f"http://{host}{path}"
                else:
                    url = f"http://{host}:{port}{path}"

                try:
                    stats.add_proxy_request((urlparse(url).hostname or host or url))
                except Exception:
                    pass

                log.info("MITM → %s %s", method, _safe_log_url(url))

                # ── CORS: extract relevant request headers ─────────────
                origin = self._header_value(headers, "origin")
                acr_method = self._header_value(
                    headers, "access-control-request-method",
                )
                acr_headers = self._header_value(
                    headers, "access-control-request-headers",
                )

                # CORS preflight — respond directly. Apps Script's
                # UrlFetchApp does not support the OPTIONS method, so
                # forwarding preflights would always fail and break every
                # cross-origin fetch/XHR the browser runs through us.
                if method.upper() == "OPTIONS" and acr_method:
                    log.debug(
                        "CORS preflight → %s (responding locally)",
                        url[:60],
                    )
                    _cors_resp = self._cors_preflight_response(
                        origin, acr_method, acr_headers,
                    )
                    writer.write(_cors_resp)
                    await writer.drain()
                    try:
                        stats.add_proxy_response((urlparse(url).hostname or host or url), len(_cors_resp))
                    except Exception:
                        pass
                    continue

                # --- VIDEO_TRAFFIC_BEFORE_STREAM_MITM ---
                self._log_video_traffic_debug(method, url, headers, "before_stream_mitm")

                # --- SABR_PASSTHROUGH_MITM ---
                if await self._maybe_video_passthrough(method, url, headers, body, writer, origin):
                    continue

                # --- MANIFEST_PREFETCH_SERVE_MITM ---
                if await self._maybe_serve_manifest_prefetch_cache(method, url, headers, writer):
                    continue

                if await self._maybe_stream_download(method, url, headers, body, writer):
                    continue

                # --- VIDEO_PREFETCH_SERVE_MITM_START ---
                if await self._maybe_serve_video_range_cache(method, url, headers, writer):
                    continue
                # --- VIDEO_PREFETCH_SERVE_MITM_END ---

                # Check local cache first (GET only)
                response = None
                if self._cache_allowed(method, url, headers, body):
                    response = self._cache.get(url)
                    if response:
                        log.debug("Cache HIT: %s", url[:60])

                if response is None:
                    # Relay through Apps Script
                    try:
                        response = await self._relay_smart(method, url, headers, body)
                    except Exception as e:
                        log.error("Relay error (%s): %s", url[:60], e)
                        err_body = f"Relay error: {e}".encode()
                        response = (
                            b"HTTP/1.1 502 Bad Gateway\r\n"
                            b"Content-Type: text/plain\r\n"
                            b"Content-Length: " + str(len(err_body)).encode() + b"\r\n"
                            b"\r\n" + err_body
                        )

                    # Cache successful GET responses
                    if self._cache_allowed(method, url, headers, body) and response:
                        ttl = ResponseCache.parse_ttl(response, url)
                        if ttl > 0:
                            self._cache.put(url, response, ttl)
                            log.debug("Cached (%ds): %s", ttl, url[:60])

                # Inject permissive CORS headers whenever the browser sent
                # an Origin (cross-origin XHR / fetch). Without this, the
                # browser blocks the response even though the relay fetched
                # it successfully.
                if origin and response:
                    response = self._inject_cors_headers(response, origin)

                self._log_response_summary(url, response)

                writer.write(response)
                await writer.drain()
                try:
                    stats.add_proxy_response((urlparse(url).hostname or host or url), len(response or b""))
                except Exception:
                    pass

                # --- MANIFEST_PREFETCH_AFTER_MITM ---
                self._handle_manifest_verified_prefetch(method, url, headers, response)

                # --- VIDEO_PREFETCH_SCHEDULE_MITM_START ---
                self._schedule_video_prefetch(method, url, headers, response)
                # --- VIDEO_PREFETCH_SCHEDULE_MITM_END ---

            except asyncio.TimeoutError:
                break
            except asyncio.IncompleteReadError:
                break
            except ConnectionError:
                break
            except Exception as e:
                log.error("MITM handler error (%s): %s", host, e)
                break

    # ── CORS helpers ──────────────────────────────────────────────

    @staticmethod
    def _cors_preflight_response(origin: str, acr_method: str,
                                 acr_headers: str) -> bytes:
        """Build a 204 response that satisfies a CORS preflight locally.

        Apps Script's UrlFetchApp does not support OPTIONS, so we have to
        answer preflights here instead of forwarding them.
        """
        allow_origin = origin or "*"
        allow_methods = (
            f"{acr_method}, GET, POST, PUT, DELETE, PATCH, OPTIONS"
            if acr_method else
            "GET, POST, PUT, DELETE, PATCH, OPTIONS"
        )
        allow_headers = acr_headers or "*"
        return (
            "HTTP/1.1 204 No Content\r\n"
            f"Access-Control-Allow-Origin: {allow_origin}\r\n"
            f"Access-Control-Allow-Methods: {allow_methods}\r\n"
            f"Access-Control-Allow-Headers: {allow_headers}\r\n"
            "Access-Control-Allow-Credentials: true\r\n"
            "Access-Control-Max-Age: 86400\r\n"
            "Vary: Origin\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        ).encode()

    @staticmethod
    def _inject_cors_headers(response: bytes, origin: str) -> bytes:
        """Strip existing Access-Control-* headers and add permissive ones.

        Keeps the body untouched; only rewrites the header block. Using
        the exact browser-supplied Origin (rather than "*") is required
        when the request is credentialed (cookies, Authorization).
        """
        sep = b"\r\n\r\n"
        if sep not in response:
            return response
        header_section, body = response.split(sep, 1)
        lines = header_section.decode(errors="replace").split("\r\n")
        lines = [ln for ln in lines
                 if not ln.lower().startswith("access-control-")]
        allow_origin = origin or "*"
        lines += [
            f"Access-Control-Allow-Origin: {allow_origin}",
            "Access-Control-Allow-Credentials: true",
            "Access-Control-Allow-Methods: GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "Access-Control-Allow-Headers: *",
            "Access-Control-Expose-Headers: *",
            "Vary: Origin",
        ]
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body

    # --- VIDEO_PREFETCH_METHODS_START ---
    def _is_video_request(self, url: str, headers: dict | None) -> bool:
        if not getattr(self, "_video_prefetch_enabled", False):
            return False

        u = str(url or "").lower()
        path = u.split("?", 1)[0]

        if any(path.endswith(ext) for ext in self._video_extensions):
            return True

        if any(token in u for token in self._video_keywords):
            return True

        accept = self._header_value(headers, "accept").lower()
        content_type = self._header_value(headers, "content-type").lower()
        sec_dest = self._header_value(headers, "sec-fetch-dest").lower()

        if "video/" in accept or "audio/" in accept:
            return True
        if "video/" in content_type or "audio/" in content_type:
            return True
        if sec_dest in {"video", "audio"}:
            return True

        return False

    @staticmethod
    def _parse_range_header(range_header: str) -> tuple[int, int | None] | None:
        m = re.match(
            r"\s*bytes\s*=\s*(\d+)\s*-\s*(\d*)\s*$",
            str(range_header or ""),
            re.I,
        )
        if not m:
            return None

        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else None

        if end is not None and end < start:
            return None

        return start, end

    @staticmethod
    def _response_content_range_end(raw_response: bytes) -> int | None:
        if not raw_response:
            return None

        hdr_end = raw_response.find(b"\r\n\r\n")
        if hdr_end < 0:
            return None

        hdr = raw_response[:hdr_end].decode(errors="replace")
        m = re.search(
            r"(?im)^content-range:\s*bytes\s+(\d+)\s*-\s*(\d+)\s*/",
            hdr,
        )
        if not m:
            return None

        return int(m.group(2))

    def _copy_headers_with_range(self, headers: dict | None, range_header: str) -> dict:
        new_headers = dict(headers or {})
        found = None

        for key in list(new_headers.keys()):
            if key.lower() == "range":
                found = key
                break

        if found is None:
            new_headers["Range"] = range_header
        else:
            new_headers[found] = range_header

        return new_headers

    async def _maybe_serve_video_range_cache(
        self,
        method: str,
        url: str,
        headers: dict | None,
        writer,
    ) -> bool:
        if method.upper() != "GET":
            return False

        range_header = self._header_value(headers, "range")
        if not range_header:
            return False

        if not self._is_video_request(url, headers):
            return False

        cached = await self._video_cache.get(method, url, range_header)
        if cached:
            log.info("Video prefetch HIT -> %s %s", range_header, _safe_log_url(url, 120))

            writer.write(cached)
            await writer.drain()

            try:
                stats.add_video_prefetch("hit", len(cached))
                stats.add_proxy_response((urlparse(url).hostname or url), len(cached))
            except Exception:
                pass

            self._schedule_video_prefetch(method, url, headers, cached)
            return True

        # Important:
        # Previously a real video Range MISS returned False and fell back to
        # normal relay. That meant only prefetch requests used video-priority,
        # while the actual player request did not. Now the real player request
        # also uses video-priority, then the response is cached and follow-up
        # ranges are scheduled.
        log.info("Video Range MISS -> %s %s", range_header, _safe_log_url(url, 120))
        try:
            stats.add_video_prefetch("miss")
        except Exception:
            pass

        try:
            response = await self._relay_video_priority(method, url, headers, b"")
        except Exception as exc:
            log.error("Video Range priority relay error (%s): %s", _safe_log_url(url, 100), exc)
            err_body = f"Video Range priority relay error: {exc}".encode()
            response = (
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(err_body)).encode() + b"\r\n"
                b"\r\n" + err_body
            )

        if not response:
            return False

        status = self._raw_response_status(response)

        if status == 206:
            await self._video_cache.put(method, url, range_header, response)
            try:
                stats.add_video_prefetch("bytes", len(response))
            except Exception:
                pass
        elif status in (429, 500, 502, 503, 504):
            log.warning(
                "Video Range priority returned status=%s range=%s url=%s",
                status,
                range_header,
                _safe_log_url(url, 120),
            )

        self._log_response_summary(url, response)

        writer.write(response)
        await writer.drain()

        try:
            stats.add_proxy_response((urlparse(url).hostname or url), len(response or b""))
        except Exception:
            pass

        self._schedule_video_prefetch(method, url, headers, response)
        return True


    def _schedule_video_prefetch(
        self,
        method: str,
        url: str,
        headers: dict | None,
        response: bytes | None,
    ):
        if not getattr(self, "_video_prefetch_enabled", False):
            return

        if method.upper() != "GET" or not response:
            return

        if not self._is_video_request(url, headers):
            return

        range_header = self._header_value(headers, "range")
        parsed = self._parse_range_header(range_header)
        if not parsed:
            if self._is_video_request(url, headers):
                log.info("Video request without Range -> %s", _safe_log_url(url, 90))
            return

        current_start, current_end = parsed
        actual_end = self._response_content_range_end(response)

        if actual_end is not None:
            current_end = actual_end
        elif current_end is None:
            current_end = current_start + self._video_prefetch_chunk_size - 1

        task = asyncio.create_task(
            self._run_video_prefetch(method, url, headers, current_end)
        )
        self._video_prefetch_tasks.add(task)
        task.add_done_callback(self._video_prefetch_tasks.discard)

    async def _run_video_prefetch(
        self,
        method: str,
        url: str,
        headers: dict | None,
        current_end: int,
    ):
        if self._video_prefetch_count <= 0:
            return

        next_start = int(current_end) + 1

        for _ in range(self._video_prefetch_count):
            next_end = next_start + self._video_prefetch_chunk_size - 1
            range_header = f"bytes={next_start}-{next_end}"
            cache_key = VideoRangeCache.key(method, url, range_header)

            if cache_key in self._video_prefetch_inflight:
                next_start = next_end + 1
                continue

            if await self._video_cache.get(method, url, range_header):
                next_start = next_end + 1
                continue

            self._video_prefetch_inflight.add(cache_key)

            try:
                async with self._video_prefetch_sem:
                    try:
                        stats.add_video_prefetch("request")
                        stats.add_video_prefetch("active_start")
                    except Exception:
                        pass

                    pf_headers = self._copy_headers_with_range(headers, range_header)
                    log.info("Video prefetch -> %s %s", range_header, _safe_log_url(url, 90))

                    raw = await self._relay_video_priority(method, url, pf_headers, b"")

                    if raw and (
                        raw.startswith(b"HTTP/1.1 206") or
                        raw.startswith(b"HTTP/1.0 206")
                    ):
                        await self._video_cache.put(method, url, range_header, raw)
                        try:
                            stats.add_video_prefetch("bytes", len(raw))
                        except Exception:
                            pass
                    else:
                        break

                next_start = next_end + 1

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("Video prefetch error (%s): %s", url[:60], exc)
                try:
                    stats.add_video_prefetch("error")
                except Exception:
                    pass
                break
            finally:
                self._video_prefetch_inflight.discard(cache_key)
                try:
                    stats.add_video_prefetch("active_done")
                except Exception:
                    pass
    # --- VIDEO_PREFETCH_METHODS_END ---



    # --- VIDEO_TRAFFIC_DEBUG_START ---
    def _is_video_like_traffic(self, url: str, headers: dict | None) -> bool:
        u = str(url or "").lower()
        path = urlparse(u).path.lower()

        video_exts = (
            ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv",
            ".ts", ".m2ts", ".m3u8", ".mpd", ".m4s", ".mp2t",
            ".3gp", ".3gpp", ".ogv", ".ogg",
        )

        video_words = (
            "videoplayback",
            "googlevideo",
            "mime=video",
            "mime=audio",
            "video/",
            "audio/",
            "hls",
            "dash",
            "itag=",
            "clen=",
            "dur=",
        )

        if any(path.endswith(x) for x in video_exts):
            return True

        if any(x in u for x in video_words):
            return True

        accept = self._header_value(headers, "accept").lower()
        sec_dest = self._header_value(headers, "sec-fetch-dest").lower()

        if "video/" in accept or "audio/" in accept:
            return True

        if sec_dest in ("video", "audio"):
            return True

        return False

    def _log_video_traffic_debug(self, method: str, url: str, headers: dict | None, stage: str):
        try:
            is_googlevideo_post = (
                method.upper() == "POST"
                and ("googlevideo" in str(url).lower() or "videoplayback" in str(url).lower())
            )

            if method.upper() != "GET" and not is_googlevideo_post:
                return

            if not self._is_video_like_traffic(url, headers) and not is_googlevideo_post:
                return

            range_header = self._header_value(headers, "range") or "-"
            accept = self._header_value(headers, "accept") or "-"
            sec_dest = self._header_value(headers, "sec-fetch-dest") or "-"
            host = urlparse(url).hostname or "-"

            note = ""
            if method.upper() == "POST" and ("googlevideo" in str(url).lower() or "videoplayback" in str(url).lower()):
                note = " SABR/Generic POST video passthrough - no Range/Manifest prefetch"

            log.info(
                "VIDEO TRAFFIC [%s]%s host=%s range=%s dest=%s accept=%s url=%s",
                stage,
                note,
                host,
                range_header[:80],
                sec_dest[:40],
                accept[:80],
                _safe_log_url(url, 160),
            )
        except Exception as exc:
            log.debug("video traffic debug failed: %s", exc)
    # --- VIDEO_TRAFFIC_DEBUG_END ---

    # --- MANIFEST_PREFETCH_METHODS_START ---
    def _is_hls_manifest_url(self, url: str) -> bool:
        return str(url or "").split("?", 1)[0].lower().endswith(".m3u8")

    def _is_manifest_segment_url(self, url: str) -> bool:
        path = str(url or "").split("?", 1)[0].lower()
        return path.endswith((".ts", ".m4s", ".mp4", ".aac", ".mp3", ".webvtt", ".vtt"))

    @staticmethod
    def _remove_range_header(headers: dict | None) -> dict:
        out = {}
        for k, v in dict(headers or {}).items():
            if k.lower() != "range":
                out[k] = v
        return out

    def _parse_hls_manifest_segments(self, manifest_url: str, body: bytes) -> list[str]:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            return []

        if "#EXTM3U" not in text:
            return []

        segments = []
        seen = set()

        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            full = urljoin(manifest_url, line)
            path = full.split("?", 1)[0].lower()

            if not path.endswith((".ts", ".m4s", ".mp4", ".aac", ".mp3", ".webvtt", ".vtt", ".m3u8")):
                continue

            if full not in seen:
                seen.add(full)
                segments.append(full)

        return segments

    async def _maybe_serve_manifest_prefetch_cache(
        self,
        method: str,
        url: str,
        headers: dict | None,
        writer,
    ) -> bool:
        if not getattr(self, "_manifest_prefetch_enabled", False):
            return False
        if method.upper() != "GET":
            return False
        if not self._is_manifest_segment_url(url):
            return False

        cached = await self._manifest_cache.get(url)
        if not cached:
            return False

        log.info("HLS PREFETCH HIT -> %s", _safe_log_url(url, 140))
        writer.write(cached)
        await writer.drain()

        try:
            stats.add_video_prefetch("hit", len(cached))
            stats.add_proxy_response((urlparse(url).hostname or url), len(cached))
        except Exception:
            pass

        self._schedule_manifest_followup_prefetch(method, url, headers)
        return True

    def _handle_manifest_verified_prefetch(
        self,
        method: str,
        url: str,
        headers: dict | None,
        response: bytes | None,
    ):
        if not getattr(self, "_manifest_prefetch_enabled", False):
            return
        if method.upper() != "GET" or not response:
            return

        if self._is_hls_manifest_url(url):
            try:
                status, resp_headers, body = self.fronter._split_raw_response(response)
            except Exception:
                return

            if status < 200 or status >= 300 or not body:
                return

            segments = self._parse_hls_manifest_segments(url, body)
            if not segments:
                log.info("HLS MANIFEST parsed=0 -> %s", _safe_log_url(url, 140))
                return

            self._manifest_segments[url] = segments
            for seg in segments:
                self._segment_to_manifest[seg] = url

            log.info("HLS MANIFEST parsed=%d -> %s", len(segments), _safe_log_url(url, 140))

            self._schedule_manifest_segment_list_prefetch(method, segments[:self._manifest_prefetch_next_segments], headers)
            return

        if self._is_manifest_segment_url(url):
            self._schedule_manifest_followup_prefetch(method, url, headers)

    def _schedule_manifest_followup_prefetch(self, method: str, segment_url: str, headers: dict | None):
        manifest_url = self._segment_to_manifest.get(segment_url)
        if not manifest_url:
            return

        segments = self._manifest_segments.get(manifest_url) or []
        try:
            idx = segments.index(segment_url)
        except ValueError:
            return

        start = idx + 1
        end = start + max(0, int(self._manifest_prefetch_next_segments or 0))
        self._schedule_manifest_segment_list_prefetch(method, segments[start:end], headers)

    def _schedule_manifest_segment_list_prefetch(self, method: str, segment_urls: list[str], headers: dict | None):
        if not segment_urls:
            return

        task = asyncio.create_task(self._prefetch_verified_manifest_segments(method, segment_urls, headers))
        self._manifest_prefetch_tasks.add(task)
        task.add_done_callback(self._manifest_prefetch_tasks.discard)

    async def _prefetch_verified_manifest_segments(self, method: str, segment_urls: list[str], headers: dict | None):
        for seg_url in segment_urls:
            if not seg_url:
                continue
            if seg_url in self._manifest_prefetch_inflight:
                continue
            if await self._manifest_cache.get(seg_url):
                continue

            self._manifest_prefetch_inflight.add(seg_url)
            try:
                async with self._manifest_prefetch_sem:
                    try:
                        stats.add_video_prefetch("request")
                        stats.add_video_prefetch("active_start")
                    except Exception:
                        pass

                    pf_headers = self._remove_range_header(headers)
                    log.info("HLS PREFETCH -> %s", _safe_log_url(seg_url, 140))
                    raw = await self._relay_video_priority(method, seg_url, pf_headers, b"")

                    if raw and (
                        raw.startswith(b"HTTP/1.1 200") or
                        raw.startswith(b"HTTP/1.0 200") or
                        raw.startswith(b"HTTP/1.1 206") or
                        raw.startswith(b"HTTP/1.0 206")
                    ):
                        await self._manifest_cache.put(seg_url, raw)
                        try:
                            stats.add_video_prefetch("bytes", len(raw))
                        except Exception:
                            pass
                    else:
                        try:
                            stats.add_video_prefetch("error")
                        except Exception:
                            pass

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("HLS PREFETCH error (%s): %s", seg_url[:80], exc)
                try:
                    stats.add_video_prefetch("error")
                except Exception:
                    pass
            finally:
                self._manifest_prefetch_inflight.discard(seg_url)
                try:
                    stats.add_video_prefetch("active_done")
                except Exception:
                    pass
    # --- MANIFEST_PREFETCH_METHODS_END ---


    # --- DIRECT_VIDEO_FALLBACK_START ---
    @staticmethod
    def _raw_response_status(raw: bytes) -> int:
        try:
            first = raw.split(b"\r\n", 1)[0].decode(errors="replace")
            m = re.search(r"\s(\d{3})\s", first)
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    def _is_googlevideo_url(self, url: str) -> bool:
        h = (urlparse(url).hostname or "").lower()
        u = str(url or "").lower()
        return h.endswith(".googlevideo.com") or "googlevideo.com" in h or "videoplayback" in u

    async def _direct_https_origin_request(
        self,
        method: str,
        url: str,
        headers: dict | None,
        body: bytes,
        timeout: float = 35.0,
    ) -> bytes:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host or parsed.scheme.lower() != "https":
            raise RuntimeError("direct fallback supports https URLs only")

        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        ssl_ctx = ssl.create_default_context()
        if certifi is not None:
            try:
                ssl_ctx.load_verify_locations(cafile=certifi.where())
            except Exception:
                pass
        if not self.fronter.verify_ssl:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx, server_hostname=host),
            timeout=min(timeout, self._tcp_connect_timeout),
        )

        try:
            clean_headers = {}
            for k, v in dict(headers or {}).items():
                lk = str(k).lower()
                if lk in {
                    "proxy-connection", "connection", "keep-alive",
                    "transfer-encoding", "host", "content-length",
                    "accept-encoding",
                }:
                    continue
                clean_headers[str(k)] = str(v)

            clean_headers["Host"] = host
            clean_headers["Connection"] = "close"
            clean_headers["Accept-Encoding"] = "identity"
            if body:
                clean_headers["Content-Length"] = str(len(body))

            req = f"{method.upper()} {path} HTTP/1.1\r\n"
            for k, v in clean_headers.items():
                req += f"{k}: {v}\r\n"
            req += "\r\n"

            writer.write(req.encode("latin-1", errors="replace") + (body or b""))
            await writer.drain()

            raw = b""
            max_bytes = int(getattr(self.fronter, "_max_response_body_bytes", 209715200))
            while True:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=timeout)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > max_bytes:
                    raise RuntimeError(f"direct fallback response too large: {len(raw)}")

            if not raw:
                raise RuntimeError("empty direct fallback response")

            return raw

        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    # --- DIRECT_VIDEO_FALLBACK_END ---


    # --- VIDEO_PRIORITY_RETRY_PATCH_START ---
    def _video_retry_status(self, raw: bytes) -> bool:
        status = self._raw_response_status(raw or b"")
        return status in (429, 500, 502, 503, 504)

    def _rotate_script_ids_once(self):
        try:
            ids = getattr(self.fronter, "_script_ids", None)
            if isinstance(ids, list) and len(ids) > 1:
                ids.append(ids.pop(0))
                return True
        except Exception:
            pass
        return False

    async def _relay_video_priority(
        self,
        method,
        url,
        headers,
        body,
        timeout_override: float | None = None,
        parallel_override: int | None = None,
    ):
        """
        Video priority relay.

        Stability changes:
        - uses a lock while temporarily overriding fronter runtime fields
        - restores previous values reliably
        - supports timeout_override for SABR/passthrough without separate global mutation
        """
        attempts = self._cfg_int(
            getattr(self, "_runtime_config_ref", {}) or {},
            "video_priority_retry_attempts",
            3,
            minimum=1,
        )
        delay_ms = self._cfg_int(
            getattr(self, "_runtime_config_ref", {}) or {},
            "video_priority_retry_delay_ms",
            120,
            minimum=0,
        )
        wanted_parallel = parallel_override or self._cfg_int(
            getattr(self, "_runtime_config_ref", {}) or {},
            "video_priority_parallel_relay",
            6,
            minimum=1,
        )

        lock = getattr(self, "_fronter_override_lock", None)
        if lock is None:
            self._fronter_override_lock = asyncio.Lock()
            lock = self._fronter_override_lock

        last_response = b""
        last_exc = None

        async with lock:
            old_parallel = None
            old_timeout = None
            changed_parallel = False
            changed_timeout = False

            try:
                if hasattr(self.fronter, "_parallel_relay"):
                    old_parallel = self.fronter._parallel_relay
                    script_count = len(getattr(self.fronter, "_script_ids", []) or [])
                    if script_count > 0:
                        wanted_parallel = min(int(wanted_parallel), script_count)
                    wanted_parallel = max(1, int(wanted_parallel))

                    if wanted_parallel > int(old_parallel or 1):
                        self.fronter._parallel_relay = wanted_parallel
                        changed_parallel = True

                if timeout_override is not None and hasattr(self.fronter, "_relay_timeout"):
                    old_timeout = self.fronter._relay_timeout
                    wanted_timeout = max(float(old_timeout or 0), float(timeout_override))
                    if wanted_timeout != float(old_timeout or 0):
                        self.fronter._relay_timeout = wanted_timeout
                        changed_timeout = True

                for attempt in range(1, attempts + 1):
                    if attempt > 1:
                        rotated = self._rotate_script_ids_once()
                        log.warning(
                            "VIDEO PRIORITY RETRY -> attempt=%s/%s rotated_script=%s url=%s",
                            attempt,
                            attempts,
                            rotated,
                            _safe_log_url(url, 140),
                        )

                    try:
                        response = await self.fronter.relay(method, url, headers, body)
                        last_response = response or b""

                        if last_response and not self._video_retry_status(last_response):
                            return last_response

                        status = self._raw_response_status(last_response)
                        log.warning(
                            "VIDEO PRIORITY bad status=%s attempt=%s/%s url=%s",
                            status,
                            attempt,
                            attempts,
                            _safe_log_url(url, 140),
                        )

                    except Exception as exc:
                        last_exc = exc
                        log.warning(
                            "VIDEO PRIORITY exception attempt=%s/%s url=%s error=%s",
                            attempt,
                            attempts,
                            _safe_log_url(url, 120),
                            exc,
                        )

                    if attempt < attempts and delay_ms > 0:
                        await asyncio.sleep(delay_ms / 1000.0)

                if last_response:
                    return last_response

                err = f"Video priority relay failed: {last_exc}".encode()
                return (
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(err)).encode() + b"\r\n"
                    b"\r\n" + err
                )

            finally:
                if changed_parallel:
                    try:
                        self.fronter._parallel_relay = old_parallel
                    except Exception:
                        pass

                if changed_timeout:
                    try:
                        self.fronter._relay_timeout = old_timeout
                    except Exception:
                        pass

    # --- VIDEO_PRIORITY_RETRY_PATCH_END ---


    # --- SABR_PASSTHROUGH_METHODS_START ---
    # --- YOUTUBE_SABR_BOOSTER_METHODS_START ---
    def _is_youtube_sabr_url(self, method: str, url: str, headers: dict | None) -> bool:
        if not getattr(self, "_youtube_sabr_booster_enabled", True):
            return False

        if str(method or "").upper() != "POST":
            return False

        u = str(url or "").lower()
        host = (urlparse(url).hostname or "").lower()

        if "generate_204" in u:
            return False

        return (
            "googlevideo.com" in host
            and "videoplayback" in u
            and ("sabr=1" in u or "rqh=1" in u)
        )

    async def _relay_youtube_sabr_boosted(self, method, url, headers, body):
        attempts = max(1, int(getattr(self, "_youtube_sabr_retry_attempts", 2) or 2))
        delay_ms = max(0, int(getattr(self, "_youtube_sabr_retry_delay_ms", 120) or 0))

        last_response = b""
        last_exc = None

        async with self._youtube_sabr_sem:
            for attempt in range(1, attempts + 1):
                try:
                    log.info(
                        "SABR BOOST -> attempt=%s/%s parallel=%s url=%s",
                        attempt,
                        attempts,
                        getattr(self, "_youtube_sabr_max_parallel", 2),
                        _safe_log_url(url, 150),
                    )

                    response = await self._relay_video_priority(
                        method,
                        url,
                        self._video_passthrough_headers(headers),
                        body or b"",
                        timeout_override=float(getattr(self, "_youtube_sabr_timeout", 180.0)),
                        parallel_override=int(getattr(self, "_youtube_sabr_max_parallel", 2) or 2),
                    )

                    last_response = response or b""
                    status = self._raw_response_status(last_response)

                    if status and status not in (429, 500, 502, 503, 504):
                        log.info("SABR BOOST OK -> status=%s url=%s", status, _safe_log_url(url, 130))
                        return last_response

                    if last_response and status in (200, 204, 206):
                        log.info("SABR BOOST OK -> status=%s url=%s", status, _safe_log_url(url, 130))
                        return last_response

                    log.warning(
                        "SABR BOOST bad status=%s attempt=%s/%s url=%s",
                        status,
                        attempt,
                        attempts,
                        _safe_log_url(url, 140),
                    )

                except Exception as exc:
                    last_exc = exc
                    log.warning(
                        "SABR BOOST exception attempt=%s/%s error=%s url=%s",
                        attempt,
                        attempts,
                        exc,
                        _safe_log_url(url, 140),
                    )

                if attempt < attempts and delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

        if last_response:
            return last_response

        err = f"YouTube SABR booster failed: {last_exc}".encode()
        return (
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(err)).encode() + b"\r\n"
                    b"\r\n" + err
                )

    # --- YOUTUBE_SABR_BOOSTER_METHODS_END ---


    def _is_sabr_or_post_video(self, method: str, url: str, headers: dict | None) -> bool:
        u = str(url or "").lower()

        if "generate_204" in u:
            return False

        if method.upper() != "POST":
            return False

        content_type = self._header_value(headers, "content-type").lower()
        accept = self._header_value(headers, "accept").lower()
        sec_dest = self._header_value(headers, "sec-fetch-dest").lower()

        # Keep this strict. If it is too broad, normal Google/YouTube API
        # calls like chrome-sync, stats/playback, and log_event get mislabeled
        # as VIDEO PASSTHROUGH and can break pages.
        strong_markers = (
            "googlevideo",
            "videoplayback",
            "sabr=1",
            "rqh=1",
            ".m4s",
            ".mp4",
            ".webm",
            ".ts",
            ".m3u8",
            ".mpd",
        )

        header_markers = (
            "video/",
            "audio/",
            "application/octet-stream",
            "application/x-mpegurl",
            "application/vnd.apple.mpegurl",
            "application/dash+xml",
        )

        if any(x in u for x in strong_markers):
            return True

        if sec_dest in ("video", "audio", "media"):
            return True

        if any(x in accept for x in header_markers):
            return True

        if any(x in content_type for x in header_markers):
            return True

        if self._is_video_like_traffic(url, headers):
            return True

        return False

    def _is_video_passthrough_candidate(self, method: str, url: str, headers: dict | None, body: bytes) -> bool:
        if not getattr(self, "_video_passthrough_enabled", True):
            return False

        m = method.upper()
        u = str(url or "").lower()

        if "generate_204" in u:
            return False

        # Range has its own Range Prefetch path.
        if self._header_value(headers, "range"):
            return False

        # Manifest/segments have Manifest Verified Prefetch path.
        try:
            if self._is_hls_manifest_url(url) or self._is_manifest_segment_url(url):
                return False
        except Exception:
            pass

        if self._is_sabr_or_post_video(method, url, headers):
            return _strict_real_video_url(url, headers)

        # For non-SABR video-like GETs without Range/Manifest, passthrough is safe.
        if m == "GET" and self._is_video_like_traffic(url, headers):
            return _strict_real_video_url(url, headers)

        return False

    @staticmethod
    def _video_passthrough_headers(headers: dict | None) -> dict:
        out = {}
        for k, v in dict(headers or {}).items():
            lk = k.lower()
            if lk in (
                "proxy-connection",
                "connection",
                "keep-alive",
                "transfer-encoding",
            ):
                continue
            out[k] = v
        return out

    async def _maybe_video_passthrough(self, method: str, url: str, headers: dict | None, body: bytes, writer, origin: str = "") -> bool:
        if not self._is_video_passthrough_candidate(method, url, headers, body):
            return False

        host = urlparse(url).hostname or "-"
        sabr_note = "SABR/POST" if self._is_sabr_or_post_video(method, url, headers) else "no-range-video"

        log.info(
            "VIDEO PASSTHROUGH -> type=%s method=%s host=%s url=%s",
            sabr_note,
            method.upper(),
            host,
            _safe_log_url(url, 160),
        )

        try:
            response = b""
            try:
                if self._is_youtube_sabr_url(method, url, headers):
                    response = await self._relay_youtube_sabr_boosted(method, url, headers, body)
                else:
                    response = await self._relay_video_priority(
                        method,
                        url,
                        self._video_passthrough_headers(headers),
                        body,
                        timeout_override=float(getattr(self, "_video_passthrough_relay_timeout", 120.0)),
                    )
            except Exception as exc:
                log.error("VIDEO PASSTHROUGH relay exception (%s): %s", _safe_log_url(url, 100), exc)

            status = self._raw_response_status(response)

            # FORCE-GAS: never use direct HTTPS fallback for YouTube/googlevideo.
            # If Apps Script returns 502, keep the 502 so no traffic leaks directly.
            if (
                status == 502
                and method.upper() == "POST"
                and self._is_googlevideo_url(url)
            ):
                log.warning(
                    "VIDEO SABR relay returned 502; direct fallback disabled by FORCE-GAS host=%s url=%s",
                    host,
                    _safe_log_url(url, 160),
                )

            if not response:
                err_body = b"Video passthrough failed: empty response"
                response = (
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(err_body)).encode() + b"\r\n"
                    b"\r\n" + err_body
                )

            if origin and response:
                response = self._inject_cors_headers(response, origin)

            self._log_response_summary(url, response)

            writer.write(response)
            await writer.drain()

            try:
                stats.add_proxy_response((urlparse(url).hostname or url), len(response or b""))
            except Exception:
                pass

            return True

        finally:
            pass

    # --- SABR_PASSTHROUGH_METHODS_END ---



    # PATCH_TELEGRAM_FAST_HELPERS
    _TELEGRAM_CIDRS = tuple(ipaddress.ip_network(x) for x in (
        "91.108.4.0/22",
        "91.108.8.0/22",
        "91.108.12.0/22",
        "91.108.16.0/22",
        "91.108.20.0/22",
        "91.108.56.0/22",
        "91.105.192.0/23",
        "149.154.160.0/20",
        "185.76.151.0/24",
        "2001:b28:f23d::/48",
        "2001:b28:f23f::/48",
        "2001:67c:4e8::/48",
        "2001:b28:f23c::/48",
        "2a0a:f280::/32",
    ))

    def _is_telegram_ip(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(str(host or "").strip("[]"))
            return any(ip in net for net in self._TELEGRAM_CIDRS)
        except Exception:
            return False

    # PATCH_TELEGRAM_TURBO_HELPERS
    @staticmethod
    def _query_has(url: str, *names: str) -> bool:
        try:
            q = parse_qs(urlparse(url).query)
            return any(name in q for name in names)
        except Exception:
            return False

    def _is_telegram_traffic(self, url: str, host: str = "") -> bool:
        h = (host or urlparse(url).hostname or "").lower().rstrip(".")
        u = str(url or "").lower()

        if h in {"telegram.org", "telegram.me", "t.me"}:
            return True
        if h.endswith(".telegram.org") or h.endswith(".telegram.me") or h.endswith(".t.me"):
            return True
        if "telegram" in h or "telegram" in u:
            return True
        if self._is_telegram_ip(h):
            return True
        return False

    def _is_turbo_allowed(self, method: str, url: str, headers: dict | None, body: bytes) -> bool:
        if not self._turbo_enabled:
            return False
        if self._turbo_skip_download_mode and str(getattr(self, "_runtime_mode", "")).lower() == "download":
            return False
        if method.upper() not in {"GET", "POST"}:
            return False
        if self._is_likely_download(url, headers or {}):
            return False
        return True

    async def _apply_turbo_mode(self, method: str, url: str, headers: dict | None, body: bytes):
        headers = dict(headers or {})
        body = body or b""

        if not self._is_turbo_allowed(method, url, headers, body):
            return headers, body

        try:
            stats.add_turbo("request")
        except Exception:
            pass

        # Aggressive Turbo:
        # Older logic added padding/headers, which caused HTTP 431 on some sites.
        # This version does not add large headers or fake request bodies.
        # If force_no_delay is enabled, delay/coalescing is completely skipped.
        if not getattr(self, "_turbo_force_no_delay", True):
            if self._turbo_coalesce_window_ms > 0:
                try:
                    await asyncio.sleep(self._turbo_coalesce_window_ms / 1000.0)
                    stats.add_turbo("delayed")
                except Exception:
                    pass

        return headers, body


    async def _apply_telegram_mode(self, method: str, url: str, headers: dict | None, body: bytes):
        if not self._telegram_mode_enabled:
            return

        host = (urlparse(url).hostname or "").lower()
        if not self._is_telegram_traffic(url, host):
            return

        try:
            stats.add_telegram("request", len(body or b""))
        except Exception:
            pass

        # Telegram Fast Mode:
        # No delay, cache, serial lock, or coalescing is applied.
        # The goal is only to detect Telegram correctly and avoid slowing down MTProto/HTTP API.
        log.info("TELEGRAM FAST -> method=%s host=%s url=%s", method, host or "-", _safe_log_url(url, 140))



    async def _relay_smart_inner(self, method, url, headers, body):
        """Choose optimal relay strategy based on request type.

        - GET requests for likely-large downloads use parallel-range.
        - All other requests (API calls, HTML, JSON, XHR) go through the
          single-request relay. This avoids injecting a synthetic Range
          header on normal traffic, which some origins honor by returning
          206 — breaking fetch()/XHR on sites like x.com or Cloudflare
          challenge pages.
        """
        if method == "GET" and not body:
            # Respect client's own Range header verbatim.
            if headers:
                for k in headers:
                    if k.lower() == "range":
                        return await self.fronter.relay(
                            method, url, headers, body
                        )
            # Only probe with Range when the URL looks like a big file.
            if self._is_likely_download(url, headers):
                return await self.fronter.relay_parallel(
                    method,
                    url,
                    headers,
                    body,
                    chunk_size=self._download_chunk_size,
                    max_parallel=self._download_max_parallel,
                    max_chunks=self._download_max_chunks,
                    min_size=self._download_min_size,
                )
        return await self.fronter.relay(method, url, headers, body)


    async def _relay_turbo(self, method, url, headers, body):
        """
        Turbo relay wrapper.

        تهاجمی‌تر از حالت معمول:
        - parallel_relay را موقتاً بالا می‌برد.
        - padding/header بزرگ اضافه نمی‌کند، پس ریسک HTTP 431 ندارد.
        - بعد از پایان request مقدار قبلی را برمی‌گرداند.
        """
        old_parallel = None
        changed = False

        try:
            if hasattr(self.fronter, "_parallel_relay"):
                old_parallel = self.fronter._parallel_relay
                script_count = len(getattr(self.fronter, "_script_ids", []) or [])
                wanted = int(getattr(self, "_turbo_parallel_relay", 3) or 3)

                if script_count > 0:
                    wanted = max(1, min(wanted, script_count))
                else:
                    wanted = max(1, wanted)

                if wanted > int(old_parallel or 1):
                    self.fronter._parallel_relay = wanted
                    changed = True

            return await self._relay_smart_inner(method, url, headers, body)

        finally:
            if changed:
                try:
                    self.fronter._parallel_relay = old_parallel
                except Exception:
                    pass


    # PATCH_TELEGRAM_TURBO_RELAY_WRAPPER
    async def _relay_smart(self, method, url, headers, body):
        method = str(method or "GET").upper()
        headers = dict(headers or {})
        body = body or b""

        host = (urlparse(url).hostname or "").lower()
        is_tg = self._telegram_mode_enabled and self._is_telegram_traffic(url, host)

        if is_tg:
            await self._apply_telegram_mode(method, url, headers, body)
            return await self._relay_smart_inner(method, url, headers, body)

        turbo_active = self._is_turbo_allowed(method, url, headers, body)
        headers, body = await self._apply_turbo_mode(method, url, headers, body)

        if turbo_active:
            return await self._relay_turbo(method, url, headers, body)

        return await self._relay_smart_inner(method, url, headers, body)




    def _cancel_background_task_set(self, attr: str, inflight_attr: str | None = None, label: str = "background"):
        """
        Cancel currently scheduled background prefetch tasks when a feature is turned off.
        This makes dashboard toggles take effect immediately instead of waiting for old tasks.
        """
        tasks = list(getattr(self, attr, set()) or [])
        if not tasks:
            if inflight_attr:
                try:
                    getattr(self, inflight_attr).clear()
                except Exception:
                    pass
            return

        loop = getattr(self, "_loop", None)
        for task in tasks:
            try:
                if task.done():
                    continue
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(task.cancel)
                else:
                    task.cancel()
            except Exception:
                pass

        try:
            getattr(self, attr).clear()
        except Exception:
            pass

        if inflight_attr:
            try:
                getattr(self, inflight_attr).clear()
            except Exception:
                pass

        log.info("Cancelled %d %s tasks after feature toggle", len(tasks), label)


    def export_runtime_state(self):
        """Return effective live runtime values actually used by ProxyServer."""
        try:
            parallel_relay = getattr(self.fronter, "_parallel_relay", None)
        except Exception:
            parallel_relay = None

        try:
            relay_timeout = getattr(self.fronter, "_relay_timeout", None)
        except Exception:
            relay_timeout = None

        try:
            max_body = getattr(self.fronter, "_max_response_body_bytes", None)
        except Exception:
            max_body = None

        return {
            "runtime_mode": getattr(self, "_runtime_mode", ""),
            "listen_host": getattr(self, "host", "-"),
            "listen_port": getattr(self, "port", "-"),
            "socks5_enabled": bool(getattr(self, "socks_enabled", False)),
            "socks5_port": getattr(self, "socks_port", "-"),

            "relay_timeout": relay_timeout,
            "parallel_relay": parallel_relay,
            "max_response_body_bytes": max_body,

            "h2_connections": len(getattr(self.fronter, "_h2_pool", []) or []),
            "h2_live_connections": sum(
                1 for h in (getattr(self.fronter, "_h2_pool", []) or [])
                if getattr(h, "_connected", False)
            ),
            "h2_available": any(
                getattr(h, "_connected", False)
                for h in (getattr(self.fronter, "_h2_pool", []) or [])
            ),
            "h2_disabled_until": getattr(self.fronter, "_h2_disabled_until", 0),

            "chunked_download_min_size": getattr(self, "_download_min_size", 0),
            "chunked_download_chunk_size": getattr(self, "_download_chunk_size", 0),
            "chunked_download_max_parallel": getattr(self, "_download_max_parallel", 0),
            "chunked_download_max_chunks": getattr(self, "_download_max_chunks", 0),
            "chunked_download_any_extension": bool(getattr(self, "_download_any_extension", False)),
            "chunked_download_extensions": list(getattr(self, "_download_extensions", [])),

            "video_prefetch_enabled": bool(getattr(self, "_video_prefetch_enabled", False)),
            "video_prefetch_next_ranges": getattr(self, "_video_prefetch_count", 0),
            "video_prefetch_parallel": getattr(self, "_video_prefetch_parallel", 0),
            "video_prefetch_chunk_size": getattr(self, "_video_prefetch_chunk_size", 0),

            "manifest_prefetch_enabled": bool(getattr(self, "_manifest_prefetch_enabled", False)),
            "manifest_prefetch_next_segments": getattr(self, "_manifest_prefetch_next_segments", 0),
            "manifest_prefetch_parallel": getattr(self, "_manifest_prefetch_parallel", 0),

            "video_passthrough_enabled": bool(getattr(self, "_video_passthrough_enabled", False)),
            "video_passthrough_relay_timeout": getattr(self, "_video_passthrough_relay_timeout", 0),

            "youtube_sabr_booster_enabled": bool(getattr(self, "_youtube_sabr_booster_enabled", False)),
            "youtube_sabr_timeout": getattr(self, "_youtube_sabr_timeout", 0),
            "youtube_sabr_max_parallel": getattr(self, "_youtube_sabr_max_parallel", 0),
            "youtube_sabr_retry_attempts": getattr(self, "_youtube_sabr_retry_attempts", 0),
            "youtube_sabr_retry_delay_ms": getattr(self, "_youtube_sabr_retry_delay_ms", 0),


            "telegram_mode_enabled": bool(getattr(self, "_telegram_mode_enabled", False)),
            "telegram_parallel_relay": getattr(self, "_telegram_parallel_relay", 0),

            "turbo_mode_enabled": bool(getattr(self, "_turbo_enabled", False)),
            "turbo_parallel_relay": getattr(self, "_turbo_parallel_relay", 0),
            "turbo_force_no_delay": bool(getattr(self, "_turbo_force_no_delay", False)),
            "turbo_skip_download_mode": bool(getattr(self, "_turbo_skip_download_mode", False)),
            "turbo_coalesce_window_ms": getattr(self, "_turbo_coalesce_window_ms", 0),
            "turbo_min_upload_padding": getattr(self, "_turbo_min_upload_padding", 0),
        }

    def apply_runtime_config(self, config: dict):
        """Apply runtime profile changes without restarting the proxy."""
        old_video_prefetch_enabled = bool(getattr(self, "_video_prefetch_enabled", False))
        old_manifest_prefetch_enabled = bool(getattr(self, "_manifest_prefetch_enabled", False))
        self._download_min_size = self._cfg_int(
            config, "chunked_download_min_size", 5 * 1024 * 1024, minimum=0,
        )
        self._download_chunk_size = self._cfg_int(
            config, "chunked_download_chunk_size", 512 * 1024, minimum=64 * 1024,
        )
        self._download_max_parallel = self._cfg_int(
            config, "chunked_download_max_parallel", 8, minimum=1,
        )
        self._download_max_chunks = self._cfg_int(
            config, "chunked_download_max_chunks", 256, minimum=1,
        )
        self._download_extensions, self._download_any_extension = (
            self._normalize_download_extensions(
                config.get(
                    "chunked_download_extensions",
                    list(self._DOWNLOAD_DEFAULT_EXTS),
                )
            )
        )

        try:
            self.fronter._max_response_body_bytes = int(
                config.get("max_response_body_bytes", self.fronter._max_response_body_bytes)
            )
        except Exception:
            pass

        try:
            self.fronter._relay_timeout = float(
                config.get("relay_timeout", self.fronter._relay_timeout)
            )
        except Exception:
            pass

        try:
            pr = int(config.get("parallel_relay", self.fronter._parallel_relay))
            self.fronter._parallel_relay = max(1, min(pr, len(self.fronter._script_ids)))
        except Exception:
            pass

        # PATCH_RUNTIME_H2_CONNECTIONS_THREADSAFE
        try:
            if bool(config.get("h2_disabled_by_dashboard", False)):
                wanted_h2 = 0
            else:
                wanted_h2 = int(config.get("h2_connections", len(getattr(self.fronter, "_h2_pool", []) or [])))
            current_h2 = len(getattr(self.fronter, "_h2_pool", []) or [])

            if wanted_h2 != current_h2 and hasattr(self.fronter, "apply_h2_connections"):
                loop = getattr(self, "_loop", None)

                if loop and loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(
                        self.fronter.apply_h2_connections(wanted_h2),
                        loop,
                    )
                    log.info("Runtime H2 rebuild scheduled: %s -> %s", current_h2, wanted_h2)
                else:
                    log.warning("Runtime H2 rebuild skipped: proxy loop not ready")
        except Exception as exc:
            log.warning("runtime h2 apply failed: %s", exc)

        # --- VIDEO_PREFETCH_RUNTIME_START ---
        self._video_prefetch_enabled = bool(
            config.get("video_prefetch_enabled", self._video_prefetch_enabled)
        )
        self._video_prefetch_count = self._cfg_int(
            config,
            "video_prefetch_next_ranges",
            self._video_prefetch_count,
            minimum=0,
        )
        self._video_prefetch_parallel = self._cfg_int(
            config,
            "video_prefetch_parallel",
            self._video_prefetch_parallel,
            minimum=1,
        )
        self._video_prefetch_chunk_size = self._cfg_int(
            config,
            "video_prefetch_chunk_size",
            self._video_prefetch_chunk_size,
            minimum=128 * 1024,
        )
        self._video_prefetch_sem = asyncio.Semaphore(self._video_prefetch_parallel)
        if old_video_prefetch_enabled and not self._video_prefetch_enabled:
            self._cancel_background_task_set(
                "_video_prefetch_tasks",
                "_video_prefetch_inflight",
                "video prefetch",
            )
        # --- VIDEO_PREFETCH_RUNTIME_END ---



        # --- MANIFEST_PREFETCH_RUNTIME ---
        self._manifest_prefetch_enabled = bool(
            config.get("manifest_prefetch_enabled", self._manifest_prefetch_enabled)
        )
        self._manifest_prefetch_next_segments = self._cfg_int(
            config,
            "manifest_prefetch_next_segments",
            self._manifest_prefetch_next_segments,
            minimum=0,
        )
        self._manifest_prefetch_parallel = self._cfg_int(
            config,
            "manifest_prefetch_parallel",
            self._manifest_prefetch_parallel,
            minimum=1,
        )
        self._manifest_prefetch_sem = asyncio.Semaphore(self._manifest_prefetch_parallel)
        if old_manifest_prefetch_enabled and not self._manifest_prefetch_enabled:
            self._cancel_background_task_set(
                "_manifest_prefetch_tasks",
                "_manifest_prefetch_inflight",
                "manifest prefetch",
            )

        # --- SABR_PASSTHROUGH_RUNTIME ---
        self._video_passthrough_enabled = bool(
            config.get("video_passthrough_enabled", self._video_passthrough_enabled)
        )
        self._video_passthrough_relay_timeout = self._cfg_float(
            config,
            "video_passthrough_relay_timeout",
            self._video_passthrough_relay_timeout,
            minimum=5.0,
        )


        # PATCH_TELEGRAM_TURBO_APPLY_RUNTIME
        # --- YOUTUBE_SABR_BOOSTER_RUNTIME_START ---
        self._youtube_sabr_booster_enabled = bool(
            config.get("youtube_sabr_booster_enabled", getattr(self, "_youtube_sabr_booster_enabled", True))
        )
        self._youtube_sabr_timeout = self._cfg_float(
            config,
            "youtube_sabr_timeout",
            getattr(self, "_youtube_sabr_timeout", 180.0),
            minimum=30.0,
        )
        self._youtube_sabr_max_parallel = self._cfg_int(
            config,
            "youtube_sabr_max_parallel",
            getattr(self, "_youtube_sabr_max_parallel", 2),
            minimum=1,
        )
        self._youtube_sabr_retry_attempts = self._cfg_int(
            config,
            "youtube_sabr_retry_attempts",
            getattr(self, "_youtube_sabr_retry_attempts", 2),
            minimum=1,
        )
        self._youtube_sabr_retry_delay_ms = self._cfg_int(
            config,
            "youtube_sabr_retry_delay_ms",
            getattr(self, "_youtube_sabr_retry_delay_ms", 120),
            minimum=0,
        )
        self._youtube_sabr_sem = asyncio.Semaphore(self._youtube_sabr_max_parallel)
        # --- YOUTUBE_SABR_BOOSTER_RUNTIME_END ---


        self._runtime_mode = str(config.get("runtime_mode", config.get("mode", ""))).lower()
        self._turbo_enabled = bool(config.get("turbo_mode_enabled", self._turbo_enabled))
        self._turbo_small_request_max = self._cfg_int(config, "turbo_small_request_max", self._turbo_small_request_max, minimum=0)
        self._turbo_min_upload_padding = self._cfg_int(config, "turbo_min_upload_padding", self._turbo_min_upload_padding, minimum=0)
        self._turbo_coalesce_window_ms = self._cfg_int(config, "turbo_coalesce_window_ms", self._turbo_coalesce_window_ms, minimum=0)
        self._turbo_skip_download_mode = bool(config.get("turbo_skip_download_mode", self._turbo_skip_download_mode))
        self._turbo_parallel_relay = self._cfg_int(config, "turbo_parallel_relay", getattr(self, "_turbo_parallel_relay", 3), minimum=1)
        self._turbo_force_no_delay = bool(config.get("turbo_force_no_delay", getattr(self, "_turbo_force_no_delay", True)))

        self._telegram_mode_enabled = bool(config.get("telegram_mode_enabled", self._telegram_mode_enabled))
        self._telegram_serial_per_host = bool(config.get("telegram_serial_per_host", self._telegram_serial_per_host))
        self._telegram_coalesce_window_ms = self._cfg_int(config, "telegram_coalesce_window_ms", self._telegram_coalesce_window_ms, minimum=0)
        self._telegram_max_delay_ms = self._cfg_int(config, "telegram_max_delay_ms", self._telegram_max_delay_ms, minimum=0)
        self._telegram_parallel_relay = self._cfg_int(config, "telegram_parallel_relay", self._telegram_parallel_relay, minimum=1)
        self._telegram_burst_limit = self._cfg_int(config, "telegram_burst_limit", getattr(self, "_telegram_burst_limit", 60), minimum=1)
        self._telegram_burst_cooldown_ms = self._cfg_int(config, "telegram_burst_cooldown_ms", getattr(self, "_telegram_burst_cooldown_ms", 60), minimum=0)
        self._telegram_hosts = tuple(
            str(x).lower().strip().lstrip(".")
            for x in config.get("telegram_hosts", getattr(self, "_telegram_hosts", []))
            if str(x).strip()
        )
        self._telegram_cidrs = tuple(str(x).strip() for x in config.get("telegram_cidrs", getattr(self, "_telegram_cidrs", [])))
        self._telegram_networks = []
        for cidr in self._telegram_cidrs:
            try:
                self._telegram_networks.append(ipaddress.ip_network(cidr, strict=False))
            except Exception:
                log.warning("Invalid telegram_cidrs entry ignored: %s", cidr)

        self._runtime_config_ref = config

        log.info(
            "Runtime config applied: mode=%s any_ext=%s min=%d chunk=%d parallel=%d max_chunks=%d cap=%s timeout=%s relay=%s",
            config.get("runtime_mode", "-"),
            self._download_any_extension,
            self._download_min_size,
            self._download_chunk_size,
            self._download_max_parallel,
            self._download_max_chunks,
            getattr(self.fronter, "_max_response_body_bytes", "-"),
            getattr(self.fronter, "_relay_timeout", "-"),
            getattr(self.fronter, "_parallel_relay", "-"),
        )

    def _is_likely_download(self, url: str, headers: dict) -> bool:
        """Heuristic: is this URL likely a large file download?"""
        path = url.split("?")[0].lower()
        accept = self._header_value(headers, "accept").lower()
        sec_dest = self._header_value(headers, "sec-fetch-dest").lower()

        # In Download mode extensions can be ["*"]. Keep that powerful mode,
        # but avoid treating normal HTML/API/navigation/static browser traffic
        # as a large file download.
        if self._download_any_extension:
            browser_doc_or_api = (
                "text/html" in accept
                or "application/json" in accept
                or "text/css" in accept
                or "javascript" in accept
                or sec_dest in {"script", "style", "font", "image"}
            )
            if browser_doc_or_api:
                return False
            return True

        for ext in self._download_extensions:
            if path.endswith(ext):
                return True

        if any(marker in accept for marker in self._DOWNLOAD_ACCEPT_MARKERS):
            return True

        return False


    # --- HLS_STREAM_DOWNLOAD_SKIP_START ---
    @staticmethod
    def _is_hls_dash_fragment_url(url: str) -> bool:
        """
        HLS/DASH manifests and tiny media fragments must not enter
        stream_parallel_download(). They are timing-sensitive and often do
        not support synthetic large Range probes correctly.
        """
        try:
            parsed = urlparse(str(url or ""))
            path = (parsed.path or "").lower()
            name = path.rsplit("/", 1)[-1]
        except Exception:
            u = str(url or "").lower().split("?", 1)[0]
            path = u
            name = u.rsplit("/", 1)[-1]

        # Manifests / playlists.
        if path.endswith((".m3u8", ".mpd")):
            return True

        # HLS/DASH fragments.
        if path.endswith((".m4s", ".ts", ".mp2t")):
            return True

        # Common fragmented MP4 init segments.
        if name.startswith("init-") and name.endswith(".mp4"):
            return True

        # Some CDNs use seg-*.mp4 for fragmented chunks.
        if name.startswith("seg-") and name.endswith((".mp4", ".m4s", ".ts")):
            return True

        # Common DASH/HLS naming variants.
        if "segment" in name and name.endswith((".mp4", ".m4s", ".ts")):
            return True

        return False
    # --- HLS_STREAM_DOWNLOAD_SKIP_END ---

    @staticmethod
    def _is_hls_stream_object_url(url: str) -> bool:
        """True for HLS manifest/segment objects that should not use range-probe streaming."""
        try:
            path = urlparse(str(url or "")).path.lower()
        except Exception:
            path = str(url or "").split("?", 1)[0].lower()

        name = path.rsplit("/", 1)[-1]

        if path.endswith((".m3u8", ".m4s", ".ts", ".mp2t")):
            return True

        if name.startswith("init-") and name.endswith(".mp4"):
            return True

        if name.startswith("seg-") and name.endswith((".mp4", ".m4v")):
            return True

        return False

    async def _maybe_stream_download(self, method: str, url: str,
                                     headers: dict | None, body: bytes,
                                     writer) -> bool:
        if method.upper() != "GET" or body:
            return False

        if headers:
            for key in headers:
                if key.lower() == "range":
                    return False

        effective_headers = headers or {}

        # HLS/DASH-style playback is made of small time-based objects.
        # Range-probe streaming these files can break timing and cause lag
        # during seek, even when bytes appear buffered. Let the normal HLS
        # path + manifest cache handle them instead.
        if self._is_hls_stream_object_url(url):
            return False

        if not self._is_likely_download(url, effective_headers):
            return False

        if not self.fronter.stream_download_allowed(url):
            return False

        self._log_video_traffic_debug(
            method,
            url,
            effective_headers,
            "stream_parallel_download",
        )

        return await self.fronter.stream_parallel_download(
            url,
            effective_headers,
            writer,
            chunk_size=self._download_chunk_size,
            max_parallel=self._download_max_parallel,
            max_chunks=self._download_max_chunks,
            min_size=self._download_min_size,
        )


    # ── Plain HTTP forwarding ─────────────────────────────────────

    async def _do_http(self, header_block: bytes, reader, writer):
        body = b""
        if _has_unsupported_transfer_encoding(header_block):
            log.warning("Unsupported Transfer-Encoding on plain HTTP request")
            writer.write(
                b"HTTP/1.1 501 Not Implemented\r\n"
                b"Connection: close\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            await writer.drain()
            return
        length = _parse_content_length(header_block)
        if length > MAX_REQUEST_BODY_BYTES:
            writer.write(b"HTTP/1.1 413 Content Too Large\r\n\r\n")
            await writer.drain()
            return
        if length > 0:
            body = await reader.readexactly(length)

        first_line = header_block.split(b"\r\n")[0].decode(errors="replace")
        log.info("HTTP → %s", _safe_log_url(first_line, 180))

        # Parse request and relay through Apps Script
        parts = first_line.strip().split(" ", 2)
        method = parts[0] if parts else "GET"
        url = parts[1] if len(parts) > 1 else "/"

        try:
            stats.add_proxy_request((urlparse(url).hostname or url))
        except Exception:
            pass

        headers = {}
        for raw_line in header_block.split(b"\r\n")[1:]:
            if b":" in raw_line:
                k, v = raw_line.decode(errors="replace").split(":", 1)
                headers[k.strip()] = v.strip()

        # ── CORS preflight over plain HTTP ─────────────────────────────
        origin = self._header_value(headers, "origin")
        acr_method = self._header_value(headers, "access-control-request-method")
        acr_headers = self._header_value(headers, "access-control-request-headers")
        if method.upper() == "OPTIONS" and acr_method:
            log.debug("CORS preflight (HTTP) → %s (responding locally)", url[:60])
            _cors_resp = self._cors_preflight_response(
                origin, acr_method, acr_headers,
            )
            writer.write(_cors_resp)
            await writer.drain()
            try:
                stats.add_proxy_response((urlparse(url).hostname or url), len(_cors_resp))
            except Exception:
                pass
            return

        # --- VIDEO_TRAFFIC_BEFORE_STREAM_HTTP ---
        self._log_video_traffic_debug(method, url, headers, "before_stream_http")

        # --- SABR_PASSTHROUGH_HTTP ---
        if await self._maybe_video_passthrough(method, url, headers, body, writer, origin):
            return

        # --- MANIFEST_PREFETCH_SERVE_HTTP ---
        if await self._maybe_serve_manifest_prefetch_cache(method, url, headers, writer):
            return

        if await self._maybe_stream_download(method, url, headers, body, writer):
            return

        # --- VIDEO_PREFETCH_SERVE_HTTP_START ---
        if await self._maybe_serve_video_range_cache(method, url, headers, writer):
            return
        # --- VIDEO_PREFETCH_SERVE_HTTP_END ---

        # Cache check for GET
        response = None
        if self._cache_allowed(method, url, headers, body):
            response = self._cache.get(url)
            if response:
                log.debug("Cache HIT (HTTP): %s", url[:60])

        if response is None:
            response = await self._relay_smart(method, url, headers, body)
            # Cache successful GET
            if self._cache_allowed(method, url, headers, body) and response:
                ttl = ResponseCache.parse_ttl(response, url)
                if ttl > 0:
                    self._cache.put(url, response, ttl)

        if origin and response:
            response = self._inject_cors_headers(response, origin)

        self._log_response_summary(url, response)

        writer.write(response)
        await writer.drain()
        try:
            stats.add_proxy_response((urlparse(url).hostname or url), len(response or b""))
        except Exception:
            pass

        # --- MANIFEST_PREFETCH_AFTER_HTTP ---
        self._handle_manifest_verified_prefetch(method, url, headers, response)

        # --- VIDEO_PREFETCH_SCHEDULE_HTTP_START ---
        self._schedule_video_prefetch(method, url, headers, response)
        # --- VIDEO_PREFETCH_SCHEDULE_HTTP_END ---
