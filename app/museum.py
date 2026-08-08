"""The museum: the owner's walkable 3-D hang.

One hang per box, stored in data/museum.json: an ordered list of rooms, each an
ordered list of work ids plus a layout — "normal" (small padding, small pieces
may pair up two high), "stacked" (the salon wall: piles climb to three high
and taller pieces join them) or "breezeway" (a passthrough hall: doors fixed
on opposite walls, works on the two sides only). The rooms are the walk:
room 1's door is the entrance, each further room opens off the last. A room
may carry a name — its signage in the walk; unnamed rooms go by their number.

Membership is by work id, like a collection, and with the same manners: a work
whose file has gone simply drops off the wall (resolve skips it), and an artist
rename re-ids works, so remap_works follows them (wired into the same places
that keep collections and the hero pin pointed right). Editing is owner-only —
which wall a painting hangs on is curation of the museum itself, not a
curator's own collection — but the walk is open to anyone who can browse.
"""
import json
import re
import threading
import time

from . import config, library

_lock = threading.RLock()

LAYOUTS = ("normal", "stacked", "breezeway")
DEFAULT_LAYOUT = "normal"

# Earlier museums spoke differently: "tight" is today's normal, and "spacious"
# (one generous line) retired unused. Both still read — an old museum.json, or
# a repo record published before the rename, builds instead of falling over.
LEGACY_LAYOUTS = {"tight": "normal", "spacious": "normal"}

# Compass walls, as the walk reads them: you enter heading north, so "n" is the
# far wall, "s" the wall at your back, "w" left and "e" right. A work absent
# from a room's walls map hangs wherever the layout finds space. A doorway
# cuts its wall into two shoulders, and a pin may name one: "s1" is the first
# shoulder as the walk reads the wall, "s2" the far one; plain "s" leaves the
# side to the room. On a wall with no doorway the suffix simply falls away.
WALLS = ("n", "s", "e", "w")
PINS = WALLS + tuple(w + i for w in WALLS for i in ("1", "2"))

# Which of a room's OWN walls holds the doorway to the next room. The compass
# is absolute — every room's north faces the same way — and any wall may hold
# the door except, from room two on, the wall the room was itself entered
# through: that one already leads backward.
OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e"}
DEFAULT_EXIT = "n"

# A room's optional second opening: another cardinal wall, or a stairway.
# Two rooms on the same floor whose extra doors face any cardinal become a
# connected pair (a passage between them); an "up" pairs with a "down" on the
# floor above to make a staircase. An unpaired extra door stands closed.
DOOR2 = ("n", "s", "e", "w", "up", "down")

# Floors stack from 0 (the front-door floor, shown as "Floor 1"). Rooms are
# stored floor-major in walk order; each floor chains its own rooms exactly
# like the old single-storey museum, and floors join by stairway — an extra
# door pair where the owner placed one, or a stair grown in the floor's last
# room otherwise.
MAX_FLOORS = 8


def clean_floor(v):
    try:
        f = int(v)
    except Exception:
        f = 0
    return max(0, min(MAX_FLOORS - 1, f))


def clean_door2(s):
    s = str(s or "").strip()
    return s if s in DOOR2 else ""


def _norm_exits(rooms):
    """Chain-normalise in place, floor by floor: within a floor each room's
    exit must be a wall and not the room's own entry (the opposite of the
    previous room's exit); the floor's last room has no onward door. Every
    floor's first room is entered from the south — the front door on the
    ground floor, the arriving stairway above. A breezeway passes straight
    through. The extra door may not sit on the entry or exit wall; a
    breezeway allows none at all."""
    floors = {}
    for r in rooms:
        floors.setdefault(r.get("floor", 0), []).append(r)
    for f, frooms in floors.items():
        entry = "s"
        for i, r in enumerate(frooms):
            last = i == len(frooms) - 1
            x = r.get("exit")
            if last:
                x = None
            elif r.get("layout") == "breezeway":
                x = OPPOSITE[entry]
            elif x not in WALLS or x == entry:
                x = next(w for w in ("n", "e", "s", "w") if w != entry)
            r["exit"] = x
            d2 = clean_door2(r.get("door2"))
            if r.get("layout") == "breezeway" or d2 == entry or (x and d2 == x):
                d2 = ""
            r["door2"] = d2
            entry = OPPOSITE[x] if x else "s"
    return rooms


