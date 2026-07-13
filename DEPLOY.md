# Deploying the Gallery on an Ubuntu server over Tailscale

This gets you a gallery that runs as a service on a headless Ubuntu box and is
reachable **only** by people you invite onto your Tailscale network — without exposing
it (or your other machines) to the public internet.

**What you'll end up with**

```
your laptop / a curator's laptop
        │  https://gallery.<your-tailnet>.ts.net   (encrypted by Tailscale/WireGuard)
        ▼
   Ubuntu box  ── tailscaled ──►  tailscale serve (HTTPS :443)
                                        │  proxies to
                                        ▼
                                 gallery.service  (waitress on 127.0.0.1:8000)
                                        │  reads/writes
                                        ▼
                                 /var/lib/gallery  (library, accounts, cache)
```

Two independent layers of control, which is exactly what you want:

- **Tailscale** decides *who can reach the box at all* (and only this box).
- **The gallery's own login** decides *what they can do* once they're on it
  (Owner / Curator / Visitor).

---

## Part 1 — Install the app

SSH into the Ubuntu box. The fastest path is the bundled installer — it does
everything in this section (packages, service user, clone, virtualenv,
**dezoomify-rs**, and the systemd service), and is idempotent, so it doubles as a
repair/upgrade tool:

```bash
curl -fsSL https://raw.githubusercontent.com/mydataismydata/online-art-gallery/main/deploy/install.sh | sudo bash
```

Prefer to run it by hand? The same steps, explicitly:

```bash
# System packages
sudo apt update
sudo apt install -y python3-venv python3-pip git curl

# A dedicated, unprivileged service account (no login shell)
sudo useradd --system --shell /usr/sbin/nologin gallery

# Code in /opt/gallery, all mutable data in /var/lib/gallery
sudo mkdir -p /opt/gallery /var/lib/gallery
sudo chown gallery:gallery /opt/gallery /var/lib/gallery

# Clone + build the virtualenv as the gallery user
sudo -u gallery git clone https://github.com/mydataismydata/online-art-gallery.git /opt/gallery
sudo -u gallery python3 -m venv /opt/gallery/.venv
sudo -u gallery /opt/gallery/.venv/bin/pip install -r /opt/gallery/requirements.txt
```

**dezoomify-rs** — the helper the Google Arts & Culture source shells out to. The
installer above already fetches it; the manual step below is the same thing (the
Linux x86_64 build; the Windows `.exe` in the repo is git-ignored):

```bash
sudo bash /opt/gallery/deploy/fetch-dezoomify.sh
```

**Install the service:**

```bash
sudo cp /opt/gallery/deploy/gallery.service /etc/systemd/system/gallery.service
# The shipped unit already assumes /opt/gallery + /var/lib/gallery + User=gallery,
# so usually no edits are needed. Review it if your paths differ:
sudo nano /etc/systemd/system/gallery.service

sudo systemctl daemon-reload
sudo systemctl enable --now gallery
systemctl status gallery          # should say "active (running)"
journalctl -u gallery -f          # live log; Ctrl-C to stop watching
```

The service now listens on `127.0.0.1:8000` — reachable from the box itself but not yet
from anywhere else. That's deliberate; Tailscale provides the front door next.

> The unit points `GALLERY_DATA`, `GALLERY_LIBRARY`, `GALLERY_CACHE`, `GALLERY_TRASH`
> and `GALLERY_SOURCES` at `/var/lib/gallery`, so your library and accounts live
> **outside** the code tree and a later `git pull` never touches them.

---

## Part 2 — Put the box on your tailnet

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints a URL — open it in any browser, sign in, and the box joins your
tailnet. Then note its addresses:

```bash
tailscale ip -4          # e.g. 100.101.102.103
tailscale status         # shows this machine's name
```

