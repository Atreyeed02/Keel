"""
Print the database host DATABASE_URL names, and fail unless it is the
expected one.

    python -m scripts.check_database_host --prefix ep-<adjective>-<noun>- --region <region>

Exits 0 if the host starts with the prefix, contains the region, and is not
Neon's connection pooler (`-pooler`); the live app and its scripts use the
direct endpoint. Otherwise it says which check failed and exits 1.
.github/workflows/demo-maintenance.yml runs this before the reset, with the
live endpoint's prefix and region, so a secret copied from the wrong Neon
branch (a snapshot, say) or endpoint stops the run before anything
connects.

It reads the URL through app.config, as the reset that follows does, and
never connects to anything. It prints the host only, never the URL, and
with the Neon endpoint's random id masked: the repository is public, and so
are its workflow logs. The checks run on the whole host.
"""

import argparse
import re
import sys
from urllib.parse import urlsplit

from app.config import settings

# A Neon endpoint is named ep-<adjective>-<noun>-<id>, and the id is what
# makes the host unique. Anything after it, such as `-pooler`, is kept.
NEON_ENDPOINT_ID = re.compile(r"^(ep-[a-z]+-[a-z]+-)[a-z0-9]+")
POOLER = "-pooler"


def masked(host: str) -> str:
    """`host` with a Neon endpoint's id replaced; any other host as it is."""
    return NEON_ENDPOINT_ID.sub(r"\1********", host)


def problems(host: str | None, prefix: str, region: str) -> list[str]:
    """Why `host` is not the expected database, or nothing if it is."""
    if not host:
        return ["DATABASE_URL names no host"]
    found = []
    if not host.startswith(prefix.lower()):
        found.append(f"it does not start with {prefix}")
    if region.lower() not in host:
        found.append(f"it is not in {region}")
    if POOLER in host:
        found.append(f"it is the connection pooler ({POOLER}), not the direct endpoint")
    return found


def main(prefix: str, region: str) -> int:
    # urlsplit lowercases the host, so the checks are case-insensitive.
    host = urlsplit(settings.database_url).hostname
    print(f"Database host: {masked(host) if host else '(none)'}")
    found = problems(host, prefix, region)
    if found:
        print(f"Not the expected database: {'; '.join(found)}.")
        return 1
    print(f"Expected database: starts with {prefix}, in {region}, direct (not {POOLER}).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--prefix", required=True, help="what the host must start with")
    parser.add_argument("--region", required=True, help="what the host must contain")
    args = parser.parse_args()
    sys.exit(main(args.prefix, args.region))
