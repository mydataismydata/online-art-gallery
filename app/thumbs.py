"""Thumbnail cache. Thumbs are width-bound JPEGs regenerated when the source mtime changes."""
import os

from PIL import Image, ImageOps

from . import config

# The whole point of this gallery is enormous images; the library is trusted.
Image.MAX_IMAGE_PIXELS = None


def thumb_for(work):
    """Return the path of a cached thumbnail for a work dict, generating it if needed."""
    key = "%s-%d" % (work["id"], int(work["mtime"]))
    out = config.THUMB_DIR / (key + ".jpg")
    if out.exists():
        return out

    src = config.LIBRARY_DIR / work["rel"]
    im = Image.open(str(src))
    # JPEG fast-path: decode at reduced scale instead of full size.
    try:
        im.draft("RGB", (config.THUMB_WIDTH * 2, config.THUMB_WIDTH * 2))
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if "A" in im.mode or im.mode == "P":
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (24, 20, 17))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((config.THUMB_WIDTH, config.THUMB_WIDTH * 4), Image.LANCZOS)

    tmp = config.THUMB_DIR / (key + ".part.jpg")
    im.save(str(tmp), "JPEG", quality=84, optimize=True)
    os.replace(str(tmp), str(out))
    return out


# Formats a browser renders directly in an <img>. Anything else — e.g. a TIFF
# that a museum (Cleveland does this) served with a .jpg name — is converted to
# JPEG once and cached, so the full-size viewer can actually display it.
_DISPLAY_MAX = 10000  # cap the long side of a conversion to stay within browser decode limits


def _is_web_displayable(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return True  # let the caller's existence check handle it
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def display_for(work):
    """Path to a browser-displayable version of a work's image. Returns the
    original for web formats (JPEG/PNG/WebP/GIF); for anything else (e.g. a TIFF
    saved with a .jpg name) returns a cached, full-size JPEG conversion."""
    src = config.LIBRARY_DIR / work["rel"]
    if _is_web_displayable(str(src)):
        return src
    key = "%s-%d.disp.jpg" % (work["id"], int(work["mtime"]))
    out = config.THUMB_DIR / key
    if out.exists():
        return out
    im = Image.open(str(src))
    im = ImageOps.exif_transpose(im)
    if "A" in im.mode or im.mode == "P":
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (24, 20, 17))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    if max(im.size) > _DISPLAY_MAX:
        im.thumbnail((_DISPLAY_MAX, _DISPLAY_MAX), Image.LANCZOS)
    tmp = config.THUMB_DIR / (key + ".part")
    im.save(str(tmp), "JPEG", quality=90, optimize=True)
    os.replace(str(tmp), str(out))
    return out
