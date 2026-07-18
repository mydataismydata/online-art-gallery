"""Owner-configured 'Auto fill' — asks an OpenAI-compatible chat API (gab.ai by
default) to research one painting and return catalogue metadata for the placard.

The model and API key live in data/ai_config.json (kept out of the library and
out of git). The key may instead come from the GALLERY_AI_KEY environment
variable, which then takes precedence — handy for a systemd-managed deployment
that would rather not keep the key in a file.

Endpoint, model, request timeout and the model-suggestion list are all owner-set
from Settings → Auto-fill, falling back to the DEFAULT_* constants below; the
endpoint and key may also come from the environment, which wins over the file.

The three research prompts (single work, batch, artist) are owner-editable too —
DEFAULT_PROMPTS below is only the starting point. What the owner CANNOT edit is
the JSON-format contract: every call appends a fixed _TECHNICAL suffix to the
user message, so a well-meaning edit of the prompt can't break the machine-read
output. The default prompts still carry the sourcing rules the owner asked for —
Wikipedia is fine for date / medium / style / genre / school but forbidden for a
description, which must come from a primary or authoritative source.
"""
import json
import os
import re
import time

import requests

from . import config

# Defaults for every AI knob — each overridable from Settings → Auto-fill and
# persisted in data/ai_config.json. The endpoint additionally honours the
# GALLERY_AI_ENDPOINT environment variable, which wins over the stored value.
DEFAULT_ENDPOINT = "https://gab.ai/v1/chat/completions"
DEFAULT_MODEL = "arya"
# Suggestions offered in Settings; the field is free-text, so any model id works.
DEFAULT_KNOWN_MODELS = ["arya", "gpt-5.5", "claude-opus-4.8", "gemini-3.1-pro", "deepseek", "kimi"]
DEFAULT_TIMEOUT = 90               # seconds, per single-work / artist call; batch scales up
_TIMEOUT_MIN, _TIMEOUT_MAX = 5, 600   # clamp a hand-entered timeout to something sane

# What we ask the model for, in order. Style, genre and school are three separate
# axes so each can be browsed on its own; the field names match the sidecar's.
_MODEL_FIELDS = ("artist", "title", "date", "medium", "style", "genre", "school",
                 "description")
_FIELD_MAP = {}

# The three prompt kinds the owner can edit, and the fixed JSON-format contract
# appended (programmatically, to the user message) for each so the output stays
# machine-readable no matter how the editable guidance is reworded.
_PROMPT_KINDS = ("work", "batch", "artist")
_TECHNICAL = {
    "work": (
        "Return ONLY a JSON object with these keys, all strings: "
        "artist, title, date, medium, style, genre, school, description. "
        "Output only the JSON object — no prose, no markdown, no code fences."
    ),
    "batch": (
        "Return ONLY a JSON array, one object per numbered painting. Each element "
        "has keys: n (the item number as given), date, medium, style, genre, school, "
        "description. Include every item number exactly once. "
        "Output only the JSON array — no prose, no markdown, no code fences."
    ),
    "artist": (
        "Return ONLY a JSON object with these keys: born, died, birthplace, "
        "nationality, movements (an array of strings), description (a string). "
        "Output only the JSON object — no prose, no markdown, no code fences."
    ),
}

# Appended to every system prompt below as a hard anti-fabrication rule. The whole
# point of Auto-fill is a placard a visitor will trust, so a blank field always
# beats a confident invention — each prompt ends by spelling that out.
_NO_FABRICATION = (
    "Guarding against fabrication — this is the most important rule of all:\n"
    "- What you return is shown verbatim in the gallery and read by visitors as "
    "established fact. A blank field is completely acceptable; a confident-sounding "
    "invention is a serious error. Whenever the two are in tension, choose the blank.\n"
    "- Check every field before you return it. If any specific claim in it — a name, "
    "date, place, institution, attribution or artwork title — is not something you can "
    "verify rather than merely guess at plausibly, leave that field empty (an empty "
    "string, or an empty array where a list is asked for) instead of inventing a "
    "detail. Apply the same test to every sentence you write inside a description: if "
    "it is not directly supported by a real source, do not write it.\n"
    "- Do not infer a value from what is merely typical of the artist, period, "
    "movement or subject; state only what is documented for THIS exact subject. If you "
    'are unsure of a field, prefer leaving it blank to filling it — "" is a valid, '
    "expected answer, not a failure.\n"
    "- If you cannot confidently identify the exact painting or artist you were asked "
    "about, return empty fields rather than describing a different one that happens to "
    "share the name. Never invent facts.\n\n"
)

