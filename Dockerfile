# Docker's official python image, from this repository's copy on ghcr.io.
# The "Mirror base images" workflow copies it from Docker's ECR Public copy
# byte for byte, so the digest below is the one Docker Hub's tag pointed to:
# Docker Hub's and ECR Public's anonymous limits both failed CI and
# Dependabot on shared addresses. Pinned by digest, so the base image
# changes only through a pull request that bumps it, which Dependabot opens
# weekly when the mirror's tag moves (.github/dependabot.yml);
# docker-compose.yml pins postgres the same way. Render builds this file,
# so it pulls from here too.
FROM ghcr.io/atreyeed02/keel-base/python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# No .pyc files (the code is read-only to the app's user), and unbuffered
# output, so JSON log lines reach the host's log collector as they happen.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime packages only: the test and lint tools are in requirements-dev.txt,
# which stays out of the image (CI's docker-smoke checks).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
# Maintenance scripts: `python -m scripts.seed_demo_data`, the rebuild, the
# backfill and the idempotency-key pruning all run from inside the image.
COPY scripts/ ./scripts/

# Run as an unprivileged user with a fixed uid. The files above stay owned by
# root, so the app can read its code but not change it.
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin keel
USER keel

EXPOSE 8000

# Migrate, then serve on $PORT (default 8000): app/serve.py. Migrating on
# start assumes a single instance; see that module before scaling out. No
# --reload here: that is for the dev override only.
CMD ["python", "-m", "app.serve"]