def _floor_major(rooms):
    """Rooms in storage order: floor by floor, walk order kept within each,
    floor numbers compacted so the stack has no empty storeys."""
    present = sorted({r.get("floor", 0) for r in rooms})
    level = {f: i for i, f in enumerate(present)}
    for r in rooms:
        r["floor"] = level[r.get("floor", 0)]
    return sorted(rooms, key=lambda r: r["floor"])

# Walls for runaway payloads, far above any hang a person would build by hand.
MAX_ROOMS = 60
MAX_WORKS = 3000


def clean_layout(s):
    s = (s or "").strip()
    s = LEGACY_LAYOUTS.get(s, s)
    return s if s in LAYOUTS else DEFAULT_LAYOUT


MAX_NAME = 60


def clean_name(s):
    """A room's name is one short line of signage: whitespace collapsed,
    length capped, absent stored as empty."""
    return " ".join(str(s or "").split())[:MAX_NAME]


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_color(s):
    """A room's wall paint: '#rrggbb', kept lowercase. Anything else means the
    museum's own grey, stored as empty."""
    s = str(s or "").strip()
    return s.lower() if _COLOR_RE.match(s) else ""


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
        walls = {k: v for k, v in walls.items() if k in ids and v in PINS}
        out.append({"work_ids": ids, "walls": walls, "exit": r.get("exit"),
                    "layout": clean_layout(r.get("layout")),
                    "name": clean_name(r.get("name")),
                    "color": clean_color(r.get("color")),
                    "floor": clean_floor(r.get("floor")),
                    "door2": clean_door2(r.get("door2"))})
    return _norm_exits(_floor_major(out))


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
                      "layout": r["layout"], "name": r["name"],
                      "color": r["color"], "floor": r["floor"],
                      "door2": r["door2"]})
        hung += len(works)
    return {"rooms": rooms, "count": hung,
            "updated": (rec or {}).get("updated")}


def save(rooms_in):
    """Replace the whole hang:
    [{work_ids: [...], walls: {id: "n"|"s"|"e"|"w" or a shoulder like "s1"},
      layout: "normal"|"stacked"|"breezeway", name: "...",
      color: "#rrggbb" (the walls' paint; empty = the stock grey),
      floor: 0-based storey, door2: extra opening ("n".."w"|"up"|"down"|"")}].

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
                 if str(k) in ids and v in PINS}
        rooms.append({"work_ids": ids, "walls": walls, "exit": r.get("exit"),
                      "layout": clean_layout(r.get("layout")),
                      "name": clean_name(r.get("name")),
                      "color": clean_color(r.get("color")),
                      "floor": clean_floor(r.get("floor")),
                      "door2": clean_door2(r.get("door2"))})
    rooms = _floor_major(rooms)
    _norm_exits(rooms)
    with _lock:
        rec = _read() or {}
        rec["rooms"] = rooms
        _write(rec)
    return detail()


def hang(ids, floor=None):
    """Hang works at the end of a storey's last room — `floor` is the 0-based
    storey asked for; one the museum doesn't have (or None) means the ground
    floor. Creates the first room if the museum is bare. A work already on a
    wall stays exactly where it is. Returns (newly_hung, room_number, floor):
    the 1-based room within its storey, and the storey they went to."""
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
        floors = sorted({r["floor"] for r in rooms})
        f = floor if floor in floors else (floors[0] if floors else 0)
        if not added:
            return 0, 0, f
        if not rooms:
            rooms = [{"work_ids": [], "layout": DEFAULT_LAYOUT, "floor": 0}]
            f = 0
        target = max(i for i, r in enumerate(rooms)
                     if r.get("floor", 0) == f)
        rooms[target]["work_ids"].extend(added)
        rec["rooms"] = rooms
        _write(rec)
        n = sum(1 for r in rooms[:target + 1] if r.get("floor", 0) == f)
        return len(added), n, f


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


def export_rooms():
    """The hang as stored — ordered ids, sparse walls, exit and layout per room —
    for the publisher to translate into pids. Chain-normalised, like every read."""
    with _lock:
        rec = _read()
    return _rooms(rec)


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
