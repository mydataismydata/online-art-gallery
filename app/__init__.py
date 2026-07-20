import secrets
from datetime import timedelta

from flask import Flask, send_from_directory

from . import config


def _load_or_create_secret():
    """A stable secret key so signed session cookies survive server restarts.
    Generated once on first run and persisted alongside the other account data."""
    try:
        key = config.SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    except Exception:
        pass
    key = secrets.token_hex(32)
    config.SECRET_KEY_FILE.write_text(key, encoding="utf-8")
    return key


def create_app():
    app = Flask(__name__, static_folder=str(config.STATIC_DIR), static_url_path="/static")

    app.secret_key = _load_or_create_secret()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Left False: the gallery is served over plain HTTP on a LAN. Set True
        # (via a reverse proxy terminating TLS) if you expose it over HTTPS.
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # A ceiling on any single request body. Sized for the one route that
        # carries real weight — a painting uploaded by hand — so an oversized
        # file is refused at the socket instead of being buffered whole.
        MAX_CONTENT_LENGTH=80 << 20,
    )

    from .webapi import bp
    app.register_blueprint(bp)

    @app.route("/")
    def index():
        return send_from_directory(str(config.STATIC_DIR), "index.html")

    return app
