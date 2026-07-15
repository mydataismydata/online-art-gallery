"""Site branding: the owner-set wordmark shown in the browser tab and header.

Two tiers, matching the museum wordmark: an optional small-caps eyebrow (the
collector's name) sitting over the title proper. The eyebrow is decoration only —
the title is what names the tab.

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


def _save(data):
    config.SITE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _clean(s, limit):
    return re.sub(r"\s+", " ", (s or "").strip())[:limit]


def get_title():
    return (_load().get("title") or "").strip() or DEFAULT_TITLE


def set_title(title):
    """Set (or clear, reverting to the default) the site title. Returns the effective title."""
    clean = _clean(title, 80)
    data = _load()
    if clean and clean != DEFAULT_TITLE:
        data["title"] = clean
    else:
        data.pop("title", None)
    _save(data)
    return get_title()


def get_eyebrow():
    """The line above the title in the header wordmark. Empty = don't render one."""
    return (_load().get("eyebrow") or "").strip()


def set_eyebrow(text):
    clean = _clean(text, 40)
    data = _load()
    if clean:
        data["eyebrow"] = clean
    else:
        data.pop("eyebrow", None)
    _save(data)
    return get_eyebrow()


def get_featured():
    """The owner's pinned hero work as {"id": …, "pid": …}, or None for the daily
    rotation. Two keys because neither alone is durable: a work id is the hash of
    its path, so repointing the painting to another artist moves the file and
    changes it — and a pid only exists once the work has been published."""
    f = _load().get("featured")
    return f if isinstance(f, dict) and f.get("id") else None


def set_featured(work_id, pid=None):
    """Pin a work to the hero, or unpin with a falsy work_id."""
    data = _load()
    if work_id:
        rec = {"id": work_id}
        if pid:
            rec["pid"] = pid
        data["featured"] = rec
    else:
        data.pop("featured", None)
    _save(data)
    return get_featured()


def remap_featured(id_map):
    """Follow the hero pin when its painting moves and is therefore re-identified —
    a repoint into another artist, or an artist edit. Without this the pin would
    silently lapse back to the rotation and look like the setting hadn't stuck."""
    f = get_featured()
    if not f:
        return
    new = (id_map or {}).get(f["id"])
    if new and new != f["id"]:
        set_featured(new, f.get("pid"))


def get_short():
    """A shorter title for narrow screens, where the full one eats the whole width.
    Empty = fall back to the full title. Never names the tab — that stays the real
    title, which is what a bookmark should say."""
    return (_load().get("short") or "").strip()


def set_short(text):
    clean = _clean(text, 40)
    data = _load()
    if clean:
        data["short"] = clean
    else:
        data.pop("short", None)
    _save(data)
    return get_short()
