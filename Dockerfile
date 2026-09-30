FROM python:3.12-slim

# No .pyc files (the code is read-only to the app's user), and unbuffered
# output, so JSON log lines reach the host's log collector as they happen.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

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
