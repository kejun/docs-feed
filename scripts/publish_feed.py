#!/usr/bin/env python3
"""Publish from a disposable CI checkout, regenerating when main advances."""

import json
from pathlib import Path
import subprocess
import sys


def run(*args, check=True):
    return subprocess.run(args, check=check, timeout=90)


def revision(ref):
    return subprocess.check_output(["git", "rev-parse", ref], text=True, timeout=90).strip()


def main():
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(1, 4):
        print(f"Publication attempt {attempt}/3.", flush=True)
        run("git", "fetch", "origin", "main")
        # releases.atom is generated: rebuild it on the latest base instead of rebasing it.
        run("git", "reset", "--hard", "origin/main")
        base = revision("HEAD")
        config = json.loads(Path("feed.json").read_text(encoding="utf-8"))
        for source in config["sources"]:
            repo = str(Path(".sources") / source["directory"])
            run("git", "-C", repo, "fetch", "origin", "main")
            run("git", "-C", repo, "reset", "--hard", "FETCH_HEAD")
        run(sys.executable, "scripts/generate_feed.py")
        run("git", "add", "releases.atom")
        changes = run("git", "diff", "--cached", "--quiet", check=False)
        if changes.returncode == 0:
            print("No document changes.", flush=True)
            return
        if changes.returncode != 1:
            changes.check_returncode()
        run("git", "commit", "-m", "chore: update document feed")
        push = run("git", "push", "origin", "HEAD:main", check=False)
        if push.returncode == 0:
            return
        run("git", "fetch", "origin", "main")
        if revision("origin/main") == base:
            print("Push failed without main advancing; not retrying.", file=sys.stderr)
            raise SystemExit(push.returncode)
        print("Main advanced during publication; regenerating on the new base.", flush=True)
    raise SystemExit("Could not publish after 3 attempts: main kept advancing.")


if __name__ == "__main__":
    main()
