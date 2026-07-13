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
          "description", "wikidata_id", "wikipedia_url", "source", "updated")


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
    clean["name"] = name
    clean["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _path(name).write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    return clean


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
