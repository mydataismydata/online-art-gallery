"""User-defined download sources for JSON-API museums (Harvard, Rijksmuseum, …).

A definition describes how to search a museum's JSON API and where each field
lives in the response, and a single generic driver runs it like any built-in
source. Definitions live in custom_sources.json and are edited from Settings."""
import json
import re
import time
from urllib.parse import quote

from ... import config, library
from ...names import name_match, parse_year, slugify
from ..util import session, fetch_json, download_to_tmp

# ids that a custom source may not claim (would shadow a built-in)
RESERVED_IDS = {"gac", "met", "aic", "cma", "rijks", "wikidata", "vam"}

FIELD_KEYS = ("title", "artist", "date", "year", "medium", "style", "image", "id")


def _load_raw():
    p = config.CUSTOM_SOURCES_FILE
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print("custom_sources.json unreadable: %s" % e, flush=True)
        return []


def _write_raw(defs):
    config.CUSTOM_SOURCES_FILE.write_text(
        json.dumps(defs, ensure_ascii=False, indent=2), encoding="utf-8")


def validate(defn):
    """Return (ok, error, cleaned_def)."""
    if not isinstance(defn, dict):
        return False, "Definition must be an object.", None
    sid = slugify(defn.get("id") or defn.get("label") or "")
    if not sid:
        return False, "An id or label is required.", None
    if sid in RESERVED_IDS:
        return False, "'%s' is a built-in source id; choose another." % sid, None
    label = (defn.get("label") or sid).strip()
    search_url = (defn.get("search_url") or "").strip()
    if "{query}" not in search_url:
        return False, "Search URL must contain the {query} placeholder.", None
    if not search_url.lower().startswith(("http://", "https://")):
        return False, "Search URL must start with http:// or https://.", None
    fields = defn.get("fields") or {}
    if not (fields.get("image") or "").strip():
        return False, "The image field mapping is required (path to each item's image URL).", None

    cleaned = {
        "id": sid,
        "label": label,
        "hint": (defn.get("hint") or "").strip() or ("Custom source: %s" % label),
        "placeholder": (defn.get("placeholder") or "Artist name").strip(),
        "search_url": search_url,
        "items_path": (defn.get("items_path") or "").strip(),
        "fields": {k: (fields.get(k) or "").strip() for k in FIELD_KEYS},
        "artist_filter": bool(defn.get("artist_filter", True)),
        "page_start": int(defn.get("page_start", 1) or 0),
        "max_pages": max(1, min(int(defn.get("max_pages", 10) or 10), 100)),
    }
    return True, "", cleaned


def list_defs():
    return _load_raw()


def get_def(sid):
    for d in _load_raw():
        if d.get("id") == sid:
            return d
    return None


def upsert(defn):
    ok, err, cleaned = validate(defn)
    if not ok:
        raise ValueError(err)
    defs = [d for d in _load_raw() if d.get("id") != cleaned["id"]]
    defs.append(cleaned)
    defs.sort(key=lambda d: d["label"].casefold())
    _write_raw(defs)
    return cleaned


def remove(sid):
    defs = _load_raw()
    kept = [d for d in defs if d.get("id") != sid]
    _write_raw(kept)
    return len(kept) != len(defs)


