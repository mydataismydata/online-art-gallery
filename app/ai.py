"""Owner-configured 'Auto fill' — asks an OpenAI-compatible chat API (gab.ai by
default) to research one painting and return catalogue metadata for the placard.

The model and API key live in data/ai_config.json (kept out of the library and
out of git). The key may instead come from the GALLERY_AI_KEY environment
variable, which then takes precedence — handy for a systemd-managed deployment
that would rather not keep the key in a file.

The system prompt hard-codes the sourcing rules the owner asked for:
  * date / medium / genre  — Wikipedia / Wikimedia / Wikidata are acceptable
  * description            — MUST come from a primary / authoritative source
                             (the holding museum's catalogue entry, a catalogue
                             raisonné, scholarly writing); Wikipedia is forbidden.
"""
import json
import os
import re

import requests

from . import config

ENDPOINT = os.environ.get("GALLERY_AI_ENDPOINT", "https://gab.ai/v1/chat/completions")
DEFAULT_MODEL = "arya"
# Suggestions offered in Settings; the field is free-text, so any model id works.
KNOWN_MODELS = ["arya", "gpt-5.5", "claude-opus-4.8", "gemini-3.1-pro", "deepseek", "kimi"]
_TIMEOUT = 90

# What we ask the model for, in order. 'genre' is stored in the work's 'style'
# field (see autofill()); everything else keeps its name.
_MODEL_FIELDS = ("artist", "title", "date", "medium", "genre", "description")
_FIELD_MAP = {"genre": "style"}

_SYSTEM = (
    "You are a museum registrar's cataloguing assistant. You are given the artist "
    "and title of a single painting held in a private gallery. Identify that exact "
    "painting and return accurate catalogue metadata as STRICT JSON.\n\n"
    "Return ONLY a JSON object with these keys, all strings: "
    "artist, title, date, medium, genre, description.\n\n"
    "Sourcing rules — follow them exactly:\n"
    "- date, medium, genre: Wikipedia, Wikimedia and Wikidata are acceptable "
    "sources, as are museum catalogues. Keep each short and factual. "
    'date = the year or year-range the work was made (e.g. "1665" or "1600-1610"). '
    'medium = the materials, e.g. "Oil on canvas". '
    "genre = the genre and/or school or movement, kept brief, e.g. "
    '"Marine painting, Dutch Golden Age".\n'
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


def autofill(work):
    """Ask the model for catalogue metadata for one work. Returns a dict of the
    fields it could supply (schema keys: artist/title/date/medium/style/
    description), omitting blanks. Raises AIError on any config/transport error."""
    key = _api_key()
    if not key:
        raise AIError("No API key set. Add one under Settings → Auto-fill.")

    artist = work.get("artist") or ""
    title = work.get("title") or ""
    known_date = work.get("date") or (str(work["year"]) if work.get("year") else "")
    user = "Painting to catalogue:\nArtist: %s\nTitle: %s\n" % (
        artist or "(unknown)", title or "(unknown)")
    if known_date:
        user += "Known date: %s\n" % known_date
    if work.get("medium"):
        user += "Known medium: %s\n" % work["medium"]
    user += "\nReturn the JSON described in your instructions."

    body = {
        "model": model(),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    try:
        r = requests.post(
            ENDPOINT,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
            json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise AIError("Couldn't reach the AI service: %s" % e)
    if r.status_code in (401, 403):
        raise AIError("The API rejected the key (%s). Check it in Settings." % r.status_code)
    if r.status_code == 402:
        raise AIError("The AI account is out of credits (402).")
    if r.status_code >= 400:
        raise AIError("AI service error %s: %s" % (r.status_code, (r.text or "")[:200]))

    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise AIError("Unexpected response from the AI service.")
    parsed = _extract_json(content)
    if parsed is None:
        raise AIError("The AI didn't return usable JSON. Try again, or another model.")

    out = {}
    for f in _MODEL_FIELDS:
        v = parsed.get(f)
        if isinstance(v, str) and v.strip():
            out[_FIELD_MAP.get(f, f)] = v.strip()
    return out
