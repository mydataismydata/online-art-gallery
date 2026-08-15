"""Export the Walk the Museum hang as JSON: every hung work, in walk order, each
tagged with the room it hangs in.

Reads the live hang (data/museum.json) and resolves each id against the library,
so run it with the same GALLERY_* env as the server points at the box you want to
export — the private Source museum, most likely — and redirect it to a file:

    python export_museum_walk.py > museum-walk.json

The shape: a `rooms` index (each room's number, floor, name, layout and how many
works it holds) followed by a flat `works` list in the order you'd meet them on
the walk. Every work carries its floor, its room number (counted within its
floor, the way the walk reads it), the room's name, and which wall it's pinned to
when it's pinned. Rooms are numbered as the walk chains them; an empty room still
takes its number — you'd walk through it — it just contributes no works. A work
whose file has gone drops off the wall, exactly as it does in the walk itself.
"""
import json
import sys
import time

from app import museum


def main():
    d = museum.detail()

    rooms_out, works_out = [], []
    seq = 0
    per_floor = {}   # floor (0-based) -> running room number within that floor

    for gi, r in enumerate(d.get("rooms") or [], 1):
        floor0 = r.get("floor", 0)
        room_n = per_floor.get(floor0, 0) + 1
        per_floor[floor0] = room_n
        name = r.get("name") or None
        works = r.get("works") or []
        walls = r.get("walls") or {}

        rooms_out.append({
            "global": gi,            # position in the whole walk, 1-based
            "floor": floor0 + 1,     # storey as signed in the walk (Floor 1 = ground)
            "room": room_n,          # number within its floor, 1-based
            "name": name,
            "layout": r.get("layout"),
            "work_count": len(works),
        })

        for w in works:
            seq += 1
            works_out.append({
                "seq": seq,                       # order across the whole walk, 1-based
                "floor": floor0 + 1,
                "room": room_n,
                "room_name": name,
                "wall": walls.get(w.get("id")) or None,
                "id": w.get("id"),
                "title": w.get("title"),
                "artist": w.get("artist"),
                "date": w.get("date") or (str(w["year"]) if w.get("year") else ""),
                "year": w.get("year"),
                "medium": w.get("medium"),
                "height_cm": w.get("height_cm"),
                "length_cm": w.get("length_cm"),
                "style": w.get("style"),
                "genre": w.get("genre"),
                "school": w.get("school"),
            })

    out = {
        "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
        "museum_updated": d.get("updated"),
        "floors": len(per_floor),
        "room_count": len(rooms_out),
        "work_count": seq,
        "rooms": rooms_out,
        "works": works_out,
    }

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    print("Exported %d works across %d rooms." % (seq, len(rooms_out)),
          file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
