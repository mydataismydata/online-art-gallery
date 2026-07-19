"""Bulk metadata: paste a JSON list in Settings, see what would change, then apply.

The owner's round trip is CSV out, JSON back in: export the artists/artworks
sheets, fill the blanks somewhere comfortable, paste the result here. So the
fields accepted are exactly the exported columns, and records are matched the
way the exports were keyed — an artist by name, a work by artist + title, all
folded so an accent or a case difference doesn't orphan a row.

Two rules keep a paste from doing quiet damage:

* Nothing is created and nothing is blanked. A name the museum doesn't hold is
  reported, not added — a bio without works is an orphan even the publisher
  refuses to ship. An empty or missing field leaves the stored value alone.
  This panel fills gaps and corrects values; deleting stays in the placard
  editor, one work at a time, on purpose.

* The preview and the apply are the same comparison. Apply doesn't trust the
  preview it showed — the library may have moved between the two clicks — it
  recounts, writes what it counted, and reports what it wrote.

A work record's artist and title are its KEY, never fields to change: renaming
moves files and re-ids works, which is update_work's job and no business of a
batch. Duplicate copies of a painting (same artist, same title, two files) are
all brought up to the pasted record — they're the same picture on two walls.
"""
import json
import re
from collections import Counter, defaultdict

from . import library, artistinfo
from .names import fold

ARTIST_FIELDS = ("born", "died", "birthplace", "nationality", "movements",
                 "description", "wikidata_id", "wikipedia_url")
WORK_FIELDS = ("date", "medium", "style", "genre", "school", "description")

MAX_RECORDS = 5000
_DETAIL_CAP = 60          # rows the preview lists before "…and n more"
_UNKNOWN_CAP = 30
_SNIP = 80                # detail values are for eyeballing, not for storage


def _scalar(field, v):
    """A pasted value as it would be stored, or None for 'leave it alone'."""
    if isinstance(v, (int, float)) and field in ("born", "died"):
        v = str(int(v))
    if not isinstance(v, str):
        return None
    if field == "description":
        v = v.replace("\r\n", "\n").replace("\r", "\n").strip()
    else:
        v = re.sub(r"\s+", " ", v).strip()
    return v or None


def _movements(v):
    """A movements value as a clean list, or None for 'leave it alone'. Accepts a
    list or the exports' joined string; both split on ; or , and keep order —
    the first movement is the one the map clusters on."""
    if isinstance(v, str):
        parts = re.split(r"[;,]", v)
    elif isinstance(v, list):
        parts = [str(p) for p in v]
    else:
        return None
    out = [re.sub(r"\s+", " ", p).strip() for p in parts]
    out = [p for p in out if p]
    return out or None


def _snip(v):
    v = v or "—"
    return v if len(v) <= _SNIP else v[:_SNIP - 1] + "…"


def _display(field, old, new):
    """What the preview shows for one changed field. Descriptions are shown as
    sizes — a 300-word placard in a diff row would drown the rows around it."""
    if field == "description":
        fmt = lambda s: ("%d chars" % len(s)) if s else "—"
        return [fmt(old), fmt(new)]
    return [_snip(old), _snip(new)]


def _parse(text):
    try:
        data = json.loads(text or "")
    except ValueError:
        raise ValueError("That isn't valid JSON. Paste a list like the empty "
                         "template: [ { … }, { … } ].")
    # Forgive the obvious wrapper: {"artists": [ … ]} means its list.
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), list):
        data = next(iter(data.values()))
    if not isinstance(data, list) or not data:
        raise ValueError("Paste a JSON list of records: [ { … }, { … } ].")
    if len(data) > MAX_RECORDS:
        raise ValueError("That's %d records — keep a paste under %d."
                         % (len(data), MAX_RECORDS))
    if not all(isinstance(r, dict) for r in data):
        raise ValueError("Every record in the list must be an object: { … }.")
    return data


def _dedupe(records, key_of):
    """Last record wins when a paste names the same thing twice — the bottom of a
    hand-edited file is where the correction usually is."""
    kept, order, invalid = {}, [], 0
    for r in records:
        k = key_of(r)
        if k is None:
            invalid += 1
            continue
        if k not in kept:
            order.append(k)
        kept[k] = r
    return [(k, kept[k]) for k in order], len(records) - invalid - len(order), invalid


# ---------------- artists ----------------

def _diff_artists(records):
    canon = {fold(a["name"]): a["name"] for a in library.artists()}

    def key_of(r):
        n = r.get("name")
        return fold(n) if isinstance(n, str) and n.strip() else None

    rows, folded, invalid = _dedupe(records, key_of)
    out = {"kind": "artists", "total": len(records), "folded": folded,
           "invalid": invalid, "changed": 0, "unchanged": 0,
           "unknown": [], "fields": Counter(), "details": []}
    writes = []                       # (canonical name, merged record) to save

    for k, rec in rows:
        name = canon.get(k)
        if not name:
            out["unknown"].append(rec.get("name").strip())
            continue
        cur = artistinfo.load(name) or {}
        changes, merged = {}, dict(cur)
        for f in ARTIST_FIELDS:
            if f == "movements":
                new = _movements(rec.get(f))
                old = [str(m).strip() for m in (cur.get("movements") or [])]
                if new is not None and new != old:
                    changes[f] = ["; ".join(old) or "—", "; ".join(new)]
                    merged[f] = new
            else:
                new = _scalar(f, rec.get(f))
                old = (cur.get(f) or "").strip()
                if new is not None and new != old:
                    changes[f] = _display(f, old, new)
                    merged[f] = new
        if not changes:
            out["unchanged"] += 1
            continue
        out["changed"] += 1
        out["fields"].update(changes.keys())
        out["details"].append({"label": name, "copies": 1, "changes": changes})
        writes.append((name, merged))

    out["_writes"] = writes
    return out