# Default editable "research guidance" for a single work — the persona, sourcing
# rules and anti-fabrication guard. The JSON envelope is NOT here; _TECHNICAL["work"]
# is appended to the user message at call time.
_WORK_GUIDANCE = (
    "You are a museum registrar's cataloguing assistant. You are given the artist "
    "and title of a single painting held in a private gallery. Identify that exact "
    "painting and return accurate catalogue metadata.\n\n"
    "Sourcing rules — follow them exactly:\n"
    "- date, medium, style, genre, school: Wikipedia, Wikimedia and Wikidata are "
    "acceptable sources, as are museum catalogues. Keep each short and factual. "
    'date = the year or year-range the work was made (e.g. "1665" or "1600-1610"). '
    'medium = the materials, e.g. "Oil on canvas". '
    "These next three are SEPARATE axes — never merge them into one field, and "
    "leave any you are unsure of as an empty string. "
    'style = the movement or manner only, e.g. "Baroque", "Impressionism", '
    '"Dutch Golden Age". '
    'genre = the subject category only, e.g. "Marine painting", "Portrait", '
    '"Landscape", "Still life", "History painting". '
    'school = the national or regional school only, e.g. "Dutch", "Flemish", '
    '"Venetian", "Heidelberg School".\n'
    "- description: DO NOT use Wikipedia or Wikimedia in ANY form for the "
    "description. It MUST be drawn from a primary or authoritative source — the "
    "holding museum's own catalogue entry or curatorial text, a catalogue "
    "raisonne, or comparable scholarly writing. Write TWO OR MORE paragraphs, "
    "specific to THIS painting — its composition, subject and context — not a "
    "general biography of the artist. If you cannot find a suitable "
    "non-Wikipedia source, return an empty string for description rather than "
    "using Wikipedia.\n"
    "- artist, title: return the canonical, corrected form if the supplied values "
    "are informal or misspelled; otherwise echo them back.\n\n"
    + _NO_FABRICATION
).strip()


class AIError(Exception):
    """Raised for any config or transport problem; the message is user-facing."""


def _load():
    try:
        return json.loads(config.AI_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data):
    config.AI_CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _api_key():
    return (os.environ.get("GALLERY_AI_KEY") or _load().get("api_key") or "").strip()


def model():
    return (_load().get("model") or "").strip() or DEFAULT_MODEL


def _endpoint_from_env():
    return bool(os.environ.get("GALLERY_AI_ENDPOINT"))


def endpoint():
    """The chat-completions URL. Environment wins, then the stored value, then the
    baked-in default — same precedence the key uses."""
    return (os.environ.get("GALLERY_AI_ENDPOINT") or _load().get("endpoint")
            or "").strip() or DEFAULT_ENDPOINT


def known_models():
    """The datalist of model suggestions shown in Settings — a stored list if the
    owner set one, otherwise the defaults. Suggestions only; any id may be typed."""
    km = _load().get("known_models")
    if isinstance(km, list):
        clean = [m.strip() for m in km if isinstance(m, str) and m.strip()]
        if clean:
            return clean
    return list(DEFAULT_KNOWN_MODELS)


def timeout():
    """Per-request timeout in seconds for the single-work and artist calls, clamped
    to a sane range. Batch scales up from this (see autofill_many)."""
    try:
        t = int(_load().get("timeout"))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(_TIMEOUT_MIN, min(_TIMEOUT_MAX, t))


def prompt(kind):
    """The editable research guidance for one prompt kind ('work'/'batch'/'artist'):
    the owner's stored override if any, else the default. The JSON-format contract is
    NOT part of this — it is appended separately from _TECHNICAL at call time."""
    stored = (_load().get("prompts") or {}).get(kind)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return DEFAULT_PROMPTS[kind]


