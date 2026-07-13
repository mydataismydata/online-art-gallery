"""One-shot importer: copies paintings from a folder of loose files (default .samples)
into the library structure, parsing artist/title/date out of the filenames.

Understood filename patterns:
    Last, First; Title; Date.jpg      e.g.  Menzel, Adolph; The Dinner at the Ball; 1878.jpg
    Title - Artist.jpg                e.g.  Execution of Lady Jane Grey - Paul Delaroche.jpg

Files it can't attribute are listed at the end and left alone.
Re-running is safe: files already imported (tracked via sidecar "original") are skipped.
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import config  # noqa: E402  (creates the library/cache dirs)
from app.names import (  # noqa: E402
    clean_title_text, normalize_comma_name, parse_year, safe_name,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MUSEUM_WORDS = ("museum", "gallery", "institute", "collection", "trust")

# Same painter, different filename spellings -> one canonical folder.
ARTIST_ALIASES = {
    "J. M. W. Turner": "Joseph Mallord William Turner",
}

# Files whose names don't carry the real attribution.
OVERRIDES = {
    "Antoine Laurent Lavoisier": {
        "artist": "Jacques-Louis David",
        "title": "Antoine Laurent Lavoisier and His Wife (Marie Anne Pierrette Paulze)",
        "date": "1788",
    },
}


def looks_like_museum(s):
    low = (s or "").lower()
    return any(w in low for w in MUSEUM_WORDS)


def clean_date(s):
    if not s:
        return None
    s = re.sub(r"(?<=\d)_(?=\d)", "–", s.strip())
    if not re.search(r"\d{3,4}", s) and "century" not in s.lower():
        return None  # e.g. "n.d" or a museum name in the date slot
    return s


def pull_trailing_year(title):
    """'Fishing Boats, 1884' -> ('Fishing Boats', '1884');  also '… 1891–1892'."""
    m = re.search(r"[,\s]+((?:1[0-9]{3}|20[0-2][0-9])(?:\s*[–_-]\s*\d{2,4})?)$", title)
    if m:
        return title[: m.start()].rstrip(" ,"), m.group(1).replace("_", "–")
    return title, None


def parse_filename(stem):
    """Returns (artist, title, date) or None if unattributable."""
    for prefix, override in OVERRIDES.items():
        if stem.startswith(prefix):
            return override["artist"], override["title"], override.get("date")

    if ";" in stem:
        parts = [p.strip() for p in stem.split(";") if p.strip()]
        artist = normalize_comma_name(parts[0])
        title = clean_title_text(parts[1]) if len(parts) > 1 else "Untitled"
        date = clean_date(parts[2]) if len(parts) > 2 else None
        # Strip a trailing year from the title only when it isn't part of the
        # title proper: no date given, or it just repeats the date's year.
        stripped, trailing = pull_trailing_year(title)
        if trailing and (date is None or parse_year(trailing) == parse_year(date)):
            title = stripped
            date = date or trailing
        return artist, title, date

    if " - " in stem:
        title, artist = stem.rsplit(" - ", 1)
        artist = re.sub(r"\s*\([^)]*\)\s*$", "", artist).strip()  # strip lifespan "(1865-1918)"
        if looks_like_museum(artist) or not artist:
            return None
        artist = normalize_comma_name(artist)
        title, trailing = pull_trailing_year(clean_title_text(title))
        return artist, title, trailing

    return None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else config.ROOT / ".samples"
    if not src_dir.is_dir():
        print("Source folder not found: %s" % src_dir)
        sys.exit(1)

    already = set()
    for sc in config.LIBRARY_DIR.rglob("*.json"):
        try:
            already.add(json.loads(sc.read_text(encoding="utf-8")).get("original"))
        except Exception:
            pass

    imported, skipped_dupe, unattributed = 0, 0, []
    for f in sorted(src_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in IMAGE_EXTS:
            continue
        if f.name in already:
            skipped_dupe += 1
            continue
        parsed = parse_filename(f.stem)
        if not parsed:
            unattributed.append(f.name)
            continue
        artist, title, date = parsed
        artist = ARTIST_ALIASES.get(artist, artist)
        year = parse_year(date)

        folder = config.LIBRARY_DIR / safe_name(artist, 80)
        folder.mkdir(parents=True, exist_ok=True)
        base = safe_name("%s (%s)" % (title, year) if year else title)
        dest = folder / (base + f.suffix.lower())
        n = 2
        while dest.exists():
            dest = folder / ("%s (%d)%s" % (base, n, f.suffix.lower()))
            n += 1
        shutil.copy2(str(f), str(dest))
        sidecar = {
            "title": title, "artist": artist, "date": date, "year": year,
            "medium": None, "style": None, "type": "painting",
            "source": "import", "original": f.name,
        }
        Path(str(dest) + ".json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")
        imported += 1
        print("  %s  <-  %s" % (dest.relative_to(config.LIBRARY_DIR), f.name))

    print("\nImported %d work(s) into %s" % (imported, config.LIBRARY_DIR))
    if skipped_dupe:
        print("Skipped %d file(s) already imported earlier." % skipped_dupe)
    if unattributed:
        print("\nLeft alone (no artist could be parsed from the filename):")
        for name in unattributed:
            print("  - %s" % name)
        print("Rename these like \"Artist Name; Title; 1875.jpg\" and re-run, or copy them\n"
              "into library/<Artist Name>/ by hand.")


if __name__ == "__main__":
    main()
