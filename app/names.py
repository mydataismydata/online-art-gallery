"""Name / title / date parsing helpers shared by the scanner, importer and downloaders."""
import re
import unicodedata

_WINDOWS_BAD = set('<>:"/\\|?*')


def safe_name(s, maxlen=150):
    """Make a string safe as a file/folder name on Windows and Linux."""
    s = (s or "").strip()
    s = s.replace(":", " -")
    s = "".join(c if (c not in _WINDOWS_BAD and ord(c) >= 32) else "_" for c in s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" .")
    return s or "Untitled"


def normalize_comma_name(name):
    """'Turner, J. M. W.' -> 'J. M. W. Turner'. Leaves other strings alone."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if name.count(",") == 1:
        last, first = [p.strip() for p in name.split(",")]
        if last and first and not re.search(r"\d", first):
            # A trailing particle elides onto the surname rather than taking a
            # space: "Aligny, Théodore Caruelle d'" is "…Caruelle d'Aligny".
            return first + ("" if first.endswith("'") else " ") + last
    return name


def strip_diacritics(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if not unicodedata.combining(c)
    )


def slugify(s):
    """'J. M. W. Turner' -> 'j-m-w-turner'. Stable key for artist-metadata files."""
    s = strip_diacritics(s or "").casefold()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def name_match(query, candidate):
    """True if every word (>1 char) of the query appears in the candidate name."""
    q = strip_diacritics(query).casefold()
    c = strip_diacritics(candidate or "").casefold()
    tokens = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) > 1]
    if not tokens:
        return False
    return all(t in c for t in tokens)


_PARTICLES = {"van", "de", "der", "den", "von", "du", "di", "da", "la", "le",
              "del", "della", "ten", "ter", "het", "op", "y", "e"}


def unshout(name):
    """Fix museum-style shouting credits: 'Arthur STREETON' -> 'Arthur Streeton'.
    Only fully-uppercase words of 2+ letters are touched, so initials like
    'J. M. W.' and already-proper names pass through unchanged."""
    def fix_word(w, first):
        letters = [c for c in w if c.isalpha()]
        if len(letters) < 2 or not all(c.isupper() for c in letters):
            return w
        lw = w.lower()
        if not first and lw in _PARTICLES:
            return lw
        if lw.startswith("mc") and len(lw) > 2:
            return "Mc" + lw[2:].capitalize()
        # capitalize each letter-run so JEAN-BAPTISTE and O'KEEFFE come out right
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), lw)

    words = (name or "").split()
    return " ".join(fix_word(w, i == 0) for i, w in enumerate(words)) or (name or "")


def particle_case_score(name):
    """How many nobiliary particles ('van', 'de', 'von') are written lowercase.
    Used to choose between artist spellings that differ only in case, so
    'Anthony van Dyck' wins over 'Anthony Van Dyck'."""
    words = (name or "").split()
    return sum(1 for w in words[1:] if w.lower() in _PARTICLES and w[:1].islower())


def artist_sort_key(name):
    """Sort painters by surname: 'Jacques-Louis David' -> 'david'."""
    words = strip_diacritics(name or "").casefold().split()
    if not words:
        return ("", "")
    return (words[-1], " ".join(words))


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suf)


def parse_year(text):
    m = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", text or "")
    return int(m.group(1)) if m else None


def era_from(year, date_text=None):
    """Century bucket, e.g. 1875 -> '19th century'."""
    if year:
        return "%s century" % _ordinal((year - 1) // 100 + 1)
    if date_text:
        m = re.search(r"(\d{1,2})\s*(?:st|nd|rd|th)\s+century", date_text, re.I)
        if m:
            return "%s century" % _ordinal(int(m.group(1)))
    return None


def clean_title_text(s):
    """Undo filename-sanitization artifacts: 'Buffalo Trail_ The...' -> 'Buffalo Trail: The...'."""
    s = (s or "").strip()
    s = s.replace("_ ", ": ")
    s = re.sub(r"(?<=\d)_(?=\d)", "–", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
