"""Run the gallery.  python serve.py [--host 0.0.0.0] [--port 8000] [--library PATH]"""
import argparse
import os
import sys


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Serve the gallery over the network.")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--library", help="path to the image library (default: ./library)")
    ap.add_argument("--dev", action="store_true", help="Flask debug server with auto-reload")
    args = ap.parse_args()

    if args.library:
        os.environ["GALLERY_LIBRARY"] = args.library

    from app import create_app
    app = create_app()

    if args.dev:
        app.run(host=args.host, port=args.port, debug=True)
    else:
        from waitress import serve
        print("Gallery serving on http://%s:%d  (library: %s)"
              % (args.host, args.port, os.environ.get("GALLERY_LIBRARY", "./library")), flush=True)
        serve(app, host=args.host, port=args.port, threads=8)


if __name__ == "__main__":
    main()