def public_config():
    """Config safe to hand the browser — never the raw key. Carries the defaults and
    the fixed technical suffixes too, so Settings can show placeholders, offer
    'reset to default', and display what gets appended automatically."""
    key = _api_key()
    return {
        "model": model(),
        "default_model": DEFAULT_MODEL,
        "known_models": known_models(),
        "default_known_models": list(DEFAULT_KNOWN_MODELS),
        "endpoint": endpoint(),
        "default_endpoint": DEFAULT_ENDPOINT,
        "endpoint_from_env": _endpoint_from_env(),
        "timeout": timeout(),
        "default_timeout": DEFAULT_TIMEOUT,
        "has_key": bool(key),
        "key_hint": ("…" + key[-4:]) if len(key) >= 4 else ("set" if key else ""),
        "key_from_env": bool(os.environ.get("GALLERY_AI_KEY")),
        "prompts": {k: prompt(k) for k in _PROMPT_KINDS},
        "default_prompts": {k: DEFAULT_PROMPTS[k] for k in _PROMPT_KINDS},
        "technical": {k: _TECHNICAL[k] for k in _PROMPT_KINDS},
        "prompts_customized": {k: prompt(k) != DEFAULT_PROMPTS[k] for k in _PROMPT_KINDS},
    }


def _clean_model_list(val):
    """Normalise the known-models field, accepting either a list or a newline/comma
    separated string. Trims, drops blanks, de-dupes, preserves order."""
    if isinstance(val, str):
        parts = re.split(r"[\n,]+", val)
    elif isinstance(val, list):
        parts = val
    else:
        return []
    seen, out = set(), []
    for p in parts:
        if isinstance(p, str):
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def set_config(model=None, api_key=None, endpoint=None, timeout=None,
               known_models=None, prompts=None):
    """Persist any subset of the AI settings. Each argument left as None is untouched;
    a blank value resets that field to its default (so it stops being stored). This
    lets Settings save the API config and the prompts from separate forms. Returns the
    fresh public_config."""
    data = _load()
    if model is not None:
        data["model"] = (model or "").strip() or DEFAULT_MODEL
    if endpoint is not None:
        e = (endpoint or "").strip()
        if e and e != DEFAULT_ENDPOINT:
            data["endpoint"] = e
        else:
            data.pop("endpoint", None)          # blank or same-as-default → unset
    if timeout is not None:
        try:
            t = int(timeout)
        except (TypeError, ValueError):
            t = 0
        if t > 0:
            data["timeout"] = max(_TIMEOUT_MIN, min(_TIMEOUT_MAX, t))
        else:
            data.pop("timeout", None)
    if known_models is not None:
        km = _clean_model_list(known_models)
        if km and km != list(DEFAULT_KNOWN_MODELS):
            data["known_models"] = km
        else:
            data.pop("known_models", None)
    if isinstance(prompts, dict):
        store = dict(data.get("prompts") or {})
        for kind in _PROMPT_KINDS:
            if kind in prompts:
                t = (prompts[kind] or "").strip()
                if t and t != DEFAULT_PROMPTS[kind]:   # store a real customisation only
                    store[kind] = t
                else:
                    store.pop(kind, None)              # blank or exact-default → reset
        if store:
            data["prompts"] = store
        else:
            data.pop("prompts", None)
    if api_key is not None:
        k = (api_key or "").strip()
        if k:
            data["api_key"] = k
        else:
            data.pop("api_key", None)
    _save(data)
    return public_config()


