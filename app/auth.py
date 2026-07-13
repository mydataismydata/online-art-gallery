"""Accounts, password hashing, sessions and role guards.

Users live in one JSON file (data/users.json), keyed by casefolded username so
logins are case-insensitive and names are unique regardless of case. Passwords are
stored only as pbkdf2 hashes. Sessions are Flask's signed cookies — we keep just the
casefolded username in the cookie and re-resolve the record on every request, so a
deleted or re-roled user takes effect immediately."""
import functools
import json
import re
import threading
import time

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import config

ROLES = ("owner", "curator", "visitor")
_RANK = {"visitor": 1, "curator": 2, "owner": 3}

# pbkdf2 is chosen explicitly: always available, no dependency on an OpenSSL build
# that supports scrypt (Werkzeug's newer default).
_HASH_METHOD = "pbkdf2:sha256"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
_MIN_PASSWORD = 6

_lock = threading.RLock()


# ---------------- store ----------------

def _load():
    try:
        data = json.loads(config.USERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"users": {}}
    except Exception:
        return {"users": {}}
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        return {"users": {}}
    return data


def _save(data):
    config.USERS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _key(username):
    return re.sub(r"\s+", " ", (username or "").strip()).casefold()


def public(record):
    """A user record safe to send to the client — no password hash."""
    if not record:
        return None
    return {"username": record.get("username"), "role": record.get("role"),
            "created": record.get("created")}


# ---------------- validation ----------------

def _clean_username(username):
    name = re.sub(r"\s+", " ", (username or "").strip())
    if not _USERNAME_RE.match(name):
        raise ValueError("Username must be 1–40 characters: letters, digits, spaces, . _ or -.")
    return name


def _check_password(password):
    if not password or len(password) < _MIN_PASSWORD:
        raise ValueError("Password must be at least %d characters." % _MIN_PASSWORD)
    return password


def _check_role(role):
    if role not in ROLES:
        raise ValueError("Role must be one of: %s." % ", ".join(ROLES))
    return role


# ---------------- queries ----------------

def any_users():
    return bool(_load()["users"])


def get_user(username):
    return _load()["users"].get(_key(username))


def list_users():
    users = _load()["users"].values()
    return sorted((public(u) for u in users),
                  key=lambda u: (_RANK.get(u["role"], 0) * -1, (u["username"] or "").casefold()))


def count_owners():
    return sum(1 for u in _load()["users"].values() if u.get("role") == "owner")


# ---------------- mutations ----------------

def create_user(username, password, role):
    name = _clean_username(username)
    _check_password(password)
    _check_role(role)
    with _lock:
        data = _load()
        key = _key(name)
        if key in data["users"]:
            raise ValueError("A user named '%s' already exists." % name)
        data["users"][key] = {
            "username": name,
            "role": role,
            "password_hash": generate_password_hash(password, method=_HASH_METHOD),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save(data)
        return public(data["users"][key])


def verify_credentials(username, password):
    rec = get_user(username)
    if not rec or not password:
        return None
    if not check_password_hash(rec.get("password_hash", ""), password):
        return None
    return rec


def set_role(username, role):
    _check_role(role)
    with _lock:
        data = _load()
        rec = data["users"].get(_key(username))
        if not rec:
            raise ValueError("No such user.")
        if rec["role"] == "owner" and role != "owner" and _count_owners(data) == 1:
            raise ValueError("Can't change the role of the only Owner - promote another Owner first.")
        rec["role"] = role
        _save(data)
        return public(rec)


def set_password(username, password):
    _check_password(password)
    with _lock:
        data = _load()
        rec = data["users"].get(_key(username))
        if not rec:
            raise ValueError("No such user.")
        rec["password_hash"] = generate_password_hash(password, method=_HASH_METHOD)
        _save(data)
        return public(rec)


def delete_user(username):
    with _lock:
        data = _load()
        key = _key(username)
        rec = data["users"].get(key)
        if not rec:
            raise ValueError("No such user.")
        if rec["role"] == "owner" and _count_owners(data) == 1:
            raise ValueError("Can't delete the only Owner.")
        del data["users"][key]
        _save(data)
        return True


def _count_owners(data):
    return sum(1 for u in data["users"].values() if u.get("role") == "owner")


# ---------------- sessions ----------------

def current_user():
    """The logged-in user's record, or None. Clears a stale cookie if the user
    was deleted since the session was issued."""
    uid = session.get("uid")
    if not uid:
        return None
    rec = _load()["users"].get(uid)
    if not rec:
        session.pop("uid", None)
        return None
    return rec


def login_session(record):
    session.clear()
    session["uid"] = _key(record["username"])
    session.permanent = True


def logout_session():
    session.clear()


def has_rank(record, role):
    return record is not None and _RANK.get(record.get("role"), 0) >= _RANK[role]


# ---------------- guards ----------------

def require_login(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Login required."}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_role(role):
    """Gate a route behind a minimum role (owner > curator > visitor)."""
    _check_role(role)

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return jsonify({"error": "Login required."}), 401
            if not has_rank(user, role):
                return jsonify({"error": "You don't have permission to do that."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return deco
