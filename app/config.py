import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION = "0.1"

LIBRARY_DIR = Path(os.environ.get("GALLERY_LIBRARY", str(ROOT / "library")))
CACHE_DIR = Path(os.environ.get("GALLERY_CACHE", str(ROOT / "cache")))
STATIC_DIR = ROOT / "static"
THUMB_DIR = CACHE_DIR / "thumbs"
TMP_DIR = CACHE_DIR / "tmp"
# Deleted works are moved here rather than unlinked, so a mis-click is recoverable.
TRASH_DIR = Path(os.environ.get("GALLERY_TRASH", str(ROOT / "trash")))
# Artist-level metadata (bio, movements, dates). Hidden dir inside the library so
# it travels with the art and is skipped by the work scanner.
ARTIST_META_DIR = LIBRARY_DIR / ".artists"
# User-defined download sources, editable from the Settings page.
CUSTOM_SOURCES_FILE = Path(os.environ.get("GALLERY_SOURCES", str(ROOT / "custom_sources.json")))

# Account data (users, collections, session secret). Kept OUTSIDE the library so
# the library stays shareable and never carries password hashes.
DATA_DIR = Path(os.environ.get("GALLERY_DATA", str(ROOT / "data")))
USERS_FILE = DATA_DIR / "users.json"
COLLECTIONS_DIR = DATA_DIR / "collections"   # one <id>.json per collection
SECRET_KEY_FILE = DATA_DIR / "secret_key"    # persisted so sessions survive restarts
# Auto-fill (owner-set model + API key for the placard editor's AI lookup).
AI_CONFIG_FILE = DATA_DIR / "ai_config.json"

for _d in (LIBRARY_DIR, THUMB_DIR, TMP_DIR, TRASH_DIR, ARTIST_META_DIR,
           DATA_DIR, COLLECTIONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

THUMB_WIDTH = 560
# Long-side cap for the fullscreen "view" image the browser actually loads. The
# original stays available at /orig for a full-resolution look/download; serving
# this smaller derivative keeps transfers fast over a slow uplink (e.g. Starlink).
VIEW_MAX = int(os.environ.get("GALLERY_VIEW_MAX", "2560"))

# Browser-like UA: Google Arts & Culture serves a degraded page to bare bots,
# and none of the museum APIs mind.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 GalleryBrowser/0.1"
)
