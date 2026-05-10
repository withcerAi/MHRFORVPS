#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MHR config optimizer.

This module is used by:
- setup_wizard.py during first setup
- main.py dashboard Script Optimizer backend
- runtime_profiles.json generation

Important behavior:
- script_count = real number of Deployment IDs
- script_tier = active selected optimization tier, 1..5
- optimized_for_script_count = same as selected tier
- tier 5 means 5 or more IDs
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
RUNTIME_PROFILES_PATH = APP_DIR / "runtime_profiles.json"

OPTIMIZER_VERSION = "script-count-v2-clean"


def _now() -> int:
    return int(time.time())


def _mb(n: int | float) -> int:
    return int(float(n) * 1024 * 1024)


def script_tier(count: int | str | None) -> int:
    """
    Convert script count into optimizer tier.

    0/1 => tier 1
    2   => tier 2
    3   => tier 3
    4   => tier 4
    5+  => tier 5
    """
    try:
        n = int(count or 0)
    except Exception:
        n = 0

    if n <= 1:
        return 1
    if n == 2:
        return 2
    if n == 3:
        return 3
    if n == 4:
        return 4
    return 5


def clamp_tier(value: Any, max_tier: int = 5) -> int:
    try:
        tier = int(value or 1)
    except Exception:
        tier = 1

    if tier < 1:
        tier = 1
    if tier > 5:
        tier = 5
    if tier > max_tier:
        tier = max_tier

    return tier


def normalize_script_ids(value: Any) -> list[str]:
    """
    Strict Deployment ID normalizer.

    Accepted:
      ["AKfycb...", "AKfycb..."]
      "AKfycb..."

    Rejected:
      comma-separated text
      newline-separated text
      JSON array string
      brackets
      quotes
      spaces/tabs/newlines inside ID

    Reason:
    Dashboard/setup UI should add one clean ID at a time.
    Backend must not silently split broken pasted input.
    """
    if value is None:
        return []

    raw_items = value if isinstance(value, list) else [value]

    out: list[str] = []
    seen: set[str] = set()

    for item in raw_items:
        x = str(item or "").strip()
        if not x:
            continue

        if any(ch in x for ch in (",", "[", "]", '"', "'")):
            raise ValueError(
                "Only one clean Script ID is allowed per entry. "
                "Comma, brackets and quotes are not allowed."
            )

        if any(ch.isspace() for ch in x):
            raise ValueError("Script ID must not contain spaces, tabs or newlines.")

        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


def _base_profile() -> dict[str, Any]:
    """
    Base profile shared by all runtime modes.
    Mode-specific profiles override these values.
    """
    return {
        "relay_timeout": 120,
        "range_probe_timeout": 60,
        "h2_stream_timeout": 60,
        "h2_connect_timeout": 25,
        "tls_connect_timeout": 25,
        "tcp_connect_timeout": 10,

        "parallel_relay": 1,
        "h2_connections": 1,

        "enable_batch": True,
        "enable_sub_batch": False,
        "batch_window_micro": 0.02,
        "batch_window_macro": 0.14,
        "batch_max": 10,

        "chunked_download_min_size": _mb(8),
        "chunked_download_chunk_size": _mb(2),
        "chunked_download_max_parallel": 2,
        "chunked_download_max_chunks": 512,
        "max_response_body_bytes": _mb(300),

        "video_prefetch_enabled": True,
        "video_prefetch_next_ranges": 1,
        "video_prefetch_parallel": 1,
        "video_prefetch_chunk_size": _mb(1),
        "video_cache_max_mb": 384,
        "video_cache_ttl_seconds": 600,

        "manifest_prefetch_enabled": True,
        "manifest_prefetch_next_segments": 2,
        "manifest_prefetch_parallel": 1,
        "manifest_cache_max_mb": 384,
        "manifest_cache_ttl_seconds": 600,

        "video_passthrough_enabled": True,
        "video_passthrough_relay_timeout": 150,

        "video_priority_retry_attempts": 2,
        "video_priority_retry_delay_ms": 150,
        "video_priority_parallel_relay": 1,

        "youtube_sabr_booster_enabled": True,
        "youtube_sabr_timeout": 180,
        "youtube_sabr_max_parallel": 1,
        "youtube_sabr_retry_attempts": 2,
        "youtube_sabr_retry_delay_ms": 150,

        "turbo_mode_enabled": False,
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
        "telegram_parallel_relay": 1,
    }


