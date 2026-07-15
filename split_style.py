#!/usr/bin/env python3
"""One-off migration: split the combined `style` sidecar field into style/genre/school.

Until now the AI was asked for a single "genre and/or school or movement" value, so
sidecars hold things like "Marine painting, Dutch Golden Age" in `style`. The gallery
now browses by three separate axes, so this splits each existing value across them:

    style  = the movement or manner    (Baroque, Dutch Golden Age, Impressionism)
    genre  = the subject category      (Marine painting, Portrait, Still life)
    school = the national/regional     (Dutch, Flemish, Heidelberg School)

Matching is by vocabulary, word-boundary and longest-phrase-first, scanned once per
bucket — so "Flemish Baroque" lands as school=Flemish + style=Baroque, and
"Dutch Golden Age" as style=Dutch Golden Age + school=Dutch. A value nothing
recognises is left alone in `style` and listed at the end for you to eyeball.

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

# The movement or manner.
STYLES = [
    "Dutch Golden Age", "Northern Renaissance", "Italian Renaissance",
    "High Renaissance", "Early Renaissance", "Renaissance", "Baroque", "Rococo",
    "Mannerism", "Mannerist", "Neoclassicism", "Neoclassical", "Romanticism",
    "Romantic", "Realism", "Realist", "Naturalism", "Naturalist",
    "Post-Impressionism", "Post-Impressionist", "Impressionism", "Impressionist",
    "Pointillism", "Divisionism", "Symbolism", "Symbolist", "Tonalism", "Luminism",
    "Orientalism", "Academic art", "Academicism", "Pre-Raphaelite", "Art Nouveau",
    "Art Deco", "Expressionism", "Cubism", "Fauvism", "Surrealism", "Modernism",
    "International Gothic", "Gothic", "Byzantine", "Medieval", "Classicism",
    "Caravaggism", "Caravaggesque", "Victorian", "Edwardian", "Biedermeier",
    "Primitivism", "Naive art", "Abstraction", "Abstract", "Minimalism", "Pop art",
    "Futurism", "Constructivism", "Suprematism", "Bauhaus", "De Stijl", "Vorticism",
    "Precisionism", "Regionalism", "Social realism", "Magic realism",
    "Photorealism", "Hyperrealism", "Postmodernism", "Contemporary", "Macchiaioli",
    "Secession", "Golden Age",
]

# The subject category.
GENRES = [
    "History painting", "Genre painting", "Genre scene", "Equestrian portrait",
    "Group portrait", "Self-portrait", "Portraiture", "Portrait",
    "Winter landscape", "Landscape", "Cityscape", "Townscape", "Veduta",
    "Seascape", "Marine painting", "Marine art", "Maritime", "Marine",
    "Still life", "Flower painting", "Floral", "Animal painting", "Animalier",
    "Religious art", "Religious", "Biblical", "Mythological", "Mythology",
    "Allegory", "Allegorical", "Nude", "Interior", "Architectural",
    "Battle painting", "Battle scene", "Battle", "Military", "Hunting scene",
    "Sporting art", "Vanitas", "Tronie", "Capriccio", "Pastoral", "Nocturne",
    "Figure painting", "Conversation piece", "Trompe l'oeil", "Icon", "Miniature",
    "Caricature", "Topographical", "Rural scene",
]

# The national or regional school.
SCHOOLS = [
    "Hudson River School", "Heidelberg School", "Barbizon School", "Norwich School",
    "Newlyn School", "Glasgow School", "Ashcan School", "Dusseldorf School",
    "Düsseldorf School", "Venetian School", "Florentine School", "Bolognese School",
    "Sienese School", "Utrecht School", "Antwerp School", "Haarlem School",
    "Delft School", "Leiden School", "Hague School", "School of Paris", "Barbizon",
    "Netherlandish", "Flemish", "Dutch", "Italian", "Venetian", "Florentine",
    "Sienese", "Bolognese", "Neapolitan", "Lombard", "Umbrian", "Ferrarese",
    "Roman", "Spanish", "French", "German", "English", "British", "Scottish",
    "Irish", "American", "Russian", "Austrian", "Swiss", "Danish", "Norwegian",
    "Swedish", "Finnish", "Belgian", "Hungarian", "Polish", "Czech", "Portuguese",
    "Greek", "Japanese", "Chinese", "Korean", "Indian", "Persian", "Australian",
    "Canadian", "Mexican", "Brazilian", "Scandinavian", "Nordic", "Antwerp",
    "Haarlem", "Delft", "Utrecht", "Leiden", "Amsterdam",
]

# Words that carry no bucket on their own — ignored when reporting leftovers.
FILLER = {"painting", "paintings", "art", "artwork", "works", "work", "style",
          "school", "movement", "and", "or", "the", "of", "a", "an", "period",
          "era", "scene", "scenes", "genre", "c", "circa", "century"}

for _v in (STYLES, GENRES, SCHOOLS):      # longest phrase first, so "Post-Impressionism"
    _v.sort(key=len, reverse=True)        # is consumed before "Impressionism"


def _pat(phrase):
    return r"\b" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\b"


def _find(vocab, text):
    """Longest-first, non-overlapping matches within one bucket."""
    found, rest = [], text
    for phrase in vocab:
        p = _pat(phrase)
        if re.search(p, rest, re.I):
            found.append(phrase)
            rest = re.sub(p, " ", rest, flags=re.I)
    return found


def _leftover(text, matched):
    rest = text
    for phrase in matched:
        rest = re.sub(_pat(phrase), " ", rest, flags=re.I)
    words = [w for w in re.split(r"[^A-Za-z'À-ɏ-]+", rest) if w]
    return [w for w in words if w.lower() not in FILLER]


def classify(value):
    """-> (fields, leftover_words, recognised)."""
    styles, genres, schools = _find(STYLES, value), _find(GENRES, value), _find(SCHOOLS, value)
    left = _leftover(value, styles + genres + schools)
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