def _apply_artists(diff):
    for name, merged in diff["_writes"]:
        artistinfo.save(name, merged)
    return len(diff["_writes"])


# ---------------- works ----------------

def _diff_works(records):
    by_key = defaultdict(list)
    for w in library.all_works():
        by_key[(fold(w["artist"]), fold(w["title"]))].append(w)

    def key_of(r):
        a, t = r.get("artist"), r.get("title")
        if not (isinstance(a, str) and a.strip() and isinstance(t, str) and t.strip()):
            return None
        return (fold(a), fold(t))

    rows, folded, invalid = _dedupe(records, key_of)
    out = {"kind": "works", "total": len(records), "folded": folded,
           "invalid": invalid, "changed": 0, "unchanged": 0, "touched": 0,
           "unknown": [], "fields": Counter(), "details": []}
    updates = {}                      # work id -> {field: value}

    for k, rec in rows:
        copies = by_key.get(k)
        if not copies:
            out["unknown"].append("%s — %s" % (rec.get("artist").strip(),
                                               rec.get("title").strip()))
            continue
        rec_changes = None
        touched_here = 0
        # Each copy is measured against its own sidecar: one may already carry a
        # medium the other is missing, and only the gap gets written.
        for w in copies:
            changes = {}
            for f in WORK_FIELDS:
                new = _scalar(f, rec.get(f))
                old = (w.get(f) or "").strip()
                if new is not None and new != old:
                    changes[f] = new
            if changes:
                updates[w["id"]] = changes
                out["fields"].update(changes.keys())
                touched_here += 1
                if rec_changes is None:
                    rec_changes = {f: _display(f, (w.get(f) or "").strip(), v)
                                   for f, v in changes.items()}
        if touched_here:
            out["changed"] += 1
            out["touched"] += touched_here
            out["details"].append({"label": "%s — %s" % (copies[0]["artist"],
                                                         copies[0]["title"]),
                                   "copies": len(copies), "changes": rec_changes})
        else:
            out["unchanged"] += 1

    out["_writes"] = updates
    return out


def _apply_works(diff):
    return library.update_works_meta(diff["_writes"])


# ---------------- export ----------------
# The other half of the round trip. An export is, byte for byte, a valid import:
# same keys, same matching, same field set — and every key is present even when
# empty, because a blank "" against a field's name is what shows a human editing
# the file WHAT can be filled in. Re-importing an untouched export changes
# nothing; that symmetry is the tool's contract.

def export_artists(names=None):
    """Every artist's record (or just the named ones), import-shaped. An artist
    with no bio at all still exports — name and blanks — because finding those
    blanks is what the export is for."""
    want = None if names is None else {fold(n) for n in names}
    out = []
    for a in library.artists():
        if want is not None and fold(a["name"]) not in want:
            continue
        info = artistinfo.load(a["name"]) or {}
        rec = {"name": a["name"]}
        for f in ARTIST_FIELDS:
            if f == "movements":
                rec[f] = [str(m).strip() for m in (info.get("movements") or [])]
            else:
                rec[f] = (info.get(f) or "").strip()
        out.append(rec)
    return out


def export_works(ids=None):
    """Every work's record (or just the given ids), import-shaped, in the same
    order the artists CSV used: painter A-Z, then earliest first. Descriptions
    travel as stored — markup and all — so a round trip loses nothing."""
    want = None if ids is None else set(ids)
    rows = [w for w in library.all_works() if want is None or w["id"] in want]
    rows.sort(key=lambda w: (w["artist"].casefold(), w["year"] or 9999,
                             w["title"].casefold()))
    out = []
    for w in rows:
        rec = {"artist": w["artist"], "title": w["title"]}
        for f in WORK_FIELDS:
            rec[f] = (w.get(f) or "").strip()
        out.append(rec)
    return out


# ---------------- entry ----------------

def run(kind, text, apply=False):
    records = _parse(text)
    if kind == "artists":
        diff, do = _diff_artists(records), _apply_artists
    elif kind == "works":
        diff, do = _diff_works(records), _apply_works
    else:
        raise ValueError("Unknown kind %r." % kind)

    if apply:
        diff["applied"] = do(diff)

    diff.pop("_writes")
    diff["fields"] = dict(diff["fields"])
    diff["unknown_more"] = max(0, len(diff["unknown"]) - _UNKNOWN_CAP)
    diff["unknown"] = diff["unknown"][:_UNKNOWN_CAP]
    diff["details_more"] = max(0, len(diff["details"]) - _DETAIL_CAP)
    diff["details"] = diff["details"][:_DETAIL_CAP]
    return diff
