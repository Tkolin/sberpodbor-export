FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq is not needed (asyncpg is pure-python protocol), but lxml wants libxml2/libxslt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxml2 libxslt1.1 curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY xsd ./xsd
COPY app ./app
COPY migrate_sqlite.py seed_accounts.py sample_export.py linkedin_slugs.py parse_work_history.py export_statuses.py build_delivery.py pack_delivery.py replace_in_zip.py ./

RUN useradd -m -u 10001 app && mkdir -p /exports && chown -R app:app /exports /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
