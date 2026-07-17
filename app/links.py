"""Connections: how the painters in this museum relate to one another.

Two families of link, and within the derived one, two tiers.

*Hand-written* — influence and curator notes — are stored in data/artist_links.json,
carry a human sentence, and are always drawn.

*Derived* are computed from what's on disk (bios + the works' style/genre/school),
never stored, so they follow the library: fix a bio and the graph fixes itself.
They split by how much weight the evidence carries:

  PRIMARY — a shared FIRST bio movement. A painter's own school is the strongest
  thing their bio asserts, so this is always drawn, whole, ungated.

  SECONDARY — a looser tie: a shared painting style, a shared painting school, a
  movement past the first in the bio, or simply the same place and time. These are
  gated on the works the museum actually holds: two painters connect only if their
  works share a nationality AND overlap in year. That gate is the whole point — it
  is what keeps an Australian and an American who both merely wrote "Impressionism"
  apart. Same word, different wall.

Within a group every qualifying pair is drawn — a clique, not a chain. An earlier
version threaded each group as a chain in birth order to keep the edge count down;
that was a mistake, because birth-order neighbours who share nothing else (a
Heidelberg painter and an American Impressionist born five years apart) came out
wired together. The map now draws the honest, denser graph and lets the reader thin
it with the type toggles. A pair still keeps only its ONE strongest link (_PRECEDENCE).

Artists are keyed by display name, casefolded for comparison, because that is what
identifies an artist everywhere else in this app. rename() keeps stored links
attached when a painter is renamed or merged.
"""
import json
import math
import secrets
import time
from collections import Counter, defaultdict

from . import config, library, artistinfo
from .names import fold

# Strongest first: a pair keeps only its best link, so a curator's sentence is
# never buried under "both were Dutch". `style` (the secondary movement/style tie)
# sits below the primary `movement` and above the bare `place_time`.
_PRECEDENCE = ("curator", "influence", "movement", "style", "place_time", "subject")
TYPES = _PRECEDENCE
HAND_TYPES = ("influence", "curator")

# The primary tier — always drawn, never gated by place or time.
PRIMARY_TYPES = ("curator", "influence", "movement")

# What the map is willing to draw. Subject alone is left off (it's a genre haze
# that says little); it stays on an artist's own page, where a shared subject is
# one card worth the mention.
MAP_TYPES = ("curator", "influence", "movement", "style", "place_time")

TYPE_META = {
    "movement":   {"label": "Movement",     "color": "#7f96ad",
                   "desc": "Their first-listed movement is the same — the strongest tie a bio makes."},
    "style":      {"label": "Style",        "color": "#9aa9bd",
                   "desc": "A looser tie — a shared painting style, school, or later movement, "
                           "between painters of one place and time."},
    "influence":  {"label": "Influence",    "color": "#c2a061",
                   "desc": "One painter shaped another — teachers, champions, models."},
    "place_time": {"label": "Place & time", "color": "#7fa389",
                   "desc": "Same nationality, overlapping working years — nothing more specific."},
    "subject":    {"label": "Subject",      "color": "#a884a3",
                   "desc": "The same subjects, seen differently."},
    "curator":    {"label": "Curator note", "color": "#bf7e63",
                   "desc": "Hand-written links with a note attached."},
}

CANVAS_W, CANVAS_H = 1600, 780
_MARGIN_X, _MARGIN_Y = 150, 120


# A note is a curator's prose — a few sentences, with the same small set of marks
# a placard allows.
MAX_NOTE = 2000
note_text = library.text_of


def _key(name):
    # Folded, not just casefolded: a stored link was written under whatever the
    # canonical spelling was that day, and the library now prefers the accented one.
    # Keying on the fold means a curator's note survives the painter gaining an é.
    return fold((name or "").strip())


def _pair_key(a, b):
    return tuple(sorted([_key(a), _key(b)]))


# ---------------- stored (hand-written) links ----------------

