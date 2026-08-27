FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

# Gunicorn 26 writes its control socket under the working directory by default.
# WORKDIR is created as root, so explicitly hand the directory to the runtime user.
RUN chown app:app /app

# Fail the image build early on syntax or provider-contract regressions.
RUN python -m py_compile app.py wsgi.py enriched_wsgi.py bbr_danskadresse.py danskadresse_full.py asbestos_guard.py property_inventory.py \
    && python -m unittest discover -s tests -p 'test_*.py'

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "enriched_wsgi:app"]
