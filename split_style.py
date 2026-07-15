#!/usr/bin/env python3
"""One-off migration: split the combined `style` sidecar field into style/genre/school.

Until now the AI was asked for a single "genre and/or school or movement" value, so
sidecars hold things like "Marine painting, Dutch Golden Age" in `style`. The gallery
now browses by three separate axes, so this splits each existing value across them:

    style  = the movement or manner    (Baroque, Dutch Golden Age, Impressionism)
    genre  = the subject category      (Marine painting, Portrait, Still life)
    school = the national/regional     (Dutch, Flemish, Heidelberg School)

Matching is by vocabulary, word-boundary and longest-form-first, scanned once per
bucket — so "Flemish Baroque" lands as school=Flemish + style=Baroque, and
"Dutch Golden Age" as style=Dutch Golden Age + school=Dutch. A value nothing
recognises is left alone in `style` and listed at the end for you to eyeball.

Two things worth knowing if you're editing the vocabularies below:

  * Each entry is  "Canonical output": [other spellings that mean the same thing].
    So "Flower painting" in the data comes out as "Floral painting". Add an alias
    to fix a wording you don't like — you never have to touch the matcher.

  * NAMED_SCHOOLS beats NATIONALITIES. A named school already implies its country
    and is the more useful label, so "Australian Impressionism" becomes the
    Heidelberg School rather than "Australian", and "Pennsylvania Impressionism"
    stays itself rather than collapsing to "American".

Dry run (writes nothing, prints exactly what it would do):

    python split_style.py --library /var/lib/gallery/library

Apply it:

    python split_style.py --library /var/lib/gallery/library --apply

Delete this file once the split is done — new works get the three fields from the AI
directly. Nothing else imports it.
"""
import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# ---- vocabularies:  "Canonical output": [other spellings meaning the same] ----

# The movement or manner.
STYLES = {
    "Dutch Golden Age": [],
    "Northern Renaissance": [], "Italian Renaissance": [], "High Renaissance": [],
    "Early Renaissance": [], "Renaissance": [],
    "Baroque": [], "Rococo": [],
    "Mannerism": ["Mannerist"],
    "Neoclassicism": ["Neoclassical", "Neo-classicism"],
    "Romanticism": ["Romantic"],
    "Realism": ["Realist"],
    "Naturalism": ["Naturalist"],
    "Post-Impressionism": ["Post-Impressionist", "Postimpressionism"],
    "Impressionism": ["Impressionist", "Impressionistic"],
    "Pointillism": ["Pointillist"],
    "Divisionism": [],
    "Symbolism": ["Symbolist"],
    "Tonalism": ["Tonalist"],
    "Luminism": [],
    "Orientalism": ["Orientalist"],
    "Academic art": ["Academicism"],
    "Pre-Raphaelite": ["Pre-Raphaelitism", "Pre-Raphaelite Brotherhood"],
    "Art Nouveau": [], "Art Deco": [],
    "Expressionism": ["Expressionist"],
    "Cubism": ["Cubist"], "Fauvism": ["Fauvist"],
    "Surrealism": ["Surrealist"], "Modernism": ["Modernist"],
    "International Gothic": [], "Gothic": [], "Byzantine": [], "Medieval": [],
    "Classicism": [],
    "Caravaggism": ["Caravaggesque", "Caravaggisti"],
    "Victorian": [], "Edwardian": [], "Biedermeier": [],
    "Primitivism": ["Naive art", "Naïve art"],
    "Abstraction": ["Abstract"],
    "Minimalism": [], "Pop art": [], "Futurism": [], "Constructivism": [],
    "Suprematism": [], "Bauhaus": [], "De Stijl": [], "Vorticism": [],
    "Precisionism": [], "Regionalism": [], "Social realism": [],
    "Magic realism": [], "Photorealism": ["Hyperrealism"], "Postmodernism": [],
    "Contemporary": [], "Macchiaioli": [], "Secession": ["Sezession"],
    "Golden Age": [],
}

