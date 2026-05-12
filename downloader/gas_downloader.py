import asyncio
import html
import os
import re
import time
import uuid
import threading
import subprocess
import sys
from urllib.parse import urlparse, unquote, parse_qs

from domain_fronter import DomainFronter
from downloader.download_state import DownloadState, ChunkState
from downloader.progress import SpeedMeter


DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def origin_from_url(url):
    u = urlparse(url)
    if not u.scheme or not u.netloc:
        return ""
    return f"{u.scheme}://{u.netloc}/"


def parse_raw_response(raw: bytes):
    if b"\r\n\r\n" not in raw:
        return 0, {}, raw
    head, body = raw.split(b"\r\n\r\n", 1)
    lines = head.decode(errors="replace").split("\r\n")
    m = re.search(r"\s(\d{3})\s", lines[0])
    status = int(m.group(1)) if m else 0
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def filename_from_url(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query or "")
    for key in ("download_filename", "filename", "file"):
        val = qs.get(key)
        if val and val[0]:
            name = unquote(val[0])
            name = os.path.basename(name)
            name = re.sub(r'[\/:*?"<>|]+', "_", name)
            if name:
                return name

    path = unquote(parsed.path or "")
    name = os.path.basename(path.rstrip("/")) or "download.bin"
    name = re.sub(r'[\/:*?"<>|]+', "_", name)
    return name


