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
