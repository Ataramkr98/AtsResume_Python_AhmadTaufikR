# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------- assets
# Compiles Tailwind, bundles the JS and copies the fonts/favicon into static_dist/.
# scripts/ must be present: `npm run build` ends with the font/favicon copy step.
FROM node:24-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY scripts ./scripts
COPY templates_jinja2 ./templates_jinja2
RUN npm run build

# --------------------------------------------------------------------------- runtime
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Every dependency ships a manylinux wheel (PyMySQL is pure Python, psycopg[binary] bundles
# libpq, cryptography and lxml publish wheels), so no compiler toolchain is needed here.
COPY pyproject.toml ./
COPY apps ./apps
COPY config ./config
RUN pip install .

COPY . .
COPY --from=assets /app/static_dist /app/static_dist
RUN python manage.py collectstatic --noinput

# Named volumes inherit the ownership of the image directory they mount over, so creating
# media/ here is what lets the non-root user write uploads at runtime.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-"]
