# syntax=docker/dockerfile:1
#
# Multi-stage, non-root production image for Threnda Community.
#
# Stage 1 builds a virtualenv (so the runtime stage never needs a compiler
# or pip's build machinery); stage 2 prepares the slim runtime filesystem;
# stage 3 flattens that prepared filesystem so superseded packages from the
# base-image layer history do not produce false-positive vulnerability results.
# The final image drops root before the process ever runs. See
# docs/deployment.md for the full operational contract.

FROM python:3.12-slim AS builder

WORKDIR /build

# libpq (via psycopg's build requirements) needs a compiler at build time
# only — psycopg[binary] actually ships prebuilt wheels, but this stays in
# case the target platform has no wheel available.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY threnda ./threnda
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir --upgrade pip "setuptools>=78.1.1" \
    && pip install --no-cache-dir ".[postgres]"

FROM python:3.12-slim AS prepared-runtime

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends libpq5 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system threnda \
    && useradd --system --gid threnda --home-dir /app --shell /usr/sbin/nologin threnda

WORKDIR /app
ENV PATH="/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /venv /venv
COPY --from=builder /build/threnda ./threnda
COPY --from=builder /build/migrations ./migrations
COPY --from=builder /build/alembic.ini ./alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/threnda-entrypoint

RUN /venv/bin/python -m pip uninstall -y pip setuptools \
    && /usr/local/bin/python -m pip uninstall -y pip setuptools \
    && chmod +x /usr/local/bin/threnda-entrypoint \
    && mkdir -p /app/data \
    && chown -R threnda:threnda /app

FROM scratch AS runtime

COPY --from=prepared-runtime / /

WORKDIR /app
ENV PATH="/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER threnda

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/v1/health || exit 1

ENTRYPOINT ["threnda-entrypoint"]
CMD ["uvicorn", "threnda.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
