"""Artist-level metadata: art movements, birth/death, birthplace, nationality.

Stored one JSON file per artist under library/.artists/<slug>.json, keyed by a slug
of the display name. Can be populated automatically from Wikidata (free, no key,
CC0 structured data) or edited by hand from the artist page."""
import json
import re
import time

from . import config
from .names import slugify, strip_diacritics
from .downloads.util import session, fetch_json

WD_API = "https://www.wikidata.org/w/api.php"

# Occupations (Wikidata QIDs) that mark a good painter/artist match.
_ARTIST_OCCUPATIONS = {
    "Q1028181",  # painter
    "Q3391743",  # visual artist
    "Q483501",   # artist
    "Q1281618",  # sculptor
    "Q329439",   # engraver
    "Q15296811", # draughtsperson
    "Q33231",    # photographer
    "Q644687",   # illustrator
}

FIELDS = ("name", "born", "died", "birthplace", "nationality", "movements",
          "description", "wikidata_id", "wikipedia_url", "source", "updated",
          "cover", "work_order", "work_order_pids")


def _path(name):
    return config.ARTIST_META_DIR / (slugify(name) + ".json")


def load(name):
    p = _path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(name, data):
    clean = {k: data.get(k) for k in FIELDS if data.get(k) not in (None, "", [])}
    # 'cover' and 'work_order' are managed separately (set_cover / set_order); a
    # bio or Wikidata save carries neither, so keep what's there rather than
    # dropping the chosen thumbnail or the hand-made hang with it.
    existing = None
    for kept in ("cover", "work_order"):
        if not clean.get(kept):
            if existing is None:
                existing = load(name) or {}
            if existing.get(kept):
                clean[kept] = existing[kept]
    clean["name"] = name
    clean["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(name).write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    return clean


def set_cover(name, work_id):
    """Set (or clear, when work_id is falsy) the artist's representative thumbnail.
    Written directly so existing bio fields are preserved untouched."""
    data = load(name) or {"name": name}
    if work_id:
        data["cover"] = work_id
    else:
        data.pop("cover", None)
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def set_order(name, work_ids):
    """Set (or clear, when falsy) the artist's hand-made hang on the box that
    AUTHORS it: the local work ids, in the arranged order. Written directly, like
    the cover, so bio fields are never disturbed. The public box doesn't author —
    it receives pids, see set_order_pids."""
    data = load(name) or {"name": name}
    ids = [w for w in (work_ids or []) if isinstance(w, str) and w]
    if ids:
        data["work_order"] = ids
    else:
        data.pop("work_order", None)
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def set_order_pids(name, pids):
    """The order as it ARRIVES on the public box: persistent publish ids, stored
    verbatim and resolved to this box's work ids only when the gallery is viewed
    (see apply_order). Storing pids rather than resolving them here is what makes
    a pull robust — the works don't have to be scannable at import time, only
    present by the time someone looks, which they always are."""
    data = load(name) or {"name": name}
    clean = [p for p in (pids or []) if isinstance(p, str) and p]
    if clean:
        data["work_order_pids"] = clean
    else:
        data.pop("work_order_pids", None)
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def apply_order(name, works):
    """The artist's works in the curator's hand order. Ordered ones come first,
    in their arranged sequence; anything the order doesn't name — a new download,
    a fresh upload — follows in the order it arrived. Entries that no longer
    resolve are simply skipped, so a deleted painting can't jam the hang.

    Two arrangements can be stored, and they key on different things: the private
    box holds `work_order` (its own work ids, since an unpublished work has no
    pid yet); the public box holds `work_order_pids` (the published ids, matched
    against each work's own pid at serve time). Pids win when both are present —
    a work is always present by the time it's viewed, so a pid never fails to
    resolve the way an import-time id could.

    Returns (works, arranged): `arranged` says an order actually applied, which
    is what tells the artist page to offer its reset."""
    info = load(name) or {}
    pid_order = info.get("work_order_pids") or []
    if pid_order:
        return _by_key(works, pid_order, lambda w: w.get("pid"))
    return _by_key(works, info.get("work_order") or [], lambda w: w["id"])


def _by_key(works, order, key_of):
    pos = {}
    for i, k in enumerate(order):
        if isinstance(k, str) and k not in pos:
            pos[k] = i
    named = [w for w in works if key_of(w) in pos]
    if not named:
        return works, False
    named.sort(key=lambda w: pos[key_of(w)])
    return named + [w for w in works if key_of(w) not in pos], True


def normalize_manual(payload):
    """Coerce a user-submitted form into a stored record."""
    movements = payload.get("movements")
    if isinstance(movements, str):
        movements = [m.strip() for m in movements.split(",") if m.strip()]
    return {
        "born": (payload.get("born") or "").strip(),
        "died": (payload.get("died") or "").strip(),
        "birthplace": (payload.get("birthplace") or "").strip(),
        "nationality": (payload.get("nationality") or "").strip(),
        "movements": movements or [],
        "description": (payload.get("description") or "").strip(),
        "wikidata_id": (payload.get("wikidata_id") or "").strip(),
        "wikipedia_url": (payload.get("wikipedia_url") or "").strip(),
        "source": "manual",
    }


# ---------------- Wikidata ----------------

def _year(claim_value):
    """Wikidata time value -> year string, respecting sign for BCE."""
    t = (claim_value or {}).get("time") or ""
    m = re.match(r"([+-])0*(\d+)-(\d{2})-(\d{2})", t)
    if not m:
        return ""
    year = int(m.group(2))
    return "%d BCE" % year if m.group(1) == "-" else str(year)


def _claim_ids(entity, prop):
    out = []
    for c in (entity.get("claims") or {}).get(prop, []):
        val = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {})
        if isinstance(val, dict) and "id" in val:
            out.append(val["id"])
    return out


