#!/usr/bin/env python3
"""One command: URL -> draft text -> new city -> open index.html to look at it.

    python3 scripts/run.py <url> [--model M] [--no-open] [--force]

To throw the result away: git checkout index.html README.md && rm texts/<id>.json
(the draft's path is printed). If a metaphor needs a new district, it stops after the draft.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import add  # noqa: E402
import build  # noqa: E402


def main():
    opening = "--no-open" not in sys.argv
    sys.argv = [a for a in sys.argv if a != "--no-open"]
    out, new = add.main()
    if new:
        sys.exit(f"Stopped: new district needed ({', '.join(sorted(new))}). "
                 f"Take {out.relative_to(build.ROOT)} to a Claude Code session.")
    sys.argv = sys.argv[:1]  # build.py reads its own flags
    build.main()
    if opening:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(build.HTML)], check=False)


if __name__ == "__main__":
    main()
