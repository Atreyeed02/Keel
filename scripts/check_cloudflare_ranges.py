"""
Compare app/client_address.py's copy of Cloudflare's address ranges with
the list Cloudflare publishes.

    python -m scripts.check_cloudflare_ranges

Exits 0 if they match. Otherwise it prints what to add and what to remove,
and exits 1. .github/workflows/cloudflare-ranges.yml runs this weekly.
Until the copy is updated, an edge in a new range is not recognised, and
its visitors share the edge's address for the rate limit. A removed range
stays trusted as a Cloudflare edge until it's taken out, so act on either.
"""

import ipaddress
import sys
import urllib.request

from app.client_address import CLOUDFLARE_NETWORKS

PUBLISHED = ("https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6")


def published_networks() -> set:
    networks = set()
    for url in PUBLISHED:
        # Cloudflare refuses urllib's default User-Agent with a 403.
        request = urllib.request.Request(url, headers={"User-Agent": "keel-range-check"})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
        networks |= {ipaddress.ip_network(line.strip()) for line in text.split() if line.strip()}
    return networks


def differences(published: set, ours: set) -> tuple[list, list]:
    """(to add, to remove), each sorted, IPv4 first."""

    def order(network):
        return (network.version, network)

    return sorted(published - ours, key=order), sorted(ours - published, key=order)


def main() -> int:
    published = published_networks()
    if not published:
        print("Cloudflare published no ranges; refusing to compare against nothing.")
        return 1
    to_add, to_remove = differences(published, set(CLOUDFLARE_NETWORKS))
    if not to_add and not to_remove:
        print(f"CLOUDFLARE_NETWORKS matches Cloudflare's {len(published)} published ranges.")
        return 0
    print("CLOUDFLARE_NETWORKS in app/client_address.py differs from Cloudflare's list:")
    for network in to_add:
        print(f"  add     {network}")
    for network in to_remove:
        print(f"  remove  {network}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
