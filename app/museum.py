"""The museum: the owner's walkable 3-D hang.

One hang per box, stored in data/museum.json: an ordered list of rooms, each an
ordered list of work ids plus a layout — "tight" (small padding, small pieces
may stack two high) or "spacious" (one generous horizontal line). The rooms are
the walk: room 1's door is the entrance, each further room opens off the last.

Membership is by work id, like a collection, and with the same manners: a work
whose file has gone simply drops off the wall (resolve skips it), and an artist
rename re-ids works, so remap_works follows them (wired into the same places
that keep collections and the hero pin pointed right). Editing is owner-only —
which wall a painting hangs on is curation of the museum itself, not a
curator's own collection — but the walk is open to anyone who can browse.
"""
import json
import threading
import time

from . import config, library

_lock = threading.RLock()

LAYOUTS = ("tight", "spacious")
DEFAULT_LAYOUT = "spacious"

# Compass walls, as the walk reads them: you enter heading north, so "n" is the
# far wall, "s" the wall at your back, "w" left and "e" right. A work absent
# from a room's walls map hangs wherever the layout finds space.
WALLS = ("n", "s", "e", "w")

# Which of a room's OWN walls holds the doorway to the next room. The compass
# is absolute — every room's north faces the same way — and any wall may hold
# the door except, from room two on, the wall the room was itself entered
# through: that one already leads backward.
OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e"}
DEFAULT_EXIT = "n"


def _norm_exits(rooms):
    """Chain-normalise exits in place: each room's exit must be a wall and not
    the room's own entry (the opposite of the previous room's exit). Room one
    starts with its entry on the south wall — the museum's front door."""
    entry = "s"
    for r in rooms:
        x = r.get("exit")
        if x not in WALLS or x == entry:
            x = next(w for w in ("n", "e", "s", "w") if w != entry)
        r["exit"] = x
        entry = OPPOSITE[x]
    return rooms

# Walls for runaway payloads, far above any hang a person would build by hand.
MAX_ROOMS = 60
MAX_WORKS = 3000


def clean_layout(s):
    s = (s or "").strip()
    return s if s in LAYOUTS else DEFAULT_LAYOUT


