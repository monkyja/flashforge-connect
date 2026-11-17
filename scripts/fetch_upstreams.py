"""Download exactly the two public references fixed in upstreams.lock.json."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "upstreams.lock.json").read_text())
REFERENCES = {
    "orcaslicer": ("OrcaSlicer", ["src/slic3r/plugin", "src/slic3r/Utils", "resources/profiles/Flashforge", "docs", "sandboxes"]),
    "flashforge": ("Orca-Flashforge", ["src/slic3r/GUI/FlashForge", "src/slic3r/Utils"]),
}


def git(*args, cwd):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def main():
    for key, (name, directories) in REFERENCES.items():
        spec = LOCK[key]
        target = ROOT / ".references" / name
        if target.exists():
            if git("rev-parse", "HEAD", cwd=target) != spec["commit"]:
                raise SystemExit(f"{target} ya existe con otra revisión; revísalo manualmente.")
        else:
            target.mkdir(parents=True)
            git("init", cwd=target)
            git("remote", "add", "origin", spec["url"], cwd=target)
            git("config", "remote.origin.promisor", "true", cwd=target)
            git("config", "remote.origin.partialclonefilter", "blob:none", cwd=target)
            git("fetch", "--depth", "1", "--filter=blob:none", "origin", spec["commit"], cwd=target)
            git("sparse-checkout", "init", "--cone", cwd=target)
            git("sparse-checkout", "set", *directories, cwd=target)
            git("checkout", "--detach", "FETCH_HEAD", cwd=target)
        print(f"{name}: {spec['commit']}")


if __name__ == "__main__":
    main()
