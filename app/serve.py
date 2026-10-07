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
start instances with `python -m app.serve --no-migrate`. The write rate
limit assumes one instance too: its counts are in this process's memory
(app/ratelimit.py), so each instance would allow the full rate.

`--reload` restarts the server when a file under `app/` changes. Only the
dev override (docker-compose.override.yml) passes it; the image's own start
command never does.
"""

import subprocess
import sys

import uvicorn

from app.config import settings


def migrate() -> None:
    # A subprocess, as the start command always was: Alembic's env.py
    # configures logging for itself, which is not what the server wants.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


def serve(reload: bool = False) -> None:
    # Only what is asked for: passing reload=False would still be a setting
    # a reader has to check, so production gets no reload argument at all.
    development = {"reload": True, "reload_dirs": ["app"]} if reload else {}
    uvicorn.run(
        "app.main:served",
        host=settings.host,
        port=settings.port,
        # Behind a host's proxy the socket peer is the proxy. `served` turns
        # the forwarded headers into the real client and scheme, trusting only
        # the proxies FORWARDED_ALLOW_IPS names (app/client_address.py). It
        # needs the peer as it connected, so uvicorn's own handling, on by
        # default, is off: left on, it would rewrite the peer first.
        proxy_headers=False,
        # One line per request is Keel's own request.completed, which logs the
        # path but never the query string. uvicorn's access line would log the
        # full request line, query string included, and the posting form's
        # no-JavaScript "Add line" and "Remove" carry what was typed there.
        access_log=False,
        **development,
    )


def main(argv: list[str]) -> None:
    if "--no-migrate" not in argv:
        migrate()
    serve(reload="--reload" in argv)


if __name__ == "__main__":
    main(sys.argv[1:])