def _merge(*parts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in parts:
        out.update(deepcopy(part or {}))
    return out


def _tier_values(tier: int) -> dict[str, dict[str, Any]]:
    """
    Runtime values per selected optimizer tier.

    These are intentionally conservative. More IDs unlock more parallelism.
    """
    tier = clamp_tier(tier, 5)

    if tier == 1:
        common = {
            "parallel_relay": 1,
            "h2_connections": 1,
            "enable_sub_batch": False,
            "batch_max": 8,
            "chunked_download_max_parallel": 2,
            "youtube_sabr_max_parallel": 1,
            "turbo_parallel_relay": 1,
            "video_priority_parallel_relay": 1,
            "telegram_parallel_relay": 1,
        }
    elif tier == 2:
        common = {
            "parallel_relay": 2,
            "h2_connections": 2,
            "enable_sub_batch": True,
            "batch_max": 14,
            "chunked_download_max_parallel": 3,
            "youtube_sabr_max_parallel": 2,
            "turbo_mode_enabled": True,
            "turbo_parallel_relay": 1,
            "video_priority_parallel_relay": 1,
            "telegram_parallel_relay": 1,
        }
    elif tier == 3:
        common = {
            "parallel_relay": 2,
            "h2_connections": 3,
            "enable_sub_batch": True,
            "batch_window_micro": 0.015,
            "batch_window_macro": 0.12,
            "batch_max": 20,
            "chunked_download_max_parallel": 4,
            "youtube_sabr_max_parallel": 2,
            "turbo_mode_enabled": True,
            "turbo_parallel_relay": 2,
            "video_prefetch_parallel": 2,
            "video_priority_parallel_relay": 1,
            "telegram_parallel_relay": 2,
        }
    elif tier == 4:
        common = {
            "parallel_relay": 3,
            "h2_connections": 3,
            "enable_sub_batch": True,
            "batch_window_micro": 0.012,
            "batch_window_macro": 0.10,
            "batch_max": 24,
            "chunked_download_chunk_size": _mb(4),
            "chunked_download_max_parallel": 4,
            "youtube_sabr_max_parallel": 2,
            "turbo_mode_enabled": True,
            "turbo_parallel_relay": 2,
            "video_prefetch_parallel": 2,
            "video_priority_parallel_relay": 2,
            "telegram_parallel_relay": 2,
        }
    else:
        common = {
            "parallel_relay": 3,
            "h2_connections": 3,
            "enable_sub_batch": True,
            "batch_window_micro": 0.015,
            "batch_window_macro": 0.12,
            "batch_max": 20,
            "chunked_download_chunk_size": _mb(2),
            "chunked_download_max_parallel": 4,
            "youtube_sabr_max_parallel": 2,
            "turbo_mode_enabled": True,
            "turbo_parallel_relay": 1,
            "video_prefetch_parallel": 2,
            "video_priority_parallel_relay": 1,
            "telegram_parallel_relay": 2,
        }

    return {
        "basic": common,
        "medium": _merge(common, {
            "relay_timeout": 150 if tier <= 2 else 120,
            "range_probe_timeout": 75 if tier <= 2 else 60,
            "h2_stream_timeout": 75 if tier <= 2 else 60,
            "batch_max": min(24, int(common.get("batch_max", 10)) + 4),
            "chunked_download_chunk_size": _mb(4 if tier >= 3 else 2),
            "max_response_body_bytes": _mb(512),
            "manifest_prefetch_next_segments": 3 if tier >= 3 else 2,
            "video_passthrough_relay_timeout": 180,
            "video_priority_parallel_relay": 2 if tier >= 3 else 1,
            "turbo_parallel_relay": 2 if tier >= 3 else 1,
        }),
        "ultra": _merge(common, {
            "relay_timeout": 180,
            "range_probe_timeout": 90,
            "h2_stream_timeout": 90,
            "batch_window_micro": 0.010 if tier >= 4 else 0.012,
            "batch_window_macro": 0.08 if tier >= 5 else 0.10,
            "batch_max": 28 if tier >= 5 else 26 if tier >= 4 else 24,
            "h2_connections": 4 if tier >= 4 else common.get("h2_connections", 1),
            "chunked_download_chunk_size": _mb(8 if tier >= 4 else 4),
            "chunked_download_max_parallel": 5 if tier >= 4 else 4 if tier >= 3 else 3 if tier == 2 else 2,
            "chunked_download_max_chunks": 1024 if tier >= 4 else 512,
            "max_response_body_bytes": _mb(1024 if tier >= 4 else 768 if tier == 3 else 512),
            "video_prefetch_next_ranges": 3 if tier >= 4 else 2 if tier >= 2 else 1,
            "manifest_prefetch_next_segments": 4 if tier >= 4 else 3 if tier >= 3 else 2,
            "manifest_prefetch_parallel": 2 if tier >= 5 else 1,
            "video_cache_max_mb": 768 if tier >= 4 else 512,
            "manifest_cache_max_mb": 768 if tier >= 4 else 512,
            "video_cache_ttl_seconds": 900 if tier >= 5 else 700,
            "manifest_cache_ttl_seconds": 900 if tier >= 5 else 700,
            "video_passthrough_relay_timeout": 240 if tier >= 5 else 180,
            "video_priority_retry_attempts": 3 if tier >= 5 else 2,
            "video_priority_retry_delay_ms": 100 if tier >= 5 else 120,
            "video_priority_parallel_relay": 3 if tier >= 5 else 2 if tier >= 4 else 1,
            "turbo_mode_enabled": True,
            "turbo_parallel_relay": 3 if tier >= 5 else 2 if tier >= 2 else 1,
        }),
        "download": _merge(common, {
            "relay_timeout": 180,
            "range_probe_timeout": 90,
            "h2_stream_timeout": 90,
            "parallel_relay": 1,
            "h2_connections": min(3, max(1, tier)),
            "enable_sub_batch": False,
            "batch_window_micro": 0.03 if tier <= 1 else 0.02,
            "batch_window_macro": 0.18 if tier <= 1 else 0.12,
            "batch_max": 6 if tier <= 1 else 8,
            "chunked_download_extensions": ["*"],
            "chunked_download_min_size": 10,
            "chunked_download_chunk_size": _mb(8 if tier >= 4 else 4 if tier >= 2 else 2),
            "chunked_download_max_parallel": 4 if tier >= 3 else 3 if tier == 2 else 2,
            "chunked_download_max_chunks": 1024 if tier >= 4 else 512,
            "max_response_body_bytes": _mb(1024 if tier >= 4 else 768 if tier == 3 else 512),
            "video_prefetch_enabled": False,
            "manifest_prefetch_enabled": False,
            "video_passthrough_enabled": False,
            "video_priority_retry_attempts": 1,
            "video_priority_retry_delay_ms": 300,
            "video_priority_parallel_relay": 1,
            "youtube_sabr_max_parallel": 2 if tier >= 2 else 1,
            "turbo_mode_enabled": False,
            "turbo_parallel_relay": 1,
            "turbo_skip_download_mode": True,
            "telegram_mode_enabled": False,
            "telegram_parallel_relay": 1,
        }),
    }


def build_runtime_profiles_for_script_count(count: int | str | None) -> dict[str, dict[str, Any]]:
    """
    Build runtime_profiles.json content for a script count or selected tier.

    Passing 5 means tier 5.
    Passing 9 also means tier 5.
    """
    tier = script_tier(count)
    base = _base_profile()
    tier_values = _tier_values(tier)

    names = {
        1: {
            "basic": "Basic Stable - 1 script ID",
            "medium": "Medium Balanced - 1 script ID",
            "ultra": "Ultra Safe - 1 script ID",
            "download": "Stable Download - 1 script ID",
        },
        2: {
            "basic": "Basic Stable - 2 script IDs",
            "medium": "Medium Balanced - 2 script IDs",
            "ultra": "Ultra Safe - 2 script IDs",
            "download": "Stable Download - 2 script IDs",
        },
        3: {
            "basic": "Basic Stable - 3 script IDs",
            "medium": "Medium Balanced - 3 script IDs",
            "ultra": "Ultra Max - 3 script IDs",
            "download": "Stable Download - 3 script IDs",
        },
        4: {
            "basic": "Basic Stable - 4 script IDs",
            "medium": "Medium Balanced - 4 script IDs",
            "ultra": "Ultra Max - 4 script IDs",
            "download": "Stable Download - 4 script IDs",
        },
        5: {
            "basic": "Basic Stable - 5+ script IDs",
            "medium": "Medium Balanced - 5+ script IDs",
            "ultra": "Ultra Max - 5+ script IDs",
            "download": "Stable Download - 5+ script IDs",
        },
    }

    profiles: dict[str, dict[str, Any]] = {}

    for mode in ("basic", "medium", "ultra", "download"):
        profiles[mode] = _merge(base, tier_values.get(mode, {}))
        profiles[mode]["label"] = names[tier][mode]

    return profiles


def apply_profile_to_config(config: dict[str, Any], profiles: dict[str, Any], mode: str | None = None) -> dict[str, Any]:
    """
    Apply selected runtime profile to config.

    Dashboard global switches survive:
    - h2_disabled_by_dashboard
    - turbo_disabled_by_dashboard
    - sabr_disabled_by_dashboard
    - video_prefetch_disabled_by_dashboard
    - manifest_prefetch_disabled_by_dashboard
    - video_passthrough_disabled_by_dashboard
    """
    cfg = deepcopy(config or {})

    selected_mode = str(mode or cfg.get("runtime_mode") or "basic").strip().lower()
    if selected_mode == "auto":
        profile_key = "basic"
    elif selected_mode in profiles:
        profile_key = selected_mode
    else:
        profile_key = "basic"

    old_flags = {
        "h2_disabled_by_dashboard": bool(cfg.get("h2_disabled_by_dashboard", False)),
        "turbo_disabled_by_dashboard": bool(cfg.get("turbo_disabled_by_dashboard", False)),
        "sabr_disabled_by_dashboard": bool(cfg.get("sabr_disabled_by_dashboard", False)),
        "video_prefetch_disabled_by_dashboard": bool(cfg.get("video_prefetch_disabled_by_dashboard", False)),
        "manifest_prefetch_disabled_by_dashboard": bool(cfg.get("manifest_prefetch_disabled_by_dashboard", False)),
        "video_passthrough_disabled_by_dashboard": bool(cfg.get("video_passthrough_disabled_by_dashboard", False)),
    }

    profile = deepcopy(profiles.get(profile_key) or profiles.get("basic") or {})
    cfg.update(profile)

    cfg["runtime_mode"] = selected_mode if selected_mode == "auto" else profile_key
    cfg["label"] = str(profile.get("label") or cfg.get("label") or profile_key)

    cfg.update(old_flags)

    if cfg.get("h2_disabled_by_dashboard"):
        before = int(profile.get("h2_connections") or cfg.get("h2_connections") or 1)
        cfg["_h2_connections_before_dashboard_off"] = max(1, before)
        cfg["h2_connections"] = 0

    if cfg.get("turbo_disabled_by_dashboard"):
        cfg["turbo_mode_enabled"] = False

    if cfg.get("sabr_disabled_by_dashboard"):
        cfg["youtube_sabr_booster_enabled"] = False

    if cfg.get("video_prefetch_disabled_by_dashboard"):
        cfg["video_prefetch_enabled"] = False

    if cfg.get("manifest_prefetch_disabled_by_dashboard"):
        cfg["manifest_prefetch_enabled"] = False

    if cfg.get("video_passthrough_disabled_by_dashboard"):
        cfg["video_passthrough_enabled"] = False

    return cfg


def optimize_config_for_script_count(config: dict[str, Any]) -> dict[str, Any]:
    """
    Optimize config by real script ID count.

    Used by setup wizard and fallback callers.
    """
    cfg = deepcopy(config or {})
    ids = normalize_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])

    cfg["script_ids"] = ids
    cfg.pop("script_id", None)

    real_count = len(ids)
    tier = script_tier(real_count)

    profiles = build_runtime_profiles_for_script_count(tier)
    cfg = apply_profile_to_config(cfg, profiles, cfg.get("runtime_mode") or "basic")

    cfg["mode"] = "apps_script"
    cfg["script_ids"] = ids
    cfg["script_count"] = real_count
    cfg["script_tier"] = tier
    cfg["optimized_for_script_count"] = tier
    cfg["optimized_at"] = _now()
    cfg["optimizer_version"] = OPTIMIZER_VERSION
    cfg["optimizer_locked_tiers"] = [x for x in range(1, 6) if x > tier]

    return cfg