def _dig(obj, path):
    """Follow a dotted path with numeric list indices: 'people.0.name'."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _records(data, defn):
    recs = _dig(data, defn.get("items_path", ""))
    return recs if isinstance(recs, list) else None


def _map(rec, defn):
    f = defn["fields"]
    def val(key):
        v = _dig(rec, f.get(key, "")) if f.get(key) else None
        return v if v is None else str(v).strip()
    date = val("date")
    year = val("year")
    return {
        "title": val("title") or "Untitled",
        "artist": val("artist") or "",
        "date": date,
        "year": int(year) if (year or "").isdigit() else parse_year(date),
        "medium": val("medium"),
        "style": val("style"),
        "image": _dig(rec, f.get("image", "")),
        "sid": val("id"),
    }


def dry_run(defn, query):
    """Fetch page 1 and report what the mapping would yield — no downloads."""
    ok, err, cleaned = validate(defn)
    if not ok:
        return {"ok": False, "error": err}
    sess = session()
    url = (cleaned["search_url"].replace("{query}", quote(query))
           .replace("{page}", str(cleaned["page_start"])))
    try:
        data = fetch_json(sess, url, timeout=45)
    except Exception as e:
        return {"ok": False, "error": "Request failed: %s" % e, "url": url}
    recs = _records(data, cleaned)
    if recs is None:
        top = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        return {"ok": False, "url": url,
                "error": "items_path did not resolve to a list. Top-level keys: %s" % (top,)}
    sample, with_image, matched = [], 0, 0
    for rec in recs[:60]:
        m = _map(rec, cleaned)
        if m["image"]:
            with_image += 1
        passes = bool(m["image"]) and (
            not cleaned["artist_filter"] or not m["artist"] or name_match(query, m["artist"]))
        if passes:
            matched += 1
        if len(sample) < 6:
            sample.append({"title": m["title"], "artist": m["artist"],
                           "image": bool(m["image"]), "passes": passes})
    return {"ok": True, "url": url, "records": len(recs),
            "with_image": with_image, "matched": matched, "sample": sample}


def run_custom(job, defn):
    ok, err, cleaned = validate(defn)
    if not ok:
        raise RuntimeError("Source misconfigured: %s" % err)
    sess = session()
    q = job.query.strip()
    max_items = job.opts.get("max_items")
    templated = "{page}" in cleaned["search_url"]
    page = cleaned["page_start"]

    for pnum in range(cleaned["max_pages"]):
        if job.cancelled:
            return
        url = cleaned["search_url"].replace("{query}", quote(q)).replace("{page}", str(page))
        try:
            data = fetch_json(sess, url, timeout=45)
        except Exception as e:
            if pnum == 0:
                raise
            job.log("Page %d request failed (%s); stopping." % (page, e))
            break
        recs = _records(data, cleaned)
        if recs is None:
            raise RuntimeError("items_path '%s' did not resolve to a list."
                               % cleaned.get("items_path"))
        if pnum == 0:
            job.log("%s: %d records on the first page; filtering…" % (cleaned["label"], len(recs)))
        if not recs:
            break

        for rec in recs:
            if job.cancelled:
                return
            m = _map(rec, cleaned)
            if not m["image"]:
                continue
            if cleaned["artist_filter"] and m["artist"] and not name_match(q, m["artist"]):
                continue
            job.found += 1
            if m["sid"] and library.source_exists(cleaned["id"], m["sid"]):
                job.skipped += 1
                continue
            meta = {
                "title": m["title"], "date": m["date"], "year": m["year"],
                "medium": m["medium"], "style": m["style"], "type": "painting",
                "source": cleaned["id"], "source_id": m["sid"], "source_url": None,
            }
            try:
                tmp = download_to_tmp(sess, str(m["image"]))
            except Exception as e:
                job.failed += 1
                job.log("FAILED \"%s\": %s" % (m["title"], e))
                continue
            path = library.save_work(m["artist"] or cleaned["label"], meta, tmp, job)
            job.saved += 1
            job.log("Saved: %s" % path.name)
            if max_items and job.saved >= max_items:
                job.log("Reached the requested maximum of %d works." % max_items)
                return
            time.sleep(0.5)

        if not templated:
            break
        page += 1
        time.sleep(0.4)


class CustomSource:
    """Adapts a definition dict to the same duck-typed interface as a built-in
    source module (ID/LABEL/HINT/PLACEHOLDER/run/availability)."""
    custom = True
    SUPPORTS_MAX_PX = False
    MAX_PX_DEFAULT = None

    def __init__(self, defn):
        self._defn = defn
        self.ID = defn["id"]
        self.LABEL = defn.get("label") or defn["id"]
        self.HINT = defn.get("hint") or ""
        self.PLACEHOLDER = defn.get("placeholder") or "Artist name"

    def availability(self):
        ok, err, _ = validate(self._defn)
        return ok, ("" if ok else err)

    def run(self, job):
        run_custom(job, self._defn)


def build_sources():
    """Instantiate a CustomSource for each stored, valid definition."""
    out = []
    for d in _load_raw():
        ok, _, cleaned = validate(d)
        if ok:
            out.append(CustomSource(cleaned))
    return out


# Starting-point templates surfaced in the Settings UI. Users paste their own key.
PRESETS = [
    {
        "label": "Harvard Art Museums",
        "note": "Free API key from harvardartmuseums.org/collections/api — replace YOUR_KEY.",
        "def": {
            "id": "harvard",
            "label": "Harvard Art Museums",
            "hint": "Harvard Art Museums API — paintings with images.",
            "search_url": ("https://api.harvardartmuseums.org/object?apikey=YOUR_KEY"
                           "&classification=Paintings&hasimage=1&size=100&q={query}&page={page}"),
            "items_path": "records",
            "fields": {"title": "title", "artist": "people.0.name", "date": "dated",
                       "year": "datebegin", "medium": "medium", "style": "",
                       "image": "primaryimageurl", "id": "objectid"},
            "artist_filter": True, "page_start": 1, "max_pages": 10,
        },
    },
    # The Rijksmuseum preset was removed in 2026: they retired the keyed
    # collection API (www.rijksmuseum.nl/api/... now returns HTTP 410 Gone).
    # The replacement Search API (data.rijksmuseum.nl/search/collection) is
    # keyless but returns Linked-Art identifiers that must each be resolved and
    # then fetched from Micrio IIIF — a multi-step flow the generic driver can't
    # express. It needs a dedicated built-in source instead.
]