def fmt_seconds(sec):
    sec = int(max(0, sec or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _exc_detail(exc):
    """Verbose exception text for downloader logs."""
    try:
        return f"{type(exc).__name__}: {repr(exc)}"
    except Exception:
        return str(exc) or "unknown error"


def _short_url(url, n=220):
    url = str(url or "")
    return url if len(url) <= n else url[:n] + "...(truncated)"


# PATCH_DOWNLOADER_FEATURES_CORE_START
def parse_schedule_ts(value):
    """
    Accepts:
      - unix timestamp int/float/string
      - datetime-local value: YYYY-MM-DDTHH:MM
      - empty/None -> 0
    """
    if value in (None, "", "-", "None"):
        return 0.0

    try:
        return float(value)
    except Exception:
        pass

    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(raw, fmt))
        except Exception:
            continue

    return 0.0


def open_folder_path(path):
    folder = os.path.dirname(os.path.abspath(path or ""))
    if not folder or not os.path.isdir(folder):
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
# PATCH_DOWNLOADER_FEATURES_CORE_END


class DownloadJob:
    def __init__(self, config, state: DownloadState, *, chunk_size, parallel, retries):
        self.config = dict(config)
        self.state = state
        self.chunk_size = int(chunk_size)
        self.parallel = int(parallel)
        self.retries = int(retries)
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.cancelled = False
        self.speed_meter = SpeedMeter()

        # Fallback for origins/Apps Script paths that ignore Range and return
        # the full file as HTTP 200. In that case downloader stores the body
        # as a single chunk instead of failing before download starts.
        self._single_response_body = None
        self._single_response_headers = {}

        # PATCH_DOWNLOADER_FEATURES_JOB_INIT_START
        self.created_at = time.time()
        self._thread_running = False
        self._download_started_monotonic = None

        if not hasattr(self.state, "scheduled_at"):
            self.state.scheduled_at = 0.0
        if not hasattr(self.state, "priority"):
            self.state.priority = 0

        self.scheduled_at = float(getattr(self.state, "scheduled_at", 0.0) or 0.0)
        self.priority = int(getattr(self.state, "priority", 0) or 0)

        self.speed_limit_bps = int(self.config.get("downloader_speed_limit_bps", 0) or 0)
        self.auto_open_folder = bool(self.config.get("downloader_auto_open_folder", False))
        self.verify_final_size = bool(self.config.get("downloader_verify_final_size", True))
        self.auto_retry_rounds = int(self.config.get("downloader_auto_retry_rounds", 2) or 2)

        self.state.scheduled_at = self.scheduled_at
        self.state.priority = self.priority
        # PATCH_DOWNLOADER_FEATURES_JOB_INIT_END

        # Downloader H2 mode is controlled from config/UI.
        # Safe fallback: if H2 is unavailable or fails, normal relay() is used.
        self._h2_bootstrap_done = False
        self._h2_logged = False
        self._h2_fallback_logs = 0

        self.state.chunk_size = self.chunk_size
        self.state.parallel = self.parallel
        self.state.retries = self.retries

    def snapshot(self):
        speed = self.speed_meter.update(self.state.downloaded)
        pct = 0.0
        eta = "-"
        if self.state.total_size > 0:
            pct = min(100.0, (self.state.downloaded / self.state.total_size) * 100.0)
            remaining = max(0, self.state.total_size - self.state.downloaded)
            if speed > 1:
                eta = fmt_seconds(remaining / speed)

        done_chunks = sum(1 for c in self.state.chunks if c.done)
        total_chunks = len(self.state.chunks)

        return {
            "id": self.state.id,
            "url": self.state.url,
            "filename": self.state.filename,
            "path": self.state.path,
            "total_size": self.state.total_size,
            "downloaded": self.state.downloaded,
            "percent": round(pct, 2),
            "speed": speed,
            "eta": eta,
            "status": self.state.status,
            "error": self.state.error,

            # PATCH_DOWNLOADER_FEATURES_SNAPSHOT_START
            "scheduled_at": float(getattr(self.state, "scheduled_at", 0.0) or 0.0),
            "priority": int(getattr(self.state, "priority", 0) or 0),
            "speed_limit_bps": int(self.speed_limit_bps or 0),
            "auto_open_folder": bool(self.auto_open_folder),
            "verify_final_size": bool(self.verify_final_size),
            "auto_retry_rounds": int(self.auto_retry_rounds or 0),
            "thread_running": bool(self._thread_running),
            # PATCH_DOWNLOADER_FEATURES_SNAPSHOT_END

            "quota_used": self.state.quota_used,
            "relay_requests": self.state.relay_requests,
            "relay_errors": self.state.relay_errors,
            "relay_sent_bytes": self.state.relay_sent_bytes,
            "relay_received_bytes": self.state.relay_received_bytes,

            "chunk_size": self.state.chunk_size,
            "parallel": self.state.parallel,
            "retries": self.state.retries,
            "done_chunks": done_chunks,
            "total_chunks": total_chunks,

            "started_at": self.state.started_at,
            "finished_at": self.state.finished_at,
            "logs": self.state.logs[-80:],
        }

    def pause(self):
        if self.state.status in ("done", "cancelled"):
            return
        self.state.status = "paused"
        self.state.log("INFO", "Paused")
        self.pause_event.clear()
        self.state.save()

    def resume(self):
        if self.state.status in ("done", "cancelled"):
            return
        self.state.status = "downloading"
        self.state.log("INFO", "Resumed")
        self.pause_event.set()
        self.state.save()

    def cancel(self):
        self.cancelled = True
        self.pause_event.set()
        self.state.status = "cancelled"
        self.state.log("WARN", "Cancelled")
        self.state.save()

    def _request_headers(self, range_header=None):
        """
        Browser-like headers for anti-hotlink / tokenized download URLs.

        Optional config.json keys:
          downloader_user_agent
          downloader_referer
          downloader_cookie
          downloader_headers
        """
        referer = (
            self.config.get("downloader_referer")
            or origin_from_url(self.state.url)
        )

        headers = {
            "User-Agent": self.config.get("downloader_user_agent", DEFAULT_BROWSER_UA),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }

        if referer:
            headers["Referer"] = referer
            headers["Origin"] = referer.rstrip("/")

        cookie = self.config.get("downloader_cookie", "")
        if cookie:
            headers["Cookie"] = str(cookie)

        extra = self.config.get("downloader_headers", {}) or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k and v is not None:
                    headers[str(k)] = str(v)

        if range_header:
            headers["Range"] = range_header

        return headers

    def _response_hint(self, status, headers, body):
        ct = str(headers.get("content-type", "")).lower()
        sample = (body or b"")[:500].decode(errors="replace").lower()

        if status in (401, 403):
            return "Access blocked. Cookie/Referer/User-Agent is probably required."

        if status in (429,):
            return "Rate limited by origin or relay."

        if "text/html" in ct and any(x in sample for x in ("cloudflare", "captcha", "forbidden", "access denied", "hotlink")):
            return "HTML anti-bot/anti-hotlink page returned instead of file."

        return ""

    def h2_enabled(self):
        return bool(self.config.get("downloader_h2_enabled", False))

    async def _warm_h2_if_needed(self, fronter):
        if not self.h2_enabled():
            return False

        if self._h2_bootstrap_done:
            try:
                return bool(fronter._h2_available())
            except Exception:
                return False

        self._h2_bootstrap_done = True
        wait_s = float(self.config.get("downloader_h2_wait_seconds", 2.5))

        try:
            if hasattr(fronter, "_warm_pool"):
                await fronter._warm_pool()
        except Exception as exc:
            self.state.log("WARN", f"H2 warm skipped: {_exc_detail(exc)}")

        deadline = time.time() + max(0.2, wait_s)
        while time.time() < deadline:
            try:
                if hasattr(fronter, "_h2_available") and fronter._h2_available():
                    self.state.log("INFO", "H2 downloader transport ready")
                    self.state.save()
                    return True
            except Exception:
                pass

            try:
                if hasattr(fronter, "_h2_connect"):
                    await fronter._h2_connect()
            except Exception:
                pass

            await asyncio.sleep(0.15)

        self.state.log("WARN", "H2 not ready; using normal relay fallback")
        self.state.save()
        return False

    async def _relay_h2_preferred(self, fronter, method, url, headers, body=b""):
        if not self.h2_enabled():
            return await fronter.relay(method, url, headers, body)

        try:
            await self._warm_h2_if_needed(fronter)

            if (
                hasattr(fronter, "_h2_available")
                and fronter._h2_available()
                and hasattr(fronter, "_build_payload")
                and hasattr(fronter, "_relay_single_h2")
            ):
                if not self._h2_logged:
                    self.state.log("INFO", "H2 preferred relay enabled for this download")
                    self._h2_logged = True
                    self.state.save()

                payload = fronter._build_payload(method, url, headers, body)
                timeout = float(self.config.get("downloader_h2_timeout", self.config.get("relay_timeout", 45)))
                return await asyncio.wait_for(
                    fronter._relay_single_h2(payload),
                    timeout=max(1.0, timeout),
                )

        except Exception as exc:
            self._h2_fallback_logs += 1
            if self._h2_fallback_logs <= 5:
                self.state.log("WARN", f"H2 fallback to normal relay: {exc}")
                self.state.save()

        return await fronter.relay(method, url, headers, body)

    async def relay_counted(self, fronter, method, url, headers, body=b""):
        sent_guess = len(str(headers or {}).encode()) + len(body or b"")
        self.state.relay_requests += 1
        self.state.quota_used += 1
        self.state.relay_sent_bytes += sent_guess

        route = "H2-preferred" if self.h2_enabled() else "H1-forced"
        range_header = ""
        try:
            range_header = str((headers or {}).get("Range") or (headers or {}).get("range") or "")
        except Exception:
            range_header = ""

        self.state.log(
            "INFO",
            f"Relay request route={route} method={method} range={range_header or '-'} url={_short_url(url)}"
        )

        t0 = time.perf_counter()
        try:
            raw = await self._relay_h2_preferred(fronter, method, url, headers, body)
            self.state.relay_received_bytes += len(raw or b"")
            status, resp_headers, resp_body = parse_raw_response(raw or b"")
            self.state.log(
                "INFO",
                f"Relay response route={route} method={method} status={status} "
                f"rx={len(raw or b'')} body={len(resp_body or b'')} "
                f"content-type={resp_headers.get('content-type', '-')} "
                f"content-length={resp_headers.get('content-length', '-')} "
                f"content-range={resp_headers.get('content-range', '-')}"
            )
            return raw
        except Exception as exc:
            self.state.relay_errors += 1
            self.state.log(
                "ERROR",
                f"Relay failed route={route} method={method} range={range_header or '-'} "
                f"url={_short_url(url)} error={_exc_detail(exc)}"
            )
            raise
        finally:
            dt = (time.perf_counter() - t0) * 1000
            if dt > 5000:
                self.state.log("WARN", f"Slow relay request route={route} method={method}: {dt:.0f}ms")
            self.state.save()


    # PATCH_DOWNLOADER_FEATURES_JOB_METHODS_START
    async def _wait_until_schedule(self):
        ts = float(getattr(self.state, "scheduled_at", 0.0) or 0.0)
        if ts <= time.time():
            return

        self.state.status = "scheduled"
        self.state.log("INFO", f"Scheduled. Waiting until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}")
        self.state.save()

        while not self.cancelled and time.time() < ts:
            await asyncio.sleep(min(1.0, max(0.1, ts - time.time())))

    async def _throttle_speed_if_needed(self):
        limit = int(self.speed_limit_bps or 0)
        if limit <= 0:
            return

        if self._download_started_monotonic is None:
            self._download_started_monotonic = time.perf_counter()
            return

        downloaded = int(self.state.downloaded or 0)
        if downloaded <= 0:
            return

        elapsed = max(0.001, time.perf_counter() - self._download_started_monotonic)
        target_elapsed = downloaded / max(1, limit)
        delay = target_elapsed - elapsed

        if delay > 0:
            await asyncio.sleep(min(delay, 2.0))

    async def _verify_final_file(self):
        if not self.verify_final_size:
            return

        path = self.state.path
        expected = int(self.state.total_size or 0)

        if not path or not os.path.exists(path):
            raise RuntimeError("final file verification failed: file missing")

        actual = os.path.getsize(path)
        if expected > 0 and actual != expected:
            raise RuntimeError(f"final file verification failed: size mismatch {actual}/{expected}")

        self.state.log("INFO", f"Verified final file size: {actual} bytes")
        self.state.save()

    async def _auto_open_folder_if_needed(self):
        if not self.auto_open_folder:
            return

        ok, result = open_folder_path(self.state.path)
        if ok:
            self.state.log("INFO", f"Opened folder: {result}")
        else:
            self.state.log("WARN", f"Auto open folder failed: {result}")
        self.state.save()
    # PATCH_DOWNLOADER_FEATURES_JOB_METHODS_END

    async def start(self):
        fronter_config = dict(self.config)

        if not self.h2_enabled():
            fronter_config["gas_downloader_force_h1"] = True

        fronter = DomainFronter(fronter_config)

        if not self.h2_enabled():
            self.state.log("INFO", "Downloader relay route: H1 forced because downloader_h2_enabled is OFF")
        else:
            self.state.log("INFO", "Downloader relay route: H2 preferred because downloader_h2_enabled is ON")
        self.state.save()
        self.state.started_at = time.time()
        self.state.status = "starting"
        self.state.log("INFO", "Download started")
        self.state.save()

        try:
            await self._wait_until_schedule()
            if self.cancelled:
                return

            try:
                await self._probe(fronter)
            except Exception as first_exc:
                self.state.log("WARN", f"Direct probe failed, trying final URL resolver: {_exc_detail(first_exc)}")
                old_url = self.state.url
                resolved = await self._resolve_final_url(fronter)

                if not resolved or self.state.url == old_url:
                    raise first_exc

                self.state.chunks = []
                self.state.total_size = 0
                self.state.downloaded = 0
                self.state.save()

                await self._probe(fronter)
            await self._prepare_file()

            self.state.status = "downloading"
            self.state.log("INFO", f"Prepared {len(self.state.chunks)} chunks")
            self.state.save()

            # PATCH_DOWNLOADER_FEATURES_AUTO_RETRY_START
            failed_round = 0

            while True:
                if self.cancelled:
                    break

                pending = [c for c in self.state.chunks if not c.done]
                if not pending:
                    break

                sem = asyncio.Semaphore(self.parallel)

                async def worker(chunk):
                    async with sem:
                        await self._download_chunk(fronter, chunk)

                results = await asyncio.gather(*[worker(c) for c in pending], return_exceptions=True)
                errors = [x for x in results if isinstance(x, Exception)]

                still_pending = [c for c in self.state.chunks if not c.done]
                if errors and still_pending:
                    failed_round += 1
                    if failed_round > self.auto_retry_rounds:
                        raise RuntimeError(f"{len(still_pending)} chunks still failed after auto retry rounds")

                    self.state.log(
                        "WARN",
                        f"Auto retry failed chunks round {failed_round}/{self.auto_retry_rounds}: {len(still_pending)} chunks pending"
                    )
                    self.state.save()
                    await asyncio.sleep(min(5.0, 1.0 + failed_round))
                    continue

                failed_round = 0
            # PATCH_DOWNLOADER_FEATURES_AUTO_RETRY_END

            if not self.cancelled and all(c.done for c in self.state.chunks):
                if os.path.exists(self.state.path):
                    os.remove(self.state.path)
                os.replace(self.state.part_path, self.state.path)
                await self._verify_final_file()

                self.state.status = "done"
                self.state.finished_at = time.time()
                self.state.downloaded = self.state.total_size
                self.state.log("DONE", f"Completed: {self.state.filename}")
                self.state.save()

                await self._auto_open_folder_if_needed()

        except Exception as e:
            if not self.cancelled:
                self.state.status = "error"
                self.state.error = _exc_detail(e)
                self.state.log("ERROR", _exc_detail(e))
                self.state.save()
        finally:
            try:
                await fronter.close()
            except Exception:
                pass


    async def _resolve_final_url(self, fronter):
        """
        Resolve temporary / generated download URLs.

        Some sites first return a page or redirect URL, then generate
        a real CDN URL such as remote_control.php?...file=...mp4.
        This method tries to find that final media URL before chunking.
        """
        original_url = self.state.url
        self.state.log("INFO", "Resolving final media URL")
        self.state.save()

        headers = self._request_headers()

        try:
            raw = await self.relay_counted(
                fronter,
                "GET",
                original_url,
                headers,
                b"",
            )

            status, resp_headers, body = parse_raw_response(raw)

            location = (
                resp_headers.get("location")
                or resp_headers.get("Location")
                or ""
            )

            if location:
                final = html.unescape(str(location).strip())
                if final.startswith("/"):
                    u = urlparse(original_url)
                    final = f"{u.scheme}://{u.netloc}{final}"

                if self._looks_like_media_url(final):
                    self.state.url = final
                    self.state.log("INFO", f"Resolved redirect media URL: {urlparse(final).netloc}")
                    self.state.save()
                    return True

            decoded = body.decode("utf-8", errors="ignore")
            decoded = html.unescape(decoded)

            patterns = [
                r'https?://[^"\'<>\s]+remote_control\.php[^"\'<>\s]+',
                r'https?://[^"\'<>\s]+/[^"\'<>\s]+\.mp4[^"\'<>\s]*',
                r'https?://[^"\'<>\s]+/[^"\'<>\s]+\.mkv[^"\'<>\s]*',
                r'https?://[^"\'<>\s]+/[^"\'<>\s]+\.webm[^"\'<>\s]*',
                r'https?://[^"\'<>\s]+/[^"\'<>\s]+\.avi[^"\'<>\s]*',
            ]

            for pat in patterns:
                m = re.search(pat, decoded, re.I)
                if not m:
                    continue

                final = html.unescape(m.group(0)).strip()
                final = final.replace("\\/", "/")

                if self._looks_like_media_url(final):
                    self.state.url = final
                    self.state.log("INFO", f"Resolved CDN media URL: {urlparse(final).netloc}")
                    self.state.save()
                    return True

            if status in (301, 302, 303, 307, 308):
                self.state.log("WARN", f"Redirect status {status}, but no media Location found")
            else:
                self.state.log("INFO", "No separate CDN URL detected; using original URL")

            return False

        except Exception as exc:
            self.state.log("WARN", f"Final URL resolve failed: {_exc_detail(exc)}")
            self.state.save()
            return False

    def _looks_like_media_url(self, url):
        u = str(url or "").lower()
        return (
            "remote_control.php" in u
            or ".mp4" in u
            or ".mkv" in u
            or ".webm" in u
            or ".avi" in u
            or ".mov" in u
            or ".ts" in u
            or ".m3u8" in u
        )

    async def _probe(self, fronter):
        self.state.status = "probing"
        self.state.log("INFO", "Checking file size, browser headers, and range support")
        self.state.save()

        try:
            head_headers = self._request_headers()
            raw = await self.relay_counted(fronter, "HEAD", self.state.url, head_headers, b"")
            status, resp_headers, body = parse_raw_response(raw)
            if status in (200, 206):
                cl = resp_headers.get("content-length")
                cr = resp_headers.get("content-range", "")
                total = 0
                m = re.search(r"/(\d+)\s*$", cr or "")
                if m:
                    total = int(m.group(1))
                elif cl:
                    total = int(cl)
                if total > 0:
                    self.state.total_size = total
                    self.state.log("INFO", f"HEAD size detected: {total} bytes")
            else:
                hint = self._response_hint(status, resp_headers, body)
                if hint:
                    self.state.log("WARN", f"HEAD probe HTTP {status}: {hint}")
        except Exception as exc:
            self.state.log("WARN", f"HEAD probe failed: {_exc_detail(exc)}")

        headers = self._request_headers("bytes=0-0")
        raw = await self.relay_counted(fronter, "GET", self.state.url, headers, b"")
        status, resp_headers, body = parse_raw_response(raw)

        if status == 206:
            cr = resp_headers.get("content-range", "")
            m = re.search(r"/(\d+)\s*$", cr)
            if not m:
                raise RuntimeError("Content-Range total missing")
            total = int(m.group(1))
            self.state.total_size = total
            self.state.log("INFO", "Range support: ON")
        elif status == 200:
            hint = self._response_hint(status, resp_headers, body)
            if hint:
                raise RuntimeError(f"Range probe returned HTML/block page: {hint}")

            ct = str(resp_headers.get("content-type", "")).lower()
            cl = resp_headers.get("content-length")
            body_len = len(body or b"")

            if body_len <= 0:
                raise RuntimeError(
                    "Server ignored Range request and returned HTTP 200 with empty body; cannot download safely"
                )

            if "text/html" in ct:
                raise RuntimeError(
                    "Server ignored Range request and returned HTML instead of file; Cookie/Referer may be required"
                )

            total = body_len
            try:
                if cl and int(cl) > 0:
                    total = int(cl)
            except Exception:
                total = body_len

            if total != body_len:
                self.state.log(
                    "WARN",
                    f"Range ignored with HTTP 200 but Content-Length/body mismatch {total}/{body_len}; using body size"
                )
                total = body_len

            self.state.total_size = total
            self._single_response_body = body
            self._single_response_headers = dict(resp_headers or {})
            self.state.chunks = [ChunkState(index=0, start=0, end=total - 1)]
            self.state.log(
                "WARN",
                f"Range support: OFF. Server returned full file via HTTP 200; using single-shot fallback ({total} bytes, type={ct or '-'})"
            )
            self.state.save()
            return
        else:
            hint = self._response_hint(status, resp_headers, body)
            if hint:
                raise RuntimeError(f"Probe failed: HTTP {status} - {hint}")
            raise RuntimeError(f"Probe failed: HTTP {status}")

        total = self.state.total_size

        if not self.state.chunks:
            chunks = []
            start = 0
            idx = 0
            while start < total:
                end = min(start + self.chunk_size - 1, total - 1)
                chunks.append(ChunkState(index=idx, start=start, end=end))
                start = end + 1
                idx += 1
            self.state.chunks = chunks

        self.state.save()

    async def _prepare_file(self):
        os.makedirs(os.path.dirname(self.state.part_path), exist_ok=True)
        with open(self.state.part_path, "ab"):
            pass
        with open(self.state.part_path, "r+b") as f:
            f.truncate(self.state.total_size)

    async def _download_chunk(self, fronter, chunk):
        if chunk.done:
            return

        if (
            chunk.index == 0
            and self._single_response_body is not None
            and len(self.state.chunks) == 1
        ):
            body = self._single_response_body or b""
            expected = chunk.end - chunk.start + 1
            if len(body) != expected:
                raise RuntimeError(f"single-shot fallback size mismatch {len(body)}/{expected}")

            with open(self.state.part_path, "r+b") as f:
                f.seek(0)
                f.write(body)

            chunk.done = True
            chunk.bytes = expected
            chunk.error = ""
            self.state.downloaded = expected
            self.state.log("INFO", f"Single-shot fallback wrote {expected} bytes")
            self.state.save()
            return

        for attempt in range(self.retries):
            if self.cancelled:
                return

            await self.pause_event.wait()

            try:
                headers = self._request_headers(f"bytes={chunk.start}-{chunk.end}")
                raw = await self.relay_counted(fronter, "GET", self.state.url, headers, b"")
                status, resp_headers, body = parse_raw_response(raw)

                if status != 206:
                    hint = self._response_hint(status, resp_headers, body)
                    if hint:
                        raise RuntimeError(f"chunk {chunk.index}: expected HTTP 206, got {status} - {hint}")
                    raise RuntimeError(f"chunk {chunk.index}: expected HTTP 206, got {status}")

                expected = chunk.end - chunk.start + 1
                if len(body) != expected:
                    raise RuntimeError(f"chunk {chunk.index}: size mismatch {len(body)}/{expected}")

                with open(self.state.part_path, "r+b") as f:
                    f.seek(chunk.start)
                    f.write(body)

                chunk.done = True
                chunk.bytes = expected
                chunk.error = ""

                self.state.downloaded = sum(
                    (c.end - c.start + 1)
                    for c in self.state.chunks
                    if c.done
                )
                await self._throttle_speed_if_needed()
                self.state.save()
                return

            except Exception as e:
                chunk.retries += 1
                chunk.error = _exc_detail(e)
                self.state.relay_errors += 1
                self.state.error = _exc_detail(e)
                self.state.log("ERROR", f"Chunk {chunk.index} retry {attempt + 1}/{self.retries}: {_exc_detail(e)}")
                self.state.save()
                await asyncio.sleep(0.7 * (attempt + 1))

        raise RuntimeError(f"chunk {chunk.index} failed after {self.retries} retries")


class DownloadManager:
    def __init__(self, config, download_dir="downloads"):
        self.config = dict(config)
        self.download_dir = os.path.abspath(download_dir)
        self.jobs = {}
        self.lock = threading.Lock()

        # PATCH_DOWNLOADER_FEATURES_MANAGER_INIT_START
        self.max_active = int(self.config.get("downloader_max_active", 2) or 2)
        self._scheduler_stop = False
        # PATCH_DOWNLOADER_FEATURES_MANAGER_INIT_END

        self._load_existing()

        # PATCH_DOWNLOADER_FEATURES_SCHEDULER_THREAD_START
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        # PATCH_DOWNLOADER_FEATURES_SCHEDULER_THREAD_END

    def _load_existing(self):
        os.makedirs(self.download_dir, exist_ok=True)
        for state_file in os.listdir(self.download_dir):
            if not state_file.endswith(".state.json"):
                continue
            path = os.path.join(self.download_dir, state_file)
            try:
                state = DownloadState.load(path)
                job = DownloadJob(
                    self.config,
                    state,
                    chunk_size=int(self.config.get("downloader_chunk_size", 8 * 1024 * 1024)),
                    parallel=int(self.config.get("downloader_parallel", 4)),
                    retries=int(self.config.get("downloader_retries", 4)),
                )
                if state.status == "downloading":
                    state.status = "paused"
                    state.log("WARN", "Recovered after restart; paused")
                    state.save()
                self.jobs[state.id] = job
            except Exception:
                pass


    # PATCH_DOWNLOADER_FEATURES_MANAGER_METHODS_START
    def _active_count(self):
        return sum(
            1 for j in self.jobs.values()
            if getattr(j, "_thread_running", False)
            and j.state.status in ("starting", "probing", "downloading")
        )

    def _start_job(self, job):
        if getattr(job, "_thread_running", False):
            return False

        def runner():
            job._thread_running = True
            try:
                asyncio.run(job.start())
            finally:
                job._thread_running = False

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        return True

    def _scheduler_loop(self):
        while not getattr(self, "_scheduler_stop", False):
            try:
                now = time.time()
                with self.lock:
                    capacity = max(0, int(self.max_active or 1) - self._active_count())

                    candidates = []
                    for job in self.jobs.values():
                        if capacity <= 0:
                            break

                        if getattr(job, "_thread_running", False):
                            continue

                        st = str(job.state.status or "").lower()
                        scheduled_at = float(getattr(job.state, "scheduled_at", 0.0) or 0.0)

                        if st == "scheduled" and scheduled_at > now:
                            continue

                        if st in ("queued", "scheduled"):
                            candidates.append(job)

                    candidates.sort(
                        key=lambda j: (
                            -int(getattr(j.state, "priority", 0) or 0),
                            float(getattr(j.state, "scheduled_at", 0.0) or 0.0),
                            getattr(j, "created_at", 0),
                        )
                    )

                    for job in candidates[:capacity]:
                        if job.state.status == "scheduled" and float(getattr(job.state, "scheduled_at", 0.0) or 0.0) > now:
                            continue
                        self._start_job(job)
            except Exception:
                pass

            time.sleep(0.5)

    def pause_all(self):
        with self.lock:
            for job in self.jobs.values():
                job.pause()

    def resume_all(self):
        with self.lock:
            for job in self.jobs.values():
                if job.state.status in ("paused", "error", "scheduled", "queued"):
                    if float(getattr(job.state, "scheduled_at", 0.0) or 0.0) > time.time():
                        job.state.status = "scheduled"
                    else:
                        job.state.status = "queued"
                    job.pause_event.set()
                    job.state.log("INFO", "Queued by resume all")
                    job.state.save()

    def cancel_all(self):
        with self.lock:
            for job in self.jobs.values():
                job.cancel()

    def set_speed_limit(self, bps):
        bps = max(0, int(bps or 0))
        with self.lock:
            self.config["downloader_speed_limit_bps"] = bps
            for job in self.jobs.values():
                job.speed_limit_bps = bps
                job.config["downloader_speed_limit_bps"] = bps
        return bps

    def set_max_active(self, n):
        n = max(1, int(n or 1))
        with self.lock:
            self.max_active = n
            self.config["downloader_max_active"] = n
        return n

    def set_auto_open_folder(self, enabled):
        enabled = bool(enabled)
        with self.lock:
            self.config["downloader_auto_open_folder"] = enabled
            for job in self.jobs.values():
                job.auto_open_folder = enabled
                job.config["downloader_auto_open_folder"] = enabled
        return enabled

    def set_verify_final_size(self, enabled):
        enabled = bool(enabled)
        with self.lock:
            self.config["downloader_verify_final_size"] = enabled
            for job in self.jobs.values():
                job.verify_final_size = enabled
                job.config["downloader_verify_final_size"] = enabled
        return enabled
    # PATCH_DOWNLOADER_FEATURES_MANAGER_METHODS_END

    def add(self, url, scheduled_at=0, priority=0):
        os.makedirs(self.download_dir, exist_ok=True)

        jid = uuid.uuid4().hex[:12]
        filename = filename_from_url(url)
        base, ext = os.path.splitext(filename)
        path = os.path.join(self.download_dir, filename)
        n = 1
        while os.path.exists(path) or os.path.exists(path + ".part"):
            filename = f"{base}_{n}{ext}"
            path = os.path.join(self.download_dir, filename)
            n += 1

        part_path = path + ".part"
        state_path = path + ".state.json"

        state = DownloadState(
            id=jid,
            url=url,
            filename=filename,
            path=path,
            part_path=part_path,
            state_path=state_path,
        )

        # PATCH_DOWNLOADER_FEATURES_ADD_STATE_START
        state.scheduled_at = parse_schedule_ts(scheduled_at)
        state.priority = int(priority or 0)
        state.status = "scheduled" if state.scheduled_at > time.time() else "queued"
        # PATCH_DOWNLOADER_FEATURES_ADD_STATE_END

        job = DownloadJob(
            self.config,
            state,
            chunk_size=int(self.config.get("downloader_chunk_size", 8 * 1024 * 1024)),
            parallel=int(self.config.get("downloader_parallel", 4)),
            retries=int(self.config.get("downloader_retries", 4)),
        )

        with self.lock:
            self.jobs[jid] = job

        job.state.log(
            "INFO",
            "Queued" if job.state.status == "queued" else f"Scheduled for {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(job.state.scheduled_at))}"
        )
        job.state.save()

        return jid

    def _run_job_thread(self, job):
        asyncio.run(job.start())

    def pause(self, jid):
        job = self.jobs.get(jid)
        if job:
            job.pause()

    def resume(self, jid):
        job = self.jobs.get(jid)
        if job:
            if job.state.status in ("paused", "error"):
                job.pause_event.set()
                if float(getattr(job.state, "scheduled_at", 0.0) or 0.0) > time.time():
                    job.state.status = "scheduled"
                else:
                    job.state.status = "queued"
                job.state.log("INFO", "Queued for resume")
                job.state.save()
            else:
                job.resume()

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if job:
            job.cancel()

    def remove(self, jid):
        job = self.jobs.get(jid)
        if not job:
            return
        job.cancel()
        for p in (job.state.state_path, job.state.part_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        with self.lock:
            self.jobs.pop(jid, None)

    def set_h2_enabled(self, enabled):
        enabled = bool(enabled)
        with self.lock:
            self.config["downloader_h2_enabled"] = enabled
            for job in self.jobs.values():
                job.config["downloader_h2_enabled"] = enabled
        return enabled

    def settings_snapshot(self):
        return {
            "h2_enabled": bool(self.config.get("downloader_h2_enabled", False)),
            "h2_wait_seconds": float(self.config.get("downloader_h2_wait_seconds", 2.5)),
            "h2_timeout": float(self.config.get("downloader_h2_timeout", self.config.get("relay_timeout", 45))),

            # PATCH_DOWNLOADER_FEATURES_SETTINGS_START
            "speed_limit_bps": int(self.config.get("downloader_speed_limit_bps", 0) or 0),
            "max_active": int(self.config.get("downloader_max_active", getattr(self, "max_active", 2)) or 2),
            "auto_open_folder": bool(self.config.get("downloader_auto_open_folder", False)),
            "verify_final_size": bool(self.config.get("downloader_verify_final_size", True)),
            "auto_retry_rounds": int(self.config.get("downloader_auto_retry_rounds", 2) or 2),
            # PATCH_DOWNLOADER_FEATURES_SETTINGS_END
        }

    def snapshot(self):
        with self.lock:
            return [j.snapshot() for j in self.jobs.values()]

    def shutdown(self):
        self._scheduler_stop = True
        with self.lock:
            for j in self.jobs.values():
                j.cancel()
