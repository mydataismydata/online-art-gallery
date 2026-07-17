"""Stamp the next build id. Run this immediately before pushing to GitHub.

    python bump_build.py && git add build.json

Every site shows its build id in Settings, so the owner can tell at a glance
whether a box is running the latest code — the local gallery and the public one
agree only when their ids do. It doesn't encode anything; it only has to be
comparable, and to go forwards.

It starts at 1000 and gains the number of minutes since the last push, which makes
the gap between two ids roughly the time between them. Two pushes inside the same
minute would otherwise land on the same id, so the step is never less than one:
"unique" is the part that matters, "minutes" is how it grows.

The id lives in build.json, in the repo, because it describes the code rather than
the box — a checkout has to carry its own id or a site couldn't report one.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parent / "build.json"
START = 1000
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def load():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return int(data["build"]), datetime.strptime(data["pushed"], STAMP)
    except FileNotFoundError:
        return None, None
    except (ValueError, KeyError, TypeError) as e:
        raise SystemExit("build.json is unreadable (%s). Fix or delete it." % e)


def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    build, pushed = load()

    if build is None:
        build, note = START, "seeded"
    else:
        minutes = int((now - pushed).total_seconds() // 60)
        if minutes < 0:
            raise SystemExit(
                "build.json was last stamped in the future (%s). A clock is wrong; "
                "fix it rather than let the id go backwards." % pushed.strftime(STAMP))
        step = max(1, minutes)          # two pushes in one minute must still differ
        build += step
        note = "+%d (%d minute%s since the last push)" % (
            step, minutes, "" if minutes == 1 else "s")

    PATH.write_text(json.dumps({"build": build, "pushed": now.strftime(STAMP)},
                               indent=1) + "\n", encoding="utf-8")
    print("build %d  %s" % (build, note))
    # Plain ASCII: this lands on a Windows console, which is not UTF-8.
    if build > 9999:
        print("note: past four digits. Still fine (an id only has to compare and "
              "rise) but no longer the 4-digit number it started as.", file=sys.stderr)


if __name__ == "__main__":
    main()