def optimize_config_for_script_tier(
    config: dict[str, Any],
    selected_tier: int | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """
    Optimize config by selected dashboard tier.

    Example:
      real IDs = 5
      selected_tier = 2

    Result:
      script_count = 5
      script_tier = 2
      optimized_for_script_count = 2
      runtime profile = tier 2
    """
    cfg = deepcopy(config or {})
    ids = normalize_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])

    real_count = len(ids)
    max_available_tier = script_tier(real_count)
    tier = clamp_tier(selected_tier if selected_tier is not None else cfg.get("optimized_for_script_count"), max_available_tier)

    cfg["script_ids"] = ids
    cfg.pop("script_id", None)

    profiles = build_runtime_profiles_for_script_count(tier)
    cfg = apply_profile_to_config(cfg, profiles, mode or cfg.get("runtime_mode") or "basic")

    cfg["mode"] = "apps_script"
    cfg["script_ids"] = ids
    cfg["script_count"] = real_count
    cfg["script_tier"] = tier
    cfg["optimized_for_script_count"] = tier
    cfg["optimized_at"] = _now()
    cfg["optimizer_version"] = OPTIMIZER_VERSION
    cfg["optimizer_locked_tiers"] = [x for x in range(1, 6) if x > max_available_tier]

    return cfg


