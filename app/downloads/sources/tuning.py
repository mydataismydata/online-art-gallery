"""Owner-tunable knobs for the built-in download sources.

Each built-in module declares a CONFIG (list of parameter specs) and optional
ENDPOINTS (label, url pairs, shown read-only). Owners override the values from
Settings; overrides persist in data/source_config.json and are read at job time
via effective(). This gives the built-ins the same visibility — and now the same
control — that custom sources already have.
"""
import json
import threading

from ... import config

_lock = threading.RLock()
_FILE = config.DATA_DIR / "source_config.json"


def _load():
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _coerce(spec, value):
    t = spec.get("type", "text")
    if t == "bool":
        return bool(value)
    if t == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return spec["default"]
        if spec.get("min") is not None:
            n = max(spec["min"], n)
        if spec.get("max") is not None:
            n = min(spec["max"], n)
        return n
    if t == "select":
        return value if value in (spec.get("options") or []) else spec["default"]
    return str(value).strip() if value is not None else ""


def effective(source_id, schema):
    """Declared defaults merged with stored overrides, coerced to declared types."""
    with _lock:
        saved = _load().get(source_id) or {}
    out = {}
    for spec in schema:
        k = spec["key"]
        out[k] = _coerce(spec, saved[k]) if k in saved else spec["default"]
    return out


def set_overrides(source_id, values, schema):
    """Store coerced overrides for the keys the schema recognises; return the new
    effective config."""
    by_key = {s["key"]: s for s in schema}
    clean = {k: _coerce(by_key[k], v) for k, v in (values or {}).items() if k in by_key}
    with _lock:
        data = _load()
        data[source_id] = clean
        _save(data)
    return effective(source_id, schema)


def reset(source_id):
    with _lock:
        data = _load()
        if source_id in data:
            del data[source_id]
            _save(data)


def describe(module):
    """Public view of one built-in's config: id, label, read-only endpoints, and
    each parameter's spec plus its current effective value."""
    schema = getattr(module, "CONFIG", []) or []
    eff = effective(module.ID, schema)
    params = [{
        "key": s["key"], "label": s["label"], "type": s.get("type", "text"),
        "help": s.get("help", ""), "default": s["default"], "value": eff[s["key"]],
        "options": s.get("options"), "min": s.get("min"), "max": s.get("max"),
    } for s in schema]
    return {
        "id": module.ID, "label": module.LABEL,
        "endpoints": [{"label": l, "url": u}
                      for l, u in (getattr(module, "ENDPOINTS", ()) or ())],
        "params": params,
    }
