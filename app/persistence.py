import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA = (
    "z czym ludzie mają największe zmagania, "
    "jakiej szukają pomocy, "
    "z jakimi problemami mierzą się na codzień"
)

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

SETTINGS_FILE = Path("settings.json")
COOKIES_FILE = Path(".fb_session.json")

_DEFAULT_SETTINGS: dict = {
    "group_url": "",
    "email": "",
    "save_session": True,
    "max_posts": 100,
    "top_n": 20,
    "criteria_description": DEFAULT_CRITERIA,
    "custom_keywords": "",
    "gemini_api_key": "",
    "headless": True,
    "scroll_wait_ms": 1500,
    "per_post_timeout": 5,
    "enrich_total_timeout": 60,
    "model": "gemini-2.0-flash",
}


def load_settings() -> dict:
    """Load settings from file, falling back to defaults for missing keys."""
    if not SETTINGS_FILE.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        merged = dict(_DEFAULT_SETTINGS)
        merged.update({k: v for k, v in saved.items() if k in _DEFAULT_SETTINGS})
        return merged
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(**kwargs) -> None:
    """Persist one or more settings to file. Unknown keys are ignored."""
    current = load_settings()
    for k, v in kwargs.items():
        if k in _DEFAULT_SETTINGS:
            current[k] = v
    try:
        SETTINGS_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Group history (groups_history.json)
# ---------------------------------------------------------------------------

GROUPS_HISTORY_FILE = Path("groups_history.json")


def load_history() -> list[dict]:
    """Return list of {name, url} dicts, newest first."""
    if not GROUPS_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(GROUPS_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_to_history(url: str) -> None:
    """Derive group name from URL and prepend to history (no duplicates)."""
    url = url.strip().rstrip("/")
    # Derive a human-readable name from the URL slug
    slug = url.split("/groups/")[-1].split("/")[0] if "/groups/" in url else url.split("/")[-1]
    name = slug.replace("-", " ").replace("_", " ").title() or url
    history = load_history()
    # Remove existing entry for same URL
    history = [h for h in history if h["url"] != url]
    history.insert(0, {"name": name, "url": url})
    # Keep at most 20 entries
    history = history[:20]
    GROUPS_HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def history_choices() -> list[str]:
    """Return display strings for the dropdown."""
    return [f"{h['name']} — {h['url']}" for h in load_history()]


def url_from_choice(choice: str) -> str:
    """Extract URL from a dropdown choice string."""
    if choice and " — " in choice:
        return choice.split(" — ", 1)[1]
    return choice


# ---------------------------------------------------------------------------
# Presets history (presets.json) — for criteria and keywords
# ---------------------------------------------------------------------------

PRESETS_FILE = Path("presets.json")


def load_presets(key: str) -> list[str]:
    """Return saved preset strings for a given key (e.g. 'criteria', 'keywords')."""
    if not PRESETS_FILE.exists():
        return []
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        return data.get(key, [])
    except Exception:
        return []


def save_preset(key: str, value: str) -> None:
    """Prepend value to presets[key], deduplicate, keep at most 15."""
    value = value.strip()
    if not value:
        return
    data: dict = {}
    if PRESETS_FILE.exists():
        try:
            data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing = data.get(key, [])
    existing = [v for v in existing if v != value]  # remove duplicate
    existing.insert(0, value)
    data[key] = existing[:15]
    PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