def _claim_times(entity, prop):
    out = []
    for c in (entity.get("claims") or {}).get(prop, []):
        val = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {})
        if isinstance(val, dict) and "time" in val:
            out.append(val)
    return out


def _score(entity, query):
    occ = set(_claim_ids(entity, "P106"))
    instance = set(_claim_ids(entity, "P31"))
    label = (entity.get("labels", {}).get("en", {}) or {}).get("value", "")
    aliases = [a.get("value", "") for a in entity.get("aliases", {}).get("en", [])]
    names = [strip_diacritics(n).casefold() for n in [label] + aliases]
    q = strip_diacritics(query).casefold()

    score = 0
    if "Q5" in instance:                       # is a human
        score += 4
    if occ & _ARTIST_OCCUPATIONS:              # is an artist
        score += 5
    if q in names:
        score += 3
    elif any(q in n or n in q for n in names):
        score += 1
    if entity.get("claims", {}).get("P569"):   # has a birth date
        score += 1
    return score


def _best_entity(sess, name):
    """Search Wikidata for `name` and return the best-matching entity dict (a human
    who is an artist), or None. Shared by the bio lookup and the Wikidata downloader."""
    search = fetch_json(sess, WD_API, {
        "action": "wbsearchentities", "search": name, "language": "en",
        "type": "item", "limit": 7, "format": "json",
    })
    hits = search.get("search") or []
    if not hits:
        return None
    ids = [h["id"] for h in hits]
    ent = fetch_json(sess, WD_API, {
        "action": "wbgetentities", "ids": "|".join(ids),
        "props": "labels|aliases|descriptions|claims|sitelinks/urls",
        "languages": "en", "sitefilter": "enwiki", "format": "json",
    })
    entities = ent.get("entities") or {}
    best, best_score = None, 0
    for qid in ids:                            # keep search-rank order on ties
        e = entities.get(qid)
        if not e:
            continue
        s = _score(e, name)
        if s > best_score:
            best, best_score = e, s
    return best if best and best_score >= 4 else None


def resolve_qid(name):
    """Best-matching Wikidata (qid, canonical English label) for an artist name,
    or (None, None). Used by the Wikidata downloader to find an artist's works."""
    best = _best_entity(session(), name)
    if not best:
        return None, None
    label = (best.get("labels", {}).get("en", {}) or {}).get("value") or name
    return best.get("id"), label


def fetch_from_wikidata(name):
    """Look the artist up on Wikidata. Returns a record dict or None if no match."""
    sess = session()
    best = _best_entity(sess, name)
    if not best:                               # nothing that's even a matching human
        return None

    # Resolve referenced items (birthplace, citizenship, movements) to labels in one call.
    ref_ids = (_claim_ids(best, "P19") + _claim_ids(best, "P27") + _claim_ids(best, "P135"))
    labels = {}
    if ref_ids:
        seen = list(dict.fromkeys(ref_ids))
        lab = fetch_json(sess, WD_API, {
            "action": "wbgetentities", "ids": "|".join(seen[:40]),
            "props": "labels", "languages": "en", "format": "json",
        })
        for qid, e in (lab.get("entities") or {}).items():
            v = (e.get("labels", {}).get("en", {}) or {}).get("value")
            if v:
                labels[qid] = v

    born = _year(next(iter(_claim_times(best, "P569")), None))
    died = _year(next(iter(_claim_times(best, "P570")), None))
    birthplace = labels.get(next(iter(_claim_ids(best, "P19")), None), "")
    nationality = labels.get(next(iter(_claim_ids(best, "P27")), None), "")
    movements = [labels[q] for q in _claim_ids(best, "P135") if q in labels]

    qid = best.get("id")
    wiki = ((best.get("sitelinks") or {}).get("enwiki") or {}).get("url", "")
    desc = (best.get("descriptions", {}).get("en", {}) or {}).get("value", "")
    label = (best.get("labels", {}).get("en", {}) or {}).get("value", "") or name

    return {
        "matched_label": label,
        "born": born,
        "died": died,
        "birthplace": birthplace,
        "nationality": nationality,
        "movements": movements,
        "description": desc,
        "wikidata_id": qid,
        "wikipedia_url": wiki,
        "source": "wikidata",
    }