def _extract_json(text):
    """Pull a JSON object out of the model's reply, tolerating code fences or a
    stray sentence around it."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


_TRACE_MAX = 20000   # cap recorded bodies so a runaway reply can't bloat the response


def _clip(s):
    s = s or ""
    return s if len(s) <= _TRACE_MAX else s[:_TRACE_MAX] + "\n… (truncated, %d chars total)" % len(s)


def _chat(key, messages, max_tokens, timeout, trace=None):
    """POST a chat-completions request; return the assistant's text. Raises AIError.

    Pass a dict as `trace` to record exactly what was sent and what came back —
    including on failure, which is when it matters. The API key is never recorded:
    it travels in the Authorization header, which we deliberately don't copy in."""
    url = endpoint()
    body = {"model": model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if trace is not None:
        trace.update({"endpoint": url, "model": body["model"],
                      "timeout": timeout, "request": body})

    def fail(msg):
        if trace is not None:
            trace["error"] = msg
        return AIError(msg)

    t0 = time.time()
    try:
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json=body, timeout=timeout)
    except requests.RequestException as e:
        if trace is not None:
            trace["ms"] = int((time.time() - t0) * 1000)
        raise fail("Couldn't reach the AI service: %s" % e)
    if trace is not None:
        trace["ms"] = int((time.time() - t0) * 1000)
        trace["status"] = r.status_code
        trace["response"] = _clip(r.text)
    if r.status_code in (401, 403):
        raise fail("The API rejected the key (%s). Check it in Settings." % r.status_code)
    if r.status_code == 402:
        raise fail("The AI account is out of credits (402).")
    if r.status_code >= 400:
        raise fail("AI service error %s: %s" % (r.status_code, (r.text or "")[:200]))
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise fail("Unexpected response from the AI service.")
    if trace is not None:
        trace["content"] = _clip(content)
    return content


def autofill(work, hint=None, trace=None):
    """Ask the model for catalogue metadata for one work. Returns a dict of the
    fields it could supply (schema keys: artist/title/date/medium/style/genre/
    school/description), omitting blanks. Raises AIError on any config/transport
    error.

    `hint` is the owner's own words about which painting this is — the way to break
    a tie when an artist has many works sharing a title (van Gogh's "Self
    Portrait", Degas' "The Dance Class"). Omitted, the request is unchanged.

    Pass a dict as `trace` to have the request/response recorded for the editor's
    debug panel (see _chat)."""
    key = _api_key()
    if not key:
        msg = "No API key set. Add one under Settings → Auto-fill."
        if trace is not None:
            trace["error"] = msg
        raise AIError(msg)

    artist = work.get("artist") or ""
    title = work.get("title") or ""
    known_date = work.get("date") or (str(work["year"]) if work.get("year") else "")
    user = "Painting to catalogue:\nArtist: %s\nTitle: %s\n" % (
        artist or "(unknown)", title or "(unknown)")
    if known_date:
        user += "Known date: %s\n" % known_date
    if work.get("medium"):
        user += "Known medium: %s\n" % work["medium"]
    hint = (hint or "").strip()
    if hint:
        user += ("\nThe gallery owner describes THIS painting as follows. This artist "
                 "has other works with the same or a similar title, so use these "
                 "details to identify the right one, and catalogue THAT painting — "
                 "not another of the same name. Treat it as authoritative:\n%s\n" % hint)
    user += "\n" + _TECHNICAL["work"]

    content = _chat(key, [{"role": "system", "content": prompt("work")},
                          {"role": "user", "content": user}], 1500, timeout(), trace=trace)
    parsed = _extract_json(content)
    if parsed is None:
        msg = "The AI didn't return usable JSON. Try again, or another model."
        if trace is not None:
            trace["error"] = msg
        raise AIError(msg)

    out = {}
    for f in _MODEL_FIELDS:
        v = parsed.get(f)
        if isinstance(v, str) and v.strip():
            out[_FIELD_MAP.get(f, f)] = v.strip()
    if trace is not None:
        trace["fields"] = out
    return out


# ---------- artist bios ----------
# What the bio form holds. `movements` is a list; the rest are plain strings.
_ARTIST_STR_FIELDS = ("born", "died", "birthplace", "nationality", "description")

