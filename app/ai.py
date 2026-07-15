"""Owner-configured 'Auto fill' — asks an OpenAI-compatible chat API (gab.ai by
default) to research one painting and return catalogue metadata for the placard.

The model and API key live in data/ai_config.json (kept out of the library and
out of git). The key may instead come from the GALLERY_AI_KEY environment
variable, which then takes precedence — handy for a systemd-managed deployment
that would rather not keep the key in a file.

The system prompt hard-codes the sourcing rules the owner asked for:
  * date / medium / style / genre / school — Wikipedia / Wikimedia / Wikidata are
                             acceptable
  * description            — MUST come from a primary / authoritative source
                             (the holding museum's catalogue entry, a catalogue
                             raisonné, scholarly writing); Wikipedia is forbidden.
"""
import json
import os
import re
import time

import requests

from . import config

ENDPOINT = os.environ.get("GALLERY_AI_ENDPOINT", "https://gab.ai/v1/chat/completions")
DEFAULT_MODEL = "arya"
# Suggestions offered in Settings; the field is free-text, so any model id works.
KNOWN_MODELS = ["arya", "gpt-5.5", "claude-opus-4.8", "gemini-3.1-pro", "deepseek", "kimi"]
_TIMEOUT = 90

# What we ask the model for, in order. Style, genre and school are three separate
# axes so each can be browsed on its own; the field names match the sidecar's.
_MODEL_FIELDS = ("artist", "title", "date", "medium", "style", "genre", "school",
                 "description")
_FIELD_MAP = {}

_SYSTEM = (
    "You are a museum registrar's cataloguing assistant. You are given the artist "
    "and title of a single painting held in a private gallery. Identify that exact "
    "painting and return accurate catalogue metadata as STRICT JSON.\n\n"
    "Return ONLY a JSON object with these keys, all strings: "
    "artist, title, date, medium, style, genre, school, description.\n\n"
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
    "are informal or misspelled; otherwise echo them back.\n"
    "- If you are not confident a field is correct for THIS specific painting, "
    "return an empty string for it. Never invent facts.\n\n"
    "Output only the JSON object — no prose, no markdown, no code fences."
)


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


def public_config():
    """Config safe to hand the browser — never the raw key."""
    key = _api_key()
    return {
        "model": model(),
        "default_model": DEFAULT_MODEL,
        "known_models": KNOWN_MODELS,
        "endpoint": ENDPOINT,
        "has_key": bool(key),
        "key_hint": ("…" + key[-4:]) if len(key) >= 4 else ("set" if key else ""),
        "key_from_env": bool(os.environ.get("GALLERY_AI_KEY")),
    }


def set_config(model=None, api_key=None):
    """Persist the model and/or key. A blank api_key clears the stored key; None
    leaves it untouched (so the browser can save a model without resending it)."""
    data = _load()
    if model is not None:
        data["model"] = (model or "").strip() or DEFAULT_MODEL
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
    body = {"model": model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if trace is not None:
        trace.update({"endpoint": ENDPOINT, "model": body["model"],
                      "timeout": timeout, "request": body})

    def fail(msg):
        if trace is not None:
            trace["error"] = msg
        return AIError(msg)

    t0 = time.time()
    try:
        r = requests.post(
            ENDPOINT,
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
    user += "\nReturn the JSON described in your instructions."

    content = _chat(key, [{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": user}], 1500, _TIMEOUT, trace=trace)
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
_ARTIST_SYSTEM = (
    "You are a museum registrar's research assistant. You are given the name of one "
    "painter whose work hangs in a private gallery. Identify that exact artist and "
    "return accurate biographical metadata as STRICT JSON.\n\n"
    "Return ONLY a JSON object with these keys: born, died, birthplace, nationality, "
    "movements (an array of strings), description (a string).\n\n"
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
    "other than <em>.\n"
    "- If you are not confident a field is correct for THIS artist, return an empty "
    "string (or empty array) for it. Never invent facts.\n\n"
    "Output only the JSON object — no prose, no markdown, no code fences."
)


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
    user += "\nReturn the JSON described in your instructions."

    content = _chat(key, [{"role": "system", "content": _ARTIST_SYSTEM},
                          {"role": "user", "content": user}], 1800, _TIMEOUT, trace=trace)
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

_BATCH_SYSTEM = (
    "You are a museum registrar's cataloguing assistant. You are given one artist "
    "and a numbered list of that artist's paintings. For EVERY item, return accurate "
    "catalogue metadata as STRICT JSON.\n\n"
    "Return ONLY a JSON array. Each element is an object with keys: "
    "n (the item number as given), date, medium, style, genre, school, description.\n\n"
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
    "use an empty string for description.\n"
    "- If you are not confident a field is correct for a specific painting, use an "
    "empty string. Never invent facts. Include every item number exactly once.\n\n"
    "Output only the JSON array — no prose, no markdown, no code fences."
)


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
    user = ("Artist: %s\nPaintings:\n%s\n\nReturn the JSON array described in your "
            "instructions, one object per numbered painting."
            % (artist or "(unknown)", "\n".join(lines)))
    max_tokens = min(900 + 320 * len(works), 8000)
    timeout = min(120 + 6 * len(works), 300)
    content = _chat(key, [{"role": "system", "content": _BATCH_SYSTEM},
                          {"role": "user", "content": user}], max_tokens, timeout)

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