def write_runtime_profiles_for_script_count(count: int | str | None, path: Path | str | None = None) -> dict[str, Any]:
    """
    Write runtime_profiles.json based on script count.
    """
    tier = script_tier(count)
    profiles = build_runtime_profiles_for_script_count(tier)

    out_path = Path(path or RUNTIME_PROFILES_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return profiles


def write_runtime_profiles_for_script_tier(selected_tier: int | str | None, path: Path | str | None = None) -> dict[str, Any]:
    """
    Write runtime_profiles.json based on selected optimizer tier.
    """
    tier = clamp_tier(selected_tier, 5)
    profiles = build_runtime_profiles_for_script_count(tier)

    out_path = Path(path or RUNTIME_PROFILES_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return profiles


def optimize_and_write_runtime_profiles(config: dict[str, Any], path: Path | str | None = None) -> dict[str, Any]:
    """
    Write runtime_profiles.json from config.

    Priority:
    1. optimized_for_script_count
    2. script_tier
    3. real script ID count
    """
    cfg = config or {}
    ids = normalize_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])

    selected = (
        cfg.get("optimized_for_script_count")
        or cfg.get("script_tier")
        or len(ids)
        or 1
    )

    tier = clamp_tier(selected, 5)
    return write_runtime_profiles_for_script_tier(tier, path=path)