# Unlike a painting's description, an artist bio MAY draw on Wikipedia — but it
# isn't allowed to stop there. The movements it returns feed the Connections map's
# clustering, so they have to be the canonical names rather than free prose.
_ARTIST_GUIDANCE = (
    "You are a museum registrar's research assistant. You are given the name of one "
    "painter whose work hangs in a private gallery. Identify that exact artist and "
    "return accurate biographical metadata.\n\n"
    "Sourcing rules — follow them exactly:\n"
    "- Wikidata and Wikipedia are acceptable starting points, but DO NOT stop there. "
    "Corroborate and extend them with primary and authoritative sources: the "
    "collection records and curatorial texts of museums holding this artist's work, "
    "catalogues raisonnes, exhibition catalogues, archival material, letters, and "
    "scholarly writing. Where those sources disagree with Wikipedia, follow the "
    "scholarship and not the summary.\n"
    '- born, died: the YEAR only, digits, e.g. "1815". Empty string if genuinely '
    "unknown or seriously disputed — do not guess a plausible year.\n"
    '- birthplace: the city or town only, e.g. "Berlin". Not the country.\n'
    '- nationality: a single adjective, e.g. "German", "Dutch", "Australian".\n'
    "- movements: the established art-historical movements or schools this painter "
    'belongs to, most characteristic first, e.g. ["Realism"], ["Impressionism"], '
    '["Heidelberg School"], ["Dutch Golden Age"]. Use the canonical NAME of the '
    "movement, never a description of their manner. Return an empty array if the "
    "painter genuinely belongs to no named movement.\n"
    "- description: TWO OR MORE paragraphs about this artist — their training, what "
    "they actually painted and how, their standing among contemporaries, and what "
    "they are remembered for. Concrete and specific, drawn from the sources above. "
    "No filler, no hedging, no list of dates already given in the other fields.\n"
    "- description formatting: separate paragraphs with a blank line. Italicise the "
    "title of every artwork you name by wrapping it in <em> and </em> — house style, "
    "e.g. <em>The Dinner at the Ball</em>. Do NOT put artwork titles in quotation "
    "marks, and do NOT use markdown (no *asterisks*, no _underscores_). Use no HTML "
    "other than <em>.\n\n"
    + _NO_FABRICATION
).strip()


def autofill_artist(name, hint=None, trace=None):
    """Research one artist and return bio fields for the owner to review — this
    never saves. Returns a dict with any of born/died/birthplace/nationality/
    movements/description the model could supply, blanks omitted. `movements` is a
    list of strings. Raises AIError on a config/transport problem.

    `hint` is the owner's own words, for when a name is ambiguous (two painters
    called Brueghel, a son working in his father's manner). Omitted, the request is
    unchanged. Pass a dict as `trace` to record the call (see _chat)."""
    key = _api_key()
    if not key:
        msg = "No API key set. Add one under Settings → Auto-fill."
        if trace is not None:
            trace["error"] = msg
        raise AIError(msg)

    name = (name or "").strip()
    user = "Artist to research:\nName: %s\n" % (name or "(unknown)")
    hint = (hint or "").strip()
    if hint:
        user += ("\nThe gallery owner describes THIS artist as follows. Use it to "
                 "identify the right painter — others may share the name — and "
                 "research THAT one. Treat it as authoritative:\n%s\n" % hint)
    user += "\n" + _TECHNICAL["artist"]

    content = _chat(key, [{"role": "system", "content": prompt("artist")},
                          {"role": "user", "content": user}], 1800, timeout(), trace=trace)
    parsed = _extract_json(content)
    if parsed is None:
        msg = "The AI didn't return usable JSON. Try again, or another model."
        if trace is not None:
            trace["error"] = msg
        raise AIError(msg)

    out = {}
    for f in _ARTIST_STR_FIELDS:
        v = parsed.get(f)
        if isinstance(v, str) and v.strip():
            out[f] = v.strip()
    # Tolerate a comma-separated string where the schema asks for an array — models
    # slip into prose here more often than anywhere else in the object.
    mv = parsed.get("movements")
    if isinstance(mv, str):
        mv = [p.strip() for p in mv.split(",")]
    if isinstance(mv, list):
        clean = [m.strip() for m in mv if isinstance(m, str) and m.strip()]
        if clean:
            out["movements"] = clean
    if trace is not None:
        trace["fields"] = out
    return out


# ---------- batch: one call for several works by the same artist ----------
# Only the fill-in fields — artist and title are already known from the gallery.
_BATCH_FIELDS = ("date", "medium", "style", "genre", "school", "description")