def _load():
    try:
        data = json.loads(config.LINKS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("links"), list):
            return data["links"]
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return []


def _save(links):
    config.LINKS_FILE.write_text(
        json.dumps({"links": links}, ensure_ascii=False, indent=1), encoding="utf-8")


def stored_links():
    return _load()


def create_link(a, b, type_, note, directed=False, created_by=None):
    """Add a hand-written link. Raises ValueError on bad input."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        raise ValueError("Pick two artists.")
    if _key(a) == _key(b):
        raise ValueError("An artist can't be linked to themselves.")
    if type_ not in HAND_TYPES:
        raise ValueError("Only influence and curator links are written by hand.")
    note = (note or "").strip()
    text = note_text(note)
    if type_ == "curator" and not text:
        raise ValueError("A curator link needs a note — that's the whole point of it.")
    if len(text) > MAX_NOTE:
        raise ValueError("Keep the note under %d characters." % MAX_NOTE)
    if not text:
        note = ""            # an editor left alone hands back <br>; store nothing

    links = _load()
    for l in links:
        # One link per pair, not one per pair-and-type: the graph only ever draws a
        # pair's strongest link, so a second one would exist but never appear — and
        # you can't remove what you can't see.
        if _pair_key(l["a"], l["b"]) == _pair_key(a, b):
            raise ValueError("Those two are already linked. Remove that link first "
                             "if you want to change what it says.")
    rec = {
        "id": secrets.token_hex(6),
        "a": a, "b": b, "type": type_, "note": note,
        "directed": bool(directed) and type_ == "influence",
        "created_by": created_by,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    links.append(rec)
    _save(links)
    return rec


def update_link(link_id, note=None, directed=None):
    links = _load()
    for l in links:
        if l["id"] == link_id:
            if note is not None:
                note = note.strip()
                text = note_text(note)
                if l["type"] == "curator" and not text:
                    raise ValueError("A curator link needs a note.")
                if len(text) > MAX_NOTE:
                    raise ValueError("Keep the note under %d characters." % MAX_NOTE)
                l["note"] = note if text else ""
            if directed is not None and l["type"] == "influence":
                l["directed"] = bool(directed)
            _save(links)
            return l
    raise LookupError("No such link.")


def delete_link(link_id):
    links = _load()
    rest = [l for l in links if l["id"] != link_id]
    if len(rest) == len(links):
        raise LookupError("No such link.")
    _save(rest)


def get_link(link_id):
    return next((l for l in _load() if l["id"] == link_id), None)


def import_published(recs):
    """Take the links published from the private box, keyed by their own id.

    Links written *here* are left alone, with one exception: a published link owns
    its pair, so a local link between the same two painters is dropped to make room.
    That reads as harsh until you look at the alternative — the graph only ever draws
    a pair's strongest link, so keeping both would leave one of them invisible and
    therefore impossible to remove. These are authored on the private box; it wins.

    Returns {"added", "updated", "unchanged"}."""
    cur = _load()
    by_id = {l.get("id"): l for l in cur if l.get("id")}
    stats = {"added": 0, "updated": 0, "unchanged": 0}

    incoming, claimed = {}, set()
    for r in recs or []:
        lid = (r.get("id") or "").strip()
        a, b = (r.get("a") or "").strip(), (r.get("b") or "").strip()
        if not lid or not a or not b or _key(a) == _key(b):
            continue
        if r.get("type") not in HAND_TYPES:
            continue
        incoming[lid] = {
            "id": lid, "a": a, "b": b, "type": r["type"],
            "note": (r.get("note") or "").strip()[:600],
            "directed": bool(r.get("directed")) and r["type"] == "influence",
            "created_by": r.get("created_by"), "created": r.get("created"),
            "source": "published",
        }
        claimed.add(_pair_key(a, b))

    out = [l for l in cur
           if l.get("id") not in incoming and _pair_key(l["a"], l["b"]) not in claimed]
    dropped = len(cur) - len(out) - sum(1 for i in incoming if i in by_id)
    for lid, rec in incoming.items():
        old = by_id.get(lid)
        if old and all(old.get(k) == rec[k] for k in ("a", "b", "type", "note", "directed")):
            stats["unchanged"] += 1
        else:
            stats["updated" if old else "added"] += 1
        out.append(rec)
    if stats["added"] or stats["updated"] or dropped:
        _save(out)
    return stats


def rename(old, new):
    """Keep hand-written links attached when an artist is renamed or merged into
    another. Called from the rename route — without it a repoint would silently
    orphan every curator note about that painter."""
    old_k = _key(old)
    links, touched = _load(), False
    for l in links:
        if _key(l["a"]) == old_k:
            l["a"], touched = new, True
        if _key(l["b"]) == old_k:
            l["b"], touched = new, True
    # A merge can leave a link pointing at itself (A→B where A became B), and a
    # pair may now be duplicated. Drop both rather than draw a loop.
    out, seen = [], set()
    for l in links:
        if _key(l["a"]) == _key(l["b"]):
            touched = True
            continue
        sig = _pair_key(l["a"], l["b"]) + (l["type"],)
        if sig in seen:
            touched = True
            continue
        seen.add(sig)
        out.append(l)
    if touched:
        _save(out)
    return out


# ---------------- artist profiles ----------------

def _profiles():
    """One record per artist in the library, merging works + bio."""
    by_artist = defaultdict(list)
    for w in library.all_works():
        by_artist[w["artist"]].append(w)

    out = {}
    for name, ws in by_artist.items():
        info = artistinfo.load(name) or {}
        years = sorted(w["year"] for w in ws if w.get("year"))
        movements = [m.strip() for m in (info.get("movements") or []) if m.strip()]
        styles = _common(ws, "style")
        genres = _common(ws, "genre")
        schools = _common(ws, "school")
        # The map clusters on ONE label per painter. A curated bio movement is the
        # best evidence; failing that the style they're most often filed under.
        primary = (movements[0] if movements
                   else styles[0] if styles
                   else schools[0] if schools
                   else (info.get("nationality") or "").strip() or "Unplaced")
        out[_key(name)] = {
            "name": name,
            "works": len(ws),
            "cover": library.cover_id(name, ws),
            "born": _year_int(info.get("born")),
            "died": _year_int(info.get("died")),
            "born_raw": (info.get("born") or "").strip(),
            "died_raw": (info.get("died") or "").strip(),
            "nationality": (info.get("nationality") or "").strip(),
            "birthplace": (info.get("birthplace") or "").strip(),
            "movements": movements,
            "styles": styles,
            "genres": genres,
            "schools": schools,
            "primary": primary,
            "year_min": years[0] if years else None,
            "year_max": years[-1] if years else None,
        }
    _canon_primaries(out)
    return out


def _canon_primaries(profiles):
    """Collapse case-variants of a movement to one spelling.

    Bios don't agree on capitalisation — Wikidata hands back "realism" where a
    curator typed "Realism" — and the map groups clusters by this string. Left
    alone, that draws two clusters with the same name and splits a school in half.
    Most-used spelling wins; ties break alphabetically so the pick is stable."""
    variants = defaultdict(Counter)
    for p in profiles.values():
        variants[p["primary"].casefold()][p["primary"]] += 1
    canon = {cf: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
             for cf, c in variants.items()}
    for p in profiles.values():
        p["primary"] = canon[p["primary"].casefold()]


def _common(works, field):
    """Distinct values of a work field for one artist, most-used first."""
    c = defaultdict(int)
    disp = {}
    for w in works:
        v = (w.get(field) or "").strip()
        if v:
            c[v.casefold()] += 1
            disp.setdefault(v.casefold(), v)
    return [disp[k] for k, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


def _year_int(s):
    """'1815' -> 1815; '1815 BCE' and junk -> None. Only CE years plot."""
    s = (s or "").strip()
    if s.isdigit():
        n = int(s)
        if 1000 <= n <= 2200:
            return n
    return None


def _works_overlap(p, q):
    """The years both painters have WORK in the museum — the span the collection can
    actually show them sharing, not their lifetimes (the owner's call: place & time
    is about the pictures on the wall, not a birth certificate). None if either has
    no dated work."""
    a0, a1, b0, b1 = p["year_min"], p["year_max"], q["year_min"], q["year_max"]
    if None in (a0, a1, b0, b1):
        return None
    lo, hi = max(a0, b0), min(a1, b1)
    return (lo, hi) if lo <= hi else None


def _same_nation(p, q):
    """The nationality two painters share, or None. 'Place' for the place-and-time
    gate: coarse enough that real schoolmates still match, sharp enough to keep an
    Australian and an American apart however alike their movement labels read."""
    a, b = p["nationality"].casefold(), q["nationality"].casefold()
    return p["nationality"] if a and a == b else None


# ---------------- derived links ----------------

def _pairs(members):
    ms = list(members)
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            yield ms[i], ms[j]


def _clique(members, type_, note_of):
    """Every pair in the group that note_of accepts — n(n-1)/2 edges, not the n-1 of
    a chain. Dedup thins each pair to its strongest link; the place/time gate thins
    the secondary tiers. The honest graph, drawn whole."""
    out = []
    for p, q in _pairs(members):
        note = note_of(p, q)
        if note:
            out.append({"a": p["name"], "b": q["name"], "type": type_,
                        "note": note, "directed": False, "derived": True})
    return out


def _group_by(profiles, field):
    g = defaultdict(list)
    for p in profiles.values():
        for v in p[field]:
            g[v.casefold()].append(p)
    return g


def derived_links(profiles):
    out = []
    ps = list(profiles.values())

    # PRIMARY — a shared FIRST bio movement. Always drawn: a clique over everyone
    # whose bio opens with the same movement, ungated by place or time.
    prim = defaultdict(list)
    for p in ps:
        if p["movements"]:
            prim[fold(p["movements"][0])].append(p)
    for members in prim.values():
        if len(members) > 1:
            out += _clique(members, "movement", lambda p, q: p["movements"][0])

    # SECONDARY — a looser tie between painters of one place and time. Only pairs of
    # the same nationality whose museum works overlap in year are even considered;
    # among those, a shared style / school / later movement makes a `style` edge, and
    # bare co-presence (same nation, same years, nothing else) makes `place_time`.
    def affinity(p, q):
        return (_shared(p["styles"], q["styles"])
                or _shared(p["schools"], q["schools"])
                or _shared(p["movements"][1:], q["movements"][1:]))

    by_nation = defaultdict(list)
    for p in ps:
        if p["nationality"] and p["year_min"] is not None:
            by_nation[p["nationality"].casefold()].append(p)
    for members in by_nation.values():
        for p, q in _pairs(members):
            ov = _works_overlap(p, q)
            if not ov:
                continue
            tag = affinity(p, q)
            if tag:
                out.append({"a": p["name"], "b": q["name"], "type": "style",
                            "note": "%s · %d–%d" % (tag, ov[0], ov[1]),
                            "directed": False, "derived": True})
            else:
                out.append({"a": p["name"], "b": q["name"], "type": "place_time",
                            "note": "%s · works %d–%d" % (p["nationality"], ov[0], ov[1]),
                            "directed": False, "derived": True})

    # SUBJECT — the same genre, seen differently. Off the map (see MAP_TYPES), kept
    # for the artist page, and left ungated: a subject is worth remarking on ACROSS
    # place and time, which is exactly what the secondary gate would forbid. A clique
    # like the rest, so it no longer depends on who was born next to whom.
    for members in _group_by(profiles, "genres").values():
        if len(members) > 1:
            out += _clique(members, "subject",
                           lambda p, q: _shared(p["genres"], q["genres"]))
    return out


def _shared(a, b):
    """First value common to two ordered lists, in a's order (its display case)."""
    bk = {v.casefold() for v in b}
    return next((v for v in a if v.casefold() in bk), None)


