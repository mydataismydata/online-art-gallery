from . import gac, met, aic, cleveland, rijks, wikidata, vam, custom

_BUILTIN = (gac, met, aic, cleveland, rijks, wikidata, vam)


def _all_modules():
    """Built-in source modules plus any user-defined custom sources (reloaded
    from disk each call so Settings edits take effect without a restart)."""
    return list(_BUILTIN) + custom.build_sources()


def get_source(source_id):
    for m in _all_modules():
        if m.ID == source_id:
            return m
    raise KeyError(source_id)


def list_sources():
    out = []
    for m in _all_modules():
        info = {
            "id": m.ID,
            "label": m.LABEL,
            "hint": m.HINT,
            "placeholder": m.PLACEHOLDER,
            "supports_max_px": getattr(m, "SUPPORTS_MAX_PX", False),
            "max_px_default": getattr(m, "MAX_PX_DEFAULT", None),
            "custom": getattr(m, "custom", False),
            "available": True,
            "note": "",
        }
        check = getattr(m, "availability", None)
        if check:
            info["available"], info["note"] = check()
        out.append(info)
    return out