Its **MagicDNS name** is `<hostname>.<your-tailnet>.ts.net` (your tailnet's name is at
the top of the [admin console](https://login.tailscale.com/admin/machines)). Rename the
machine to something tidy like `gallery` in the admin console if you want a clean URL.

If **ufw** is enabled on this box, allow tailnet traffic in:

```bash
sudo ufw allow in on tailscale0
```

---

## Part 3 — Expose the gallery over the tailnet (HTTPS, recommended)

`tailscale serve` puts a real HTTPS front-end on the app, reachable only from your
tailnet. First enable HTTPS for your tailnet **once**: admin console → **DNS** → enable
**MagicDNS** and **HTTPS Certificates**. Then, on the box:

```bash
sudo tailscale serve --bg 8000
tailscale serve status        # shows the public-within-tailnet URL
```

That maps `https://gallery.<your-tailnet>.ts.net` → `127.0.0.1:8000`, with a valid
Let's Encrypt cert and no port number. Traffic is HTTPS end-to-end over WireGuard.
This pairs with the unit's default `--host 127.0.0.1` bind — the app is never directly
exposed; only Tailscale can reach it. To undo: `sudo tailscale serve --https=443 off`.

**Simpler alternative (no HTTPS):** skip `tailscale serve`, edit the unit's `ExecStart`
to `--host 0.0.0.0`, `daemon-reload` + `restart`, and reach it at
`http://gallery.<your-tailnet>.ts.net:8000`. Traffic is still encrypted by Tailscale on
the wire, but the browser shows "not secure" and `0.0.0.0` also binds the port on any
LAN/public interface the box has (still behind the login wall). Prefer the `serve`
route unless it gives you trouble.

> **Pick one hostname and stick with it.** The login session is a cookie scoped to the
> host you used. If you log in at the `.ts.net` name and later visit by raw `100.x` IP,
> you'll just be asked to sign in again — not a bug.

---

## Part 4 — Create your Owner account

From your laptop (already on the tailnet), open the URL. The very first visit shows a
**one-time setup screen** — create your **Owner** account. You're in.

Then, as Owner, go to **Settings → Users** to create accounts for everyone else and set
each person's role (Curator or Visitor).

---

## Part 5 — Inviting curators (the access-control answer)

**Yes — you can give other people access to *only* this box and nothing else on your
tailnet.** There are two ways; the first is what you want.

### ✅ Recommended: *share* the single machine (not "add to network")

Tailscale's **node sharing** is built for exactly this. In the admin console →
**Machines** → click the `gallery` box → **⋯ / Share** → enter the person's email. They
accept the invite with their own (free) Tailscale account, and the shared box appears on
*their* tailnet.

- A shared user can reach **only that one shared machine** — they cannot see or connect
  to any of your other boxes, and you don't have to touch ACLs.
- Send them the same `https://gallery.<your-tailnet>.ts.net` URL; the cert and MagicDNS
  name work for shared-in users too.
- Then create their **Curator** account in the gallery (Settings → Users). Tailscale
  lets them reach the door; the Curator login decides what they can do inside.

This is strictly better than adding them as full members for your use case — fewer
moving parts and no risk of over-exposure.

### Alternative: add them as tailnet *members* + lock down with an ACL

If you'd rather have the curators be full members of your tailnet (e.g. you're on a
Teams plan and want them managed centrally), you **must** write an ACL — a brand-new
tailnet's default policy is *allow-all*, so members can otherwise reach every box.

A ready-to-adapt policy is in
[`deploy/tailscale-acl-example.hujson`](deploy/tailscale-acl-example.hujson): it tags
this box `tag:gallery`, puts curators in a `group:curators`, grants that group access to
`tag:gallery` **only**, and (critically) removes the default allow-all rule. Tag the box
with:

```bash
sudo tailscale up --advertise-tags=tag:gallery
```

> Do **not** use `tailscale funnel` — that publishes the box to the *public* internet.
> Everything here keeps it tailnet-only, which is what you asked for.

---

## Updating to a new version

```bash
sudo bash /opt/gallery/deploy/update.sh
```

That pulls the latest code, refreshes Python deps, makes sure `dezoomify-rs` is
present, and restarts the service. Your library and accounts are untouched — they
live in `/var/lib/gallery`. (Add `FORCE_DEZOOMIFY=1` before the command to also
re-download the newest dezoomify-rs.)

## Backups

Back up **`/var/lib/gallery`** — that one directory holds your artwork library, all
accounts and collections (`data/`), and custom sources. The code in `/opt/gallery` is
just a `git clone` you can recreate anytime. The session secret is in
`/var/lib/gallery/data/secret_key`; losing it only logs everyone out once.

## Troubleshooting

| Symptom | Check |
|---|---|
| `systemctl status gallery` not running | `journalctl -u gallery -e` — usually a path/permission in the unit |
| Can't reach the URL from your laptop | `tailscale status` on both ends; `tailscale serve status` on the box; `sudo ufw allow in on tailscale0` |
| Cert warning in the browser | You're hitting the raw `100.x` IP or `:8000` — use the `https://…ts.net` name from `tailscale serve status` |
| A curator sees "login required" | They reached the box fine (good) but have no gallery account yet — create one in Settings → Users |
| Permission-denied writing library | The service user (`gallery`) must own `/var/lib/gallery`: `sudo chown -R gallery:gallery /var/lib/gallery` |
