import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES_FILE = ROOT / "runtime_profiles.json"

ALIASES = {
    "1": "video",
    "video": "video",
    "youtube": "video",
    "yt": "video",

    "2": "basic",
    "basic": "basic",
    "browser": "basic",

    "3": "download",
    "download": "download",
    "dl": "download",
}

def load_profiles():
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def apply_runtime_profile(config: dict, mode: str) -> str:
    profiles = load_profiles()

    key = ALIASES.get(str(mode or "basic").strip().lower(), "basic")
    profile = profiles.get(key) or profiles.get("basic") or profiles.get("basic") or next(iter(profiles.values()))

    for k, v in profile.items():
        if k == "label":
            continue
        config[k] = v

    config["runtime_mode"] = key
    return profile.get("label", key)
