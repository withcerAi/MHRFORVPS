import time
import hashlib
import copy


class AutoTuner:
    """
    Runtime advisor v4.

    Focus:
    - Understands new H2/range/HLS timeout knobs.
    - Does not disable video/H2 features aggressively.
    - Keeps HLS objects out of chunked download mode.
    - Uses recent/runtime logs over stale historical totals.
    - Can suggest/apply range_probe_timeout, h2_stream_timeout, h2_connect_timeout.
    """

    HLS_STREAM_EXTS = (".m3u8", ".m4s", ".ts", ".mpd", ".mp2t")

    def __init__(self, config):
        self.config = config
        self.cached = None
        self.cached_at = 0
        self.interval = int(config.get("auto_tune_suggestion_interval_seconds", 60) or 60)

    @staticmethod
    def _safe_int(v, d=0):
        try:
            if v in (None, "", "-", "None"):
                return d
            return int(float(v))
        except Exception:
            return d

    @staticmethod
    def _safe_float(v, d=0.0):
        try:
            if v in (None, "", "-", "None"):
                return d
            return float(v)
        except Exception:
            return d

    @staticmethod
    def _pct(part, total):
        total = max(1, int(total or 1))
        return round((int(part or 0) / total) * 100, 3)

    @staticmethod
    def _fmt_bytes(n):
        try:
            n = float(n or 0)
        except Exception:
            n = 0.0
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while n >= 1024 and i < len(units) - 1:
            n /= 1024
            i += 1
        return f"{n:.1f} {units[i]}"

    @staticmethod
    def _bytes_to_mb(v):
        try:
            if v in (None, "", "-", "None"):
                return 0.0
            return float(v) / 1024.0 / 1024.0
        except Exception:
            return 0.0

    @classmethod
    def _mem_mb(cls, memory, mb_key, byte_key):
        if not isinstance(memory, dict):
            return 0.0
        raw_mb = memory.get(mb_key)
        if raw_mb not in (None, "", "-", "None"):
            return cls._safe_float(raw_mb, 0.0)
        return cls._bytes_to_mb(memory.get(byte_key))

    @staticmethod
    def _norm_host(host):
        return str(host or "").strip().lower()

    def _merged_config(self, snapshot):
        c = dict(self.config or {})
        effective = snapshot.get("config") or {}
        if isinstance(effective, dict):
            c.update(effective)
        return c

    def _all_log_text(self, logs):
        all_lines = []
        for key in ("errors", "video", "sabr", "downloads", "live"):
            all_lines.extend(logs.get(key, []) or [])
        return "\n".join(str(x).lower() for x in all_lines[-900:])

    def _log_score(self, logs):
        text = self._all_log_text(logs)

        def count_any(*items):
            return sum(text.count(x) for x in items)

        return {
            "timeout": count_any("timeout", "timeouterror", "timed out"),
            "relay_error": count_any("relay error", "relay failed", "outbound connect failed"),
            "h1_timeout": count_any("relay timeout via h1"),
            "h2_timeout": count_any("relay timeout via h2", "h2 stream", "h2_fanout_stream_or_apps_script", "h2_stream_no_response"),
            "h2_error": count_any(
                "h2 reader error",
                "h2 temporarily disabled",
                "h2 disabled",
                "h2 reader loop ended",
                "h2 remote closed",
                "connectionerror",
                "winerror 64",
                "network name is no longer available",
            ),
            "h2_reconnect": count_any("h2 connected", "h2 reconnect", "re-enabled script"),
            "range_probe": count_any("initial range probe", "range probe"),
            "stream_fallback": count_any("streaming download fallback", "invalid first range", "file too small"),
            "quota": count_any("quota", "rate limit", "too many", " 429", "status=429"),
            "bad_status": count_any(
                " 502", " 503", " 504", " 500", " 403", " 429",
                "status=502", "status=503", "status=504", "status=500", "status=403", "status=429",
            ),
            "video": count_any("googlevideo", "videoplayback", "video passthrough", "video range", "video stream", ".m4s", ".m3u8"),
            "hls_manifest": count_any("hls manifest parsed", "master.m3u8", "index-v1-a1.m3u8"),
            "hls_prefetch": count_any("hls prefetch ->"),
            "hls_hit": count_any("hls prefetch hit"),
            "hls_segment": count_any("seg-", ".m4s"),
            "hls_no_range": count_any("video request without range"),
            "manifest_404": count_any("status=404 type=application/vnd.apple.mpegurl", "master.m3u8") if "status=404" in text else 0,
            "sabr": count_any("sabr boost", "youtube sabr", "sabr=1", "video sabr direct"),
            "playback_failed": count_any("videoplayfailed", "playbackfailed", "videoplayfailed402", "playerbuffering"),
            "sni_error": count_any("sni-rewrite outbound connect failed", "sni rewrite", "sni-rewrite"),
            "reset_error": count_any("connectionreseterror", "winerror 10054", "connection reset"),
            "download": count_any("parallel download", "download progress", "download complete", "chunks"),
            "pagead_noise": count_any("pagead/interaction", "youtube.com/pagead"),
            "static_asset": count_any("i.ytimg.com", "yt3.ggpht.com", "gstatic", "ytimg"),
            "script_disabled": count_any("disabled script", "script warning"),

            # Exit-node / server-side signals. These come from logs that main.py already collected.
            "server_memory_pressure": count_any(
                "exit_node_memory_pressure",
                "memory-guard",
                "hard limit reached",
                "critical_before_request",
                "interval_soft_limit",
            ),
            "server_busy": count_any(
                "exit_node_busy",
                "max_inflight_reached",
                "server busy",
                "too many inflight",
            ),
            "server_health_fail": count_any(
                "exit node health via relay failed",
                "offline_or_locked",
                "server-health",
                "exit-node-status",
            ),
            "server_response_too_large": count_any(
                "response_too_large",
                "content_length_exceeds_limit",
                "stream_exceeds_limit",
                "gas_json_base64_limit_protection",
            ),
            "server_target_timeout": count_any(
                "target_timeout",
                "socket_timeout",
                "ttfb_timeout_after_connect",
                "body_timeout_after_response_started",
                "connect_or_tls_timeout",
            ),
            "server_relay_error": count_any(
                "blocked_or_dns_failed",
                "request_error",
                "response_error",
                "server_error",
            ),
        }

    def _host_category(self, host):
        h = self._norm_host(host)
        if not h:
            return "unknown"
        if "googlevideo" in h or "youtube" in h or "ytimg" in h or "ggpht" in h:
            return "youtube"
        if "phncdn" in h or h.endswith("-h.phncdn.com") or "cdn77" in h:
            return "hls_video"
        if "googleapis" in h or "gstatic" in h or "google.com" in h or "clients.google" in h:
            return "google_services"
        if h.startswith("127.") or h == "localhost" or h.endswith(".local") or h.endswith(".lan"):
            return "local"
        if h.replace(".", "").isdigit():
            return "exit_node"
        return "web"

    def _severity_from_rate(self, err_rate, requests=0):
        if requests >= 50 and err_rate >= 45:
            return "critical"
        if requests >= 30 and err_rate >= 25:
            return "warning"
        if requests >= 10 and err_rate >= 20:
            return "warning"
        return "healthy"

    def _host_reports(self, snapshot):
        top_hosts = snapshot.get("top_hosts") or []
        groups = {}
        per_host = []

        for item in top_hosts:
            host = self._norm_host(item.get("host"))
            req = self._safe_int(item.get("requests"))
            err = self._safe_int(item.get("errors"))
            bytes_ = self._safe_int(item.get("bytes"))

            if not host:
                continue

            rate = self._pct(err, req)
            cat = self._host_category(host)
            g = groups.setdefault(cat, {
                "category": cat,
                "requests": 0,
                "errors": 0,
                "bytes": 0,
                "hosts": [],
            })
            g["requests"] += req
            g["errors"] += err
            g["bytes"] += bytes_
            g["hosts"].append(host)

            sev = self._severity_from_rate(rate, req)
            issue = "Looks stable."
            recommendation = "No host-specific change needed."

            if sev == "critical":
                issue = f"High historical failure rate on {host}: {rate}%."
                recommendation = "Check runtime/recent errors before applying changes."
            elif sev == "warning":
                issue = f"Noticeable historical failures on {host}: {rate}%."
                recommendation = "Monitor recent behavior; old errors may be stale."

            per_host.append({
                "host": host,
                "category": cat,
                "requests": req,
                "errors": err,
                "error_rate": rate,
                "bytes": bytes_,
                "bytes_h": self._fmt_bytes(bytes_),
                "severity": sev,
                "issue": issue,
                "recommendation": recommendation,
            })

        group_reports = []
        for cat, g in groups.items():
            rate = self._pct(g["errors"], g["requests"])
            sev = self._severity_from_rate(rate, g["requests"])

            titles = {
                "youtube": "YouTube / video",
                "hls_video": "HLS video CDN",
                "google_services": "Google services / APIs",
                "web": "General web browsing",
                "exit_node": "Exit node health path",
                "local": "Local / LAN",
            }

            group_reports.append({
                "category": cat,
                "title": titles.get(cat, cat),
                "requests": g["requests"],
                "errors": g["errors"],
                "error_rate": rate,
                "bytes": g["bytes"],
                "bytes_h": self._fmt_bytes(g["bytes"]),
                "hosts": sorted(set(g["hosts"]))[:8],
                "severity": sev,
            })

        per_host.sort(key=lambda x: (x["severity"] != "critical", x["error_rate"] * -1, x["requests"] * -1))
        group_reports.sort(key=lambda x: (x["severity"] != "critical", x["error_rate"] * -1, x["requests"] * -1))

        return group_reports, per_host[:15]

    def _script_reports(self, snapshot):
        scripts = snapshot.get("script_ids") or []
        out = []

        runtime_req_total = 0
        runtime_err_total = 0
        total_req_total = 0
        total_err_total = 0
        critical = 0
        warning = 0

        for item in scripts:
            name = str(item.get("script") or item.get("short_id") or item.get("sid") or "-")

            total_req = self._safe_int(item.get("requests"))
            total_err = self._safe_int(item.get("errors"))

            runtime_req = self._safe_int(item.get("runtime_requests"), 0)
            runtime_err = self._safe_int(item.get("runtime_errors"), 0)
            runtime_rate = self._safe_float(item.get("runtime_error_rate"), self._pct(runtime_err, runtime_req))

            limit = self._safe_int(item.get("limit"), 0)
            pct = self._safe_float(item.get("quota_percent"), 0.0)
            status = str(item.get("last_status") or "OK")
            rate_limited = bool(item.get("rate_limited"))

            status_bad = status.upper() not in ("", "OK", "200", "SUCCESS", "HEALTHY")

            severity = "healthy"
            issue = "Healthy in the current runtime window."

            if rate_limited or pct >= 95 or status_bad:
                severity = "critical"
                issue = "Current status/rate/quota indicates a real problem."
                critical += 1
            elif runtime_req >= 20 and runtime_err >= 10 and runtime_rate >= 35:
                severity = "critical"
                issue = "High runtime error rate in the current window."
                critical += 1
            elif runtime_req >= 20 and runtime_err >= 4 and runtime_rate >= 15:
                severity = "warning"
                issue = "Runtime errors exist, but this is not critical yet."
                warning += 1
            elif total_err > 0:
                severity = "healthy"
                issue = "Historical errors exist, but runtime window is currently OK."

            runtime_req_total += runtime_req
            runtime_err_total += runtime_err
            total_req_total += total_req
            total_err_total += total_err

            out.append({
                "script": name,
                "requests": total_req,
                "errors": total_err,
                "runtime_requests": runtime_req,
                "runtime_errors": runtime_err,
                "runtime_error_rate": round(runtime_rate, 2),
                "error_rate": self._pct(total_err, total_req),
                "quota_percent": round(pct, 2),
                "limit": limit,
                "last_status": status,
                "rate_limited": rate_limited,
                "severity": severity,
                "issue": issue,
            })

        out.sort(key=lambda x: (x["severity"] != "critical", x["runtime_error_rate"] * -1, x["runtime_errors"] * -1))

        summary = {
            "runtime_requests": runtime_req_total,
            "runtime_errors": runtime_err_total,
            "runtime_error_rate": self._pct(runtime_err_total, runtime_req_total),
            "total_requests": total_req_total,
            "total_errors": total_err_total,
            "error_rate": self._pct(total_err_total, total_req_total),
            "critical": critical,
            "warning": warning,
            "count": len(out),
        }

        return summary, out

    def _feature_reports(self, c):
        h2_on = (
            not bool(c.get("h2_disabled_by_dashboard", False))
            and self._safe_int(c.get("h2_connections"), 0) > 0
        )

        items = [
            {
                "name": "H2",
                "key": "h2_connections",
                "enabled": h2_on,
                "global_off": bool(c.get("h2_disabled_by_dashboard", False)),
                "detail": f"{self._safe_int(c.get('h2_connections'), 0)} configured | stream_timeout={c.get('h2_stream_timeout', '-')} | connect_timeout={c.get('h2_connect_timeout', '-')}",
            },
            {
                "name": "Range Probe",
                "key": "range_probe_timeout",
                "enabled": True,
                "global_off": False,
                "detail": f"timeout={c.get('range_probe_timeout', c.get('relay_timeout', '-'))}",
            },
            {
                "name": "Turbo",
                "key": "turbo_mode_enabled",
                "enabled": bool(c.get("turbo_mode_enabled", False)),
                "global_off": bool(c.get("turbo_disabled_by_dashboard", False)),
                "detail": f"parallel={c.get('turbo_parallel_relay', '-')}",
            },
            {
                "name": "SABR",
                "key": "youtube_sabr_booster_enabled",
                "enabled": bool(c.get("youtube_sabr_booster_enabled", False)),
                "global_off": bool(c.get("sabr_disabled_by_dashboard", False)),
                "detail": f"parallel={c.get('youtube_sabr_max_parallel', '-')}",
            },
            {
                "name": "Video Prefetch",
                "key": "video_prefetch_enabled",
                "enabled": bool(c.get("video_prefetch_enabled", False)),
                "global_off": bool(c.get("video_prefetch_disabled_by_dashboard", False)),
                "detail": f"ranges={c.get('video_prefetch_next_ranges', '-')}, parallel={c.get('video_prefetch_parallel', '-')}, ttl={c.get('video_cache_ttl_seconds', '-')}",
            },
            {
                "name": "Manifest Prefetch",
                "key": "manifest_prefetch_enabled",
                "enabled": bool(c.get("manifest_prefetch_enabled", False)),
                "global_off": bool(c.get("manifest_prefetch_disabled_by_dashboard", False)),
                "detail": f"segments={c.get('manifest_prefetch_next_segments', '-')}, parallel={c.get('manifest_prefetch_parallel', '-')}, ttl={c.get('manifest_cache_ttl_seconds', '-')}",
            },
            {
                "name": "Video Passthrough",
                "key": "video_passthrough_enabled",
                "enabled": bool(c.get("video_passthrough_enabled", False)),
                "global_off": bool(c.get("video_passthrough_disabled_by_dashboard", False)),
                "detail": f"timeout={c.get('video_passthrough_relay_timeout', '-')}",
            },
            {
                "name": "Batch",
                "key": "enable_batch",
                "enabled": bool(c.get("enable_batch", False)),
                "global_off": False,
                "detail": f"batch_max={c.get('batch_max', '-')}",
            },
            {
                "name": "Sub-batch",
                "key": "enable_sub_batch",
                "enabled": bool(c.get("enable_sub_batch", False)),
                "global_off": False,
                "detail": f"micro={c.get('batch_window_micro', '-')}, macro={c.get('batch_window_macro', '-')}",
            },
        ]

        all_main_features_off = (
            not h2_on
            and not bool(c.get("turbo_mode_enabled", False))
            and not bool(c.get("youtube_sabr_booster_enabled", False))
            and not bool(c.get("video_prefetch_enabled", False))
            and not bool(c.get("manifest_prefetch_enabled", False))
            and not bool(c.get("video_passthrough_enabled", False))
        )

        return items, all_main_features_off

    def _quality(self, runtime_rate, score, script_summary, runtime_bytes_from_google):
        usable_traffic_bonus = 10 if runtime_bytes_from_google >= 2 * 1024 * 1024 else 0

        relay_penalty = min(
            85,
            runtime_rate * 1.7
            + score["relay_error"] * 2.0
            + score["timeout"] * 1.2
            + score["bad_status"] * 1.2
            + score["h1_timeout"] * 1.4,
        )
        h2_penalty = min(85, score["h2_error"] * 4 + score["h2_timeout"] * 3 + score["script_disabled"] * 1.5)
        video_penalty = min(
            90,
            score["range_probe"] * 4
            + score["stream_fallback"] * 3
            + score["playback_failed"] * 8
            + max(0, score["hls_no_range"] - score["hls_hit"]) * 0.15
            + score["manifest_404"] * 5,
        )
        browsing_penalty = min(80, score["sni_error"] * 5 + score["timeout"] * 0.8 + score["bad_status"] * 0.7)
        script_penalty = min(80, script_summary["runtime_error_rate"] * 1.2 + script_summary["critical"] * 12 + script_summary["warning"] * 4)

        overall_penalty = max(relay_penalty, video_penalty * 0.75, browsing_penalty * 0.65, script_penalty * 0.8, h2_penalty * 0.75)
        overall = round(max(0, min(100, 100 - overall_penalty + usable_traffic_bonus)), 1)

        return {
            "overall": overall,
            "relay": round(max(0, 100 - relay_penalty + usable_traffic_bonus), 1),
            "h2": round(max(0, 100 - h2_penalty + usable_traffic_bonus), 1),
            "video": round(max(0, 100 - video_penalty + usable_traffic_bonus), 1),
            "browsing": round(max(0, 100 - browsing_penalty + usable_traffic_bonus), 1),
            "scripts": round(max(0, 100 - script_penalty), 1),
        }

    def _add_change(self, changes, reasons, c, key, value, why, area="General", impact="Stability"):
        old = c.get(key)
        if old == value:
            return

        disabled_guards = {
            "turbo_mode_enabled": "turbo_disabled_by_dashboard",
            "youtube_sabr_booster_enabled": "sabr_disabled_by_dashboard",
            "video_prefetch_enabled": "video_prefetch_disabled_by_dashboard",
            "manifest_prefetch_enabled": "manifest_prefetch_disabled_by_dashboard",
            "video_passthrough_enabled": "video_passthrough_disabled_by_dashboard",
        }

        guard = disabled_guards.get(key)
        if value is True and guard and bool(c.get(guard, False)):
            return

        changes.append({
            "key": key,
            "old": old,
            "new": value,
            "why": why,
            "area": area,
            "impact": impact,
        })

        if why not in reasons:
            reasons.append(why)

    def _sanitized_download_extensions(self, c):
        raw = c.get("chunked_download_extensions")
        if not isinstance(raw, list):
            return raw

        out = []
        changed = False
        for item in raw:
            ext = str(item or "").strip().lower()
            if not ext:
                continue
            if ext in self.HLS_STREAM_EXTS:
                changed = True
                continue
            out.append(item)

        if changed:
            return out
        return raw

    def _runtime_knobs(self, c):
        keys = [
            "relay_timeout",
            "range_probe_timeout",
            "h2_stream_timeout",
            "h2_connect_timeout",
            "tls_connect_timeout",
            "tcp_connect_timeout",
            "h2_connections",
            "parallel_relay",
            "batch_max",
            "chunked_download_chunk_size",
            "chunked_download_max_parallel",
            "manifest_prefetch_next_segments",
            "manifest_prefetch_parallel",
            "video_prefetch_parallel",
        ]

        return [
            {
                "key": k,
                "value": c.get(k, "-"),
                "severity": "healthy",
            }
            for k in keys
        ]


    def _server_report(self, snapshot, logs, c, score):
        """
        Analyze exit-node/server health from the existing main.py snapshot only.

        Important:
        - This method does not perform network calls.
        - It only reads snapshot["exit_node_health"], snapshot["exit_node_health_error"],
          and current dashboard/log signals.
        """
        exit_health = snapshot.get("exit_node_health") or {}
        exit_error = str(snapshot.get("exit_node_health_error") or "-")
        is_dict = isinstance(exit_health, dict)
        ok = bool(exit_health.get("ok")) if is_dict else False

        memory = exit_health.get("memory") if is_dict else {}
        if not isinstance(memory, dict):
            memory = {}

        rss_mb = self._mem_mb(memory, "rssMB", "rss")
        heap_mb = self._mem_mb(memory, "heapUsedMB", "heapUsed")
        heap_total_mb = self._mem_mb(memory, "heapTotalMB", "heapTotal")
        external_mb = self._mem_mb(memory, "externalMB", "external")
        array_buffers_mb = self._mem_mb(memory, "arrayBuffersMB", "arrayBuffers")

        inflight = self._safe_int(exit_health.get("inflight"), 0) if is_dict else 0
        total_requests = self._safe_int(exit_health.get("totalRequests"), 0) if is_dict else 0
        total_errors = self._safe_int(exit_health.get("totalErrors"), 0) if is_dict else 0
        total_bytes_in = self._safe_int(exit_health.get("totalBytesIn"), 0) if is_dict else 0
        total_bytes_out = self._safe_int(exit_health.get("totalBytesOut"), 0) if is_dict else 0
        uptime_seconds = self._safe_int(exit_health.get("uptime_seconds"), 0) if is_dict else 0
        cleanup_count = self._safe_int(exit_health.get("memoryCleanupCount"), 0) if is_dict else 0

        error_rate = self._pct(total_errors, total_requests) if total_requests else 0
        cleanup_per_hour = round(cleanup_count / max(1.0, uptime_seconds / 3600.0), 2) if uptime_seconds else 0.0

        memory_limits = exit_health.get("memoryLimits") if is_dict else {}
        if not isinstance(memory_limits, dict):
            memory_limits = {}

        # Best-effort server limits. Supports both flat and nested health payloads.
        soft_limit_mb = self._safe_float(
            memory_limits.get("softLimitMB")
            or exit_health.get("memorySoftLimitMB")
            or exit_health.get("softLimitMB")
            or c.get("exit_node_memory_soft_limit_mb")
            or c.get("MEMORY_SOFT_LIMIT_MB"),
            520.0,
        )
        hard_limit_mb = self._safe_float(
            memory_limits.get("hardLimitMB")
            or exit_health.get("memoryHardLimitMB")
            or exit_health.get("hardLimitMB")
            or c.get("exit_node_memory_hard_limit_mb")
            or c.get("MEMORY_HARD_LIMIT_MB"),
            680.0,
        )

        rss_pct_hard = round((rss_mb / max(1.0, hard_limit_mb)) * 100, 1) if rss_mb else 0.0
        rss_pct_soft = round((rss_mb / max(1.0, soft_limit_mb)) * 100, 1) if rss_mb else 0.0

        signals = []
        recs = []
        cards = []

        def add_signal(name, value, severity, detail):
            signals.append({
                "name": name,
                "value": value,
                "severity": severity,
                "detail": detail,
            })

        def add_rec(title, severity, issue, recommendation, command="-"):
            recs.append({
                "title": title,
                "severity": severity,
                "issue": issue,
                "recommendation": recommendation,
                "command": command,
            })

        server_level = "healthy"
        server_note = "Exit node looks healthy from the latest dashboard snapshot."

        if not is_dict:
            server_level = "warning"
            server_note = "No exit-node health snapshot is available yet."
            add_signal(
                "Health snapshot",
                "missing",
                "warning",
                "main.py has not received a server health payload yet.",
            )
            add_rec(
                "Wait for server health sample",
                "warning",
                "No exit-node health data is available.",
                "Open the dashboard and let the health monitor collect one or two samples. This analysis intentionally does not fetch the server by itself.",
            )
        elif not ok:
            server_level = "critical"
            server_note = "Exit node health is not OK or the node is offline/locked."
            add_signal(
                "Server status",
                "offline / not ok",
                "critical",
                exit_error,
            )
            add_rec(
                "Check PM2 process and health key",
                "critical",
                "The dashboard snapshot says the exit node is not healthy.",
                "Verify pm2 status, HEALTH_KEY, EXIT_NODE_PSK, firewall, and that the node is listening on the expected port.",
                "pm2 status && pm2 logs exit-node --lines 80",
            )
        else:
            add_signal(
                "Server status",
                "online",
                "healthy",
                f"Uptime {uptime_seconds}s, requests {total_requests}.",
            )

        # Memory pressure.
        memory_sev = "healthy"
        if score.get("server_memory_pressure", 0) > 0 or (rss_mb and rss_mb >= hard_limit_mb):
            memory_sev = "critical"
            server_level = "critical"
            server_note = "Exit node memory pressure is critical."
            add_rec(
                "Reduce exit-node memory pressure",
                "critical",
                f"RSS is {rss_mb:.1f}MB, near/over hard limit {hard_limit_mb:.0f}MB. Heap is only {heap_mb:.1f}MB, so pressure is likely sockets/buffers/native memory rather than JS heap.",
                "Lower socket/inflight pressure first. If the VPS has enough RAM, only then raise MEMORY_HARD_LIMIT_MB. Do not rely only on --max-old-space-size because heap usage is low.",
                "MAX_SOCKETS=128 MAX_FREE_SOCKETS=8 MAX_INFLIGHT=128 MEMORY_SOFT_LIMIT_MB=520 MEMORY_HARD_LIMIT_MB=680",
            )
        elif rss_mb and rss_mb >= soft_limit_mb:
            memory_sev = "warning"
            if server_level == "healthy":
                server_level = "warning"
                server_note = "Exit node memory is above the soft limit."
            add_rec(
                "Watch memory guard activity",
                "warning",
                f"RSS is {rss_mb:.1f}MB, above soft limit {soft_limit_mb:.0f}MB.",
                "If cleanups keep increasing, reduce MAX_FREE_SOCKETS and MAX_SOCKETS or reduce local prefetch/fanout pressure.",
                "MAX_SOCKETS=160 MAX_FREE_SOCKETS=12 MAX_INFLIGHT=160",
            )

        add_signal(
            "RSS memory",
            f"{rss_mb:.1f} MB",
            memory_sev,
            f"{rss_pct_soft}% of soft limit {soft_limit_mb:.0f}MB, {rss_pct_hard}% of hard limit {hard_limit_mb:.0f}MB.",
        )
        add_signal(
            "Heap / external",
            f"heap {heap_mb:.1f} MB / ext {external_mb:.1f} MB",
            "healthy" if heap_mb < 256 else "warning",
            f"heapTotal={heap_total_mb:.1f}MB, arrayBuffers={array_buffers_mb:.1f}MB.",
        )

        # Cleanup pressure.
        cleanup_sev = "healthy"
        if cleanup_per_hour >= 30 or cleanup_count >= 50:
            cleanup_sev = "critical"
            server_level = "critical"
            add_rec(
                "Memory cleanup is too frequent",
                "critical",
                f"Memory cleanup count is {cleanup_count} ({cleanup_per_hour}/hour).",
                "This means the server is constantly fighting memory pressure. Reduce concurrency and free sockets, and restart the PM2 process after changing env.",
                "MAX_FREE_SOCKETS=8 MAX_SOCKETS=128 MAX_INFLIGHT=128",
            )
        elif cleanup_per_hour >= 10 or cleanup_count >= 20:
            cleanup_sev = "warning"
            if server_level == "healthy":
                server_level = "warning"
            add_rec(
                "Cleanup rate is elevated",
                "warning",
                f"Memory cleanup count is {cleanup_count} ({cleanup_per_hour}/hour).",
                "Keep an eye on RSS. If video/download traffic is heavy, reduce prefetch parallelism or exit-node socket counts.",
            )

        add_signal(
            "Memory cleanups",
            str(cleanup_count),
            cleanup_sev,
            f"Approx {cleanup_per_hour}/hour.",
        )

        # Inflight pressure.
        inflight_sev = "healthy"
        if score.get("server_busy", 0) > 0 or inflight >= 100:
            inflight_sev = "critical"
            server_level = "critical"
            add_rec(
                "Exit node is overloaded",
                "critical",
                f"Inflight requests are {inflight}.",
                "Reduce MAX_INFLIGHT and local fanout/prefetch. High inflight often means stuck upstream requests or too much concurrent video/download pressure.",
                "MAX_INFLIGHT=128",
            )
        elif inflight >= 50:
            inflight_sev = "warning"
            if server_level == "healthy":
                server_level = "warning"
            add_rec(
                "Inflight is high",
                "warning",
                f"Inflight requests are {inflight}.",
                "Watch for stuck requests. Consider lowering local parallel_relay, video prefetch parallel, or download max parallel.",
            )

        add_signal(
            "Inflight",
            str(inflight),
            inflight_sev,
            "Current active requests on the exit node.",
        )

        # Error rate.
        error_sev = "healthy"
        if total_requests >= 100 and error_rate >= 25:
            error_sev = "critical"
            server_level = "critical"
            add_rec(
                "High exit-node error rate",
                "critical",
                f"Exit node error rate is {error_rate}% ({total_errors}/{total_requests}).",
                "Check PM2 logs for target_timeout, blocked_or_dns_failed, response_too_large, and memory pressure. Lower request pressure before increasing timeouts.",
                "pm2 logs exit-node --lines 120",
            )
        elif total_requests >= 100 and error_rate >= 10:
            error_sev = "warning"
            if server_level == "healthy":
                server_level = "warning"
            add_rec(
                "Exit-node errors are elevated",
                "warning",
                f"Exit node error rate is {error_rate}% ({total_errors}/{total_requests}).",
                "Review server logs and dashboard host groups. If errors are mostly response_too_large, lower MAX_RESPONSE_BODY or keep large downloads in chunked/stream mode.",
            )

        add_signal(
            "Server errors",
            f"{total_errors}/{total_requests} ({error_rate}%)",
            error_sev,
            "Historical errors reported by the exit-node process.",
        )

        # Response size and timeout pressure from logs.
        if score.get("server_response_too_large", 0) > 0:
            if server_level == "healthy":
                server_level = "warning"
            add_signal(
                "Large response guard",
                str(score.get("server_response_too_large", 0)),
                "warning",
                "Server saw response_too_large / GAS JSON base64 limit protection.",
            )
            add_rec(
                "Large body protection triggered",
                "warning",
                "Some responses are too large for the JSON/base64 relay path.",
                "For big downloads, keep chunked/stream download enabled. Avoid raising MAX_RESPONSE_BODY too much on low-RAM VPS because base64 JSON doubles memory pressure.",
                "MAX_RESPONSE_BODY=134217728  # safer on small VPS",
            )

        if score.get("server_target_timeout", 0) >= 2:
            if server_level == "healthy":
                server_level = "warning"
            add_signal(
                "Target timeouts",
                str(score.get("server_target_timeout", 0)),
                "warning",
                "Exit node target/socket timeout signals appeared in logs.",
            )
            add_rec(
                "Origin timeout pressure",
                "warning",
                "The exit node saw target/socket timeouts.",
                "If this happens during video, reduce video/SABR parallelism first. If it happens on slow APIs, increase TIMEOUT_SLOW_MS or TIMEOUT_VIDEO_MS only after memory is stable.",
                "TIMEOUT_SLOW_MS=150000 TIMEOUT_VIDEO_MS=180000",
            )

        if score.get("server_health_fail", 0) > 0 and server_level == "healthy":
            server_level = "warning"
            add_signal(
                "Health monitor",
                str(score.get("server_health_fail", 0)),
                "warning",
                "Health monitor had recent failures in logs.",
            )

        # Throughput info.
        add_signal(
            "Traffic in/out",
            f"{self._fmt_bytes(total_bytes_in)} / {self._fmt_bytes(total_bytes_out)}",
            "healthy",
            "Bytes seen by the exit-node relay process.",
        )

        server_quality = 100
        if server_level == "critical":
            server_quality = 35
        elif server_level == "warning":
            server_quality = 68

        if memory_sev == "critical":
            server_quality = min(server_quality, 30)
        elif memory_sev == "warning":
            server_quality = min(server_quality, 65)

        if inflight_sev == "critical":
            server_quality = min(server_quality, 40)
        elif inflight_sev == "warning":
            server_quality = min(server_quality, 70)

        if error_sev == "critical":
            server_quality = min(server_quality, 45)
        elif error_sev == "warning":
            server_quality = min(server_quality, 75)

        cards = [
            {
                "title": "Exit Node",
                "value": "ONLINE" if ok else "OFFLINE",
                "severity": "healthy" if ok else "critical",
                "detail": exit_error if not ok else f"Uptime {uptime_seconds}s, requests {total_requests}.",
            },
            {
                "title": "Server Memory",
                "value": f"{rss_mb:.1f} MB" if rss_mb else "-",
                "severity": memory_sev,
                "detail": f"Heap {heap_mb:.1f}MB, external {external_mb:.1f}MB, hard limit {hard_limit_mb:.0f}MB.",
            },
            {
                "title": "Inflight",
                "value": str(inflight),
                "severity": inflight_sev,
                "detail": "Active requests currently handled by the exit node.",
            },
            {
                "title": "Server Errors",
                "value": f"{error_rate}%",
                "severity": error_sev,
                "detail": f"{total_errors} errors over {total_requests} requests.",
            },
            {
                "title": "Memory Cleanups",
                "value": str(cleanup_count),
                "severity": cleanup_sev,
                "detail": f"{cleanup_per_hour}/hour.",
            },
            {
                "title": "Server Quality",
                "value": f"{server_quality}/100",
                "severity": server_level,
                "detail": server_note,
            },
        ]

        knobs = [
            {
                "key": "RSS memory",
                "value": f"{rss_mb:.1f} MB",
                "severity": memory_sev,
                "recommended": "Keep below soft limit during normal browsing/video.",
            },
            {
                "key": "MEMORY_SOFT_LIMIT_MB",
                "value": soft_limit_mb,
                "severity": "healthy",
                "recommended": "520 on small VPS; higher only if RAM allows.",
            },
            {
                "key": "MEMORY_HARD_LIMIT_MB",
                "value": hard_limit_mb,
                "severity": memory_sev,
                "recommended": "680 on small VPS; 850-1200 only on larger RAM VPS.",
            },
            {
                "key": "MAX_SOCKETS",
                "value": memory_limits.get("maxSockets", exit_health.get("maxSockets", "-")) if is_dict else "-",
                "severity": "warning" if memory_sev != "healthy" else "healthy",
                "recommended": "128-160 when memory pressure appears.",
            },
            {
                "key": "MAX_FREE_SOCKETS",
                "value": memory_limits.get("maxFreeSockets", exit_health.get("maxFreeSockets", "-")) if is_dict else "-",
                "severity": "warning" if memory_sev != "healthy" else "healthy",
                "recommended": "8-16 to reduce idle socket RSS.",
            },
            {
                "key": "MAX_INFLIGHT",
                "value": memory_limits.get("maxInflight", exit_health.get("maxInflight", "-")) if is_dict else "-",
                "severity": inflight_sev,
                "recommended": "96-160 on small VPS; higher only if stable.",
            },
            {
                "key": "MAX_RESPONSE_BODY",
                "value": memory_limits.get("maxResponseBody", exit_health.get("maxResponseBody", "-")) if is_dict else "-",
                "severity": "warning" if score.get("server_response_too_large", 0) else "healthy",
                "recommended": "Avoid huge values with JSON/base64 relay. Prefer chunked/stream download.",
            },
        ]

        return cards, signals, recs, knobs, server_level, server_note, server_quality

    def _front_health_report(self, snapshot, logs, c, score):
        """
        Front IP / SNI / H2 fronting diagnostics.
        Uses only snapshot/config/logs already collected by main.py.
        Does not make any new request.
        """
        front_ip = c.get("front_ip") or c.get("front_connect_host") or c.get("google_ip") or "-"
        sni_host = c.get("front_sni_host") or c.get("front_domain") or "-"
        http_host = c.get("front_http_host") or "script.google.com"

        recent_timeouts = self._safe_int(c.get("front_ip_recent_timeouts"), 0)
        threshold = self._safe_int(
            c.get("front_ip_diagnostic_threshold")
            or c.get("front_ip_diagnostic_timeout_threshold"),
            5,
        )
        window_s = self._safe_int(c.get("front_ip_diagnostic_window_seconds"), 120)
        cooldown_s = self._safe_int(c.get("front_ip_diagnostic_warning_cooldown"), 30)

        h2_total = self._safe_int(c.get("h2_connections"), 0)
        h2_live = self._safe_int(c.get("h2_live_connections"), 0)
        h2_available = bool(c.get("h2_available", False))
        h2_rebuilding = bool(c.get("h2_rebuilding", False))
        h2_disabled_until = self._safe_int(c.get("h2_disabled_until"), 0)

        sni_pool = c.get("sni_rotation") or c.get("front_domains") or []
        if not isinstance(sni_pool, list):
            sni_pool = [str(sni_pool)]

        disabled_scripts = c.get("disabled_scripts") or []
        blacklisted_count = self._safe_int(c.get("blacklisted_scripts"), 0)
        disabled_count = blacklisted_count
        if isinstance(disabled_scripts, list):
            disabled_count = max(disabled_count, len(disabled_scripts))

        signals = []
        recs = []

        def add_signal(name, value, severity, detail):
            signals.append({
                "name": name,
                "value": value,
                "severity": severity,
                "detail": detail,
            })

        def add_rec(title, severity, issue, recommendation, command="-"):
            recs.append({
                "title": title,
                "severity": severity,
                "issue": issue,
                "recommendation": recommendation,
                "command": command,
            })

        front_level = "healthy"
        front_quality = 100
        front_note = "Front IP / SNI path looks healthy from the current snapshot."

        front_route_sev = "healthy"
        if threshold > 0 and recent_timeouts >= threshold:
            front_level = "critical"
            front_quality = 35
            front_route_sev = "critical"
            front_note = (
                "Front IP path looks unhealthy. This is usually outside MHR: "
                "ISP filtering, bad route, packet loss, weak Google front IP, or front-domain/SNI filtering."
            )
            add_rec(
                "Front IP path problem",
                "critical",
                f"Repeated timeout signals through front IP {front_ip}: {recent_timeouts}/{threshold} in about {window_s}s.",
                "This is probably not a Python/MHR bug. Check your internet path, scan/change google_ip, try another network/ISP, or reduce H2/fanout only as a temporary workaround.",
                "python main.py --scan",
            )
        elif recent_timeouts > 0:
            front_level = "warning"
            front_quality = 72
            front_route_sev = "warning"
            front_note = "Some Front IP timeout signals were detected. Check route/SNI before changing MHR tuning."
            add_rec(
                "Front IP timeout signals",
                "warning",
                f"Front IP {front_ip} has {recent_timeouts}/{threshold} recent timeout signals.",
                "If this number grows, scan/change google_ip or test the same setup from another network path.",
                "python main.py --scan",
            )

        add_signal(
            "Front IP route",
            f"{recent_timeouts}/{threshold}",
            front_route_sev,
            f"front_ip={front_ip}, window={window_s}s, cooldown={cooldown_s}s.",
        )

        sni_sev = "healthy"
        if score.get("sni_error", 0) > 0:
            sni_sev = "warning"
            if front_level == "healthy":
                front_level = "warning"
                front_quality = min(front_quality, 75)
                front_note = "SNI/front-domain warning signals were detected."
            add_rec(
                "Possible SNI/front-domain filtering",
                "warning",
                f"SNI rewrite/front-domain errors appeared in logs. Current SNI/front domain is {sni_host}.",
                "This can be outside MHR if the ISP filters the chosen SNI/front domain. Try another front_domain/front_domains value or another network path.",
            )

        if not sni_pool:
            sni_sev = "warning"
            if front_level == "healthy":
                front_level = "warning"
                front_quality = min(front_quality, 78)
            add_rec(
                "SNI rotation pool missing",
                "warning",
                "No front_domains/SNI rotation pool was exported.",
                "Check config front_domain/front_domains. A small stable pool is better than random unstable domains.",
            )

        add_signal(
            "SNI / front domain",
            str(sni_host),
            sni_sev,
            "HTTP host: " + str(http_host) + " | SNI pool: " + (", ".join(str(x) for x in sni_pool[:8]) if sni_pool else "-"),
        )

        h2_sev = "healthy"
        h2_value = f"{h2_live}/{h2_total}"

        if h2_rebuilding:
            h2_sev = "warning"
            h2_value = f"rebuilding {h2_live}/{h2_total}"
        elif h2_disabled_until > 0:
            h2_sev = "warning"
            h2_value = f"cooldown {h2_disabled_until}s"
            if front_level == "healthy":
                front_level = "warning"
                front_quality = min(front_quality, 76)
            add_rec(
                "H2 cooldown detected",
                "warning",
                f"H2 is temporarily in cooldown for {h2_disabled_until}s.",
                "If Front IP timeouts also rise, this points to route/front IP trouble. If only H2 fails, reduce h2_connections to 2-3.",
            )
        elif h2_total > 0 and h2_live <= 0 and not h2_available:
            h2_sev = "warning"
            h2_value = f"starting {h2_live}/{h2_total}"
            if front_level == "healthy":
                front_level = "warning"
                front_quality = min(front_quality, 80)
            add_rec(
                "H2 has no live connection",
                "warning",
                f"H2 is configured as {h2_total}, but live connections are {h2_live}.",
                "This is normal during startup. If it stays like this, suspect Front IP/SNI/network path or reduce h2_connections.",
            )

        add_signal(
            "H2 fronting",
            h2_value,
            h2_sev,
            f"h2_available={h2_available}, h2_rebuilding={h2_rebuilding}.",
        )

        script_sev = "healthy"
        if disabled_count >= 2:
            script_sev = "warning"
            if front_level == "healthy":
                front_level = "warning"
                front_quality = min(front_quality, 76)
            add_rec(
                "Multiple script deployments disabled",
                "warning",
                f"{disabled_count} Apps Script deployments are disabled/blacklisted.",
                "If the reason is timeout together with Front IP timeout signals, suspect internet/front IP/SNI path. If the reason is 429/quota, add more scripts or reduce fanout.",
            )

        add_signal(
            "Disabled scripts",
            str(disabled_count),
            script_sev,
            "Current runtime disabled/blacklisted Apps Script deployments.",
        )

        cards = [
            {
                "title": "Front IP",
                "value": str(front_ip),
                "severity": front_route_sev,
                "detail": f"Timeout signals {recent_timeouts}/{threshold} in about {window_s}s.",
            },
            {
                "title": "SNI",
                "value": str(sni_host),
                "severity": sni_sev,
                "detail": f"HTTP host: {http_host}. Pool size: {len(sni_pool)}.",
            },
            {
                "title": "H2 Front",
                "value": h2_value,
                "severity": h2_sev,
                "detail": f"available={h2_available}, rebuilding={h2_rebuilding}.",
            },
            {
                "title": "Front Quality",
                "value": f"{front_quality}/100",
                "severity": front_level,
                "detail": front_note,
            },
        ]

        knobs = [
            {
                "key": "google_ip / front_ip",
                "value": front_ip,
                "severity": front_route_sev,
                "recommended": "If timeout signals rise, scan/change google_ip. This is often ISP/route related.",
            },
            {
                "key": "front_domain / SNI",
                "value": sni_host,
                "severity": sni_sev,
                "recommended": "Use a stable Google front domain. Change it only if your network filters the current SNI.",
            },
            {
                "key": "h2_connections",
                "value": h2_total,
                "severity": h2_sev,
                "recommended": "2-3 is safer. Higher values can increase pressure on weak routes.",
            },
            {
                "key": "front_ip_recent_timeouts",
                "value": recent_timeouts,
                "severity": front_route_sev,
                "recommended": "0 is ideal. Repeated timeouts usually mean network/front IP path trouble outside MHR.",
            },
            {
                "key": "range_probe_timeout",
                "value": c.get("range_probe_timeout", "-"),
                "severity": "healthy",
                "recommended": "Raise only if range probes time out while Front IP route is stable.",
            },
            {
                "key": "h2_stream_timeout",
                "value": c.get("h2_stream_timeout", "-"),
                "severity": "healthy",
                "recommended": "Raise if H2 streams are slow; do not use it to hide route/SNI problems.",
            },
        ]

        return cards, signals, recs, knobs, front_level, front_note, front_quality



    def _make_output(self, now, snapshot, logs, c, score, changes, reasons, health_level, note):
        runtime_req = self._safe_int(snapshot.get("runtime_google_requests"), self._safe_int(snapshot.get("google_requests"), 0))
        runtime_err = self._safe_int(snapshot.get("runtime_errors"), self._safe_int(snapshot.get("errors"), 0))
        runtime_rate = self._safe_float(snapshot.get("runtime_error_rate"), self._pct(runtime_err, runtime_req))
        runtime_bytes_from_google = self._safe_int(snapshot.get("runtime_bytes_from_google"), 0)

        total_req = max(1, self._safe_int(snapshot.get("google_requests"), 1))
        total_err = self._safe_int(snapshot.get("errors"), 0)
        total_rate = self._pct(total_err, total_req)

        host_groups, host_reports = self._host_reports(snapshot)
        script_summary, script_reports = self._script_reports(snapshot)
        feature_reports, safe_mode_active = self._feature_reports(c)
        quality = self._quality(runtime_rate, score, script_summary, runtime_bytes_from_google)
        server_cards, server_signals, server_recommendations, server_knobs, server_level, server_note, server_quality = self._server_report(snapshot, logs, c, score)
        quality["server"] = server_quality
        front_cards, front_signals, front_recommendations, front_knobs, front_level, front_note, front_quality = self._front_health_report(snapshot, logs, c, score)
        quality["front"] = front_quality

        exit_health = snapshot.get("exit_node_health") or {}
        inflight = self._safe_int(exit_health.get("inflight"), 0) if isinstance(exit_health, dict) else 0

        summary_cards = [
            {
                "title": "Overall",
                "value": f"{quality['overall']}/100",
                "severity": health_level,
                "detail": f"Runtime: {runtime_err} errors over {runtime_req} requests ({runtime_rate}%). Total historical: {total_err}/{total_req} ({total_rate}%).",
            },
            {
                "title": "Video / HLS",
                "value": f"{quality['video']}/100",
                "severity": "critical" if quality["video"] < 45 else "warning" if quality["video"] < 70 else "healthy",
                "detail": f"HLS segments={score['hls_segment']}, prefetch={score['hls_prefetch']}, hits={score['hls_hit']}, range_probe={score['range_probe']}.",
            },
            {
                "title": "H2",
                "value": f"{quality['h2']}/100",
                "severity": "critical" if quality["h2"] < 45 else "warning" if quality["h2"] < 70 else "healthy",
                "detail": f"H2 timeout={score['h2_timeout']}, H2 errors={score['h2_error']}, reconnect={score['h2_reconnect']}.",
            },
            {
                "title": "Relay",
                "value": f"{quality['relay']}/100",
                "severity": "critical" if quality["relay"] < 45 else "warning" if quality["relay"] < 70 else "healthy",
                "detail": f"Timeouts={score['timeout']}, H1={score['h1_timeout']}, relay errors={score['relay_error']}, bad statuses={score['bad_status']}.",
            },
            {
                "title": "Scripts",
                "value": f"{quality['scripts']}/100",
                "severity": "critical" if script_summary["critical"] else "warning" if script_summary["warning"] else "healthy",
                "detail": f"Runtime script errors: {script_summary['runtime_errors']} over {script_summary['runtime_requests']} requests.",
            },
            {
                "title": "Exit inflight",
                "value": str(inflight),
                "severity": "critical" if inflight >= 100 else "warning" if inflight >= 50 else "healthy",
                "detail": "High inflight means requests may be stuck or waiting too long.",
            },
        ]

        # Keep server analysis as a separate section in the Suggestions page.
        # Do not mix it into main summary_cards, so user can distinguish
        # local relay/browser tuning from exit-node process health.

        if safe_mode_active:
            msg = "Safe mode is active: H2, Turbo, SABR, prefetch and passthrough are all off."
            if msg not in reasons:
                reasons.insert(0, msg)

        sid = hashlib.sha1(
            repr({
                "changes": changes,
                "score": score,
                "runtime_rate": runtime_rate,
                "runtime_req": runtime_req,
                "quality": quality,
                "note": note,
            }).encode("utf-8")
        ).hexdigest()[:12]

        out = {
            "ok": True,
            "analysis_version": "runtime-hls-h2-v4",
            "id": sid,
            "created_at": int(now),
            "next_refresh_in": self.interval,
            "error_rate": runtime_rate,
            "total_error_rate": total_rate,
            "runtime_error_rate": runtime_rate,
            "runtime_errors": runtime_err,
            "runtime_requests": runtime_req,
            "runtime_baseline_age_seconds": self._safe_int(snapshot.get("runtime_baseline_age_seconds"), 0),
            "runtime_baseline_reason": snapshot.get("runtime_baseline_reason", "-"),
            "score": score,
            "health_level": health_level,
            "quality": quality,
            "summary_cards": summary_cards,
            "server_cards": server_cards,
            "server_signals": server_signals,
            "server_recommendations": server_recommendations,
            "server_knobs": server_knobs,
            "server_health_level": server_level,
            "server_note": server_note,
            "front_cards": front_cards,
            "front_signals": front_signals,
            "front_recommendations": front_recommendations,
            "front_knobs": front_knobs,
            "front_health_level": front_level,
            "front_note": front_note,
            "site_groups": host_groups,
            "site_reports": host_reports,
            "script_summary": script_summary,
            "script_reports": script_reports,
            "feature_reports": feature_reports,
            "runtime_knobs": self._runtime_knobs(c),
            "safe_mode_active": safe_mode_active,
            "changes": changes,
            "reasons": reasons[:16] if reasons else [note],
            "apply_mode": "auto" if changes else "none",
            "note": note,
            "request_sample": runtime_req,
            "can_apply": bool(changes),
        }

        self.cached = out
        self.cached_at = now
        return out

    def suggest(self, snapshot, logs):
        now = time.time()

        if self.cached and now - self.cached_at < self.interval:
            out = dict(self.cached)
            out["next_refresh_in"] = max(0, int(self.interval - (now - self.cached_at)))
            return out

        snapshot = snapshot or {}
        logs = logs or {}
        c = self._merged_config(snapshot)
        score = self._log_score(logs)

        mode = str(c.get("runtime_mode") or snapshot.get("web_mode") or "basic").lower()

        runtime_req = self._safe_int(snapshot.get("runtime_google_requests"), self._safe_int(snapshot.get("google_requests"), 0))
        runtime_err = self._safe_int(snapshot.get("runtime_errors"), self._safe_int(snapshot.get("errors"), 0))
        runtime_rate = self._safe_float(snapshot.get("runtime_error_rate"), self._pct(runtime_err, runtime_req))
        runtime_bytes_from_google = self._safe_int(snapshot.get("runtime_bytes_from_google"), 0)

        script_summary, script_reports = self._script_reports(snapshot)

        exit_health = snapshot.get("exit_node_health") or {}
        inflight = self._safe_int(exit_health.get("inflight"), 0) if isinstance(exit_health, dict) else 0

        current_parallel = self._safe_int(c.get("parallel_relay"), 1)
        current_download_parallel = self._safe_int(c.get("chunked_download_max_parallel"), 4)
        current_h2 = self._safe_int(c.get("h2_connections"), 0)
        current_batch = self._safe_int(c.get("batch_max"), 20)
        current_timeout = self._safe_int(c.get("relay_timeout"), 90)
        current_range_probe_timeout = self._safe_int(c.get("range_probe_timeout"), current_timeout)
        current_h2_stream_timeout = self._safe_int(c.get("h2_stream_timeout"), current_timeout)
        current_h2_connect_timeout = self._safe_int(c.get("h2_connect_timeout"), self._safe_int(c.get("tls_connect_timeout"), 20))

        min_sample = self._safe_int(c.get("auto_tune_min_sample_requests", 50), 50)
        enough_runtime = runtime_req >= min_sample
        useful_traffic = runtime_bytes_from_google >= 2 * 1024 * 1024

        hard_h2 = score["h2_error"] >= 4 or score["h2_timeout"] >= 4
        hard_video = (
            score["playback_failed"] >= 2
            or score["range_probe"] >= 3
            or score["stream_fallback"] >= 3
            or (score["hls_no_range"] >= 8 and score["timeout"] >= 4)
        )
        hard_relay = score["relay_error"] >= 8 or score["timeout"] >= 18 or score["bad_status"] >= 18
        hard_exit = inflight >= 100
        hard_scripts = script_summary["critical"] >= 2
        _server_cards_tmp, _server_signals_tmp, _server_recs_tmp, _server_knobs_tmp, server_level_tmp, server_note_tmp, _server_quality_tmp = self._server_report(snapshot, logs, c, score)
        server_snapshot_present = isinstance(snapshot.get("exit_node_health"), dict)
        server_ok = bool(snapshot.get("exit_node_health", {}).get("ok")) if server_snapshot_present else False
        hard_server = server_level_tmp == "critical"
        warn_server = server_level_tmp == "warning"

        # Offline/missing health is a server diagnostic problem, not proof that local
        # relay/video concurrency should be reduced. Only reduce local pressure when
        # the server is online and reports real pressure such as memory/inflight/errors.
        hard_server_pressure = hard_server and server_ok
        hard_server_offline = hard_server and not server_ok

        _front_cards_tmp, _front_signals_tmp, _front_recs_tmp, _front_knobs_tmp, front_level_tmp, front_note_tmp, _front_quality_tmp = self._front_health_report(snapshot, logs, c, score)
        hard_front = front_level_tmp == "critical"
        warn_front = front_level_tmp == "warning"

        critical = False
        warning = False
        monitoring = False

        reasons = []
        changes = []

        if not enough_runtime and not (hard_h2 or hard_video or hard_relay or hard_exit or hard_scripts or hard_server or warn_server or hard_front or warn_front):
            return self._make_output(
                now,
                snapshot,
                logs,
                c,
                score,
                [],
                [f"Analyzing current runtime data. Need at least {min_sample} runtime requests before strong recommendations."],
                "healthy",
                "Analyzing current runtime data. Keep browsing normally so MHR can collect a clean sample.",
            )

        if enough_runtime and runtime_err >= 25 and runtime_rate >= 40:
            critical = True
            reasons.append(f"Critical runtime error rate: {runtime_rate}% over {runtime_req} current-runtime requests.")
        elif enough_runtime and runtime_err >= 10 and runtime_rate >= 20:
            warning = True
            reasons.append(f"Warning runtime error rate: {runtime_rate}% over {runtime_req} current-runtime requests.")
        elif runtime_err > 0:
            monitoring = True
            reasons.append(f"Runtime errors are present but not enough for aggressive tuning: {runtime_err}/{runtime_req} ({runtime_rate}%).")

        if hard_h2:
            warning = True
            reasons.append(f"Recent H2 timeout/drop instability detected: h2_timeout={score['h2_timeout']}, h2_error={score['h2_error']}.")

        if hard_video:
            warning = True
            reasons.append("Recent HLS/video seek/range instability detected.")

        if hard_relay:
            critical = True
            reasons.append(f"Recent relay/backend pressure detected: timeout={score['timeout']}, relay={score['relay_error']}, status={score['bad_status']}.")

        if hard_exit:
            warning = True
            reasons.append(f"Exit node inflight is high: {inflight}.")

        if hard_scripts:
            warning = True
            reasons.append(f"{script_summary['critical']} Apps Script deployments are critical in the runtime window.")

        if hard_server_pressure:
            critical = True
            reasons.append("Exit-node/server health is critical. See the dedicated Server Health section for PM2/env recommendations.")
        elif hard_server_offline:
            monitoring = True
            reasons.append("Exit-node health is offline/missing. Suggestions will show server diagnostics but will not reduce relay/video speed just because health data is unavailable.")
        elif warn_server:
            warning = True
            reasons.append("Exit-node/server health has warnings. See the dedicated Server Health section.")

        if hard_front:
            warning = True
            reasons.append("Front IP / SNI path looks critical. This is probably outside MHR: ISP route/filtering, bad Google front IP, packet loss, or SNI filtering. Check internet path or scan/change google_ip.")
        elif warn_front:
            warning = True
            reasons.append("Front IP / SNI path has warnings. Check the dedicated Front IP / SNI section before changing MHR tuning.")

        if script_summary["warning"] >= 2:
            monitoring = True
            reasons.append(f"{script_summary['warning']} Apps Script deployments have runtime warnings.")

        # HLS stream objects must not be treated as big downloads.
        sanitized_exts = self._sanitized_download_extensions(c)
        if sanitized_exts != c.get("chunked_download_extensions"):
            self._add_change(
                changes,
                reasons,
                c,
                "chunked_download_extensions",
                sanitized_exts,
                "Remove HLS stream objects from chunked-download detection; .m3u8/.m4s/.ts should use HLS/manifest path, not parallel download probing.",
                "Video / HLS",
                "Fix seek/buffer stutter",
            )

        # Keep Basic conservative when traffic is useful.
        if mode == "basic" and useful_traffic and runtime_rate < 30 and not hard_relay:
            if critical and not hard_relay:
                critical = False
                warning = True
            reasons.append("Basic mode has useful traffic; tuning stays conservative and avoids disabling features.")

        # H2 tuning: do not hard-disable; stretch timeout and reduce pressure first.
        if hard_h2 or score["h2_timeout"] >= 2:
            self._add_change(
                changes,
                reasons,
                c,
                "h2_stream_timeout",
                max(current_h2_stream_timeout, 75 if not critical else 90),
                "Increase H2 stream timeout so slow Apps Script/H2 streams are not killed too early.",
                "H2",
                "Fewer H2 premature timeouts",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "h2_connect_timeout",
                max(current_h2_connect_timeout, 25 if not critical else 30),
                "Increase H2 connect timeout for slower front-domain handshakes.",
                "H2",
                "Connection stability",
            )
            if current_h2 > 3:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "h2_connections",
                    3,
                    "Reduce excessive H2 connection pressure without disabling H2.",
                    "H2",
                    "Lower H2 churn",
                )
            elif critical and current_h2 > 2:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "h2_connections",
                    2,
                    "Critical runtime pressure detected; keep H2 on but reduce H2 connection pressure.",
                    "H2",
                    "Lower H2 churn",
                )

        # Range probe tuning.
        if score["range_probe"] >= 1 or score["stream_fallback"] >= 1:
            self._add_change(
                changes,
                reasons,
                c,
                "range_probe_timeout",
                max(current_range_probe_timeout, 75 if not critical else 90),
                "Increase range probe timeout because initial range probes are timing out before the relay finishes.",
                "Range / Download",
                "Fewer false probe failures",
            )

        # HLS seek stability: keep features on but reduce over-prefetch pressure.
        if hard_video or score["hls_no_range"] >= 5 or score["playback_failed"] >= 1:
            self._add_change(
                changes,
                reasons,
                c,
                "manifest_prefetch_parallel",
                1,
                "Keep Manifest Prefetch enabled but serialise it to avoid competing with the active player request.",
                "Video / HLS",
                "Less playback contention",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "manifest_prefetch_next_segments",
                min(max(self._safe_int(c.get("manifest_prefetch_next_segments"), 4), 2), 4),
                "Limit manifest lookahead so seeks do not flood old/far segments.",
                "Video / HLS",
                "Better seek responsiveness",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "manifest_cache_ttl_seconds",
                max(self._safe_int(c.get("manifest_cache_ttl_seconds"), 180), 600),
                "Keep fetched HLS segments longer so going backward in the video can hit cache instead of loading again.",
                "Video / HLS",
                "Better rewind cache",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "video_cache_ttl_seconds",
                max(self._safe_int(c.get("video_cache_ttl_seconds"), 180), 600),
                "Keep video range cache longer for backward seeks.",
                "Video",
                "Better rewind cache",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "video_prefetch_parallel",
                min(max(self._safe_int(c.get("video_prefetch_parallel"), 2), 1), 2),
                "Keep video prefetch conservative so it does not compete with active HLS segment fetches.",
                "Video",
                "Less contention",
            )

        # Relay pressure tuning.
        if critical:
            self._add_change(
                changes,
                reasons,
                c,
                "parallel_relay",
                max(1, min(current_parallel, 1)),
                "Reduce Apps Script fanout because current runtime pressure is critical.",
                "Relay",
                "Lower errors / slower peak speed",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "batch_max",
                max(4, min(current_batch, 8)),
                "Use smaller batches while runtime errors are high.",
                "Relay",
                "Stability",
            )
            self._add_change(
                changes,
                reasons,
                c,
                "relay_timeout",
                max(current_timeout, 150),
                "Give slow relay requests more time during unstable runtime conditions.",
                "Relay",
                "Fewer premature failures",
            )

            if hard_video:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "chunked_download_chunk_size",
                    2097152,
                    "Use smaller 2MB chunks for range stability.",
                    "Video / Download",
                    "Less range pressure",
                )
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "chunked_download_max_parallel",
                    max(1, min(current_download_parallel, 2)),
                    "Reduce parallel range downloads so playback requests do not compete with each other.",
                    "Video / Download",
                    "Stability",
                )
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "video_priority_retry_attempts",
                    1,
                    "Reduce repeated video retries while the current runtime window is unstable.",
                    "Video",
                    "Lower request pressure",
                )

            if score["quota"] >= 2 or hard_scripts:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "enable_sub_batch",
                    False,
                    "Disable sub-batch while Apps Script/runtime pressure is high.",
                    "Scripts",
                    "Lower concurrency pressure",
                )

            if hard_server_pressure:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "manifest_prefetch_parallel",
                    1,
                    "Server health is critical; reduce background HLS prefetch pressure.",
                    "Server / Video",
                    "Lower exit-node pressure",
                )
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "video_prefetch_parallel",
                    1,
                    "Server health is critical; reduce background video range prefetch pressure.",
                    "Server / Video",
                    "Lower exit-node pressure",
                )
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "youtube_sabr_max_parallel",
                    1,
                    "Server health is critical; reduce SABR parallel pressure.",
                    "Server / YouTube",
                    "Lower exit-node pressure",
                )
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "chunked_download_max_parallel",
                    max(1, min(current_download_parallel, 2)),
                    "Server health is critical; reduce parallel download pressure.",
                    "Server / Download",
                    "Lower exit-node pressure",
                )

            return self._make_output(
                now,
                snapshot,
                logs,
                c,
                score,
                changes[:10],
                reasons,
                "critical",
                "Critical current-runtime condition detected. Suggestions keep features alive but reduce pressure and raise timeouts.",
            )

        if warning:
            if runtime_rate >= 20 and runtime_err >= 10:
                self._add_change(
                    changes,
                    reasons,
                    c,
                    "relay_timeout",
                    max(current_timeout, 120),
                    "Slightly increase timeout because runtime errors are elevated.",
                    "Runtime",
                    "Fewer premature failures",
                )

            return self._make_output(
                now,
                snapshot,
                logs,
                c,
                score,
                changes[:8],
                reasons,
                "warning",
                "Warning-level runtime issues detected. Suggestions are conservative and avoid disabling features.",
            )

        if monitoring:
            return self._make_output(
                now,
                snapshot,
                logs,
                c,
                score,
                changes[:4],
                reasons,
                "healthy",
                "Monitoring only. Current runtime errors are not high enough for aggressive tuning.",
            )

        return self._make_output(
            now,
            snapshot,
            logs,
            c,
            score,
            changes[:4],
            reasons or ["Current runtime window looks usable."],
            "healthy",
            "No changes needed right now. Current runtime data does not justify tuning.",
        )

    def apply(self, config, suggestion):
        if not suggestion.get("can_apply") or not suggestion.get("changes"):
            return config

        for item in suggestion.get("changes", []):
            key = item.get("key")
            if not key:
                continue
            config[key] = item.get("new")

        config["runtime_mode"] = "auto"
        config["auto_tune_enabled"] = True
        config["auto_tune_suggestion_mode"] = True
        config["auto_tune_last_apply_id"] = suggestion.get("id", "")
        config["auto_tune_last_apply_at"] = int(time.time())
        config["auto_tune_last_reason"] = suggestion.get("note", "")
        config["auto_tune_last_change_at"] = int(time.time())
        return config

    def tune(self, snapshot, config, logs):
        suggestion = self.suggest(snapshot or {}, logs or {})

        if not bool(config.get("auto_tune_auto_apply", False)):
            return config, {
                "changed": False,
                "reason": "Manual suggestion mode. Auto apply is disabled.",
                "suggestion_id": suggestion.get("id"),
            }

        if not suggestion.get("can_apply"):
            return config, {
                "changed": False,
                "reason": "No safe automatic change available.",
                "suggestion_id": suggestion.get("id"),
            }

        new_config = copy.deepcopy(config)
        self.apply(new_config, suggestion)

        return new_config, {
            "changed": True,
            "reason": suggestion.get("note", "Applied runtime-hls-h2 auto-tune suggestion."),
            "suggestion_id": suggestion.get("id"),
        }