_BATCH_GUIDANCE = (
    "You are a museum registrar's cataloguing assistant. You are given one artist "
    "and a numbered list of that artist's paintings. For EVERY item, return accurate "
    "catalogue metadata.\n\n"
    "Sourcing rules — follow them exactly:\n"
    "- date, medium, style, genre, school: Wikipedia, Wikimedia and Wikidata are "
    "acceptable, as are museum catalogues. Keep each short and factual. "
    "date = the year or year-range; medium = the materials, e.g. \"Oil on canvas\". "
    "style, genre and school are SEPARATE axes — never merge them into one field, "
    "and leave any you are unsure of as an empty string. "
    "style = the movement or manner only, e.g. \"Baroque\", \"Dutch Golden Age\"; "
    "genre = the subject category only, e.g. \"Marine painting\", \"Portrait\"; "
    "school = the national or regional school only, e.g. \"Dutch\", \"Flemish\".\n"
    "- description: DO NOT use Wikipedia or Wikimedia in ANY form. It MUST come from a "
    "primary or authoritative source — the holding museum's catalogue entry, a "
    "catalogue raisonne, or comparable scholarship. Write ONE or TWO concise "
    "paragraphs specific to THAT painting (its composition, subject and context), not "
    "a biography of the artist. If you cannot find a suitable non-Wikipedia source, "
    "use an empty string for description.\n\n"
    + _NO_FABRICATION
).strip()


# The editable defaults, keyed by prompt kind. Assembled here, after all three
# guidance blocks exist; prompt() and public_config() read through this.
DEFAULT_PROMPTS = {
    "work": _WORK_GUIDANCE,
    "batch": _BATCH_GUIDANCE,
    "artist": _ARTIST_GUIDANCE,
}


def _salvage_objects(text):
    """Best-effort: parse each top-level {...} block, dropping a broken final one
    (so a truncated array still yields the works that came through intact)."""
    out, depth, start = [], 0, -1
    for idx, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start:idx + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except ValueError:
                    pass
                start = -1
    return out


def _extract_array(text):
    """Pull a JSON array of objects out of the reply, tolerating code fences and a
    truncated tail."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    i, j = text.find("["), text.rfind("]")
    if i != -1 and j > i:
        try:
            v = json.loads(text[i:j + 1])
            if isinstance(v, list):
                return [o for o in v if isinstance(o, dict)]
        except ValueError:
            pass
    return _salvage_objects(text)


def autofill_many(artist, works):
    """One API call for several works by the same artist. Returns a list aligned to
    `works`; each element is a dict of the fillable fields (date/medium/style/
    description) the model supplied for that work, or {} if none. Raises AIError on a
    config/transport error."""
    key = _api_key()
    if not key:
        raise AIError("No API key set. Add one under Settings → Auto-fill.")
    lines = []
    for i, w in enumerate(works, 1):
        hints = []
        d = w.get("date") or (str(w["year"]) if w.get("year") else "")
        if d:
            hints.append("date so far: %s" % d)
        if w.get("medium"):
            hints.append("medium so far: %s" % w["medium"])
        tail = (" — " + "; ".join(hints)) if hints else ""
        lines.append("%d. %s%s" % (i, w.get("title") or "(untitled)", tail))
    user = ("Artist: %s\nPaintings:\n%s\n\n%s"
            % (artist or "(unknown)", "\n".join(lines), _TECHNICAL["batch"]))
    max_tokens = min(900 + 320 * len(works), 8000)
    # A batch does more work than one lookup, so scale up from the configured base:
    # +6s per painting, but never below the base and never past a 300s ceiling
    # (unless the owner deliberately set the base higher still).
    base = timeout()
    call_timeout = min(base + 6 * len(works), max(300, base))
    content = _chat(key, [{"role": "system", "content": prompt("batch")},
                          {"role": "user", "content": user}], max_tokens, call_timeout)

    out = [{} for _ in works]
    for obj in _extract_array(content):
        try:
            n = int(obj.get("n"))
        except (TypeError, ValueError):
            continue
        if not (1 <= n <= len(works)):
            continue
        fields = {}
        for f in _BATCH_FIELDS:
            v = obj.get(f)
            if isinstance(v, str) and v.strip():
                fields[_FIELD_MAP.get(f, f)] = v.strip()
        out[n - 1] = fields
    return out