def _read():
    try:
        rec = json.loads(config.MUSEUM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


def _write(rec):
    rec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    config.MUSEUM_FILE.write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def _rooms(rec):
    """The stored rooms, shaped defensively: the file is hand-editable."""
    out = []
    for r in (rec or {}).get("rooms") or []:
        if not isinstance(r, dict):
            continue
        ids = [w for w in r.get("work_ids") or [] if isinstance(w, str) and w]
        walls = r.get("walls") if isinstance(r.get("walls"), dict) else {}
        walls = {k: v for k, v in walls.items() if k in ids and v in WALLS}
        out.append({"work_ids": ids, "walls": walls, "exit": r.get("exit"),
                    "layout": clean_layout(r.get("layout"))})
    return _norm_exits(out)


def detail():
    """The museum as the walk (and the arrange screen) sees it: rooms with works
    resolved to full dicts, ids whose file no longer exists silently skipped.
    Empty rooms stay in the payload — the arrange screen needs somewhere to drag
    a painting into; the walk itself just skips them."""
    with _lock:
        rec = _read()
    rooms, hung = [], 0
    for r in _rooms(rec):
        works = [w for w in (library.get(wid) for wid in r["work_ids"]) if w]
        rooms.append({"works": works, "walls": r["walls"], "exit": r["exit"],
                      "layout": r["layout"]})
        hung += len(works)
    return {"rooms": rooms, "count": hung,
            "updated": (rec or {}).get("updated")}


def save(rooms_in):
    """Replace the whole hang:
    [{work_ids: [...], walls: {id: "n"|"s"|"e"|"w"}, layout: "tight"|"spacious"}].

    Ids are validated against the library and a work hangs once — a duplicate
    keeps its first placement. Unknown ids are dropped rather than stored:
    nothing else does the museum's bookkeeping, so a stale id would sit in the
    file forever. The walls map is sparse — only pinned works appear; anything
    else hangs where the layout finds space. Empty rooms are kept; pruning them
    is the arrange screen's decision, not a side effect of saving."""
    if not isinstance(rooms_in, list):
        raise ValueError("rooms must be a list.")
    if len(rooms_in) > MAX_ROOMS:
        raise ValueError("That's %d rooms — keep the museum under %d."
                         % (len(rooms_in), MAX_ROOMS))
    by_id = library.scan()["by_id"]
    seen, rooms, total = set(), [], 0
    for r in rooms_in:
        if not isinstance(r, dict):
            raise ValueError("Each room must be an object: { work_ids, layout }.")
        ids = []
        for wid in r.get("work_ids") or []:
            wid = str(wid)
            if wid in by_id and wid not in seen:
                seen.add(wid)
                ids.append(wid)
        total += len(ids)
        if total > MAX_WORKS:
            raise ValueError("That's over %d works — hang fewer." % MAX_WORKS)
        walls = r.get("walls") if isinstance(r.get("walls"), dict) else {}
        walls = {str(k): v for k, v in walls.items()
                 if str(k) in ids and v in WALLS}
        rooms.append({"work_ids": ids, "walls": walls, "exit": r.get("exit"),
                      "layout": clean_layout(r.get("layout"))})
    _norm_exits(rooms)
    with _lock:
        rec = _read() or {}
        rec["rooms"] = rooms
        _write(rec)
    return detail()


def hang(ids):
    """Hang works at the end of the last room, creating the first room if the
    museum is bare. A work already on a wall stays exactly where it is. Returns
    (newly_hung, room_number) — the 1-based room they went into."""
    by_id = library.scan()["by_id"]
    with _lock:
        rec = _read() or {}
        rooms = _rooms(rec)
        have = {w for r in rooms for w in r["work_ids"]}
        added = []
        for wid in (str(i) for i in ids or []):
            if wid in by_id and wid not in have:
                have.add(wid)
                added.append(wid)
        if added:
            if not rooms:
                rooms = [{"work_ids": [], "layout": DEFAULT_LAYOUT}]
            rooms[-1]["work_ids"].extend(added)
            rec["rooms"] = rooms
            _write(rec)
        return len(added), len(rooms) or 1


def unhang(ids):
    """Take works off whichever wall holds them. Returns how many came down."""
    drop = {str(i) for i in ids or []}
    with _lock:
        rec = _read()
        if not rec:
            return 0
        rooms = _rooms(rec)
        n = 0
        for r in rooms:
            keep = [w for w in r["work_ids"] if w not in drop]
            n += len(r["work_ids"]) - len(keep)
            r["work_ids"] = keep
            r["walls"] = {k: v for k, v in r["walls"].items() if k not in drop}
        if n:
            rec["rooms"] = rooms
            _write(rec)
        return n


def hung_ids():
    """Every id on a wall right now — for marking what's already in the museum."""
    with _lock:
        rec = _read()
    return [w for r in _rooms(rec) for w in r["work_ids"]]


def remap_works(id_map):
    """Follow works whose id changed because their file moved (an artist rename
    or a repoint) — the museum's copy of collections.remap_works, for the same
    reason: without it the painting is still in the library but silently off the
    wall. Returns 1 if the hang was rewritten."""
    id_map = {k: v for k, v in (id_map or {}).items() if k and v and v != k}
    if not id_map:
        return 0
    with _lock:
        rec = _read()
        if not rec:
            return 0
        rooms = _rooms(rec)
        changed = False
        for r in rooms:
            moved = [id_map.get(w, w) for w in r["work_ids"]]
            if moved != r["work_ids"]:
                r["work_ids"] = moved
                r["walls"] = {id_map.get(k, k): v for k, v in r["walls"].items()}
                changed = True
        if changed:
            rec["rooms"] = rooms
            _write(rec)
        return 1 if changed else 0
