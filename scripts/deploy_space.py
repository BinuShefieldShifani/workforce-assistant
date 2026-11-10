"""Day 5: push the back end to a Hugging Face Space (Docker SDK).

The Space is its own git repository. This script copies only what the image needs
into build/space/, adds the Space README (which tells Hugging Face to build the
Dockerfile), and force-pushes it. Git will ask for your Hugging Face username and
an access token with write permission as the password.

Usage:
    python scripts/deploy_space.py --space <hf-username>/workforce-assistant-api
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from workforce.config import RAW_CSV, ROOT

BUILD = ROOT / "build" / "space"


def run(*cmd: str) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=BUILD, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space", required=True, help="e.g. binu/workforce-assistant-api")
    args = parser.parse_args()

    if not RAW_CSV.exists():
        print(f"{RAW_CSV} is missing. The image builds its database from it.")
        return 1

    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    for name in ("src", "scripts"):
        shutil.copytree(ROOT / name, BUILD / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    for name in ("pyproject.toml", "Dockerfile"):
        shutil.copy(ROOT / name, BUILD / name)
    (BUILD / "data" / "raw").mkdir(parents=True)
    shutil.copy(RAW_CSV, BUILD / "data" / "raw" / RAW_CSV.name)
    shutil.copy(ROOT / "deploy" / "space_README.md", BUILD / "README.md")

    url = f"https://huggingface.co/spaces/{args.space}"
    run("git", "init", "-q", "-b", "main")
    # commit as the same person as the main repo (its local config wins over global)
    for key in ("user.name", "user.email"):
        found = subprocess.run(["git", "config", key], cwd=ROOT, capture_output=True, text=True)
        value = found.stdout.strip()
        if value:
            run("git", "config", key, value)
    run("git", "add", ".")
    run("git", "commit", "-q", "-m", "Deploy workforce assistant API")
    run("git", "push", "--force", url, "main")
    print(f"\nPushed. Watch the build at {url} (Logs tab). It takes a few minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