# The subject category.
GENRES = {
    "History painting": ["History piece"],
    "Genre painting": ["Genre scene", "Genre work"],
    "Equestrian portrait": [], "Group portrait": [], "Self-portrait": [],
    "Portrait": ["Portraiture", "Portrait painting"],
    "Winter landscape": [],
    "Landscape": ["Landscape painting"],
    "Cityscape": ["Townscape", "Veduta", "Urban scene"],
    "Seascape": [],
    "Marine painting": ["Marine art", "Maritime painting", "Maritime", "Marine"],
    "Still life": ["Stilleven"],
    "Floral painting": ["Flower painting", "Flower piece", "Floral", "Flowers"],
    "Animal painting": ["Animalier", "Animal art"],
    "Religious art": ["Religious painting", "Religious", "Sacred art"],
    "Biblical": [],
    "Mythological": ["Mythology", "Mythological painting"],
    "Allegory": ["Allegorical"],
    "Nude": [], "Interior": ["Interior scene"],
    "Architectural": ["Architectural painting"],
    "Battle painting": ["Battle scene", "Battle"],
    "Military": [], "Hunting scene": [], "Sporting art": [], "Vanitas": [],
    "Tronie": [], "Capriccio": [],
    "Pastoral": ["Pastoral scene"],
    "Nocturne": [],
    "Figure painting": ["Figurative"],
    "Conversation piece": [],
    "Trompe l'oeil": ["Trompe-l'oeil"],
    "Icon": [], "Miniature": [], "Caricature": [], "Topographical": [],
    "Rural scene": ["Rural life"],
}

# Named schools and artists' colonies. These WIN over a bare nationality below —
# "Heidelberg School" already tells you Australian, and is the more useful label.
NAMED_SCHOOLS = {
    "Heidelberg School": ["Australian Impressionism", "Australian Impressionist"],
    "Hudson River School": [],
    "Pennsylvania Impressionism": ["Pennsylvania Impressionist", "New Hope School"],
    "Skagen Painters": ["Skagen"],
    "Barbizon School": ["Barbizon"],
    "Hague School": [],
    "Norwich School": [], "Newlyn School": [],
    "Glasgow School": ["Glasgow Boys"],
    "Ashcan School": [],
    "Düsseldorf School": ["Dusseldorf School"],
    "Venetian School": [], "Florentine School": [], "Bolognese School": [],
    "Sienese School": [],
    "Utrecht School": ["Utrecht Caravaggism", "Utrecht Caravaggisti"],
    "Antwerp School": [], "Haarlem School": [], "Delft School": [],
    "Leiden School": ["Leiden fijnschilders"],
    "School of Paris": ["École de Paris", "Ecole de Paris"],
    "Cornish School": [], "Norwegian Romantic Nationalism": [],
}

# The plain national / regional school.
NATIONALITIES = {
    "Netherlandish": ["Early Netherlandish"],
    "Flemish": [], "Dutch": [], "Italian": [], "Venetian": [], "Florentine": [],
    "Sienese": [], "Bolognese": [], "Neapolitan": [], "Lombard": [], "Umbrian": [],
    "Ferrarese": [], "Roman": [], "Spanish": [], "French": [], "German": [],
    "English": [], "British": [], "Scottish": [], "Welsh": [], "Irish": [],
    "American": [], "Russian": [], "Austrian": [], "Swiss": [], "Danish": [],
    "Norwegian": [], "Swedish": [], "Finnish": [], "Belgian": [], "Hungarian": [],
    "Polish": [], "Czech": [], "Portuguese": [], "Greek": [], "Japanese": [],
    "Chinese": [], "Korean": [], "Indian": [], "Persian": [], "Australian": [],
    "Canadian": [], "Mexican": [], "Brazilian": [], "Scandinavian": [],
    "Nordic": [], "Antwerp": [], "Haarlem": [], "Delft": [], "Utrecht": [],
    "Leiden": [], "Amsterdam": [],
}

# Words that carry no bucket on their own — ignored when reporting leftovers.
FILLER = {"painting", "paintings", "painters", "painter", "art", "artwork",
          "works", "work", "style", "school", "movement", "and", "or", "the",
          "of", "a", "an", "period", "era", "scene", "scenes", "genre", "c",
          "circa", "century"}


def _forms(vocab):
    """[(surface form, canonical)] — longest surface first, so "Post-Impressionism"
    is consumed before "Impressionism" and "Australian Impressionism" before
    "Australian"."""
    out = []
    for canon, aliases in vocab.items():
        for surface in [canon] + list(aliases):
            out.append((surface, canon))
    out.sort(key=lambda p: len(p[0]), reverse=True)
    return out


STYLE_FORMS = _forms(STYLES)
GENRE_FORMS = _forms(GENRES)
NAMED_FORMS = _forms(NAMED_SCHOOLS)
NATION_FORMS = _forms(NATIONALITIES)