def optimizer_message(count: int | str | None, lang: str = "fa") -> str:
    """
    Human message for setup/dashboard.
    """
    try:
        n = int(count or 0)
    except Exception:
        n = 0

    tier = script_tier(n)
    fa = str(lang or "fa").lower().startswith("fa")

    if fa:
        return (
            f"کانفیگ مناسب برای {n} اسکریپت ID ثبت شد. "
            f"سطح بهینه‌سازی فعال: Tier {tier}. "
            "اگر بعداً تعداد IDها را تغییر دادی، از بخش Script Optimizer دوباره Apply Optimization را بزن."
        )

    return (
        f"Optimized config registered for {n} script ID(s). "
        f"Active optimization tier: Tier {tier}. "
        "If you change IDs later, use Script Optimizer and click Apply Optimization again."
    )


def optimizer_status_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Optional helper for dashboard/debug.
    Safe to call from anywhere.
    """
    cfg = config or {}
    ids = normalize_script_ids(cfg.get("script_ids") or cfg.get("script_id") or [])

    real_count = len(ids)
    max_tier = script_tier(real_count)

    try:
        active = int(cfg.get("optimized_for_script_count") or cfg.get("script_tier") or 0)
    except Exception:
        active = 0

    active = clamp_tier(active or max_tier, max_tier)

    tiers = []
    for n in range(1, 6):
        tiers.append({
            "tier": n,
            "label": f"Tier {n}",
            "description": "For 5 or more Script IDs" if n == 5 else f"For {n} Script ID" + ("" if n == 1 else "s"),
            "locked": n > max_tier,
            "available": n <= max_tier,
            "active": n == active,
        })

    return {
        "ok": True,
        "script_ids": ids,
        "script_count": real_count,
        "max_tier": max_tier,
        "max_available_tier": max_tier,
        "script_tier": active,
        "active_tier": active,
        "selected_tier": active,
        "optimized_for_script_count": active,
        "optimized_at": int(cfg.get("optimized_at") or 0),
        "optimizer_version": cfg.get("optimizer_version", OPTIMIZER_VERSION),
        "locked_tiers": [x for x in range(1, 6) if x > max_tier],
        "tiers": tiers,
        "label": cfg.get("label", "-"),
        "runtime_mode": cfg.get("runtime_mode", "-"),
    }


def _self_test() -> None:
    ids = ["AKfycb1", "AKfycb2", "AKfycb3", "AKfycb4", "AKfycb5"]
    cfg = {
        "runtime_mode": "basic",
        "script_ids": ids,
        "h2_disabled_by_dashboard": False,
    }

    out = optimize_config_for_script_count(cfg)
    assert out["script_count"] == 5
    assert out["script_tier"] == 5
    assert out["optimized_for_script_count"] == 5
    assert out["runtime_mode"] == "basic"
    assert out["parallel_relay"] >= 1

    out2 = optimize_config_for_script_tier(cfg, 2)
    assert out2["script_count"] == 5
    assert out2["script_tier"] == 2
    assert out2["optimized_for_script_count"] == 2

    profiles = build_runtime_profiles_for_script_count(5)
    for key in ("basic", "medium", "ultra", "download"):
        assert key in profiles
        assert "label" in profiles[key]

    print("[OK] config_optimizer self-test passed")


if __name__ == "__main__":
    _self_test()