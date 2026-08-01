FROM python:3.14-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --user --no-cache-dir -e ".[api,alpaca,postgres,monitoring]"

FROM python:3.14-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /app/.local
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app/src

COPY . .

RUN groupadd -r traderos && useradd -r -g traderos -d /app traderos && \
    chown -R traderos:traderos /app

USER traderos

VOLUME ["/app/data", "/app/exports"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD traderos health || exit 1

EXPOSE 8000

ENTRYPOINT ["traderos-api"]
