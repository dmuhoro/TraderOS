FROM python:3.14-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --user --no-cache-dir -e ".[api,alpaca,postgres,monitoring,streaming]"

FROM python:3.14-slim AS runtime

# Postgres client pinned to the server's major version: Railway's managed
# Postgres is PG 18.x, and pg_dump refuses to dump a NEWER server than the
# client ("aborting because of server version mismatch"). Debian trixie only
# ships postgresql-client-17, so the matching 18 client comes from PGDG
# (official repo, deb822 .sources format per postgresql.org/download/linux/debian).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && printf 'Types: deb deb-src\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: trixie-pgdg\nArchitectures: amd64\nComponents: main\nSigned-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n' \
        > /etc/apt/sources.list.d/pgdg.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
        postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /app/.local
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

COPY . .

RUN groupadd -r traderos && useradd -r -g traderos -d /app traderos && \
    chown -R traderos:traderos /app && \
    mkdir -p /app/data /app/exports && \
    chown traderos:traderos /app/data /app/exports

USER traderos

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/v1/healthz || exit 1

EXPOSE 8000

ENTRYPOINT ["traderos-api"]
