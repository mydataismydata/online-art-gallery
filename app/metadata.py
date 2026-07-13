"""Best-effort metadata lookup for a single work, behind the 'Find metadata'
batch action. Matches the artwork on Wikidata by title + artist (only accepting a
painting/artwork whose creator matches), then reads the field from the English
Wikipedia infobox, falling back to Wikidata's 'material used' property.

Only 'medium' is implemented so far; find_fields() is the extension point.
"""
import re

import requests

from . import config, artistinfo

_UA = config.USER_AGENT + " (+metadata lookup)"
_S = requests.Session()
_S.headers.update({"User-Agent": _UA})
_TIMEOUT = 25

# painting (Q3305213) plus a few artwork classes we accept as a match
_ART_TYPES = {"Q3305213", "Q838948", "Q11060274", "Q93184", "Q179700"}
_entity_cache = {}


def _get(url, params=None):
    try:
        r = _S.get(url, params=params, timeout=_TIMEOUT)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _tokens(s):
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}


def _entity(qid):
    if qid in _entity_cache:
        return _entity_cache[qid]
    r = _get("https://www.wikidata.org/wiki/Special:EntityData/%s.json" % qid)
    ent = None
    if r:
        try:
            ent = (r.json().get("entities") or {}).get(qid)
        except ValueError:
            ent = None
    _entity_cache[qid] = ent
    return ent


def _claim_ids(ent, prop):
    out = []
    for c in (ent.get("claims") or {}).get(prop, []):
        try:
            out.append(c["mainsnak"]["datavalue"]["value"]["id"])
        except (KeyError, TypeError):
            pass
    return out


_artist_qid_cache = {}


def _artist_qid(artist):
    """Resolve the artist name to a Wikidata QID once (cached), reusing the app's
    painter-preferring resolver — matching creators by QID is far more reliable
    than by label, which sometimes doesn't resolve."""
    if not artist:
        return None
    key = artist.strip().lower()
    if key not in _artist_qid_cache:
        try:
            qid, _ = artistinfo.resolve_qid(artist)
        except Exception:
            qid = None
        _artist_qid_cache[key] = qid
    return _artist_qid_cache[key]


def _search_hits(query):
    r = _get("https://www.wikidata.org/w/api.php", {
        "action": "wbsearchentities", "search": query, "language": "en",
        "format": "json", "type": "item", "limit": 6})
    if not r:
        return []
    try:
        return (r.json().get("search") or [])[:6]
    except ValueError:
        return []


def match_entity(title, artist):
    """A Wikidata item matching the title whose creator matches the artist, or None."""
    if not title:
        return None
    aqid = _artist_qid(artist)
    artist_tokens = _tokens(artist)
    # Museum titles often carry a parenthetical subtitle — also try it stripped.
    queries = [title]
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    if stripped and stripped != title:
        queries.append(stripped)

    for query in queries:
        for hit in _search_hits(query):
            ent = _entity(hit["id"])
            if not ent or not (set(_claim_ids(ent, "P31")) & _ART_TYPES):
                continue
            creators = set(_claim_ids(ent, "P170"))
            if aqid:
                if aqid in creators:
                    return ent
                continue  # we know the artist's QID; a different creator ⇒ wrong work
            # no QID for the artist — fall back to matching a creator's label
            for cq in creators:
                ce = _entity(cq)
                label = (((ce or {}).get("labels") or {}).get("en") or {}).get("value", "")
                if artist_tokens and artist_tokens & _tokens(label):
                    return ent
    return None


_SUPPORTS = {"canvas", "panel", "wood", "paper", "cardboard", "copper", "board",
             "oak", "poplar", "masonite", "linen", "vellum", "parchment", "ivory"}
_PAINTS = {"tempera", "watercolor", "watercolour", "gouache", "ink", "pastel",
           "chalk", "charcoal", "acrylic", "fresco", "encaustic", "enamel"}


def _clean_wikitext(v):
    v = re.sub(r"(?is)<ref[^>]*>.*?</ref>", "", v)
    v = re.sub(r"(?is)<ref[^>]*/>", "", v)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)                    # simple templates
    v = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", v)      # [[a|b]] -> b
    v = re.sub(r"\[\[([^\]]*)\]\]", r"\1", v)               # [[a]] -> a
    v = v.replace("'''", "").replace("''", "")
    v = re.sub(r"<[^>]+>", "", v)
    return re.sub(r"\s+", " ", v).strip(" .;,")


def _medium_from_wikipedia(ent):
    title = (((ent.get("sitelinks") or {}).get("enwiki")) or {}).get("title")
    if not title:
        return None
    r = _get("https://en.wikipedia.org/w/api.php", {
        "action": "parse", "page": title, "prop": "wikitext", "section": "0",
        "format": "json", "formatversion": "2", "redirects": "1"})
    if not r:
        return None
    try:
        wt = (r.json().get("parse") or {}).get("wikitext") or ""
    except ValueError:
        return None
    m = re.search(r"(?im)^\s*\|\s*medium\s*=\s*([^\n]+)$", wt)
    if not m:
        return None
    med = _clean_wikitext(m.group(1))
    # "Oil on Canvas" -> "Oil on canvas" (Wikipedia links capitalise the support)
    med = re.sub(r"^([A-Za-z][\w ]*? on )([A-Z][a-z]+)$", lambda x: x.group(1) + x.group(2).lower(), med)
    return med if 2 < len(med) < 120 else None


def _medium_from_wikidata(ent):
    labels = []
    for mq in _claim_ids(ent, "P186"):
        me = _entity(mq)
        lbl = (((me or {}).get("labels") or {}).get("en") or {}).get("value")
        if lbl:
            labels.append(lbl.strip())
    if not labels:
        return None
    paints, supports = [], []
    for l in labels:
        ll = l.lower()
        if ll in _SUPPORTS:
            supports.append(l)
        elif "paint" in ll or ll in _PAINTS:
            paints.append(l)
    if paints and supports:
        p = paints[0].replace(" paint", "").strip()
        return "%s on %s" % (p[:1].upper() + p[1:], supports[0].lower())
    joined = ", ".join(labels[:3])
    return joined[:1].upper() + joined[1:] if joined else None


def find_medium(work):
    ent = match_entity(work.get("title"), work.get("artist"))
    if not ent:
        return None, None
    med = _medium_from_wikipedia(ent)
    if med:
        return med, "wikipedia"
    med = _medium_from_wikidata(ent)
    if med:
        return med, "wikidata"
    return None, None


def find_fields(work, fields):
    """Return {field: value} for the requested fields that could be found."""
    found = {}
    if "medium" in fields:
        med, _prov = find_medium(work)
        if med:
            found["medium"] = med
    return found
