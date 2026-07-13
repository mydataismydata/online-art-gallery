"""'Related artists' for the artist page: other artists already in this library who
share an art movement, nationality, era or style with the one you're viewing.

Everything is derived from what's on disk — the works (era/style/years) plus any saved
Wikidata bios (movements/nationality) — so it needs no network and only ever points to
artists you actually have. Shared movement is the strongest signal, then style, then
nationality; a shared century only counts when the two also overlap in time, so a busy
century doesn't make everyone 'related'."""
from collections import defaultdict

from . import library, artistinfo


def _profile(name, works_by_artist):
    ws = works_by_artist.get(name, [])
    eras = {w["era"] for w in ws if w.get("era")}
    styles = {w["style"].strip().casefold() for w in ws if (w.get("style") or "").strip()}
    years = [w["year"] for w in ws if w.get("year")]
    info = artistinfo.load(name) or {}
    movements_disp = {m.strip(): m.strip().casefold()
                      for m in (info.get("movements") or []) if m.strip()}
    return {
        "eras": eras,
        "styles": styles,
        "years": (min(years), max(years)) if years else None,
        "movements": set(movements_disp.values()),
        "movements_disp": movements_disp,          # display-cased -> casefold
        "nationality": (info.get("nationality") or "").strip(),
    }


def _years_overlap(a, b):
    if not a or not b:
        return False
    return max(a[0], b[0]) <= min(a[1], b[1])


def related_artists(name, limit=8):
    by_artist = defaultdict(list)
    for w in library.all_works():
        by_artist[w["artist"]].append(w)
    if name not in by_artist:
        return []
    t = _profile(name, by_artist)

    out = []
    for other, ws in by_artist.items():
        if other == name:
            continue
        p = _profile(other, by_artist)

        shared_mv = t["movements"] & p["movements"]
        shared_style = t["styles"] & p["styles"]
        shared_era = t["eras"] & p["eras"]
        same_nat = bool(t["nationality"]) and t["nationality"].casefold() == p["nationality"].casefold()
        overlap = _years_overlap(t["years"], p["years"])

        # A shared century only counts as a relation if the two also overlap in time —
        # otherwise a strong signal (movement/style/nationality) is required.
        strong = bool(shared_mv or shared_style or same_nat)
        if not strong and not (shared_era and overlap):
            continue

        score = (5 * len(shared_mv) + 2 * len(shared_style) +
                 (2 if same_nat else 0) + 2 * len(shared_era) + (1 if overlap else 0))

        # A short 'why', strongest reason first, at most two parts.
        why = []
        if shared_mv:
            disp = next((d for d, cf in t["movements_disp"].items() if cf in shared_mv), None)
            if disp:
                why.append(disp)
        if len(why) < 2 and shared_style:
            why.append(next(iter(sorted(shared_style))).title())
        if len(why) < 2 and same_nat:
            why.append(t["nationality"])
        if len(why) < 2 and shared_era:
            why.append(sorted(shared_era)[0])

        out.append({
            "name": other,
            "count": len(ws),
            "cover": library.cover_id(other, ws),
            "score": score,
            "why": " · ".join(why[:2]),
        })

    out.sort(key=lambda a: (-a["score"], -a["count"], a["name"].casefold()))
    return out[:limit]
