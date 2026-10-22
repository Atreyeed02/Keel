"""
Check the packages installed in the app image against the requirements files.

    python3 .github/scripts/check_image_packages.py <pip-freeze-output>

The input is `pip list --format=freeze` run inside the image. Fails unless
every pin in requirements.txt is installed at exactly its version, and no
package pinned in requirements-dev.txt is installed at all: the test and
lint tools must stay out of what production runs. Both lists come from the
files, so a tool added to requirements-dev.txt later is covered without a
change here. Standard library only: CI runs it with the runner's python3.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def normalize(name: str) -> str:
    # PEP 503: case, and runs of "-", "_" and ".", don't distinguish names
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    """name -> version for each `name[extras]==version` line; skips `-r`."""
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, version = line.split("==")
        found[normalize(re.sub(r"\[.*\]", "", name))] = version.strip()
    return found


def problems(freeze: str, root: Path = ROOT) -> list[str]:
    installed = {}
    for line in freeze.splitlines():
        if "==" in line:
            name, version = line.strip().split("==", 1)
            installed[normalize(name)] = version
    runtime = pins(root / "requirements.txt")
    dev = pins(root / "requirements-dev.txt")
    out = [
        f"{name}=={version} is not installed (found: {installed.get(name, 'nothing')})"
        for name, version in runtime.items()
        if installed.get(name) != version
    ]
    out += [
        f"dev tool {name} is installed ({installed[name]})" for name in dev if name in installed
    ]
    return out


def main() -> int:
    freeze = Path(sys.argv[1]).read_text(encoding="utf-8")
    found = problems(freeze)
    for problem in found:
        print(f"::error::{problem}")
    if not found:
        print(f"image packages OK: all {len(pins(ROOT / 'requirements.txt'))} runtime pins "
              f"installed, none of the {len(pins(ROOT / 'requirements-dev.txt'))} dev tools")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
