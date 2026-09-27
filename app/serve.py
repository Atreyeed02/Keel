"""
Start Keel the way a container host runs it: migrate, then serve.

    python -m app.serve

This is the image's start command. It runs `alembic upgrade head`, and only
if that succeeds, starts uvicorn on HOST:PORT (PORT defaults to 8000; most
hosts set it).

Migrating on start assumes one instance. Two instances starting together
would both run the migrations; Alembic takes no lock to stop that, so a
host that scales out, or starts a new instance before stopping the old one,
should run `alembic upgrade head` as a separate release step instead and
start instances with `python -m app.serve --no-migrate`.
"""

import subprocess
import sys

import uvicorn

from app.config import settings


def migrate() -> None:
    # A subprocess, as the start command always was: Alembic's env.py
    # configures logging for itself, which is not what the server wants.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


def serve() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        # Behind a host's proxy the socket peer is the proxy. These make
        # request.client and request.url.scheme the real ones, but only for
        # proxies FORWARDED_ALLOW_IPS names (app/config.py).
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


def main(argv: list[str]) -> None:
    if "--no-migrate" not in argv:
        migrate()
    serve()


if __name__ == "__main__":
    main(sys.argv[1:])