# ---------------- the graph ----------------

def _dedupe(links, profiles):
    """One link per pair — the strongest — and only between artists we actually
    hold. Hand-written links come first so they always win their pair."""
    rank = {t: i for i, t in enumerate(_PRECEDENCE)}
    best = {}
    for l in sorted(links, key=lambda l: rank.get(l["type"], 99)):
        ak, bk = _key(l["a"]), _key(l["b"])
        if ak == bk or ak not in profiles or bk not in profiles:
            continue
        pk = (ak, bk) if ak < bk else (bk, ak)
        if pk not in best:
            best[pk] = l
    return list(best.values())


def all_links():
    """Every link in the museum: stored first (they win ties), then derived."""
    profiles = _profiles()
    return _dedupe(stored_links() + derived_links(profiles), profiles), profiles


def _layout(profiles, nodes):
    """Deterministic map positions in a 1600x780 space, clustered by movement.

    Clusters run left-to-right in rough chronological order (by median birth
    year), which makes the map read as a timeline you can wander. Members sit on
    a ring around their cluster's centre. Hand-tuning would beat this, but it has
    to be right for any library without anyone placing a single node."""
    groups = defaultdict(list)
    for n in nodes:
        groups[profiles[n]["primary"]].append(n)

    def median_birth(g):
        ys = sorted(profiles[n]["born"] for n in g if profiles[n]["born"])
        return ys[len(ys) // 2] if ys else 9999

    order = sorted(groups.items(), key=lambda kv: (median_birth(kv[1]), kv[0].casefold()))
    cols = max(1, math.ceil(math.sqrt(len(order) * (CANVAS_W / CANVAS_H))))
    rows = max(1, math.ceil(len(order) / cols))
    cell_w = (CANVAS_W - 2 * _MARGIN_X) / cols
    cell_h = (CANVAS_H - 2 * _MARGIN_Y) / rows

    pos, labels = {}, []
    for i, (label, members) in enumerate(order):
        cx = _MARGIN_X + cell_w * (i % cols) + cell_w / 2
        cy = _MARGIN_Y + cell_h * (i // cols) + cell_h / 2
        members = sorted(members, key=lambda n: (profiles[n]["born"] or 9999, n))
        for n, (x, y) in _ring(members, cx, cy, min(cell_w, cell_h)).items():
            # Clamp inside the canvas: a big cluster near an edge would otherwise
            # push its outer ring off the side of the map.
            pos[n] = (min(CANVAS_W - 70, max(70, x)), min(CANVAS_H - 40, max(40, y)))
        labels.append({"label": label, "x": round(cx, 1),
                       "y": round(min(CANVAS_H - 18, cy + min(cell_w, cell_h) * 0.5), 1)})
    return pos, labels


# A node is at most 84px across, so anything under ~92px apart reads as a collision.
_PER_RING = 7
_MIN_GAP = 92


def _ring(members, cx, cy, cell):
    """Members on concentric rings around their cluster's centre. The radius grows
    with the crowd — eight painters evenly spaced on one small circle would sit on
    top of each other — and spills to a second ring past _PER_RING."""
    if len(members) == 1:
        return {members[0]: (cx, cy)}
    out = {}
    for j, n in enumerate(members):
        ring = j // _PER_RING
        in_ring = min(_PER_RING, len(members) - ring * _PER_RING)
        # Wide enough that neighbours on this ring clear each other, but never
        # tighter than the cluster's natural size.
        spread = _MIN_GAP / (2 * math.sin(math.pi / max(2, in_ring)))
        r = max(cell * 0.3, spread) * (1 + 0.7 * ring)
        # Offset each outer ring so it doesn't line up radially with the inner one.
        ang = -math.pi / 2 + 2 * math.pi * (j % _PER_RING) / in_ring + 0.45 * ring
        out[n] = (cx + r * math.cos(ang), cy + r * math.sin(ang))
    return out


def graph():
    """Everything the Connections page draws."""
    links, profiles = all_links()
    # Only the kinds the map draws (subject is artist-page-only).
    links = [l for l in links if l["type"] in MAP_TYPES]

    # Every painter is drawn — no node cap. A big museum makes a big blob; the map
    # zooms, so the owner pulls the painters apart by scrolling in rather than by us
    # deciding who's worth showing. What thins the picture is the type toggles.
    kept = set(profiles)
    pos, labels = _layout(profiles, sorted(kept))
    nodes = []
    for k in sorted(kept, key=lambda k: profiles[k]["name"].casefold()):
        p = profiles[k]
        x, y = pos[k]
        nodes.append({
            "id": k, "name": p["name"], "cover": p["cover"], "works": p["works"],
            "born": p["born"], "died": p["died"],
            "movement": p["primary"], "year_min": p["year_min"], "year_max": p["year_max"],
            "x": round(x, 1), "y": round(y, 1),
        })
    counts = defaultdict(int)
    for l in links:
        counts[l["type"]] += 1
    index = library.title_index()
    return {
        "nodes": nodes,
        "links": [_public_link(l, index) for l in links],
        "clusters": labels,
        "types": {t: dict(TYPE_META[t], count=counts[t]) for t in MAP_TYPES},
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "truncated": 0,                    # nothing is dropped any more — kept for the client
        # Every painter in the museum. `nodes` now holds all of them too; this stays
        # the list the link/thread pickers read (never `nodes`), so a picker can name
        # anyone even if a future change starts thinning the drawn set again.
        "artists": [{"id": k, "name": profiles[k]["name"]}
                    for k in sorted(profiles, key=lambda k: profiles[k]["name"].casefold())],
    }


def _public_link(l, index=None):
    # a_id/b_id are the node keys the map joins on. Sent explicitly rather than
    # re-derived in the browser: Python's casefold() and JS's toLowerCase() don't
    # agree on every name, and a near-miss would silently drop an edge.
    out = {"a": l["a"], "b": l["b"], "a_id": _key(l["a"]), "b_id": _key(l["b"]),
           "type": l["type"], "note": l.get("note") or "",
           "directed": bool(l.get("directed")), "derived": bool(l.get("derived"))}
    if l.get("id"):
        out["id"] = l["id"]
    # A note that names a painting in italics gets somewhere to go, on the same
    # terms a placard does — derived here because only the server knows the library.
    if index is not None:
        xr = library.xref_in(out["note"], index)
        if xr:
            out["xref"] = xr
    return out


def for_artist(name, limit=None):
    """This artist's links, strongest first — what the artist page's strip shows.
    Curator notes lead: a human sentence beats 'both were Dutch' every time."""
    links, profiles = all_links()
    k = _key(name)
    if k not in profiles:
        return []
    rank = {t: i for i, t in enumerate(_PRECEDENCE)}
    index = library.title_index()
    mine = []
    for l in links:
        if _key(l["a"]) != k and _key(l["b"]) != k:
            continue
        other = profiles[_key(l["b"] if _key(l["a"]) == k else l["a"])]
        rec = {
            "type": l["type"], "note": l.get("note") or "",
            "derived": bool(l.get("derived")), "id": l.get("id"),
            "directed": bool(l.get("directed")),
            "from_me": bool(l.get("directed")) and _key(l["a"]) == k,
            "other": other["name"], "cover": other["cover"], "works": other["works"],
        }
        xr = library.xref_in(rec["note"], index)
        if xr:
            rec["xref"] = xr
        mine.append(rec)
    mine.sort(key=lambda m: (rank.get(m["type"], 99), m["other"].casefold()))
    return mine[:limit] if limit else mine
