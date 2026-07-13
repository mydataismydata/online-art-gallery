# The Gallery

A self-hosted gallery for real paintings, served over your own network. Browse by
artist, era, medium or style; view every work truly full screen (fit-to-screen, one
click for 1:1 pixels with drag-panning — no tiled "zoom viewer" nonsense); and pull
new artists into the library from Google Arts & Culture (via dezoomify-rs) or from
open-access museum APIs at full resolution.

Each artist page can carry a short biography — art movement(s), birth/death years and
birthplace — filled in automatically from Wikidata or edited by hand. You can select
and delete works from any grid (deletions move to a `trash/` folder, so nothing is
lost to a mis-click), and you can teach the app new JSON-API museum sources from the
**Settings** page without touching code.

Access is gated by **accounts** with three roles — Owner, Curator, Visitor — and
curators can assemble works into shareable **Collections**. It's built to run on a
small home server and be reached privately over [Tailscale](https://tailscale.com);
see **[DEPLOY.md](DEPLOY.md)**.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python serve.py                 # serves on http://0.0.0.0:8000
```

Then open `http://<machine-ip>:8000` from anywhere on your network. **The first
screen creates the first Owner account** (see *Accounts & roles* below); nothing in
the gallery is visible until you sign in.

> Starting from empty? Add artists with the **Add artist** button. If you have a
> folder of loosely-named image files, `python import_samples.py <folder>` bulk-imports
> them, parsing artist/title/date out of the filenames.

Options: `python serve.py --port 8000 --library /mnt/art/library`
(or set `GALLERY_LIBRARY` / `GALLERY_CACHE` / `GALLERY_DATA` environment variables).

## Accounts & roles

The gallery sits behind a **login wall** — every page and every image requires a
signed-in account. Three roles:

| | Browse museum & collections | Create & manage **own** collections | Add artists, download, delete, edit bios, manage sources | Manage users |
|---|:--:|:--:|:--:|:--:|
| **Visitor** | ✅ | — | — | — |
| **Curator** | ✅ | ✅ | — | — |
| **Owner** | ✅ | ✅ (any collection) | ✅ | ✅ |

- **First run** shows a one-time setup screen that creates the first **Owner**.
- The Owner creates every other account from **Settings → Users** and sets each role.
  There is no open self-registration.
- Roles are enforced **server-side** — hiding a button in the UI is only cosmetic.
  Passwords are hashed (pbkdf2-sha256); sessions are signed, `HttpOnly` cookies.

Account data (users, collections, the session secret) lives in a top-level `data/`
directory, kept **out** of `library/` so the library never carries password hashes and
stays freely shareable. Relocate it with `GALLERY_DATA=/var/lib/gallery/data`.

## Collections

A **Collection** is a hand-picked, ordered set of works that displays just like the
home page — a curator's themed room inside the museum. Curators and Owners create them
from the **Collections** page, then add works by turning on **Select** on any grid
(home, an artist page, or a Browse result) and choosing **Add to collection**.

Every collection is **visible to everyone** who can sign in; only its creating Curator
(and any Owner) may edit or delete it.

## The library is just folders

```
library/
  Joseph Mallord William Turner/
    The Fighting Temeraire ... (1839).jpg
    The Fighting Temeraire ... (1839).jpg.json    <- optional metadata sidecar
```

- **Adding works by hand:** copy an image into `library/<Artist Name>/`. Name it
  `Title (1875).jpg` or `Title; 1875.jpg` and the title/date are parsed automatically.
  It appears in the gallery within a couple of seconds — no restart, no rescan.
- **Sidecars** (`<image>.json`) carry richer metadata and win over filename parsing:

  ```json
  { "title": "The Fog Warning", "artist": "Winslow Homer", "date": "1885",
    "year": 1885, "medium": "Oil on canvas", "style": "Realism",
    "type": "painting", "source_url": "https://..." }
  ```

  `medium`, `style` and the derived century (`era`) drive the Browse page.
  Downloads write sidecars automatically; imports get them from their filenames.
- Supported formats: JPEG, PNG, WebP. Thumbnails are cached in `cache/thumbs/`.

## Downloading an artist

**Add artist** in the top bar. Pick a source, type a name, optionally cap the number
of works or (for Google) the pixel size. Jobs run in the background with a live log;
works already in the library are recognized by source ID and skipped, so re-running
a job only fetches what's new.

| Source | Notes |
|---|---|
| Google Arts & Culture | Needs `dezoomify-rs` (see below). Accepts an artist name or a pasted entity URL. Downloads each work stitched from tiles, capped at **12,000 px** per side by default — set *Max size* higher (up to ~60,000; JPEG tops out at 65,535) for gigapixel scans, at the cost of minutes of CPU and huge files per painting. The artist page embeds only its first batch of works (typically 40–60), so very prolific artists may need other sources too. |
| The Met (Open Access) | CC0, original full-resolution files, no API key. |
| Art Institute of Chicago | CC0 public-domain paintings over IIIF at the largest size served. |
| Cleveland Museum of Art | CC0 open access, largest available image. |
| Rijksmuseum (open data) | CC0/public-domain, full-resolution Micrio IIIF images, **no API key**. Uses the museum's 2026 keyless Linked-Art API, which resolves each work through several linked records, so it's a bit slower. Search by the maker's full name, e.g. `Rembrandt van Rijn`. |
| Wikidata / Wikimedia Commons | Resolves the artist on Wikidata, then pulls their paintings' full-resolution files from Wikimedia Commons, **no API key**. Good reach for painters the museum APIs miss. Refuses to download when it can't confidently match the name. |
| Victoria & Albert Museum | Open-access fine art over IIIF at up to 3,000 px, **no API key**. Strong on British art and design. |
| *Custom sources* | Anything you add from **Settings** (see below). |

## Artist details

Each artist page has an expandable **Bio** block — art movement(s), birth/death years,
birthplace, nationality and a one-line description — sourced from Wikidata (free, no key,
CC0 data). Matching prefers a human whose occupation is painter/artist, so
"J. M. W. Turner" resolves correctly. Use **Re-fetch** to pull it again, or **edit** to
fix anything by hand. Details are stored per artist under `library/.artists/<slug>.json`,
so they travel with your library.

A **Related artists** row (click to expand) surfaces other painters in your library who
share a movement, style, nationality or era — computed locally from the bio data, with
no network calls.

## Curating: deleting works

Any grid (an artist's page, or a Browse result) has a **Select** button. Turn it on,
click the works you want, and **Delete**. Deleted works — image and metadata sidecar —
are **moved to a `trash/` folder** next to the library, not erased, so a mistake is
recoverable by moving the files back. They vanish from the gallery immediately.

## Settings: adding your own sources

**Settings** in the top bar lets you add any museum that exposes a JSON search API
returning direct image URLs, no code required. You describe, per source:

- a **Search URL** containing `{query}` (and `{page}` if it paginates), including any
  API key,
- the **items path** — where the results array sits in the response (e.g. `records`,
  `data`, `artObjects`), and
- **field mappings** — dotted paths into each item for title, artist, date, image URL,
  etc. (`people.0.name`, `webImage.url`, …).

A **Test** button dry-runs the config against a real query and shows how many records
would be saved plus a sample, so you can get the mappings right before downloading.
Two presets — **Harvard Art Museums** and **Rijksmuseum** — are included as starting
points; load one, paste your own free API key, and save. Custom sources then appear in
the **Add artist** source list like any built-in. Definitions are stored in
`custom_sources.json`; note that API keys are kept there in plain text.

### dezoomify-rs

The Google Arts & Culture source shells out to
[dezoomify-rs](https://github.com/lovasoa/dezoomify-rs). Windows: `dezoomify-rs.exe`
already sits next to `serve.py`. On the Ubuntu server it's fetched automatically by
`deploy/install.sh` (and `deploy/update.sh`); to place it on its own:

```bash
sudo bash deploy/fetch-dezoomify.sh        # -> /opt/gallery/dezoomify-rs
```

The app looks for the binary next to `serve.py`, on `PATH`, or at `$DEZOOMIFY_RS`.

## Running on the Ubuntu server (systemd + Tailscale)

**[DEPLOY.md](DEPLOY.md)** is the full walkthrough: clone, virtualenv, the Linux
`dezoomify-rs`, a hardened systemd unit, and putting the gallery on your Tailscale
network so only invited people can reach it — and *only* this box, not your other
machines.

The short version — a ready-to-edit unit ships in
[`deploy/gallery.service`](deploy/gallery.service):

```bash
sudo cp deploy/gallery.service /etc/systemd/system/gallery.service
sudo nano /etc/systemd/system/gallery.service   # set User=, paths, GALLERY_* dirs
sudo systemctl daemon-reload && sudo systemctl enable --now gallery
```

## Other good sources of high-quality, non-watermarked images

Beyond the built-ins above (which now include Wikidata/Commons and the V&A), these are
all confirmed high-resolution and watermark-free. Several can be added today from
**Settings** (any JSON API with direct image URLs); the rest are good future built-ins.

- **Harvard Art Museums** — broad open-access collection; free API key.
  *Included as a Settings preset.*
- **National Gallery of Art, Washington** — open access, full-res downloads; JSON API,
  addable from Settings.
- **Getty Museum Open Content**, **Smithsonian Open Access**, **Paris Musées**,
  **Yale University Art Gallery / Yale Center for British Art** — all CC0/open.
- **Wikimedia Commons** — enormous; includes most of the Google Art Project gigapixel
  scans at full resolution (category "Google Art Project"). The built-in **Wikidata**
  source already pulls an artist's Commons paintings; a direct Commons *category*
  browser would be a good future addition.
- **Web Gallery of Art (wga.hu)** — great coverage of old masters, moderate resolution.
- Avoid WikiArt for acquisitions: resolution is inconsistent and licensing murky.

## Roadmap

- **Similar artwork**: compute CLIP image embeddings per work on the server
  (`open_clip` runs fine on CPU), cosine nearest-neighbours behind `/api/similar`,
  and a strip of matches in the viewer. The id-per-work data model is already
  embedding-ready.
- Drawings as a second `type` alongside paintings (the field already exists).
- Full-text search box; pre-scaled display derivatives for gigapixel files; a way to
  browse works by an artist's movement, tying the bio data into the Browse page.