def _pat(phrase):
    # [\s,]+ between words so a stray comma ("Skagen, Painters") still matches.
    return r"\b" + r"[\s,]+".join(re.escape(w) for w in phrase.split()) + r"\b"


def _find(forms, text):
    """-> (canonicals, surfaces matched). Non-overlapping within the bucket."""
    found, surfaces, rest = [], [], text
    for surface, canon in forms:
        p = _pat(surface)
        if re.search(p, rest, re.I):
            if canon not in found:
                found.append(canon)
            surfaces.append(surface)
            rest = re.sub(p, " ", rest, flags=re.I)
    return found, surfaces


def _leftover(text, surfaces):
    rest = text
    # Longest first: strip "Pennsylvania Impressionism" whole before the style
    # bucket's "Impressionism" can break it up and strand "Pennsylvania" as a
    # phantom dropped word.
    for s in sorted(surfaces, key=len, reverse=True):
        rest = re.sub(_pat(s), " ", rest, flags=re.I)
    words = [w for w in re.split(r"[^A-Za-z'À-ɏ-]+", rest) if w]
    return [w for w in words if w.lower() not in FILLER]


def classify(value):
    """-> (fields, leftover_words, recognised)."""
    styles, s_sf = _find(STYLE_FORMS, value)
    genres, g_sf = _find(GENRE_FORMS, value)
    named, n_sf = _find(NAMED_FORMS, value)
    nations, t_sf = _find(NATION_FORMS, value)
    # A named school implies its country, so it wins outright; the nationality is
    # still consumed (t_sf) so it isn't reported as a dropped word.
    schools = named or nations
    left = _leftover(value, s_sf + g_sf + n_sf + t_sf)
    if not (styles or genres or schools):
        return {"style": value, "genre": None, "school": None}, left, False
    return ({"style": ", ".join(styles) or None,
             "genre": ", ".join(genres) or None,
             "school": ", ".join(schools) or None}, left, True)


def sidecars(root):
    for p in sorted(root.rglob("*.json")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue                                   # skip .artists/ etc.
        if Path(p.stem).suffix.lower() in IMAGE_EXTS:  # "Foo.jpg.json" -> ".jpg"
            yield p


def main():
    ap = argparse.ArgumentParser(description="Split the combined style field into style/genre/school.")
    ap.add_argument("--library", required=True, help="path to the image library")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()

    root = Path(args.library).expanduser()
    if not root.is_dir():
        sys.exit("No such library: %s" % root)

    seen = OrderedDict()      # original value -> (fields, leftover, recognised, count)
    total = changed = blank = 0
    unrecognised = Counter()
    writes = []

    for sc in sidecars(root):
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
        except Exception as e:
            print("  ! unreadable, skipping: %s (%s)" % (sc.name, e))
            continue
        total += 1
        value = (data.get("style") or "").strip()
        if not value:
            blank += 1
            continue
        if data.get("genre") or data.get("school"):
            continue                                   # already split; leave alone
        fields, left, ok = classify(value)
        if value in seen:
            seen[value][3] += 1
        else:
            seen[value] = [fields, left, ok, 1]
        if not ok:
            unrecognised[value] += 1
            continue                                   # nothing to change
        if all(fields.get(k) == (data.get(k) or None) for k in ("style", "genre", "school")):
            continue
        changed += 1
        writes.append((sc, data, fields))

    print("\n%d sidecar(s) scanned · %d with no style · %d distinct style value(s)\n"
          % (total, blank, len(seen)))
    print("Proposed split")
    print("=" * 66)
    for value, (fields, left, ok, n) in seen.items():
        if not ok:
            continue
        print('  "%s"  (%d work%s)' % (value, n, "" if n == 1 else "s"))
        for k in ("style", "genre", "school"):
            print("      %-7s: %s" % (k, fields[k] if fields[k] else "—"))
        if left:
            print("      %-7s: %s" % ("dropped", ", ".join(left)))
        print()

    if unrecognised:
        print("Not recognised — left as-is in `style`, nothing written")
        print("=" * 66)
        for value, n in unrecognised.most_common():
            print('  "%s"  (%d work%s)' % (value, n, "" if n == 1 else "s"))
        print()

    if not args.apply:
        print("Dry run — nothing written. %d work(s) would change." % changed)
        print("Re-run with --apply to write.")
        return

    for sc, data, fields in writes:
        for k in ("style", "genre", "school"):
            if fields[k]:
                data[k] = fields[k]
            else:
                data.pop(k, None)
        sc.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Applied to %d work(s)." % changed)


if __name__ == "__main__":
    main()
