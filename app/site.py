"""Site branding: the owner-set title shown in the browser tab and header.

Stored per-instance in data/site.json so the public snapshot can carry a
different name from the local box. Falls back to DEFAULT_TITLE when unset."""
import json
import re

from . import config

DEFAULT_TITLE = "The Gallery"


def _load():
    try:
        data = json.loads(config.SITE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {}


def get_title():
    return (_load().get("title") or "").strip() or DEFAULT_TITLE


def set_title(title):
    """Set (or clear, reverting to the default) the site title. Returns the effective title."""
    clean = re.sub(r"\s+", " ", (title or "").strip())[:80]
    data = _load()
    if clean and clean != DEFAULT_TITLE:
        data["title"] = clean
    else:
        data.pop("title", None)
    config.SITE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return get_title()
