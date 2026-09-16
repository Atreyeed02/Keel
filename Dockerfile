FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
# Dev utilities — not used at runtime, but `docker compose exec app
# python -m scripts.seed_demo_data` is the documented way to load demo data.
COPY scripts/ ./scripts/

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
